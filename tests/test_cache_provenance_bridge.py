"""New provenance permits only the reviewed digest optimization source change."""
import ast
from copy import deepcopy
from pathlib import Path
import zipfile

import pytest

from experiments.firmware_cache import audit_connected_checkpoint as audit


@pytest.fixture(scope="module")
def worldio_sources():
    root = Path(__file__).resolve().parents[1]
    archive = root / "experiments/memory_controller_runtime/current_four_kib/evidence/connected-source-inputs.zip"
    with zipfile.ZipFile(archive) as source:
        original = source.read("src/shamaos/worldio.py")
    return original, (root / "src/shamaos/worldio.py").read_bytes()


def test_bridge_checks_retained_and_optimized_fingerprints_directly(worldio_sources):
    checked = audit.fingerprint_equivalence(*worldio_sources)
    assert len(checked["retained_method_current_method_comparisons"]) == 13
    assert checked["invalid_palette_indices_rejected_by_both"]
    assert checked["entire_remaining_module_ast_identical"]
    assert checked["excluded_exactly_reviewed_functions"] == ["_Chunk.fingerprint", "_chunk_nbt"]
    native = checked["native_nbt_comparator_fastpath"]
    assert len(native["exact_native_bytes_compared"]) == 17
    assert native["exact_native_bytes_compared"]["uniform-comparators"]["comparator_entities"] == 4096
    assert native["invalid_used_indices_and_comparator_properties_rejected_by_both"]
    assert checked["changed_native_writer_rejected"]
    assert checked["unreviewed_fingerprint_change_rejected"]


def test_bridge_rejects_a_different_fingerprint_even_with_unchanged_surroundings(worldio_sources):
    _, current = worldio_sources
    tree = ast.parse(current)
    audit.fingerprint_method(tree).body.append(ast.Return(ast.Constant(None)))
    with pytest.raises(AssertionError):
        audit.normalized_fingerprint_source(ast.unparse(tree), integrated=True)


def test_bridge_rejects_a_native_encoding_change_outside_the_reviewed_method(worldio_sources):
    original, current = worldio_sources
    tree = ast.parse(current)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_signed_long")
    function.body = [ast.Return(ast.Constant(0))]
    assert audit.normalized_fingerprint_source(original, integrated=False, native_nbt=True) != audit.normalized_fingerprint_source(
        ast.unparse(tree), integrated=True, native_nbt=True)


def test_bridge_rejects_an_unreviewed_native_nbt_change(worldio_sources):
    _, current = worldio_sources
    tree = ast.parse(current)
    audit.nbt_function(tree).body.append(ast.Return(ast.Constant(None)))
    with pytest.raises(AssertionError):
        audit.normalized_fingerprint_source(ast.unparse(tree), integrated=True, native_nbt=True)


@pytest.mark.parametrize("integrated", (False, True))
@pytest.mark.parametrize("target", ("fingerprint", "native_nbt"))
def test_pinned_method_digest_accepts_python311_field_shape_without_changing_the_tree(worldio_sources, integrated, target):
    selector = audit.fingerprint_method if target == "fingerprint" else audit.nbt_function
    hashes = audit.FINGERPRINT_AST_SHA256 if target == "fingerprint" else audit.NBT_AST_SHA256
    method = selector(ast.parse(worldio_sources[int(integrated)]))
    for node in ast.walk(method):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node._fields = tuple(field for field in node._fields if field != "type_params")
            if hasattr(node, "type_params"):
                del node.type_params
    before = ast.dump(method, include_attributes=True)
    assert audit.digest(audit.reviewed_ast_dump(method).encode()) == hashes[integrated]
    assert ast.dump(method, include_attributes=True) == before


@pytest.mark.parametrize("integrated", (False, True))
@pytest.mark.parametrize("registered", (False, True))
@pytest.mark.parametrize("target", ("fingerprint", "native_nbt"))
def test_ast_portability_does_not_remove_unreviewed_generic_parameters(worldio_sources, integrated, registered, target):
    selector = audit.fingerprint_method if target == "fingerprint" else audit.nbt_function
    hashes = audit.FINGERPRINT_AST_SHA256 if target == "fingerprint" else audit.NBT_AST_SHA256
    method = selector(ast.parse(worldio_sources[int(integrated)]))
    changed = deepcopy(method)
    changed._fields = tuple(field for field in changed._fields if field != "type_params")
    if registered:
        changed._fields = (*changed._fields, "type_params")
    changed.type_params = [ast.Name(id="UnreviewedTypeParameter", ctx=ast.Load())]
    assert audit.digest(audit.reviewed_ast_dump(changed).encode()) != hashes[integrated]
