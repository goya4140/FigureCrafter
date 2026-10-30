from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from decomposer.validation import _editable_text_mismatches
from decomposer.source_line_registration import apply, propose, split_runs


class SplitTextIdentityTest(unittest.TestCase):
    def test_source_measured_line_split_preserves_exact_text_and_shape_order(self):
        scene = {"canvas": {"width": 240, "height": 120}, "z_order": ["paragraph/text"],
                 "elements": [{"id": "paragraph/text", "source_label_id": "paragraph", "kind": "text",
                               "editable": True, "text": "A\nB\nC", "bbox": [10, 10, 210, 95],
                               "style": {"font_size": 24}}]}
        with tempfile.TemporaryDirectory(prefix="source-line-proposal-") as name:
            reference, preview = Path(name) / "reference.png", Path(name) / "preview.png"
            for path, starts in ((reference, [12, 40, 68]), (preview, [17, 39, 62])):
                image = Image.new("RGB", (240, 120), "white")
                draw = ImageDraw.Draw(image)
                for top in starts:
                    draw.rectangle([20, top, 180, top + 11], fill="black")
                image.save(path)
            proposals = propose(scene, reference, preview)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["source_rows"], [12, 40, 68])
        self.assertEqual(proposals[0]["row_drift"], [5, -1, -6])
        candidate = apply(scene, proposals[0])
        self.assertEqual([item["text"] for item in candidate["elements"]], ["A", "B", "C"])
        self.assertEqual(candidate["z_order"], ["paragraph/text/line-1", "paragraph/text/line-2", "paragraph/text/line-3"])
        self.assertEqual(_editable_text_mismatches({"objects": [{"id": "paragraph", "category": "text", "text": "A\nB\nC"}]}, candidate), [])

    def test_split_runs_rejects_source_visible_drift(self):
        with self.assertRaisesRegex(ValueError, "identical visible and source"):
            split_runs({"text": "x", "runs": [{"text": "x", "source_text": "_x"}]})

    def test_native_line_shapes_preserve_recognition_line_breaks(self):
        recognition = {"objects": [{"id": "paragraph", "category": "text", "text": "A\nB\nC"}]}
        lines = [
            {"source_label_id": "paragraph", "kind": "text", "editable": True, "text": text, "line_break_after": index < 2}
            for index, text in enumerate("ABC")
        ]
        self.assertEqual(_editable_text_mismatches(recognition, {"elements": lines}), [])
        lines[1]["line_break_after"] = False
        self.assertEqual(len(_editable_text_mismatches(recognition, {"elements": lines})), 1)


if __name__ == "__main__":
    unittest.main()
