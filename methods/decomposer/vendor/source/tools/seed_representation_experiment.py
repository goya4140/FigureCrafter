"""Seed an isolated run from a validated, immutable pre-Representation snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


FROZEN_FILES = (
    "input/input.json",
    "input/normalized.png",
    "observations/candidates.json",
    "observations/evidence-ir.json",
    "observations/ocr.stdout.log",
    "observations/ocr.stderr.log",
    "recognition/recognition.json",
    "calls/recognition/metadata.json",
    "representation/asset-decisions.json",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed(source: Path, target: Path) -> dict[str, str]:
    if target.exists():
        raise FileExistsError(f"experiment directory already exists: {target}")
    metadata = json.loads((source / "calls/recognition/metadata.json").read_text())
    if metadata.get("status") != "complete" or metadata.get("fallback_used"):
        raise ValueError("source Recognition is not a successful model stage")
    expected = {name: digest(source / name) for name in FROZEN_FILES}
    for directory in ("input", "observations", "recognition", "calls/recognition"):
        shutil.copytree(source / directory, target / directory)
    target.joinpath("representation").mkdir(parents=True)
    shutil.copy2(source / "representation/asset-decisions.json", target / "representation/asset-decisions.json")
    actual = {name: digest(target / name) for name in FROZEN_FILES}
    if actual != expected:
        raise RuntimeError("copied upstream files do not match source hashes")
    manifest = {"source_run": str(source.resolve()), "files": expected}
    (target / "frozen-upstream.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return expected


def verify(target: Path) -> None:
    manifest = json.loads((target / "frozen-upstream.json").read_text())
    current = {name: digest(target / name) for name in manifest["files"]}
    if current != manifest["files"]:
        raise RuntimeError("upstream snapshot changed during experiment")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify(args.target)
        print("frozen upstream verified")
    else:
        hashes = seed(args.source, args.target)
        print(f"seeded {args.target} from {args.source} ({len(hashes)} frozen files)")
