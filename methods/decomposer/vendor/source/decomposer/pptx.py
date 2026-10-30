from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image

from .common import read_json, write_json
from .polygons import patch


def _libreoffice_font_environment(soffice: str, temporary: Path) -> tuple[dict[str, str], dict]:
    """Explicit font discovery also works through the bundled shell wrapper."""
    from xml.sax.saxutils import escape
    environment = os.environ.copy()
    directories = [Path(p).expanduser() for p in (*os.environ.get("DECOMPOSER_FONT_DIRS", "").split(os.pathsep),
        "/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
        "~/Library/Fonts", "/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts",
    ) if p and Path(p).expanduser().is_dir()]
    if not directories:
        return environment, {"mode": "system", "configured": False}
    cache = temporary / "fontconfig-cache"
    cache.mkdir(parents=True, exist_ok=True)
    config = temporary / "fonts.conf"
    config.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">'
                      '<fontconfig>' + ''.join('<dir>'+escape(str(p))+'</dir>' for p in directories)
                      + '<cachedir>'+escape(str(cache))+'</cachedir></fontconfig>')
    environment.update(FONTCONFIG_FILE=str(config), FONTCONFIG_PATH=str(temporary), XDG_CACHE_HOME=str(cache),
                       SAL_FONTPATH=os.pathsep.join(str(p) for p in directories))
    return environment, {"mode": "explicit-system-fonts", "configured": True,
                         "directories": [str(p) for p in directories], "cache": "isolated-writable",
                         "wrapper_safe": True}


def inspect_pptx(path: str | Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"corrupt member: {corrupt}")
        slides = [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
        if len(slides) != 1:
            raise ValueError(f"expected one slide, found {len(slides)}")
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
        root = ET.fromstring(archive.read(slides[0]))
        ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main", "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        result = {
            "slide_count": 1,
            "shape_count": len(root.findall(".//p:sp", ns)),
            "picture_count": len(root.findall(".//p:pic", ns)),
            "connector_count": len(root.findall(".//p:cxnSp", ns)),
            "text_run_count": len(root.findall(".//a:t", ns)),
            "xml_valid": True,
        }
        result["is_single_screenshot_like"] = result["picture_count"] == 1 and result["shape_count"] == 0 and result["text_run_count"] == 0
        return result


def render(pptx: Path, preview: Path, canvas: tuple[int, int], runtime: dict) -> dict:
    soffice, pdftoppm = runtime.get("soffice"), runtime.get("pdftoppm")
    if not soffice or not pdftoppm:
        return {"status": "skipped", "reason": "renderer_not_configured"}
    with tempfile.TemporaryDirectory(prefix="decomposer-render-") as temporary_name:
        temporary = Path(temporary_name)
        render_environment, font_environment = _libreoffice_font_environment(soffice, temporary)
        process = subprocess.run(
            [soffice, "-env:UserInstallation=" + (temporary / "profile").as_uri(), "--headless", "--convert-to", "pdf", "--outdir", str(temporary), str(pptx)],
            capture_output=True, text=True, timeout=runtime.get("timeout_seconds", 180),
            env=render_environment,
        )
        (preview.parent / "render.log").write_text(process.stdout + "\n" + process.stderr)
        pdf = temporary / f"{pptx.stem}.pdf"
        if process.returncode or not pdf.exists():
            raise RuntimeError("LibreOffice failed to render PPTX")
        subprocess.run(
            [pdftoppm, "-png", "-singlefile", "-scale-to-x", str(canvas[0]), "-scale-to-y", str(canvas[1]), str(pdf), str(temporary / "preview")],
            check=True, capture_output=True, timeout=runtime.get("timeout_seconds", 180),
        )
        import re
        actual_fonts = sorted(set(n.decode("ascii", errors="replace") for n in
                                  re.findall(rb"/BaseFont\s*/([^\s/<>]+)", pdf.read_bytes())))
        write_json(preview.parent / "render-font-audit.json", {
            "font_environment": font_environment, "pdf_base_fonts": actual_fonts,
            "verification": "Actual exported PDF BaseFont names; not only requested PPTX names.",
        })
        if runtime.get("retain_diagnostic_pdf"):
            import shutil
            shutil.copy2(pdf, preview.parent / "rendered.pdf")
        with Image.open(temporary / "preview.png") as image:
            if image.size != canvas:
                raise ValueError(f"preview size mismatch: {image.size} != {canvas}")
            image.convert("RGB").save(preview)
    return {"status": "ready", "path": str(preview), "size": list(canvas), "font_environment": font_environment}


def compile_scene(scene_path: str | Path, output_dir: str | Path, config: dict, render_output: bool = True) -> dict:
    scene_path, output_dir = Path(scene_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = read_json(scene_path)
    canvas_area = max(1.0, float(scene["canvas"]["width"] * scene["canvas"]["height"]))
    image_elements = [item for item in scene.get("elements", []) if item.get("kind") == "image"]
    image_area_ratios = [
        max(0.0, float(item["bbox"][2]) * float(item["bbox"][3])) / canvas_area
        for item in image_elements
    ]
    raster_audit = {
        "image_count": len(image_elements),
        "max_image_area_ratio": round(max(image_area_ratios, default=0.0), 6),
        "summed_image_area_ratio": round(sum(image_area_ratios), 6),
    }
    allow_full_canvas = bool(config.get("runtime", {}).get("diagnostic_allow_full_canvas_raster", False))
    if not allow_full_canvas and (
        raster_audit["max_image_area_ratio"] >= 0.85
        or raster_audit["summed_image_area_ratio"] >= 0.90
    ):
        raise RuntimeError(f"raster safety audit rejected screenshot-like scene: {raster_audit}")
    pptx = output_dir / "editable.pptx"
    compiler_report = output_dir / "compiler-report.json"
    script = Path(__file__).parents[1] / "backend" / "compile.mjs"
    process = subprocess.run(
        [config.get("runtime", {}).get("node", "node"), str(script), str(scene_path), str(pptx), str(compiler_report)],
        capture_output=True, text=True, timeout=config.get("runtime", {}).get("timeout_seconds", 180),
    )
    (output_dir / "compile.log").write_text(process.stdout + "\n" + process.stderr)
    if process.returncode or not pptx.exists():
        raise RuntimeError("deterministic PPTX compiler failed; inspect compile.log")
    patched_geometry = patch(pptx, scene)
    compiler_payload = read_json(compiler_report)
    if patched_geometry:
        compiler_payload["fallbacks"] = [row for row in compiler_payload.get("fallbacks", []) if row.get("id") not in patched_geometry]
        compiler_payload["native_custom_geometry"] = sorted(patched_geometry)
        write_json(compiler_report, compiler_payload)
    structure = inspect_pptx(pptx)
    if structure["is_single_screenshot_like"] and not allow_full_canvas:
        raise RuntimeError("PPTX safety audit rejected single screenshot-like output")
    canvas = (int(scene["canvas"]["width"]), int(scene["canvas"]["height"]))
    rendering = render(pptx, output_dir / "preview.png", canvas, config.get("runtime", {})) if render_output else {"status": "skipped", "reason": "disabled"}
    report = {
        "status": "render_success" if rendering["status"] == "ready" else "structural_success",
        "scene_degraded": scene["degraded"], "compiler": compiler_payload,
        "structure": structure, "render": rendering,
        "raster_audit": raster_audit,
    }
    write_json(output_dir / "report.json", report)
    return report
