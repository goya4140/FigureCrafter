from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from decomposer.styled_multiline_registration import apply, propose


class StyledMultilineRegistrationTest(unittest.TestCase):
    def test_colored_multiline_translation_preserves_runs_and_source_text(self):
        scene = {"canvas": {"width": 120, "height": 100}, "elements": [
            {"id": "list/text", "source_label_id": "list", "kind": "text",
             "text": "Red\nText", "bbox": [20, 20, 70, 60],
             "runs": [{"text": "Red\n", "source_text": "Red\n", "style": {"color": "#C02030"}},
                      {"text": "Text", "source_text": "Text", "style": {"color": "#C02030"}}]},
        ]}
        with tempfile.TemporaryDirectory(prefix="styled-multiline-color-") as name:
            source, preview = Path(name) / "source.png", Path(name) / "preview.png"
            for path, left in ((source, 35), (preview, 25)):
                image = Image.new("RGB", (120, 100), "white")
                draw = ImageDraw.Draw(image)
                draw.rectangle((left, 30, left + 19, 41), fill="#C02030")
                draw.rectangle((left, 50, left + 19, 61), fill="#C02030")
                image.save(path)
            proposals = propose(scene, source, preview)
        self.assertEqual(len(proposals), 1)
        self.assertEqual((proposals[0]["shift_x"], proposals[0]["shift_y"]), (10, 0))
        candidate = apply(scene, proposals)
        self.assertEqual(candidate["elements"][0]["bbox"], [30, 20, 70, 60])
        self.assertEqual(candidate["elements"][0]["text"], "Red\nText")
        self.assertEqual(candidate["elements"][0]["runs"], scene["elements"][0]["runs"])
        self.assertEqual(scene["elements"][0]["bbox"], [20, 20, 70, 60])

    def test_unstyled_text_is_not_candidate(self):
        scene = {"canvas": {"width": 120, "height": 100}, "elements": [
            {"id": "list/text", "source_label_id": "list", "kind": "text",
             "text": "Red\nText", "bbox": [20, 20, 70, 60]},
        ]}
        with tempfile.TemporaryDirectory(prefix="styled-multiline-control-") as name:
            image_path = Path(name) / "blank.png"
            Image.new("RGB", (120, 100), "white").save(image_path)
            self.assertEqual(propose(scene, image_path, image_path), [])


if __name__ == "__main__":
    unittest.main()
