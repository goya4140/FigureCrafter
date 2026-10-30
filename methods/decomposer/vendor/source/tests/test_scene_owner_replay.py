from __future__ import annotations

import unittest

from tools.probe_scene_owner_replay import replayed_scene


class SceneOwnerReplayTest(unittest.TestCase):
    def test_replays_only_named_owner_and_preserves_source(self):
        baseline = {"canvas": {"width": 100, "height": 100}, "elements": [
            {"id": "a/text", "kind": "text", "source_label_id": "a",
             "text": "A", "bbox": [1, 1, 20, 10]},
            {"id": "b/text", "kind": "text", "source_label_id": "b",
             "text": "B", "bbox": [30, 1, 20, 10]},
        ]}
        donor = {"canvas": baseline["canvas"], "elements": [
            {**baseline["elements"][0], "bbox": [2, 2, 20, 10]},
            {**baseline["elements"][1], "bbox": [40, 1, 20, 10]},
        ]}
        candidate = replayed_scene(baseline, donor, ["a/text"])
        self.assertEqual(candidate["elements"][0]["bbox"], [2, 2, 20, 10])
        self.assertEqual(candidate["elements"][1], baseline["elements"][1])
        self.assertEqual(baseline["elements"][0]["bbox"], [1, 1, 20, 10])
        changed_text = {"canvas": donor["canvas"], "elements": [
            {**donor["elements"][0], "text": "wrong"}, donor["elements"][1],
        ]}
        with self.assertRaisesRegex(ValueError, "source identity changed"):
            replayed_scene(baseline, changed_text, ["a/text"])


if __name__ == "__main__":
    unittest.main()
