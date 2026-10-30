"""Independent image evidence for severe native-text render collapse.

Assess multiline styled Courier text, short relative-looking line-spacing,
and high-contrast dark text on a light source region. These can export
editable OOXML while rendering invisible or severely shrunken ink. This is
not a general text-similarity or color-fidelity metric.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


MIN_BOX_AREA = 3000
MIN_SOURCE_DARK_PIXELS = 500
MIN_INK_RATIO = 0.25
MIN_PLAIN_DARK_INK_RATIO = 0.15
DARK_MEAN_LIMIT = 160


def _dark_color(value: Any) -> bool:
    raw = str(value or "").lstrip("#")
    if len(raw) != 6:
        return False
    try:
        channels = [int(raw[index:index + 2], 16) for index in (0, 2, 4)]
    except ValueError:
        return False
    return sum(channels) / 3 < DARK_MEAN_LIMIT


def _light_chromatic_color(value: Any) -> np.ndarray | None:
    """Return a declared visible color that a dark-pixel count would miss."""
    raw = str(value or "").lstrip("#")
    if len(raw) != 6:
        return None
    try:
        channels = np.array([int(raw[index:index + 2], 16) for index in (0, 2, 4)])
    except ValueError:
        return None
    if int(np.ptp(channels)) < 40:
        return None
    return channels


def _color_ink_count(crop: np.ndarray, color: np.ndarray) -> int:
    pixels = crop.astype(np.int32)
    distance_sq = ((pixels - color) ** 2).sum(axis=2)
    chroma = np.ptp(pixels, axis=2)
    return int(((distance_sq < 70 ** 2) & (chroma >= 20)).sum())


def assess(scene: dict[str, Any], reference_path: str | Path, preview_path: str | Path) -> dict[str, Any]:
    """Return measured eligible owners and any severe source-relative collapses."""
    with Image.open(reference_path) as image:
        reference = np.asarray(image.convert("RGB"), dtype=np.uint8)
    with Image.open(preview_path) as image:
        preview = np.asarray(image.convert("RGB"), dtype=np.uint8)
    if reference.shape != preview.shape:
        raise ValueError("source and preview dimensions differ")
    height, width = reference.shape[:2]
    assessed: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for element in scene.get("elements", []):
        if element.get("kind") != "text":
            continue
        style = element.get("style", {})
        courier_styled = (
            str(style.get("font_family") or "").casefold() == "courier new"
            and len(element.get("runs") or []) >= 2
            and len(str(element.get("text") or "").split("\n")) >= 3
        )
        relative_leading = (
            len(str(element.get("text") or "").split("\n")) >= 2
            and 0.5 <= float(style.get("line_spacing") or 0) <= 3
        )
        plain_dark_multiline = (
            len(str(element.get("text") or "").split("\n")) >= 2
            and _dark_color(style.get("color"))
        )
        glyph_runs = [run for run in element.get("runs") or [] if str(run.get("text") or "").strip()]
        styled_dark_multiline = (
            len(str(element.get("text") or "").split("\n")) >= 2
            and bool(glyph_runs)
            and all(_dark_color((run.get("style") or {}).get("color", style.get("color")))
                    for run in glyph_runs)
        )
        if not (courier_styled or relative_leading or plain_dark_multiline or styled_dark_multiline):
            continue
        box = element.get("bbox") or []
        if len(box) != 4:
            continue
        x, y, box_width, box_height = (float(value) for value in box)
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1 = min(width, int(x + box_width + .999))
        y1 = min(height, int(y + box_height + .999))
        if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) < MIN_BOX_AREA:
            continue
        source_crop = reference[y0:y1, x0:x1]
        if (plain_dark_multiline or styled_dark_multiline) and not (courier_styled or relative_leading):
            # The dark-ink ratio is meaningful only against a mostly light
            # source region; colored/dark panels require a different measure.
            if float(np.median(source_crop)) < 230:
                continue
        colored_ink = (_light_chromatic_color(style.get("color"))
                       if relative_leading and not glyph_runs else None)
        if colored_ink is not None:
            source_dark = _color_ink_count(source_crop, colored_ink)
            if source_dark / source_crop.shape[0] / source_crop.shape[1] > 0.6:
                # A broad matching panel is not evidence of visible glyphs.
                continue
        else:
            source_dark = int((source_crop.mean(axis=2) < DARK_MEAN_LIMIT).sum())
        if source_dark < MIN_SOURCE_DARK_PIXELS:
            continue
        preview_crop = preview[y0:y1, x0:x1]
        if colored_ink is not None:
            rendered_dark = _color_ink_count(preview_crop, colored_ink)
        else:
            rendered_dark = int((preview_crop.mean(axis=2) < DARK_MEAN_LIMIT).sum())
        ratio = rendered_dark / source_dark
        minimum_ratio = MIN_PLAIN_DARK_INK_RATIO if (plain_dark_multiline or styled_dark_multiline) and not (
            courier_styled or relative_leading
        ) else MIN_INK_RATIO
        row = {
            "element_id": element["id"],
            "source_label_id": element["source_label_id"],
            "evaluation_region": [x0, y0, x1 - x0, y1 - y0],
            "evaluation_region_source": "affected_scene_owners",
            "source_dark_pixels": source_dark,
            "rendered_dark_pixels": rendered_dark,
            "ink_measurement": "declared_color_match" if colored_ink is not None else "dark_pixel",
            "rendered_to_source_ratio": round(ratio, 6),
            "minimum_ratio": minimum_ratio,
            "mechanism": "relative-leading-render-collapse" if relative_leading
                         else "styled-courier-render-collapse" if courier_styled
                         else "styled-dark-multiline-render-collapse" if styled_dark_multiline
                         else "dark-multiline-autofit-collapse",
        }
        assessed.append(row)
        if ratio < minimum_ratio:
            failures.append(row)
    return {"assessed": assessed, "failures": failures}
