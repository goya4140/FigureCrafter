#!/usr/bin/env python3
"""Deterministically audit terminal samples in a frozen first-100 batch."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image

from decomposer.source_line_registration import apply as apply_source_line_proposal, propose as propose_source_lines
from decomposer.native_shadow_registration import apply as apply_native_shadow, pairs as native_shadow_pairs, variants as native_shadow_variants
from decomposer.short_text_registration import apply as apply_short_text, align as align_short_text, detect as detect_short_text, height_scene as short_text_height_scene
from decomposer.text_registration import propose as propose_text_registration
from decomposer.flat_panel_registration import apply as apply_flat_panel, measure as measure_flat_panels
from decomposer.rect_fill_registration import apply as apply_rect_fill, measure as measure_rect_fills
from decomposer.styled_multiline_registration import apply as apply_styled_multiline, propose as propose_styled_multiline
from decomposer.text_tracking_registration import apply as apply_text_tracking, eligible as eligible_text_tracking, TRACKING_CANDIDATES
from decomposer.joint_shadow_text_registration import apply_shadow as apply_joint_shadow, shadow_owners as measure_joint_shadow_owners, text_proposal as propose_joint_text, SHADOW_OFFSET_DELTAS
from decomposer.native_text_style_probe import (
    dark_ink_candidates, styled_scene, scaled_monospace_scene,
    single_line_monospace_ink_candidates,
)
from decomposer.quality import affected_scene_region
from decomposer.quality import compare as compare_visual
from decomposer.fixture_provenance import verify_replay
from decomposer.rendered_text_ink import assess as assess_rendered_text_ink
from decomposer.text_visibility import assess as assess_text_visibility


REQUIRED_CHECKS = {
    "pptx_package",
    "not_single_screenshot",
    "compiler_fallbacks",
    "script_font_routing",
    "cjk_render_font_environment",
    "raster_safety",
    "provenance_coverage",
    "arrowhead_export_coverage",
    "styled_text_run_export",
    "visible_label_coverage",
    "editable_text_coverage",
    "native_element_editability",
    "asset_provenance",
    "visual_threshold",
}
TERMINAL_SUCCESS = {"render_success", "structural_success"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def refinement_memory_injection(run: Path) -> dict[str, Any]:
    """Audit the actual memory rows supplied to each refinement model call."""
    rounds = []
    for path in sorted((run / "refinement").glob("round-*/call/input.json")):
        rows = read_json(path).get("applicable_memories", [])
        rounds.append({
            "path": str(path.relative_to(run)),
            "count": len(rows),
            "memory_ids": [item.get("memory_id") for item in rows],
            "non_downstreamed_ids": [item.get("memory_id") for item in rows
                                     if item.get("status") != "downstreamed"],
            "superseded_ids": [item.get("memory_id") for item in rows
                               if item.get("status") == "superseded"],
            "within_limit": len(rows) <= 8,
        })
    return {
        "rounds": rounds,
        "all_downstreamed": all(not row["non_downstreamed_ids"] for row in rounds),
        "no_superseded": all(not row["superseded_ids"] for row in rounds),
        "within_limit": all(row["within_limit"] for row in rounds),
    }


def normalized_text(value: Any) -> str:
    return "".join(str(value or "").split()).casefold()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    weight = index - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 6)


def version_tuple(value: Any) -> tuple[int, ...]:
    """Keep prerelease suffixes from disabling the matching version audits."""
    return tuple(int(part) for part in str(value or "0.0.0").split("-", 1)[0].split(".")[:3]
                 if part.isdigit())


def registration_assessment_region(
    version_parts: tuple[int, ...], baseline_scene: dict[str, Any],
    candidate_scene: dict[str, Any], owner_ids: list[str],
    canvas: tuple[int, int],
) -> list[float]:
    """Replay the measurement scope used by each registration version."""
    if version_parts >= (0, 6, 19):
        return affected_scene_region(baseline_scene, candidate_scene, owner_ids)
    return [0, 0, *canvas]


def ooxml_shape_rotations(roots: list[ET.Element], namespaces: dict[str, str]) -> dict[str, float]:
    """Read native DrawingML shape rotations, keyed by the exported shape name."""
    rotations: dict[str, float] = {}
    for root in roots:
        for shape in root.findall(".//p:sp", namespaces):
            identity = shape.find("./p:nvSpPr/p:cNvPr", namespaces)
            transform = shape.find("./p:spPr/a:xfrm", namespaces)
            if identity is None or not identity.get("name"):
                continue
            raw = int(transform.get("rot", "0")) if transform is not None else 0
            rotations[identity.get("name", "")] = round((raw / 60000.0) % 360.0, 6)
    return rotations


def ooxml_shape_endpoints(
    roots: list[ET.Element], namespaces: dict[str, str]
) -> dict[str, dict[str, str | None]]:
    """Read serialized DrawingML line endpoint types, keyed by exported shape name."""
    endpoints: dict[str, dict[str, str | None]] = {}
    for root in roots:
        for shape in root.findall(".//p:sp", namespaces):
            identity = shape.find("./p:nvSpPr/p:cNvPr", namespaces)
            if identity is None or not identity.get("name"):
                continue
            line = shape.find("./p:spPr/a:ln", namespaces)
            head = line.find("./a:headEnd", namespaces) if line is not None else None
            tail = line.find("./a:tailEnd", namespaces) if line is not None else None
            endpoints[identity.get("name", "")] = {
                "head_end": head.get("type") if head is not None else None,
                "tail_end": tail.get("type") if tail is not None else None,
            }
    return endpoints


def ooxml_text_facts(roots: list[ET.Element], namespaces: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Read visible text and native baseline values from each DrawingML text shape."""
    drawing = namespaces["a"]

    def visible_text(shape: ET.Element) -> str:
        body = shape.find("p:txBody", namespaces)
        if body is None:
            return ""
        paragraphs = []
        for paragraph in body.findall("a:p", namespaces):
            parts = []
            for child in paragraph:
                if child.tag == f"{{{drawing}}}br":
                    parts.append("\n")
                elif child.tag in {f"{{{drawing}}}r", f"{{{drawing}}}fld"}:
                    text = child.find("a:t", namespaces)
                    if text is not None:
                        parts.append(text.text or "")
            paragraphs.append("".join(parts))
        return "\n".join(paragraphs)

    facts: dict[str, dict[str, Any]] = {}
    for root in roots:
        for shape in root.findall(".//p:sp", namespaces):
            identity = shape.find("./p:nvSpPr/p:cNvPr", namespaces)
            if identity is None or not identity.get("name"):
                continue
            facts[identity.get("name", "")] = {
                "text": visible_text(shape),
                "baselines": [int(item.get("baseline", "0")) for item in shape.findall(".//a:rPr", namespaces)],
            }
    return facts


def arrowhead_export_mismatches(
    scene: dict[str, Any], endpoints: dict[str, dict[str, str | None]]
) -> list[dict[str, Any]]:
    """Compare scene arrow intent with endpoint types parsed independently from the PPTX."""
    mismatches = []
    for element in scene.get("elements", []):
        if element.get("kind") not in {"line", "polyline", "path"}:
            continue
        expected_start = bool(element.get("arrow_start"))
        expected_end = bool(element.get("arrow_end"))
        if not expected_start and not expected_end:
            continue
        rows = [
            value for name, value in endpoints.items()
            if name == element.get("id") or name.startswith(str(element.get("id")) + ".segment")
        ]
        actual_start = any(item.get("head_end") not in {None, "none"} for item in rows)
        actual_end = any(item.get("tail_end") not in {None, "none"} for item in rows)
        if actual_start != expected_start or actual_end != expected_end:
            mismatches.append({
                "element_id": element.get("id"),
                "expected": {"arrow_start": expected_start, "arrow_end": expected_end},
                "actual": {"arrow_start": actual_start, "arrow_end": actual_end},
                "ooxml_shapes": rows,
            })
    return mismatches


def boxes_intersect(left: list[float], right: list[float]) -> bool:
    if len(left) != 4 or len(right) != 4:
        return False
    lx, ly, lw, lh = map(float, left)
    rx, ry, rw, rh = map(float, right)
    return min(lx + lw, rx + rw) > max(lx, rx) and min(ly + lh, ry + rh) > max(ly, ry)


