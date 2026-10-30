from __future__ import annotations

import unittest

from audit_first100 import registration_assessment_region


class AuditOwnerRegionTest(unittest.TestCase):
    def test_legacy_measurement_was_full_canvas(self):
        before = {"canvas": {"width": 100, "height": 80},
                  "elements": [{"id": "owner", "bbox": [10, 10, 20, 20]}]}
        after = {"canvas": before["canvas"],
                 "elements": [{"id": "owner", "bbox": [14, 12, 20, 20]}]}
        self.assertEqual(
            registration_assessment_region((0, 6, 18), before, after, ["owner"], (100, 80)),
            [0, 0, 100, 80],
        )

    def test_candidate_measurement_covers_old_and_new_owner(self):
        before = {"canvas": {"width": 100, "height": 80},
                  "elements": [{"id": "owner", "bbox": [10, 10, 20, 20]}]}
        after = {"canvas": before["canvas"],
                 "elements": [{"id": "owner", "bbox": [14, 12, 20, 20]}]}
        self.assertEqual(
            registration_assessment_region((0, 6, 19), before, after, ["owner"], (100, 80)),
            [10, 10, 24, 22],
        )


if __name__ == "__main__":
    unittest.main()
