from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from decomposer.flat_panel_registration import apply, measure


class FlatPanelRegistrationTest(unittest.TestCase):
    def test_source_color_and_bounded_extent_preserve_native_identity(self):
        scene = {"canvas": {"width": 220, "height": 220}, "elements": [
            {"id": "panel/panel", "source_label_id": "panel", "kind": "rect",
             "bbox": [30, 20, 100, 140],
             "style": {"fill": "#DDEEFE", "stroke": "none", "shadow": {"type": "outer"}}},
            {"id": "label/text", "source_label_id": "label", "kind": "text",
             "bbox": [40, 30, 20, 15], "text": "Editable", "style": {"font_size": 15}},
        ], "z_order": ["panel/panel", "label/text"]}
        with tempfile.TemporaryDirectory(prefix="flat-panel-source-") as name:
            source = Path(name) / "source.png"
            image = Image.new("RGB", (220, 220), "white")
            ImageDraw.Draw(image).rectangle((33, 20, 133, 164), fill="#DDEEFF")
            image.save(source)
            proposals = measure(scene, source)
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertEqual(proposal["source_fill"], "#DDEEFF")
        self.assertEqual((proposal["source_left"], proposal["source_right"], proposal["source_bottom"]),
                         (33, 133, 165))
        variant = next(row for row in proposal["variants"] if row["name"] == "measured-extent-minus-0")
        candidate = apply(scene, proposal, variant)
        self.assertEqual(candidate["elements"][0]["bbox"], [33.0, 20, 101.0, 145.0])
        self.assertEqual(candidate["elements"][0]["style"]["fill"], "#DDEEFF")
        self.assertEqual(candidate["elements"][1], scene["elements"][1])
        self.assertEqual(scene["elements"][0]["bbox"], [30, 20, 100, 140])

    def test_panel_without_native_shadow_is_not_eligible(self):
        scene = {"canvas": {"width": 120, "height": 120}, "elements": [
            {"id": "panel/panel", "source_label_id": "panel", "kind": "rect",
             "bbox": [30, 20, 40, 70], "style": {"fill": "#DDEEFE", "stroke": "none"}},
        ]}
        with tempfile.TemporaryDirectory(prefix="flat-panel-control-") as name:
            source = Path(name) / "source.png"
            Image.new("RGB", (120, 120), "#DDEEFF").save(source)
            self.assertEqual(measure(scene, source), [])


if __name__ == "__main__":
    unittest.main()
