from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image, ImageDraw
import json


def test_same_pixels_different_png_serialization_are_not_input_drift(tmp_path: Path) -> None:
    path = Path(__file__).resolve().parents[1] / "tools" / "compare_same_id_stages.py"
    spec = importlib.util.spec_from_file_location("compare_same_id_stages", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for run in (baseline, candidate):
        (run / "input").mkdir(parents=True)
    image = Image.new("RGB", (20, 20), "white")
    image.save(baseline / "input" / "normalized.png", compress_level=0)
    image.save(candidate / "input" / "normalized.png", compress_level=9)
    result = module.compare(baseline, candidate, None)
    assert result["same_normalized_input_bytes"] is False
    assert result["same_normalized_input_pixels"] is True
    assert all(stage["complete"] is False for stage in result["stages"])


def test_text_owner_comparison_measures_rendered_ink_and_line_positions(tmp_path: Path) -> None:
    path = Path(__file__).resolve().parents[1] / "tools" / "compare_same_id_stages.py"
    spec = importlib.util.spec_from_file_location("compare_same_id_stages", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = Image.new("RGB", (120, 50), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((15, 5, 60, 13), fill="black")
    draw.rectangle((15, 20, 60, 28), fill="black")
    runs = []
    for name, preview in (("baseline", Image.new("RGB", source.size, "white")), ("candidate", source)):
        run = tmp_path / name
        for relative in ("input", "recognition", "output"):
            (run / relative).mkdir(parents=True)
        source.save(run / "input/normalized.png")
        preview.save(run / "output/preview.png")
        (run / "recognition/recognition.json").write_text(json.dumps({"objects": [
            {"id": "label", "category": "text", "text": "A\nB", "bbox": [0, 0, 100, 40]},
        ]}))
        (run / "output/scene.json").write_text(json.dumps({"elements": [
            {"id": "label/text", "source_label_id": "label", "kind": "text", "text": "A\nB",
             "bbox": [0, 0, 100, 40], "style": {"font_size": 20}},
        ]}))
        runs.append(run)
    comparison = module.compare(*runs, "label")
    owner = comparison["text_owners"]["owners"]["label"]
    assert owner["baseline"]["rendered_dark_pixels"] == 0
    assert owner["candidate"]["rendered_dark_pixels"] > 0
    assert owner["candidate"]["source_line_starts"] == [5, 20]
    assert owner["candidate"]["rendered_line_starts"] == [5, 20]
    candidate = runs[1]
    recognition = json.loads((candidate / "recognition/recognition.json").read_text())
    recognition["objects"][0]["id"] = "renamed"
    (candidate / "recognition/recognition.json").write_text(json.dumps(recognition))
    scene = json.loads((candidate / "output/scene.json").read_text())
    scene["elements"][0]["source_label_id"] = "renamed"
    (candidate / "output/scene.json").write_text(json.dumps(scene))
    mapped = module.compare(*runs, "label", "renamed")
    assert mapped["text_owner_pair"]["baseline_label_id"] == "label"
    assert mapped["text_owner_pair"]["candidate_label_id"] == "renamed"
    assert mapped["text_owner_pair"]["candidate"]["rendered_dark_pixels"] > 0
