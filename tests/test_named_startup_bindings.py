"""Exercise the production flat/bounded seam for the measured startup component."""
import hashlib
import json
from pathlib import Path
import tarfile

import pytest

from shamaos import bounded_system, system_build
from shamaos.model import Vec3
from shamaos.resource_accounting import physical_netlist_resource_lower_bound


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "experiments/named_startup_domain"
TOP = "shama_startup_control"


@pytest.fixture(scope="module")
def startup(tmp_path_factory):
    directory = tmp_path_factory.mktemp("startup-map")
    with tarfile.open(EVIDENCE / "evidence/native.tar.xz") as saved:
        mapped = saved.extractfile("positive/startup.json").read()
        shell_raw = saved.extractfile("shell/mapped.json").read()
        native = json.load(saved.extractfile("positive/report.json"))
        case = json.load(saved.extractfile("positive/power-cycle.json"))
    shell = json.loads(shell_raw)["modules"]["shama_soc"]
    assert hashlib.sha256(mapped).hexdigest() == native["mapped_sha256"]
    assert hashlib.sha256(shell_raw).hexdigest() == json.loads((EVIDENCE / "binding.json").read_bytes())["shell_sha256"]
    path = directory / "startup.json"
    path.write_bytes(mapped)
    part = bounded_system.place_startup_control(path, surface_y=-61, maximum_y=285)
    return path, part, shell, case


def test_production_shell_has_one_startup_driver_for_each_safety_output(startup):
    _, _, module, _ = startup
    names = module["netnames"]
    children = dict(system_build.PARTITION_SPECS)
    cell = module["cells"]["u_startup"]
    for name in ("rst", "core_clk", "clock_hold"):
        bit, = names[name]["bits"]
        drivers = [(instance, pin) for instance, item in module["cells"].items()
                   for pin, wires in item["connections"].items()
                   if item.get("port_directions", {}).get(pin, "output" if pin in ("Q","Y") else "input") == "output" and bit in wires]
        assert drivers == [("u_startup", name)]
        assert cell["connections"][name] == [bit]
    for instance in children.keys() - {"u_startup"}:
        connections = module["cells"][instance]["connections"]
        assert connections["rst"] == names["rst"]["bits"]
        assert connections["clk"] == names["clk" if instance == "u_input" else "core_clk"]["bits"]
    assert cell["connections"]["clk"] == module["ports"]["clk"]["bits"]
    assert cell["connections"]["clock_hold"] == module["ports"]["clock_hold"]["bits"]
    assert cell["connections"]["divider_high"] == [names["core_div_counter"]["bits"][-1]]


@pytest.mark.parametrize("backend", ["flat", "bounded"])
def test_actual_backend_places_and_routes_every_named_contact(startup, backend, monkeypatch, tmp_path):
    path, reference, full_shell, native_case = startup
    macro = full_shell["cells"]["u_startup"]
    directions = macro["port_directions"]
    # Keep the complete production macro and its nine exact signal IDs.
    # Other macro geometries and global routes are outside this placement test.
    ports = {pin: {"direction": direction, "bits": macro["connections"][pin]}
             for pin, direction in directions.items()}
    cut = {"modules": {"shama_soc": {"ports": ports, "cells": {"u_startup": macro}}}}
    def copy_actual(rtl_files, *, top, output_json, **options):
        if top == TOP:
            assert list(rtl_files) == [ROOT / "rtl/shama_startup_control.sv"]
            output_json.write_bytes(path.read_bytes())
        else:
            assert top == "shama_soc" and options["blackbox_modules"] == (TOP,)
            output_json.write_text(json.dumps(cut))
    original_position = system_build._partition_origin
    instance_index = system_build.PARTITION_SPECS.index(("u_startup", TOP))
    monkeypatch.setattr(system_build, "PARTITION_SPECS", (("u_startup", TOP),))
    monkeypatch.setattr(system_build, "synthesize_json", copy_actual)
    monkeypatch.setattr(system_build, "_partition_origin", lambda base, _: original_position(base, instance_index))
    logic = system_build._prepare_partitioned_logic(
        root=ROOT, build=tmp_path, yosys="unused-from-retained-map",
        base_origin=Vec3(73, -60, 91), minimum_build_y=-60, logic_backend=backend)
    part = dict(logic.partitions)["u_startup"]
    assert type(part) is bounded_system.StartupPartition
    delta = Vec3(part.bounds.min.x-reference.bounds.min.x, part.bounds.min.y-reference.bounds.min.y,
                 part.bounds.min.z-reference.bounds.min.z)
    def translated(point):
        return point.offset(delta.x, delta.y, delta.z)
    shell = logic.shell if backend == "flat" else logic.shell.physical
    nets = {net.bit: net for net in shell.nets}
    for label, direction in directions.items():
        coordinates = native_case["inputs" if direction == "input" else "outputs"][label]
        contact, = (part.input_ports if direction == "input" else part.output_ports)[label]
        assert contact == translated(Vec3(*coordinates))
        net = nets[macro["connections"][label][0]]
        if direction == "output":
            assert (net.source.name, net.source.pin, net.source.pos) == ("macro:u_startup[0]", label, contact)
        else:
            assert [(pin.name,pin.pin,pin.pos) for pin in net.sinks] == [("macro:u_startup[0]",label,contact)]
    manifest = logic.manifest()["partitions"]["u_startup"]
    assert manifest["cell_count"] == 29 and manifest["original_port_bits"] == 9
    original, before, after = reference.request_clock_repeater()
    location, translated_before, translated_after = part.request_clock_repeater()
    assert (location, translated_before, translated_after) == (translated(original), before, after)
    assert manifest["startup_private_clock_delay"]["receiver"] == "reset_pulse_request"
    # Compare every emitted electrode, route and support, including the corrected
    # delay, after moving the real native structure into this backend's district.
    emitted = {p.pos:p.block for p in part.iter_placements()}
    assert emitted == {translated(p.pos):p.block for p in reference.iter_placements()}
    assert emitted[location] == after and dict(after.properties)["delay"] == "4"
    cost = physical_netlist_resource_lower_bound(part)
    assert cost["cells"] == 29 and cost["exact_child_route_chunks"] > 0


def test_retained_named_startup_reload_and_warmup_free_control():
    from experiments.named_startup_domain.replay_reopen import review

    result = review(EVIDENCE / "evidence/independent-reopen.tar.xz")
    assert result["cold"]["facade"]["complete_reset_pulses"] == 80
    assert result["reopened"]["facade"]["complete_divided_pulses"] == 1
    assert result["immediate_load"]["facade"]["complete_reset_pulses"] == 8
    assert all(case["registers"]["reset_pulse_request"]["setup_min_rt"] >= 8
               for case in result.values())
