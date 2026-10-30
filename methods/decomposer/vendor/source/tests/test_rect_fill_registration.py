from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from decomposer.rect_fill_registration import apply, measure


class RectFillRegistrationTest(unittest.TestCase):
    def test_source_mode_changes_only_native_rect_fill(self):
        scene = {"canvas": {"width": 120, "height": 100}, "elements": [
            {"id": "panel/body", "source_label_id": "panel", "kind": "rect",
             "bbox": [10, 10, 80, 60], "style": {"fill": "#E7E2F3"}},
            {"id": "label/text", "source_label_id": "label", "kind": "text",
             "bbox": [20, 20, 30, 20], "text": "Editable", "style": {"font_size": 20}},
        ], "z_order": ["panel/body", "label/text"]}
        with tempfile.TemporaryDirectory(prefix="rect-fill-source-mode-") as name:
            source, preview = Path(name) / "source.png", Path(name) / "preview.png"
            for path, color in ((source, "#E7E3F4"), (preview, "#E7E2F3")):
                image = Image.new("RGB", (120, 100), "white")
                ImageDraw.Draw(image).rectangle((10, 10, 89, 69), fill=color)
                image.save(path)
            proposals = measure(scene, source, preview)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["old_fill"], "#E7E2F3")
        self.assertEqual(proposals[0]["new_fill"], "#E7E3F4")
        self.assertGreater(proposals[0]["source_mode_fraction"], .99)
        candidate = apply(scene, proposals)
        self.assertEqual(candidate["elements"][0]["style"]["fill"], "#E7E3F4")
        self.assertEqual(candidate["elements"][1], scene["elements"][1])
        self.assertEqual(scene["elements"][0]["style"]["fill"], "#E7E2F3")

    def test_no_change_when_source_matches_render(self):
        scene = {"canvas": {"width": 120, "height": 100}, "elements": [
            {"id": "panel/body", "source_label_id": "panel", "kind": "rect",
             "bbox": [10, 10, 80, 60], "style": {"fill": "#E7E2F3"}},
        ]}
        with tempfile.TemporaryDirectory(prefix="rect-fill-control-") as name:
            image_path = Path(name) / "matching.png"
            Image.new("RGB", (120, 100), "#E7E2F3").save(image_path)
            self.assertEqual(measure(scene, image_path, image_path), [])


if __name__ == "__main__":
    unittest.main()
