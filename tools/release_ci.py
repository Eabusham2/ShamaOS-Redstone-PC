"""Acquire exact-source GitHub evidence and append release assets without deleting any.

Uses the runner's authenticated `gh` client. No credentials enter downloaded
archives, command arguments, manifests or logs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote

from shamaos.release import validate_ci
from shamaos.release_io import extract_zip, sha256
from shamaos.worldio import WorldWriteError


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def paginated(path, key):
    separator = "&" if "?" in path else "?"
    page = 1
    while True:
        response = api(f"{path}{separator}per_page=100&page={page}")
        values = response[key] if key else response
        yield from values
        if len(values) < 100:
            break
        page += 1


def download_artifact(repo: str, artifact: dict, destination: Path):
    if artifact.get("expired"):
        raise WorldWriteError(f"CI artifact {artifact['name']} expired; rerun exact-commit CI before releasing.")
    with destination.open("xb") as stream:
        subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts/{int(artifact['id'])}/zip"],
                       stdout=stream, check=True)
    digest = artifact.get("digest")
    if digest and digest != "sha256:" + sha256(destination):
        raise WorldWriteError(f"Downloaded CI artifact {artifact['name']} failed its GitHub digest check.")


def acquire(repo: str, commit: str, destination: Path, candidate_run: int | None = None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise WorldWriteError("Invalid repository or source commit")
    destination.mkdir(parents=True, exist_ok=False)
    ci_run = None
    for run in paginated(f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={commit}&status=success", "workflow_runs"):
        if run["head_sha"] == commit and run.get("event") != "pull_request" and run.get("head_repository", {}).get("full_name") == repo:
            jobs = list(paginated(f"repos/{repo}/actions/runs/{run['id']}/jobs", "jobs"))
            report = {**run, "jobs": jobs}
            try:
                validate_ci(report, commit)
            except WorldWriteError:
                continue
            ci_run = report
            break
    if ci_run is None:
        raise WorldWriteError("No green exact-commit CI run includes all twelve full-synthesis partitions and all other gates.")
    (destination / "ci.json").write_text(json.dumps(ci_run, indent=2) + "\n", encoding="utf-8")
    artifacts = list(paginated(f"repos/{repo}/actions/runs/{ci_run['id']}/artifacts", "artifacts"))
    required = {f"full-soc-{name}" for name in ("u_cpu", "u_input", "u_gpu", "u_display_bridge", "u_vram", "u_services",
                "u_kernel", "u_fs", "u_asm", "u_cache", "u_ram", "u_flash")}
    required |= {f"full-soc-report-{commit}", f"shamaos-tested-fixture-{commit}"}
    if not required.issubset({artifact["name"] for artifact in artifacts}):
        raise WorldWriteError("The exact-commit CI run is missing original validation artifacts.")
    archive_dir = destination / "ci-artifacts"
    archive_dir.mkdir()
    for artifact in artifacts:
        name = artifact["name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise WorldWriteError("A CI artifact has an unsafe file name.")
        download_artifact(repo, artifact, archive_dir / (name + ".zip"))
    runs = ([api(f"repos/{repo}/actions/runs/{candidate_run}")] if candidate_run else
            paginated(f"repos/{repo}/actions/runs?head_sha={commit}&status=success", "workflow_runs"))
    for run in runs:
        if (run.get("head_sha") != commit or run.get("conclusion") != "success" or run.get("status") != "completed"
                or run.get("event") == "pull_request" or run.get("head_repository", {}).get("full_name") != repo):
            continue
        for artifact in paginated(f"repos/{repo}/actions/runs/{run['id']}/artifacts", "artifacts"):
            if artifact["name"] != f"shamaos-full-world-candidate-{commit}":
                continue
            archive = destination / "candidate.zip"
            download_artifact(repo, artifact, archive)
            extract_zip(archive, destination / "candidate")
            (destination / "candidate-run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
            print(f"Downloaded full-world candidate from same-commit run {run['id']}; independent release checks follow.")
            return
    if candidate_run:
        raise WorldWriteError("That run does not contain a successful same-commit complete-world candidate artifact.")
    print("No accepted full-world artifact exists. The workflow will run the real full generator and its release gates.")


def append_assets(repo: str, tag: str, folder: Path):
    """Retain every existing asset. Reruns skip only byte-identical uploads."""
    manifest = json.loads((folder / "release-manifest.json").read_text(encoding="utf-8"))
    if api(f"repos/{repo}/commits/{quote(tag, safe='')}").get("sha") != manifest["source_commit"]:
        raise WorldWriteError("The release tag moved after packaging; refusing to attach files to a different source commit.")
    release = api(f"repos/{repo}/releases/tags/{quote(tag, safe='')}")
    existing = {item["name"]: item for item in paginated(f"repos/{repo}/releases/{release['id']}/assets", None)}
    upload = []
    with tempfile.TemporaryDirectory(prefix="shamaos-existing-assets-") as temporary:
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            if path.name in existing:
                local = Path(temporary) / path.name
                with local.open("wb") as stream:
                    subprocess.run(["gh", "api", "-H", "Accept: application/octet-stream",
                        f"repos/{repo}/releases/assets/{existing[path.name]['id']}"], stdout=stream, check=True)
                if sha256(local) != sha256(path):
                    raise WorldWriteError(f"Existing asset {path.name} differs; refusing to delete or replace it.")
            else:
                upload.append(path)
        # All collisions are checked before the first new upload.
        for path in upload:
            subprocess.run(["gh", "release", "upload", tag, str(path), "--repo", repo], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    get = sub.add_parser("acquire")
    get.add_argument("--commit", required=True)
    get.add_argument("--output", type=Path, required=True)
    get.add_argument("--candidate-run", type=int)
    upload = sub.add_parser("upload")
    upload.add_argument("--tag", required=True)
    upload.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    args = parser.parse_args()
    try:
        if not args.repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
            raise WorldWriteError("Provide --repo owner/repository or run inside GitHub Actions.")
        if args.command == "acquire":
            acquire(args.repo, args.commit, args.output, args.candidate_run)
        else:
            append_assets(args.repo, args.tag, args.folder)
        return 0
    except (WorldWriteError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Release blocked: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
