from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.text_registration import propose
from decomposer.text_registration import run as run_registration
from decomposer.common import write_json


class TextRegistrationTest(unittest.TestCase):
    def test_noop_resume_reuses_evidence_and_rejects_identity_drift(self):
        with tempfile.TemporaryDirectory(prefix="text-registration-resume-") as temporary_name:
            root = Path(temporary_name)
            output = root / "output"
            output.mkdir()
            image = Image.new("RGB", (100, 60), "white")
            image.save(root / "reference.png")
            image.save(output / "preview.png")
            write_json(output / "scene.json", {
                "canvas": {"width": 100, "height": 60}, "elements": [], "z_order": [], "assets": {},
            })
            first = run_registration(root, root / "reference.png", {})
            second = run_registration(root, root / "reference.png", {})
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "unchanged")
            self.assertTrue((root / "text-registration" / "decision.json").is_file())
            third = run_registration(root, root / "reference.png", {}, pass_number=2)
            fourth = run_registration(root, root / "reference.png", {}, pass_number=2)
            self.assertEqual(third, fourth)
            self.assertEqual(third["status"], "unchanged")
            self.assertEqual(third["pass_number"], 2)
            self.assertTrue((root / "text-registration-pass-2" / "decision.json").is_file())
            with self.assertRaisesRegex(ValueError, "at most two"):
                run_registration(root, root / "reference.png", {}, pass_number=3)
            write_json(output / "scene.json", {
                "canvas": {"width": 100, "height": 60}, "elements": [{"id": "new"}], "z_order": ["new"], "assets": {},
            })
            with self.assertRaisesRegex(ValueError, "identity drift"):
                run_registration(root, root / "reference.png", {})
            with self.assertRaisesRegex(ValueError, "identity drift"):
                run_registration(root, root / "reference.png", {}, pass_number=2)

    def test_proposes_bounded_shift_without_mutating_source_scene(self):
        scene = {
            "canvas": {"width": 120, "height": 80},
            "elements": [{
                "id": "label/text", "source_label_id": "label", "kind": "text",
                "text": "A", "bbox": [15, 15, 30, 30],
            }, {
                "id": "icon/shape", "source_label_id": "icon", "kind": "rect",
                "bbox": [55, 15, 30, 30],
            }],
        }
        with tempfile.TemporaryDirectory(prefix="text-registration-proposal-") as temporary_name:
            root = Path(temporary_name)
            reference = Image.new("RGB", (120, 80), "white")
            preview = Image.new("RGB", (120, 80), "white")
            ImageDraw.Draw(reference).rectangle((25, 25, 34, 34), fill="black")
            ImageDraw.Draw(preview).rectangle((23, 24, 32, 33), fill="black")
            reference.save(root / "reference.png")
            preview.save(root / "preview.png")
            candidate, proposals = propose(scene, root / "reference.png", root / "preview.png")
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["id"], "label/text")
        self.assertEqual(candidate["elements"][0]["bbox"], [17, 16, 30, 30])
        self.assertEqual(candidate["elements"][1]["bbox"], scene["elements"][1]["bbox"])
        self.assertEqual(scene["elements"][0]["bbox"], [15, 15, 30, 30])

    def test_second_pass_requires_first_pass_evidence(self):
        with tempfile.TemporaryDirectory(prefix="text-registration-pass-chain-") as temporary_name:
            root = Path(temporary_name)
            output = root / "output"
            output.mkdir()
            write_json(output / "scene.json", {"canvas": {"width": 10, "height": 10}, "elements": []})
            with self.assertRaisesRegex(ValueError, "requires first-pass evidence"):
                run_registration(root, root / "reference.png", {}, pass_number=2)


if __name__ == "__main__":
    unittest.main()
