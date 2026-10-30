from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


def write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def number(value: Any, default: float = 0.0) -> float:
    if finite(value):
        return float(value)
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def canonical_color(value: Any, default: str = "#000000") -> str:
    if value in (None, "", "none", "transparent"):
        return "none" if str(value).lower() in ("none", "transparent") else default
    text = str(value).strip()
    short = re.fullmatch(r"#([0-9a-fA-F]{3})", text)
    if short:
        return "#" + "".join(character * 2 for character in short.group(1)).upper()
    full = re.fullmatch(r"#([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", text)
    if full:
        return "#" + full.group(1).upper()
    embedded = re.search(r"#[0-9a-fA-F]{6}", text)
    return embedded.group(0).upper() if embedded else default


def canonical_bbox(value: Any, canvas: tuple[float, float], minimum: float = 1.0) -> list[float]:
    width, height = canvas
    raw = list(value) if isinstance(value, (list, tuple)) and len(value) == 4 else [0, 0, width, height]
    x, y, w, h = (number(raw[index], 0.0) for index in range(4))
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    x = clamp(x, 0.0, max(0.0, width - minimum))
    y = clamp(y, 0.0, max(0.0, height - minimum))
    w = clamp(w, minimum, max(minimum, width - x))
    h = clamp(h, minimum, max(minimum, height - y))
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def canonical_points(value: Any, canvas: tuple[float, float]) -> list[list[float]]:
    width, height = canvas
    points: list[list[float]] = []
    if isinstance(value, list):
        for raw in value:
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                continue
            point = [round(clamp(number(raw[0]), 0, width), 4), round(clamp(number(raw[1]), 0, height), 4)]
            if not points or point != points[-1]:
                points.append(point)
    return points


def safe_id(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-.")
    return (text or fallback)[:80]


def unique_id(preferred: str, used: set[str]) -> str:
    candidate = preferred
    suffix = 2
    while candidate in used:
        candidate = f"{preferred}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def canonical_font_family(value: Any) -> str:
    """Resolve CSS/generic family names to concrete fonts PowerPoint can embed."""
    text = str(value or "Arial").strip()
    first = text.split(",", 1)[0].strip().strip("\"'") or "Arial"
    aliases = {
        "sans-serif": "Arial", "sans serif": "Arial", "system-ui": "Arial",
        "ui-sans-serif": "Arial", "-apple-system": "Arial", "blinkmacsystemfont": "Arial",
        "serif": "Times New Roman", "ui-serif": "Times New Roman",
        "monospace": "Courier New", "ui-monospace": "Courier New",
    }
    return aliases.get(first.casefold(), first)[:100]


def style(raw: Any, *, text: bool = False) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    dash_type = raw.get("dash_type")
    if dash_type not in {"solid", "dash", "dashDot", "lgDash", "lgDashDot", "lgDashDotDot", "sysDash", "sysDot"}:
        dash_type = "dash" if raw.get("dashed", False) else "solid"
    result = {
        "fill": canonical_color(raw.get("fill"), "none"),
        "stroke": canonical_color(raw.get("stroke"), "#000000"),
        "stroke_width": round(clamp(number(raw.get("stroke_width"), 1), 0, 100), 4),
        "color": canonical_color(raw.get("color"), "#000000"),
        "font_size": round(clamp(number(raw.get("font_size"), 20), 1, 500), 4),
        "font_family": canonical_font_family(raw.get("font_family") or raw.get("font_face")),
        "bold": bool(raw.get("bold", False)),
        "italic": bool(raw.get("italic", False)),
        "align": raw.get("align") if raw.get("align") in ("left", "center", "right") else "center",
        "dashed": dash_type != "solid",
        "dash_type": dash_type,
    }
    if text:
        result["fill"] = "none"
        result["stroke"] = "none"
        if raw.get("line_spacing") is not None:
            result["line_spacing"] = round(clamp(number(raw["line_spacing"], result["font_size"] * .85), 1, 500), 4)
    shadow = raw.get("shadow")
    if isinstance(shadow, dict) and shadow.get("type") == "outer":
        result["shadow"] = {
            "type": "outer",
            "color": canonical_color(shadow.get("color"), "#666666").removeprefix("#"),
            "opacity": round(clamp(number(shadow.get("opacity"), 0.35), 0, 1), 4),
            "blur": round(clamp(number(shadow.get("blur"), 5), 0, 20), 4),
            "offset": round(clamp(number(shadow.get("offset"), 5), 0, 20), 4),
            "angle": round(number(shadow.get("angle"), 225) % 360, 4),
        }
    return result
