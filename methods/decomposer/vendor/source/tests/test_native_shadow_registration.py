from __future__ import annotations

import unittest

from decomposer.native_shadow_registration import apply, pairs, variants


class NativeShadowRegistrationTest(unittest.TestCase):
    def test_pair_proposal_preserves_native_panel_and_removes_only_proxy(self):
        scene = {"canvas": {"width": 200, "height": 120},
                 "elements": [
                     {"id": "card/shadow", "kind": "rect", "source_label_id": "card",
                      "bbox": [22, 22, 100, 60], "style": {"fill": "#AAAAAA", "stroke": "none"}},
                     {"id": "card/panel", "kind": "rect", "source_label_id": "card",
                      "bbox": [10, 10, 100, 60], "style": {"fill": "#E7E2F3", "stroke": "none"}},
                     {"id": "card/text", "kind": "text", "source_label_id": "card",
                      "bbox": [20, 20, 80, 30], "text": "Card", "style": {}},
                 ], "z_order": ["card/shadow", "card/panel", "card/text"]}
        found = pairs(scene)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["offset_x"], 12)
        recipes = variants(found)
        self.assertEqual(len(recipes), 3)
        self.assertEqual(recipes[-1]["shadow"]["blur"], 20)
        candidate = apply(scene, found, recipes[-1])
        self.assertEqual([item["id"] for item in candidate["elements"]], ["card/panel", "card/text"])
        self.assertEqual(candidate["z_order"], ["card/panel", "card/text"])
        self.assertEqual(candidate["elements"][0]["style"]["shadow"], recipes[-1]["shadow"])
        self.assertNotIn("shadow", scene["elements"][1]["style"])

    def test_incompatible_shadow_proxy_is_not_proposed(self):
        scene = {"elements": [
            {"id": "a/shadow", "kind": "rect", "source_label_id": "a",
             "bbox": [12, 12, 50, 50], "style": {"fill": "#AAAAAA", "stroke": "none"}},
            {"id": "a/panel", "kind": "rect", "source_label_id": "a",
             "bbox": [0, 0, 60, 50], "style": {"fill": "#EEEEEE", "stroke": "none"}},
        ]}
        self.assertEqual(pairs(scene), [])


if __name__ == "__main__":
    unittest.main()
