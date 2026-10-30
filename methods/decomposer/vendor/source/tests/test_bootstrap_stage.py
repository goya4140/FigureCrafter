from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from bootstrap_stage import _append_lineage, _normalized_config, _tree_manifest


class BootstrapStageTest(unittest.TestCase):
    def test_tree_manifest_is_content_deterministic(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-bootstrap-tree-") as name:
            root = Path(name)
            (root / "a").mkdir()
            (root / "a" / "x.txt").write_text("x")
            (root / "b.txt").write_text("b")
            first = _tree_manifest(root, [root / "a", root / "b.txt"])
            second = _tree_manifest(root, [root / "b.txt", root / "a"])
            self.assertEqual(first, second)
            self.assertEqual(first["file_count"], 2)
            self.assertEqual(len(first["tree_sha256"]), 64)

    def test_normalized_config_ignores_only_rebound_knowledge_paths(self):
        left = {
            "model": {"name": "x"},
            "knowledge": {"path": "/old"},
            "refinement": {"knowledge_path": "/old", "max_rounds": 3},
        }
        right = {
            "model": {"name": "x"},
            "knowledge": {"path": "/new"},
            "refinement": {"knowledge_path": "/new", "max_rounds": 3},
        }
        self.assertEqual(_normalized_config(left), _normalized_config(right))
        right["refinement"]["max_rounds"] = 2
        self.assertNotEqual(_normalized_config(left), _normalized_config(right))

    def test_lineage_appends_successor_and_terminal_observation_without_mutating_prior(self):
        with tempfile.TemporaryDirectory(prefix="decomposer-lineage-") as name:
            root = Path(name)
            (root / "training-identity.json").write_text("{}")
            (root / "progress.json").write_text("{}")
            predecessor = root / "successors" / "v1"
            predecessor.mkdir(parents=True)
            (predecessor / "progress.json").write_text(json.dumps({
                "status": "stopped_early", "stop_event": {"reason": "hard_validation_failure"},
            }))
            (predecessor / "bootstrap-import.json").write_text("{}")
            first = _append_lineage(root, predecessor, 7)
            (root / "lineage.json").write_text(json.dumps(first))
            prior = json.loads(json.dumps(first["successors"][0]))
            destination = root / "successors" / "v2"
            destination.mkdir()
            (destination / "bootstrap-import.json").write_text("{}")
            second = _append_lineage(root, destination, 8)
            self.assertEqual(second["successors"][0], prior)
            self.assertEqual(second["successors"][1]["next_sample_index"], 9)
            self.assertEqual(second["events"][-1]["status"], "stopped_early")


if __name__ == "__main__":
    unittest.main()
