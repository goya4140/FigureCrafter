from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from decomposer.common import sha256
from tools.audit_memory_artifacts import audit, candidate_paths


class MemoryArtifactAuditTest(unittest.TestCase):
    def test_historical_path_conventions_and_stale_hash(self):
        with tempfile.TemporaryDirectory(prefix="memory-artifact-audit-") as name:
            project = Path(name)
            knowledge = project / "snapshot" / "knowledge"
            (knowledge / "memory").mkdir(parents=True)
            (knowledge / "evidence").mkdir()
            (project / "experiments").mkdir()
            (project / "Decomposer").mkdir()
            proof = project / "experiments" / "proof.json"
            code = project / "Decomposer" / "policy.py"
            proof.write_text('{"measured":true}')
            code.write_text("VERSION = 2")
            memory = {"memory_id": "representation-example", "status": "downstreamed",
                      "evidence_ids": ["e1"]}
            (knowledge / "memory" / "representation-example.json").write_text(json.dumps(memory))
            evidence = {"artifact_refs": [
                {"path": "experiments/proof.json", "sha256": sha256(proof)},
                {"path": "../experiments/proof.json", "sha256": sha256(proof)},
                {"path": "policy.py", "sha256": "0" * 64},
                {"path": "experiments/missing.json", "sha256": "0" * 64},
            ]}
            (knowledge / "evidence" / "e1.json").write_text(json.dumps(evidence))
            report = audit(knowledge, project)
            self.assertEqual(report["downstreamed_memory_count"], 1)
            self.assertEqual(report["linked_evidence_count"], 1)
            self.assertEqual(report["status_counts"], {
                "hash_match": 2, "hash_mismatch": 1, "missing_artifact": 1})
            self.assertEqual(report["affected_memory_ids"], ["representation-example"])
            self.assertEqual(candidate_paths(str(proof), project), [proof.resolve()])


if __name__ == "__main__":
    unittest.main()
