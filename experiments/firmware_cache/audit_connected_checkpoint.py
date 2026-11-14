"""Bind the retained complete-cache measurement to the final 4 KiB defaults.

The original source archive remains immutable. The integrated default cache,
source-provenance binding and exact fingerprint/comparator-scan optimizations
are checked separately. The measured fixture already uses explicit 4 KiB inputs.
"""
from __future__ import annotations

import argparse
from array import array
import ast
from copy import deepcopy
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reviewed_ast_dump(tree):
    """Keep the exact Python 3.12 AST representation across supported runtimes.

    Python 3.11 omits the empty generic-parameter field; 3.13 hides empty lists
    in dumps by default. Neither changes these reviewed methods. Preserve all
    present fields, including any nonempty generic parameters, for rejection.
    """
    tree = deepcopy(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and "type_params" not in node._fields:
            node._fields = (*node._fields, "type_params")
            node.type_params = getattr(node, "type_params", [])
    options = {"show_empty": True} if sys.version_info >= (3, 13) else {}
    return ast.dump(tree, include_attributes=False, **options)


def normalized_defaults(data, expected_bytes):
    tree = ast.parse(data)
    assert isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant)
    tree.body.pop(0)
    matches = [node for node in tree.body if isinstance(node, ast.Assign)
               and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
               and node.targets[0].id == "CACHE_BYTES"]
    assert len(matches) == 1
    value = matches[0].value
    assert (isinstance(value, ast.BinOp) and isinstance(value.op, ast.LShift)
            and isinstance(value.left, ast.Constant) and isinstance(value.right, ast.Constant))
    assert value.left.value << value.right.value == expected_bytes
    matches[0].value = ast.Constant(value="REVIEWED_CACHE_DEFAULT")
    return ast.dump(tree, include_attributes=False)


def normalized_source_binding(data, *, integrated):
    """Ignore exactly the added metadata file name, retaining the complete AST."""
    tree = ast.parse(data)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "source_provenance"]
    assert len(functions) == 1
    lists = [node.value for node in functions[0].body if isinstance(node, ast.Assign)
             and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
             and node.targets[0].id == "files" and isinstance(node.value, ast.List)]
    assert len(lists) == 1
    policy = "src/shamaos/hardware/cache_layout.py"
    entries = [item for item in lists[0].elts if isinstance(item, ast.Constant) and item.value == policy]
    assert len(entries) == int(integrated)
    if integrated:
        lists[0].elts.remove(entries[0])
    return ast.dump(tree, include_attributes=False)


FINGERPRINT_AST_SHA256 = {
    False: "e39a12a195b0a0989ea5d4e928a32efa912245194e4fda1fbfab69824f405bda",
    True: "66a27bf9520986c21c1645b300d4d4fd659c75896edf9f968d52725a1fbb7bec",
}
NBT_AST_SHA256 = {
    False: "9ce10c2c3183af5dee20ccff3e986a7cb6db2b02476ce9641e1dd45d45eecd7f",
    True: "1ff22bbb2eb222b4e2aa4cd83bfe4c7bba215e60ffade7d8e5d93c5647186ba9",
}


def fingerprint_method(tree):
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_Chunk"]
    assert len(classes) == 1
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name == "fingerprint"]
    assert len(methods) == 1
    return methods[0]


def nbt_function(tree):
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_chunk_nbt"]
    assert len(functions) == 1
    return functions[0]


def normalized_fingerprint_source(data, *, integrated, native_nbt=False):
    """Admit only exact reviewed function ASTs, retaining everything else."""
    tree = ast.parse(data)
    method = fingerprint_method(tree)
    assert digest(reviewed_ast_dump(method).encode()) == FINGERPRINT_AST_SHA256[integrated]
    method.body = [ast.Pass()]
    if native_nbt:
        function = nbt_function(tree)
        assert digest(reviewed_ast_dump(function).encode()) == NBT_AST_SHA256[integrated]
        function.body = [ast.Pass()]
    return ast.dump(tree, include_attributes=False)


