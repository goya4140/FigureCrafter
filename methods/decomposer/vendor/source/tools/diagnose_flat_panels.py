#!/usr/bin/env python3
"""Read-only source/render flat-panel color and extent diagnostic."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from decomposer.common import read_json


def _longest(indices: np.ndarray) -> tuple[int, int] | None:
    if len(indices) == 0:
        return None
    groups = np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)
    group = max(groups, key=len)
    return int(group[0]), int(group[-1])


def _extent(pixels: np.ndarray, color: np.ndarray, box: list[float]) -> dict:
    x, y, width, height = box
    left = max(0, round(x - 20))
    right = min(pixels.shape[1], round(x + width + 20))
    top = max(0, round(y - 20))
    bottom = min(pixels.shape[0], round(y + height + 20))
    matching = np.max(np.abs(pixels[top:bottom, left:right] - color), axis=2) <= 2
    horizontal = matching.mean(axis=0)
    vertical = matching.mean(axis=1)
    x_extent = _longest(np.flatnonzero(horizontal >= .4))
    y_extent = _longest(np.flatnonzero(vertical >= .4))
    sample_y = min(pixels.shape[0] - 1, round(y + height - 12))
    sample_x = min(pixels.shape[1] - 1, round(x + width - 12))
    x_cross = _longest(np.flatnonzero(np.max(np.abs(pixels[sample_y, left:right] - color), axis=1) <= 2))
    y_cross = _longest(np.flatnonzero(np.max(np.abs(pixels[top:bottom, sample_x] - color), axis=1) <= 2))
    return {
        "x": [x_extent[0] + left, x_extent[1] + left] if x_extent else None,
        "y": [y_extent[0] + top, y_extent[1] + top] if y_extent else None,
        "median_color": [int(v) for v in np.median(pixels[top:bottom, left:right][matching], axis=0)] if matching.any() else None,
        "cross_section": {
            "row": sample_y,
            "x": [x_cross[0] + left, x_cross[1] + left] if x_cross else None,
            "column": sample_x,
            "y": [y_cross[0] + top, y_cross[1] + top] if y_cross else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    scene = read_json(run / "output" / "scene.json")
    source = np.asarray(Image.open(run / "input" / "normalized.png").convert("RGB"), dtype=np.int16)
    render = np.asarray(Image.open(run / "output" / "preview.png").convert("RGB"), dtype=np.int16)
    for element in scene["elements"]:
        if element["kind"] != "rect" or element["bbox"][2] < 300 or element["bbox"][3] < 300:
            continue
        fill = element["style"].get("fill", "")
        if len(fill) != 7 or not fill.startswith("#"):
            continue
        color = np.array([int(fill[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)
        print({"id": element["id"], "bbox": element["bbox"], "fill": fill,
               "source": _extent(source, color, element["bbox"]),
               "render": _extent(render, color, element["bbox"])})


if __name__ == "__main__":
    main()
