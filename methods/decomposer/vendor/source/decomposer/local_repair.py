"""Execution and measured acceptance for small, label-local repairs.

No model-specific labels, fixture coordinates or raster replacements belong here.
These guards complement source identity and the existing hard validation gates.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .common import finite
from .quality import metrics


class RepairPreflightError(ValueError):
    pass


def expand_text_owner(component: dict, primitive: dict, placement: dict,
                      requested: Any, canvas: dict) -> bool:
    """Rebase a single-text owner without scaling its glyphs or other labels.

    The requested bbox is in the ORIGINAL component coordinates. Expansion is
    deliberately restricted to an unrotated, single-text owner. Composite owners
    need a separately designed group operation; silently clipping is forbidden.
    """
    if not isinstance(requested, (list, tuple)) or len(requested) != 4 or not all(finite(v) for v in requested):
        raise RepairPreflightError("invalid_primitive_bbox: four finite numbers required")
    x, y, w, h = map(float, requested)
    if w <= 0 or h <= 0:
        raise RepairPreflightError("invalid_primitive_bbox: positive dimensions required")
    cw, ch = component["width"], component["height"]
    if x >= 0 and y >= 0 and x + w <= cw and y + h <= ch:
        return False
    if primitive["kind"] != "text" or len(component["primitives"]) != 1 or abs(placement.get("rotation_deg", 0)) > .001:
        raise RepairPreflightError("bbox_expansion_requires_unrotated_single_text_owner")
    left, top = min(0., x), min(0., y)
    right, bottom = max(cw, x + w), max(ch, y + h)
    if (right-left) > 2*cw or (bottom-top) > 2*ch or (right-left)*(bottom-top) > 3*cw*ch:
        raise RepairPreflightError("bbox_expansion_exceeds_local_budget")
    px, py, pw, ph = placement["bbox"]
    sx, sy = pw/cw, ph/ch
    new_place = [px+left*sx, py+top*sy, (right-left)*sx, (bottom-top)*sy]
    if (new_place[0] < 0 or new_place[1] < 0 or
            new_place[0]+new_place[2] > canvas["width"] or new_place[1]+new_place[3] > canvas["height"]):
        raise RepairPreflightError("bbox_expansion_leaves_canvas")
    component["width"], component["height"] = round(right-left, 4), round(bottom-top, 4)
    primitive["bbox"] = [round(x-left, 4), round(y-top, 4), round(w, 4), round(h, 4)]
    placement["bbox"] = [round(v, 4) for v in new_place]
    return True


def preflight(before: dict, after: dict, proposal: dict) -> dict:
    """Enforce rendered-scene locality and detect effective no-ops before export."""
    if before["assets"] != after["assets"] or before["canvas"] != after["canvas"]:
        raise RepairPreflightError("patch_changes_canvas_or_assets")
    old, new = ({e["id"]: e for e in s["elements"]} for s in (before, after))
    if old.keys() != new.keys():
        raise RepairPreflightError("patch_changes_element_identity")
    declared = set(proposal.get("affected_label_ids", []))
    changed = [key for key in old if old[key] != new[key]]
    if any(old[key]["source_label_id"] not in declared for key in changed):
        raise RepairPreflightError("patch_changes_undeclared_scene_owner")
    untouched_order = lambda s: [key for key in s["z_order"] if old[key]["source_label_id"] not in declared]
    if untouched_order(before) != untouched_order(after):
        raise RepairPreflightError("patch_reorders_undeclared_owners")
    if not changed and before["z_order"] == after["z_order"]:
        raise RepairPreflightError("canonical_scene_noop: requested patch has no effective scene change")
    if not changed:
        changed = [key for key in old if old[key]["source_label_id"] in declared]
    # Include both locations, so moving outside a tile never evades measurement.
    boxes = [s[key]["bbox"] for key in changed for s in (old, new)]
    x0, y0 = max(0., min(b[0] for b in boxes)-8), max(0., min(b[1] for b in boxes)-8)
    x1 = min(before["canvas"]["width"], max(b[0]+b[2] for b in boxes)+8)
    y1 = min(before["canvas"]["height"], max(b[1]+b[3] for b in boxes)+8)
    return {"passed": True, "changed_element_ids": changed,
            "unchanged_element_count": len(old)-len(changed), "region": [x0, y0, x1-x0, y1-y0],
            "region_source": "changed_elements_before_after_union"}


def _intersects(a, b):
    return min(a[0]+a[2], b[0]+b[2]) > max(a[0], b[0]) and min(a[1]+a[3], b[1]+b[3]) > max(a[1], b[1])


def _ink_measure(ref: np.ndarray, cur: np.ndarray, color: str) -> dict:
    """A conservative visible-ink measurement; ambiguous backgrounds are skipped."""
    try:
        rgb = np.array([int(color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)])
    except (ValueError, TypeError):
        return {"eligible": False, "reason": "unknown_color"}
    if ref.size == 0 or float(np.median(ref)) < 205 or float(rgb.mean()) > 195:
        return {"eligible": False, "reason": "unsupported_contrast"}
    def mask(a):
        if np.ptp(rgb) < 35 and float(rgb.mean()) < 160:
            return a.mean(axis=2) < 160
        return (((a.astype(float)-rgb)**2).sum(axis=2) < 80**2) & (np.ptp(a, axis=2) > 20)
    source, rendered = mask(ref), mask(cur)
    source_n, rendered_n = int(source.sum()), int(rendered.sum())
    if source_n < 20 or source_n/source.size > .40:
        return {"eligible": False, "reason": "insufficient_or_ambiguous_source_ink"}
    # This is an ink guard, not OCR or a proof of readable text.
    return {"eligible": True, "source_pixels": source_n, "rendered_pixels": rendered_n,
            "source_ratio": rendered_n/source_n}


def local_guard(reference: Path, before_scene: dict, before_path: Path,
                after_scene: dict, after_path: Path, execution: dict) -> dict:
    """Guard changed text and unchanged neighboring owners against regressions."""
    with Image.open(reference) as r, Image.open(before_path) as b, Image.open(after_path) as a:
        images = [im.convert("RGB") for im in (r,b,a)]
    if len({im.size for im in images}) != 1:
        raise ValueError("local guard requires identical image dimensions")
    arrays = [np.asarray(im) for im in images]
    old, new = ({e["id"]: e for e in s["elements"]} for s in (before_scene, after_scene))
    changed = set(execution["changed_element_ids"])
    rows, failures = [], []
    region = execution["region"]
    before_target, after_target = metrics(images[0], images[1], region), metrics(images[0], images[2], region)
    pixel_delta = after_target["pixel_similarity"]-before_target["pixel_similarity"]
    edge_delta = (after_target["edge_iou"] or 0)-(before_target["edge_iou"] or 0)
    if pixel_delta < -.002 or edge_delta < -.02:
        failures.append({"element_id": "changed_region", "reasons": ["target_metric_tradeoff_exceeds_tolerance"],
                         "pixel_delta": pixel_delta, "edge_delta": edge_delta})
    for key, prev in old.items():
        cur = new[key]
        if key not in changed and not _intersects(prev["bbox"], region):
            continue
        boxes = [prev["bbox"], cur["bbox"]]
        x0 = max(0, math.floor(min(b[0] for b in boxes)-2))
        y0 = max(0, math.floor(min(b[1] for b in boxes)-2))
        x1 = min(images[0].width, math.ceil(max(b[0]+b[2] for b in boxes)+2))
        y1 = min(images[0].height, math.ceil(max(b[1]+b[3] for b in boxes)+2))
        box = [x0,y0,x1-x0,y1-y0]
        if x1 <= x0 or y1 <= y0:
            continue
        # No rendered change means this neighbor cannot have regressed.
        if np.array_equal(arrays[1][y0:y1,x0:x1], arrays[2][y0:y1,x0:x1]):
            continue
        bm, am = metrics(images[0], images[1], box), metrics(images[0], images[2], box)
        row = {"element_id": key, "kind": prev["kind"], "changed": key in changed,
               "region": box, "before": bm, "after": am}
        reasons = []
        if key not in changed:
            pd = am["pixel_similarity"]-bm["pixel_similarity"]
            ed = (am["edge_iou"] or 0)-(bm["edge_iou"] or 0)
            if pd < -.01 or ed < -.03:
                reasons.append("neighbor_reference_fidelity_regression")
        if prev["kind"] == "text":
            color = prev.get("style", {}).get("color", "")
            bi = _ink_measure(arrays[0][y0:y1,x0:x1], arrays[1][y0:y1,x0:x1], color)
            ai = _ink_measure(arrays[0][y0:y1,x0:x1], arrays[2][y0:y1,x0:x1], color)
            row.update(before_ink=bi, after_ink=ai)
            if bi.get("eligible") and ai.get("eligible"):
                b_ratio, a_ratio = bi["source_ratio"], ai["source_ratio"]
                before_error = abs(math.log(max(b_ratio, 1e-6)))
                after_error = abs(math.log(max(a_ratio, 1e-6)))
                if ai["rendered_pixels"] < .8*bi["rendered_pixels"] and after_error > before_error+.10:
                    reasons.append("rendered_text_ink_loss_away_from_reference")
        if reasons:
            row["reasons"] = reasons
            failures.append(row)
        rows.append(row)
    return {"passed": not failures, "assessed": rows, "failures": failures,
            "target_fidelity": {"before": before_target, "after": after_target,
                                "pixel_regression_tolerance": .002, "edge_regression_tolerance": .02},
            "limitations": "Ink/fidelity guards do not establish OCR correctness or all text collisions."}
