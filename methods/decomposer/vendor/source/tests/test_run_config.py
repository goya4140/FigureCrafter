from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run import merge


class RunConfigTest(unittest.TestCase):
    def test_model_profile_deep_merges_without_dropping_base_config(self):
        base = {"model": {"backend": "codex-cli", "timeout_seconds": 1800}, "validation": {"minimum_pixel_similarity": 0.94}}
        profile = {"model": {"name": "gpt-5.6-terra", "effort": "medium"}}
        result = merge(base, profile)
        self.assertEqual(result["model"]["backend"], "codex-cli")
        self.assertEqual(result["model"]["name"], "gpt-5.6-terra")
        self.assertEqual(result["validation"]["minimum_pixel_similarity"], 0.94)
        self.assertNotIn("name", base["model"])


if __name__ == "__main__":
    unittest.main()
