"""Run unchanged regressions with process-local candidate bindings only."""
from __future__ import annotations

if not __debug__:
    raise RuntimeError("Column-search acceptance requires assertions; -O/-OO is unsupported")

import argparse
import importlib.util
from pathlib import Path
import sys


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measured-apron", action="store_true")
    arguments, pytest_arguments = parser.parse_known_args()
    directory = Path(__file__).resolve().parent
    candidate = load("shamaos.hardware._unit_step_column_router", directory / "external_router_unit.py")
    from shamaos.hardware import external_router, local_router
    external_router._plan_escapes = candidate._plan_escapes
    local_router._plan_escapes = candidate._plan_escapes
    if arguments.measured_apron:
        import shamaos.hardware
        placement = load("shamaos.hardware.partition_placement", directory / "partition_placement_snapshot.py")
        shamaos.hardware.partition_placement = placement
    import pytest
    raise SystemExit(pytest.main(pytest_arguments))