def audit_run(run: Path) -> dict[str, Any]:
    missing_files = [
        str(path.relative_to(run))
        for path in (
            run / "status.json",
            run / "recognition" / "recognition.json",
            run / "representation" / "representation.json",
            run / "layout" / "layout.json",
            run / "output" / "scene.json",
            run / "output" / "editable.pptx",
            run / "output" / "provenance.json",
            run / "output" / "page-manifest.json",
            run / "output" / "validation.json",
        )
        if not path.is_file()
    ]
    if missing_files:
        return {"sample_id": run.name, "auditable": False, "passed": False, "missing_files": missing_files}

    status = read_json(run / "status.json")
    recognition = read_json(run / "recognition" / "recognition.json")
    representation = read_json(run / "representation" / "representation.json")
    scene = read_json(run / "output" / "scene.json")
    provenance = read_json(run / "output" / "provenance.json")
    validation = read_json(run / "output" / "validation.json")
    report = read_json(run / "output" / "report.json")
    manifest = read_json(run / "output" / "page-manifest.json")

    failures: list[dict[str, Any]] = []

    def require(condition: bool, code: str, detail: Any = None) -> None:
        if not condition:
            failures.append({"code": code, "detail": detail})

    require(status.get("status") in TERMINAL_SUCCESS, "non-success-terminal-status", status.get("status"))
    require(validation.get("passed") is True, "validation-not-passed", validation.get("failures"))
    model_calls = {}
    for stage in ("recognition", "representation", "layout"):
        metadata_path = run / "calls" / stage / "metadata.json"
        metadata = read_json(metadata_path) if metadata_path.is_file() else None
        model_calls[stage] = metadata
        require(metadata is not None and metadata.get("status") in {"complete", "fixture"}
                and metadata.get("fallback_used") is False,
                "model-stage-fallback-or-incomplete", {"stage": stage, "metadata": metadata})
    fixture_stages = {stage for stage, metadata in model_calls.items()
                      if metadata and metadata.get("status") == "fixture"}
    fixture_provenance = None
    if version_tuple(status.get("version")) >= (0, 6, 20) and fixture_stages:
        if fixture_stages != {"representation", "layout"}:
            require(False, "fixture-provenance-unsupported-stage-set", sorted(fixture_stages))
        else:
            try:
                fixture_provenance = verify_replay(run)
            except (OSError, KeyError, TypeError, ValueError) as exc:
                require(False, "fixture-provenance-invalid", str(exc))
    checks = validation.get("checks", {})
    required_checks = REQUIRED_CHECKS | ({"rendered_native_text_ink"} if version_tuple(status.get("version")) >= (0, 6, 21) else set())
    require(required_checks.issubset(checks), "missing-validation-checks", sorted(required_checks - set(checks)))
    failed_checks = sorted(name for name, item in checks.items() if item.get("passed") is not True)
    require(not failed_checks, "failed-validation-checks", failed_checks)
    rendered_text_ink = None
    if version_tuple(status.get("version")) >= (0, 6, 21):
        preview = run / "output" / "preview.png"
        reference = run / "input" / "normalized.png"
        if preview.is_file() and reference.is_file():
            rendered_text_ink = assess_rendered_text_ink(scene, reference, preview)
            require(not rendered_text_ink["failures"], "rendered-native-text-ink-collapse", rendered_text_ink["failures"])
        else:
            require(False, "rendered-native-text-ink-evidence-missing", {"preview": str(preview), "reference": str(reference)})
    require(not list(run.rglob("*.svg")), "svg-intermediate-present")
    pptx = run / manifest.get("build", {}).get("pptx", "output/editable.pptx")
    require(pptx.is_file(), "manifest-pptx-missing", str(pptx))
    if pptx.is_file():
        require(sha256(pptx) == manifest.get("build", {}).get("pptx_sha256"), "manifest-pptx-hash-mismatch")
    hash_mismatches = []
    for name, item in manifest.get("canonical", {}).items():
        path = run / item.get("path", "")
        if not path.is_file() or sha256(path) != item.get("sha256"):
            hash_mismatches.append(name)
    source_path = Path(manifest.get("source", {}).get("path", ""))
    if not source_path.is_file() or sha256(source_path) != manifest.get("source", {}).get("sha256"):
        hash_mismatches.append("source")
    for item in manifest.get("asset_provenance", []):
        path = Path(item.get("path", ""))
        if not path.is_file() or sha256(path) != item.get("sha256"):
            hash_mismatches.append(f"asset:{item.get('asset_id')}")
    require(not hash_mismatches, "manifest-hash-mismatch", hash_mismatches)

    package_facts = {"zip_valid": False, "xml_valid": False, "slide_count": 0, "shape_count": 0, "picture_count": 0, "text_run_count": 0}
    shape_rotations: dict[str, float] = {}
    shape_endpoints: dict[str, dict[str, str | None]] = {}
    text_facts: dict[str, dict[str, Any]] = {}
    if pptx.is_file():
        try:
            with zipfile.ZipFile(pptx) as archive:
                package_facts["zip_valid"] = archive.testzip() is None
                slides = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
                package_facts["slide_count"] = len(slides)
                roots = [ET.fromstring(archive.read(name)) for name in slides]
                package_facts["xml_valid"] = all(
                    ET.fromstring(archive.read(name)) is not None
                    for name in archive.namelist() if name.endswith((".xml", ".rels"))
                )
                namespaces = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main", "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
                package_facts["shape_count"] = sum(len(root.findall(".//p:sp", namespaces)) for root in roots)
                package_facts["picture_count"] = sum(len(root.findall(".//p:pic", namespaces)) for root in roots)
                package_facts["text_run_count"] = sum(len(root.findall(".//a:t", namespaces)) for root in roots)
                shape_rotations = ooxml_shape_rotations(roots, namespaces)
                shape_endpoints = ooxml_shape_endpoints(roots, namespaces)
                text_facts = ooxml_text_facts(roots, namespaces)
        except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
            package_facts["error"] = f"{type(exc).__name__}: {exc}"
    require(package_facts["zip_valid"] and package_facts["xml_valid"] and package_facts["slide_count"] == 1, "pptx-package-invalid", package_facts)
    single_screenshot = package_facts["picture_count"] == 1 and package_facts["shape_count"] == 0 and package_facts["text_run_count"] == 0
    require(not single_screenshot, "single-screenshot-output", package_facts)

    scene_ids = {item["id"] for item in scene.get("elements", [])}
    traced_ids = {
        item["id"]
        for label in provenance.get("labels", {}).values()
        for item in label.get("scene_elements", [])
    }
    require(scene_ids == traced_ids, "provenance-scene-id-mismatch", sorted(scene_ids ^ traced_ids))

    expected_text = {
        item["id"]: str(item.get("text") or "")
        for item in recognition.get("objects", [])
        if item.get("category") != "equation" and normalized_text(item.get("text"))
    }
    actual_text = {
        label_id: "".join(
            str(item.get("text", "")) + ("\n" if item.get("line_break_after") is True else "")
            for item in scene.get("elements", [])
            if item.get("source_label_id") == label_id and item.get("kind") == "text" and item.get("editable") is True
        )
        for label_id in expected_text
    }
    text_mismatches = {
        label_id: {"expected": text, "actual": actual_text.get(label_id, "")}
        for label_id, text in expected_text.items()
        if actual_text.get(label_id) != text
    }
    require(not text_mismatches, "editable-text-mismatch", text_mismatches)

    native_text_visibility = assess_text_visibility(recognition, scene)
    require(not native_text_visibility["failures"], "native-text-invisible",
            native_text_visibility["failures"])

    source_primitives = {
        f"{component['label_id']}/{primitive['id']}": (component['label_id'], primitive)
        for component in representation.get("components", [])
        for primitive in component.get("primitives", [])
    }
    split_groups = {}
    for item in scene.get("elements", []):
        parent = item.get("source_primitive_id")
        if parent:
            split_groups.setdefault(parent, []).append(item)
    split_failures = []
    for parent, lines in split_groups.items():
        original = source_primitives.get(parent)
        count = len(lines)
        indices = [item.get("source_line_index") for item in lines]
        expected = original[1].get("text") if original else None
        actual = "".join(item.get("text", "") + ("\n" if item.get("line_break_after") is True else "") for item in lines)
        if (
            not original or original[1].get("kind") != "text"
            or any(item.get("source_label_id") != original[0] or item.get("kind") != "text" or item.get("editable") is not True for item in lines)
            or indices != list(range(count))
            or any(item.get("source_line_count") != count or item.get("line_break_after") is not (index < count - 1) for index, item in enumerate(lines))
            or any("\n" in str(item.get("text", "")) for item in lines)
            or any(lines[index]["bbox"][1] >= lines[index + 1]["bbox"][1] for index in range(count - 1))
            or expected != actual
        ):
            split_failures.append({"source_primitive_id": parent, "indices": indices, "expected": expected, "actual": actual})
    require(not split_failures, "split-text-source-identity-mismatch", split_failures)

    native_not_editable = [
        item["id"] for item in scene.get("elements", [])
        if item.get("kind") != "image" and item.get("editable") is not True
    ]
    require(not native_not_editable, "native-element-not-editable", native_not_editable)

    directional_text = [
        item for item in scene.get("elements", [])
        if item.get("kind") == "text" and round(float(item.get("rotation_deg") or 0.0) % 360.0, 6) in {90.0, 270.0}
    ]
    directional_rotation_mismatches = [
        {
            "element_id": item["id"],
            "expected_rotation_deg": round(float(item.get("rotation_deg") or 0.0) % 360.0, 6),
            "ooxml_rotation_deg": shape_rotations.get(item["id"]),
        }
        for item in directional_text
        if shape_rotations.get(item["id"]) != round(float(item.get("rotation_deg") or 0.0) % 360.0, 6)
    ]
    require(not directional_rotation_mismatches, "directional-text-rotation-mismatch", directional_rotation_mismatches)

    arrow_elements = [
        item for item in scene.get("elements", [])
        if item.get("kind") in {"line", "polyline", "path"}
        and (item.get("arrow_start") or item.get("arrow_end"))
    ]
    arrowhead_mismatches = arrowhead_export_mismatches(scene, shape_endpoints)
    require(not arrowhead_mismatches, "arrowhead-ooxml-mismatch", arrowhead_mismatches)

    styled_text_elements = [
        item for item in scene.get("elements", [])
        if item.get("kind") == "text" and isinstance(item.get("runs"), list) and item.get("runs")
    ]
    styled_text_mismatches = []
    for element in styled_text_elements:
        runs = element["runs"]
        source = "".join(str(item.get("source_text", item.get("text", ""))) for item in runs)
        visible = "".join(str(item.get("text", "")) for item in runs)
        facts = text_facts.get(element["id"], {})
        baselines = facts.get("baselines", [])
        expected_subscripts = sum(item.get("script") == "subscript" and bool(item.get("text")) for item in runs)
        expected_superscripts = sum(item.get("script") == "superscript" and bool(item.get("text")) for item in runs)
        if (
            source != str(element.get("text", ""))
            or facts.get("text") != visible
            or sum(value < 0 for value in baselines) < expected_subscripts
            or sum(value > 0 for value in baselines) < expected_superscripts
        ):
            styled_text_mismatches.append({
                "element_id": element["id"], "source_text": source,
                "expected_source_text": element.get("text"), "visible_text": visible,
                "ooxml_text": facts.get("text"), "baselines": baselines,
            })
    require(not styled_text_mismatches, "styled-text-run-ooxml-mismatch", styled_text_mismatches)

    assets = representation.get("assets", {})
    bad_assets = [
        asset_id for asset_id, item in assets.items()
        if item.get("source_basis") not in {"sam_candidate", "recognition_bbox"}
        or not item.get("source_candidate_id")
        or item.get("mode") != "source_crop"
    ]
    require(not bad_assets, "asset-provenance-invalid", bad_assets)

    cjk_elements = [
        item["id"] for item in scene.get("elements", [])
        if item.get("kind") == "text" and any(
            "\u3400" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff" or "\uac00" <= char <= "\ud7af"
            for char in str(item.get("text") or "")
        )
    ]
    if cjk_elements:
        font_environment = report.get("render", {}).get("font_environment", {})
        require(
            font_environment.get("mode") == "bundled-fontconfig" and font_environment.get("configured") is True,
            "cjk-render-font-environment-missing",
            {"elements": cjk_elements, "font_environment": font_environment},
        )

    refinement = status.get("refinement") or {}
    decisions = []
    for path in sorted((run / "refinement").glob("round-*/decision.json")):
        decision = read_json(path)
        if not decision or decision.get("acceptable") is True:
            continue
        decisions.append({"path": str(path.relative_to(run)), "decision": decision})
    malformed_decisions = [
        item for item in decisions
        if not {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "evaluation_region", "evaluation_region_source"}.issubset(item["decision"])
        or not isinstance(item["decision"].get("affected_label_ids"), list)
        or not item["decision"].get("affected_label_ids")
        or not isinstance(item["decision"].get("affected_primitive_ids"), list)
        or not isinstance(item["decision"].get("evaluation_region"), list)
    ]
    require(not malformed_decisions, "refinement-decision-missing-localization", malformed_decisions)

    region_alignment_failures = []
    scene_by_label: dict[str, list[dict[str, Any]]] = {}
    for element in scene.get("elements", []):
        scene_by_label.setdefault(str(element.get("source_label_id") or ""), []).append(element)
    for item in decisions:
        if item in malformed_decisions:
            continue
        decision = item["decision"]
        round_root = run / Path(item["path"]).parent
        discovery_path = round_root / "discover" / "discovery.json"
        discovery = read_json(discovery_path) if discovery_path.is_file() else {"candidates": []}
        candidates = discovery.get("candidates", [])
        selected = next(
            (candidate for candidate in candidates if candidate.get("candidate_id") == decision.get("selected_candidate_id")),
            None,
        )
        affected = {str(value) for value in decision.get("affected_label_ids", []) if value}
        selected_overlap = bool(selected and affected.intersection(map(str, selected.get("label_ids", []))))
        source = decision.get("evaluation_region_source")
        region = decision.get("evaluation_region", [])
        if source == "selected_difference_tile":
            valid = selected_overlap and list(selected.get("region", [])) == list(region)
        elif source == "affected_scene_owners":
            owner_boxes = [
                element.get("bbox", [])
                for label_id in affected for element in scene_by_label.get(label_id, [])
            ]
            valid = not selected_overlap and bool(owner_boxes) and any(boxes_intersect(region, box) for box in owner_boxes)
        else:
            valid = False
        if not valid:
            region_alignment_failures.append({
                "path": item["path"],
                "evaluation_region_source": source,
                "selected_candidate_id": decision.get("selected_candidate_id"),
                "selected_overlaps_affected_labels": selected_overlap,
                "affected_label_ids": sorted(affected),
                "evaluation_region": region,
            })
    require(not region_alignment_failures, "refinement-evaluation-region-not-owner-bound", region_alignment_failures)

    registration = status.get("text_registration")
    registration_pass_2 = status.get("text_registration_pass_2")
    previous_final_hash = None
    previous_final_pptx_hash = None
    for evidence_name, decision_status, pass_number in (
        ("text-registration", registration, 1),
        ("text-registration-pass-2", registration_pass_2, 2),
    ):
        if not decision_status:
            version_parts = version_tuple(status.get("version"))
            if pass_number == 2 and version_parts >= (0, 6, 7):
                require(False, "text-registration-pass-2-missing")
            continue
        evidence_dir = run / evidence_name
        decision_path = evidence_dir / "decision.json"
        require(decision_path.is_file(), f"{evidence_name}-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == decision_status.get("status")
            and decision.get("accepted") == decision_status.get("accepted")
            and decision.get("pass_number", 1) == pass_number,
            f"{evidence_name}-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            f"{evidence_name}-localization-missing",
        )
        if previous_final_hash is not None:
            require(decision.get("baseline_scene_sha256") == previous_final_hash, "text-registration-pass-chain-mismatch")
        baseline_scene_path = evidence_dir / "baseline" / "scene.json"
        candidate_scene_path = evidence_dir / "candidate" / "scene.json"
        if decision.get("accepted"):
            require(baseline_scene_path.is_file() and candidate_scene_path.is_file(), f"{evidence_name}-evidence-missing")
            if baseline_scene_path.is_file() and candidate_scene_path.is_file():
                require(
                    decision.get("baseline_scene_sha256") == sha256(baseline_scene_path)
                    and decision.get("candidate_scene_sha256") == sha256(candidate_scene_path)
                    and decision.get("final_scene_sha256") == sha256(candidate_scene_path)
                    and decision.get("final_pptx_sha256") == sha256(evidence_dir / "candidate" / "editable.pptx"),
                    f"{evidence_name}-evidence-hash-mismatch",
                )
            if baseline_scene_path.is_file() and candidate_scene_path.is_file():
                baseline_scene = read_json(baseline_scene_path)
                candidate_scene = read_json(candidate_scene_path)
                changed, unsafe = set(), []
                before_elements = baseline_scene.get("elements", [])
                after_elements = candidate_scene.get("elements", [])
                if len(before_elements) != len(after_elements):
                    unsafe.append("element_count_changed")
                for old, new in zip(before_elements, after_elements):
                    if old.get("id") != new.get("id"):
                        unsafe.append("element_order_changed")
                        continue
                    old_box, new_box = old.get("bbox", []), new.get("bbox", [])
                    if old_box != new_box:
                        changed.add(old["id"])
                    old_copy, new_copy = dict(old), dict(new)
                    new_copy["bbox"] = old_box
                    if old_copy != new_copy or (
                        old_box != new_box and (
                            old.get("kind") != "text" or len(old_box) != 4 or len(new_box) != 4
                            or old_box[2:] != new_box[2:]
                            or abs(old_box[0] - new_box[0]) > 3
                            or abs(old_box[1] - new_box[1]) > 3
                        )
                    ):
                        unsafe.append(old["id"])
                if (baseline_scene.get("assets") != candidate_scene.get("assets") or baseline_scene.get("z_order") != candidate_scene.get("z_order")
                    or baseline_scene.get("canvas") != candidate_scene.get("canvas")):
                    unsafe.append("scene_structure_changed")
                require(not unsafe, f"{evidence_name}-unsafe-scene-change", unsafe)
                require(changed == set(decision.get("affected_primitive_ids", [])), f"{evidence_name}-affected-ids-mismatch", sorted(changed))
            assessment = decision.get("assessment") or {}
            before, after = assessment.get("before_global") or {}, assessment.get("after_global") or {}
            require(
                after.get("pixel_similarity", -1) > before.get("pixel_similarity", -1)
                and (after.get("edge_iou") or 0) >= (before.get("edge_iou") or 0),
                f"{evidence_name}-no-measured-net-gain", assessment,
            )
        previous_final_hash = decision.get("final_scene_sha256") if decision.get("accepted") else decision.get("baseline_scene_sha256")
        if decision.get("accepted"):
            previous_final_pptx_hash = decision.get("final_pptx_sha256")
    source_line_registration = status.get("source_line_registration")
    version_parts = version_tuple(status.get("version"))
    if version_parts >= (0, 6, 9) and not source_line_registration:
        require(False, "source-line-registration-missing")
    if source_line_registration:
        evidence = run / "source-line-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "source-line-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == source_line_registration.get("status")
            and decision.get("accepted") == source_line_registration.get("accepted"),
            "source-line-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "source-line-registration-localization-missing",
        )
        require(decision.get("baseline_scene_sha256") == previous_final_hash, "source-line-registration-pass-chain-mismatch")
        attempts = decision.get("attempts", [])
        first_baseline = evidence / "attempt-001" / "baseline"
        proposal_scene = first_baseline if attempts else run / "output"
        if (proposal_scene / "scene.json").is_file() and (proposal_scene / "preview.png").is_file():
            computed = propose_source_lines(
                read_json(proposal_scene / "scene.json"),
                run / "input" / "normalized.png", proposal_scene / "preview.png",
            )
            require(
                computed == [item.get("proposal") for item in attempts],
                "source-line-registration-proposal-mismatch",
            )
        running_hash = decision.get("baseline_scene_sha256")
        running_pptx_hash = decision.get("baseline_pptx_sha256")
        for index, attempt in enumerate(attempts, 1):
            attempt_dir = evidence / f"attempt-{index:03d}"
            baseline_dir = attempt_dir / "baseline"
            candidate_dir = attempt_dir / "candidate"
            required = [baseline_dir / "scene.json", baseline_dir / "editable.pptx",
                        candidate_dir / "scene.json", candidate_dir / "editable.pptx"]
            if not all(path.is_file() for path in required):
                require(False, "source-line-registration-attempt-evidence-missing", index)
                continue
            require(
                attempt.get("baseline_scene_sha256") == running_hash == sha256(baseline_dir / "scene.json")
                and attempt.get("baseline_pptx_sha256") == running_pptx_hash == sha256(baseline_dir / "editable.pptx")
                and attempt.get("candidate_scene_sha256") == sha256(candidate_dir / "scene.json")
                and attempt.get("candidate_pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                "source-line-registration-attempt-hash-mismatch", index,
            )
            try:
                expected_candidate = apply_source_line_proposal(read_json(baseline_dir / "scene.json"), attempt["proposal"])
                require(expected_candidate == read_json(candidate_dir / "scene.json"),
                        "source-line-registration-unsafe-scene-change", index)
            except (KeyError, ValueError) as exc:
                require(False, "source-line-registration-invalid-proposal", {"index": index, "error": str(exc)})
            assessment = attempt.get("assessment") or {}
            before, after = assessment.get("before_global") or {}, assessment.get("after_global") or {}
            expected_accept = (
                attempt.get("compiler_status") == "render_success"
                and attempt.get("candidate_fallbacks") == attempt.get("baseline_fallbacks")
                and after.get("pixel_similarity", -1) > before.get("pixel_similarity", -1)
                and (after.get("edge_iou") or 0) >= (before.get("edge_iou") or 0)
            )
            require(attempt.get("accepted") is expected_accept,
                    "source-line-registration-acceptance-mismatch", index)
            if attempt.get("accepted"):
                running_hash = attempt.get("candidate_scene_sha256")
                running_pptx_hash = attempt.get("candidate_pptx_sha256")
        require(decision.get("accepted") is any(item.get("accepted") for item in attempts),
                "source-line-registration-aggregate-acceptance-mismatch")
        require(
            decision.get("final_scene_sha256") == running_hash
            and decision.get("final_pptx_sha256") == running_pptx_hash,
            "source-line-registration-final-chain-mismatch",
        )
        previous_final_hash = running_hash
        previous_final_pptx_hash = running_pptx_hash
    native_shadow_registration = status.get("native_shadow_registration")
    if version_parts >= (0, 6, 10) and not native_shadow_registration:
        require(False, "native-shadow-registration-missing")
    if native_shadow_registration:
        evidence = run / "native-shadow-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "native-shadow-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == native_shadow_registration.get("status")
            and decision.get("accepted") == native_shadow_registration.get("accepted"),
            "native-shadow-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "native-shadow-registration-localization-missing",
        )
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "native-shadow-registration-pass-chain-mismatch",
        )
        attempts = decision.get("attempts", [])
        baseline_dir = evidence / "baseline" if attempts else run / "output"
        if (baseline_dir / "scene.json").is_file():
            baseline_scene = read_json(baseline_dir / "scene.json")
            computed_pairs = native_shadow_pairs(baseline_scene)
            computed_variants = native_shadow_variants(computed_pairs)
            require(
                decision.get("pairs") == computed_pairs
                and decision.get("variants") == computed_variants
                and [item.get("candidate_id") for item in attempts] == [item["candidate_id"] for item in computed_variants],
                "native-shadow-registration-proposal-mismatch",
            )
        else:
            baseline_scene, computed_pairs, computed_variants = {}, [], []
            require(False, "native-shadow-registration-baseline-missing")
        if attempts:
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_dir / "scene.json")
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "native-shadow-registration-baseline-hash-mismatch",
            )
        baseline_fallbacks = read_json(baseline_dir / "report.json")["compiler"].get("fallbacks", []) if (baseline_dir / "report.json").is_file() else []
        eligible_attempts = []
        for index, attempt in enumerate(attempts, 1):
            candidate_dir = evidence / f"candidate-{index:03d}"
            required = [candidate_dir / "scene.json", candidate_dir / "editable.pptx", candidate_dir / "report.json"]
            if not all(path.is_file() for path in required) or index > len(computed_variants):
                require(False, "native-shadow-registration-candidate-evidence-missing", index)
                continue
            require(
                attempt.get("scene_sha256") == sha256(candidate_dir / "scene.json")
                and attempt.get("pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                "native-shadow-registration-candidate-hash-mismatch", index,
            )
            expected_scene = apply_native_shadow(baseline_scene, computed_pairs, computed_variants[index - 1])
            require(expected_scene == read_json(candidate_dir / "scene.json"),
                    "native-shadow-registration-unsafe-scene-change", index)
            candidate_report = read_json(candidate_dir / "report.json")
            assessment = attempt.get("assessment") or {}
            before, after = assessment.get("before_global") or {}, assessment.get("after_global") or {}
            expected_eligible = (
                attempt.get("compiler_status") == candidate_report.get("status") == "render_success"
                and attempt.get("compiler_fallbacks") == candidate_report.get("compiler", {}).get("fallbacks", []) == baseline_fallbacks
                and after.get("pixel_similarity", -1) > before.get("pixel_similarity", -1)
                and (after.get("edge_iou") or 0) >= (before.get("edge_iou") or 0)
            )
            require(attempt.get("eligible") is expected_eligible,
                    "native-shadow-registration-eligibility-mismatch", index)
            if expected_eligible:
                eligible_attempts.append(attempt)
        best = max(eligible_attempts, key=lambda item: (
            item["assessment"]["after_global"]["pixel_similarity"],
            item["assessment"]["after_global"]["edge_iou"] or 0,
        )) if eligible_attempts else None
        require(
            decision.get("accepted") is (best is not None)
            and decision.get("selected_candidate_id") == (best["candidate_id"] if best else None),
            "native-shadow-registration-selection-mismatch",
        )
        expected_scene_hash = best["scene_sha256"] if best else decision.get("baseline_scene_sha256")
        expected_pptx_hash = best["pptx_sha256"] if best else decision.get("baseline_pptx_sha256")
        require(
            decision.get("final_scene_sha256") == expected_scene_hash
            and decision.get("final_pptx_sha256") == expected_pptx_hash,
            "native-shadow-registration-final-chain-mismatch",
        )
        previous_final_hash = expected_scene_hash
        previous_final_pptx_hash = expected_pptx_hash
    short_text_registration = status.get("short_text_registration")
    if version_parts >= (0, 6, 11) and not short_text_registration:
        require(False, "short-text-registration-missing")
    if short_text_registration:
        evidence = run / "short-text-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "short-text-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == short_text_registration.get("status")
            and decision.get("accepted") == short_text_registration.get("accepted"),
            "short-text-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "short-text-registration-localization-missing",
        )
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "short-text-registration-pass-chain-mismatch",
        )
        detected = decision.get("detected", [])
        baseline_dir = evidence / "baseline" if detected else run / "output"
        if (baseline_dir / "scene.json").is_file() and (baseline_dir / "preview.png").is_file():
            baseline_scene = read_json(baseline_dir / "scene.json")
            computed_detected = detect_short_text(
                baseline_scene, run / "input" / "normalized.png", baseline_dir / "preview.png")
            require(detected == computed_detected, "short-text-registration-detection-mismatch")
        else:
            baseline_scene, computed_detected = {}, []
            require(False, "short-text-registration-baseline-missing")
        height_dir = evidence / "height-probe"
        if detected:
            required = [height_dir / "scene.json", height_dir / "editable.pptx", height_dir / "preview.png"]
            if all(path.is_file() for path in required):
                expected_height = short_text_height_scene(baseline_scene, computed_detected)
                require(
                    expected_height == read_json(height_dir / "scene.json")
                    and decision.get("height_probe_scene_sha256") == sha256(height_dir / "scene.json")
                    and decision.get("height_probe_pptx_sha256") == sha256(height_dir / "editable.pptx"),
                    "short-text-registration-height-evidence-mismatch",
                )
                computed_aligned = align_short_text(
                    expected_height, computed_detected,
                    run / "input" / "normalized.png", height_dir / "preview.png")
                require(decision.get("aligned") == computed_aligned,
                        "short-text-registration-alignment-mismatch")
            else:
                computed_aligned = []
                require(False, "short-text-registration-height-evidence-missing")
        else:
            computed_aligned = []
            require(decision.get("aligned") == [], "short-text-registration-unexpected-alignment")
        attempts = decision.get("attempts", [])
        require([item.get("proposal") for item in attempts] == computed_aligned,
                "short-text-registration-attempt-proposal-mismatch")
        running_hash = decision.get("baseline_scene_sha256")
        running_pptx_hash = decision.get("baseline_pptx_sha256")
        for index, attempt in enumerate(attempts, 1):
            attempt_dir = evidence / f"attempt-{index:03d}"
            before_dir, after_dir = attempt_dir / "baseline", attempt_dir / "candidate"
            required = [before_dir / "scene.json", before_dir / "editable.pptx",
                        after_dir / "scene.json", after_dir / "editable.pptx", after_dir / "report.json"]
            if not all(path.is_file() for path in required):
                require(False, "short-text-registration-attempt-evidence-missing", index)
                continue
            require(
                attempt.get("baseline_scene_sha256") == running_hash == sha256(before_dir / "scene.json")
                and attempt.get("baseline_pptx_sha256") == running_pptx_hash == sha256(before_dir / "editable.pptx")
                and attempt.get("candidate_scene_sha256") == sha256(after_dir / "scene.json")
                and attempt.get("candidate_pptx_sha256") == sha256(after_dir / "editable.pptx"),
                "short-text-registration-attempt-hash-mismatch", index,
            )
            try:
                expected_candidate = apply_short_text(read_json(before_dir / "scene.json"), attempt["proposal"])
                require(expected_candidate == read_json(after_dir / "scene.json"),
                        "short-text-registration-unsafe-scene-change", index)
            except (KeyError, ValueError) as exc:
                require(False, "short-text-registration-invalid-proposal", {"index": index, "error": str(exc)})
            candidate_report = read_json(after_dir / "report.json")
            assessment = attempt.get("assessment") or {}
            before, after = assessment.get("before_global") or {}, assessment.get("after_global") or {}
            expected_accept = (
                attempt.get("compiler_status") == candidate_report.get("status") == "render_success"
                and attempt.get("candidate_fallbacks") == candidate_report.get("compiler", {}).get("fallbacks", []) == attempt.get("baseline_fallbacks")
                and after.get("pixel_similarity", -1) > before.get("pixel_similarity", -1)
                and (after.get("edge_iou") or 0) >= (before.get("edge_iou") or 0)
            )
            require(attempt.get("accepted") is expected_accept,
                    "short-text-registration-acceptance-mismatch", index)
            if attempt.get("accepted"):
                running_hash = attempt.get("candidate_scene_sha256")
                running_pptx_hash = attempt.get("candidate_pptx_sha256")
        require(decision.get("accepted") is any(item.get("accepted") for item in attempts),
                "short-text-registration-aggregate-acceptance-mismatch")
        require(
            decision.get("final_scene_sha256") == running_hash
            and decision.get("final_pptx_sha256") == running_pptx_hash,
            "short-text-registration-final-chain-mismatch",
        )
        previous_final_hash = running_hash
        previous_final_pptx_hash = running_pptx_hash
    post_short_text_registration = status.get("post_short_text_registration")
    if version_parts >= (0, 6, 12) and not post_short_text_registration:
        require(False, "post-short-text-registration-missing")
    if post_short_text_registration:
        evidence = run / "post-short-text-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "post-short-text-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == post_short_text_registration.get("status")
            and decision.get("accepted") == post_short_text_registration.get("accepted"),
            "post-short-text-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "post-short-text-registration-localization-missing",
        )
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "post-short-text-registration-pass-chain-mismatch",
        )
        proposals = decision.get("proposals", [])
        baseline_dir = evidence / "baseline" if (evidence / "baseline" / "scene.json").is_file() else run / "output"
        if (baseline_dir / "scene.json").is_file() and (baseline_dir / "preview.png").is_file():
            baseline_scene = read_json(baseline_dir / "scene.json")
            expected_scene, computed_proposals = propose_text_registration(
                baseline_scene, run / "input" / "normalized.png", baseline_dir / "preview.png",
                max_shift=6, min_absolute_gain=1.0, min_relative_gain=.05,
            )
            require(proposals == computed_proposals, "post-short-text-registration-proposal-mismatch")
        else:
            expected_scene = {}
            require(False, "post-short-text-registration-baseline-missing")
        if proposals:
            candidate_dir = evidence / "candidate"
            required = [baseline_dir / "editable.pptx", candidate_dir / "scene.json",
                        candidate_dir / "editable.pptx", candidate_dir / "report.json"]
            if all(path.is_file() for path in required):
                require(
                    decision.get("baseline_scene_sha256") == sha256(baseline_dir / "scene.json")
                    and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx")
                    and decision.get("candidate_scene_sha256") == sha256(candidate_dir / "scene.json")
                    and decision.get("candidate_pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                    "post-short-text-registration-evidence-hash-mismatch",
                )
                require(expected_scene == read_json(candidate_dir / "scene.json"),
                        "post-short-text-registration-unsafe-scene-change")
                candidate_report = read_json(candidate_dir / "report.json")
                assessment = decision.get("assessment") or {}
                before, after = assessment.get("before_global") or {}, assessment.get("after_global") or {}
                baseline_fallbacks = read_json(baseline_dir / "report.json")["compiler"].get("fallbacks", [])
                expected_accept = (
                    decision.get("compiler_status") == candidate_report.get("status") == "render_success"
                    and decision.get("baseline_fallbacks") == baseline_fallbacks
                    and decision.get("candidate_fallbacks") == candidate_report.get("compiler", {}).get("fallbacks", []) == baseline_fallbacks
                    and after.get("pixel_similarity", -1) > before.get("pixel_similarity", -1)
                    and (after.get("edge_iou") or 0) >= (before.get("edge_iou") or 0)
                )
                require(decision.get("accepted") is expected_accept,
                        "post-short-text-registration-acceptance-mismatch")
            else:
                require(False, "post-short-text-registration-evidence-missing")
        else:
            require(decision.get("accepted") is False,
                    "post-short-text-registration-unexpected-acceptance")
        expected_final_scene = (decision.get("candidate_scene_sha256") if decision.get("accepted")
                                else decision.get("baseline_scene_sha256"))
        expected_final_pptx = (decision.get("candidate_pptx_sha256") if decision.get("accepted")
                               else decision.get("baseline_pptx_sha256"))
        require(
            decision.get("final_scene_sha256") == expected_final_scene
            and decision.get("final_pptx_sha256") == expected_final_pptx,
            "post-short-text-registration-final-chain-mismatch",
        )
        previous_final_hash = expected_final_scene
        previous_final_pptx_hash = expected_final_pptx
    flat_panel_registration = status.get("flat_panel_registration")
    if version_parts >= (0, 6, 13) and not flat_panel_registration:
        require(False, "flat-panel-registration-missing")
    if flat_panel_registration:
        evidence = run / "flat-panel-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "flat-panel-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == flat_panel_registration.get("status")
            and decision.get("accepted") == flat_panel_registration.get("accepted"),
            "flat-panel-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "flat-panel-registration-localization-missing",
        )
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "flat-panel-registration-pass-chain-mismatch",
        )
        proposals = decision.get("proposals", [])
        baseline_dir = evidence / "baseline" if (evidence / "baseline" / "scene.json").is_file() else run / "output"
        baseline_scene_path = baseline_dir / "scene.json"
        if baseline_dir != run / "output":
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_scene_path)
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "flat-panel-registration-baseline-hash-mismatch",
            )
        if baseline_scene_path.is_file():
            baseline_scene = read_json(baseline_scene_path)
            require(
                proposals == measure_flat_panels(baseline_scene, run / "input" / "normalized.png"),
                "flat-panel-registration-proposal-mismatch",
            )
        else:
            require(False, "flat-panel-registration-baseline-missing")
        running_scene_path = baseline_scene_path
        running_pptx_path = baseline_dir / "editable.pptx"
        running_preview_path = baseline_dir / "preview.png"
        attempts = decision.get("attempts", [])
        position = 0
        any_selected = False
        for proposal in proposals:
            group = attempts[position:position + len(proposal["variants"])]
            require(len(group) == len(proposal["variants"]), "flat-panel-registration-attempt-count-mismatch")
            eligible = []
            for local_index, variant in enumerate(proposal["variants"]):
                attempt_index = position + local_index + 1
                attempt = group[local_index] if local_index < len(group) else {}
                candidate_dir = evidence / f"candidate-{attempt_index:03d}"
                candidate_scene_path = candidate_dir / "scene.json"
                candidate_pptx_path = candidate_dir / "editable.pptx"
                if not all(path.is_file() for path in (candidate_scene_path, candidate_pptx_path,
                        candidate_dir / "preview.png", candidate_dir / "report.json",
                        running_scene_path, running_pptx_path, running_preview_path)):
                    require(False, "flat-panel-registration-evidence-missing", attempt_index)
                    continue
                expected_scene = apply_flat_panel(read_json(running_scene_path), proposal, variant)
                require(
                    attempt.get("proposal_id") == proposal["id"]
                    and attempt.get("variant") == variant
                    and read_json(candidate_scene_path) == expected_scene,
                    "flat-panel-registration-unsafe-scene-change", attempt_index,
                )
                require(
                    attempt.get("baseline_scene_sha256") == sha256(running_scene_path)
                    and attempt.get("baseline_pptx_sha256") == sha256(running_pptx_path)
                    and attempt.get("candidate_scene_sha256") == sha256(candidate_scene_path)
                    and attempt.get("candidate_pptx_sha256") == sha256(candidate_pptx_path),
                    "flat-panel-registration-evidence-hash-mismatch", attempt_index,
                )
                with Image.open(run / "input" / "normalized.png") as reference_image:
                    canvas = reference_image.size
                actual_assessment = compare_visual(
                    run / "input" / "normalized.png", running_preview_path,
                    candidate_dir / "preview.png", [0, 0, *canvas],
                )
                require(attempt.get("assessment") == actual_assessment,
                        "flat-panel-registration-assessment-mismatch", attempt_index)
                before, after = actual_assessment["before_global"], actual_assessment["after_global"]
                baseline_fallbacks = read_json(running_scene_path.parent / "report.json")["compiler"].get("fallbacks", [])
                candidate_report = read_json(candidate_dir / "report.json")
                qualified = (
                    candidate_report.get("status") == attempt.get("compiler_status") == "render_success"
                    and baseline_fallbacks == attempt.get("baseline_fallbacks")
                    and candidate_report.get("compiler", {}).get("fallbacks", []) == attempt.get("candidate_fallbacks") == baseline_fallbacks
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0)
                )
                require(attempt.get("qualified") is qualified,
                        "flat-panel-registration-qualification-mismatch", attempt_index)
                if qualified:
                    eligible.append((local_index, attempt))
            expected_selected = (max(eligible, key=lambda item: (
                item[1]["assessment"]["after_global"]["pixel_similarity"],
                item[1]["assessment"]["after_global"]["edge_iou"] or 0,
            ))[0] if eligible else None)
            require(
                [index for index, attempt in enumerate(group) if attempt.get("selected")]
                == ([] if expected_selected is None else [expected_selected]),
                "flat-panel-registration-selection-mismatch", proposal["id"],
            )
            if expected_selected is not None:
                any_selected = True
                chosen_dir = evidence / f"candidate-{position + expected_selected + 1:03d}"
                running_scene_path = chosen_dir / "scene.json"
                running_pptx_path = chosen_dir / "editable.pptx"
                running_preview_path = chosen_dir / "preview.png"
            position += len(group)
        require(position == len(attempts), "flat-panel-registration-extra-attempts")
        require(decision.get("accepted") is any_selected,
                "flat-panel-registration-aggregate-acceptance-mismatch")
        expected_flat_scene = sha256(running_scene_path) if proposals else previous_final_hash
        expected_flat_pptx = sha256(running_pptx_path) if proposals else previous_final_pptx_hash
        require(
            decision.get("final_scene_sha256") == expected_flat_scene
            and decision.get("final_pptx_sha256") == expected_flat_pptx,
            "flat-panel-registration-final-chain-mismatch",
        )
        previous_final_hash = decision.get("final_scene_sha256")
        previous_final_pptx_hash = decision.get("final_pptx_sha256")
    rect_fill_registration = status.get("rect_fill_registration")
    if version_parts >= (0, 6, 14) and not rect_fill_registration:
        require(False, "rect-fill-registration-missing")
    if rect_fill_registration:
        evidence = run / "rect-fill-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "rect-fill-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == rect_fill_registration.get("status")
            and decision.get("accepted") == rect_fill_registration.get("accepted"),
            "rect-fill-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "rect-fill-registration-localization-missing",
        )
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "rect-fill-registration-pass-chain-mismatch",
        )
        proposals = decision.get("proposals", [])
        baseline_dir = evidence / "baseline" if (evidence / "baseline" / "scene.json").is_file() else run / "output"
        baseline_scene_path = baseline_dir / "scene.json"
        if baseline_dir != run / "output":
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_scene_path)
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "rect-fill-registration-baseline-hash-mismatch",
            )
        if baseline_scene_path.is_file():
            baseline_scene = read_json(baseline_scene_path)
            require(
                proposals == measure_rect_fills(
                    baseline_scene, run / "input" / "normalized.png", baseline_dir / "preview.png"),
                "rect-fill-registration-proposal-mismatch",
            )
        else:
            baseline_scene = {}
            require(False, "rect-fill-registration-baseline-missing")
        if proposals:
            candidate_dir = evidence / "candidate"
            required = [baseline_dir / "editable.pptx", candidate_dir / "scene.json",
                        candidate_dir / "editable.pptx", candidate_dir / "preview.png",
                        candidate_dir / "report.json"]
            if all(path.is_file() for path in required):
                require(
                    decision.get("baseline_scene_sha256") == sha256(baseline_scene_path)
                    and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx")
                    and decision.get("candidate_scene_sha256") == sha256(candidate_dir / "scene.json")
                    and decision.get("candidate_pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                    "rect-fill-registration-evidence-hash-mismatch",
                )
                require(read_json(candidate_dir / "scene.json") == apply_rect_fill(baseline_scene, proposals),
                        "rect-fill-registration-unsafe-scene-change")
                with Image.open(run / "input" / "normalized.png") as reference_image:
                    canvas = reference_image.size
                candidate_scene = read_json(candidate_dir / "scene.json")
                assessment_region = registration_assessment_region(
                    version_parts, baseline_scene, candidate_scene,
                    decision["affected_primitive_ids"], canvas,
                )
                if version_parts >= (0, 6, 19):
                    require(decision.get("evaluation_region") == assessment_region
                            and decision.get("evaluation_region_source") == "affected_scene_owners",
                            "rect-fill-registration-region-mismatch")
                assessment = compare_visual(
                    run / "input" / "normalized.png", baseline_dir / "preview.png",
                    candidate_dir / "preview.png", assessment_region,
                )
                require(decision.get("assessment") == assessment,
                        "rect-fill-registration-assessment-mismatch")
                before, after = assessment["before_global"], assessment["after_global"]
                baseline_fallbacks = read_json(baseline_dir / "report.json")["compiler"].get("fallbacks", [])
                candidate_report = read_json(candidate_dir / "report.json")
                expected_accept = (
                    decision.get("compiler_status") == candidate_report.get("status") == "render_success"
                    and decision.get("baseline_fallbacks") == baseline_fallbacks
                    and decision.get("candidate_fallbacks") == candidate_report.get("compiler", {}).get("fallbacks", []) == baseline_fallbacks
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0)
                )
                require(decision.get("accepted") is expected_accept,
                        "rect-fill-registration-acceptance-mismatch")
            else:
                require(False, "rect-fill-registration-evidence-missing")
        else:
            require(decision.get("accepted") is False, "rect-fill-registration-unexpected-acceptance")
        expected_final_scene = (decision.get("candidate_scene_sha256") if decision.get("accepted")
                                else decision.get("baseline_scene_sha256"))
        expected_final_pptx = (decision.get("candidate_pptx_sha256") if decision.get("accepted")
                               else decision.get("baseline_pptx_sha256"))
        require(
            decision.get("final_scene_sha256") == expected_final_scene
            and decision.get("final_pptx_sha256") == expected_final_pptx,
            "rect-fill-registration-final-chain-mismatch",
        )
        previous_final_hash = expected_final_scene
        previous_final_pptx_hash = expected_final_pptx
    styled_multiline_registration = status.get("styled_multiline_registration")
    if version_parts >= (0, 6, 15) and not styled_multiline_registration:
        require(False, "styled-multiline-registration-missing")
    if styled_multiline_registration:
        evidence = run / "styled-multiline-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "styled-multiline-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == styled_multiline_registration.get("status")
            and decision.get("accepted") == styled_multiline_registration.get("accepted"),
            "styled-multiline-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "styled-multiline-registration-localization-missing",
        )
        baseline_dir = evidence / "baseline"
        baseline_scene_path = baseline_dir / "scene.json"
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "styled-multiline-registration-pass-chain-mismatch",
        )
        if baseline_scene_path.is_file() and (baseline_dir / "preview.png").is_file():
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_scene_path)
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "styled-multiline-registration-baseline-hash-mismatch",
            )
            baseline_scene = read_json(baseline_scene_path)
            proposals = decision.get("proposals", [])
            require(
                proposals == propose_styled_multiline(
                    baseline_scene, run / "input" / "normalized.png", baseline_dir / "preview.png"),
                "styled-multiline-registration-proposal-mismatch",
            )
        else:
            proposals = []
            baseline_scene = {}
            require(False, "styled-multiline-registration-baseline-missing")
        if proposals:
            candidate_dir = evidence / "candidate"
            required = [candidate_dir / "scene.json", candidate_dir / "editable.pptx",
                        candidate_dir / "preview.png", candidate_dir / "report.json"]
            if all(path.is_file() for path in required):
                require(
                    decision.get("candidate_scene_sha256") == sha256(candidate_dir / "scene.json")
                    and decision.get("candidate_pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                    "styled-multiline-registration-evidence-hash-mismatch",
                )
                require(read_json(candidate_dir / "scene.json") == apply_styled_multiline(baseline_scene, proposals),
                        "styled-multiline-registration-unsafe-scene-change")
                with Image.open(run / "input" / "normalized.png") as reference_image:
                    canvas = reference_image.size
                candidate_scene = read_json(candidate_dir / "scene.json")
                assessment_region = registration_assessment_region(
                    version_parts, baseline_scene, candidate_scene,
                    decision["affected_primitive_ids"], canvas,
                )
                if version_parts >= (0, 6, 19):
                    require(decision.get("evaluation_region") == assessment_region
                            and decision.get("evaluation_region_source") == "affected_scene_owners",
                            "styled-multiline-registration-region-mismatch")
                assessment = compare_visual(
                    run / "input" / "normalized.png", baseline_dir / "preview.png",
                    candidate_dir / "preview.png", assessment_region,
                )
                require(decision.get("assessment") == assessment,
                        "styled-multiline-registration-assessment-mismatch")
                before, after = assessment["before_global"], assessment["after_global"]
                baseline_fallbacks = read_json(baseline_dir / "report.json")["compiler"].get("fallbacks", [])
                candidate_report = read_json(candidate_dir / "report.json")
                expected_accept = (
                    decision.get("compiler_status") == candidate_report.get("status") == "render_success"
                    and decision.get("baseline_fallbacks") == baseline_fallbacks
                    and decision.get("candidate_fallbacks") == candidate_report.get("compiler", {}).get("fallbacks", []) == baseline_fallbacks
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0)
                )
                require(decision.get("accepted") is expected_accept,
                        "styled-multiline-registration-acceptance-mismatch")
            else:
                require(False, "styled-multiline-registration-evidence-missing")
        else:
            require(decision.get("accepted") is False,
                    "styled-multiline-registration-unexpected-acceptance")
        expected_final_scene = (decision.get("candidate_scene_sha256") if decision.get("accepted")
                                else decision.get("baseline_scene_sha256"))
        expected_final_pptx = (decision.get("candidate_pptx_sha256") if decision.get("accepted")
                               else decision.get("baseline_pptx_sha256"))
        require(
            decision.get("final_scene_sha256") == expected_final_scene
            and decision.get("final_pptx_sha256") == expected_final_pptx,
            "styled-multiline-registration-final-chain-mismatch",
        )
        previous_final_hash = expected_final_scene
        previous_final_pptx_hash = expected_final_pptx
    text_tracking_registration = status.get("text_tracking_registration")
    if version_parts >= (0, 6, 16) and not text_tracking_registration:
        require(False, "text-tracking-registration-missing")
    if text_tracking_registration:
        evidence = run / "text-tracking-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "text-tracking-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == text_tracking_registration.get("status")
            and decision.get("accepted") == text_tracking_registration.get("accepted"),
            "text-tracking-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "text-tracking-registration-localization-missing",
        )
        baseline_dir = evidence / "baseline"
        baseline_scene_path = baseline_dir / "scene.json"
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "text-tracking-registration-pass-chain-mismatch",
        )
        if baseline_scene_path.is_file() and (baseline_dir / "preview.png").is_file():
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_scene_path)
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "text-tracking-registration-baseline-hash-mismatch",
            )
            baseline_scene = read_json(baseline_scene_path)
            rows = eligible_text_tracking(baseline_scene)
            require(decision.get("eligible") == rows and decision.get("candidates") == list(TRACKING_CANDIDATES),
                    "text-tracking-registration-eligibility-mismatch")
        else:
            baseline_scene = {}
            rows = []
            require(False, "text-tracking-registration-baseline-missing")
        attempts = decision.get("attempts", [])
        require(len(attempts) == (len(TRACKING_CANDIDATES) if rows else 0),
                "text-tracking-registration-attempt-count-mismatch")
        qualified_rows = []
        for index, spacing in enumerate(TRACKING_CANDIDATES if rows else ()):
            attempt = attempts[index] if index < len(attempts) else {}
            candidate_id = f"tracking-{int(spacing)}"
            candidate_dir = evidence / candidate_id
            required = [candidate_dir / "scene.json", candidate_dir / "editable.pptx",
                        candidate_dir / "preview.png", candidate_dir / "report.json"]
            if not all(path.is_file() for path in required):
                require(False, "text-tracking-registration-evidence-missing", candidate_id)
                continue
            require(
                attempt.get("candidate_id") == candidate_id and attempt.get("spacing") == spacing
                and attempt.get("candidate_scene_sha256") == sha256(candidate_dir / "scene.json")
                and attempt.get("candidate_pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                "text-tracking-registration-evidence-hash-mismatch", candidate_id,
            )
            require(read_json(candidate_dir / "scene.json") == apply_text_tracking(baseline_scene, rows, spacing),
                    "text-tracking-registration-unsafe-scene-change", candidate_id)
            with Image.open(run / "input" / "normalized.png") as reference_image:
                canvas = reference_image.size
            candidate_scene = read_json(candidate_dir / "scene.json")
            assessment_region = registration_assessment_region(
                version_parts, baseline_scene, candidate_scene,
                decision["affected_primitive_ids"], canvas,
            )
            if version_parts >= (0, 6, 19):
                require(decision.get("evaluation_region") == assessment_region
                        and decision.get("evaluation_region_source") == "affected_scene_owners",
                        "text-tracking-registration-region-mismatch", candidate_id)
            assessment = compare_visual(
                run / "input" / "normalized.png", baseline_dir / "preview.png",
                candidate_dir / "preview.png", assessment_region,
            )
            require(attempt.get("assessment") == assessment,
                    "text-tracking-registration-assessment-mismatch", candidate_id)
            before, after = assessment["before_global"], assessment["after_global"]
            baseline_fallbacks = read_json(baseline_dir / "report.json")["compiler"].get("fallbacks", [])
            candidate_report = read_json(candidate_dir / "report.json")
            qualified = (
                attempt.get("compiler_status") == candidate_report.get("status") == "render_success"
                and attempt.get("baseline_fallbacks") == baseline_fallbacks
                and attempt.get("candidate_fallbacks") == candidate_report.get("compiler", {}).get("fallbacks", []) == baseline_fallbacks
                and after["pixel_similarity"] > before["pixel_similarity"]
                and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0)
            )
            require(attempt.get("qualified") is qualified,
                    "text-tracking-registration-qualification-mismatch", candidate_id)
            if qualified:
                qualified_rows.append(attempt)
        chosen = (max(qualified_rows, key=lambda row: (
            row["assessment"]["after_global"]["pixel_similarity"],
            row["assessment"]["after_global"]["edge_iou"] or 0,
        )) if qualified_rows else None)
        expected_selected = chosen["candidate_id"] if chosen else None
        require(decision.get("selected_candidate_id") == expected_selected
                and decision.get("accepted") is (chosen is not None),
                "text-tracking-registration-selection-mismatch")
        expected_final_scene = chosen["candidate_scene_sha256"] if chosen else decision.get("baseline_scene_sha256")
        expected_final_pptx = chosen["candidate_pptx_sha256"] if chosen else decision.get("baseline_pptx_sha256")
        require(
            decision.get("final_scene_sha256") == expected_final_scene
            and decision.get("final_pptx_sha256") == expected_final_pptx,
            "text-tracking-registration-final-chain-mismatch",
        )
        previous_final_hash = expected_final_scene
        previous_final_pptx_hash = expected_final_pptx
    post_tracking_text_registration = status.get("post_tracking_text_registration")
    if (0, 6, 17) <= version_parts < (0, 6, 18) and not post_tracking_text_registration:
        require(False, "post-tracking-text-registration-missing")
    if post_tracking_text_registration:
        evidence = run / "post-tracking-text-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "post-tracking-text-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == post_tracking_text_registration.get("status")
            and decision.get("accepted") == post_tracking_text_registration.get("accepted"),
            "post-tracking-text-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "post-tracking-text-registration-localization-missing",
        )
        baseline_dir = evidence / "baseline"
        required_baseline = [baseline_dir / "scene.json", baseline_dir / "editable.pptx",
                             baseline_dir / "preview.png", baseline_dir / "report.json"]
        require(all(path.is_file() for path in required_baseline),
                "post-tracking-text-registration-baseline-missing")
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "post-tracking-text-registration-pass-chain-mismatch",
        )
        if all(path.is_file() for path in required_baseline):
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_dir / "scene.json")
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "post-tracking-text-registration-baseline-hash-mismatch",
            )
            baseline_scene = read_json(baseline_dir / "scene.json")
            expected_scene, computed_proposals = propose_text_registration(
                baseline_scene, run / "input" / "normalized.png", baseline_dir / "preview.png",
                max_shift=6, min_absolute_gain=1.0, min_relative_gain=.03,
            )
            proposals = decision.get("proposals", [])
            require(proposals == computed_proposals,
                    "post-tracking-text-registration-proposal-mismatch")
            by_id = {item["id"]: item for item in baseline_scene["elements"]}
            boxes = [by_id[item["id"]]["bbox"] for item in computed_proposals]
            expected_region = ([min(box[0] for box in boxes), min(box[1] for box in boxes),
                                max(box[0] + box[2] for box in boxes) - min(box[0] for box in boxes),
                                max(box[1] + box[3] for box in boxes) - min(box[1] for box in boxes)]
                               if boxes else [0, 0, baseline_scene["canvas"]["width"], baseline_scene["canvas"]["height"]])
            require(
                decision.get("owner_stage") == "layout"
                and decision.get("field") == "bbox"
                and decision.get("evaluation_region") == expected_region
                and decision.get("evaluation_region_source") == ("affected_scene_owners" if boxes else "full_canvas_no_candidate")
                and decision.get("affected_label_ids") == sorted({item["source_label_id"] for item in computed_proposals})
                and decision.get("affected_primitive_ids") == [item["id"] for item in computed_proposals]
                and decision.get("selected_candidate_id") == ("deterministic-post-tracking-text-registration" if boxes else None),
                "post-tracking-text-registration-localization-mismatch",
            )
            if proposals:
                candidate_dir = evidence / "candidate"
                required_candidate = [candidate_dir / "scene.json", candidate_dir / "editable.pptx",
                                      candidate_dir / "preview.png", candidate_dir / "report.json"]
                if all(path.is_file() for path in required_candidate):
                    require(
                        decision.get("candidate_scene_sha256") == sha256(candidate_dir / "scene.json")
                        and decision.get("candidate_pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                        "post-tracking-text-registration-candidate-hash-mismatch",
                    )
                    require(expected_scene == read_json(candidate_dir / "scene.json"),
                            "post-tracking-text-registration-unsafe-scene-change")
                    with Image.open(run / "input" / "normalized.png") as reference_image:
                        canvas = reference_image.size
                    assessment = compare_visual(
                        run / "input" / "normalized.png", baseline_dir / "preview.png",
                        candidate_dir / "preview.png", [0, 0, *canvas],
                    )
                    require(decision.get("assessment") == assessment,
                            "post-tracking-text-registration-assessment-mismatch")
                    before, after = assessment["before_global"], assessment["after_global"]
                    baseline_fallbacks = read_json(baseline_dir / "report.json")["compiler"].get("fallbacks", [])
                    candidate_report = read_json(candidate_dir / "report.json")
                    accepted = (
                        decision.get("compiler_status") == candidate_report.get("status") == "render_success"
                        and decision.get("baseline_fallbacks") == baseline_fallbacks
                        and decision.get("candidate_fallbacks") == candidate_report.get("compiler", {}).get("fallbacks", []) == baseline_fallbacks
                        and after["pixel_similarity"] > before["pixel_similarity"]
                        and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0)
                    )
                    require(decision.get("accepted") is accepted,
                            "post-tracking-text-registration-acceptance-mismatch")
                else:
                    require(False, "post-tracking-text-registration-candidate-missing")
            else:
                require(decision.get("accepted") is False,
                        "post-tracking-text-registration-unexpected-acceptance")
        expected_final_scene = (decision.get("candidate_scene_sha256") if decision.get("accepted")
                                else decision.get("baseline_scene_sha256"))
        expected_final_pptx = (decision.get("candidate_pptx_sha256") if decision.get("accepted")
                               else decision.get("baseline_pptx_sha256"))
        require(
            decision.get("final_scene_sha256") == expected_final_scene
            and decision.get("final_pptx_sha256") == expected_final_pptx,
            "post-tracking-text-registration-final-chain-mismatch",
        )
        previous_final_hash = expected_final_scene
        previous_final_pptx_hash = expected_final_pptx
    joint_shadow_text_registration = status.get("joint_shadow_text_registration")
    if version_parts >= (0, 6, 18) and not joint_shadow_text_registration:
        require(False, "joint-shadow-text-registration-missing")
    if joint_shadow_text_registration:
        evidence = run / "joint-shadow-text-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "joint-shadow-text-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == joint_shadow_text_registration.get("status")
            and decision.get("accepted") == joint_shadow_text_registration.get("accepted"),
            "joint-shadow-text-registration-status-mismatch",
        )
        require(
            {"owner_stage", "mechanism", "affected_label_ids", "affected_primitive_ids", "field", "evaluation_region", "evaluation_region_source", "selected_candidate_id"}.issubset(decision),
            "joint-shadow-text-registration-localization-missing",
        )
        baseline_dir = evidence / "baseline"
        required_baseline = [baseline_dir / "scene.json", baseline_dir / "editable.pptx",
                             baseline_dir / "preview.png", baseline_dir / "report.json"]
        require(all(path.is_file() for path in required_baseline),
                "joint-shadow-text-registration-baseline-missing")
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "joint-shadow-text-registration-pass-chain-mismatch",
        )
        if all(path.is_file() for path in required_baseline):
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_dir / "scene.json")
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "joint-shadow-text-registration-baseline-hash-mismatch",
            )
            baseline_scene = read_json(baseline_dir / "scene.json")
            reference = run / "input" / "normalized.png"
            owners = measure_joint_shadow_owners(baseline_scene, reference, baseline_dir / "preview.png")
            require(decision.get("shadow_owners") == owners
                    and decision.get("shadow_offset_deltas") == list(SHADOW_OFFSET_DELTAS),
                    "joint-shadow-text-registration-owner-mismatch")
            expected_ids = []
            affected_ids = {row["id"] for row in owners}
            text_only_scene, text_only_proposals = propose_joint_text(
                baseline_scene, reference, baseline_dir / "preview.png")
            if text_only_proposals:
                expected_ids.append("text-only")
            expected_ids += [f"shadow-plus-text-{delta}" for delta in SHADOW_OFFSET_DELTAS if owners]
            attempts = decision.get("attempts", [])
            require([item.get("candidate_id") for item in attempts] == expected_ids,
                    "joint-shadow-text-registration-attempt-list-mismatch")
            baseline_fallbacks = read_json(baseline_dir / "report.json")["compiler"].get("fallbacks", [])
            qualified_rows = []
            for candidate_id in expected_ids:
                attempt = next((item for item in attempts if item.get("candidate_id") == candidate_id), {})
                if candidate_id == "text-only":
                    delta = None
                    intermediate_scene, intermediate_preview = baseline_scene, baseline_dir / "preview.png"
                    expected_scene, proposals = text_only_scene, text_only_proposals
                else:
                    delta = int(candidate_id.rsplit("-", 1)[1])
                    shadow_dir = evidence / f"shadow-{delta}"
                    required_shadow = [shadow_dir / "scene.json", shadow_dir / "editable.pptx",
                                       shadow_dir / "preview.png", shadow_dir / "report.json"]
                    if not all(path.is_file() for path in required_shadow):
                        require(False, "joint-shadow-text-registration-shadow-evidence-missing", candidate_id)
                        continue
                    intermediate_scene = apply_joint_shadow(baseline_scene, owners, delta)
                    intermediate_preview = shadow_dir / "preview.png"
                    require(
                        intermediate_scene == read_json(shadow_dir / "scene.json")
                        and attempt.get("shadow_scene_sha256") == sha256(shadow_dir / "scene.json")
                        and attempt.get("shadow_pptx_sha256") == sha256(shadow_dir / "editable.pptx"),
                        "joint-shadow-text-registration-shadow-mismatch", candidate_id,
                    )
                    expected_scene, proposals = propose_joint_text(intermediate_scene, reference, intermediate_preview)
                affected_ids.update(item["id"] for item in proposals)
                candidate_dir = evidence / candidate_id
                required_candidate = [candidate_dir / "scene.json", candidate_dir / "editable.pptx",
                                      candidate_dir / "preview.png", candidate_dir / "report.json"]
                if not all(path.is_file() for path in required_candidate):
                    require(False, "joint-shadow-text-registration-candidate-missing", candidate_id)
                    continue
                require(attempt.get("shadow_offset_delta") == delta
                        and attempt.get("text_proposals") == proposals,
                        "joint-shadow-text-registration-proposal-mismatch", candidate_id)
                require(
                    attempt.get("candidate_scene_sha256") == sha256(candidate_dir / "scene.json")
                    and attempt.get("candidate_pptx_sha256") == sha256(candidate_dir / "editable.pptx")
                    and read_json(candidate_dir / "scene.json") == expected_scene,
                    "joint-shadow-text-registration-candidate-hash-or-scene-mismatch", candidate_id,
                )
                attempt_boxes = [
                    next(item for item in baseline_scene["elements"] if item["id"] == item_id)["bbox"]
                    for item_id in sorted(affected_ids)
                ]
                attempt_region = ([min(box[0] for box in attempt_boxes), min(box[1] for box in attempt_boxes),
                                   max(box[0] + box[2] for box in attempt_boxes) - min(box[0] for box in attempt_boxes),
                                   max(box[1] + box[3] for box in attempt_boxes) - min(box[1] for box in attempt_boxes)]
                                  if attempt_boxes else [0, 0, baseline_scene["canvas"]["width"], baseline_scene["canvas"]["height"]])
                if version_parts >= (0, 6, 19):
                    require(attempt.get("evaluation_region") == attempt_region
                            and attempt.get("evaluation_region_source") == ("affected_scene_owners" if attempt_boxes else "full_canvas_no_candidate"),
                            "joint-shadow-text-registration-region-mismatch", candidate_id)
                assessment_region = (attempt_region if version_parts >= (0, 6, 19)
                                     else [0, 0, baseline_scene["canvas"]["width"], baseline_scene["canvas"]["height"]])
                assessment = compare_visual(reference, baseline_dir / "preview.png",
                                            candidate_dir / "preview.png", assessment_region)
                require(attempt.get("assessment") == assessment,
                        "joint-shadow-text-registration-assessment-mismatch", candidate_id)
                candidate_report = read_json(candidate_dir / "report.json")
                before, after = assessment["before_global"], assessment["after_global"]
                qualified = (
                    attempt.get("compiler_status") == candidate_report.get("status") == "render_success"
                    and attempt.get("baseline_fallbacks") == baseline_fallbacks
                    and attempt.get("candidate_fallbacks") == candidate_report.get("compiler", {}).get("fallbacks", []) == baseline_fallbacks
                    and after["pixel_similarity"] > before["pixel_similarity"]
                    and (after["edge_iou"] or 0) >= (before["edge_iou"] or 0)
                )
                require(attempt.get("qualified") is qualified,
                        "joint-shadow-text-registration-qualification-mismatch", candidate_id)
                if qualified:
                    qualified_rows.append(attempt)
            selected = (max(qualified_rows, key=lambda item: (
                item["assessment"]["after_global"]["pixel_similarity"],
                item["assessment"]["after_global"]["edge_iou"] or 0,
            )) if qualified_rows else None)
            require(decision.get("selected_candidate_id") == (selected["candidate_id"] if selected else None)
                    and decision.get("accepted") is (selected is not None),
                    "joint-shadow-text-registration-selection-mismatch")
            by_id = {element["id"]: element for element in baseline_scene["elements"]}
            affected = [by_id[item_id] for item_id in sorted(affected_ids)]
            boxes = [item["bbox"] for item in affected]
            expected_region = ([min(box[0] for box in boxes), min(box[1] for box in boxes),
                                max(box[0] + box[2] for box in boxes) - min(box[0] for box in boxes),
                                max(box[1] + box[3] for box in boxes) - min(box[1] for box in boxes)]
                               if boxes else [0, 0, baseline_scene["canvas"]["width"], baseline_scene["canvas"]["height"]])
            require(
                decision.get("owner_stage") == "layout"
                and decision.get("field") == ["style.shadow", "bbox"]
                and decision.get("affected_primitive_ids") == sorted(affected_ids)
                and decision.get("affected_label_ids") == sorted({item["source_label_id"] for item in affected})
                and decision.get("evaluation_region") == expected_region
                and decision.get("evaluation_region_source") == ("affected_scene_owners" if boxes else "full_canvas_no_candidate"),
                "joint-shadow-text-registration-localization-mismatch",
            )
        else:
            selected = None
        expected_final_scene = (selected["candidate_scene_sha256"] if selected
                                else decision.get("baseline_scene_sha256"))
        expected_final_pptx = (selected["candidate_pptx_sha256"] if selected
                               else decision.get("baseline_pptx_sha256"))
        require(decision.get("final_scene_sha256") == expected_final_scene
                and decision.get("final_pptx_sha256") == expected_final_pptx,
                "joint-shadow-text-registration-final-chain-mismatch")
        previous_final_hash = expected_final_scene
        previous_final_pptx_hash = expected_final_pptx
    native_text_style_registration = status.get("native_text_style_registration")
    if version_parts >= (0, 6, 19) and not native_text_style_registration:
        require(False, "native-text-style-registration-missing")
    if native_text_style_registration:
        evidence = run / "native-text-style-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "native-text-style-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == native_text_style_registration.get("status")
            and decision.get("accepted") == native_text_style_registration.get("accepted"),
            "native-text-style-registration-status-mismatch",
        )
        baseline_dir = evidence / "baseline"
        required_baseline = [baseline_dir / "scene.json", baseline_dir / "editable.pptx",
                             baseline_dir / "preview.png", baseline_dir / "report.json"]
        require(all(path.is_file() for path in required_baseline),
                "native-text-style-registration-baseline-missing")
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None
                 or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "native-text-style-registration-pass-chain-mismatch",
        )
        if all(path.is_file() for path in required_baseline):
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_dir / "scene.json")
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "native-text-style-registration-baseline-hash-mismatch",
            )
            reference = run / "input" / "normalized.png"
            initial_triggers = dark_ink_candidates(
                read_json(baseline_dir / "scene.json"), reference, baseline_dir / "preview.png")
            require(decision.get("triggers") == initial_triggers,
                    "native-text-style-registration-trigger-mismatch")
            owner_decisions = decision.get("owner_decisions", [])
            require(len(owner_decisions) == len(initial_triggers),
                    "native-text-style-registration-owner-count-mismatch")
            current_dir = baseline_dir
            accepted_ids = []
            last_selected = None
            last_region = None
            for index, trigger in enumerate(initial_triggers, 1):
                if index > len(owner_decisions):
                    break
                owner = owner_decisions[index - 1]
                owner_id = trigger["id"]
                current_scene = read_json(current_dir / "scene.json")
                live_triggers = {item["id"] for item in dark_ink_candidates(
                    current_scene, reference, current_dir / "preview.png")}
                if owner_id not in live_triggers:
                    require(owner.get("owner_id") == owner_id
                            and owner.get("status") == "trigger-resolved-by-prior-owner"
                            and owner.get("accepted") is False,
                            "native-text-style-registration-resolved-trigger-mismatch", owner_id)
                    continue
                search_path = evidence / f"owner-{index:03d}" / "search-report.json"
                require(search_path.is_file(), "native-text-style-registration-search-missing", owner_id)
                if not search_path.is_file():
                    continue
                search = read_json(search_path)
                require(search.get("owner_id") == owner_id,
                        "native-text-style-registration-search-owner-mismatch", owner_id)
                qualified_rows = []
                baseline_fallbacks = read_json(current_dir / "report.json")["compiler"].get("fallbacks", [])
                require(search.get("baseline_fallbacks") == baseline_fallbacks,
                        "native-text-style-registration-baseline-fallback-mismatch", owner_id)
                for attempt in search.get("attempts", []):
                    if attempt.get("status") != "measured":
                        require(attempt.get("status") in {"style-render-failed", "shift-outside-canvas"},
                                "native-text-style-registration-attempt-status-invalid", owner_id)
                        continue
                    identity = attempt.get("candidate_id")
                    candidate_dir = evidence / f"owner-{index:03d}" / str(identity) / "candidate"
                    required_candidate = [candidate_dir / "scene.json", candidate_dir / "editable.pptx",
                                          candidate_dir / "preview.png", candidate_dir / "report.json"]
                    require(all(path.is_file() for path in required_candidate),
                            "native-text-style-registration-candidate-missing", identity)
                    if not all(path.is_file() for path in required_candidate):
                        continue
                    shift = attempt.get("source_ink_shift") or {}
                    expected_scene = styled_scene(
                        current_scene, owner_id, attempt["font_family"], attempt["font_size"],
                        shift.get("shift_x", 0), shift.get("shift_y", 0))
                    candidate_scene = read_json(candidate_dir / "scene.json")
                    region = affected_scene_region(current_scene, candidate_scene, [owner_id])
                    assessment = compare_visual(reference, current_dir / "preview.png",
                                                candidate_dir / "preview.png", region)
                    report = read_json(candidate_dir / "report.json")
                    require(expected_scene == candidate_scene
                            and attempt.get("scene_sha256") == sha256(candidate_dir / "scene.json")
                            and attempt.get("pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                            "native-text-style-registration-candidate-identity-mismatch", identity)
                    require(attempt.get("evaluation_region") == region
                            and attempt.get("evaluation_region_source") == "affected_scene_owners"
                            and attempt.get("assessment") == assessment,
                            "native-text-style-registration-assessment-mismatch", identity)
                    before_local, after_local = assessment["before_local"], assessment["after_local"]
                    before_global, after_global = assessment["before_global"], assessment["after_global"]
                    qualified = (
                        report.get("status") == "render_success"
                        and report["compiler"].get("fallbacks", []) == baseline_fallbacks
                        and after_local["pixel_similarity"] >= before_local["pixel_similarity"]
                        and (after_local["edge_iou"] or 0) >= (before_local["edge_iou"] or 0)
                        and after_global["pixel_similarity"] > before_global["pixel_similarity"]
                        and (after_global["edge_iou"] or 0) >= (before_global["edge_iou"] or 0)
                    )
                    require(attempt.get("qualified") is qualified,
                            "native-text-style-registration-qualification-mismatch", identity)
                    if qualified:
                        qualified_rows.append(attempt)
                best = max(qualified_rows, key=lambda item: (
                    item["assessment"]["after_global"]["pixel_similarity"],
                    item["assessment"]["after_global"]["edge_iou"] or 0,
                    item["assessment"]["after_local"]["pixel_similarity"],
                )) if qualified_rows else None
                require(search.get("best_candidate_id") == (best["candidate_id"] if best else None)
                        and owner.get("selected_candidate_id") == (best["candidate_id"] if best else None)
                        and owner.get("accepted") is (best is not None)
                        and owner.get("assessment") == (best["assessment"] if best else None),
                        "native-text-style-registration-selection-mismatch", owner_id)
                if best:
                    current_dir = evidence / f"owner-{index:03d}" / best["candidate_id"] / "candidate"
                    accepted_ids.append(owner_id)
                    last_selected = best["candidate_id"]
                    last_region = best["evaluation_region"]
            require(decision.get("accepted") is bool(accepted_ids)
                    and decision.get("affected_primitive_ids") == accepted_ids
                    and decision.get("selected_candidate_id") == last_selected
                    and decision.get("evaluation_region") == last_region,
                    "native-text-style-registration-final-selection-mismatch")
            expected_final_scene = sha256(current_dir / "scene.json")
            expected_final_pptx = sha256(current_dir / "editable.pptx")
            require(decision.get("final_scene_sha256") == expected_final_scene
                    and decision.get("final_pptx_sha256") == expected_final_pptx,
                    "native-text-style-registration-final-chain-mismatch")
            previous_final_hash = expected_final_scene
            previous_final_pptx_hash = expected_final_pptx
    single_line_monospace_registration = status.get("single_line_monospace_registration")
    if version_parts >= (0, 6, 19) and not single_line_monospace_registration:
        require(False, "single-line-monospace-registration-missing")
    if single_line_monospace_registration:
        evidence = run / "single-line-monospace-registration"
        decision_path = evidence / "decision.json"
        require(decision_path.is_file(), "single-line-monospace-registration-decision-missing")
        decision = read_json(decision_path) if decision_path.is_file() else {}
        require(
            decision.get("status") == single_line_monospace_registration.get("status")
            and decision.get("accepted") == single_line_monospace_registration.get("accepted"),
            "single-line-monospace-registration-status-mismatch",
        )
        baseline_dir = evidence / "baseline"
        required_baseline = [baseline_dir / "scene.json", baseline_dir / "editable.pptx",
                             baseline_dir / "preview.png", baseline_dir / "report.json"]
        require(all(path.is_file() for path in required_baseline),
                "single-line-monospace-registration-baseline-missing")
        require(
            decision.get("baseline_scene_sha256") == previous_final_hash
            and (previous_final_pptx_hash is None
                 or decision.get("baseline_pptx_sha256") == previous_final_pptx_hash),
            "single-line-monospace-registration-pass-chain-mismatch",
        )
        if all(path.is_file() for path in required_baseline):
            require(
                decision.get("baseline_scene_sha256") == sha256(baseline_dir / "scene.json")
                and decision.get("baseline_pptx_sha256") == sha256(baseline_dir / "editable.pptx"),
                "single-line-monospace-registration-baseline-hash-mismatch",
            )
            reference = run / "input" / "normalized.png"
            initial_triggers = single_line_monospace_ink_candidates(
                read_json(baseline_dir / "scene.json"), reference, baseline_dir / "preview.png")
            require(decision.get("triggers") == initial_triggers,
                    "single-line-monospace-registration-trigger-mismatch")
            owner_decisions = decision.get("owner_decisions", [])
            require(len(owner_decisions) == len(initial_triggers),
                    "single-line-monospace-registration-owner-count-mismatch")
            current_dir = baseline_dir
            accepted_ids = []
            last_selected = None
            last_region = None
            for index, trigger in enumerate(initial_triggers, 1):
                if index > len(owner_decisions):
                    break
                owner = owner_decisions[index - 1]
                owner_id = trigger["id"]
                current_scene = read_json(current_dir / "scene.json")
                live_triggers = {item["id"] for item in single_line_monospace_ink_candidates(
                    current_scene, reference, current_dir / "preview.png")}
                if owner_id not in live_triggers:
                    require(owner.get("owner_id") == owner_id
                            and owner.get("status") == "trigger-resolved-by-prior-owner"
                            and owner.get("accepted") is False,
                            "single-line-monospace-registration-resolved-trigger-mismatch", owner_id)
                    continue
                search_path = evidence / f"owner-{index:03d}" / "search-report.json"
                require(search_path.is_file(), "single-line-monospace-registration-search-missing", owner_id)
                if not search_path.is_file():
                    continue
                search = read_json(search_path)
                require(search.get("owner_id") == owner_id,
                        "single-line-monospace-registration-search-owner-mismatch", owner_id)
                qualified_rows = []
                baseline_fallbacks = read_json(current_dir / "report.json")["compiler"].get("fallbacks", [])
                require(search.get("baseline_fallbacks") == baseline_fallbacks,
                        "single-line-monospace-registration-baseline-fallback-mismatch", owner_id)
                for attempt in search.get("attempts", []):
                    if attempt.get("status") != "measured":
                        require(attempt.get("status") in {"box-outside-canvas", "style-render-failed", "shift-outside-canvas"},
                                "single-line-monospace-registration-attempt-status-invalid", owner_id)
                        continue
                    identity = attempt.get("candidate_id")
                    candidate_dir = evidence / f"owner-{index:03d}" / str(identity) / "candidate"
                    required_candidate = [candidate_dir / "scene.json", candidate_dir / "editable.pptx",
                                          candidate_dir / "preview.png", candidate_dir / "report.json"]
                    require(all(path.is_file() for path in required_candidate),
                            "single-line-monospace-registration-candidate-missing", identity)
                    if not all(path.is_file() for path in required_candidate):
                        continue
                    shift = attempt.get("source_ink_shift") or {}
                    expected_scene = scaled_monospace_scene(
                        current_scene, owner_id, attempt["font_size"],
                        shift.get("shift_x", 0), shift.get("shift_y", 0))
                    candidate_scene = read_json(candidate_dir / "scene.json")
                    region = affected_scene_region(current_scene, candidate_scene, [owner_id])
                    assessment = compare_visual(reference, current_dir / "preview.png",
                                                candidate_dir / "preview.png", region)
                    report = read_json(candidate_dir / "report.json")
                    require(expected_scene == candidate_scene
                            and attempt.get("scene_sha256") == sha256(candidate_dir / "scene.json")
                            and attempt.get("pptx_sha256") == sha256(candidate_dir / "editable.pptx"),
                            "single-line-monospace-registration-candidate-identity-mismatch", identity)
                    require(attempt.get("evaluation_region") == region
                            and attempt.get("evaluation_region_source") == "affected_scene_owners"
                            and attempt.get("assessment") == assessment,
                            "single-line-monospace-registration-assessment-mismatch", identity)
                    before_local, after_local = assessment["before_local"], assessment["after_local"]
                    before_global, after_global = assessment["before_global"], assessment["after_global"]
                    qualified = (
                        report.get("status") == "render_success"
                        and report["compiler"].get("fallbacks", []) == baseline_fallbacks
                        and after_local["pixel_similarity"] >= before_local["pixel_similarity"]
                        and (after_local["edge_iou"] or 0) >= (before_local["edge_iou"] or 0)
                        and after_global["pixel_similarity"] > before_global["pixel_similarity"]
                        and (after_global["edge_iou"] or 0) >= (before_global["edge_iou"] or 0)
                    )
                    require(attempt.get("qualified") is qualified,
                            "single-line-monospace-registration-qualification-mismatch", identity)
                    if qualified:
                        qualified_rows.append(attempt)
                best = max(qualified_rows, key=lambda item: (
                    item["assessment"]["after_global"]["pixel_similarity"],
                    item["assessment"]["after_global"]["edge_iou"] or 0,
                    item["assessment"]["after_local"]["pixel_similarity"],
                )) if qualified_rows else None
                require(search.get("best_candidate_id") == (best["candidate_id"] if best else None)
                        and owner.get("selected_candidate_id") == (best["candidate_id"] if best else None)
                        and owner.get("accepted") is (best is not None)
                        and owner.get("assessment") == (best["assessment"] if best else None),
                        "single-line-monospace-registration-selection-mismatch", owner_id)
                if best:
                    current_dir = evidence / f"owner-{index:03d}" / best["candidate_id"] / "candidate"
                    accepted_ids.append(owner_id)
                    last_selected = best["candidate_id"]
                    last_region = best["evaluation_region"]
            require(decision.get("accepted") is bool(accepted_ids)
                    and decision.get("affected_primitive_ids") == accepted_ids
                    and decision.get("selected_candidate_id") == last_selected
                    and decision.get("evaluation_region") == last_region,
                    "single-line-monospace-registration-final-selection-mismatch")
            expected_final_scene = sha256(current_dir / "scene.json")
            expected_final_pptx = sha256(current_dir / "editable.pptx")
            require(decision.get("final_scene_sha256") == expected_final_scene
                    and decision.get("final_pptx_sha256") == expected_final_pptx,
                    "single-line-monospace-registration-final-chain-mismatch")
            previous_final_hash = expected_final_scene
            previous_final_pptx_hash = expected_final_pptx
    if previous_final_hash is not None:
        require(previous_final_hash == sha256(run / "output" / "scene.json"), "text-registration-final-scene-hash-mismatch")
    if previous_final_pptx_hash is not None:
        require(previous_final_pptx_hash == sha256(run / "output" / "editable.pptx"), "text-registration-final-pptx-hash-mismatch")

    memory_injection = {}
    for stage in ("recognition", "representation", "layout"):
        call_input = run / "calls" / stage / "input.json"
        if call_input.is_file():
            payload = read_json(call_input)
            rows = payload.get("applicable_memories", [])
            memory_injection[stage] = {
                "count": len(rows),
                "memory_ids": [item.get("memory_id") for item in rows],
                "all_downstreamed": all(item.get("status") == "downstreamed" for item in rows),
                "all_stage_owned": all(item.get("owner_stage") == stage for item in rows),
            }
            require(memory_injection[stage]["count"] == 0, "generation-memory-present", memory_injection[stage])
    memory_injection["refinement"] = refinement_memory_injection(run)
    require(memory_injection["refinement"]["no_superseded"],
            "superseded-refinement-memory", memory_injection["refinement"])
    require(memory_injection["refinement"]["within_limit"],
            "refinement-memory-limit-exceeded", memory_injection["refinement"])

    return {
        "schema_version": "decomposer.batch-sample-audit.v2",
        "sample_id": run.name,
        "auditable": True,
        "passed": not failures,
        "status": status.get("status"),
        "version": status.get("version"),
        "degraded": bool(status.get("degraded")),
        "model_calls": model_calls,
        "counts": {
            "recognition_objects": len(recognition.get("objects", [])),
            "representation_components": len(representation.get("components", [])),
            "scene_elements": len(scene_ids),
            "editable_text_objects": len(expected_text),
            "localized_rasters": len(assets),
        },
        "visual": validation.get("visual"),
        "refinement": refinement,
        "text_registration": registration,
        "text_registration_pass_2": registration_pass_2,
        "source_line_registration": source_line_registration,
        "native_shadow_registration": native_shadow_registration,
        "short_text_registration": short_text_registration,
        "post_short_text_registration": post_short_text_registration,
        "flat_panel_registration": flat_panel_registration,
        "rect_fill_registration": rect_fill_registration,
        "styled_multiline_registration": styled_multiline_registration,
        "text_tracking_registration": text_tracking_registration,
        "post_tracking_text_registration": post_tracking_text_registration,
        "joint_shadow_text_registration": joint_shadow_text_registration,
        "native_text_style_registration": native_text_style_registration,
        "single_line_monospace_registration": single_line_monospace_registration,
        "fixture_provenance": fixture_provenance,
        "rendered_text_ink": rendered_text_ink,
        "memory_injection": memory_injection,
        "package": package_facts,
        "invariants": {
            "direct_pptx_without_svg": not list(run.rglob("*.svg")),
            "model_calls_complete": all(
                item is not None and item.get("status") in {"complete", "fixture"}
                and item.get("fallback_used") is False for item in model_calls.values()),
            "fixture_replay_verified": not fixture_stages or bool(fixture_provenance),
            "not_single_screenshot": not single_screenshot,
            "manifest_hashes_verified": not hash_mismatches,
            "provenance_coverage": f"{len(traced_ids)}/{len(scene_ids)}",
            "exact_editable_text": f"{len(expected_text) - len(text_mismatches)}/{len(expected_text)}",
            "visible_native_text": f"{native_text_visibility['assessed'] - len(native_text_visibility['failures'])}/{native_text_visibility['assessed']}",
            "all_native_elements_editable": not native_not_editable,
            "directional_text_rotations": f"{len(directional_text) - len(directional_rotation_mismatches)}/{len(directional_text)}",
            "arrowhead_ooxml_exports": f"{len(arrow_elements) - len(arrowhead_mismatches)}/{len(arrow_elements)}",
            "styled_text_run_exports": f"{len(styled_text_elements) - len(styled_text_mismatches)}/{len(styled_text_elements)}",
            "all_assets_source_grounded": not bad_assets,
            "cjk_text_elements": len(cjk_elements),
            "localized_refinement_decisions": len(decisions) - len(malformed_decisions),
            "owner_bound_refinement_regions": sum(
                item["decision"].get("evaluation_region_source") in {"selected_difference_tile", "affected_scene_owners"}
                for item in decisions if item not in malformed_decisions
            ),
            "verified_refinement_regions": f"{len(decisions) - len(malformed_decisions) - len(region_alignment_failures)}/{len(decisions)}",
            "text_registration_audited": bool(registration) and not any(item["code"].startswith("text-registration-") for item in failures),
            "source_line_registration_audited": bool(source_line_registration) and not any(item["code"].startswith("source-line-registration-") for item in failures),
            "native_shadow_registration_audited": bool(native_shadow_registration) and not any(item["code"].startswith("native-shadow-registration-") for item in failures),
            "short_text_registration_audited": bool(short_text_registration) and not any(item["code"].startswith("short-text-registration-") for item in failures),
            "post_short_text_registration_audited": bool(post_short_text_registration) and not any(item["code"].startswith("post-short-text-registration-") for item in failures),
            "flat_panel_registration_audited": bool(flat_panel_registration) and not any(item["code"].startswith("flat-panel-registration-") for item in failures),
            "rect_fill_registration_audited": bool(rect_fill_registration) and not any(item["code"].startswith("rect-fill-registration-") for item in failures),
            "styled_multiline_registration_audited": bool(styled_multiline_registration) and not any(item["code"].startswith("styled-multiline-registration-") for item in failures),
            "text_tracking_registration_audited": bool(text_tracking_registration) and not any(item["code"].startswith("text-tracking-registration-") for item in failures),
            "post_tracking_text_registration_audited": bool(post_tracking_text_registration) and not any(item["code"].startswith("post-tracking-text-registration-") for item in failures),
            "joint_shadow_text_registration_audited": bool(joint_shadow_text_registration) and not any(item["code"].startswith("joint-shadow-text-registration-") for item in failures),
            "native_text_style_registration_audited": bool(native_text_style_registration) and not any(item["code"].startswith("native-text-style-registration-") for item in failures),
            "single_line_monospace_registration_audited": bool(single_line_monospace_registration) and not any(item["code"].startswith("single-line-monospace-registration-") for item in failures),
        },
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--write", action="store_true", help="Write sample-NNN-audit.json files and batch-audit.json.")
    args = parser.parse_args()
    batch = args.batch.resolve()
    identity = read_json(batch / "training-identity.json")
    manifest_samples = read_json(Path(identity["manifest"]))["samples"]
    if identity.get("sample_indices"):
        indexed_samples = [(index, manifest_samples[index - 1]) for index in identity["sample_indices"]]
    else:
        indexed_samples = list(enumerate(manifest_samples[: identity["requested_samples"]], 1))
    audits = []
    for index, sample in indexed_samples:
        run = batch / "runs" / sample["sample_id"]
        if not (run / "status.json").is_file():
            continue
        status = read_json(run / "status.json")
        if status.get("status") not in TERMINAL_SUCCESS:
            continue
        audit = audit_run(run)
        audit["sample_index"] = index
        audits.append(audit)
        if args.write:
            (batch / f"sample-{index:03d}-audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n")
    summary = {
        "schema_version": "decomposer.batch-audit.v1",
        "requested": identity["requested_samples"],
        "audited": len(audits),
        "passed": sum(item["passed"] for item in audits),
        "failed": sum(not item["passed"] for item in audits),
        "complete": len(audits) == identity["requested_samples"] and all(item["passed"] for item in audits),
        "failed_samples": [item["sample_id"] for item in audits if not item["passed"]],
        "samples": [{"sample_index": item["sample_index"], "sample_id": item["sample_id"], "passed": item["passed"]} for item in audits],
    }
    pixel = [item["visual"]["metrics"]["pixel_similarity"] for item in audits if item.get("visual", {}).get("metrics")]
    edge = [item["visual"]["metrics"]["edge_iou"] for item in audits if item.get("visual", {}).get("metrics")]
    summary["quality_distribution"] = {
        "pixel_similarity": {
            "minimum": min(pixel, default=None), "p10": percentile(pixel, .10),
            "median": percentile(pixel, .50), "mean": round(statistics.mean(pixel), 6) if pixel else None,
            "maximum": max(pixel, default=None),
        },
        "edge_iou": {
            "minimum": min(edge, default=None), "p10": percentile(edge, .10),
            "median": percentile(edge, .50), "mean": round(statistics.mean(edge), 6) if edge else None,
            "maximum": max(edge, default=None),
        },
    }
    summary["batch_counts"] = {
        "degraded": sum(item.get("degraded", False) for item in audits),
        "localized_rasters": sum(item.get("counts", {}).get("localized_rasters", 0) for item in audits),
        "cjk_text_elements": sum(item.get("invariants", {}).get("cjk_text_elements", 0) for item in audits),
        "localized_refinement_decisions": sum(item.get("invariants", {}).get("localized_refinement_decisions", 0) for item in audits),
        "owner_bound_refinement_regions": sum(item.get("invariants", {}).get("owner_bound_refinement_regions", 0) for item in audits),
        "verified_refinement_regions": sum(
            int(str(item.get("invariants", {}).get("verified_refinement_regions", "0/0")).split("/")[0])
            for item in audits
        ),
        "directional_text_elements": sum(
            int(str(item.get("invariants", {}).get("directional_text_rotations", "0/0")).split("/")[-1])
            for item in audits
        ),
        "arrowhead_elements": sum(
            int(str(item.get("invariants", {}).get("arrowhead_ooxml_exports", "0/0")).split("/")[-1])
            for item in audits
        ),
        "styled_text_elements": sum(
            int(str(item.get("invariants", {}).get("styled_text_run_exports", "0/0")).split("/")[-1])
            for item in audits
        ),
        "accepted_refinement_rounds": sum(item.get("refinement", {}).get("accepted_rounds", 0) for item in audits),
    }
    memories = [read_json(path) for path in (batch / "knowledge" / "memory").glob("*.json")]
    summary["knowledge"] = {
        "memory_count": len(memories),
        "evidence_count": len(list((batch / "knowledge" / "evidence").glob("*.json"))),
        "skill_count": len(list((batch / "knowledge" / "skills").glob("*/SKILL.md"))),
        "status_counts": dict(sorted(Counter(item.get("status", "unknown") for item in memories).items())),
        "stage_counts": dict(sorted(Counter(item.get("owner_stage", "unknown") for item in memories).items())),
    }
    if args.write:
        (batch / "batch-audit.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
