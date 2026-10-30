from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from PIL import Image

from decomposer.common import sha256, write_json
from decomposer.post_short_text_registration import run


class PostShortTextRegistrationTest(unittest.TestCase):
    def test_noop_chain_resume_and_identity_drift(self):
        with tempfile.TemporaryDirectory(prefix="post-short-text-chain-") as name:
            root = Path(name)
            output = root / "output"
            output.mkdir()
            write_json(output / "scene.json", {
                "canvas": {"width": 100, "height": 60}, "elements": [], "z_order": [],
            })
            (output / "editable.pptx").write_bytes(b"unchanged-pptx")
            Image.new("RGB", (100, 60), "white").save(output / "preview.png")
            Image.new("RGB", (100, 60), "white").save(root / "reference.png")
            prior = root / "short-text-registration"
            prior.mkdir()
            write_json(prior / "decision.json", {"final_scene_sha256": sha256(output / "scene.json")})
            first = run(root, root / "reference.png", {})
            self.assertEqual(first["status"], "unchanged")
            self.assertFalse(first["accepted"])
            self.assertEqual(first["baseline_scene_sha256"], first["final_scene_sha256"])
            self.assertEqual(first, run(root, root / "reference.png", {}))
            write_json(output / "scene.json", {
                "canvas": {"width": 100, "height": 60},
                "elements": [{"id": "unrelated"}], "z_order": ["unrelated"],
            })
            with self.assertRaisesRegex(ValueError, "identity drift"):
                run(root, root / "reference.png", {})

    def test_requires_prior_short_text_evidence(self):
        with tempfile.TemporaryDirectory(prefix="post-short-text-prior-") as name:
            with self.assertRaisesRegex(ValueError, "requires short-text evidence"):
                run(Path(name), Path(name) / "reference.png", {})


if __name__ == "__main__":
    unittest.main()