def fingerprint_equivalence(original, integrated):
    """Compare retained code directly, including canonical palette/ownership cases."""
    from shamaos import worldio
    from shamaos.hardware.memory import redstone_wire, repeater
    from shamaos.model import BlockState

    expected = normalized_fingerprint_source(original, integrated=False, native_nbt=True)
    assert expected == normalized_fingerprint_source(integrated, integrated=True, native_nbt=True)
    namespace = vars(worldio).copy()
    method = fingerprint_method(ast.parse(original))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "retained-connected-worldio", "exec"), namespace)
    reference = namespace["fingerprint"]
    air = worldio._Chunk(-7, 31, {sy: worldio._Section() for sy in range(-4, 20)})
    probes = {"empty": worldio._Chunk(-7, 31), "unowned-air": air,
              "terrain": worldio._new_chunk(-7, 31, -61)}
    owned = deepcopy(air)
    owned.sections[4].assigned[0] = 1
    owned.sections[4].assigned[-1] = 128
    probes["owned-air"] = owned
    unused = deepcopy(air)
    unused.sections[4].palette += [BlockState.of("minecraft:stone"), worldio.AIR]
    probes["unused-palette"] = unused
    states = [worldio.AIR, BlockState.of("minecraft:stone"), redstone_wire(power=7),
              repeater("east", delay=3), BlockState.of("minecraft:redstone_lamp", lit="true")]
    for stride in (1, 3, 37, 4095):
        chunk = deepcopy(probes["terrain"])
        section = worldio._Section(palette=list(states))
        for index in range(0, 4096, stride):
            section.indices[index] = 1 + index % 4
            section.assigned[index >> 3] |= 1 << (index & 7)
        chunk.sections[4] = section
        probes[f"mixed-stride-{stride}"] = chunk
        permuted = deepcopy(chunk)
        changed = permuted.sections[4]
        changed.palette = list(reversed(states)) + [states[1], BlockState.of("minecraft:dirt")]
        changed.indices = array("H", (len(states) - 1 - value for value in section.indices))
        changed.indices[0] = len(states)
        probes[f"permuted-duplicate-unused-stride-{stride}"] = permuted
    checked = {}
    for name, chunk in probes.items():
        before, after = reference(chunk), chunk.fingerprint()
        assert before == after
        checked[name] = {"sha256": after[0], "assigned_blocks": after[1]}
    invalid = deepcopy(air)
    invalid.sections[4].indices[0] = 1
    for candidate in (reference, worldio._Chunk.fingerprint):
        try:
            candidate(invalid)
        except IndexError:
            pass
        else:
            raise AssertionError("fingerprint lost invalid palette-index rejection")
    altered = ast.parse(integrated)
    next(node for node in altered.body if isinstance(node, ast.FunctionDef) and node.name == "_signed_long").body = [ast.Return(ast.Constant(0))]
    assert expected != normalized_fingerprint_source(ast.unparse(altered), integrated=True, native_nbt=True)
    altered = ast.parse(integrated)
    fingerprint_method(altered).body = [ast.Return(ast.Constant(None))]
    try:
        normalized_fingerprint_source(ast.unparse(altered), integrated=True, native_nbt=True)
    except AssertionError:
        pass
    else:
        raise AssertionError("unreviewed fingerprint replacement accepted")
    comparator_review = native_nbt_equivalence(original, probes)
    return {"function": "_Chunk.fingerprint", "retained_method_ast_sha256": FINGERPRINT_AST_SHA256[False],
        "integrated_method_ast_sha256": FINGERPRINT_AST_SHA256[True], "entire_remaining_module_ast_identical": True,
        "excluded_exactly_reviewed_functions": ["_Chunk.fingerprint", "_chunk_nbt"],
        "native_nbt_comparator_fastpath": comparator_review,
        "retained_method_current_method_comparisons": checked, "invalid_palette_indices_rejected_by_both": True,
        "changed_native_writer_rejected": True, "unreviewed_fingerprint_change_rejected": True,
        "scope": "Exact reviewed palette lookup and absent-comparator scan optimizations only; no emitted geometry, serialized native bytes, decoder or digest contract change"}


