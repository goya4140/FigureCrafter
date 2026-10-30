#!/usr/bin/env python3
"""Read-only source/render color-mode comparison for editable filled rectangles."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decomposer.common import read_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    scene = read_json(run / "output" / "scene.json")
    source = np.asarray(Image.open(run / "input" / "normalized.png").convert("RGB"), dtype=np.int16)
    render = np.asarray(Image.open(run / "output" / "preview.png").convert("RGB"), dtype=np.int16)
    rows = []
    for element in scene["elements"]:
        if element["kind"] != "rect":
            continue
        fill = str(element.get("style", {}).get("fill") or "")
        if len(fill) != 7 or not fill.startswith("#"):
            continue
        x, y, w, h = (round(value) for value in element["bbox"])
        if w < 40 or h < 15:
            continue
        x0, y0, x1, y1 = max(0, x + 3), max(0, y + 3), min(source.shape[1], x + w - 3), min(source.shape[0], y + h - 3)
        if x1 <= x0 or y1 <= y0:
            continue
        old = np.array([int(fill[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)
        src = source[y0:y1, x0:x1]
        cur = render[y0:y1, x0:x1]
        rendered_old = np.max(np.abs(cur - old), axis=2) <= 2
        source_near = np.max(np.abs(src - old), axis=2) <= 15
        matched = src[rendered_old & source_near]
        if len(matched) < max(200, .02 * (x1 - x0) * (y1 - y0)):
            continue
        values, counts = np.unique(matched, axis=0, return_counts=True)
        mode = values[counts.argmax()]
        delta = mode - old
        if np.max(np.abs(delta)) == 0:
            continue
        rows.append({
            "id": element["id"], "bbox": element["bbox"], "old_fill": fill,
            "source_mode": "#" + "".join(f"{int(channel):02X}" for channel in mode),
            "delta": [int(channel) for channel in delta],
            "mode_pixels": int(counts.max()),
            "matched_pixels": len(matched),
            "signal": round(int(counts.max()) * float(np.max(np.abs(delta))), 1),
        })
    for row in sorted(rows, key=lambda row: row["signal"], reverse=True):
        print(row)


if __name__ == "__main__":
    main()
