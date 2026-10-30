from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from decomposer.common import write_json
from decomposer.fixture_provenance import attest_replay, safe_asset_decision_delta, verify_replay


class FixtureProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.recognition = {"objects": [{"id": "person", "category": "image",
                                         "bbox": [10, 20, 100, 100]}]}
        self.old = {
            "policy": {"max_area_ratio": .35},
            "summary": {"localized_raster_count": 1, "native_count": 0},
            "decisions": [{
                "label_id": "person", "decision": "localized_raster",
                "source_candidate_id": "sam01", "source_basis": "sam_candidate",
                "approved_bbox": [30, 40, 30, 30], "area_ratio": .01,
                "reason_codes": ["sam_visual_leaf"],
            }],
        }
        self.new = {
            "policy": {"max_area_ratio": .35, "minimum_sam_object_coverage": .6},
            "summary": self.old["summary"],
            "decisions": [{
                "label_id": "person", "decision": "localized_raster",
                "source_candidate_id": "recognition:person", "source_basis": "recognition_bbox",
                "approved_bbox": [10, 20, 100, 100], "area_ratio": .1,
                "reason_codes": ["sam_insufficient_object_coverage", "recognition_bbox_fallback"],
            }],
        }

    def test_safe_sam_to_recognition_fallback(self):
        self.assertEqual(safe_asset_decision_delta(self.old, self.new, self.recognition), ["person"])

    def test_policy_only_change_reuses_proposal(self):
        new = copy.deepcopy(self.old)
        new["decisions"][0]["approved_bbox"] = [10, 20, 90, 90]
        old = copy.deepcopy(new)
        old["policy"].pop("minimum_sam_object_coverage", None)
        new["policy"]["minimum_sam_object_coverage"] = .6
        self.assertEqual(safe_asset_decision_delta(old, new, self.recognition), [])

    def test_rejects_non_image_or_weaker_gate(self):
        recognition = copy.deepcopy(self.recognition)
        recognition["objects"][0]["category"] = "shape"
        with self.assertRaisesRegex(ValueError, "not the recognized image leaf"):
            safe_asset_decision_delta(self.old, self.new, recognition)
        new = copy.deepcopy(self.new)
        new["policy"]["minimum_sam_object_coverage"] = .2
        with self.assertRaisesRegex(ValueError, "stricter SAM coverage gate"):
            safe_asset_decision_delta(self.old, new, self.recognition)

    def test_attestation_rejects_tampered_source_response(self):
        with tempfile.TemporaryDirectory(prefix="fixture-provenance-") as name:
            root = Path(name)
            source, replay = root / "source", root / "replay"
            for run in (source, replay):
                (run / "input").mkdir(parents=True)
                (run / "input" / "normalized.png").write_bytes(b"same-source-image")
                (run / "recognition").mkdir()
                write_json(run / "recognition" / "recognition.json", self.recognition)
            old_component = [{"label_id": "person", "primitives": [
                {"kind": "image", "asset_id": "old", "bbox": [0, 0, 100, 100]}]}]
            new_component = copy.deepcopy(old_component)
            new_component[0]["primitives"][0]["asset_id"] = "new"
            inputs = {
                "representation": ({"recognition": self.recognition, "asset_decisions": self.old},
                                   {"recognition": self.recognition, "asset_decisions": self.new}),
                "layout": ({"canvas": [100, 100], "components": old_component},
                           {"canvas": [100, 100], "components": new_component}),
            }
            for stage, (old_input, new_input) in inputs.items():
                proposal = {"stage": stage, "unused_optional": None}
                normalized = {"stage": stage}
                (source / stage).mkdir()
                (source / "calls" / stage).mkdir(parents=True)
                (replay / stage).mkdir()
                (replay / "calls" / stage).mkdir(parents=True)
                write_json(source / stage / "model-output.json", normalized)
                (source / "calls" / stage / "response.txt").write_text(json.dumps(proposal))
                write_json(source / "calls" / stage / "input.json", old_input)
                write_json(source / "calls" / stage / "metadata.json",
                           {"stage": stage, "status": "complete", "fallback_used": False})
                write_json(replay / stage / "model-output.json", normalized)
                write_json(replay / "calls" / stage / "response.json", normalized)
                write_json(replay / "calls" / stage / "input.json", new_input)
                write_json(replay / "calls" / stage / "metadata.json",
                           {"stage": stage, "status": "fixture", "fallback_used": False})
            self.assertTrue(attest_replay(source, replay)["stages"])
            self.assertEqual(verify_replay(replay)["changed_asset_labels"], ["person"])
            (source / "calls" / "layout" / "response.txt").write_text("{}")
            with self.assertRaisesRegex(ValueError, "source evidence hash mismatch"):
                verify_replay(replay)


if __name__ == "__main__":
    unittest.main()
