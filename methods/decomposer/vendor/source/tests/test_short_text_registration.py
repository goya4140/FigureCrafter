from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from decomposer.short_text_registration import align, apply, detect, height_scene


class ShortTextRegistrationTest(unittest.TestCase):
    def test_source_ink_deficit_selects_collapsed_native_text_and_preserves_text(self):
        scene = {"canvas": {"width": 160, "height": 100}, "elements": [
            {"id": "label/text", "source_label_id": "label", "kind": "text",
             "text": "Editable", "editable": True, "bbox": [10, 10, 120, 20],
             "style": {"font_size": 30, "color": "#315DAD"}},
        ], "z_order": ["label/text"]}
        color = "#315DAD"
        with tempfile.TemporaryDirectory(prefix="short-text-evidence-") as name:
            root = Path(name)
            source, preview, expanded = (root / file for file in ("source.png", "preview.png", "expanded.png"))
            for path, box in ((source, [30, 15, 75, 25]),
                              (preview, [45, 17, 50, 21]),
                              (expanded, [32, 18, 77, 28])):
                image = Image.new("RGB", (160, 100), "white")
                ImageDraw.Draw(image).rectangle(box, fill=color)
                image.save(path)
            detected = detect(scene, source, preview)
            self.assertEqual(len(detected), 1)
            self.assertLess(detected[0]["ink_ratio"], .4)
            taller = height_scene(scene, detected)
            self.assertEqual(taller["elements"][0]["bbox"], [10, 10, 120, 45.0])
            aligned = align(taller, detected, source, expanded)
            self.assertEqual((aligned[0]["shift_x"], aligned[0]["shift_y"]), (-2, -3))
            candidate = apply(scene, aligned[0])
            self.assertEqual(candidate["elements"][0]["text"], "Editable")
            self.assertEqual(candidate["elements"][0]["bbox"], [8, 7, 120, 45.0])

    def test_source_ink_without_collapse_is_not_selected(self):
        scene = {"canvas": {"width": 160, "height": 100}, "elements": [
            {"id": "label/text", "source_label_id": "label", "kind": "text",
             "text": "Editable", "bbox": [10, 10, 120, 20],
             "style": {"font_size": 30, "color": "#315DAD"}},
        ]}
        with tempfile.TemporaryDirectory(prefix="short-text-control-") as name:
            source, preview = Path(name) / "source.png", Path(name) / "preview.png"
            image = Image.new("RGB", (160, 100), "white")
            ImageDraw.Draw(image).rectangle([30, 15, 75, 25], fill="#315DAD")
            image.save(source)
            image.save(preview)
            self.assertEqual(detect(scene, source, preview), [])


if __name__ == "__main__":
    unittest.main()
