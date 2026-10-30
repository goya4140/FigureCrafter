from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from decomposer.common import write_json
from decomposer.evolution import freeze_checkpoint, issue_key, observation_report, stage_spec, write_observation


class EvolutionProtocolTest(unittest.TestCase):
    def test_formal_stage_boundaries_are_fixed(self):
        self.assertEqual(stage_spec(1), {"stage": 1, "start": 1, "end": 25, "focus": "problem-map-and-observability"})
        self.assertEqual(stage_spec(4)["start"], 76)
        self.assertEqual(stage_spec(4)["end"], 100)
        with self.assertRaises(ValueError):
            stage_spec(5)

    def test_observation_report_is_read_only_and_clusters_exact_owner_tuple(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-evolution-") as temporary_name:
            root = Path(temporary_name)
            rows = []
            for index in (1, 2):
                sample_id = f"sample-{index}"
                run = root / "runs" / sample_id
                write_json(run / "output/validation.json", {
                    "failures": [{"owner_stage": "representation", "code": "text-overflow", "label_ids": ["title"]}],
                    "checks": {"styled_text_run_export": {"passed": True}},
                })
                write_json(run / "representation/representation.json", {
                    "components": [{"primitives": [{
                        "id": "title/text", "kind": "text", "text": "IModel",
                        "runs": [
                            {"text": "I", "source_text": "I", "script": "normal"},
                            {"text": "Model", "source_text": "_Model", "script": "subscript"},
                        ],
                    }]}],
                })
                write_json(run / "calls/representation/input.json", {
                    "applicable_memories": [{
                        "memory_id": "representation-literal-math-markup-in-text-primitive",
                        "status": "downstreamed",
                    }],
                })
                rows.append({
                    "sample_index": index, "sample_id": sample_id, "version": "0.5.7",
                    "status": "validation_failed", "seconds": 10 * index,
                    "refinement": {"accepted_rounds": 0, "history": []},
                })
            progress = {"samples": rows}
            report = observation_report(root, progress, stage_spec(1), observation_number=2)
            self.assertTrue(report["read_only"])
            self.assertFalse(report["policy"]["framework_mutation_allowed"])
            self.assertEqual(report["clusters"][0]["owner_stage"], "representation")
            self.assertEqual(report["clusters"][0]["mechanism"], "text-overflow")
            self.assertEqual(len(report["clusters"][0]["samples"]), 2)
            self.assertEqual(report["efficiency"]["error_recurrence_rate"], 1.0)
            self.assertEqual(report["efficiency"]["memory_injection_rate"], 1.0)
            self.assertEqual(report["efficiency"]["memory_reuse_rate"], 1.0)
            self.assertEqual(report["efficiency"]["first_pass_memory_reuse_rate"], 1.0)
            self.assertEqual(len(report["memory_reuse_evidence"]), 2)
            self.assertEqual(json.loads(issue_key({"owner_stage": "representation", "mechanism": "text-overflow"}))["owner_stage"], "representation")

    def test_observation_uses_persisted_assessment_for_refinement_net_gain(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-evolution-assessment-") as temporary_name:
            root = Path(temporary_name)
            run = root / "runs/sample-1"
            write_json(run / "refinement/round-001/decision.json", {
                "owner_stage": "layout", "mechanism": "text-scale-underestimation",
                "affected_label_ids": ["title"], "affected_primitive_ids": ["title/text"],
                "evaluation_region": [1, 2, 3, 4], "evaluation_region_source": "affected_scene_owners",
            })
            write_json(run / "refinement/round-001/assessment.json", {
                "accepted": True, "local_pixel_delta": 0.01,
                "local_edge_delta": 0.02, "global_pixel_delta": 0.001,
            })
            progress = {"samples": [{
                "sample_index": 1, "sample_id": "sample-1", "version": "0.5.9",
                "status": "render_success", "seconds": 10,
                "refinement": {"accepted_rounds": 1, "history": [{"accepted": True}]},
            }]}
            report = observation_report(root, progress, stage_spec(1), observation_number=1)
            self.assertEqual(report["efficiency"]["refinement_net_local_pixel_delta"], 0.01)
            self.assertEqual(report["efficiency"]["refinement_net_local_edge_delta"], 0.02)

    def test_checkpoint_hashes_observations_and_deliverables(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-checkpoint-") as temporary_name:
            root = Path(temporary_name)
            write_json(root / "training-identity.json", {"id": "frozen"})
            write_json(root / "frozen-config.json", {"config": "frozen"})
            write_json(root / "knowledge/memory/example.json", {"memory_id": "example", "status": "downstreamed"})
            write_json(root / "sample-001-audit.json", {"passed": True})
            sample = root / "runs/sample-1"
            write_json(sample / "status.json", {"status": "render_success"})
            write_json(sample / "output/validation.json", {"passed": True})
            write_json(sample / "output/page-manifest.json", {"build": {}})
            (sample / "output/editable.pptx").write_bytes(b"pptx")
            progress = {"samples": [{"sample_index": 1, "sample_id": "sample-1", "status": "render_success"}]}
            write_json(root / "progress.json", progress)
            write_observation(root, progress, stage_spec(1), 1)
            checkpoint = freeze_checkpoint(root, progress, stage_spec(1))
            self.assertTrue(checkpoint["frozen"])
            self.assertTrue(checkpoint["all_terminal_success"])
            self.assertEqual(len(checkpoint["observation_reports"]), 1)
            self.assertEqual(len(checkpoint["artifacts"]), 5)
            self.assertEqual(checkpoint["knowledge_snapshot"]["file_count"], 1)
            self.assertEqual(len(checkpoint["knowledge_snapshot"]["tree_sha256"]), 64)
            self.assertIsNotNone(checkpoint["frozen_config_sha256"])


if __name__ == "__main__":
    unittest.main()
