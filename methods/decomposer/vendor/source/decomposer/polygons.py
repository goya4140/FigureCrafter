from __future__ import annotations

import math
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"a": A, "p": P}


TOKEN = re.compile(r"[MmLlHhVvQqCcAaZz]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
NUMERIC_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
NUMERIC_WORD = re.compile(
    r"(?:(?<![A-Za-z])|(?<=[MmLlHhVvQqCcAa]))(?:"
    + "|".join(sorted(NUMERIC_WORDS, key=len, reverse=True)) + r")(?![A-Za-z])",
    re.IGNORECASE,
)


def normalize_numeric_path_words(value: str) -> str:
    """Repair only unambiguous spelled-out numeric path coordinates.

    Unknown words and malformed commands remain untouched, so the strict path
    parser can reject them instead of silently inventing geometry.
    """
    return NUMERIC_WORD.sub(lambda match: str(NUMERIC_WORDS[match.group().lower()]), value)


def _arc_to_cubics(
    x1: float, y1: float, rx: float, ry: float, rotation_deg: float,
    large_arc: float, sweep: float, x2: float, y2: float,
) -> list[tuple]:
    """Convert one SVG endpoint arc into one or more cubic Bezier segments."""
    if large_arc not in (0.0, 1.0) or sweep not in (0.0, 1.0):
        raise ValueError("SVG arc flags must be 0 or 1")
    rx, ry = abs(rx), abs(ry)
    if rx == 0.0 or ry == 0.0:
        return [("L", x2, y2)]
    if x1 == x2 and y1 == y2:
        return []

    phi = math.radians(rotation_deg % 360.0)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    radii_scale = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if radii_scale > 1.0:
        scale = math.sqrt(radii_scale)
        rx *= scale
        ry *= scale

    numerator = max(
        0.0,
        rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p,
    )
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    factor = 0.0 if denominator == 0.0 else math.sqrt(numerator / denominator)
    if bool(large_arc) == bool(sweep):
        factor = -factor
    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        length = math.hypot(ux, uy) * math.hypot(vx, vy)
        value = max(-1.0, min(1.0, dot / max(length, 1e-12)))
        result = math.acos(value)
        return -result if ux * vy - uy * vx < 0 else result

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = angle(1.0, 0.0, ux, uy)
    delta = angle(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2.0 * math.pi
    elif sweep and delta < 0:
        delta += 2.0 * math.pi

    segment_count = max(1, int(math.ceil(abs(delta) / (math.pi / 2.0))))
    segment_delta = delta / segment_count
    cubics: list[tuple] = []

    def mapped(px: float, py: float) -> tuple[float, float]:
        return (
            cx + rx * cos_phi * px - ry * sin_phi * py,
            cy + rx * sin_phi * px + ry * cos_phi * py,
        )

    for index in range(segment_count):
        start = theta1 + index * segment_delta
        end = start + segment_delta
        alpha = 4.0 / 3.0 * math.tan((end - start) / 4.0)
        cos_start, sin_start = math.cos(start), math.sin(start)
        cos_end, sin_end = math.cos(end), math.sin(end)
        c1 = mapped(cos_start - alpha * sin_start, sin_start + alpha * cos_start)
        c2 = mapped(cos_end + alpha * sin_end, sin_end - alpha * cos_end)
        endpoint = mapped(cos_end, sin_end)
        cubics.append(("C", c1[0], c1[1], c2[0], c2[1], endpoint[0], endpoint[1]))
    # Avoid accumulated floating point drift at the externally visible endpoint.
    last = cubics[-1]
    cubics[-1] = (*last[:-2], x2, y2)
    return cubics


def _path_commands(value: str) -> list[list[tuple]]:
    normalized = value.replace(",", " ")
    residue = TOKEN.sub("", normalized)
    if residue.strip():
        raise ValueError(f"unsupported path syntax: {residue.strip()!r}")
    tokens = TOKEN.findall(normalized)
    result: list[list[tuple]] = []; current: list[tuple] = []; index = 0; command = None
    x = y = start_x = start_y = 0.0
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "Q": 4, "C": 6, "A": 7, "Z": 0}
    while index < len(tokens):
        token = tokens[index]
        if token.upper() in arity:
            command = token; index += 1
            if command.upper() == "M" and current:
                result.append(current); current = []
            if command.upper() == "Z":
                current.append(("Z",)); command = None
                x, y = start_x, start_y
            continue
        if command is None: raise ValueError("path number without command")
        upper = command.upper(); relative = command.islower(); count = arity[upper]
        if index + count > len(tokens): raise ValueError(f"incomplete path command {command}")
        values = tuple(float(value) for value in tokens[index:index + count]); index += count
        if upper == "H":
            x = x + values[0] if relative else values[0]; current.append(("L", x, y))
        elif upper == "V":
            y = y + values[0] if relative else values[0]; current.append(("L", x, y))
        elif upper in ("M", "L"):
            nx, ny = values
            if relative: nx, ny = x + nx, y + ny
            x, y = nx, ny; current.append((upper, x, y))
            if upper == "M":
                start_x, start_y = x, y
                command = "l" if relative else "L"
        elif upper == "Q":
            cx, cy, nx, ny = values
            if relative: cx, cy, nx, ny = x + cx, y + cy, x + nx, y + ny
            current.append(("Q", cx, cy, nx, ny)); x, y = nx, ny
        elif upper == "C":
            c1x, c1y, c2x, c2y, nx, ny = values
            if relative: c1x, c1y, c2x, c2y, nx, ny = x + c1x, y + c1y, x + c2x, y + c2y, x + nx, y + ny
            current.append(("C", c1x, c1y, c2x, c2y, nx, ny)); x, y = nx, ny
        elif upper == "A":
            rx, ry, rotation, large_arc, sweep, nx, ny = values
            if relative: nx, ny = x + nx, y + ny
            current.extend(_arc_to_cubics(x, y, rx, ry, rotation, large_arc, sweep, nx, ny))
            x, y = nx, ny
    if current: result.append(current)
    if not result: raise ValueError("empty path")
    return result


def _coord(value: float, origin: float, size: float, extent: int) -> str:
    return str(round((value - origin) / max(size, 1e-9) * extent))


def _custom_geometry(element: dict) -> ET.Element:
    geometry = ET.Element(f"{{{A}}}custGeom")
    for tag in ("avLst", "gdLst", "ahLst", "cxnLst"):
        ET.SubElement(geometry, f"{{{A}}}{tag}")
    ET.SubElement(geometry, f"{{{A}}}rect", l="0", t="0", r="w", b="h")
    paths = ET.SubElement(geometry, f"{{{A}}}pathLst")
    x, y, width, height = element["bbox"]
    extent_x, extent_y = max(1, round(width * 9525)), max(1, round(height * 9525))
    if element["kind"] == "polygon":
        subpaths = [[("M" if index == 0 else "L", point[0], point[1]) for index, point in enumerate(element["points"])] + [("Z",)]]
        viewbox = [x, y, width, height]
    else:
        subpaths = _path_commands(element.get("path", ""))
        viewbox = element.get("path_viewbox") or [0, 0, width, height]
    vx, vy, vw, vh = viewbox
    for subpath_index, commands in enumerate(subpaths):
        closed = bool(commands and commands[-1][0] == "Z")
        # A compound filled contour needs its inner loops in the same native
        # path; separate paths fill the holes back in. Opt in explicitly.
        if not element.get("compound_path") or subpath_index == 0:
            path = ET.SubElement(paths, f"{{{A}}}path", w=str(extent_x), h=str(extent_y), fill="norm" if closed else "none")
        for item in commands:
            name, values = item[0], item[1:]
            if name in ("M", "L"):
                operation = ET.SubElement(path, f"{{{A}}}{'moveTo' if name == 'M' else 'lnTo'}")
                ET.SubElement(operation, f"{{{A}}}pt", x=_coord(values[0], vx, vw, extent_x), y=_coord(values[1], vy, vh, extent_y))
            elif name == "Q":
                operation = ET.SubElement(path, f"{{{A}}}quadBezTo")
                for offset in (0, 2): ET.SubElement(operation, f"{{{A}}}pt", x=_coord(values[offset], vx, vw, extent_x), y=_coord(values[offset + 1], vy, vh, extent_y))
            elif name == "C":
                operation = ET.SubElement(path, f"{{{A}}}cubicBezTo")
                for offset in (0, 2, 4): ET.SubElement(operation, f"{{{A}}}pt", x=_coord(values[offset], vx, vw, extent_x), y=_coord(values[offset + 1], vy, vh, extent_y))
            elif name == "Z": ET.SubElement(path, f"{{{A}}}close")
    return geometry


def patch(pptx: str | Path, scene: dict) -> set[str]:
    custom = {item["id"]: item for item in scene["elements"] if item["kind"] in ("polygon", "path")}
    if not custom:
        return set()
    pptx = Path(pptx)
    with zipfile.ZipFile(pptx) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    slide_name = "ppt/slides/slide1.xml"
    tree = ET.fromstring(files[slide_name])
    found: set[str] = set()
    for shape in tree.findall(".//p:sp", NS):
        properties = shape.find("p:nvSpPr/p:cNvPr", NS)
        if properties is None or properties.get("name") not in custom:
            continue
        element = custom[properties.get("name")]
        shape_properties = shape.find("p:spPr", NS)
        if shape_properties is None:
            continue
        try:
            geometry = _custom_geometry(element)
        except ValueError:
            # Preserve the compiler's editable placeholder and explicit
            # fallback record rather than failing the entire slide.
            continue
        for child in list(shape_properties):
            if child.tag in (f"{{{A}}}prstGeom", f"{{{A}}}custGeom"):
                shape_properties.remove(child)
        shape_properties.insert(1, geometry)
        found.add(element["id"])
    files[slide_name] = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
    temporary = pptx.with_suffix(pptx.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    temporary.replace(pptx)
    return found
