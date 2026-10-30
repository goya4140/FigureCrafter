from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from decomposer.common import sha256, style as canonical_style, write_json
from decomposer import VERSION
from decomposer.pipeline import _model_stage_reusable, _require_model_stage, execute
from decomposer.evidence import build as build_evidence, dispositions
from decomposer.recognition import canonicalize as canonicalize_recognition, run as run_recognition
from decomposer.representation import _approved_raster_text_primitives, canonicalize as canonicalize_representation, run as run_representation
from decomposer.layout import canonicalize as canonicalize_layout, default_layout, run as run_layout
from decomposer.asset_policy import decide as decide_assets
from decomposer.assemble import assemble
from decomposer.pptx import _libreoffice_font_environment, compile_scene
from decomposer.provenance import _shape_records
from decomposer.validation import _arrowhead_export_mismatches, _cjk_render_environment_mismatches, _editable_text_mismatches, _script_font_route_mismatches, _styled_text_run_mismatches
from decomposer.text_visibility import assess as assess_text_visibility
from decomposer.schemas import RECOGNITION_PROPOSAL_SCHEMA, REPRESENTATION_PROPOSAL_SCHEMA, LAYOUT_PROPOSAL_SCHEMA, REFINEMENT_SCHEMA


class PipelineTest(unittest.TestCase):
    def test_large_canvas_fits_powerpoint_page_and_keeps_right_text_position(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        with tempfile.TemporaryDirectory(prefix="decomposer-large-page-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 11520, "height": 5459, "background": "#FFFFFF"},
                "elements": [{"id": "right/text", "source_label_id": "right", "category": "text",
                              "kind": "text", "bbox": [10582, 134, 503, 302], "rotation_deg": 0,
                              "editable": True, "style": canonical_style({"font_size": 100}, text=True),
                              "text": "Right label"}],
                "z_order": ["right/text"], "assets": {}, "degraded": False,
            }
            scene_path = root / "scene.json"
            write_json(scene_path, scene)
            compile_scene(scene_path, root / "output", {"runtime": {"node": "node"}}, render_output=False)
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
                slide = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                  "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            width = int(presentation.find("p:sldSz", ns).attrib["cx"])
            x = int(slide.find(".//p:sp/p:spPr/a:xfrm/a:off", ns).attrib["x"])
            self.assertLessEqual(width, 50 * 914400)
            self.assertGreater(x, width * .85)

    def test_native_text_shadow_preserves_editable_text_and_ooXML_effect(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        shadow_style = canonical_style({"font_size": 30, "shadow": {
            "type": "outer", "color": "#666666", "opacity": .35,
            "blur": 1, "offset": 2, "angle": 45,
        }}, text=True)
        self.assertEqual(shadow_style["shadow"]["color"], "666666")
        with tempfile.TemporaryDirectory(prefix="decomposer-text-shadow-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 200, "height": 80, "background": "#FFFFFF"},
                "elements": [{"id": "title/text", "source_label_id": "title", "category": "text", "kind": "text",
                              "bbox": [10, 10, 180, 45], "rotation_deg": 0, "editable": True,
                              "style": shadow_style, "text": "Native title"}],
                "z_order": ["title/text"], "assets": {}, "degraded": False,
            }
            scene_path = root / "scene.json"; write_json(scene_path, scene)
            report = compile_scene(scene_path, root / "output", {"runtime": {"node": "node"}}, render_output=False)
            self.assertEqual(report["compiler"]["fallbacks"], [])
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                  "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            shape = slide_root.find(".//p:sp", ns)
            self.assertIsNotNone(shape)
            self.assertEqual(len(shape.findall(".//a:outerShdw", ns)), 1)
            self.assertEqual("".join(item.text or "" for item in shape.findall(".//a:t", ns)), "Native title")

    def test_native_text_explicit_line_spacing_survives_canonicalization_and_export(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        explicit = canonical_style({"font_size": 9.5, "line_spacing": 11.5}, text=True)
        default = canonical_style({"font_size": 9.5}, text=True)
        self.assertEqual(explicit["line_spacing"], 11.5)
        self.assertNotIn("line_spacing", default)
        assembled = assemble(
            {"canvas": {"width": 200, "height": 100}, "background": "#FFFFFF", "degraded": False},
            {"components": [{"label_id": "explicit", "category": "text", "width": 180, "height": 35,
                             "primitives": [{"id": "text", "kind": "text", "bbox": [0, 0, 180, 35],
                                             "style": explicit, "text": "one\ntwo", "editable": True}],
                             "primitive_order": ["text"]}], "assets": {}, "degraded": False},
            {"placements": [{"label_id": "explicit", "bbox": [10, 10, 180, 35], "rotation_deg": 0,
                             "font_scale": 2, "visible": True}], "component_order": ["explicit"], "degraded": False},
        )
        self.assertEqual(assembled["elements"][0]["style"]["line_spacing"], 23.0)
        with tempfile.TemporaryDirectory(prefix="decomposer-native-leading-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 200, "height": 100, "background": "#FFFFFF"},
                "elements": [
                    {"id": "explicit/text", "source_label_id": "explicit", "category": "text", "kind": "text",
                     "bbox": [10, 10, 180, 35], "rotation_deg": 0, "editable": True,
                     "style": explicit, "text": "one\ntwo"},
                    {"id": "default/text", "source_label_id": "default", "category": "text", "kind": "text",
                     "bbox": [10, 55, 180, 35], "rotation_deg": 0, "editable": True,
                     "style": default, "text": "three\nfour"},
                ],
                "z_order": ["explicit/text", "default/text"], "assets": {}, "degraded": False,
            }
            scene_path = root / "scene.json"; write_json(scene_path, scene)
            report = compile_scene(scene_path, root / "output", {"runtime": {"node": "node"}}, render_output=False)
            self.assertEqual(report["compiler"]["fallbacks"], [])
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                  "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            shapes = slide_root.findall(".//p:sp", ns)
            self.assertEqual(len(shapes), 2)
            spacings = [[int(item.attrib["val"]) for item in shape.findall(".//a:spcPts", ns)] for shape in shapes]
            self.assertTrue(spacings[0] and all(value == 1150 for value in spacings[0]))
            self.assertTrue(spacings[1] and all(abs(value - 808) <= 1 for value in spacings[1]))

    def test_relative_line_spacing_is_exported_as_font_relative_points(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        with tempfile.TemporaryDirectory(prefix="decomposer-relative-leading-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 240, "height": 100, "background": "#FFFFFF"},
                "elements": [{
                    "id": "prompt/text", "source_label_id": "prompt", "category": "text",
                    "kind": "text", "bbox": [5, 5, 220, 90], "rotation_deg": 0,
                    "editable": True, "style": canonical_style({
                        "font_size": 40, "line_spacing": 1.2, "color": "#000000",
                    }, text=True), "text": "First line\nSecond line",
                }],
                "z_order": ["prompt/text"], "assets": {}, "degraded": False,
            }
            write_json(root / "scene.json", scene)
            report = compile_scene(root / "scene.json", root / "output",
                                   {"runtime": {"node": "node"}}, render_output=False)
            self.assertEqual(report["compiler"]["fallbacks"], [])
            self.assertEqual(report["compiler"]["line_spacing_caps"], [])
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            drawing = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            spacings = [int(item.attrib["val"]) for item in slide_root.findall(".//a:spcPts", drawing)]
            self.assertEqual(spacings, [3600, 3600])

    def test_tight_multiline_box_caps_leading_without_reducing_native_font_size(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        with tempfile.TemporaryDirectory(prefix="decomposer-tight-leading-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 600, "height": 100, "background": "#FFFFFF"},
                "elements": [{"id": "title/text", "source_label_id": "title", "category": "text",
                              "kind": "text", "bbox": [10, 10, 239, 49], "rotation_deg": 0,
                              "editable": True, "style": canonical_style({"font_size": 22,
                              "line_spacing": 27, "color": "#111111"}, text=True),
                              "text": "Embedding Predictive\nPre-Training:"},
                             {"id": "rotated/text", "source_label_id": "rotated", "category": "text",
                              "kind": "text", "bbox": [300, 10, 239, 49], "rotation_deg": 90,
                              "editable": True, "style": canonical_style({"font_size": 22,
                              "line_spacing": 27, "color": "#111111"}, text=True),
                              "text": "Rotated first\nRotated second"}],
                "z_order": ["title/text", "rotated/text"], "assets": {}, "degraded": False,
            }
            write_json(root / "scene.json", scene)
            report = compile_scene(root / "scene.json", root / "output",
                                   {"runtime": {"node": "node"}}, render_output=False)
            self.assertEqual(report["compiler"]["fallbacks"], [])
            self.assertEqual(report["compiler"]["line_spacing_caps"], [{
                "id": "title/text", "source_label_id": "title",
                "reason": "multiline-owner-box-height",
                "requested_pt": 27, "exported_pt": 20.25,
                "font_size_pt": 16.5, "line_count": 2,
            }])
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            drawing = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            spacings = [int(item.attrib["val"]) for item in slide_root.findall(".//a:spcPts", drawing)]
            sizes = [int(item.attrib["sz"]) for item in slide_root.findall(".//a:rPr", drawing)]
            self.assertEqual(spacings, [2025, 2025, 2700, 2700])
            self.assertEqual(sizes, [1650, 1650, 1650, 1650])

    def test_multiline_cap_scales_with_three_and_twelve_line_owner_boxes(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        with tempfile.TemporaryDirectory(prefix="decomposer-multiline-cap-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 400, "height": 400, "background": "#FFFFFF"},
                "elements": [
                    {"id": "three/text", "source_label_id": "three", "category": "text",
                     "kind": "text", "bbox": [10, 10, 130, 85], "rotation_deg": 0,
                     "editable": True, "style": canonical_style({"font_size": 23,
                     "line_spacing": 31, "color": "#000000"}, text=True),
                     "text": "alpha\nbeta\ngamma"},
                    {"id": "twelve/text", "source_label_id": "twelve", "category": "text",
                     "kind": "text", "bbox": [200, 10, 20, 313], "rotation_deg": 0,
                     "editable": True, "style": canonical_style({"font_size": 14,
                     "line_spacing": 26.8, "color": "#000000"}, text=True),
                     "text": "1\n2\n3\n4\n5a\n5b\n5c\n5d\n6a\n6b\n6c\n6d"},
                ],
                "z_order": ["three/text", "twelve/text"], "assets": {}, "degraded": False,
            }
            write_json(root / "scene.json", scene)
            report = compile_scene(root / "scene.json", root / "output",
                                   {"runtime": {"node": "node"}}, render_output=False)
            self.assertEqual(report["compiler"]["fallbacks"], [])
            self.assertEqual(report["compiler"]["line_spacing_caps"], [
                {"id": "three/text", "source_label_id": "three",
                 "reason": "multiline-owner-box-height", "requested_pt": 31,
                 "exported_pt": 23.25, "font_size_pt": 17.25, "line_count": 3},
                {"id": "twelve/text", "source_label_id": "twelve",
                 "reason": "multiline-owner-box-height", "requested_pt": 26.8,
                 "exported_pt": 20.3864, "font_size_pt": 10.5, "line_count": 12},
            ])
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            drawing = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            shapes = slide_root.findall(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sp")
            self.assertEqual(len(shapes), 2)
            for shape, line_count, spacing, font_size in zip(
                shapes, (3, 12), (2325, 2039), (1725, 1050)
            ):
                exported = [int(item.attrib["val"]) for item in shape.findall(".//a:spcPts", drawing)]
                sizes = [int(item.attrib["sz"]) for item in shape.findall(".//a:rPr", drawing)]
                self.assertEqual(exported, [spacing] * line_count)
                self.assertEqual(sizes, [font_size] * line_count)

    def test_resume_rejects_degraded_model_calls_for_every_generation_stage(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-stage-reuse-") as temporary_name:
            root = Path(temporary_name)
            for stage in ("recognition", "representation", "layout"):
                metadata = root / "calls" / stage / "metadata.json"
                self.assertFalse(_model_stage_reusable(root, stage))
                write_json(metadata, {"stage": stage, "status": "complete", "fallback_used": False})
                self.assertTrue(_model_stage_reusable(root, stage))
                write_json(metadata, {"stage": stage, "status": "fixture", "fallback_used": False})
                self.assertTrue(_model_stage_reusable(root, stage))
                write_json(metadata, {"stage": stage, "status": "degraded", "fallback_used": True})
                self.assertFalse(_model_stage_reusable(root, stage))
                write_json(metadata, {"stage": stage, "status": "complete", "fallback_used": True})
                self.assertFalse(_model_stage_reusable(root, stage))
                write_json(metadata, {"stage": stage, "status": "running", "fallback_used": False})
                self.assertFalse(_model_stage_reusable(root, stage))

    def test_required_model_failure_stops_before_downstream_generation(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-required-model-") as temporary_name:
            root = Path(temporary_name)
            for stage in ("recognition", "representation", "layout"):
                metadata = root / "calls" / stage / "metadata.json"
                write_json(metadata, {"stage": stage, "status": "degraded", "fallback_used": True,
                                      "error": "RuntimeError: model command exited 1"})
                with self.assertRaisesRegex(RuntimeError, f"{stage} model call unavailable"):
                    _require_model_stage(root, stage)
            image = root / "source.png"
            Image.new("RGB", (320, 180), "white").save(image)
            run = root / "run"
            with self.assertRaisesRegex(RuntimeError, "recognition model call unavailable"):
                execute(image, run, {"model": {"command": "/usr/bin/false"}, "detectors": {}},
                        render_output=False)
            self.assertEqual(json.loads((run / "status.json").read_text())["status"], "runtime_failed")
            self.assertFalse((run / "representation" / "representation.json").exists())

    def test_native_shape_shadow_is_canonical_and_exported(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        raw_style = {"fill": "#C6DBE8", "stroke": "none", "shadow": {
            "type": "outer", "color": "#666666", "opacity": .35,
            "blur": 5, "offset": 5, "angle": 225,
        }}
        shape_style = canonical_style(raw_style)
        self.assertEqual(shape_style["shadow"]["color"], "666666")
        with tempfile.TemporaryDirectory(prefix="decomposer-native-shadow-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 200, "height": 100, "background": "#FFFFFF"},
                "elements": [{
                    "id": "tile/rect", "source_label_id": "tile", "category": "shape", "kind": "rect",
                    "bbox": [30, 20, 100, 50], "rotation_deg": 0, "editable": True,
                    "style": shape_style,
                }, {
                    "id": "panel/path", "source_label_id": "panel", "category": "shape", "kind": "path",
                    "bbox": [140, 20, 40, 50], "rotation_deg": 0, "editable": True,
                    "path": "M0 0 L40 0 L40 50 L0 50 Z", "style": shape_style,
                }],
                "z_order": ["tile/rect", "panel/path"], "assets": {}, "degraded": False,
            }
            scene_path = root / "scene.json"; write_json(scene_path, scene)
            report = compile_scene(scene_path, root / "output", {"runtime": {"node": "node", "timeout_seconds": 180}}, render_output=False)
            self.assertEqual(report["compiler"]["fallbacks"], [])
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            drawing = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            self.assertEqual(len(slide_root.findall(".//a:outerShdw", drawing)), 2)
            self.assertEqual(len(slide_root.findall(".//a:blip", drawing)), 0)

    def test_evidence_ir_preserves_hierarchy_and_candidate_dispositions(self):
        raw = {
            "sources": {"sam": {"status": "ready"}, "ocr": {"status": "ready"}},
            "items": [
                {"id": "panel", "parser": "SAM3", "label": "content_box", "bbox": [0, 0, 100, 80], "confidence": .9},
                {"id": "picture", "parser": "SAM3", "label": "picture", "bbox": [10, 10, 40, 30], "confidence": .8},
                {"id": "picture-copy", "parser": "SAM3", "label": "picture", "bbox": [10, 10, 40, 30], "confidence": .7},
                {"id": "text", "parser": "OCR", "label": "text", "bbox": [12, 12, 20, 8], "text": "A", "confidence": .95},
            ],
        }
        evidence = build_evidence(raw, (100, 80))
        self.assertEqual(evidence["summary"]["candidate_count"], 3)
        self.assertEqual(evidence["summary"]["suppressed_count"], 1)
        picture = next(item for item in evidence["candidates"] if item["id"] == "picture")
        self.assertEqual(picture["parent_id"], "panel")
        recognition, _ = canonicalize_recognition({
            "objects": [{"id": "photo", "category": "image", "bbox": [10, 10, 40, 30], "candidate_ids": ["picture", "missing"]}]
        }, (100, 80), evidence)
        self.assertEqual(recognition["objects"][0]["candidate_ids"], ["picture"])
        audit = dispositions(evidence, recognition, {
            "candidate_decisions": [{"candidate_id": "panel", "status": "merged", "reason": "container absorbed by photo object"}]
        })
        self.assertEqual(next(row for row in audit["rows"] if row["candidate_id"] == "picture")["status"], "used")
        self.assertIn("panel", [row["candidate_id"] for row in audit["rows"] if row["status"] == "merged"])

    def test_empty_and_malformed_content_still_canonicalizes(self):
        recognition, report = canonicalize_recognition({"background": "not-a-color", "objects": ["bad"]}, (100, 50), {"items": []})
        self.assertEqual(recognition["background"], "#FFFFFF")
        self.assertEqual(recognition["objects"][0]["id"], "fallback-background")
        self.assertEqual(recognition["objects"][0]["category"], "shape")
        self.assertEqual(report["fallback"], "editable_blank_canvas")

        with tempfile.TemporaryDirectory(prefix="decomposer-canonical-") as temporary_name:
            image = Path(temporary_name) / "source.png"
            Image.new("RGB", (100, 50), "white").save(image)
            representation, _ = canonicalize_representation(
                {"components": [{"label_id": "fallback-background", "width": "bad", "height": -3, "primitives": []}]},
                recognition, image, Path(temporary_name) / "representation",
            )
            self.assertEqual(len(representation["components"]), 1)
            self.assertEqual(representation["components"][0]["primitives"][0]["kind"], "rect")

    def test_empty_path_is_native_rect_before_compilation(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "scad", "category": "shape", "bbox": [10, 10, 56, 80],
        }]}, (100, 100), {"items": []})
        with tempfile.TemporaryDirectory(prefix="decomposer-empty-path-") as temporary_name:
            root = Path(temporary_name)
            source = root / "source.png"
            Image.new("RGB", (100, 100), "white").save(source)
            representation, report = canonicalize_representation({"components": [{
                "label_id": "scad", "width": 56, "height": 80, "primitives": [{
                    "id": "box", "kind": "path", "bbox": [0, 0, 56, 80], "path": "",
                    "style": {"fill": "#CDE5FA", "stroke": "#0077BE"},
                }],
            }]}, recognition, source, root / "representation")
            self.assertEqual(representation["components"][0]["primitives"][0]["kind"], "rect")
            self.assertEqual(report["repairs"][0]["action"], "replaced_invalid_path_with_native_rect")
            scene = assemble(recognition, representation, default_layout(recognition))
            scene_path = root / "scene.json"
            write_json(scene_path, scene)
            compiled = compile_scene(scene_path, root / "output", {"runtime": {"node": "node"}}, render_output=False)
            self.assertEqual(compiled["compiler"]["fallbacks"], [])

    def test_asset_gate_allows_only_small_sam_leaf(self):
        raw = {
            "sources": {"sam": {"status": "ready"}},
            "items": [
                {"id": "whole", "source": "sam3", "label": "picture", "bbox": [0, 0, 100, 100], "confidence": .99},
                {"id": "leaf", "source": "sam3", "label": "picture", "bbox": [10, 10, 20, 20], "confidence": .9},
            ],
        }
        evidence = build_evidence(raw, (100, 100))
        whole = next(item for item in evidence["candidates"] if item["id"] == "whole")
        leaf = next(item for item in evidence["candidates"] if item["id"] == "leaf")
        self.assertTrue(whole["whole_canvas_risk"])
        self.assertFalse(whole["crop_candidate"])
        self.assertTrue(leaf["crop_candidate"])
        recognition, _ = canonicalize_recognition({"objects": [
            {"id": "page", "category": "image", "bbox": [0, 0, 100, 100], "candidate_ids": ["whole"]},
            {"id": "photo", "category": "image", "bbox": [8, 8, 25, 25], "candidate_ids": ["leaf"], "semantic_level": "leaf"},
        ]}, (100, 100), evidence)
        decisions = decide_assets(recognition, evidence, {})
        by_id = {item["label_id"]: item for item in decisions["decisions"]}
        self.assertEqual(by_id["page"]["decision"], "native")
        self.assertEqual(by_id["photo"]["decision"], "localized_raster")
        self.assertEqual(by_id["photo"]["approved_bbox"], leaf["bbox"])
        with tempfile.TemporaryDirectory(prefix="decomposer-approved-raster-") as temporary_name:
            image = Path(temporary_name) / "source.png"
            Image.new("RGB", (100, 100), "white").save(image)
            representation, report = canonicalize_representation({"components": [
                {"label_id": "page", "primitives": [{"id": "native", "kind": "rect", "bbox": [0, 0, 100, 100]}]},
                {"label_id": "photo", "primitives": [{"id": "crop", "kind": "image", "bbox": [0, 0, 25, 25]}]},
            ]}, recognition, image, Path(temporary_name) / "representation", decisions)
            self.assertFalse(representation["degraded"])
            self.assertEqual(report["approved_raster_count"], 1)
            self.assertFalse(representation["components"][1]["primitives"][0]["editable"])

    def test_approved_raster_keeps_only_exact_visible_native_text(self):
        evidence = build_evidence({"sources": {}, "items": []}, (100, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "badge", "category": "image", "bbox": [10, 10, 40, 40],
            "text": "1", "semantic_level": "leaf",
        }]}, (100, 100), evidence)
        decisions = {"decisions": [{"label_id": "badge", "decision": "localized_raster",
                                   "approved_bbox": [10, 10, 40, 40], "source_basis": "recognition_bbox"}]}
        with tempfile.TemporaryDirectory(prefix="decomposer-raster-native-text-") as temporary_name:
            root = Path(temporary_name)
            image = root / "source.png"
            Image.new("RGB", (100, 100), "black").save(image)
            component = {"label_id": "badge", "width": 40, "height": 40, "primitives": [
                {"id": "surrogate", "kind": "ellipse", "bbox": [0, 0, 40, 40]},
                {"id": "number", "kind": "text", "bbox": [8, 3, 24, 34], "text": "1",
                 "style": {"color": "#FFFFFF", "font_size": 30}},
            ]}
            representation, report = canonicalize_representation(
                {"components": [component]}, recognition, image, root / "positive", decisions)
            primitives = representation["components"][0]["primitives"]
            self.assertEqual([item["kind"] for item in primitives], ["image", "text"])
            self.assertEqual(primitives[0]["bbox"], [0.0, 0.0, 40.0, 40.0])
            self.assertEqual(primitives[1]["text"], "1")
            self.assertEqual(primitives[1]["style"]["color"], "#FFFFFF")
            self.assertEqual(report["policy_overrides"][0]["retained_exact_visible_text_primitives"], ["number"])
            layout = default_layout(recognition)
            layout["degraded"] = False
            scene = assemble(recognition, representation, layout)
            self.assertEqual(assess_text_visibility(recognition, scene)["failures"], [])

            component["primitives"][1]["text"] = "2"
            rejected, _ = canonicalize_representation(
                {"components": [component]}, recognition, image, root / "wrong-text", decisions)
            self.assertNotEqual(rejected["components"][0]["primitives"][-1]["text"], "2")
            component["primitives"][1]["text"] = "1"
            component["primitives"][1]["style"]["color"] = "none"
            invisible, _ = canonicalize_representation(
                {"components": [component]}, recognition, image, root / "invisible", decisions)
            self.assertNotEqual(invisible["components"][0]["primitives"][-1]["id"], "number")
            self.assertEqual(_approved_raster_text_primitives({"primitives": [{
                "kind": "text", "text": "12", "style": {"color": "#FFFFFF"}, "runs": [
                    {"text": "1", "style": {"color": "#FFFFFF"}},
                    {"text": "2", "style": {"color": "none"}},
                ],
            }]}, "12"), [])
            self.assertEqual(_approved_raster_text_primitives({"primitives": [{
                "kind": "text", "text": "12", "style": {"color": "#FFFFFF"}, "runs": [
                    {"text": "1", "source_text": "1", "style": {"color": "#FFFFFF"}},
                    {"text": "2", "source_text": "X", "style": {"color": "#FFFFFF"}},
                ],
            }]}, "12"), [])

    def test_local_crop_placement_and_retained_native_text_coexist(self):
        evidence = build_evidence({"sources": {}, "items": []}, (100, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "photo-badge", "category": "image", "bbox": [10, 10, 50, 50],
            "text": "A", "semantic_level": "leaf",
        }]}, (100, 100), evidence)
        decisions = {"decisions": [{
            "label_id": "photo-badge", "decision": "localized_raster",
            "approved_bbox": [20, 25, 20, 15], "source_basis": "sam",
        }]}
        with tempfile.TemporaryDirectory(prefix="decomposer-combined-raster-") as temporary_name:
            root = Path(temporary_name)
            image = root / "source.png"
            Image.new("RGB", (100, 100), "white").save(image)
            representation, _ = canonicalize_representation({"components": [{
                "label_id": "photo-badge", "width": 50, "height": 50,
                "primitives": [
                    {"id": "model-image", "kind": "image", "bbox": [0, 0, 50, 50]},
                    {"id": "native-label", "kind": "text", "bbox": [5, 5, 20, 20],
                     "text": "A", "style": {"color": "#111111"}},
                ],
            }]}, recognition, image, root / "representation", decisions)
        primitives = representation["components"][0]["primitives"]
        self.assertEqual([item["kind"] for item in primitives], ["image", "text"])
        self.assertEqual(primitives[0]["bbox"], [10.0, 15.0, 20.0, 15.0])
        self.assertEqual(primitives[1]["id"], "native-label")

    def test_unapproved_model_image_is_not_cropped(self):
        evidence = build_evidence({"sources": {}, "items": []}, (100, 60))
        recognition, _ = canonicalize_recognition({"objects": [
            {"id": "invented", "category": "image", "bbox": [10, 10, 30, 20], "candidate_ids": []}
        ]}, (100, 60), evidence)
        decisions = decide_assets(recognition, evidence, {})
        with tempfile.TemporaryDirectory(prefix="decomposer-policy-") as temporary_name:
            image = Path(temporary_name) / "source.png"
            Image.new("RGB", (100, 60), "white").save(image)
            representation, report = canonicalize_representation({"components": [{
                "label_id": "invented", "primitives": [{"id": "bad", "kind": "image", "bbox": [0, 0, 30, 20]}]
            }]}, recognition, image, Path(temporary_name) / "representation", decisions)
            primitive = representation["components"][0]["primitives"][0]
            self.assertEqual(primitive["kind"], "rect")
            self.assertTrue(primitive["editable"])
            self.assertEqual(report["asset_count"], 0)

    def test_high_confidence_recognition_image_bbox_is_safe_crop_fallback(self):
        evidence = build_evidence({"sources": {"ocr": {"status": "ready"}}, "items": []}, (200, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "photo", "category": "image", "bbox": [20, 10, 60, 40],
            "candidate_ids": [], "semantic_level": "leaf", "confidence": .97,
        }]}, (200, 100), evidence)
        decisions = decide_assets(recognition, evidence, {})
        decision = decisions["decisions"][0]
        self.assertEqual(decision["decision"], "localized_raster")
        self.assertEqual(decision["source_basis"], "recognition_bbox")
        self.assertEqual(decision["approved_bbox"], [20.0, 10.0, 60.0, 40.0])
        with tempfile.TemporaryDirectory(prefix="decomposer-recognition-raster-") as temporary_name:
            image = Path(temporary_name) / "source.png"
            Image.new("RGB", (200, 100), "navy").save(image)
            representation, report = canonicalize_representation({"components": [{
                "label_id": "photo", "primitives": [{"id": "crop", "kind": "image", "bbox": [0, 0, 60, 40]}]
            }]}, recognition, image, Path(temporary_name) / "representation", decisions)
            self.assertEqual(report["asset_count"], 1)
            asset = next(iter(representation["assets"].values()))
            self.assertEqual(asset["source_basis"], "recognition_bbox")

    def test_large_photo_leaf_needs_explicit_recognition_area_cap(self):
        evidence = build_evidence({"sources": {"ocr": {"status": "ready"}}, "items": []}, (100, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "photo", "category": "image", "bbox": [10, 10, 62, 60],
            "candidate_ids": [], "semantic_level": "leaf", "confidence": .99,
        }]}, (100, 100), evidence)
        default = decide_assets(recognition, evidence, {"asset_policy": {"max_area_ratio": .35}})
        expanded = decide_assets(recognition, evidence, {"asset_policy": {
            "max_area_ratio": .35, "max_recognition_leaf_area_ratio": .4,
        }})
        self.assertEqual(default["decisions"][0]["decision"], "native")
        self.assertEqual(expanded["decisions"][0]["decision"], "localized_raster")
        self.assertEqual(expanded["decisions"][0]["source_basis"], "recognition_bbox")
        self.assertEqual(expanded["policy"]["max_area_ratio"], .35)
        self.assertEqual(expanded["policy"]["max_recognition_leaf_area_ratio"], .4)
        wider_general = decide_assets(recognition, evidence, {"asset_policy": {"max_area_ratio": .6}})
        self.assertEqual(wider_general["policy"]["max_recognition_leaf_area_ratio"], .6)

    def test_partial_sam_leaf_does_not_replace_whole_recognition_object(self):
        evidence = build_evidence({"sources": {"sam": {"status": "ready"}}, "items": [
            {"id": "whole", "source": "sam3", "label": "illustration", "bbox": [20, 10, 60, 40], "confidence": .8},
            {"id": "fragment", "source": "sam3", "label": "illustration", "bbox": [55, 30, 25, 20], "confidence": .99},
        ]}, (200, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "person", "category": "image", "bbox": [20, 10, 60, 40],
            "candidate_ids": ["whole", "fragment"], "semantic_level": "leaf", "confidence": .99,
        }]}, (200, 100), evidence)
        decision = decide_assets(recognition, evidence, {})["decisions"][0]
        self.assertEqual(decision["decision"], "localized_raster")
        self.assertEqual(decision["source_basis"], "recognition_bbox")
        self.assertEqual(decision["approved_bbox"], [20.0, 10.0, 60.0, 40.0])
        self.assertIn("sam_insufficient_object_coverage", decision["reason_codes"])
        with tempfile.TemporaryDirectory(prefix="decomposer-whole-image-crop-") as temporary_name:
            image = Path(temporary_name) / "source.png"
            Image.new("RGB", (200, 100), "white").save(image)
            representation, _ = canonicalize_representation({"components": [{
                "label_id": "person", "width": 60, "height": 40,
                "primitives": [{"id": "fragment", "kind": "image", "bbox": [35, 20, 25, 20]}],
            }]}, recognition, image, Path(temporary_name) / "representation", decide_assets(recognition, evidence, {}))
            self.assertEqual(representation["components"][0]["primitives"][0]["bbox"], [0.0, 0.0, 60, 40])

    def test_partial_sam_leaf_with_ocr_heavy_bbox_is_not_cropped(self):
        evidence = build_evidence({"sources": {"sam": {"status": "ready"}, "ocr": {"status": "ready"}}, "items": [
            {"id": "fragment", "source": "sam3", "label": "illustration", "bbox": [55, 30, 25, 20], "confidence": .99},
            {"id": "ocr", "source": "ocr", "label": "text", "bbox": [25, 15, 40, 20], "text": "caption", "confidence": .99},
        ]}, (200, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "person", "category": "image", "bbox": [20, 10, 60, 40],
            "candidate_ids": ["fragment"], "semantic_level": "leaf", "confidence": .99,
        }]}, (200, 100), evidence)
        decision = decide_assets(recognition, evidence, {})["decisions"][0]
        self.assertEqual(decision["decision"], "native")
        self.assertIsNone(decision["approved_bbox"])
        self.assertIn("sam_insufficient_object_coverage", decision["reason_codes"])

    def test_covering_sam_leaf_wins_over_higher_confidence_fragment(self):
        evidence = build_evidence({"sources": {"sam": {"status": "ready"}}, "items": [
            {"id": "covering", "source": "sam3", "label": "illustration", "bbox": [21, 11, 55, 35], "confidence": .8},
            {"id": "fragment", "source": "sam3", "label": "illustration", "bbox": [75, 10, 5, 40], "confidence": .99},
        ]}, (200, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "person", "category": "image", "bbox": [20, 10, 60, 40],
            "candidate_ids": ["covering", "fragment"], "semantic_level": "leaf", "confidence": .99,
        }]}, (200, 100), evidence)
        decision = decide_assets(recognition, evidence, {})["decisions"][0]
        self.assertEqual(decision["source_basis"], "sam_candidate")
        self.assertEqual(decision["source_candidate_id"], "covering")
        self.assertNotIn("sam_insufficient_object_coverage", decision["reason_codes"])

    def test_approved_raster_replaces_model_vector_surrogate(self):
        evidence = build_evidence({"sources": {}, "items": []}, (200, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "photo", "category": "image", "bbox": [20, 10, 60, 40],
            "candidate_ids": [], "semantic_level": "leaf", "confidence": .99,
        }]}, (200, 100), evidence)
        decisions = decide_assets(recognition, evidence, {})
        with tempfile.TemporaryDirectory(prefix="decomposer-raster-override-") as temporary_name:
            image = Path(temporary_name) / "source.png"
            Image.new("RGB", (200, 100), "navy").save(image)
            representation, report = canonicalize_representation({"components": [{
                "label_id": "photo", "primitives": [
                    {"id": "face", "kind": "path", "bbox": [0, 0, 60, 40], "path": "M0 0 L60 40"},
                    {"id": "hair", "kind": "polygon", "points": [[0, 0], [60, 0], [30, 20]]},
                ],
            }]}, recognition, image, Path(temporary_name) / "representation", decisions)
            primitives = representation["components"][0]["primitives"]
            self.assertEqual([item["kind"] for item in primitives], ["image"])
            self.assertEqual(report["policy_override_count"], 1)
            self.assertEqual(report["policy_overrides"][0]["replaced_kinds"], ["path", "polygon"])
            self.assertFalse(representation["degraded"])

    def test_recognition_bbox_fallback_rejects_ocr_heavy_image(self):
        evidence = build_evidence({"sources": {"ocr": {"status": "ready"}}, "items": [{
            "id": "ocr1", "source": "ocr", "label": "text", "bbox": [25, 15, 50, 30],
            "text": "not a photo", "confidence": .99,
        }]}, (200, 100))
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "image-like", "category": "image", "bbox": [20, 10, 60, 40],
            "candidate_ids": [], "semantic_level": "leaf", "confidence": .98,
        }]}, (200, 100), evidence)
        decision = decide_assets(recognition, evidence, {})["decisions"][0]
        self.assertEqual(decision["decision"], "native")
        self.assertIn("no_eligible_sam_or_safe_recognition_bbox", decision["reason_codes"])

    def test_text_glyph_outline_is_replaced_with_complete_editable_text(self):
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "vertical", "category": "text", "bbox": [40, 10, 10, 60],
            "text": "1×1 2D Conv", "geometry_hint": "Text rotated clockwise 90 degrees.",
            "style_hint": {"font_size": 18, "font_family": "Arial"},
        }]}, (100, 100), {"items": []})
        with tempfile.TemporaryDirectory(prefix="decomposer-text-coverage-") as temporary_name:
            source = Path(temporary_name) / "source.png"
            Image.new("RGB", (100, 100), "white").save(source)
            representation, report = canonicalize_representation({"components": [{
                "label_id": "vertical", "width": 10, "height": 60,
                "primitives": [{"id": "glyphs", "kind": "path", "bbox": [0, 0, 10, 60], "path": "M0 0 L10 10"}],
            }]}, recognition, source, Path(temporary_name) / "representation")
        primitives = representation["components"][0]["primitives"]
        self.assertEqual([(item["kind"], item.get("text")) for item in primitives], [("text", "1×1 2D Conv")])
        self.assertTrue(primitives[0]["editable"])
        self.assertTrue(representation["degraded"])
        self.assertEqual(report["repairs"][0]["action"], "restored_editable_text_coverage")

    def test_representation_repairs_exact_spacing_without_moving_single_text(self):
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "tokens", "category": "shape", "bbox": [5, 5, 90, 30],
            "text": "1  2  3", "geometry_hint": "three tiles",
        }]}, (100, 100), {"items": []})
        with tempfile.TemporaryDirectory(prefix="decomposer-exact-text-") as temporary_name:
            source = Path(temporary_name) / "source.png"
            Image.new("RGB", (100, 100), "white").save(source)
            representation, report = canonicalize_representation({"components": [{
                "label_id": "tokens", "width": 90, "height": 30,
                "primitives": [
                    {"id": "tile", "kind": "rect", "bbox": [0, 0, 90, 30]},
                    {"id": "text", "kind": "text", "bbox": [0, 5, 90, 20], "text": "1     2     3"},
                ],
            }]}, recognition, source, Path(temporary_name) / "representation")
        primitives = representation["components"][0]["primitives"]
        self.assertEqual([(item["id"], item["kind"]) for item in primitives], [("tile", "rect"), ("text", "text")])
        self.assertEqual(primitives[1]["bbox"], [0.0, 5.0, 90.0, 20.0])
        self.assertEqual(primitives[1]["text"], "1  2  3")
        self.assertEqual(report["repairs"][0]["action"], "restored_exact_editable_text_identity")

    def test_representation_validation_rejects_refinement_whitespace_drift(self):
        recognition = {"objects": [{"id": "tokens", "category": "shape", "text": "1  2"}]}
        representation = {"components": [{
            "label_id": "tokens", "primitive_order": ["text"],
            "primitives": [{"id": "text", "kind": "text", "text": "1     2"}],
        }]}
        from decomposer.representation import validate as validate_representation
        with self.assertRaisesRegex(AssertionError, "exact recognition text identity"):
            validate_representation(representation, recognition)

    def test_composite_shape_keeps_geometry_and_gains_exact_editable_text(self):
        recognition, _ = canonicalize_recognition({"objects": [{
            "id": "vertical-box", "category": "shape", "bbox": [40, 10, 10, 60],
            "text": "1×1 2D Conv", "geometry_hint": "Tall box; label rotated clockwise 90 degrees.",
            "style_hint": {"stroke": "#49AFD6", "font_size": 18, "font_family": "Arial"},
        }]}, (100, 100), {"items": []})
        with tempfile.TemporaryDirectory(prefix="decomposer-composite-text-") as temporary_name:
            source = Path(temporary_name) / "source.png"
            Image.new("RGB", (100, 100), "white").save(source)
            representation, report = canonicalize_representation({"components": [{
                "label_id": "vertical-box", "width": 10, "height": 60,
                "primitives": [
                    {"id": "outline", "kind": "rect", "bbox": [0, 0, 10, 60]},
                    {"id": "glyphs", "kind": "path", "bbox": [1, 1, 8, 58], "path": "M0 0 L8 8"},
                ],
            }]}, recognition, source, Path(temporary_name) / "representation")
        primitives = representation["components"][0]["primitives"]
        self.assertEqual([item["kind"] for item in primitives], ["rect", "path", "text"])
        self.assertEqual(primitives[-1]["text"], "1×1 2D Conv")
        self.assertTrue(primitives[-1]["editable"])
        self.assertTrue(report["repairs"][0]["preserved_non_text_geometry"])

    def test_editable_text_validation_covers_text_bearing_shapes(self):
        recognition = {"objects": [
            {"id": "shape-label", "category": "shape", "text": "Encoder"},
            {"id": "formula", "category": "equation", "text": "x^2"},
        ]}
        scene = {"elements": [
            {"id": "shape-label/outline", "source_label_id": "shape-label", "kind": "rect", "editable": True},
            {"id": "formula/text", "source_label_id": "formula", "kind": "text", "text": "x", "editable": True},
        ]}
        self.assertEqual(_editable_text_mismatches(recognition, scene), [
            {"label_id": "shape-label", "expected": "Encoder", "actual": ""}
        ])

    def test_cjk_text_requires_explicit_compiler_font_route(self):
        scene = {"elements": [
            {"id": "prompt/text", "kind": "text", "text": "English \u77d2\u5929\u8fc7\u6d77"},
            {"id": "latin/text", "kind": "text", "text": "English only"},
        ]}
        self.assertEqual(
            _script_font_route_mismatches(scene, {"script_font_routes": []}),
            [{"element_id": "prompt/text", "route": None}],
        )
        self.assertEqual(_script_font_route_mismatches(scene, {"script_font_routes": [{
            "id": "prompt/text", "script": "cjk", "requested_font": "Times New Roman", "resolved_font": "Arial Unicode MS",
        }]}), [])

    def test_bundled_libreoffice_gets_isolated_fontconfig_cache(self):
        with tempfile.TemporaryDirectory() as temporary_name:
            temporary = Path(temporary_name)
            soffice = temporary / "LibreOfficeDev.app" / "Contents" / "MacOS" / "soffice"
            config = temporary / "LibreOfficeDev.app" / "Contents" / "Resources" / "fontconfig" / "fonts.conf"
            soffice.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            soffice.touch()
            config.touch()
            environment, report = _libreoffice_font_environment(str(soffice), temporary / "render")
            self.assertEqual(environment["FONTCONFIG_FILE"], str(config.resolve()))
            self.assertEqual(environment["XDG_CACHE_HOME"], str(temporary / "render" / "fontconfig-cache"))
            self.assertEqual(report["mode"], "bundled-fontconfig")
            self.assertTrue(report["configured"])

    def test_cjk_render_validation_rejects_unconfigured_bundled_renderer(self):
        scene = {"elements": [{"id": "prompt/text", "kind": "text", "text": "矒天过海"}]}
        runtime = {"soffice": "/runtime/LibreOfficeDev.app/Contents/MacOS/soffice"}
        report = {"render": {"status": "ready"}}
        self.assertEqual(_cjk_render_environment_mismatches(scene, report, runtime)[0]["element_ids"], ["prompt/text"])
        report["render"]["font_environment"] = {"mode": "bundled-fontconfig", "configured": True}
        self.assertEqual(_cjk_render_environment_mismatches(scene, report, runtime), [])

    def test_arrowhead_export_validation_uses_ooxml_endpoint_facts(self):
        scene = {"elements": [{
            "id": "edge/route", "source_label_id": "edge", "kind": "path",
            "arrow_start": False, "arrow_end": True,
        }]}
        provenance = {"labels": {"edge": {
            "scene_elements": [{"id": "edge/route"}],
            "ooxml_shapes": [{"name": "edge/route", "shape_id": 7, "head_end": "none", "tail_end": "none"}],
        }}}
        mismatch = _arrowhead_export_mismatches(scene, provenance)
        self.assertEqual(mismatch[0]["element_id"], "edge/route")
        provenance["labels"]["edge"]["ooxml_shapes"][0]["tail_end"] = "triangle"
        self.assertEqual(_arrowhead_export_mismatches(scene, provenance), [])

    def test_styled_text_run_validation_uses_visible_text_and_native_baseline(self):
        scene = {"elements": [{
            "id": "title/text", "kind": "text", "text": "I_Source",
            "runs": [
                {"text": "I", "source_text": "I", "script": "normal"},
                {"text": "Source", "source_text": "_Source", "script": "subscript"},
            ],
        }]}
        provenance = {"labels": {"title": {"ooxml_shapes": [{
            "name": "title/text", "text": "ISource", "run_baselines": [0, -25000],
        }]}}}
        self.assertEqual(_styled_text_run_mismatches(scene, provenance), [])
        provenance["labels"]["title"]["ooxml_shapes"][0]["run_baselines"] = [0, 0]
        mismatch = _styled_text_run_mismatches(scene, provenance)
        self.assertEqual(mismatch[0]["actual_subscripts"], 0)

    def test_default_layout_converts_explicit_text_rotation_hint(self):
        recognition = {"canvas": {"width": 100, "height": 100}, "objects": [
            {"id": "cw", "bbox": [40, 10, 10, 60], "geometry_hint": "Text rotated clockwise."},
            {"id": "ccw", "bbox": [10, 20, 20, 40], "geometry_hint": "Rotated counterclockwise."},
            {"id": "numeric", "bbox": [70, 20, 10, 40], "geometry_hint": "Text rotated 90 degrees."},
            {"id": "ambiguous", "bbox": [20, 70, 40, 10], "geometry_hint": "Text is rotated."},
        ]}
        placements = {item["label_id"]: item for item in default_layout(recognition)["placements"]}
        self.assertEqual(placements["cw"]["bbox"], [15.0, 35.0, 60, 10])
        self.assertEqual(placements["cw"]["rotation_deg"], 90.0)
        self.assertEqual(placements["ccw"]["bbox"], [0, 30.0, 40, 20])
        self.assertEqual(placements["ccw"]["rotation_deg"], 270.0)
        self.assertEqual(placements["numeric"]["rotation_deg"], 90.0)
        self.assertEqual(placements["ambiguous"]["rotation_deg"], 0.0)

    def test_rotated_text_uses_visible_extent_for_negative_native_box(self):
        recognition = {"canvas": {"width": 3200, "height": 3032}, "objects": [{
            "id": "vertical", "bbox": [84, 1502, 65, 583],
            "geometry_hint": "Text rotated 90 degrees counterclockwise",
        }]}
        representation = {"components": [{"label_id": "vertical"}]}
        initial = default_layout(recognition)["placements"][0]
        self.assertEqual(initial["bbox"], [-175.0, 1761.0, 583, 65])
        self.assertEqual(initial["rotation_deg"], 270.0)
        proposal = {"operations": [{"op": "place", "label_id": "vertical",
                                    "bbox": [-175, 1761, 583, 65], "rotation_deg": 270}]}
        layout, report = canonicalize_layout(proposal, recognition, representation)
        self.assertEqual(layout["placements"][0]["bbox"], [-175.0, 1761.0, 583.0, 65.0])
        self.assertEqual(report["repair_count"], 0)
        outside = {"operations": [{"op": "place", "label_id": "vertical",
                                   "bbox": [-500, 1761, 583, 65], "rotation_deg": 270}]}
        guarded, guard_report = canonicalize_layout(outside, recognition, representation)
        self.assertEqual(guarded["placements"][0]["bbox"], initial["bbox"])
        self.assertEqual(guard_report["repairs"][0]["action"], "ignored_outside_visible_canvas")

    def test_compiler_emits_editable_subscript_runs_without_literal_markup(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        with tempfile.TemporaryDirectory(prefix="decomposer-text-runs-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 320, "height": 100, "background": "#FFFFFF"},
                "elements": [{
                    "id": "title/text", "source_label_id": "title", "category": "text", "kind": "text",
                    "bbox": [10, 10, 300, 80], "rotation_deg": 0, "editable": True,
                    "style": {"color": "#000000", "font_size": 32, "font_family": "Arial", "bold": False, "italic": False, "align": "left"},
                    "text": "I_Source",
                    "runs": [
                        {"text": "I", "source_text": "I", "script": "normal", "style": {"color": "#000000", "font_size": 32, "font_family": "Arial", "bold": False, "italic": True}},
                        {"text": "Source", "source_text": "_Source", "script": "subscript", "style": {"color": "#000000", "font_size": 24, "font_family": "Arial", "bold": False, "italic": True}},
                    ],
                }],
                "z_order": ["title/text"], "assets": {}, "degraded": False,
            }
            scene_path = root / "scene.json"; write_json(scene_path, scene)
            report = compile_scene(scene_path, root / "output", {"runtime": {"node": "node", "timeout_seconds": 180}}, render_output=False)
            self.assertEqual(report["compiler"]["styled_text_run_count"], 2)
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            drawing = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            self.assertEqual("".join(item.text or "" for item in slide_root.findall(".//a:t", drawing)), "ISource")
            baselines = [int(item.get("baseline", "0")) for item in slide_root.findall(".//a:rPr", drawing)]
            self.assertTrue(any(value < 0 for value in baselines))

    def test_compiler_preserves_multiline_styled_runs_and_empty_paragraphs(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        with tempfile.TemporaryDirectory(prefix="decomposer-multiline-runs-") as temporary_name:
            root = Path(temporary_name)
            scene = {
                "schema_version": "decomposer.scene.v1",
                "canvas": {"width": 320, "height": 140, "background": "#FFFFFF"},
                "elements": [{
                    "id": "mmos/text", "source_label_id": "mmos", "category": "shape", "kind": "text",
                    "bbox": [10, 10, 300, 120], "rotation_deg": 0, "editable": True,
                    "style": {"color": "#4775B5", "font_size": 30, "font_family": "Arial", "bold": False, "italic": False, "align": "center"},
                    "text": "MMOs:\n\nfluents +- \\delta",
                    "runs": [
                        {"text": "MMOs:\n\nfluents +- ", "source_text": "MMOs:\n\nfluents +- ", "script": "normal"},
                        {"text": "δ", "source_text": "\\delta", "script": "normal", "style": {"color": "#4775B5", "font_size": 30, "font_family": "Arial", "italic": True}},
                    ],
                }],
                "z_order": ["mmos/text"], "assets": {}, "degraded": False,
            }
            scene_path = root / "scene.json"; write_json(scene_path, scene)
            compile_scene(scene_path, root / "output", {"runtime": {"node": "node", "timeout_seconds": 180}}, render_output=False)
            records = _shape_records(root / "output/editable.pptx")
            self.assertEqual(records["mmos/text"]["text"], "MMOs:\n\nfluents +- δ")
            with zipfile.ZipFile(root / "output/editable.pptx") as archive:
                slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            drawing = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            paragraphs = ["".join(item.text or "" for item in paragraph.findall(".//a:t", drawing)) for paragraph in slide_root.findall(".//a:p", drawing)]
            self.assertEqual(paragraphs, ["MMOs:", "", "fluents +- δ"])

    def test_model_output_schemas_are_closed(self):
        def visit(value):
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertIs(value.get("additionalProperties"), False)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        for schema in (RECOGNITION_PROPOSAL_SCHEMA, REPRESENTATION_PROPOSAL_SCHEMA, LAYOUT_PROPOSAL_SCHEMA, REFINEMENT_SCHEMA):
            visit(schema)

    def test_generation_stages_receive_no_memories(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-guidance-") as temporary_name:
            root = Path(temporary_name)
            source = root / "source.png"
            Image.new("RGB", (100, 60), "white").save(source)
            memory = root / "knowledge/memory"
            memory.mkdir(parents=True)
            rows = [
                {"memory_id": "recognition-verified", "owner_stage": "recognition", "status": "downstreamed", "accepted_count": 1, "evidence_ids": ["a"]},
                {"memory_id": "recognition-candidate", "owner_stage": "recognition", "status": "candidate", "accepted_count": 3, "evidence_ids": ["b"]},
                {"memory_id": "representation-verified", "owner_stage": "representation", "status": "downstreamed", "accepted_count": 1, "evidence_ids": ["c"]},
                {"memory_id": "layout-verified", "owner_stage": "layout", "status": "downstreamed", "accepted_count": 1, "evidence_ids": ["d"]},
            ]
            for row in rows:
                row["mechanism"] = "fixture-owned-memory"
                if row["status"] == "downstreamed":
                    code = ROOT / "decomposer/__init__.py"
                    row["downstream_proof"] = {
                        "version": VERSION,
                        "scope": f"{row['owner_stage']} owned objects in this fixture",
                        "counterexamples": ["other stages are excluded"],
                        "evidence_id": row["evidence_ids"][0],
                        "artifact_refs": [{"path": str(source), "sha256": sha256(source)}],
                        "code_artifact": {"path": str(code), "sha256": sha256(code)},
                    }
                write_json(memory / f"{row['memory_id']}.json", row)
            evidence_dir = root / "knowledge/evidence"
            evidence_dir.mkdir()
            for evidence_id in ("a", "c", "d"):
                write_json(evidence_dir / f"{evidence_id}.json", {
                    "owner_stage": {"a": "recognition", "c": "representation", "d": "layout"}[evidence_id],
                    "mechanism": "fixture-owned-memory",
                    "accepted": True, "assessment": {
                        "accepted": True,
                        "before_local": {"pixel_similarity": .8, "edge_iou": .3},
                        "after_local": {"pixel_similarity": .82, "edge_iou": .31},
                        "before_global": {"pixel_similarity": .9, "edge_iou": .4},
                        "after_global": {"pixel_similarity": .902, "edge_iou": .405},
                        "local_pixel_delta": .02, "local_edge_delta": .01,
                        "global_pixel_delta": .002,
                    },
                    "artifact_refs": [{"path": str(source), "sha256": sha256(source)}],
                })
            fixtures = root / "fixtures"
            fixtures.mkdir()
            write_json(fixtures / "recognition.json", {"objects": [{"id": "box", "category": "shape", "bbox": [10, 10, 40, 30]}]})
            write_json(fixtures / "representation.json", {"components": [{"label_id": "box", "width": 40, "height": 30, "primitives": [{"id": "body", "kind": "rect", "bbox": [0, 0, 40, 30]}]}]})
            write_json(fixtures / "layout.json", {"operations": []})
            config = {"knowledge": {"path": str(root / "knowledge")}}
            evidence = build_evidence({"sources": {}, "items": []}, (100, 60))
            recognition = run_recognition(source, (100, 60), evidence, config, root / "recognition", root / "calls", fixtures)
            representation = run_representation(source, recognition, config, root / "representation", root / "calls", asset_decisions={"decisions": []}, fixture_dir=fixtures)
            run_layout(source, recognition, representation, config, root / "layout", root / "calls", fixtures)
            for stage in ("recognition", "representation", "layout"):
                payload = json.loads((root / "calls" / stage / "input.json").read_text())
                self.assertEqual(payload["applicable_memories"], [])

    def test_json_only_pipeline_compiles(self):
        has_pptxgenjs = any(path.exists() for path in (
            ROOT / "node_modules" / "pptxgenjs",
            ROOT.parent / "node_modules" / "pptxgenjs",
            ROOT / "backend" / "node_modules" / "pptxgenjs",
        ))
        if shutil.which("node") is None or not has_pptxgenjs:
            self.skipTest("node/pptxgenjs unavailable")
        with tempfile.TemporaryDirectory(prefix="decomposer-test-") as temporary_name:
            temporary = Path(temporary_name)
            source = temporary / "source.png"
            Image.new("RGB", (640, 360), "white").save(source)
            fixtures = temporary / "fixtures"
            fixtures.mkdir()
            write_json(fixtures / "recognition.json", {
                "background": "#fff",
                "objects": [
                    {"id": "node", "category": "box", "bbox": [-2, 40, 220, 100], "text": "Encoder", "style_hint": {"fill": "#def"}},
                    {"id": "cjk", "category": "text", "bbox": [220, 220, 200, 60], "text": "\u77d2\u5929\u8fc7\u6d77"},
                    {"id": "photo", "category": "image", "bbox": [300, 40, 200, 140], "text": ""},
                ],
            })
            write_json(fixtures / "representation.json", {
                "components": [{
                    "label_id": "node", "width": 220, "height": 100,
                    "primitives": [
                        {"id": "body", "kind": "polygon", "points": [[0, 0], [220, 0], [200, 100], [20, 100]], "style": {"fill": "#def"}},
                        {"id": "title", "kind": "text", "bbox": [10, 20, 200, 50], "text": "Encoder", "style": {"font_family": "sans-serif", "color": "#000000"}},
                        {"id": "route", "kind": "path", "bbox": [10, 75, 200, 15], "path": "M0 7.5 L200 7.5", "arrow_end": True, "style": {"fill": "none", "stroke": "#000000"}},
                    ],
                }, {
                    "label_id": "cjk", "width": 200, "height": 60,
                    "primitives": [{"id": "text", "kind": "text", "bbox": [0, 0, 200, 60], "text": "\u77d2\u5929\u8fc7\u6d77", "style": {"font_family": "Arial", "color": "#000000"}}],
                }]
            })
            write_json(fixtures / "layout.json", {
                "operations": [
                    {"op": "place", "label_id": "node", "bbox": [-20, 35, 240, 110], "rotation_deg": 0},
                    {"op": "unknown", "label_id": "photo"},
                ]
            })
            soffice, pdftoppm = shutil.which("soffice"), shutil.which("pdftoppm")
            can_render = bool(soffice and pdftoppm)
            config = {"model": {"command": "/usr/bin/false"}, "detectors": {}, "runtime": {"node": "node", "soffice": soffice, "pdftoppm": pdftoppm, "timeout_seconds": 180}}
            output = temporary / "run"
            status = execute(source, output, config, fixture_dir=fixtures, render_output=can_render)
            self.assertEqual(status["status"], "render_success" if can_render else "structural_success",
                             json.loads((output / "output" / "validation.json").read_text()))
            self.assertTrue((output / "output" / "editable.pptx").exists())
            with zipfile.ZipFile(output / "output" / "editable.pptx") as archive:
                slide_xml = archive.read("ppt/slides/slide1.xml")
            slide_root = ET.fromstring(slide_xml)
            drawing = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            self.assertIsNotNone(slide_root.find(".//a:custGeom", drawing))
            self.assertIsNotNone(slide_root.find(".//a:normAutofit", drawing))
            self.assertIsNotNone(slide_root.find(".//a:tailEnd[@type='triangle']", drawing))
            self.assertNotIn(b"sans-serif", slide_xml)
            self.assertIn(b' typeface="Arial"', slide_xml)
            self.assertIn('typeface="Arial Unicode MS"'.encode(), slide_xml)
            if can_render:
                self.assertTrue((output / "output" / "preview.png").exists())
            scene = json.loads((output / "output" / "scene.json").read_text())
            self.assertEqual(len(scene["elements"]), 5)
            title = next(item for item in scene["elements"] if item["id"] == "node/title")
            self.assertEqual(title["style"]["font_family"], "Arial")
            route = next(item for item in scene["elements"] if item["id"] == "node/route")
            self.assertTrue(route["arrow_end"])
            provenance = json.loads((output / "output" / "provenance.json").read_text())
            route_shapes = provenance["labels"]["node"]["ooxml_shapes"]
            route_shape = next(item for item in route_shapes if item["name"] == "node/route")
            self.assertEqual(route_shape["tail_end"], "triangle")
            validation = json.loads((output / "output" / "validation.json").read_text())
            self.assertTrue(validation["checks"]["arrowhead_export_coverage"]["passed"])
            self.assertTrue(validation["checks"]["script_font_routing"]["passed"])
            self.assertTrue(scene["degraded"])
            write_json(output / "calls" / "representation" / "metadata.json", {
                "stage": "representation", "status": "degraded", "fallback_used": True,
                "error": "TimeoutExpired: prior model call timed out",
            })
            execute(source, output, config, fixture_dir=fixtures, render_output=can_render, resume=True)
            retry_report = json.loads((output / "resume-report.json").read_text())
            self.assertEqual(
                [(item["phase"], item["reused"]) for item in retry_report["phases"]],
                [("input", True), ("observations", True), ("recognition", True),
                 ("representation", False), ("layout", False)],
            )
            self.assertEqual(
                json.loads((output / "calls" / "representation" / "metadata.json").read_text())["status"],
                "fixture",
            )
            for proposal in fixtures.glob("*.json"):
                proposal.rename(proposal.with_suffix(".unavailable"))
            execute(source, output, config, fixture_dir=fixtures, render_output=can_render, resume=True)
            resume_report = json.loads((output / "resume-report.json").read_text())
            self.assertTrue(all(item["reused"] for item in resume_report["phases"]))


if __name__ == "__main__":
    unittest.main()
