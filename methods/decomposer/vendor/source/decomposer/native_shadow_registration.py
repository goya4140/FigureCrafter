"""Measured replacement of flat shadow proxies with native editable shadows."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
from typing import Any

from PIL import Image

from .common import read_json, sha256, write_json
from .pptx import compile_scene
from .provenance import build as build_provenance
from .quality import compare
from .text_registration import ARTIFACTS, _adopt_candidate


def pairs(scene: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in scene["elements"]}
    z_order = scene.get("z_order", [])
    found = []
    for proxy in scene["elements"]:
        if proxy["kind"] != "rect" or not proxy["id"].endswith("/shadow"):
            continue
        panel_id = proxy["id"].removesuffix("/shadow") + "/panel"
        panel = by_id.get(panel_id)
        if not panel or panel["kind"] != "rect" or panel["source_label_id"] != proxy["source_label_id"]:
            continue
        a, b = proxy["bbox"], panel["bbox"]
        dx, dy = a[0] - b[0], a[1] - b[1]
        fill = str(proxy["style"].get("fill") or "")
        neutral_gray = (len(fill) == 7 and fill.startswith("#")
                        and all(char in "0123456789ABCDEFabcdef" for char in fill[1:])
                        and fill[1:3] == fill[3:5] == fill[5:7]
                        and 30 <= int(fill[1:3], 16) <= 220) if len(fill) == 7 else False
        if (not 2 <= dx <= 20 or not 2 <= dy <= 20 or abs(dx - dy) > 3
                or a[2:] != b[2:] or panel["style"].get("shadow")
                or proxy["style"].get("stroke") != "none"
                or not neutral_gray or panel["style"].get("fill") in ("none", "#FFFFFF")
                or proxy["id"] not in z_order or panel_id not in z_order
                or z_order.index(proxy["id"]) >= z_order.index(panel_id)):
            continue
        found.append({"proxy_id": proxy["id"], "panel_id": panel_id,
                      "source_label_id": proxy["source_label_id"],
                      "offset_x": dx, "offset_y": dy})
    return found


def variants(pair_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not pair_rows:
        return []
    displacement = sum((row["offset_x"] + row["offset_y"]) / 2 for row in pair_rows) / len(pair_rows)
    recipes = ((.67, .67, .35), (1.33, .83, .30), (1.67, .83, .25))
    return [
        {"candidate_id": f"native-shadow-{index}",
         "shadow": {"type": "outer", "color": "666666", "opacity": opacity,
                    "blur": round(min(20, displacement * blur_factor), 4),
                    "offset": round(min(20, displacement * offset_factor), 4),
                    "angle": 45.0}}
        for index, (blur_factor, offset_factor, opacity) in enumerate(recipes, 1)
    ]


def apply(scene: dict[str, Any], pair_rows: list[dict[str, Any]], variant: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(scene)
    by_id = {item["id"]: item for item in candidate["elements"]}
    removed = set()
    for row in pair_rows:
        proxy, panel = by_id.get(row["proxy_id"]), by_id.get(row["panel_id"])
        if proxy is None or panel is None or panel["source_label_id"] != proxy["source_label_id"]:
            raise ValueError("shadow pair identity drift")
        panel["style"]["shadow"] = copy.deepcopy(variant["shadow"])
        removed.add(proxy["id"])
    candidate["elements"] = [item for item in candidate["elements"] if item["id"] not in removed]
    candidate["z_order"] = [item for item in candidate["z_order"] if item not in removed]
    return candidate


def run(root: Path, reference_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    output = root / "output"
    evidence = root / "native-shadow-registration"
    prior_path = root / "source-line-registration" / "decision.json"
    if not prior_path.is_file():
        raise ValueError("native shadow registration requires source-line evidence")
    prior = read_json(prior_path)
    prior_final = prior["final_scene_sha256"]
    current_hash = sha256(output / "scene.json")
    if evidence.exists():
        decision_path = evidence / "decision.json"
        if not decision_path.is_file():
            raise ValueError("incomplete native-shadow evidence; inspect before resuming")
        decision = read_json(decision_path)
        if current_hash == decision.get("final_scene_sha256"):
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and decision.get("accepted"):
            _adopt_candidate(evidence / "final", output)
            if (sha256(output / "scene.json") != decision.get("final_scene_sha256")
                    or sha256(output / "editable.pptx") != decision.get("final_pptx_sha256")):
                raise ValueError("reused native-shadow candidate hash mismatch")
            return decision
        if current_hash == decision.get("baseline_scene_sha256") and not decision.get("accepted"):
            return decision
        raise ValueError("native-shadow registration identity drift")
    if current_hash != prior_final:
        raise ValueError("native-shadow input is not source-line final scene")
    scene = read_json(output / "scene.json")
    pair_rows = pairs(scene)
    candidates = variants(pair_rows)
    boxes = [next(item["bbox"] for item in scene["elements"] if item["id"] == row["panel_id"]) for row in pair_rows]
    if boxes:
        x1 = min(box[0] for box in boxes); y1 = min(box[1] for box in boxes)
        x2 = max(box[0] + box[2] for box in boxes); y2 = max(box[1] + box[3] for box in boxes)
        region = [x1, y1, x2 - x1, y2 - y1]
    else:
        region = [0, 0, scene["canvas"]["width"], scene["canvas"]["height"]]
    result: dict[str, Any] = {
        "owner_stage": "representation", "mechanism": "flat-shadow-proxy-to-native-outer-shadow",
        "selected_candidate_id": None,
        "affected_label_ids": sorted({row["source_label_id"] for row in pair_rows}),
        "affected_primitive_ids": sorted([row["proxy_id"] for row in pair_rows] + [row["panel_id"] for row in pair_rows]),
        "field": "shadow", "evaluation_region": region,
        "evaluation_region_source": "affected_scene_owners" if boxes else "full_canvas_no_candidate",
        "pairs": pair_rows, "variants": candidates, "attempts": [],
        "baseline_scene_sha256": current_hash,
        "baseline_pptx_sha256": sha256(output / "editable.pptx"),
        "accepted": False,
    }
    evidence.mkdir(exist_ok=False)
    if candidates:
        baseline_dir = evidence / "baseline"
        baseline_dir.mkdir()
        for name in ARTIFACTS:
            if (output / name).is_file():
                shutil.copy2(output / name, baseline_dir / name)
    baseline_fallbacks = read_json(output / "report.json")["compiler"].get("fallbacks", [])
    for index, candidate in enumerate(candidates, 1):
        candidate_scene = apply(scene, pair_rows, candidate)
        candidate_dir = evidence / f"candidate-{index:03d}"
        candidate_dir.mkdir()
        write_json(candidate_dir / "scene.json", candidate_scene)
        report = compile_scene(candidate_dir / "scene.json", candidate_dir, config, True)
        build_provenance(candidate_scene, candidate_dir / "editable.pptx", candidate_dir)
        with Image.open(reference_path) as image:
            canvas = image.size
        assessment = compare(reference_path, output / "preview.png", candidate_dir / "preview.png",
                             region)
        before, after = assessment["before_global"], assessment["after_global"]
        candidate_fallbacks = report["compiler"].get("fallbacks", [])
        eligible = (report["status"] == "render_success"
                    and candidate_fallbacks == baseline_fallbacks
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0))
        result["attempts"].append({
            "candidate_id": candidate["candidate_id"], "eligible": eligible,
            "assessment": assessment, "compiler_status": report["status"],
            "compiler_fallbacks": candidate_fallbacks,
            "scene_sha256": sha256(candidate_dir / "scene.json"),
            "pptx_sha256": sha256(candidate_dir / "editable.pptx"),
        })
    eligible = [item for item in result["attempts"] if item["eligible"]]
    if eligible:
        best = max(eligible, key=lambda item: (item["assessment"]["after_global"]["pixel_similarity"],
                                               item["assessment"]["after_global"]["edge_iou"] or 0))
        index = next(i for i, item in enumerate(result["attempts"], 1) if item is best)
        _adopt_candidate(evidence / f"candidate-{index:03d}", output)
        result["accepted"] = True
        result["selected_candidate_id"] = best["candidate_id"]
        final_dir = evidence / "final"
        final_dir.mkdir()
        for name in ARTIFACTS:
            if (output / name).is_file():
                shutil.copy2(output / name, final_dir / name)
    result["status"] = "accepted" if result["accepted"] else "rejected" if candidates else "unchanged"
    result["final_scene_sha256"] = sha256(output / "scene.json")
    result["final_pptx_sha256"] = sha256(output / "editable.pptx")
    write_json(evidence / "decision.json", result)
    return result
