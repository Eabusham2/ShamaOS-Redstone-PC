"""Reduce shared routing demand by cutting the complete mapped connectivity graph."""

from collections import Counter

from .partition_planner import PlanningError, signal_links


def maximum_prefixes(graph, tree, *, facade):
    """Count the terminals that each physical parent allocator will receive."""
    clusters, entity_leaf, _ = tree
    demand = [0] * len(clusters)
    for signal in graph.signals.values():
        driver = entity_leaf[signal.source.entity]
        sinks = {entity_leaf[endpoint.entity] for endpoint in signal.sinks}
        for source, sink in signal_links(driver, sinks, clusters):
            if clusters[source]["children"]:
                demand[source] += 1
            if clusters[sink]["children"]:
                demand[sink] += 1
    if facade:
        for entity in range(graph.cell_count, len(graph.entities)):
            parent = clusters[entity_leaf[entity]]["parent"]
            while parent is not None:
                demand[parent] += 2  # One child contact and one exterior contact.
                parent = clusters[parent]["parent"]
    return max(demand, default=0)


def _connections(graph):
    adjacent = [{} for _ in graph.entities]
    signal_bits = [set() for _ in graph.entities]
    populations = {}
    for bit in sorted(graph.signals):
        signal = graph.signals[bit]
        source = signal.source.entity
        entities = {source, *(sink.entity for sink in signal.sinks)}
        populations[bit] = len(entities)
        for entity in entities:
            signal_bits[entity].add(bit)
        # Limit the influence of broadcasts on spatial affinity. Every signal
        # still contributes to the boundary counts and physical routing plan.
        if 1 < len(entities) <= 64:
            weight = max(1, 16 // (len(entities) - 1))
            for neighbor in sorted(entities - {source}):
                adjacent[source][neighbor] = adjacent[source].get(neighbor, 0) + weight
                adjacent[neighbor][source] = adjacent[neighbor].get(source, 0) + weight
    return adjacent, signal_bits, populations


def _cut_tree(graph, connections, *, arity, cell_budget, boundary_budget):
    try:
        import pymetis
    except ImportError as error:
        raise PlanningError("the default hierarchy exceeds the physical prefix budget; install pymetis==2025.2.2 to plan a bounded graph cut") from error

    adjacent, signal_bits, populations = connections
    clusters, entity_leaf = [], [-1] * len(graph.entities)

    def partition(members):
        if len(members) <= cell_budget:
            counts = Counter(bit for entity in members for bit in signal_bits[entity])
            boundary = sum(count < populations[bit] for bit, count in counts.items())
            if boundary <= boundary_budget:
                identity = len(clusters)
                for entity in members:
                    entity_leaf[entity] = identity
                clusters.append({"id": identity, "parent": None, "children": [], "level": 0,
                    "cell_ids": [entity for entity in members if entity < graph.cell_count],
                    "port_ids": [entity for entity in members if entity >= graph.cell_count]})
                return identity
            if len(members) == 1:
                raise PlanningError(f"entity {members[0]} needs {boundary} boundary terminals")
        indices = {entity: index for index, entity in enumerate(members)}
        offsets, neighbors, weights = [0], [], []
        for entity in members:
            for neighbor, weight in adjacent[entity].items():
                if neighbor in indices:
                    neighbors.append(indices[neighbor])
                    weights.append(weight)
            offsets.append(len(neighbors))
        parts = min(arity, len(members))
        groups = [[] for _ in range(parts)]
        if neighbors:
            split = pymetis.part_graph(parts, adjacency=pymetis.CSRAdjacency(offsets, neighbors),
                eweights=weights, recursive=True, options=pymetis.Options(seed=1))
            for entity, group in zip(members, split.vertex_part, strict=True):
                groups[group].append(entity)
        if sum(bool(group) for group in groups) < 2:
            # When METIS cannot divide a subgraph, a balanced split still makes progress.
            groups = [members[index * len(members) // parts:(index + 1) * len(members) // parts]
                      for index in range(parts)]
        children = [partition(group) for group in groups if group]
        identity = len(clusters)
        for child in children:
            clusters[child]["parent"] = identity
        clusters.append({"id": identity, "parent": None, "children": children,
            "level": 1 + max(clusters[child]["level"] for child in children),
            "cell_ids": [], "port_ids": []})
        return identity

    root = partition(list(range(len(graph.entities))))
    return clusters, entity_leaf, root


def fit_parent_prefixes(graph, tree, *, cell_budget, boundary_budget, arity, limit, facade):
    # Each signal-tree branch uses a subset of the original source/sink leaves.
    # A parent uses at most one terminal per incident tree edge of each signal.
    # This cheap bound lets small fixtures retain their original plan directly.
    upper_bound = sum(min(arity + 1, 1 + len(signal.sinks)) for signal in graph.signals.values())
    if facade:
        upper_bound += 2 * (len(graph.entities) - graph.cell_count)
    if upper_bound <= limit:
        return tree
    baseline = maximum_prefixes(graph, tree, facade=facade)
    if baseline <= limit:
        return tree
    connections = _connections(graph)
    counts = {"affinity": baseline}
    for branching in dict.fromkeys((arity, 2)):
        candidate = _cut_tree(graph, connections, arity=branching,
                              cell_budget=cell_budget, boundary_budget=boundary_budget)
        count = maximum_prefixes(graph, candidate, facade=facade)
        if count <= limit:
            return candidate
        counts[f"cut-{branching}"] = count
    raise PlanningError(f"no candidate hierarchy fits {limit} shared parent terminals: {counts}")
