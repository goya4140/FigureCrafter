from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from decomposer.joint_shadow_text_registration import apply_shadow, shadow_owners


class JointShadowTextRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.scene = {"canvas": {"width": 500, "height": 500}, "elements": [
            {"id": "panel/a", "source_label_id": "panel", "kind": "rect",
             "bbox": [20, 20, 250, 250],
             "style": {"fill": "#F0F0F0", "shadow": {
                 "type": "outer", "color": "666666", "opacity": .25,
                 "blur": 20, "offset": 10, "angle": 45,
             }}},
        ]}

    def test_source_darkness_gates_native_shadow_candidate(self):
        with tempfile.TemporaryDirectory(prefix="joint-shadow-measure-") as directory:
            source_path = Path(directory) / "source.png"
            preview_path = Path(directory) / "preview.png"
            source = Image.new("RGB", (500, 500), "white")
            draw = ImageDraw.Draw(source)
            draw.rectangle((272, 120, 285, 170), fill=(120, 120, 120))
            source.save(source_path)
            Image.new("RGB", (500, 500), "white").save(preview_path)
            rows = shadow_owners(self.scene, source_path, preview_path)
            self.assertEqual([row["id"] for row in rows], ["panel/a"])
            self.assertGreater(rows[0]["right_luminance_gap"], 20)
            original = copy.deepcopy(self.scene)
            candidate = apply_shadow(self.scene, rows, 6)
            self.assertEqual(candidate["elements"][0]["style"]["shadow"]["offset"], 16.0)
            self.assertEqual(candidate["elements"][0]["style"]["shadow"]["color"], "000000")
            self.assertEqual(self.scene, original)
            changed = copy.deepcopy(self.scene)
            changed["elements"][0]["bbox"][0] += 1
            with self.assertRaises(ValueError):
                apply_shadow(changed, rows, 6)

    def test_no_dark_source_band_has_no_candidate(self):
        with tempfile.TemporaryDirectory(prefix="joint-shadow-control-") as directory:
            source_path = Path(directory) / "source.png"
            preview_path = Path(directory) / "preview.png"
            Image.new("RGB", (500, 500), "white").save(source_path)
            Image.new("RGB", (500, 500), "white").save(preview_path)
            self.assertEqual(shadow_owners(self.scene, source_path, preview_path), [])


if __name__ == "__main__":
    unittest.main()
