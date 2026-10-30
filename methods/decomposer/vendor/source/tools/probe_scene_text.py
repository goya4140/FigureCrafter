#!/usr/bin/env python3
"""Measure selected text and stroke-style changes on a copied scene."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.common import read_json, write_json
from decomposer.pptx import compile_scene
from decomposer.provenance import build as build_provenance
from decomposer.quality import compare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--scene", type=Path, help="Optional copied scene for combined mechanism probe")
    parser.add_argument("--before-preview", type=Path, help="Preview corresponding to --scene")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--font", action="append", default=[], help="shape-id=font-size-in-source-pixels")
    parser.add_argument("--font-factor", type=float, help="multiply every native text font size, including styled runs")
    parser.add_argument("--font-family", action="append", default=[], help="shape-id=font-family")
    parser.add_argument("--char-spacing", action="append", default=[], help="shape-id=spacing-in-points")
    parser.add_argument("--dash", action="append", default=[], help="shape-id=solid|dash|sysDash|sysDot")
    parser.add_argument("--fill", action="append", default=[], help="shape-id=#RRGGBB")
    parser.add_argument("--bbox", action="append", default=[], help="shape-id=x,y,width,height")
    parser.add_argument("--dash-all", choices=["solid", "dash", "sysDash", "sysDot"], help="set every dashed line-like shape")
    parser.add_argument("--height", action="append", default=[], help="shape-id=new-bbox-height-in-source-pixels")
    parser.add_argument("--width", action="append", default=[], help="shape-id=new-bbox-width-in-source-pixels")
    parser.add_argument("--y", action="append", default=[], help="shape-id=new-bbox-top-in-source-pixels")
    parser.add_argument("--x", action="append", default=[], help="shape-id=new-bbox-left-in-source-pixels")
    parser.add_argument("--height-floor-ratio", type=float, help="raise text bbox height to at least font-size times ratio")
    parser.add_argument("--stroke-factor", type=float, help="multiply line-like stroke widths")
    parser.add_argument("--tile-shadow", action="store_true", help="add native outer shadow to each /tile rect")
    parser.add_argument("--panel-shadow", action="store_true", help="replace paired /shadow rect with native /panel shadow")
    parser.add_argument("--shadow-id", action="append", default=[], help="add native shadow to exact existing shape id")
    parser.add_argument("--tile-shadow-prefix", action="append", default=[], help="restrict tile shadows to ids with these prefixes")
    parser.add_argument("--shadow-blur", type=float, default=5.0)
    parser.add_argument("--shadow-offset", type=float, default=5.0)
    parser.add_argument("--shadow-angle", type=float, default=45.0)
    parser.add_argument("--shadow-opacity", type=float, default=0.35)
    parser.add_argument("--shadow-color", default="666666")
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    source = args.source_run.resolve()
    scene = read_json(args.scene.resolve() if args.scene else source / "output" / "scene.json")
    by_id = {item["id"]: item for item in scene["elements"]}
    changes = []
    if not args.font and args.font_factor is None and not args.font_family and not args.char_spacing and not args.dash and not args.fill and not args.bbox and not args.dash_all and not args.height and not args.width and not args.y and not args.x and args.height_floor_ratio is None and args.stroke_factor is None and not args.tile_shadow and not args.panel_shadow and not args.shadow_id:
        raise ValueError("at least one style or geometry probe is required")
    if args.font_factor is not None:
        if not .85 <= args.font_factor <= 1.15:
            raise ValueError("font-factor outside controlled diagnostic range")
        for element in scene["elements"]:
            if element["kind"] != "text":
                continue
            old_size = element["style"]["font_size"]
            element["style"]["font_size"] = round(old_size * args.font_factor, 4)
            for run in element.get("runs", []):
                run["style"]["font_size"] = round(run["style"]["font_size"] * args.font_factor, 4)
            changes.append({"id": element["id"], "old_font_size": old_size,
                            "new_font_size": element["style"]["font_size"]})
    for assignment in args.font:
        shape_id, separator, raw_size = assignment.rpartition("=")
        if not separator or shape_id not in by_id or by_id[shape_id]["kind"] != "text":
            raise ValueError(f"not a text shape assignment: {assignment}")
        size = float(raw_size)
        if not 1 <= size <= 300:
            raise ValueError(f"font size outside controlled probe range: {size}")
        element = by_id[shape_id]
        old_size = element["style"]["font_size"]
        element["style"]["font_size"] = size
        for run in element.get("runs", []):
            run["style"]["font_size"] = size
        changes.append({"id": shape_id, "old_font_size": old_size, "new_font_size": size})
    for assignment in args.font_family:
        shape_id, separator, family = assignment.rpartition("=")
        if not separator or shape_id not in by_id or by_id[shape_id]["kind"] != "text" or not family.strip():
            raise ValueError(f"not a text shape font-family assignment: {assignment}")
        element = by_id[shape_id]
        old_family = element["style"]["font_family"]
        element["style"]["font_family"] = family
        for run in element.get("runs", []):
            run["style"]["font_family"] = family
        changes.append({"id": shape_id, "old_font_family": old_family, "new_font_family": family})
    for assignment in args.char_spacing:
        shape_id, separator, raw_spacing = assignment.rpartition("=")
        if not separator or shape_id not in by_id or by_id[shape_id]["kind"] != "text":
            raise ValueError(f"invalid text character-spacing assignment: {assignment}")
        spacing = float(raw_spacing)
        if not -3 <= spacing <= 8:
            raise ValueError(f"text character spacing outside diagnostic range: {spacing}")
        element = by_id[shape_id]
        old_spacing = element["style"].get("char_spacing", 0)
        element["style"]["char_spacing"] = spacing
        changes.append({"id": shape_id, "old_char_spacing": old_spacing, "new_char_spacing": spacing})
    for assignment in args.dash:
        shape_id, separator, dash_type = assignment.rpartition("=")
        if not separator or shape_id not in by_id or dash_type not in {"solid", "dash", "sysDash", "sysDot"}:
            raise ValueError(f"invalid dash assignment: {assignment}")
        element = by_id[shape_id]
        old_dash_type = element["style"].get("dash_type", "dash" if element["style"].get("dashed") else "solid")
        element["style"]["dash_type"] = dash_type
        element["style"]["dashed"] = dash_type != "solid"
        changes.append({"id": shape_id, "old_dash_type": old_dash_type, "new_dash_type": dash_type})
    for assignment in args.fill:
        shape_id, separator, fill = assignment.rpartition("=")
        if not separator or shape_id not in by_id or by_id[shape_id]["kind"] not in {"rect", "round_rect", "ellipse", "polygon", "path"}:
            raise ValueError(f"invalid fill assignment: {assignment}")
        if len(fill) != 7 or fill[0] != "#" or any(char not in "0123456789abcdefABCDEF" for char in fill[1:]):
            raise ValueError(f"invalid fill color: {fill}")
        element = by_id[shape_id]
        old_fill = element["style"].get("fill")
        element["style"]["fill"] = fill.upper()
        changes.append({"id": shape_id, "old_fill": old_fill, "new_fill": fill.upper()})
    for assignment in args.bbox:
        shape_id, separator, raw_bbox = assignment.rpartition("=")
        if not separator or shape_id not in by_id:
            raise ValueError(f"invalid bbox assignment: {assignment}")
        values = [float(value) for value in raw_bbox.split(",")]
        if (len(values) != 4 or values[0] < 0 or values[1] < 0
                or values[2] <= 0 or values[3] <= 0
                or values[0] + values[2] > scene["canvas"]["width"]
                or values[1] + values[3] > scene["canvas"]["height"]):
            raise ValueError(f"bbox outside source canvas: {assignment}")
        element = by_id[shape_id]
        old_bbox = element["bbox"][:]
        element["bbox"] = values
        changes.append({"id": shape_id, "old_bbox": old_bbox, "new_bbox": values})
    if args.dash_all:
        for element in scene["elements"]:
            if element["kind"] not in {"line", "polyline", "path"} or not element["style"].get("dashed"):
                continue
            old_dash_type = element["style"].get("dash_type", "dash")
            element["style"]["dash_type"] = args.dash_all
            element["style"]["dashed"] = args.dash_all != "solid"
            changes.append({"id": element["id"], "old_dash_type": old_dash_type, "new_dash_type": args.dash_all})
    for assignment in args.height:
        shape_id, separator, raw_height = assignment.rpartition("=")
        if not separator or shape_id not in by_id or by_id[shape_id]["kind"] != "text":
            raise ValueError(f"not a text shape height assignment: {assignment}")
        height = float(raw_height)
        if not 1 <= height <= scene["canvas"]["height"]:
            raise ValueError(f"bbox height outside controlled probe range: {height}")
        element = by_id[shape_id]
        old_height = element["bbox"][3]
        element["bbox"][3] = height
        changes.append({"id": shape_id, "old_bbox_height": old_height, "new_bbox_height": height})
    for assignment in args.width:
        shape_id, separator, raw_width = assignment.rpartition("=")
        if not separator or shape_id not in by_id or by_id[shape_id]["kind"] != "text":
            raise ValueError(f"not a text shape width assignment: {assignment}")
        width = float(raw_width)
        element = by_id[shape_id]
        if not 1 <= width <= scene["canvas"]["width"] - element["bbox"][0]:
            raise ValueError(f"bbox width outside controlled probe range: {width}")
        old_width = element["bbox"][2]
        element["bbox"][2] = width
        changes.append({"id": shape_id, "old_bbox_width": old_width, "new_bbox_width": width})
    for assignment in args.y:
        shape_id, separator, raw_y = assignment.rpartition("=")
        if not separator or shape_id not in by_id or by_id[shape_id]["kind"] != "text":
            raise ValueError(f"not a text shape top assignment: {assignment}")
        y = float(raw_y)
        if not 0 <= y < scene["canvas"]["height"]:
            raise ValueError(f"bbox top outside controlled probe range: {y}")
        element = by_id[shape_id]
        old_y = element["bbox"][1]
        element["bbox"][1] = y
        changes.append({"id": shape_id, "old_bbox_top": old_y, "new_bbox_top": y})
    for assignment in args.x:
        shape_id, separator, raw_x = assignment.rpartition("=")
        if not separator or shape_id not in by_id or by_id[shape_id]["kind"] != "text":
            raise ValueError(f"not a text shape left assignment: {assignment}")
        x = float(raw_x)
        if not 0 <= x < scene["canvas"]["width"]:
            raise ValueError(f"bbox left outside controlled probe range: {x}")
        element = by_id[shape_id]
        old_x = element["bbox"][0]
        element["bbox"][0] = x
        changes.append({"id": shape_id, "old_bbox_left": old_x, "new_bbox_left": x})
    if args.height_floor_ratio is not None:
        if not 0.75 <= args.height_floor_ratio <= 1.5:
            raise ValueError("height floor ratio outside controlled probe range")
        for element in scene["elements"]:
            if element["kind"] != "text":
                continue
            old_height = element["bbox"][3]
            new_height = min(scene["canvas"]["height"] - element["bbox"][1], max(old_height, float(element["style"].get("font_size", 20)) * args.height_floor_ratio))
            if new_height > old_height:
                element["bbox"][3] = round(new_height, 4)
                changes.append({"id": element["id"], "old_bbox_height": old_height, "new_bbox_height": element["bbox"][3]})
    if args.stroke_factor is not None:
        if not 0.5 <= args.stroke_factor <= 2.0:
            raise ValueError("stroke factor outside controlled probe range")
        for element in scene["elements"]:
            if element["kind"] not in {"line", "polyline", "path"}:
                continue
            old_width = element["style"]["stroke_width"]
            element["style"]["stroke_width"] = round(old_width * args.stroke_factor, 4)
            changes.append({"id": element["id"], "old_stroke_width": old_width, "new_stroke_width": element["style"]["stroke_width"]})
    if args.tile_shadow:
        if not 0 <= args.shadow_blur <= 20 or not 0 <= args.shadow_offset <= 20 or not 0 <= args.shadow_angle < 360 or not 0 <= args.shadow_opacity <= 1:
            raise ValueError("shadow probe outside controlled range")
        for element in scene["elements"]:
            if element["kind"] != "rect" or not element["id"].endswith("/tile"):
                continue
            if args.tile_shadow_prefix and not any(element["id"].startswith(prefix) for prefix in args.tile_shadow_prefix):
                continue
            element["style"]["shadow"] = {"type": "outer", "color": args.shadow_color, "opacity": args.shadow_opacity, "blur": args.shadow_blur, "offset": args.shadow_offset, "angle": args.shadow_angle}
            changes.append({"id": element["id"], "new_shadow": element["style"]["shadow"]})
    if args.panel_shadow:
        removed = set()
        for element in scene["elements"]:
            if element["kind"] != "rect" or not element["id"].endswith("/shadow"):
                continue
            panel_id = element["id"].removesuffix("/shadow") + "/panel"
            panel = by_id.get(panel_id)
            if not panel or panel["kind"] != "rect":
                continue
            panel["style"]["shadow"] = {"type": "outer", "color": args.shadow_color, "opacity": args.shadow_opacity, "blur": args.shadow_blur, "offset": args.shadow_offset, "angle": args.shadow_angle}
            removed.add(element["id"])
            changes.append({"id": panel_id, "replaced_shadow_rect": element["id"], "new_shadow": panel["style"]["shadow"]})
        if not removed:
            raise ValueError("no /shadow and /panel pairs found")
        scene["elements"] = [element for element in scene["elements"] if element["id"] not in removed]
        scene["z_order"] = [element_id for element_id in scene["z_order"] if element_id not in removed]
    for shape_id in args.shadow_id:
        element = by_id.get(shape_id)
        if not element or element["kind"] not in {"rect", "round_rect", "ellipse", "polygon", "path"}:
            raise ValueError(f"not a shadow-capable native shape: {shape_id}")
        element["style"]["shadow"] = {"type": "outer", "color": args.shadow_color, "opacity": args.shadow_opacity, "blur": args.shadow_blur, "offset": args.shadow_offset, "angle": args.shadow_angle}
        changes.append({"id": shape_id, "new_shadow": element["style"]["shadow"]})
    write_json(out / "scene.json", scene)
    config = read_json(args.config)
    report = compile_scene(out / "scene.json", out, config, True)
    build_provenance(scene, out / "editable.pptx", out)
    with Image.open(source / "input" / "normalized.png") as image:
        canvas = image.size
    assessment = compare(
        source / "input" / "normalized.png",
        args.before_preview.resolve() if args.before_preview else source / "output" / "preview.png",
        out / "preview.png", [0, 0, *canvas],
    )
    result = {"changes": changes, "compiler_fallbacks": report["compiler"].get("fallbacks"), "assessment": assessment}
    write_json(out / "probe.json", result)
    print(result)


if __name__ == "__main__":
    main()
