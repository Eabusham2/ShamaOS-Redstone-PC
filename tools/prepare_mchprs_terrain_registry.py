"""Reproduce a three-block native-terrain registry extension in a private copy.

The pinned engine ships the authoritative Java registry but omits these blocks
from its implemented subset. Only generated registration/classification is
extended. Redstone source and all pre-existing block arms are unchanged.
Both sparse native-world and dense reference harnesses use that same extension;
their original unmodified-registry binaries remain separate build targets.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import shutil

REPOSITORY = Path(__file__).resolve().parents[1]
PINNED_REVISION = "f9e10d2572116bf5dcb9a36a305f28e40641a61a"
EXPECTED_GENERATED_SHA256 = "3dcbf366da901d487c04fd6fbf60930614a44e384be160ea2d279d6861c4a468"
EXPECTED_REGISTRY_SHA256 = "18d83f2c0c04adda138cd8caeb8db14442628f1e6ca3c949b202db2d63573f73"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def insert_method(text, method, anchor, added):
    start = text.index("    pub fn " + method + "(")
    end = text.find("    pub fn ", start + 1)
    if end < 0:
        end = len(text)
    section = text[start:end]
    assert section.count(anchor) == 1, (method, anchor)
    section = section.replace(anchor, anchor + added)
    return text[:start] + section + text[end:]


def prepare_runner(root: Path, *, directory: str, original_manifest: Path,
                   original_name: str, name: str, terrain_feature: bool = False) -> dict:
    """Write a private harness project using unchanged source and lock seed."""
    runner = root / directory
    runner.mkdir(exist_ok=True)
    original = original_manifest.read_text()
    package = f'name = "{original_name}"'
    if original.count(package) != 1:
        raise ValueError("original terrain harness package identity is ambiguous")
    manifest = original.replace(package, f'name = "{name}"', 1)
    if terrain_feature:
        feature = '[features]\nterrain_registry = []'
        if manifest.count(feature) != 1:
            raise ValueError("sparse harness terrain feature declaration changed")
        manifest = manifest.replace(feature, '[features]\ndefault = ["terrain_registry"]\nterrain_registry = []', 1)
    source = original_manifest.parent / "src/main.rs"
    manifest += '\n[[bin]]\nname=' + json.dumps(name) + '\npath=' + json.dumps(str(source)) + '\n'
    manifest += '\n[patch."https://github.com/MCHPR/MCHPRS"]\nmchprs_blocks={path="../vendor/mchprs/crates/blocks"}\n'
    (runner / "Cargo.toml").write_text(manifest)
    # Preserve every non-local dependency resolution from the pinned harness.
    # Cargo makes the copied block crate and its pinned path dependencies local.
    original_lock = original_manifest.with_name("Cargo.lock")
    lock = original_lock.read_text()
    if lock.count(package) != 1:
        raise ValueError("original terrain harness lock identity is ambiguous")
    (runner / "Cargo.lock").write_text(lock.replace(package, f'name = "{name}"', 1))
    return {"manifest": str(runner / "Cargo.toml"), "binary_name": name,
            "manifest_sha256": sha(runner / "Cargo.toml"),
            "original_manifest_sha256": sha(original_manifest),
            "original_lock_sha256": sha(original_lock), "unchanged_source": str(source),
            "unchanged_source_sha256": sha(source)}


def prepare(source: Path, output: Path) -> dict:
    PINNED = source.resolve()
    ROOT = output.resolve()
    DEST = ROOT / "vendor/mchprs"
    if sha(PINNED / "crates/blocks/src/generated.rs") != EXPECTED_GENERATED_SHA256:
        raise ValueError("the source is not the unchanged pinned block registry")
    if sha(PINNED / "mc_data/blocks.json") != EXPECTED_REGISTRY_SHA256:
        raise ValueError("the bundled native Java registry does not match the pinned revision")
    expected_source = json.loads((REPOSITORY / "tests/mchprs_sparse_runtime/pinned-source-provenance.json").read_text())
    if expected_source["revision"] != PINNED_REVISION:
        raise ValueError("source manifest revision differs from the pinned engine")
    for relative, expected_hash in expected_source["files_sha256"].items():
        if sha(PINNED / relative) != expected_hash:
            raise ValueError(f"pinned engine source changed: {relative}")
    ROOT.mkdir(parents=True, exist_ok=True)
    if not DEST.exists():
        shutil.copytree(PINNED, DEST, ignore=shutil.ignore_patterns(".git", "target"))
    for relative, expected_hash in expected_source["files_sha256"].items():
        if relative in ("crates/blocks/src/generated.rs", "mc_data/gen_info.yaml"):
            continue
        if sha(DEST / relative) != expected_hash:
            raise ValueError(f"private engine contains an unreviewed source change: {relative}")
    registry = json.loads((PINNED / "mc_data/blocks.json").read_text())
    expected = {
        "minecraft:bedrock": [{"default": True, "id": 79}],
        "minecraft:dirt": [{"default": True, "id": 10}],
        "minecraft:grass_block": [{"id": 8, "properties": {"snowy": "true"}},
                                  {"default": True, "id": 9, "properties": {"snowy": "false"}}],
    }
    for name, states in expected.items():
        assert registry[name]["states"] == states
    rel = "crates/blocks/src/generated.rs"
    original = (PINNED / rel).read_text()
    text = original.replace("    Air,\n    Stone,", "    Air,\n    Stone,\n    Bedrock,\n    Dirt,\n    GrassBlock { snowy: bool },", 1)
    text = insert_method(text, "get_name", '            Block::Stone => "minecraft:stone",',
                         '\n            Block::Bedrock => "minecraft:bedrock",\n            Block::Dirt => "minecraft:dirt",\n            Block::GrassBlock { .. } => "minecraft:grass_block",')
    text = insert_method(text, "from_name", '            "minecraft:stone" => Block::Stone,',
                         '\n            "minecraft:bedrock" => Block::Bedrock,\n            "minecraft:dirt" => Block::Dirt,\n            "minecraft:grass_block" => Block::GrassBlock { snowy: false },')
    for method in ("is_solid", "is_cube"):
        text = insert_method(text, method, "            Block::Stone => true,",
                             "\n            Block::Bedrock | Block::Dirt | Block::GrassBlock { .. } => true,")
    text = insert_method(text, "set_properties", "            Block::Stone => {}",
                         '\n            Block::Bedrock | Block::Dirt => {}\n            Block::GrassBlock { snowy } => { <bool as BlockProperty>::decode(snowy, &props, "snowy"); }')
    text = insert_method(text, "properties", "            Block::Stone => {}",
                         '\n            Block::Bedrock | Block::Dirt => {}\n            Block::GrassBlock { snowy } => { <bool as BlockProperty>::encode(*snowy, &mut props, "snowy"); }')
    for method in ("rotate", "flip"):
        text = insert_method(text, method, "            Block::Stone => {}",
                             "\n            Block::Bedrock | Block::Dirt | Block::GrassBlock { .. } => {}")
    text = insert_method(text, "get_id", "            Block::Stone => 1,",
                         "\n            Block::Bedrock => 79,\n            Block::Dirt => 10,\n            Block::GrassBlock { snowy } => if snowy { 8 } else { 9 },")
    start = text.index("    pub fn from_id(")
    pos = text.index("        match id {", start) + len("        match id {")
    text = text[:pos] + "\n            79 => Block::Bedrock,\n            10 => Block::Dirt,\n            8..=9 => Block::GrassBlock { snowy: id == 8 }," + text[pos:]
    text = insert_method(text, "is_stone", "            Block::Stone => true,", "\n            Block::Bedrock => true,")
    (DEST / rel).write_text(text)
    yaml_rel = "mc_data/gen_info.yaml"
    original_yaml = (PINNED / yaml_rel).read_text()
    yaml = original_yaml.replace('  "minecraft:stone": "solid,cube,simple_item,stone"',
                                '  "minecraft:stone": "solid,cube,simple_item,stone"\n'
                                '  "minecraft:bedrock": "solid,cube,stone"\n'
                                '  "minecraft:dirt": "solid,cube"\n'
                                '  "minecraft:grass_block": "solid,cube"', 1)
    (DEST / yaml_rel).write_text(yaml)
    patch = "".join(difflib.unified_diff(original.splitlines(True), text.splitlines(True),
                                         fromfile="a/" + rel, tofile="b/" + rel))
    patch += "".join(difflib.unified_diff(original_yaml.splitlines(True), yaml.splitlines(True),
                                          fromfile="a/" + yaml_rel, tofile="b/" + yaml_rel))
    (ROOT / "native-terrain-registry.patch").write_text(patch)
    redstone = {}
    for path in sorted((PINNED / "crates/redstone").rglob("*")):
        if path.is_file():
            name = str(path.relative_to(PINNED))
            assert path.read_bytes() == (DEST / name).read_bytes(), name
            redstone[name] = sha(path)
    report = {
        "engine_revision": "f9e10d2572116bf5dcb9a36a305f28e40641a61a",
        "patch_sha256": sha(ROOT / "native-terrain-registry.patch"),
        "bundled_java_registry_sha256": sha(PINNED / "mc_data/blocks.json"),
        "original_generated_sha256": sha(PINNED / rel),
        "extended_generated_sha256": sha(DEST / rel),
        "added_native_states": expected,
        "redstone_source_unchanged_sha256": redstone,
        "scope": "native inert full-cube terrain registration only; all redstone implementation unchanged",
    }
    (ROOT / "terrain-engine-provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    expected_patch = REPOSITORY / "tests/mchprs_sparse_runtime/native-terrain-registry.patch"
    if patch != expected_patch.read_text():
        raise ValueError("generated terrain extension differs from the audited patch")
    report["runners"] = {
        "sparse": prepare_runner(ROOT, directory="terrain-runner",
            original_manifest=REPOSITORY / "tests/mchprs_sparse_runtime/Cargo.toml",
            original_name="shama-sparse-world-check", name="shama-sparse-world-check-terrain", terrain_feature=True),
        "dense": prepare_runner(ROOT, directory="dense-terrain-runner",
            original_manifest=REPOSITORY / "tests/mchprs_runtime/Cargo.toml",
            original_name="shama-redstone-check", name="shama-dense-world-check-terrain"),
    }
    report["runner_manifest"] = report["runners"]["sparse"]["manifest"]
    report["dense_runner_manifest"] = report["runners"]["dense"]["manifest"]
    (ROOT / "terrain-engine-provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    import argparse
    import os
    import subprocess
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="unchanged pinned MCHPRS checkout")
    parser.add_argument("--output", type=Path, required=True, help="private generated engine/build directory")
    parser.add_argument("--build", action="store_true", help="build both terrain harnesses offline using cached Rust dependencies")
    args = parser.parse_args()
    report = prepare(args.source, args.output)
    if args.build:
        environment = dict(os.environ, SHAMA_TERRAIN_PATCH_SHA256=report["patch_sha256"])
        for runner in report["runners"].values():
            subprocess.run(["cargo", "build", "--offline", "--release", "--manifest-path", runner["manifest"]],
                           env=environment, check=True)
    print(json.dumps({key: value for key, value in report.items() if key != "redstone_source_unchanged_sha256"}, indent=2))
