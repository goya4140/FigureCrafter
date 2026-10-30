#!/usr/bin/env python3
"""Compare two complete runs of one source ID at each semantic stage."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, sha256
from decomposer.source_line_registration import _ink_rows


STAGES = (
    ("recognition", "recognition/recognition.json", "objects", "id"),
    ("representation", "representation/representation.json", "components", "label_id"),
    ("layout", "layout/layout.json", "placements", "label_id"),
    ("scene", "output/scene.json", "elements", "id"),
)


def _owner_rows(data: dict, key: str, identity: str, label: str | None) -> dict[str, dict]:
    rows = data.get(key) or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    selected = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if label and label not in {row.get("id"), row.get("label_id"), row.get("source_label_id")}:
            continue
        identifier = row.get(identity)
        if identifier:
            selected[str(identifier)] = row
    return selected


def _text_owner_metrics(run: Path, label: str | None) -> dict[str, dict]:
    """Measure native text owners in source and final render, without modifying a run."""
    recognition_path = run / "recognition/recognition.json"
    scene_path = run / "output/scene.json"
    preview_path = run / "output/preview.png"
    source_path = run / "input/normalized.png"
    if not all(path.is_file() for path in (recognition_path, scene_path, preview_path, source_path)):
        return {}
    with Image.open(source_path) as image:
        source = np.asarray(image.convert("RGB"))
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"))
    if source.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    recognition = read_json(recognition_path)
    scene = read_json(scene_path)
    result = {}
    for obj in recognition.get("objects", []):
        identifier = obj.get("id")
        expected = str(obj.get("text") or "")
        if not identifier or (label and label != identifier) or not expected.strip() or obj.get("category") == "equation":
            continue
        owners = [item for item in scene.get("elements", [])
                  if item.get("kind") == "text" and item.get("source_label_id") == identifier]
        boxes = [item["bbox"] for item in owners if len(item.get("bbox") or []) == 4]
        region = None
        source_dark = rendered_dark = None
        source_lines = rendered_lines = None
        if boxes:
            x0 = max(0, int(min(box[0] for box in boxes)))
            y0 = max(0, int(min(box[1] for box in boxes)))
            x1 = min(source.shape[1], int(max(box[0] + box[2] for box in boxes) + .999))
            y1 = min(source.shape[0], int(max(box[1] + box[3] for box in boxes) + .999))
            if x1 > x0 and y1 > y0:
                region = [x0, y0, x1 - x0, y1 - y0]
                source_dark = int((source[y0:y1, x0:x1].mean(axis=2) < 160).sum())
                rendered_dark = int((preview[y0:y1, x0:x1].mean(axis=2) < 160).sum())
                expected_lines = len(expected.split("\n"))
                if expected_lines > 1:
                    source_lines = _ink_rows(source, region, expected_lines) or None
                    rendered_lines = _ink_rows(preview, region, expected_lines) or None
        result[identifier] = {
            "recognition_text": expected,
            "recognition_bbox": obj.get("bbox"),
            "native_text": "".join(str(item.get("text") or "") for item in owners),
            "native_element_ids": [item.get("id") for item in owners],
            "native_boxes": boxes,
            "native_font_sizes": [item.get("style", {}).get("font_size") for item in owners],
            "native_line_counts": [len(str(item.get("text") or "").split("\n")) for item in owners],
            "native_runs": [[{"text": part.get("text"), "script": part.get("script")}
                             for part in item.get("runs") or []] for item in owners],
            "evaluation_region": region,
            "evaluation_region_source": "affected_scene_owners" if region else "no_scene_text_owner",
            "source_dark_pixels": source_dark,
            "rendered_dark_pixels": rendered_dark,
            "source_line_starts": source_lines,
            "rendered_line_starts": rendered_lines,
        }
    return result


def compare(baseline: Path, candidate: Path, label: str | None,
            candidate_label: str | None = None) -> dict:
    baseline_image = baseline / "input/normalized.png"
    candidate_image = candidate / "input/normalized.png"
    with Image.open(baseline_image) as image:
        baseline_pixels = np.asarray(image.convert("RGBA"))
    with Image.open(candidate_image) as image:
        candidate_pixels = np.asarray(image.convert("RGBA"))
    result = {
        "baseline": str(baseline), "candidate": str(candidate), "label_filter": label,
        "candidate_label_filter": candidate_label if candidate_label is not None else label,
        "same_normalized_input_bytes": sha256(baseline_image) == sha256(candidate_image),
        "same_normalized_input_pixels": bool(np.array_equal(baseline_pixels, candidate_pixels)),
        "stages": [],
    }
    old_text = _text_owner_metrics(baseline, label)
    new_text = _text_owner_metrics(candidate, candidate_label if candidate_label is not None else label)
    result["text_owners"] = {
        "removed_ids": sorted(old_text.keys() - new_text.keys()),
        "added_ids": sorted(new_text.keys() - old_text.keys()),
        "owners": {key: {"baseline": old_text[key], "candidate": new_text[key]}
                   for key in sorted(old_text.keys() & new_text.keys())},
    }
    if label and candidate_label and label in old_text and candidate_label in new_text:
        result["text_owner_pair"] = {
            "baseline_label_id": label, "candidate_label_id": candidate_label,
            "baseline": old_text[label], "candidate": new_text[candidate_label],
        }
    for name, relative, list_key, identity in STAGES:
        old_path, new_path = baseline / relative, candidate / relative
        if not old_path.is_file() or not new_path.is_file():
            result["stages"].append({"stage": name, "complete": False})
            continue
        old = _owner_rows(read_json(old_path), list_key, identity, label)
        new = _owner_rows(read_json(new_path), list_key, identity,
                          candidate_label if candidate_label is not None else label)
        changed = sorted(key for key in old.keys() & new.keys() if old[key] != new[key])
        result["stages"].append({
            "stage": name, "complete": True,
            "baseline_sha256": sha256(old_path), "candidate_sha256": sha256(new_path),
            "identical_file": sha256(old_path) == sha256(new_path),
            "removed_ids": sorted(old.keys() - new.keys()),
            "added_ids": sorted(new.keys() - old.keys()),
            "changed_ids": changed,
            "changed_rows": {key: {"baseline": old[key], "candidate": new[key]} for key in changed},
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--label")
    parser.add_argument("--candidate-label", help="Explicit same-source owner mapping when Recognition changed its ID.")
    args = parser.parse_args()
    print(json.dumps(compare(args.baseline.resolve(), args.candidate.resolve(), args.label,
                             args.candidate_label),
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
