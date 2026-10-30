from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from decomposer.common import write_json
from decomposer.native_text_style_probe import (
    dark_ink_candidates, dark_ink_shifts, scaled_monospace_scene, search_owner,
    single_line_monospace_ink_candidates, styled_scene,
)
from decomposer.native_text_style_registration import run as register_native_text_style


class NativeTextStyleProbeTest(unittest.TestCase):
    def test_styled_scene_preserves_exact_text_and_source(self):
        original = {"canvas": {"width": 100, "height": 100}, "elements": [
            {"id": "label/text", "source_label_id": "label", "kind": "text",
             "text": "Query\nContext", "bbox": [10, 20, 40, 40],
             "style": {"font_family": "Arial", "font_size": 43},
             "runs": [{"text": "Query\nContext", "source_text": "Query\nContext",
                       "style": {"font_family": "Arial", "font_size": 43}}]},
        ]}
        changed = styled_scene(original, "label/text", "Arial Black", 40, -4, 4)
        owner = changed["elements"][0]
        self.assertEqual(owner["text"], "Query\nContext")
        self.assertEqual(owner["runs"][0]["source_text"], "Query\nContext")
        self.assertEqual(owner["bbox"], [6, 24, 40, 40])
        self.assertEqual(owner["runs"][0]["style"]["font_family"], "Arial Black")
        self.assertEqual(original["elements"][0]["bbox"], [10, 20, 40, 40])
        with self.assertRaisesRegex(ValueError, "outside canvas"):
            styled_scene(original, "label/text", "Arial Black", 40, -11, 0)

    def test_detects_dark_ink_deficit_without_white_or_styled_false_positive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Image.new("RGB", (160, 100), "white")
            preview = source.copy()
            draw_source = ImageDraw.Draw(source)
            draw_preview = ImageDraw.Draw(preview)
            draw_source.rectangle((20, 20, 69, 29), fill="black")
            draw_source.rectangle((20, 40, 69, 49), fill="black")
            draw_preview.rectangle((20, 20, 39, 29), fill="black")
            draw_preview.rectangle((20, 40, 39, 49), fill="black")
            source.save(root / "source.png")
            preview.save(root / "preview.png")
            base = {"id": "label/text", "source_label_id": "label", "kind": "text",
                    "text": "Query\nContext", "bbox": [15, 15, 60, 45],
                    "style": {"font_size": 40, "font_family": "Arial",
                              "bold": True, "color": "#080904"}}
            white = {**base, "id": "white/text", "source_label_id": "white",
                     "style": {**base["style"], "color": "#FFFFFF"}}
            styled = {**base, "id": "styled/text", "source_label_id": "styled",
                      "runs": [{"text": "Query", "style": {"color": "#FF0000"}}]}
            mixed_font = {**base, "id": "mixed-font/text", "source_label_id": "mixed-font",
                          "runs": [{"text": "Query", "style": {"font_size": 34}}]}
            scene = {"elements": [base, white, styled, mixed_font]}
            rows = dark_ink_candidates(scene, root / "source.png", root / "preview.png")
            self.assertEqual([row["id"] for row in rows], ["label/text"])
            self.assertEqual(rows[0]["source_dark_pixels"], 1000)
            self.assertEqual(rows[0]["rendered_dark_pixels"], 400)
            self.assertEqual(rows[0]["ink_ratio"], .4)

    def test_bounded_shift_ranks_true_translation_without_wraparound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Image.new("RGB", (100, 100), "white")
            preview = source.copy()
            ImageDraw.Draw(source).rectangle((30, 40, 49, 49), fill="black")
            ImageDraw.Draw(preview).rectangle((34, 37, 53, 46), fill="black")
            source.save(root / "source.png")
            preview.save(root / "preview.png")
            best = dark_ink_shifts(root / "source.png", root / "preview.png",
                                   [25, 35, 35, 25], max_shift=6)
            self.assertEqual((best[0]["shift_x"], best[0]["shift_y"]), (-4, 3))
            self.assertEqual(best[0]["ink_iou"], 1.0)
            with self.assertRaisesRegex(ValueError, "bounded shift"):
                dark_ink_shifts(root / "source.png", root / "preview.png",
                                [25, 35, 35, 25], max_shift=6, padding=2)

    def test_search_refuses_untriggered_owner_without_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = Image.new("RGB", (100, 100), "white")
            image.save(root / "source.png")
            image.save(root / "preview.png")
            scene = {"canvas": {"width": 100, "height": 100}, "elements": [
                {"id": "label/text", "source_label_id": "label", "kind": "text",
                 "text": "Query\nContext", "bbox": [10, 10, 60, 50],
                 "style": {"font_size": 40, "font_family": "Arial",
                           "bold": True, "color": "#000000"}},
            ]}
            output = root / "search"
            with self.assertRaisesRegex(ValueError, "conservative dark-ink trigger"):
                search_owner(scene, root / "source.png", root / "preview.png",
                             "label/text", {}, output, {"compiler": {"fallbacks": []}})
            self.assertFalse(output.exists())

    def test_registration_no_trigger_is_idempotent_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            image = Image.new("RGB", (100, 100), "white")
            image.save(root / "source.png")
            image.save(output / "preview.png")
            scene = {"canvas": {"width": 100, "height": 100}, "elements": [
                {"id": "label/text", "source_label_id": "label", "kind": "text",
                 "text": "Query\nContext", "bbox": [10, 10, 60, 50],
                 "style": {"font_size": 40, "font_family": "Arial",
                           "bold": True, "color": "#000000"}},
            ]}
            write_json(output / "scene.json", scene)
            (output / "editable.pptx").write_bytes(b"unchanged-pptx")
            first = register_native_text_style(root, root / "source.png", {})
            second = register_native_text_style(root, root / "source.png", {})
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "unchanged")
            self.assertEqual(first["owner_decisions"], [])
            self.assertEqual((output / "editable.pptx").read_bytes(), b"unchanged-pptx")
            self.assertTrue((root / "native-text-style-registration/decision.json").is_file())

    def test_single_line_monospace_trigger_and_box_scale(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Image.new("RGB", (160, 100), "white")
            preview = source.copy()
            ImageDraw.Draw(source).rectangle((20, 30, 89, 39), fill="black")
            ImageDraw.Draw(preview).rectangle((20, 30, 49, 39), fill="black")
            source.save(root / "source.png")
            preview.save(root / "preview.png")
            owner = {"id": "label/text", "source_label_id": "label", "kind": "text",
                     "text": "Retrieval", "bbox": [15, 25, 90, 40],
                     "style": {"font_size": 40, "font_family": "Courier New",
                               "bold": True, "color": "#080904"}}
            scene = {"canvas": {"width": 160, "height": 100}, "elements": [owner]}
            rows = single_line_monospace_ink_candidates(
                scene, root / "source.png", root / "preview.png")
            self.assertEqual([item["id"] for item in rows], ["label/text"])
            self.assertEqual(rows[0]["ink_ratio"], round(300 / 700, 6))
            proposal = scaled_monospace_scene(scene, "label/text", 45, 3, 2)
            self.assertEqual(proposal["elements"][0]["bbox"], [18, 16, 90, 54])
            self.assertEqual(proposal["elements"][0]["text"], "Retrieval")
            self.assertEqual(owner["bbox"], [15, 25, 90, 40])


if __name__ == "__main__":
    unittest.main()
