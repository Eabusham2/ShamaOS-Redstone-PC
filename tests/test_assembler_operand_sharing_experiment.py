"""Small guard cases for the isolated assembler size/equivalence experiment."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "experiments/assembler_operand_sharing/measure_size.py"
SPEC = importlib.util.spec_from_file_location("operand_sharing_size", SCRIPT)
measurement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


def mapped(tmp_path, cells, ports=None, creator="Yosys 0.33 (git sha1 2584903a060)"):
    path = tmp_path / "mapped.json"
    path.write_text(json.dumps({
        "creator": creator,
        "modules": {measurement.TOP: {"cells": cells, "ports": ports or {}}},
    }))
    return path


def cell(kind, **pins):
    return {"type": kind, "connections": {pin: [bit] for pin, bit in pins.items()}}


def test_counts_actual_enable_lowering_and_literal_native_templates(tmp_path):
    source = mapped(tmp_path, {"enabled": cell("$_DFFE_PP_", C=2, D=3, E=4, Q=5)}, {
        "c": {"direction": "input", "bits": [2]},
        "d": {"direction": "input", "bits": [3]},
        "enable": {"direction": "input", "bits": [4]},
        "q": {"direction": "output", "bits": [5]},
    })
    result = measurement.count_mapping(source, tmp_path / "counts.json")
    assert result["raw_cells"] == 1
    assert result["lowered_native_cells"] == 5
    assert result["native_cell_types"] == {"$_DFF_P_": 1, "$_NAND_": 3, "$_NOT_": 1}
    expected = sum(result["templates"][kind]["unique_blocks"] * count
                   for kind, count in result["native_cell_types"].items())
    assert result["literal_unique_cell_blocks"] == expected
    assert result["logical_stats"]["maximum_endpoint_unit_gate_depth"] == 3
    assert result["whole_machine_chunks"] is None
    assert result["physical_clock_speed_verified"] is False


def test_constant_gate_and_dff_boundaries_have_unit_depth(tmp_path):
    source = mapped(tmp_path, {
        "constant": cell("$_NOT_", A="0", Y=6),
        "data": cell("$_AND_", A=6, B=8, Y=7),
        "register": cell("$_DFF_P_", C=2, D=7, Q=8),
    }, {"clock": {"direction": "input", "bits": [2]},
        "out": {"direction": "output", "bits": [8]}})
    result = measurement.count_mapping(source, tmp_path / "counts.json")
    assert result["logical_stats"]["maximum_unit_gate_depth"] == 2
    assert result["logical_stats"]["maximum_endpoint_unit_gate_depth"] == 2
    assert result["logical_stats"]["constant_input_uses"] == {"0": 1}


@pytest.mark.parametrize("cells,ports,diagnostic", [
    ({"loop0": cell("$_NOT_", A=3, Y=2), "loop1": cell("$_NOT_", A=2, Y=3)}, {}, "combinational cycle"),
    ({"undriven": cell("$_NOT_", A=9, Y=2)}, {}, "undriven net"),
    ({"one": cell("$_NOT_", A="0", Y=2), "two": cell("$_NOT_", A="1", Y=2)}, {}, "multiple/constant output driver"),
    ({"bad": {"type": "$_NOT_", "connections": {"A": [2, 3], "Y": [4]}}}, {}, "nonscalar native cell"),
])
def test_malformed_logic_rejected(tmp_path, cells, ports, diagnostic):
    source = mapped(tmp_path, cells, ports)
    output = tmp_path / "counts.json"
    with pytest.raises(AssertionError, match=diagnostic):
        measurement.count_mapping(source, output)
    assert not output.exists()


def test_unsupported_native_template_rejected(tmp_path):
    source = mapped(tmp_path, {"xor": cell("$_XOR_", A="0", B="1", Y=2)})
    with pytest.raises(KeyError, match="unsupported physical redstone cell"):
        measurement.count_mapping(source, tmp_path / "counts.json")


def test_different_yosys_version_rejected(tmp_path):
    source = mapped(tmp_path, {}, creator="Yosys 0.99")
    with pytest.raises(AssertionError, match="Yosys 0.33"):
        measurement.count_mapping(source, tmp_path / "counts.json")


@pytest.mark.parametrize("value", ["x", "z"])
def test_unresolved_constants_rejected(tmp_path, value):
    source = mapped(tmp_path, {"unknown": cell("$_NOT_", A=value, Y=2)})
    output = tmp_path / "counts.json"
    with pytest.raises(AssertionError, match="unresolved/unsupported constant"):
        measurement.count_mapping(source, output)
    assert not output.exists()


@pytest.mark.parametrize("raw", [
    {**cell("$_NOT_", A="0", Y=2), "port_directions": {"A": "output", "Y": "input"}},
    {**cell("$_DFFE_PP_", C=2, D=3, E=4, Q=5),
     "port_directions": {"C": "input", "D": "input", "E": "output", "Q": "output"}},
])
def test_bad_directions_rejected_before_and_after_lowering(tmp_path, raw):
    source = mapped(tmp_path, {"malformed": raw})
    output = tmp_path / "counts.json"
    with pytest.raises(AssertionError, match="cell port directions disagree"):
        measurement.count_mapping(source, output)
    assert not output.exists()


def test_changed_prepared_source_rejected_before_synthesis(tmp_path, monkeypatch):
    (tmp_path / "source.sv").write_text("first")
    expected = measurement.sha(tmp_path / "source.sv")
    (tmp_path / "source.sv").write_text("changed")
    monkeypatch.setattr(measurement, "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="source changed since preparation"):
        measurement.verify_prepared(tmp_path, {"source_hashes": {"source.sv": expected}})


@pytest.mark.parametrize("name", ["measure_size.py", "check_equivalence.py"])
def test_experiments_refuse_disabled_assertions(name):
    result = subprocess.run([sys.executable, "-O", str(SCRIPT.with_name(name)), "--help"],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert "requires" in result.stderr and "assertions" in result.stderr
