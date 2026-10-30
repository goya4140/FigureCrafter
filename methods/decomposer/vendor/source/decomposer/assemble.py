from __future__ import annotations

from typing import Any


def _scale_bbox(local: list[float], origin: list[float], component: dict[str, Any]) -> list[float]:
    x, y, width, height = origin
    local_x, local_y, local_width, local_height = local
    scale_x, scale_y = width / component["width"], height / component["height"]
    return [
        round(x + local_x * scale_x, 4), round(y + local_y * scale_y, 4),
        round(local_width * scale_x, 4), round(local_height * scale_y, 4),
    ]


def _scale_points(points: list[list[float]], origin: list[float], component: dict[str, Any]) -> list[list[float]]:
    x, y, width, height = origin
    scale_x, scale_y = width / component["width"], height / component["height"]
    return [[round(x + px * scale_x, 4), round(y + py * scale_y, 4)] for px, py in points]


def assemble(recognition: dict[str, Any], representation: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    components = {item["label_id"]: item for item in representation["components"]}
    placements = {item["label_id"]: item for item in layout["placements"]}
    elements: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for label_id in layout["component_order"]:
        component, placement = components[label_id], placements[label_id]
        if not placement["visible"]:
            continue
        primitives = {item["id"]: item for item in component["primitives"]}
        for primitive_id in component["primitive_order"]:
            primitive = primitives[primitive_id]
            element: dict[str, Any] = {
                "id": f"{label_id}/{primitive_id}", "source_label_id": label_id,
                "category": component["category"], "kind": primitive["kind"],
                "style": dict(primitive["style"]), "rotation_deg": placement["rotation_deg"],
                "editable": primitive["editable"],
            }
            element["style"]["font_size"] = round(element["style"]["font_size"] * placement["font_scale"], 4)
            if primitive["kind"] == "text" and "line_spacing" in element["style"]:
                element["style"]["line_spacing"] = round(element["style"]["line_spacing"] * placement["font_scale"], 4)
            if primitive["kind"] in ("line", "polyline", "polygon"):
                element["points"] = _scale_points(primitive["points"], placement["bbox"], component)
                xs = [point[0] for point in element["points"]]
                ys = [point[1] for point in element["points"]]
                element["bbox"] = [min(xs), min(ys), max(max(xs) - min(xs), 0.01), max(max(ys) - min(ys), 0.01)]
                element["arrow_start"] = primitive.get("arrow_start", False)
                element["arrow_end"] = primitive.get("arrow_end", False)
            else:
                element["bbox"] = _scale_bbox(primitive["bbox"], placement["bbox"], component)
            if primitive["kind"] == "text":
                element["text"] = primitive["text"]
                if primitive.get("runs"):
                    element["runs"] = []
                    for run in primitive["runs"]:
                        assembled_run = {**run, "style": dict(run["style"])}
                        assembled_run["style"]["font_size"] = round(
                            assembled_run["style"]["font_size"] * placement["font_scale"], 4
                        )
                        element["runs"].append(assembled_run)
            elif primitive["kind"] == "image":
                element["asset_id"] = primitive["asset_id"]
            elif primitive["kind"] == "path":
                element["path"] = primitive.get("path", "")
                element["path_viewbox"] = ([0, 0, primitive["bbox"][2], primitive["bbox"][3]]
                                           if primitive.get("coordinate_space") == "primitive" else primitive["bbox"])
                element["fallback_kind"] = primitive.get("fallback_kind", "rect")
                element["arrow_start"] = primitive.get("arrow_start", False)
                element["arrow_end"] = primitive.get("arrow_end", False)
            elements.append(element)
            by_id[element["id"]] = element
    z_order = [element["id"] for element in elements]
    scene = {
        "schema_version": "decomposer.scene.v1",
        "canvas": {**recognition["canvas"], "background": recognition["background"]},
        "elements": elements,
        "z_order": z_order,
        "assets": representation["assets"],
        "semantic_groups": {
            component["label_id"]: component["members"]
            for component in representation["components"] if component.get("group_only")
        },
        "degraded": recognition["degraded"] or representation["degraded"] or layout["degraded"],
    }
    validate(scene)
    return scene


def validate(scene: dict[str, Any]) -> None:
    ids = [item["id"] for item in scene["elements"]]
    if len(ids) != len(set(ids)) or ids != scene["z_order"]:
        raise AssertionError("scene IDs and z_order must be a complete unique sequence")
    for element in scene["elements"]:
        if element["bbox"][2] <= 0 or element["bbox"][3] <= 0:
            raise AssertionError(f"invalid scene element {element['id']}")
        if element["kind"] == "image" and element.get("asset_id") not in scene["assets"]:
            raise AssertionError(f"unresolved scene asset {element['id']}")