def native_nbt_equivalence(original, probes):
    """Compare actual serialized NBT, including used and unused comparators."""
    from shamaos import worldio
    from shamaos.model import BlockState

    namespace = vars(worldio).copy()
    function = nbt_function(ast.parse(original))
    exec(compile(ast.Module(body=[function], type_ignores=[]), "retained-connected-native-nbt", "exec"), namespace)
    reference = namespace["_chunk_nbt"]
    off, on = (worldio.canonical_block_state(BlockState.of("minecraft:comparator", powered=value)) for value in ("false", "true"))
    extra = {}
    for name in ("unused-comparator", "unused-invalid-comparator", "mixed-comparators", "uniform-comparators"):
        chunk = worldio._new_chunk(-7, 31, -61)
        section = worldio._Section(palette=[worldio.AIR, off, on])
        if name == "unused-invalid-comparator":
            section.palette.append(BlockState.of("minecraft:comparator", powered="invalid"))
        elif name == "mixed-comparators":
            for index, value in ((0, 1), (255, 2), (4095, 2)):
                section.indices[index] = value
                section.assigned[index >> 3] |= 1 << (index & 7)
        elif name == "uniform-comparators":
            section.palette = [on]
            section.assigned[:] = b"\xff" * 512
        chunk.sections[4] = section
        extra[name] = chunk

    def serialized(root):
        stream = io.BytesIO()
        root.write(stream)
        return stream.getvalue()

    checked = {}
    for name, chunk in (probes | extra).items():
        before, after = reference(chunk), worldio._chunk_nbt(chunk)
        original_bytes, current_bytes = serialized(before), serialized(after)
        assert original_bytes == current_bytes
        checked[name] = {"serialized_native_sha256": digest(current_bytes), "bytes": len(current_bytes),
                         "comparator_entities": len(after["block_entities"])}
    invalid = worldio._Chunk(0, 0, {0: worldio._Section()})
    invalid.sections[0].indices[4095] = 65535
    missing_power = worldio._Chunk(0, 0, {0: worldio._Section(palette=[BlockState.of("minecraft:comparator")])})
    for candidate in (reference, worldio._chunk_nbt):
        for chunk, expected_error in ((invalid, IndexError), (missing_power, KeyError)):
            try:
                candidate(chunk)
            except expected_error:
                pass
            else:
                raise AssertionError("native NBT fast path lost invalid-state rejection")
    return {"function": "_chunk_nbt", "retained_function_ast_sha256": NBT_AST_SHA256[False],
        "integrated_function_ast_sha256": NBT_AST_SHA256[True], "exact_native_bytes_compared": checked,
        "invalid_used_indices_and_comparator_properties_rejected_by_both": True}


