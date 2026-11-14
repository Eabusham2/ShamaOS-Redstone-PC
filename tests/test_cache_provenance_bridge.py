"""New provenance permits only the reviewed digest optimization source change."""
import ast
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
    assert audit.normalized_fingerprint_source(original, integrated=False) != audit.normalized_fingerprint_source(
        ast.unparse(tree), integrated=True)
