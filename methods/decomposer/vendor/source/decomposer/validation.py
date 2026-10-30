from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from PIL import Image, ImageDraw

from .common import read_json, sha256, write_json
from .quality import discover
from .rendered_text_ink import assess as assess_rendered_text_ink
from .text_visibility import assess as assess_text_visibility


def _text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


_EAST_ASIAN_TEXT = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")


def _script_font_route_mismatches(scene: dict[str, Any], compiler: dict[str, Any]) -> list[dict[str, Any]]:
    """Require an explicit CJK-capable compiler route for every East Asian text primitive."""
    expected = {
        item["id"] for item in scene.get("elements", [])
        if item.get("kind") == "text" and _EAST_ASIAN_TEXT.search(str(item.get("text") or ""))
    }
    routes = {
        item.get("id"): item for item in compiler.get("script_font_routes", [])
        if item.get("script") == "cjk"
    }
    mismatches = []
    for element_id in sorted(expected):
        route = routes.get(element_id)
        if not route or route.get("resolved_font") != "Arial Unicode MS":
            mismatches.append({"element_id": element_id, "route": route})
    return mismatches


def _cjk_render_environment_mismatches(scene: dict[str, Any], report: dict[str, Any], runtime: dict[str, Any]) -> list[dict[str, Any]]:
    """Require the bundled headless renderer to expose a writable Fontconfig cache."""
    element_ids = sorted(
        item["id"] for item in scene.get("elements", [])
        if item.get("kind") == "text" and _EAST_ASIAN_TEXT.search(str(item.get("text") or ""))
    )
    render = report.get("render", {})
    soffice = str(runtime.get("soffice") or "")
    uses_bundle = "LibreOfficeDev.app/Contents/MacOS" in soffice
    if not element_ids or render.get("status") != "ready" or not uses_bundle:
        return []
    environment = render.get("font_environment") or {}
    if environment.get("mode") == "bundled-fontconfig" and environment.get("configured") is True:
        return []
    return [{"element_ids": element_ids, "font_environment": environment}]


def _editable_text_mismatches(recognition: dict[str, Any], scene: dict[str, Any]) -> list[dict[str, str]]:
    """Require exact editable text for every non-equation object that declares text."""
    missing = []
    for item in recognition["objects"]:
        if item["category"] == "equation" or not _text(item.get("text")):
            continue
        rendered = "".join(
            str(element.get("text", "")) + ("\n" if element.get("line_break_after") is True else "")
            for element in scene["elements"]
            if element["source_label_id"] == item["id"] and element["kind"] == "text" and element.get("editable", False)
        )
        if rendered != str(item["text"]):
            missing.append({"label_id": item["id"], "expected": item["text"], "actual": rendered})
    return missing


