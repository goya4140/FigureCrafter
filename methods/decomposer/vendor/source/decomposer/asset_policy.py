from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import clamp, number, write_json
from .atomic_objects import text_crop_conflicts


DEFAULT_ROLES = {
    "picture", "photo", "photograph", "icon", "illustration", "logo", "rendered_object"
}


def _area(bbox: Any) -> float:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return 0.0
    return max(0.0, number(bbox[2], 0.0)) * max(0.0, number(bbox[3], 0.0))


def _intersection_area(left: Any, right: Any) -> float:
    if not isinstance(left, list) or len(left) != 4 or not isinstance(right, list) or len(right) != 4:
        return 0.0
    lx, ly, lw, lh = (number(value, 0.0) for value in left)
    rx, ry, rw, rh = (number(value, 0.0) for value in right)
    width = max(0.0, min(lx + lw, rx + rw) - max(lx, rx))
    height = max(0.0, min(ly + lh, ry + rh) - max(ly, ry))
    return width * height


def _ocr_coverage(bbox: Any, candidates: dict[str, dict[str, Any]]) -> float:
    area = _area(bbox)
    if area <= 0:
        return 1.0
    covered = sum(
        _intersection_area(bbox, candidate.get("bbox"))
        for candidate in candidates.values()
        if candidate.get("source") == "ocr" and number(candidate.get("confidence"), 0.0) >= 0.8
    )
    return clamp(covered / area, 0.0, 1.0)


