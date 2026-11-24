"""Backend-selection plumbing must never stand in for complete-world acceptance.

The expensive physical writer and preflight are test doubles here. No test in
this file produces a playable world or an accepted release candidate.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shamaos.worldio import WorldWriteError
from shamaos.generator import write_placement_stages
from shamaos.model import BlockState, Placement, Vec3
from tools import build_release_candidate as builder


COMMIT = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


def _ci_report():
    names = ["world-backend", "rtl", "behavioral-and-redstone", "full-physical-synthesis"]
    names += [f"test ({system}, {python})" for system in ("ubuntu", "windows")
              for python in ("3.11", "3.12")]
    names += [f"full-physical-partitions ({index})" for index in range(12)]
    return {"head_sha": COMMIT, "conclusion": "success", "status": "completed",
            "path": ".github/workflows/ci.yml",
            "jobs": [{"name": name, "conclusion": "success"} for name in names]}


@pytest.fixture
def pipeline(monkeypatch):
    config = builder.load_config(ROOT / "configs/default.toml")
    config["generator"]["bounded_logic"] = {"cell_budget": 64, "domain_budget": 128}
    seen = []
    preflight = {"capacity_contract": {"status": "passed"},
                 "storage_minimum_fits_available_disk": True}
    state = SimpleNamespace(config=config, seen=seen, preflight=preflight,
                            acceptance=None, dirty=False, head=COMMIT,
                            readback={"status": "passed", "method": "independent-anvil-block-state-readback",
                                      "data_version": 3700, "terrain_preserved": True, "grass_surface_y": -61,
                                      "minimum_build_y": -60, "assigned_blocks": 4, "chunks": 1,
                                      "regions": 1, "region_bytes": 12288})

    def git_output(args, **kwargs):
        if args == ["git", "rev-parse", "HEAD"]:
            return state.head + "\n"
        assert args == ["git", "status", "--porcelain", "--untracked-files=no"]
        return b" M tracked-file\n" if state.dirty else b""

    def preflight_double(effective, **kwargs):
        seen.append(("preflight", deepcopy(effective)))
        return deepcopy(preflight)

    real_plan = builder.build_plan
    real_manifest = builder.manifest

    def plan(effective):
        seen.append(("plan", deepcopy(effective)))
        return real_plan(effective)

    def writer_double(world, effective, plan):
        seen.append(("writer", deepcopy(effective)))
        if state.acceptance is not None:
            (world.parent / "acceptance.json").write_text(json.dumps(state.acceptance), encoding="utf-8")
        return SimpleNamespace(written_blocks=4, system_metadata={"logic_backend": effective["generator"]["logic_backend"]},
                               stage_counts={}, world_validation=state.readback)

    def manifest(effective, plan):
        seen.append(("manifest", deepcopy(effective)))
        return real_manifest(effective, plan)

    monkeypatch.setattr(builder, "subprocess", SimpleNamespace(check_output=git_output))
    monkeypatch.setattr(builder, "load_config", lambda _: config)
    monkeypatch.setattr(builder, "generation_preflight", preflight_double)
    monkeypatch.setattr(builder, "build_plan", plan)
    monkeypatch.setattr(builder, "write_available_placements", writer_double)
    monkeypatch.setattr(builder, "manifest", manifest)
    return state


def _build(destination, **kwargs):
    return builder.build_candidate(destination, commit=COMMIT,
                                   ci=_ci_report(), config_path=ROOT / "configs/default.toml", **kwargs)


@pytest.mark.parametrize("configured,override,expected", [
    (None, None, "flat"), ("bounded", None, "bounded"),
    ("flat", "bounded", "bounded"), ("bounded", "flat", "flat"),
])
def test_effective_backend_reaches_every_stage_but_runtime_gate_stays_closed(
        tmp_path, pipeline, configured, override, expected):
    if configured is not None:
        pipeline.config["generator"]["logic_backend"] = configured
    original = deepcopy(pipeline.config)
    destination = tmp_path / "candidate"
    with pytest.raises(WorldWriteError, match="acceptance are still missing"):
        _build(destination, logic_backend=override)
    assert pipeline.config == original
    assert [name for name, _ in pipeline.seen] == ["preflight", "plan", "writer", "manifest"]
    effective = pipeline.seen[0][1]
    assert effective["world"]["minimum_build_y"] == -60
    assert effective["world"]["surface_source"] == "config-new-world"
    assert effective["generator"]["logic_backend"] == expected
    assert effective["generator"]["bounded_logic"] == original["generator"]["bounded_logic"]
    assert all(config == effective for _, config in pipeline.seen)
    preflight = json.loads((destination / "generation-preflight.json").read_text())
    generated = json.loads((destination / "generated-manifest.json").read_text())
    assert preflight["logic_backend"] == generated["logic_backend"] == expected
    assert generated["config_sha256"] == hashlib.sha256(
        json.dumps(effective, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert generated["runtime_validation"] == {"status": "not-run", "scope": "complete-machine"}
    assert not (destination / "acceptance.json").exists()


@pytest.mark.parametrize("option", [False, "true", 1])
def test_release_requires_readback_before_expensive_generation(tmp_path, pipeline, option):
    pipeline.config["generator"]["validate_after_write"] = option
    destination = tmp_path / "candidate"
    with pytest.raises(WorldWriteError, match="validate_after_write"):
        _build(destination)
    assert not destination.exists()
    assert not pipeline.seen


@pytest.mark.parametrize("changes", [
    {"status": "not-run"}, {"method": "unverified"}, {"data_version": 0},
    {"terrain_preserved": False}, {"grass_surface_y": 0}, {"minimum_build_y": -61},
    {"assigned_blocks": 0}, {"chunks": True}, {"regions": None}, {"region_bytes": -1},
])
def test_failed_or_incomplete_native_readback_cannot_stamp_manifest(tmp_path, pipeline, changes):
    pipeline.readback.update(changes)
    destination = tmp_path / "candidate"
    with pytest.raises(WorldWriteError, match="native Anvil readback"):
        _build(destination)
    assert [stage for stage, _ in pipeline.seen] == ["preflight", "plan", "writer"]
    assert (destination / "generation-preflight.json").is_file()
    assert not (destination / "generated-manifest.json").exists()


def test_release_readback_checks_accept_actual_native_writer_output(tmp_path):
    world = tmp_path / "world"
    config = builder.resolve_world_surface(builder.load_config(ROOT / "configs/default.toml"), world)
    block = Placement(Vec3(32, -60, 32), BlockState.of("minecraft:stone"), "test")
    _, _, report = write_placement_stages(world, [("test", [block])], surface_y=-61)
    assert report["assigned_blocks"] > 0
    builder._require_native_readback(report, config)


@pytest.mark.parametrize("invalid", ["unknown", "", False])
@pytest.mark.parametrize("in_config", [False, True])
def test_invalid_backend_is_rejected_before_creating_candidate(tmp_path, pipeline, invalid, in_config):
    kwargs = {}
    if in_config:
        pipeline.config["generator"]["logic_backend"] = invalid
    else:
        kwargs["logic_backend"] = invalid
    with pytest.raises(WorldWriteError, match="Logic backend"):
        _build(tmp_path / "candidate", **kwargs)
    assert not pipeline.seen
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize("failure", ["capacity", "disk"])
def test_bounded_selection_does_not_bypass_preflight(tmp_path, pipeline, failure):
    if failure == "capacity":
        pipeline.preflight["capacity_contract"]["status"] = "failed"
    else:
        pipeline.preflight["storage_minimum_fits_available_disk"] = False
    with pytest.raises(WorldWriteError, match="preflight"):
        _build(tmp_path / "candidate", logic_backend="bounded")
    assert [name for name, _ in pipeline.seen] == ["preflight"]
    report = json.loads((tmp_path / "candidate" / "generation-preflight.json").read_text())
    assert report["logic_backend"] == "bounded"
    assert not (tmp_path / "candidate" / "generated-manifest.json").exists()


@pytest.mark.parametrize("failure", ["ci", "head", "dirty", "existing"])
def test_bounded_selection_preserves_source_and_destination_guards(tmp_path, pipeline, failure):
    destination = tmp_path / "candidate"
    ci = _ci_report()
    if failure == "ci":
        ci["jobs"].pop()
    elif failure == "head":
        pipeline.head = "b" * 40
    elif failure == "dirty":
        pipeline.dirty = True
    else:
        destination.mkdir()
        (destination / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(WorldWriteError):
        builder.build_candidate(destination, commit=COMMIT, ci=ci,
            config_path=ROOT / "configs/default.toml", logic_backend="bounded")
    assert not pipeline.seen
    if failure == "existing":
        assert (destination / "keep.txt").read_text() == "keep"
        assert len(list(destination.iterdir())) == 1
    else:
        assert not destination.exists()


def test_bounded_generation_cannot_accept_an_invented_acceptance_file(tmp_path, pipeline):
    pipeline.acceptance = {"status": "passed"}
    with pytest.raises(WorldWriteError, match="untested worlds cannot be released"):
        _build(tmp_path / "candidate", logic_backend="bounded")


def test_cli_bounded_selection_is_applied_and_missing_runtime_returns_failure(tmp_path, pipeline, capsys):
    ci = tmp_path / "ci.json"
    ci.write_text(json.dumps(_ci_report()), encoding="utf-8")
    result = builder.main(["--candidate", str(tmp_path / "candidate"), "--commit", COMMIT,
                           "--ci-report", str(ci), "--logic-backend", "bounded"])
    assert result == 1
    assert all(config["generator"]["logic_backend"] == "bounded" for _, config in pipeline.seen)
    assert "acceptance are still missing" in capsys.readouterr().out


def test_cli_rejects_unknown_backend_without_reading_ci_or_starting_generation(tmp_path, pipeline):
    with pytest.raises(SystemExit) as exc:
        builder.main(["--candidate", str(tmp_path / "candidate"), "--commit", COMMIT,
                      "--ci-report", str(tmp_path / "absent.json"), "--logic-backend", "unknown"])
    assert exc.value.code == 2
    assert not pipeline.seen
    assert not (tmp_path / "candidate").exists()
