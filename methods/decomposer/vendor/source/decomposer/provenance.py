from __future__ import annotations

from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

from PIL import Image, ImageDraw

from .common import sha256, write_json


P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"p": P, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def _color(index: int) -> tuple[int, int, int]:
    value = index + 1
    return value & 255, (value >> 8) & 255, (value >> 16) & 255


def _drawingml_text(shape: ET.Element) -> str:
    """Reconstruct visible text, including paragraph and explicit line breaks."""
    body = shape.find("p:txBody", NS)
    if body is None:
        return ""
    paragraphs: list[str] = []
    for paragraph in body.findall("a:p", NS):
        parts: list[str] = []
        for child in paragraph:
            if child.tag == f"{{{NS['a']}}}br":
                parts.append("\n")
            elif child.tag in {f"{{{NS['a']}}}r", f"{{{NS['a']}}}fld"}:
                text = child.find("a:t", NS)
                if text is not None:
                    parts.append(text.text or "")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _shape_records(pptx: Path) -> dict[str, dict[str, Any]]:
    with zipfile.ZipFile(pptx) as archive:
        root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
    result: dict[str, dict[str, Any]] = {}
    for tag in ("sp", "pic", "cxnSp"):
        for shape in root.findall(f".//p:{tag}", NS):
            props = shape.find(f"p:nv{tag[0].upper() + tag[1:]}Pr/p:cNvPr", NS)
            if props is None:
                # pic uses nvPicPr and connectors use nvCxnSpPr.
                props = shape.find(".//p:cNvPr", NS)
            if props is not None and props.get("name"):
                head = shape.find(".//a:headEnd", NS)
                tail = shape.find(".//a:tailEnd", NS)
                text = _drawingml_text(shape)
                baselines = [int(item.get("baseline", "0")) for item in shape.findall(".//a:rPr", NS)]
                result[props.get("name", "")] = {
                    "shape_id": int(props.get("id", "0")),
                    "head_end": head.get("type", "none") if head is not None else None,
                    "tail_end": tail.get("type", "none") if tail is not None else None,
                    "text": text,
                    "run_baselines": baselines,
                }
    return result


def build(scene: dict[str, Any], pptx: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Build the label -> primitive -> scene -> OOXML trace and a diagnostic ID mask."""
    output_dir, pptx = Path(output_dir), Path(pptx)
    output_dir.mkdir(parents=True, exist_ok=True)
    shape_records = _shape_records(pptx)
    width, height = int(scene["canvas"]["width"]), int(scene["canvas"]["height"])
    mask = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(mask)
    labels: dict[str, dict[str, Any]] = {}
    palette: dict[str, list[int]] = {}
    for index, element in enumerate(scene["elements"]):
        uid = element["id"]
        label_id = element["source_label_id"]
        rgb = _color(index)
        palette[uid] = list(rgb)
        x, y, w, h = element["bbox"]
        if element["kind"] in ("line", "polyline") and element.get("points"):
            draw.line([tuple(p) for p in element["points"]], fill=rgb, width=max(3, round(element.get("style", {}).get("stroke_width", 1) + 2)))
        elif element["kind"] == "polygon" and element.get("points"):
            draw.polygon([tuple(p) for p in element["points"]], fill=rgb)
        else:
            draw.rectangle([x, y, x + w, y + h], fill=rgb)
        ooxml = [
            {"name": name, **record}
            for name, record in shape_records.items()
            if name == uid or name.startswith(uid + ".segment")
        ]
        labels.setdefault(label_id, {"label_id": label_id, "primitive_ids": [], "scene_elements": [], "ooxml_shapes": []})
        labels[label_id]["primitive_ids"].append(uid)
        labels[label_id]["scene_elements"].append({
            "id": uid, "kind": element["kind"], "bbox": element["bbox"],
            **({"source_primitive_id": element["source_primitive_id"]} if element.get("source_primitive_id") else {}),
        })
        labels[label_id]["ooxml_shapes"].extend(ooxml)
    mask_path = output_dir / "id-mask.png"
    mask.save(mask_path)
    trace = {
        "schema_version": "decomposer.provenance.v1",
        "pptx_sha256": sha256(pptx),
        "scene_sha256": sha256(output_dir / "scene.json"),
        "labels": labels,
        "primitive_to_label": {pid: lid for lid, item in labels.items() for pid in item["primitive_ids"]},
        "id_mask": str(mask_path),
        "palette": palette,
    }
    write_json(output_dir / "provenance.json", trace)
    write_json(output_dir / "label-index.json", list(labels.values()))
    return trace
