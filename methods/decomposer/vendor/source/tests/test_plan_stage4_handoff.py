from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.plan_stage4_handoff import plan


class PlanStage4HandoffTest(unittest.TestCase):
    def test_refuses_incomplete_stage_before_read_or_write(self):
        with tempfile.TemporaryDirectory() as name:
            batch = Path(name)
            with patch("tools.plan_stage4_handoff.verify", return_value={
                "identity_and_snapshot_consistent": True,
                "complete_coverage": False,
                "checkpoint_present": False,
                "stage": {"stage": 3},
            }):
                with self.assertRaisesRegex(ValueError, "25 consistent terminal rows"):
                    plan(batch)
            self.assertEqual(list(batch.iterdir()), [])

    def test_provisional_handoff_inherits_knowledge_without_claiming_quality(self):
        with tempfile.TemporaryDirectory() as name:
            batch = Path(name)
            (batch / "knowledge").mkdir()
            (batch / "checkpoint.json").write_text(json.dumps({
                "checkpoint_eligible": False,
                "knowledge_snapshot": {"tree_sha256": "tree-hash"},
            }))
            (batch / "training-identity.json").write_text("{}")
            config = {
                "knowledge": {"path": "/old/knowledge", "seed_path": "/old/seed"},
                "refinement": {"knowledge_path": "/old/knowledge", "max_rounds": 3},
                "validation": {"minimum_pixel_similarity": 0.94},
                "detectors": {"ocr": {"command": ["python", "/old/tools/apple_vision_ocr.py"]}},
            }
            (batch / "frozen-config.json").write_text(json.dumps(config))
            (batch / "progress.json").write_text(json.dumps({
                "status": "complete_checkpoint_blocked",
                "samples": [{"sample_index": n, "sample_id": f"sample-{n:03d}",
                             "status": "render_success"} for n in range(51, 76)],
                "checkpoint_blockers": [{"reason": "independent_audit_failed", "sample_index": 51}],
            }))
            snapshot = {
                "identity_and_snapshot_consistent": True,
                "complete_coverage": True,
                "checkpoint_present": True,
                "stage": {"stage": 3},
            }
            with (patch("tools.plan_stage4_handoff.verify", return_value=snapshot),
                  patch("tools.plan_stage4_handoff.audit_memory", return_value={
                      "memory_count": 2, "eligible_count": 0, "eligible_memory_ids": [],
                  }),
                  patch("tools.plan_stage4_handoff.audit_run", return_value={
                      "passed": False, "failures": [{"code": "rendered-native-text-ink-collapse"}],
                  })):
                result = plan(batch)
            self.assertEqual(result["quality_status"], "provisional")
            self.assertEqual(result["independent_audits_passed"], 0)
            self.assertEqual(result["independent_failure_codes"], {"rendered-native-text-ink-collapse": 25})
            self.assertEqual(result["stage4_config"]["evolution_handoff"]["unresolved_sample_indices"],
                             list(range(51, 76)))
            self.assertEqual(result["stage4_config"]["knowledge"]["seed_path"], str((batch / "knowledge").resolve()))
            self.assertNotIn("path", result["stage4_config"]["knowledge"])
            self.assertNotIn("knowledge_path", result["stage4_config"]["refinement"])
            self.assertEqual(result["stage4_config"]["validation"], config["validation"])
            self.assertEqual(Path(result["stage4_config"]["detectors"]["ocr"]["command"][1]).name,
                             "apple_vision_ocr.py")
            self.assertNotEqual(result["stage4_config"]["detectors"]["ocr"]["command"][1],
                                config["detectors"]["ocr"]["command"][1])
            self.assertEqual(json.loads((batch / "frozen-config.json").read_text()), config)


if __name__ == "__main__":
    unittest.main()
