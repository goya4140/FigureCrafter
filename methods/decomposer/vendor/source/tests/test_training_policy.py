from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decomposer.evolution import TERMINAL_PROCESSED, freeze_checkpoint, stage_spec
from decomposer.common import write_json
from train_first100 import (
    EXECUTION_BLOCKER_STATUSES, _add_checkpoint_blocker, _manifest_indexed_samples,
    _record_sample, _resume_identity_migration,
)
from adopt_quality_stop import LEGACY_QUALITY_REASONS


class TrainingPolicyTest(unittest.TestCase):
    def test_target_manifest_preserves_original_indices(self):
        samples = [{"sample_index": 29, "sample_id": "s29"},
                   {"sample_index": 84, "sample_id": "s84"}]
        self.assertEqual([index for index, _ in _manifest_indexed_samples({"samples": samples}, 100)], [29, 84])
        with self.assertRaises(ValueError):
            _manifest_indexed_samples({"samples": list(reversed(samples))}, 100)

    def test_only_quality_stop_reasons_are_adoptable(self):
        self.assertIn("hard_validation_failure", LEGACY_QUALITY_REASONS)
        self.assertNotIn("execution_failure", LEGACY_QUALITY_REASONS)

    def test_quality_failure_is_processed_but_execution_failure_is_not(self):
        self.assertIn("validation_failed", TERMINAL_PROCESSED)
        self.assertNotIn("failed", TERMINAL_PROCESSED)
        self.assertIn("failed", EXECUTION_BLOCKER_STATUSES)

    def test_retry_replaces_current_row_and_preserves_attempt(self):
        progress = {"samples": [{"sample_index": 9, "sample_id": "s9", "status": "failed"}]}
        _record_sample(progress, {"sample_index": 9, "sample_id": "s9", "status": "render_success"})
        self.assertEqual(len(progress["samples"]), 1)
        self.assertEqual(progress["samples"][0]["status"], "render_success")
        self.assertEqual(progress["attempt_history"][0]["status"], "failed")

    def test_checkpoint_blocker_is_deduplicated_and_attached_to_row(self):
        progress = {"samples": []}
        row = {"sample_index": 8, "sample_id": "s8", "status": "validation_failed"}
        event = {"reason": "quality_validation_failure", "sample_index": 8, "sample_id": "s8"}
        _add_checkpoint_blocker(progress, event, row)
        _add_checkpoint_blocker(progress, event, row)
        self.assertEqual(len(progress["checkpoint_blockers"]), 1)
        self.assertTrue(row["checkpoint_blocking"])

    def test_resume_identity_migration_is_narrowly_scoped(self):
        prior = {"manifest_sha256": "m", "source_hashes": {"train_first100.py": "old"}}
        current = {
            "manifest_sha256": "m", "execution_failure_policy": "continue",
            "source_hashes": {"train_first100.py": "new"},
        }
        migration = _resume_identity_migration(prior, current)
        self.assertEqual(migration["changed_source_files"], ["train_first100.py"])
        changed_method = dict(current, manifest_sha256="different")
        self.assertIsNone(_resume_identity_migration(prior, changed_method))
        changed_other_source = dict(current, source_hashes={"train_first100.py": "new", "decomposer/model.py": "new"})
        self.assertIsNone(_resume_identity_migration(prior, changed_other_source))

    def test_frozen_checkpoint_is_blocked_when_quality_event_exists(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="decomposer-policy-") as name:
            root = Path(name)
            write_json(root / "training-identity.json", {"id": "x"})
            write_json(root / "frozen-config.json", {"id": "x"})
            progress = {
                "samples": [{"sample_index": 1, "sample_id": "s1", "status": "validation_failed"}],
                "checkpoint_blockers": [{"reason": "quality_validation_failure", "sample_index": 1, "sample_id": "s1"}],
            }
            write_json(root / "progress.json", progress)
            checkpoint = freeze_checkpoint(root, progress, stage_spec(1))
            self.assertEqual(checkpoint["checkpoint_status"], "blocked")
            self.assertFalse(checkpoint["checkpoint_eligible"])


if __name__ == "__main__":
    unittest.main()