def audit(output):
    from experiments.memory_controller_runtime import current_fixture, interconnect, scenarios
    from experiments.partition_placement.port_egress_manifest import read_egress_manifest

    if output.exists():
        raise FileExistsError(output)
    evidence = ROOT / "experiments/memory_controller_runtime/current_four_kib/evidence"
    checkpoint_path = evidence / "connected-union-checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    report_bytes = (evidence / checkpoint["observed_build_report_file"]).read_bytes()
    assert digest(report_bytes) == checkpoint["observed_build_report_sha256"]
    retained = json.loads(report_bytes)
    source_zip = evidence / checkpoint["source_input_archive"]
    assert digest(source_zip.read_bytes()) == checkpoint["source_input_archive_sha256"]
    with zipfile.ZipFile(source_zip) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) == checkpoint["source_input_archive_files"] == 55
        differences = {}
        for name, expected in retained["source_hashes"].items():
            original, current = archive.read(name), (ROOT / name).read_bytes()
            assert digest(original) == expected
            if digest(current) != expected:
                differences[name] = {"retained_sha256": expected, "integrated_sha256": digest(current)}
        assert set(differences) == {"src/shamaos/machine_spec.py", "src/shamaos/generator.py", "src/shamaos/worldio.py"}
        assert normalized_defaults(archive.read("src/shamaos/machine_spec.py"), 8192) == normalized_defaults(
            (ROOT / "src/shamaos/machine_spec.py").read_bytes(), 4096)
        original_generator = archive.read("src/shamaos/generator.py")
        integrated_generator = (ROOT / "src/shamaos/generator.py").read_bytes()
        expected_generator = normalized_source_binding(original_generator, integrated=False)
        assert expected_generator == normalized_source_binding(integrated_generator, integrated=True)
        # Changing the real capacity contract must not fit the metadata-only
        # exception, even though the same source-binding addition is present.
        marker = b'"cache_bytes": CACHE_BYTES, "physical_cache_bytes": CACHE_BYTES'
        assert integrated_generator.count(marker) == 1
        changed_capacity = integrated_generator.replace(marker,
            b'"cache_bytes": CACHE_BYTES + 4, "physical_cache_bytes": CACHE_BYTES')
        assert expected_generator != normalized_source_binding(changed_capacity, integrated=True)
        fingerprint_review = fingerprint_equivalence(archive.read("src/shamaos/worldio.py"),
            (ROOT / "src/shamaos/worldio.py").read_bytes())
        with tempfile.TemporaryDirectory(prefix="connected-cache-provenance-") as directory:
            target = Path(directory)
            for name in names:
                path = target / name
                assert path.resolve().is_relative_to(target)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(name))
            current = target / "inputs/current"
            prepared, graph, shape, fabric, origin = current_fixture.load_prepared(current)
            integrated_shape, integrated_fabric, integrated_origin = current_fixture.physical_configuration(ROOT, 4096)
            assert (shape, fabric, origin) == (integrated_shape, integrated_fabric, integrated_origin)
            for name, expected in prepared["input_hashes"].items():
                assert digest((ROOT / name).read_bytes()) == expected
            plan = json.loads((current / "plan.json").read_text())
            placed = read_egress_manifest(target / "inputs/facade-balanced.json.gz",
                retained["facade_manifest"]["sha256"], graph, plan, source_root=target)
            connection = interconnect.connect_cache(graph, placed, integrated_fabric, integrated_origin,
                                                       capacity_bytes=4096)
            nets_bytes = (json.dumps([asdict(net) for net in connection.nets], indent=2, sort_keys=True) + "\n").encode()
            assert digest(nets_bytes) == retained["external_nets_sha256"]
            initial = b"".join(word.to_bytes(4, "little") for word in scenarios.initial_words(capacity_bytes=4096))
            assert digest(initial) == retained["physical_memory"]["initial_sha256"]
            assert asdict(fabric.bounds(connection.bank_origin)) == retained["physical_memory"]["bounds"]
            assert (fabric.total_bytes, fabric.bank_count, len(connection.nets)) == (4096, 1, 77)
    first, second = retained["independent_sort_union"], retained["sqlite_union"]
    assert first["literal_unique_blocks"] == second["unique_blocks"] == checkpoint["unique_blocks"]
    for key in ("occupied_chunks", "bounds", "palette", "sorted_unique_record_sha256"):
        assert json.loads(json.dumps(first[key])) == json.loads(json.dumps(second[key]))
    for key in ("occupied_chunks", "bounds", "sorted_unique_record_sha256"):
        assert first[key] == checkpoint[key]
    historical_path = ROOT / checkpoint["historical_comparison"]["receipt"]
    assert digest(historical_path.read_bytes()) == checkpoint["historical_comparison"]["receipt_sha256"]
    historical = json.loads(historical_path.read_text())
    for key in ("unique_blocks", "occupied_chunks"):
        assert historical[key] == checkpoint["historical_comparison"][key]
        assert abs(100 * (historical[key] - checkpoint[key]) / historical[key]
                   - checkpoint["historical_comparison"][key + "_reduction_percent"]) < 1e-12
    result = {"schema": "current-cache-final-integration-provenance-review-v3", "status": "passed",
        "scope": "Retained complete-union integrity; explicit final default, metadata-only source binding and exact reviewed fingerprint/native comparator-scan optimizations; exact integrated physical bank/route inputs; no fresh literal union or native run",
        "audit_source_sha256": digest(Path(__file__).read_bytes()),
        "checkpoint_sha256": digest(checkpoint_path.read_bytes()),
        "retained_source_zip_sha256": digest(source_zip.read_bytes()),
        "source_files": len(retained["source_hashes"]), "source_differences": differences,
        "default_only_semantic_difference": {"CACHE_BYTES": {"retained": 8192, "integrated": 4096}},
        "generator_difference": {"function": "source_provenance", "added_file_binding": "src/shamaos/hardware/cache_layout.py",
            "entire_remaining_module_ast_identical": True, "changed_real_capacity_contract_rejected": True},
        "worldio_difference": fingerprint_review,
        "explicit_input_hashes": prepared["input_hashes"],
        "integrated_physical_configuration_exact": True, "integrated_external_nets_exact": True,
        "integrated_bank_bounds_and_initial_bytes_exact": True,
        "connected_routes": len(connection.nets), "physical_banks": fabric.bank_count,
        "capacity_bytes": fabric.total_bytes, "unique_blocks": checkpoint["unique_blocks"],
        "occupied_chunks": checkpoint["occupied_chunks"], "both_exact_unions_agree": True,
        "sorted_unique_record_sha256": checkpoint["sorted_unique_record_sha256"],
        "historical_reduction_percentages_recomputed": True,
        "unchanged_archive": True, "native_runtime_verified": False, "whole_machine_verified": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.output)
