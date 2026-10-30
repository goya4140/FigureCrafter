from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .common import canonical_bbox, clamp, number, safe_id, unique_id, write_json


def _area(box: list[float]) -> float:
    return max(0.0, box[2]) * max(0.0, box[3])


def _intersection(left: list[float], right: list[float]) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    width = max(0.0, min(lx + lw, rx + rw) - max(lx, rx))
    height = max(0.0, min(ly + lh, ry + rh) - max(ly, ry))
    return width * height


def _iou(left: list[float], right: list[float]) -> float:
    overlap = _intersection(left, right)
    union = _area(left) + _area(right) - overlap
    return overlap / union if union > 0 else 0.0


def _contains(parent: list[float], child: list[float], threshold: float) -> bool:
    child_area = _area(child)
    return child_area > 0 and _intersection(parent, child) / child_area >= threshold


def _source(item: dict[str, Any]) -> str:
    value = item.get("source") or item.get("parser") or item.get("provider") or "unknown"
    return safe_id(value, "unknown").lower()


def _candidate(item: dict[str, Any], index: int, canvas: tuple[int, int], used: set[str]) -> dict[str, Any]:
    source = _source(item)
    preferred = safe_id(item.get("id"), f"{source}-{index:04d}")
    candidate_id = unique_id(preferred, used)
    label = str(item.get("label") or item.get("type") or item.get("category") or "unknown").strip().lower()
    bbox = canonical_bbox(item.get("bbox"), canvas)
    canvas_area = max(1.0, float(canvas[0] * canvas[1]))
    candidate = {
        "id": candidate_id,
        "source": source,
        "label": label or "unknown",
        "bbox": bbox,
        "area_ratio": round(_area(bbox) / canvas_area, 6),
        "confidence": round(clamp(number(item.get("confidence"), item.get("score", 0.5)), 0, 1), 4),
        "text": str(item.get("text") or ""),
        "source_id": str(item.get("id") or ""),
        "source_index": index,
    }
    for field in ("prompt", "mask_path", "parent_region_id", "detection_level", "granularity"):
        if item.get(field) not in (None, ""):
            candidate[field] = item[field]
    return candidate


