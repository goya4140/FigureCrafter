from __future__ import annotations

import copy
import unittest

from decomposer.text_tracking_registration import apply, eligible


class TextTrackingRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.text = "A long native editable paragraph with source identity.\nSecond line of the same paragraph.\nThird line."
        self.element = {
            "id": "prompt/text", "source_label_id": "prompt", "kind": "text",
            "text": self.text, "bbox": [10, 20, 350, 100],
            "style": {"color": "#111111", "font_size": 21},
        }
        self.scene = {"canvas": {"width": 400, "height": 200}, "elements": [self.element]}

    def test_eligible_and_apply_preserve_native_text_identity(self):
        original = copy.deepcopy(self.scene)
        rows = eligible(self.scene)
        self.assertEqual([row["id"] for row in rows], ["prompt/text"])
        candidate = apply(self.scene, rows, 5.0)
        self.assertEqual(candidate["elements"][0]["style"]["char_spacing"], 5.0)
        self.assertEqual(candidate["elements"][0]["text"], self.text)
        self.assertEqual(candidate["elements"][0]["source_label_id"], "prompt")
        self.assertEqual(self.scene, original)

    def test_controls_are_ineligible(self):
        for change in (
            {"runs": [{"text": self.text}]},
            {"bbox": [10, 20, 290, 100]},
            {"text": "Short text"},
            {"style": {"color": "#AA0000"}},
            {"style": {"color": "#111111", "char_spacing": 2.0}},
        ):
            scene = copy.deepcopy(self.scene)
            scene["elements"][0].update(change)
            self.assertEqual(eligible(scene), [])

    def test_apply_rejects_identity_drift(self):
        rows = eligible(self.scene)
        changed = copy.deepcopy(self.scene)
        changed["elements"][0]["text"] += "!"
        with self.assertRaises(ValueError):
            apply(changed, rows, 5.0)
        with self.assertRaises(ValueError):
            apply(self.scene, rows, 9.0)


if __name__ == "__main__":
    unittest.main()
