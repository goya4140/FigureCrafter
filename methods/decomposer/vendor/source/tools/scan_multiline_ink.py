#!/usr/bin/env python3
"""Read-only scan for severe source-relative native-text ink loss."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decomposer.rendered_text_ink import assess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--maximum-ratio", type=float, default=0.15)
    parser.add_argument("--gate", action="store_true", help="Report only failures of the candidate independent gate.")
    parser.add_argument("--include-single-line", action="store_true", help="Exploratory raw scan; not an audit gate.")
    args = parser.parse_args()
    for base in args.runs:
        progress = json.loads((base / "progress.json").read_text())
        for sample in progress["samples"]:
            run = base / "runs" / sample["sample_id"]
            scene_path = run / "output" / "scene.json"
            source_path = run / "input" / "normalized.png"
            preview_path = run / "output" / "preview.png"
            if not all(path.exists() for path in (scene_path, source_path, preview_path)):
                continue
            if args.gate:
                result = assess(json.loads(scene_path.read_text()), source_path, preview_path)
                for failure in result["failures"]:
                    print(json.dumps({
                        "sample_index": sample["sample_index"],
                        "sample_id": sample["sample_id"],
                        **failure,
                    }, ensure_ascii=False))
                continue
            source = np.asarray(Image.open(source_path).convert("RGB"))
            preview = np.asarray(Image.open(preview_path).convert("RGB"))
            if source.shape != preview.shape:
                continue
            for element in json.loads(scene_path.read_text())["elements"]:
                if element.get("kind") != "text":
                    continue
                if not args.include_single_line and "\n" not in str(element.get("text") or ""):
                    continue
                x, y, width, height = element["bbox"]
                x0, y0 = max(0, int(x)), max(0, int(y))
                x1 = min(source.shape[1], int(x + width + .999))
                y1 = min(source.shape[0], int(y + height + .999))
                if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) < 3000:
                    continue
                source_crop = source[y0:y1, x0:x1]
                preview_crop = preview[y0:y1, x0:x1]
                source_dark = int((source_crop.mean(axis=2) < 160).sum())
                preview_dark = int((preview_crop.mean(axis=2) < 160).sum())
                if source_dark < 500 or preview_dark / source_dark >= args.maximum_ratio:
                    continue
                print(json.dumps({
                    "sample_index": sample["sample_index"],
                    "element_id": element["id"],
                    "ratio": round(preview_dark / source_dark, 6),
                    "source_dark": source_dark,
                    "preview_dark": preview_dark,
                    "source_median": float(np.median(source_crop)),
                    "line_spacing": element.get("style", {}).get("line_spacing"),
                    "font_size": element.get("style", {}).get("font_size"),
                    "color": element.get("style", {}).get("color"),
                    "line_count": len(str(element.get("text") or "").split("\n")),
                }, ensure_ascii=False))


if __name__ == "__main__":
    main()