def decide(
    recognition: dict[str, Any],
    evidence: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    policy = config.get("asset_policy", {})
    max_area_ratio = clamp(number(policy.get("max_area_ratio"), 0.35), 0.01, 0.85)
    max_recognition_leaf_area_ratio = clamp(
        number(policy.get("max_recognition_leaf_area_ratio"), max_area_ratio),
        0.01, 0.85,
    )
    max_ocr_coverage = clamp(number(policy.get("max_ocr_coverage"), 0.12), 0.0, 1.0)
    allow_recognition_bbox_fallback = bool(policy.get("allow_recognition_bbox_fallback", True))
    minimum_recognition_confidence = clamp(number(policy.get("minimum_recognition_confidence"), 0.9), 0.0, 1.0)
    minimum_sam_object_coverage = clamp(number(policy.get("minimum_sam_object_coverage"), 0.6), 0.0, 1.0)
    allowed_roles = {
        str(value).strip().lower()
        for value in policy.get("allowed_roles", sorted(DEFAULT_ROLES))
    }
    candidates = {
        str(item.get("id")): item
        for item in evidence.get("candidates", [])
        if isinstance(item, dict) and item.get("id")
    }
    decisions: list[dict[str, Any]] = []
    for obj in recognition.get("objects", []):
        reasons: list[str] = []
        eligible: list[dict[str, Any]] = []
        crop_conflicts = []
        strong_recognition_leaf = (
            obj.get("category") == "image"
            and obj.get("semantic_level") == "leaf"
            and number(obj.get("confidence"), 0.0) >= minimum_recognition_confidence
            and not str(obj.get("text") or "").strip()
            and _area(obj.get("bbox")) > 0
        )
        rejected_partial_sam = False
        if obj.get("category") != "image":
            reasons.append("category_requires_native_representation")
        if obj.get("semantic_level") == "container":
            reasons.append("container_not_crop_eligible")
        for candidate_id in obj.get("candidate_ids", []):
            candidate = candidates.get(str(candidate_id))
            if not candidate or candidate.get("source") not in {"sam", "sam3"}:
                continue
            if candidate.get("label") not in allowed_roles:
                continue
            if not candidate.get("crop_candidate"):
                continue
            if candidate.get("semantic_level") != "leaf" or candidate.get("child_ids"):
                continue
            if candidate.get("whole_canvas_risk") or candidate.get("needs_split"):
                continue
            if number(candidate.get("area_ratio"), 1.0) > max_area_ratio:
                continue
            if number(candidate.get("ocr_covered_area_ratio"), 1.0) > max_ocr_coverage:
                continue
            if strong_recognition_leaf and (
                _intersection_area(candidate.get("bbox"), obj.get("bbox")) / _area(obj.get("bbox"))
                < minimum_sam_object_coverage
            ):
                rejected_partial_sam = True
                continue
            conflicts = text_crop_conflicts(candidate["bbox"], recognition, obj["id"], evidence)
            if conflicts:
                crop_conflicts.extend(conflicts)
                continue
            eligible.append(candidate)
        if rejected_partial_sam and not eligible:
            reasons.append("sam_insufficient_object_coverage")
        source_basis = None
        if obj.get("category") == "image" and obj.get("semantic_level") != "container" and eligible:
            selected = sorted(
                eligible,
                key=lambda value: (-number(value.get("confidence"), 0.0), number(value.get("area_ratio"), 1.0)),
            )[0]
            decision = "localized_raster"
            reasons.extend(["sam_visual_leaf", "below_area_limit", "low_ocr_overlap"])
            source_candidate_id = selected["id"]
            source_basis = "sam_candidate"
            approved_bbox = list(selected["bbox"])
            area_ratio = number(selected.get("area_ratio"), 0.0)
        elif obj.get("category") == "image" and obj.get("semantic_level") == "leaf" and allow_recognition_bbox_fallback:
            bbox = list(obj.get("bbox") or [])
            canvas = recognition.get("canvas", {})
            canvas_area = max(1.0, number(canvas.get("width"), 0.0) * number(canvas.get("height"), 0.0))
            area_ratio = _area(bbox) / canvas_area
            ocr_coverage = _ocr_coverage(bbox, candidates)
            confidence = number(obj.get("confidence"), 0.0)
            conflicts = text_crop_conflicts(bbox, recognition, obj["id"], evidence)
            crop_conflicts.extend(conflicts)
            if (
                len(bbox) == 4
                and area_ratio <= max_recognition_leaf_area_ratio
                and ocr_coverage <= max_ocr_coverage
                and confidence >= minimum_recognition_confidence
                and not str(obj.get("text") or "").strip()
                and not conflicts
            ):
                decision = "localized_raster"
                reasons.extend([
                    "recognition_image_leaf", "recognition_bbox_fallback", "below_area_limit",
                    "low_ocr_overlap", "high_recognition_confidence",
                ])
                source_candidate_id = f"recognition:{obj['id']}"
                source_basis = "recognition_bbox"
                approved_bbox = bbox
            else:
                decision = "native"
                source_candidate_id = None
                approved_bbox = None
                area_ratio = 0.0
                reasons.append("no_eligible_sam_or_safe_recognition_bbox")
        else:
            decision = "native"
            source_candidate_id = None
            approved_bbox = None
            area_ratio = 0.0
            if obj.get("category") == "image" and not eligible:
                reasons.append("no_eligible_sam_leaf")
        if crop_conflicts:
            reasons.append("crop_intersects_separate_native_text")
        decisions.append({
            "label_id": obj["id"],
            "decision": decision,
            "source_candidate_id": source_candidate_id,
            "source_basis": source_basis,
            "approved_bbox": approved_bbox,
            "area_ratio": round(area_ratio, 6),
            "reason_codes": list(dict.fromkeys(reasons)),
            "native_text_conflicts": crop_conflicts,
        })
    return {
        "schema_version": "decomposer.asset-decisions.v1",
        "policy": {
            "allowed_roles": sorted(allowed_roles),
            "max_area_ratio": max_area_ratio,
            "max_recognition_leaf_area_ratio": max_recognition_leaf_area_ratio,
            "max_ocr_coverage": max_ocr_coverage,
            "allow_recognition_bbox_fallback": allow_recognition_bbox_fallback,
            "minimum_recognition_confidence": minimum_recognition_confidence,
            "minimum_sam_object_coverage": minimum_sam_object_coverage,
        },
        "decisions": decisions,
        "summary": {
            "localized_raster_count": sum(item["decision"] == "localized_raster" for item in decisions),
            "native_count": sum(item["decision"] == "native" for item in decisions),
        },
    }


def materialize(
    recognition: dict[str, Any],
    evidence: dict[str, Any],
    config: dict[str, Any],
    path: str | Path,
) -> dict[str, Any]:
    result = decide(recognition, evidence, config)
    write_json(path, result)
    return result


def decide_with_photo_overlays(recognition, evidence, config, image_path):
    """Admit small native labels only on verified white patches in photo leaves.
    Nonwhite/large/unsupported overlaps keep the original rejection.
    """
    from PIL import Image
    from .atomic_objects import is_photographic_leaf
    result=decide(recognition,evidence,config)
    objects={o['id']:o for o in recognition['objects']}
    with Image.open(image_path) as original:
        image=original.convert('RGB')
        for decision in result['decisions']:
            owner=objects[decision['label_id']]
            if owner['category']!='image' or owner.get('semantic_level')!='leaf' or not is_photographic_leaf(owner):continue
            if decision['decision']=='localized_raster':continue
            conflicts=text_crop_conflicts(owner['bbox'],recognition,owner['id'],evidence)
            if not conflicts:continue
            x,y,w,h=owner['bbox'];area=w*h;exclusions=[]
            for target_id in dict.fromkeys(c['owner_id'] for c in conflicts):
                target=objects[target_id];a,b,c,d=target['bbox'];a=int(a)-2;b=int(b)-2;c=int(c)+4;d=int(d)+4
                if a<x or b<y or a+c>x+w or b+d>y+h or c*d/area>.06:break
                supported=any(z['owner_id']==target_id and z.get('evidence_id') for z in conflicts)
                if not supported:break
                border=[image.getpixel((i,j)) for i in range(a,a+c) for j in range(b,b+d) if i in (a,a+c-1) or j in (b,b+d-1)]
                white=sum(min(pixel)>=240 for pixel in border)/len(border)
                if white<.98:break
                exclusions.append({'label_id':target_id,'bbox':[a,b,c,d],'fill':'#FFFFFF','white_border_ratio':white,'source_text':target['text']})
            else:
                if exclusions and sum(e['bbox'][2]*e['bbox'][3] for e in exclusions)/area<=.08 and area/(recognition['canvas']['width']*recognition['canvas']['height'])<=result['policy']['max_recognition_leaf_area_ratio']:
                    decision.update(decision='localized_raster',approved_bbox=owner['bbox'],source_candidate_id='recognition:'+owner['id'],source_basis='recognition_bbox_with_verified_white_native_label_patches',area_ratio=round(area/(recognition['canvas']['width']*recognition['canvas']['height']),6),native_text_exclusions=exclusions,reason_codes=['photographic_leaf','cached_ocr_supported_native_label','verified_white_patch','native_label_removed_from_raster','below_area_limit'])
    from .asset_transforms import augment_decisions
    with Image.open(image_path) as source:
        result=augment_decisions(result,recognition,evidence,source)
    from .photo_label_contract import augment
    result=augment(result,recognition,evidence,image_path)
    return result
