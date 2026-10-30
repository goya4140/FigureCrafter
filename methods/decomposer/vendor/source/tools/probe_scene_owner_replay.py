#!/usr/bin/env python3
"""Isolated owner-group replay between two scenes, with a fresh PPTX render."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, sha256, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.quality import affected_scene_region, compare


def replayed_scene(baseline: dict, donor: dict, owner_ids: list[str]) -> dict:
    if baseline["canvas"] != donor["canvas"]:
        raise ValueError("owner replay requires the same canvas")
    if len(set(owner_ids)) != len(owner_ids) or not owner_ids:
        raise ValueError("owner replay requires unique nonempty IDs")
    candidate = copy.deepcopy(baseline)
    donors = {item["id"]: item for item in donor["elements"]}
    target_indices = {item["id"]: index for index, item in enumerate(candidate["elements"])}
    for owner_id in owner_ids:
        if owner_id not in donors or owner_id not in target_indices:
            raise ValueError(f"owner missing from matched scenes: {owner_id}")
        current = candidate["elements"][target_indices[owner_id]]
        replacement = donors[owner_id]
        for field in ("kind", "source_label_id", "text"):
            if current.get(field) != replacement.get(field):
                raise ValueError(f"owner source identity changed: {owner_id}/{field}")
        candidate["elements"][target_indices[owner_id]] = copy.deepcopy(replacement)
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--donor", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--owner-id", action="append", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise FileExistsError(output)
    baseline_path, donor_path = args.baseline.resolve(), args.donor.resolve()
    baseline, donor = read_json(baseline_path), read_json(donor_path)
    candidate = replayed_scene(baseline, donor, args.owner_id)
    output.mkdir(parents=True)
    write_json(output / "scene.json", candidate)
    report = compile_scene(output / "scene.json", output, read_json(args.config), True)
    build_provenance(candidate, output / "editable.pptx", output)
    region = affected_scene_region(baseline, candidate, args.owner_id)
    assessment = compare(args.reference, baseline_path.parent / "preview.png",
                         output / "preview.png", region)
    before_report = read_json(baseline_path.parent / "report.json")
    result = {
        "schema_version": "decomposer.owner-replay-probe.v1",
        "scope": "isolated-mechanism-diagnostic-no-adoption",
        "baseline_scene_sha256": sha256(baseline_path),
        "donor_scene_sha256": sha256(donor_path),
        "candidate_scene_sha256": sha256(output / "scene.json"),
        "candidate_pptx_sha256": sha256(output / "editable.pptx"),
        "owner_ids": args.owner_id,
        "affected_label_ids": sorted({item["source_label_id"] for item in candidate["elements"]
                                      if item["id"] in args.owner_id}),
        "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners",
        "assessment": assessment,
        "compiler_status": report["status"],
        "baseline_fallbacks": before_report["compiler"].get("fallbacks", []),
        "candidate_fallbacks": report["compiler"].get("fallbacks", []),
    }
    write_json(output / "probe.json", result)
    print({"owners": args.owner_id, "local": assessment["after_local"],
           "global": assessment["after_global"], "fallbacks": result["candidate_fallbacks"]})


if __name__ == "__main__":
    main()
