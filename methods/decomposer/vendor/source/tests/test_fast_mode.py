from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from decomposer.model import ModelExecutionBlocked, _codex_tier_arguments, invoke_json


class FastModeTests(unittest.TestCase):
    def test_tier_requires_both_explicit_settings(self) -> None:
        self.assertEqual(_codex_tier_arguments({}), [])
        self.assertEqual(
            _codex_tier_arguments({"service_tier": "fast", "fast_mode": True}),
            ["-c", 'service_tier="fast"', "-c", "features.fast_mode=true"],
        )
        for incomplete in ({"service_tier": "fast"}, {"fast_mode": True},
                           {"service_tier": "fast", "fast_mode": False}):
            with self.assertRaises(ValueError):
                _codex_tier_arguments(incomplete)

    def test_invocation_passes_fast_flags_despite_ignored_user_config(self) -> None:
        captured: list[str] = []

        def fake_run(arguments: list[str], input_text: str, timeout: float,
                     environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
            captured.extend(arguments)
            Path(arguments[arguments.index("-o") + 1]).write_text("{}")
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary, patch(
            "decomposer.model._run_model_process", side_effect=fake_run
        ):
            result, metadata = invoke_json(
                "recognition", "test", {}, {"type": "object"},
                Path(temporary) / "source.png",
                {"model": {"command": "codex", "name": "gpt-6-astra",
                           "effort": "medium", "service_tier": "fast", "fast_mode": True}},
                Path(temporary) / "call",
            )
            persisted = json.loads((Path(temporary) / "call" / "metadata.json").read_text())
        self.assertEqual(result, {})
        self.assertEqual(metadata["status"], "complete")
        self.assertEqual(persisted["service_tier_requested"], "fast")
        self.assertTrue(persisted["fast_mode_requested"])
        self.assertIn("--ignore-user-config", captured)
        self.assertIn('service_tier="fast"', captured)
        self.assertIn("features.fast_mode=true", captured)

    def test_fast_tier_failure_stops_instead_of_content_fallback(self) -> None:
        def rejected(arguments: list[str], input_text: str, timeout: float,
                     environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(arguments, 2, "", "Fast tier unavailable")

        with tempfile.TemporaryDirectory() as temporary, patch(
            "decomposer.model._run_model_process", side_effect=rejected
        ):
            call_dir = Path(temporary) / "call"
            with self.assertRaises(ModelExecutionBlocked):
                invoke_json(
                    "recognition", "test", {}, {"type": "object"},
                    Path(temporary) / "source.png",
                    {"model": {"service_tier": "fast", "fast_mode": True}}, call_dir,
                )
            metadata = json.loads((call_dir / "metadata.json").read_text())
            self.assertEqual(metadata["status"], "execution_blocked")
            self.assertFalse(metadata["fallback_used"])


if __name__ == "__main__":
    unittest.main()
