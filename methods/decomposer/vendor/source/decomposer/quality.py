from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from .common import write_json


def _similarity(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    mean = sum(ImageStat.Stat(diff).mean) / 3.0
    return max(0.0, 1.0 - mean / 255.0)


def _edge_mask(image: Image.Image) -> Image.Image:
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    return edges.point(lambda value: 255 if value >= 24 else 0, mode="1")


def _iou(a: Image.Image, b: Image.Image) -> float | None:
    a, b = _edge_mask(a), _edge_mask(b)
    intersection = ImageChops.logical_and(a, b).histogram()[255]
    union = ImageChops.logical_or(a, b).histogram()[255]
    return intersection / union if union else None


def metrics(reference: Image.Image, current: Image.Image, region: list[float] | None = None) -> dict[str, Any]:
    if region:
        x, y, w, h = region
        box = (max(0, int(x)), max(0, int(y)), min(reference.width, int(x + w + .999)), min(reference.height, int(y + h + .999)))
        reference, current = reference.crop(box), current.crop(box)
    return {"pixel_similarity": round(_similarity(reference, current), 6), "edge_iou": None if (value := _iou(reference, current)) is None else round(value, 6)}


def boxes_region(boxes: list[list[float]], canvas: dict[str, Any]) -> list[float]:
    if not boxes:
        return [0, 0, canvas["width"], canvas["height"]]
    x1 = min(box[0] for box in boxes); y1 = min(box[1] for box in boxes)
    x2 = max(box[0] + box[2] for box in boxes); y2 = max(box[1] + box[3] for box in boxes)
    return [x1, y1, x2 - x1, y2 - y1]


def affected_scene_region(before_scene: dict[str, Any], after_scene: dict[str, Any],
                          primitive_ids: list[str]) -> list[float]:
    """Cover both old and proposed owner boxes so a moved shape is measured."""
    canvas = before_scene["canvas"]
    if not primitive_ids:
        return boxes_region([], canvas)
    before = {item["id"]: item for item in before_scene["elements"]}
    after = {item["id"]: item for item in after_scene["elements"]}
    boxes = []
    for primitive_id in primitive_ids:
        if primitive_id not in before or primitive_id not in after:
            raise ValueError(f"affected primitive missing from scene: {primitive_id}")
        boxes.extend((before[primitive_id]["bbox"], after[primitive_id]["bbox"]))
    return boxes_region(boxes, canvas)


def discover(reference_path: str | Path, current_path: str | Path, scene: dict[str, Any], output_dir: str | Path, top_k: int = 5) -> dict[str, Any]:
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(reference_path) as ref_image, Image.open(current_path) as cur_image:
        ref = ref_image.convert("RGB")
        cur = cur_image.convert("RGB").resize(ref.size)
        diff = ImageChops.difference(ref, cur).convert("L")
        overlay = ref.copy(); draw = ImageDraw.Draw(overlay)
        candidates = []
        cols, rows = 4, 4
        for row in range(rows):
            for col in range(cols):
                left, top = col * ref.width // cols, row * ref.height // rows
                right, bottom = (col + 1) * ref.width // cols, (row + 1) * ref.height // rows
                score = sum(ImageStat.Stat(diff.crop((left, top, right, bottom))).mean) / 255.0
                region = [left, top, right - left, bottom - top]
                related = []
                for element in scene["elements"]:
                    x, y, w, h = element["bbox"]
                    if min(right, x + w) > max(left, x) and min(bottom, y + h) > max(top, y):
                        related.append(element["source_label_id"])
                candidates.append({"candidate_id": f"tile-{row}-{col}", "region": region, "difference_score": round(score, 6), "label_ids": sorted(set(related))})
        candidates.sort(key=lambda item: item["difference_score"], reverse=True)
        candidates = candidates[:top_k]
        for index, item in enumerate(candidates, 1):
            x, y, w, h = item["region"]
            draw.rectangle((x, y, x + w, y + h), outline=(255, 0, 0), width=3)
            draw.text((x + 4, y + 4), str(index), fill=(255, 0, 0))
        overlay.save(output_dir / "difference-overlay.png")
        report = {"global": metrics(ref, cur), "candidates": candidates, "overlay": str(output_dir / "difference-overlay.png")}
        write_json(output_dir / "discovery.json", report)
        return report


def compare(reference_path: str | Path, before_path: str | Path, after_path: str | Path, region: list[float], tolerance: float = 0.002) -> dict[str, Any]:
    with Image.open(reference_path) as r, Image.open(before_path) as b, Image.open(after_path) as a:
        r, b, a = r.convert("RGB"), b.convert("RGB").resize(r.size), a.convert("RGB").resize(r.size)
        before_local, after_local = metrics(r, b, region), metrics(r, a, region)
        before_global, after_global = metrics(r, b), metrics(r, a)
    local_delta = after_local["pixel_similarity"] - before_local["pixel_similarity"]
    edge_delta = 0.0 if before_local["edge_iou"] is None or after_local["edge_iou"] is None else after_local["edge_iou"] - before_local["edge_iou"]
    global_delta = after_global["pixel_similarity"] - before_global["pixel_similarity"]
    accepted = (local_delta >= 0.001 or edge_delta >= 0.003) and global_delta >= -tolerance
    return {"accepted": accepted, "before_local": before_local, "after_local": after_local, "before_global": before_global, "after_global": after_global, "local_pixel_delta": round(local_delta, 6), "local_edge_delta": round(edge_delta, 6), "global_pixel_delta": round(global_delta, 6)}