def _arrowhead_export_mismatches(scene: dict[str, Any], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify requested scene arrowheads against serialized DrawingML endpoints."""
    by_element: dict[str, list[dict[str, Any]]] = {}
    for label in provenance.get("labels", {}).values():
        for element in label.get("scene_elements", []):
            uid = element.get("id")
            if uid:
                by_element[uid] = [
                    item for item in label.get("ooxml_shapes", [])
                    if item.get("name") == uid or str(item.get("name", "")).startswith(uid + ".segment")
                ]
    missing = []
    for element in scene.get("elements", []):
        if element.get("kind") not in {"line", "polyline", "path"}:
            continue
        expected_start = bool(element.get("arrow_start"))
        expected_end = bool(element.get("arrow_end"))
        if not expected_start and not expected_end:
            continue
        records = by_element.get(element["id"], [])
        actual_start = any(item.get("head_end") not in {None, "none"} for item in records)
        actual_end = any(item.get("tail_end") not in {None, "none"} for item in records)
        if (expected_start and not actual_start) or (expected_end and not actual_end):
            missing.append({
                "label_id": element["source_label_id"], "element_id": element["id"],
                "expected": {"arrow_start": expected_start, "arrow_end": expected_end},
                "actual": {"arrow_start": actual_start, "arrow_end": actual_end},
                "ooxml_shapes": records,
            })
    return missing


def _styled_text_run_mismatches(scene: dict[str, Any], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify styled-run source identity, visible OOXML text, and native script baselines."""
    records = {
        item.get("name"): item
        for label in provenance.get("labels", {}).values()
        for item in label.get("ooxml_shapes", [])
        if item.get("name")
    }
    mismatches = []
    for element in scene.get("elements", []):
        runs = element.get("runs")
        if element.get("kind") != "text" or not isinstance(runs, list) or not runs:
            continue
        source = "".join(str(item.get("source_text", item.get("text", ""))) for item in runs)
        visible = "".join(str(item.get("text", "")) for item in runs)
        record = records.get(element["id"], {})
        baselines = record.get("run_baselines", [])
        expected_subscripts = sum(item.get("script") == "subscript" and bool(item.get("text")) for item in runs)
        expected_superscripts = sum(item.get("script") == "superscript" and bool(item.get("text")) for item in runs)
        actual_subscripts = sum(number < 0 for number in baselines)
        actual_superscripts = sum(number > 0 for number in baselines)
        if (
            source != str(element.get("text", ""))
            or record.get("text") != visible
            or actual_subscripts < expected_subscripts
            or actual_superscripts < expected_superscripts
        ):
            mismatches.append({
                "element_id": element["id"], "expected_source_text": element.get("text"),
                "run_source_text": source, "expected_visible_text": visible,
                "ooxml_visible_text": record.get("text"),
                "expected_subscripts": expected_subscripts, "actual_subscripts": actual_subscripts,
                "expected_superscripts": expected_superscripts, "actual_superscripts": actual_superscripts,
            })
    return mismatches


def _contact_sheet(reference: Path, preview: Path, difference: Path, target: Path) -> None:
    sources = [Image.open(path).convert("RGB") for path in (reference, preview, difference)]
    try:
        width, height = sources[0].size
        sheet = Image.new("RGB", (width * 3, height + 32), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (image, title) in enumerate(zip(sources, ("SOURCE", "PPTX RENDER", "DIFFERENCE"))):
            sheet.paste(image.resize((width, height)), (index * width, 32))
            draw.text((index * width + 8, 8), title, fill="black")
        sheet.save(target)
    finally:
        for image in sources:
            image.close()


def _manifest(root: Path, image: Path, recognition: dict, representation: dict, layout: dict, scene: dict, provenance: dict) -> dict:
    output = root / "output"
    text_inventory = [
        {"label_id": item["id"], "category": item["category"], "text": item.get("text", ""), "bbox": item["bbox"]}
        for item in recognition["objects"] if _text(item.get("text"))
    ]
    visual_inventory = [
        {"label_id": item["id"], "category": item["category"], "bbox": item["bbox"], "candidate_ids": item.get("candidate_ids", [])}
        for item in recognition["objects"] if item["category"] not in {"text", "equation"}
    ]
    assets = []
    for asset_id, item in representation.get("assets", {}).items():
        path = Path(item["path"])
        assets.append({
            "asset_id": asset_id, "path": str(path), "sha256": sha256(path),
            "source_label_id": item.get("source_label_id"), "source_candidate_id": item.get("source_candidate_id"),
            "source_basis": item.get("source_basis"), "source_bbox": item.get("source_bbox"), "mode": item.get("mode"),
        })
    canonical = {}
    for name, relative in {
        "recognition": "recognition/recognition.json", "representation": "representation/representation.json",
        "layout": "layout/layout.json", "scene": "output/scene.json", "provenance": "output/provenance.json",
    }.items():
        canonical[name] = {"path": relative, "sha256": sha256(root / relative)}
    return {
        "schema_version": "decomposer.page-manifest.v1",
        "source": {"path": str(image.resolve()), "sha256": sha256(image), "width": scene["canvas"]["width"], "height": scene["canvas"]["height"]},
        "canonical": canonical,
        "text_inventory": text_inventory,
        "visual_inventory": visual_inventory,
        "asset_provenance": assets,
        "build": {"pptx": "output/editable.pptx", "pptx_sha256": sha256(output / "editable.pptx"), "compiler": "deterministic-pptx-native"},
        "trace": {"label_count": len(provenance.get("labels", {})), "scene_element_count": len(scene["elements"])},
    }


def _covered_logical_groups(recognition, representation, layout, scene):
    """Nonpainting groups are covered only through all their visible painted members."""
    objects = {item["id"]: item for item in recognition["objects"]}
    visible = {item["label_id"] for item in layout["placements"] if item["visible"]}
    painted = {item["source_label_id"] for item in scene["elements"]}
    pending = {item["label_id"]: item for item in representation["components"]
               if item.get("group_only") is True}
    covered = set()
    for _ in range(len(pending)):
        added = set()
        for label, component in pending.items():
            owner = objects.get(label, {})
            members = component.get("members", [])
            if (label in visible and label not in painted and not component.get("primitives")
                    and owner.get("semantic_level") == "container" and not str(owner.get("text") or "").strip()
                    and members and len(members) == len(set(members)) and label not in members
                    and set(members) <= visible and set(members) <= painted | covered):
                added.add(label)
        if not added - covered:
            break
        covered |= added
    return covered


def _grounded_asset(asset, recognition, layout, scene, image, config):
    """Replay the approved transform and require exact pixels plus native label replacement."""
    if not asset.get("source_candidate_id") or asset.get("mode") != "source_crop":
        return False
    if asset.get("source_basis") == "observed_photo_native_label_boundary":
        from .photo_label_contract import verify
        return verify(asset,recognition,layout,scene,image,config)
    if asset.get("source_basis") == "observed_atomic_activation_field":
        from .fidelity_contracts import verify_activation_asset
        return verify_activation_asset(asset,recognition,layout,scene,image)
    if asset.get("source_basis") == "observed_atomic_visible_photo":
        from .render_contracts import verify_atomic_asset
        return verify_atomic_asset(asset, recognition, layout, scene, image, config)
    if asset.get("source_basis") in {"sam_candidate", "recognition_bbox"}:
        return True
    if asset.get("source_basis") == "recognition_atomic_photo_with_owned_overlays":
        try:
            from .asset_policy import decide_with_photo_overlays
            from .asset_transforms import transformed_crop
            path=Path(asset["path"])
            source_root=next((p for p in path.parents if (p/"input-normalized.png").is_file()
                              and (p/"observations/evidence-ir.json").is_file()
                              and sha256(p/"input-normalized.png")==sha256(image)),None)
            if source_root is None or sha256(path)!=asset.get("sha256"):return False
            replay=decide_with_photo_overlays(recognition,read_json(source_root/"observations/evidence-ir.json"),config,image)
            approved=next((d for d in replay["decisions"] if d["label_id"]==asset["source_label_id"]),{})
            if any(approved.get(k)!=asset.get(v) for k,v in [("source_basis","source_basis"),("source_candidate_id","source_candidate_id"),("approved_bbox","source_bbox"),("asset_transforms","asset_transforms")]):return False
            if approved.get("decision")!="localized_raster" or not approved.get("asset_transforms"):return False
            replacements={t["label_id"] for t in asset["asset_transforms"]}
            painted={e["source_label_id"] for e in scene["elements"] if e.get("editable") or e["kind"]=="image" and e.get("asset_id") in scene["assets"]}
            visible={p["label_id"] for p in layout["placements"] if p["visible"]}
            if not replacements<=painted&visible:return False
            image_indices=[i for i,e in enumerate(scene["elements"]) if e.get("asset_id") and scene["assets"].get(e["asset_id"],{}).get("source_label_id")==asset["source_label_id"]]
            for label in replacements:
                if image_indices and min(i for i,e in enumerate(scene["elements"]) if e["source_label_id"]==label)<=max(image_indices):return False
            with Image.open(image) as original,Image.open(path) as actual:
                expected=transformed_crop(original,asset["source_bbox"],asset["asset_transforms"])
                return expected.size==actual.size and expected.tobytes()==actual.convert("RGBA").tobytes()
        except (OSError,ValueError,TypeError,KeyError,StopIteration):return False
    if asset.get("source_basis") != "recognition_bbox_with_verified_white_native_label_patches":
        return False
    try:
        from .asset_policy import decide_with_photo_overlays
        path = Path(asset["path"])
        if path.parent.name != "assets" or path.parent.parent.name != "representation":
            return False
        source_root = next((parent for parent in path.parents
                            if (parent / "input-normalized.png").is_file()
                            and (parent / "observations" / "evidence-ir.json").is_file()
                            and sha256(parent / "input-normalized.png") == sha256(image)), None)
        if sha256(path) != asset.get("sha256") or source_root is None:
            return False
        evidence = read_json(source_root / "observations" / "evidence-ir.json")
        replay = decide_with_photo_overlays(recognition, evidence, config, image)
        approved = next((item for item in replay["decisions"] if item["label_id"] == asset["source_label_id"]), {})
        if (approved.get("decision") != "localized_raster" or approved.get("source_basis") != asset["source_basis"]
                or approved.get("source_candidate_id") != asset["source_candidate_id"]
                or approved.get("approved_bbox") != asset.get("source_bbox")
                or approved.get("native_text_exclusions") != asset.get("native_text_exclusions")
                or not asset.get("native_text_exclusions")):
            return False
        native_labels = {item["source_label_id"] for item in scene["elements"]
                         if item["kind"] == "text" and item.get("editable")}
        visible = {item["label_id"] for item in layout["placements"] if item["visible"]}
        exclusions = asset["native_text_exclusions"]
        if not {item["label_id"] for item in exclusions} <= native_labels & visible:
            return False
        x, y, w, h = asset["source_bbox"]
        bounds = [int(x), int(y), max(int(x + w + .999), int(x) + 1), max(int(y + h + .999), int(y) + 1)]
        with Image.open(image) as original, Image.open(path) as actual:
            source = original.convert("RGBA")
            expected = source.crop(bounds)
            for exclusion in exclusions:
                a, b, c, d = exclusion["bbox"]
                if exclusion.get("fill") != "#FFFFFF" or a < x or b < y or a+c > x+w or b+d > y+h or c*d/(w*h) > .06:
                    return False
                border = [source.getpixel((i,j))[:3] for i in range(a,a+c) for j in range(b,b+d)
                          if i in (a,a+c-1) or j in (b,b+d-1)]
                if not border or sum(min(pixel) >= 240 for pixel in border)/len(border) < .98:
                    return False
                ImageDraw.Draw(expected).rectangle([a-bounds[0], b-bounds[1], a+c-bounds[0]-1, b+d-bounds[1]-1], fill="white")
            if sum(e["bbox"][2]*e["bbox"][3] for e in exclusions)/(w*h) > .08:
                return False
            return expected.size == actual.size and expected.tobytes() == actual.convert("RGBA").tobytes()
    except (OSError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
        return False


def validate_run(root: str | Path, image: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    root, image = Path(root), Path(image)
    output = root / "output"
    recognition = read_json(root / "recognition" / "recognition.json")
    representation = read_json(root / "representation" / "representation.json")
    layout = read_json(root / "layout" / "layout.json")
    scene = read_json(output / "scene.json")
    provenance = read_json(output / "provenance.json")
    report = read_json(output / "report.json")
    manifest = _manifest(root, image, recognition, representation, layout, scene, provenance)
    write_json(output / "page-manifest.json", manifest)

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    def check(name: str, passed: bool, *, code: str, owner_stage: str, label_ids: list[str] | None = None, detail: Any = None) -> None:
        checks[name] = {"passed": bool(passed), "detail": detail}
        if not passed:
            failures.append({"code": code, "owner_stage": owner_stage, "label_ids": label_ids or [], "detail": detail})

    compiler_fallbacks = report["compiler"].get("fallbacks", [])
    check("pptx_package", bool(report["structure"].get("xml_valid")) and report["structure"].get("slide_count") == 1,
          code="invalid-pptx-package", owner_stage="compiler", detail=report["structure"])
    check("not_single_screenshot", not report["structure"].get("is_single_screenshot_like", False),
          code="single-screenshot-output", owner_stage="compiler", detail=report["structure"])
    check("compiler_fallbacks", not compiler_fallbacks, code="compiler-fallback-present", owner_stage="compiler",
          label_ids=sorted({str(item.get("id", "")).split("/", 1)[0] for item in compiler_fallbacks}), detail=compiler_fallbacks)
    script_font_mismatches = _script_font_route_mismatches(scene, report["compiler"])
    check("script_font_routing", not script_font_mismatches, code="missing-cjk-font-route", owner_stage="compiler",
          label_ids=sorted({item["element_id"].split("/", 1)[0] for item in script_font_mismatches}), detail=script_font_mismatches)
    cjk_environment_mismatches = _cjk_render_environment_mismatches(scene, report, config.get("runtime", {}))
    check("cjk_render_font_environment", not cjk_environment_mismatches, code="missing-cjk-render-font-environment", owner_stage="compiler",
          label_ids=sorted({element_id.split("/", 1)[0] for item in cjk_environment_mismatches for element_id in item["element_ids"]}),
          detail=cjk_environment_mismatches)
    check("raster_safety", report["raster_audit"].get("max_image_area_ratio", 1) < .85 and report["raster_audit"].get("summed_image_area_ratio", 1) < .90,
          code="raster-safety-violation", owner_stage="representation", detail=report["raster_audit"])

    scene_ids = {item["id"] for item in scene["elements"]}
    traced_ids = set()
    for label in provenance.get("labels", {}).values():
        shape_names = {item.get("name") for item in label.get("ooxml_shapes", [])}
        for item in label.get("scene_elements", []):
            if item["id"] in shape_names or any(str(name).startswith(item["id"] + ".segment") for name in shape_names):
                traced_ids.add(item["id"])
    check("provenance_coverage", scene_ids == traced_ids, code="incomplete-ooxml-provenance", owner_stage="compiler",
          label_ids=sorted({item["source_label_id"] for item in scene["elements"] if item["id"] not in traced_ids}),
          detail={"scene": len(scene_ids), "traced": len(traced_ids), "missing": sorted(scene_ids - traced_ids)})

    missing_arrowheads = _arrowhead_export_mismatches(scene, provenance)
    check("arrowhead_export_coverage", not missing_arrowheads, code="arrowhead-export-mismatch", owner_stage="compiler",
          label_ids=sorted({item["label_id"] for item in missing_arrowheads}), detail=missing_arrowheads)

    styled_text_mismatches = _styled_text_run_mismatches(scene, provenance)
    check("styled_text_run_export", not styled_text_mismatches, code="styled-text-run-export-mismatch", owner_stage="compiler",
          label_ids=sorted({item["element_id"].split("/", 1)[0] for item in styled_text_mismatches}), detail=styled_text_mismatches)

    visible = {item["label_id"] for item in layout["placements"] if item["visible"]}
    scene_labels = {item["source_label_id"] for item in scene["elements"]}
    logical_groups = _covered_logical_groups(recognition, representation, layout, scene)
    covered_labels = scene_labels | logical_groups
    check("visible_label_coverage", visible == covered_labels, code="visible-label-missing-from-scene", owner_stage="representation",
          label_ids=sorted(visible - covered_labels), detail={"visible": len(visible), "scene": len(scene_labels),
                                                          "logical_groups": sorted(logical_groups)})

    missing_text = _editable_text_mismatches(recognition, scene)
    check("editable_text_coverage", not missing_text, code="editable-text-coverage-mismatch", owner_stage="representation",
          label_ids=[item["label_id"] for item in missing_text], detail=missing_text)

    invisible_native_text = assess_text_visibility(recognition, scene)
    check("native_text_visibility", not invisible_native_text["failures"],
          code="native-text-invisible", owner_stage="representation",
          label_ids=sorted({label for item in invisible_native_text["failures"]
                            for label in item["affected_label_ids"]}),
          detail=invisible_native_text)

    noneditable_native = [item["id"] for item in scene["elements"] if item["kind"] != "image" and not item.get("editable", False)]
    check("native_element_editability", not noneditable_native, code="native-element-not-editable", owner_stage="representation",
          label_ids=sorted({item.split("/", 1)[0] for item in noneditable_native}), detail=noneditable_native)

    ungrounded_assets = [
        item for item in manifest["asset_provenance"]
        if not _grounded_asset(representation["assets"].get(item["asset_id"], {}),
                               recognition, layout, scene, image, config)
    ]
    check("asset_provenance", not ungrounded_assets, code="ungrounded-raster-asset", owner_stage="representation",
          label_ids=sorted({item.get("source_label_id") for item in ungrounded_assets if item.get("source_label_id")}), detail=ungrounded_assets)

    for item in recognition["objects"]:
        if item["category"] == "equation":
            warnings.append({"code": "formula-not-native-office-math", "owner_stage": "representation", "label_ids": [item["id"]],
                             "detail": "Formula is represented by editable text primitives; native OMML is not yet implemented."})

    visual = {"status": "skipped", "reason": "render_unavailable"}
    if (output / "preview.png").is_file():
        ink = assess_rendered_text_ink(scene, image, output / "preview.png")
        check("rendered_native_text_ink", not ink["failures"],
              code="rendered-native-text-ink-collapse", owner_stage="compiler",
              label_ids=sorted({item["source_label_id"] for item in ink["failures"]}), detail=ink)
        diagnostics = discover(image, output / "preview.png", scene, output / "validation-assets")
        _contact_sheet(image, output / "preview.png", Path(diagnostics["overlay"]), output / "contact-sheet.png")
        thresholds = config.get("validation", {})
        minimum_pixel = float(thresholds.get("minimum_pixel_similarity", .94))
        minimum_edge = float(thresholds.get("minimum_edge_iou", .20))
        global_metrics = diagnostics["global"]
        visual_passed = global_metrics["pixel_similarity"] >= minimum_pixel and (global_metrics["edge_iou"] or 0) >= minimum_edge
        visual = {"status": "passed" if visual_passed else "failed", "metrics": global_metrics,
                  "thresholds": {"minimum_pixel_similarity": minimum_pixel, "minimum_edge_iou": minimum_edge},
                  "candidates": diagnostics["candidates"]}
        check("visual_threshold", visual_passed, code="visual-quality-below-threshold", owner_stage="unresolved",
              label_ids=diagnostics["candidates"][0].get("label_ids", []) if diagnostics["candidates"] else [], detail=visual)

    result = {
        "schema_version": "decomposer.validation.v1", "passed": not failures,
        "checks": checks, "visual": visual, "failures": failures, "warnings": warnings,
        "artifacts": {"manifest": "output/page-manifest.json", "contact_sheet": "output/contact-sheet.png" if (output / "contact-sheet.png").exists() else None,
                      "difference_overlay": "output/validation-assets/difference-overlay.png" if (output / "validation-assets/difference-overlay.png").exists() else None},
    }
    write_json(output / "validation.json", result)
    return result
