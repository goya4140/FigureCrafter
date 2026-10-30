from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from decomposer.representation_shards import generate, partition_objects
from tools.seed_representation_experiment import FROZEN_FILES, seed, verify


def objects(count: int) -> list[dict]:
    categories = ("chart", "equation", "shape", "text", "arrow")
    return [
        {"id": f"owner_{i}", "category": categories[i % len(categories)],
         "bbox": [float((i % 8) * 100), float((i // 8) * 100), 80.0, 80.0], "text": ""}
        for i in range(count)
    ]


class RepresentationShardsTest(unittest.TestCase):
    def test_partition_is_deterministic_and_exact(self):
        rows = objects(37)
        first = partition_objects(rows, 7)
        second = partition_objects(rows, 7)
        self.assertEqual([[r["id"] for r in shard] for shard in first],
                         [[r["id"] for r in shard] for shard in second])
        self.assertEqual(len(first), 7)
        self.assertEqual({r["id"] for shard in first for r in shard}, {r["id"] for r in rows})
        self.assertEqual(sum(map(len, first)), len(rows))
        self.assertTrue(all(first))
        self.assertEqual(len(partition_objects(rows[:2], 9)), 2)

    def test_seed_preserves_upstream_and_detects_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            target = Path(temporary) / "candidate"
            for name in FROZEN_FILES:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            (source / "calls/recognition/metadata.json").write_text(
                json.dumps({"status": "complete", "fallback_used": False}),
            )
            seed(source, target)
            verify(target)
            (target / "recognition/recognition.json").write_text("changed")
            with self.assertRaisesRegex(RuntimeError, "upstream snapshot changed"):
                verify(target)

    def test_shard_results_merge_in_recognition_order(self):
        rows = objects(13)
        recognition = {"canvas": {"width": 800, "height": 200}, "objects": rows}
        decisions = {"decisions": [{"label_id": row["id"], "decision": "native"} for row in rows]}

        def fake_model(stage, prompt, payload, schema, image, config, directory, fixture_dir):
            components = [
                {"label_id": row["id"], "primitives": [{"id": "box", "kind": "rect"}]}
                for row in reversed(payload["recognition"]["objects"])
            ]
            return {"components": components}, {"status": "complete", "fallback_used": False, "seconds": 0.1}

        with tempfile.TemporaryDirectory() as temporary, patch(
            "decomposer.representation_shards.fixture_or_model", side_effect=fake_model,
        ):
            proposal, metadata = generate(
                Path(temporary) / "source.png", recognition, decisions,
                {"representation": {"strategy": "parallel_shards", "shards": 4, "max_concurrency": 2}},
                temporary, "Representation prompt",
            )
            self.assertEqual([row["label_id"] for row in proposal["components"]],
                             [row["id"] for row in rows])
            self.assertEqual(metadata["status"], "complete")
            self.assertEqual(metadata["shard_count"], 4)
            saved = json.loads((Path(temporary) / "representation/input.json").read_text())
            self.assertEqual(saved["recognition"], recognition)
            self.assertEqual(saved["asset_decisions"], decisions)

    def test_missing_owner_fails_closed(self):
        rows = objects(4)
        recognition = {"canvas": {"width": 800, "height": 200}, "objects": rows}

        def fake_model(*args):
            return {"components": []}, {"status": "complete", "fallback_used": False}

        with tempfile.TemporaryDirectory() as temporary, patch(
            "decomposer.representation_shards.fixture_or_model", side_effect=fake_model,
        ):
            with self.assertRaisesRegex(ValueError, "missing, duplicate, or foreign"):
                generate(Path(temporary) / "source.png", recognition, {"decisions": []},
                         {"representation": {"strategy": "parallel_shards", "shards": 2}},
                         temporary, "Representation prompt")
            metadata = json.loads((Path(temporary) / "representation/metadata.json").read_text())
            self.assertEqual(metadata["status"], "failed")

    def test_cropped_shard_keeps_global_coordinates(self):
        rows = [
            {"id": "left", "category": "shape", "bbox": [100.0, 80.0, 40.0, 30.0], "text": ""},
            {"id": "right", "category": "shape", "bbox": [800.0, 500.0, 40.0, 30.0], "text": ""},
        ]
        recognition = {"canvas": {"width": 1000, "height": 700}, "objects": rows}
        captured = []

        def fake_model(stage, prompt, payload, schema, image, config, directory, fixture_dir):
            with Image.open(image) as crop:
                captured.append((payload["image_region"], crop.size,
                                 payload["recognition"]["objects"][0]["bbox"]))
            return {"components": [
                {"label_id": row["id"], "primitives": [{"id": "box", "kind": "rect"}]}
                for row in payload["recognition"]["objects"]
            ]}, {"status": "complete", "fallback_used": False}

        with tempfile.TemporaryDirectory() as temporary, patch(
            "decomposer.representation_shards.fixture_or_model", side_effect=fake_model,
        ):
            image = Path(temporary) / "source.png"
            Image.new("RGB", (1000, 700), "white").save(image)
            generate(image, recognition, {"decisions": []},
                     {"representation": {"strategy": "parallel_shards", "shards": 2,
                                         "image_scope": "shard_crop"}}, temporary, "prompt")
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(region[2:] == list(size) for region, size, _ in captured))
        self.assertEqual({tuple(bbox) for _, _, bbox in captured},
                         {tuple(row["bbox"]) for row in rows})


if __name__ == "__main__":
    unittest.main()