def build(candidates: dict[str, Any], canvas: tuple[int, int], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a deterministic evidence scaffold without treating detections as semantic truth."""
    policy = (config or {}).get("evidence", {})
    duplicate_iou = clamp(number(policy.get("duplicate_iou"), 0.9), 0, 1)
    containment = clamp(number(policy.get("containment_threshold"), 0.98), 0, 1)
    overlap_iou = clamp(number(policy.get("overlap_iou"), 0.1), 0, 1)
    split_area_ratio = clamp(number(policy.get("split_area_ratio"), 0.35), 0.01, 1)
    whole_canvas_ratio = clamp(number(policy.get("whole_canvas_ratio"), 0.85), split_area_ratio, 1)
    crop_roles = {
        str(value).strip().lower()
        for value in policy.get("crop_candidate_roles", ["picture", "photo", "photograph", "icon", "illustration", "logo", "rendered_object"])
    }
    used: set[str] = set()
    normalized = [
        _candidate(item, index, canvas, used)
        for index, item in enumerate(candidates.get("items", []), 1)
        if isinstance(item, dict)
    ]

    kept: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for item in sorted(normalized, key=lambda value: (-value["confidence"], value["source_index"])):
        duplicate = next((
            other for other in kept
            if (item["source"], item["label"], item["text"]) == (other["source"], other["label"], other["text"])
            and _iou(item["bbox"], other["bbox"]) >= duplicate_iou
        ), None)
        if duplicate:
            suppressed.append({
                "id": item["id"], "kept_id": duplicate["id"],
                "reason": "same_source_label_text_duplicate", "iou": round(_iou(item["bbox"], duplicate["bbox"]), 4),
            })
        else:
            kept.append(item)

    relations: list[dict[str, Any]] = []
    parents: dict[str, list[tuple[float, str]]] = {item["id"]: [] for item in kept}
    for left_index, left in enumerate(kept):
        for right in kept[left_index + 1:]:
            overlap = _intersection(left["bbox"], right["bbox"])
            if overlap <= 0:
                continue
            left_area, right_area = _area(left["bbox"]), _area(right["bbox"])
            if left_area > right_area * 1.01 and _contains(left["bbox"], right["bbox"], containment):
                parents[right["id"]].append((left_area, left["id"]))
                relations.append({"type": "contains", "source_id": left["id"], "target_id": right["id"]})
            elif right_area > left_area * 1.01 and _contains(right["bbox"], left["bbox"], containment):
                parents[left["id"]].append((right_area, right["id"]))
                relations.append({"type": "contains", "source_id": right["id"], "target_id": left["id"]})
            else:
                score = _iou(left["bbox"], right["bbox"])
                if score >= overlap_iou:
                    relations.append({
                        "type": "overlaps", "source_id": left["id"], "target_id": right["id"],
                        "iou": round(score, 4),
                    })

    children: dict[str, list[str]] = {item["id"]: [] for item in kept}
    for item in kept:
        choices = sorted(parents[item["id"]])
        item["parent_id"] = choices[0][1] if choices else None
        if item["parent_id"]:
            children[item["parent_id"]].append(item["id"])
    for item in kept:
        item["child_ids"] = children[item["id"]]

    ocr_items = [item for item in kept if item["source"] in {"ocr", "paddleocr", "pp-ocrv5", "remote_paddleocr"}]
    container_roles = {"panel", "content_box", "content box", "diagram", "chart", "plot", "grid", "border"}
    for item in kept:
        is_sam = item["source"] in {"sam", "sam3"}
        ocr_ids: list[str] = []
        ocr_area = 0.0
        for ocr in ocr_items:
            if ocr["id"] == item["id"]:
                continue
            overlap = _intersection(item["bbox"], ocr["bbox"])
            if overlap <= 0:
                continue
            ocr_ids.append(ocr["id"])
            ocr_area += overlap
            relations.append({"type": "contains_text", "source_id": item["id"], "target_id": ocr["id"]})
        area = max(1.0, _area(item["bbox"]))
        edge_aligned = item["bbox"][0] <= canvas[0] * 0.05 and item["bbox"][1] <= canvas[1] * 0.05
        whole_canvas_risk = item["area_ratio"] >= whole_canvas_ratio or (
            edge_aligned and item["bbox"][2] >= canvas[0] * 0.9 and item["bbox"][3] >= canvas[1] * 0.9
        )
        needs_split = is_sam and item["area_ratio"] > split_area_ratio
        semantic_level = "container" if item["child_ids"] or item["label"] in container_roles or needs_split else "leaf"
        item.update({
            "semantic_level": semantic_level,
            "ocr_region_ids": ocr_ids,
            "ocr_covered_area_ratio": round(min(1.0, ocr_area / area), 6),
            "contains_high_confidence_text": any(
                ocr["id"] in ocr_ids and ocr["confidence"] >= 0.5 for ocr in ocr_items
            ),
            "whole_canvas_risk": whole_canvas_risk,
            "needs_split": needs_split,
            "crop_candidate": bool(
                is_sam and item["label"] in crop_roles and semantic_level == "leaf" and not whole_canvas_risk
            ),
        })

    by_source: dict[str, int] = {}
    by_label: dict[str, int] = {}
    for item in kept:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
        by_label[item["label"]] = by_label.get(item["label"], 0) + 1
    return {
        "schema_version": "decomposer.evidence.v2",
        "canvas": {"width": canvas[0], "height": canvas[1]},
        "candidates": kept,
        "relations": relations,
        "suppressed": suppressed,
        "detector_sources": candidates.get("sources", {}),
        "summary": {
            "raw_count": len(normalized), "candidate_count": len(kept), "suppressed_count": len(suppressed),
            "by_source": by_source, "by_label": by_label,
            "needs_split_count": sum(bool(item["needs_split"]) for item in kept),
            "whole_canvas_risk_count": sum(bool(item["whole_canvas_risk"]) for item in kept),
            "crop_candidate_count": sum(bool(item["crop_candidate"]) for item in kept),
        },
    }


def render_overlay(image_path: str | Path, evidence: dict[str, Any], output_path: str | Path) -> None:
    colors = {"sam": "#E45756", "sam3": "#E45756", "ocr": "#2E86AB", "pp-ocrv5": "#2E86AB"}
    with Image.open(image_path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    line_width = max(1, round(max(image.size) / 700))
    for item in evidence["candidates"]:
        x, y, width, height = item["bbox"]
        color = colors.get(item["source"], "#6A4C93")
        draw.rectangle([x, y, x + width, y + height], outline=color, width=line_width)
        caption = f'{item["id"]} {item["label"]} {item["confidence"]:.2f}'
        text_box = draw.textbbox((x, y), caption)
        caption_width = max(1, text_box[2] - text_box[0])
        caption_height = max(1, text_box[3] - text_box[1])
        top = max(0, y - caption_height - 2)
        draw.rectangle([x, top, min(image.width, x + caption_width + 4), top + caption_height + 2], fill=color)
        draw.text((x + 2, top), caption, fill="white")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def dispositions(evidence: dict[str, Any], recognition: dict[str, Any], proposal: dict[str, Any] | None = None) -> dict[str, Any]:
    used_by: dict[str, list[str]] = {}
    for obj in recognition.get("objects", []):
        for candidate_id in obj.get("candidate_ids", []):
            used_by.setdefault(candidate_id, []).append(obj["id"])
    decisions: dict[str, dict[str, Any]] = {}
    allowed_decisions = {"rejected", "merged", "uncertain"}
    raw_decisions = proposal.get("candidate_decisions", []) if isinstance(proposal, dict) else []
    if isinstance(raw_decisions, list):
        for raw in raw_decisions:
            if not isinstance(raw, dict):
                continue
            candidate_id = str(raw.get("candidate_id") or "")
            status = str(raw.get("status") or "").lower()
            if candidate_id and status in allowed_decisions:
                decisions[candidate_id] = {"status": status, "reason": str(raw.get("reason") or "model_candidate_decision")}
    rows = []
    for item in evidence.get("candidates", []):
        object_ids = used_by.get(item["id"], [])
        decision = decisions.get(item["id"])
        status = "used" if object_ids else decision["status"] if decision else "unclaimed"
        reason = "referenced_by_recognition" if object_ids else decision["reason"] if decision else "model_did_not_reference_candidate"
        rows.append({
            "candidate_id": item["id"],
            "status": status,
            "object_ids": object_ids,
            "reason": reason,
        })
    rows.extend({
        "candidate_id": item["id"], "status": "suppressed_duplicate", "object_ids": [],
        "reason": item["reason"], "kept_candidate_id": item["kept_id"],
    } for item in evidence.get("suppressed", []))
    new_objects = [obj["id"] for obj in recognition.get("objects", []) if not obj.get("candidate_ids")]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "schema_version": "decomposer.candidate-dispositions.v1",
        "rows": rows,
        "new_objects_without_detector_evidence": new_objects,
        "summary": counts,
    }


def materialize(
    image_path: str | Path,
    candidates: dict[str, Any],
    canvas: tuple[int, int],
    config: dict[str, Any],
    directory: str | Path,
) -> dict[str, Any]:
    directory = Path(directory)
    evidence = build(candidates, canvas, config)
    write_json(directory / "evidence-ir.json", evidence)
    render_overlay(image_path, evidence, directory / "evidence-overlay.png")
    return evidence
