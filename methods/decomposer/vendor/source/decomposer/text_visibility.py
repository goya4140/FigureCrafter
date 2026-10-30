"""Independent structural evidence that source text has visible native ink intent."""
from __future__ import annotations

from typing import Any


def _hidden(element: dict[str, Any]) -> bool:
    runs = element.get("runs")
    if isinstance(runs, list) and runs:
        glyph_runs = [run for run in runs if str(run.get("text") or "").strip()]
        if glyph_runs:
            return all(str((run.get("style") or {}).get("color", element.get("style", {}).get("color"))
                           or "").casefold() == "none" for run in glyph_runs)
    return str((element.get("style") or {}).get("color") or "").casefold() == "none"


def _overlap_ratio(first: list[float], second: list[float]) -> float:
    if len(first) != 4 or len(second) != 4 or first[2] <= 0 or first[3] <= 0:
        return 0.0
    left = max(float(first[0]), float(second[0]))
    top = max(float(first[1]), float(second[1]))
    right = min(float(first[0]) + float(first[2]), float(second[0]) + float(second[2]))
    bottom = min(float(first[1]) + float(first[3]), float(second[1]) + float(second[3]))
    return max(0.0, right - left) * max(0.0, bottom - top) / (float(first[2]) * float(first[3]))


def assess(recognition: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    """Find non-equation Recognition text present only as transparent native text."""
    failures = []
    assessed = 0
    canvas = scene.get("canvas") or {}
    for source in recognition.get("objects", []):
        expected = str(source.get("text") or "")
        if source.get("category") == "equation" or not expected.strip():
            continue
        assessed += 1
        owned = [element for element in scene.get("elements", [])
                 if element.get("source_label_id") == source.get("id")
                 and element.get("kind") == "text" and element.get("editable") is True]
        visible_text = "".join(
            str(element.get("text") or "") + ("\n" if element.get("line_break_after") is True else "")
            for element in owned if not _hidden(element)
        )
        if visible_text == expected:
            continue
        boxes = [element.get("bbox") for element in owned if len(element.get("bbox") or []) == 4]
        if boxes:
            x0 = max(0, min(float(box[0]) for box in boxes))
            y0 = max(0, min(float(box[1]) for box in boxes))
            x1 = min(float(canvas.get("width", float("inf"))),
                     max(float(box[0]) + float(box[2]) for box in boxes))
            y1 = min(float(canvas.get("height", float("inf"))),
                     max(float(box[1]) + float(box[3]) for box in boxes))
            region = [x0, y0, max(0, x1 - x0), max(0, y1 - y0)]
            region_source = "affected_scene_owners"
        else:
            region = source.get("bbox") or [0, 0, 0, 0]
            region_source = "recognition_bbox_no_scene_owner"
        local_images = [element for element in scene.get("elements", [])
                        if element.get("source_label_id") == source.get("id")
                        and element.get("kind") == "image"]
        raster_overlap = max((_overlap_ratio(text.get("bbox") or [], image.get("bbox") or [])
                              for text in owned if _hidden(text) for image in local_images), default=0.0)
        raster_proxy = raster_overlap >= 0.5
        failures.append({
            "owner_stage": "representation",
            "mechanism": ("native-text-transparent-over-local-raster" if raster_proxy
                          else "native-text-transparent-or-missing"),
            "affected_label_ids": [source["id"]],
            "affected_primitive_ids": [element["id"] for element in owned],
            "evaluation_region": region,
            "evaluation_region_source": region_source,
            "expected_source_text": expected,
            "visible_native_source_text": visible_text,
            "hidden_primitive_ids": [element["id"] for element in owned if _hidden(element)],
            "same_owner_local_raster_overlap_ratio": round(raster_overlap, 6),
        })
    return {"assessed": assessed, "failures": failures}
