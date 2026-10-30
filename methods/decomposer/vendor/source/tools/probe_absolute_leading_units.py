#!/usr/bin/env python3
"""Probe whether model-supplied absolute leading uses source pixels, not points."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.quality import affected_scene_region, compare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--factor", required=True, type=float)
    args = parser.parse_args()
    if not .6 <= args.factor <= 1:
        raise ValueError("factor outside bounded point-conversion probe")
    run, out = args.run.resolve(), args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    original = read_json(run / "output" / "scene.json")
    candidate = read_json(run / "output" / "scene.json")
    changes = []
    for element in candidate["elements"]:
        if element["kind"] != "text":
            continue
        style = element["style"]
        value = float(style.get("line_spacing") or 0)
        if value <= 3:
            continue
        converted = round(value * args.factor, 4)
        style["line_spacing"] = converted
        changes.append({"id": element["id"], "old": value, "new": converted})
    if not changes:
        raise ValueError("no absolute leading to probe")
    out.mkdir(parents=True)
    write_json(out / "scene.json", candidate)
    report = compile_scene(out / "scene.json", out, read_json(args.config), True)
    region = affected_scene_region(original, candidate, [item["id"] for item in changes])
    assessment = compare(
        run / "input" / "normalized.png", run / "output" / "preview.png",
        out / "preview.png", region,
    )
    result = {
        "owner_stage": "compiler",
        "mechanism": "absolute-leading-source-pixels-vs-powerpoint-points",
        "factor": args.factor,
        "affected_label_ids": sorted({item["id"].split("/", 1)[0] for item in changes}),
        "affected_primitive_ids": [item["id"] for item in changes],
        "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners",
        "changes": changes,
        "compiler_status": report["status"],
        "compiler_fallbacks": report["compiler"].get("fallbacks", []),
        "assessment": assessment,
    }
    write_json(out / "probe.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
