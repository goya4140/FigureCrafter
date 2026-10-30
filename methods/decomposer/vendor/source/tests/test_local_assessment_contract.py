"""Deterministic registrations must measure the owner region they report."""

import ast
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from decomposer.quality import affected_scene_region, boxes_region, compare
from decomposer.short_text_registration import proposal_region
from decomposer.source_line_registration import apply as apply_source_line, replacement_boxes


MODULES = (
    "flat_panel_registration",
    "joint_shadow_text_registration",
    "native_shadow_registration",
    "post_short_text_registration",
    "post_tracking_text_registration",
    "rect_fill_registration",
    "short_text_registration",
    "source_line_registration",
    "styled_multiline_registration",
    "text_registration",
    "text_tracking_registration",
)


class LocalAssessmentContractTest(unittest.TestCase):
    def test_owner_region_covers_old_and_new_geometry(self):
        before = {"canvas": {"width": 100, "height": 80}, "elements": [
            {"id": "moved", "bbox": [10, 20, 20, 10]},
            {"id": "other", "bbox": [0, 0, 5, 5]},
        ]}
        after = {"canvas": before["canvas"], "elements": [
            {"id": "moved", "bbox": [25, 12, 20, 16]},
            {"id": "other", "bbox": [0, 0, 5, 5]},
        ]}
        self.assertEqual(affected_scene_region(before, after, ["moved"]), [10, 12, 35, 18])
        self.assertEqual(affected_scene_region(before, after, []), [0, 0, 100, 80])
        with self.assertRaisesRegex(ValueError, "affected primitive missing"):
            affected_scene_region(before, after, ["missing"])

    def test_split_line_region_includes_all_native_replacements(self):
        before = {"canvas": {"width": 200, "height": 100}, "elements": [
            {"id": "label/text", "source_label_id": "label", "kind": "text",
             "text": "one\ntwo\nthree", "bbox": [20, 20, 150, 60], "style": {"font_size": 14}},
        ], "z_order": ["label/text"]}
        proposal = {"id": "label/text", "line_count": 3,
                    "first_top": 10, "line_advance": 25, "line_height": 20}
        after = apply_source_line(before, proposal)
        boxes = replacement_boxes(before, after, proposal)
        self.assertEqual(len(boxes), 4)
        self.assertEqual(boxes_region(boxes, before["canvas"]), [20, 10, 150, 70])
        with self.assertRaisesRegex(ValueError, "replacement identity drift"):
            replacement_boxes(before, after, {**proposal, "line_count": 2})

    def test_short_text_attempt_region_covers_only_changed_owner(self):
        proposal = {"old_bbox": [10, 20, 40, 12], "new_bbox": [8, 15, 44, 21]}
        self.assertEqual(proposal_region(proposal), [8, 15, 44, 21])

    def test_registration_compare_uses_reported_region(self):
        package = Path(__file__).resolve().parents[1] / "decomposer"
        for name in MODULES:
            with self.subTest(name=name):
                tree = ast.parse((package / f"{name}.py").read_text())
                calls = [node for node in ast.walk(tree)
                         if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Name)
                         and node.func.id == "compare"]
                self.assertEqual(len(calls), 1)
                region = ast.unparse(calls[0].args[3])
                self.assertIn(region, ("region", "attempt_region"))

    def test_local_metrics_differ_from_global_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = Image.new("RGB", (100, 100), "white")
            before = reference.copy()
            after = reference.copy()
            ImageDraw.Draw(reference).rectangle((10, 10, 29, 29), fill="black")
            ImageDraw.Draw(after).rectangle((10, 10, 29, 29), fill="black")
            paths = [root / name for name in ("reference.png", "before.png", "after.png")]
            for image, path in zip((reference, before, after), paths):
                image.save(path)
            assessment = compare(*paths, [0, 0, 40, 40])
            self.assertGreater(assessment["local_pixel_delta"], assessment["global_pixel_delta"])
            self.assertGreater(assessment["after_local"]["pixel_similarity"],
                               assessment["before_local"]["pixel_similarity"])


if __name__ == "__main__":
    unittest.main()
