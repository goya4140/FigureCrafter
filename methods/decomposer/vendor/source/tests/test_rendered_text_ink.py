from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from decomposer.rendered_text_ink import assess


def _image(path: Path, *, dark_width: int) -> None:
    image = Image.new("RGB", (160, 100), "white")
    draw = ImageDraw.Draw(image)
    if dark_width > 0:
        draw.rectangle((10, 10, 10 + dark_width - 1, 59), fill="black")
    image.save(path)


def _colored_image(path: Path, *, width: int, color: str = "#B17AC5") -> None:
    image = Image.new("RGB", (160, 100), "white")
    if width:
        ImageDraw.Draw(image).rectangle((10, 10, 10 + width - 1, 59), fill=color)
    image.save(path)


def _scene(*, family: str = "Courier New", runs: bool = True,
           line_spacing: float | None = None) -> dict:
    return {"elements": [{
        "id": "code/text", "source_label_id": "code", "kind": "text",
        "bbox": [5, 5, 140, 70], "text": "one\ntwo\nthree",
        "style": {"font_family": family, **({"line_spacing": line_spacing}
                                         if line_spacing is not None else {})},
        "runs": [{"text": "one\n", "style": {"font_family": family}},
                 {"text": "two\nthree", "style": {"font_family": family}}] if runs else [],
    }]}


def test_visible_styled_code_passes_and_autofit_collapse_fails(tmp_path: Path) -> None:
    source, visible, collapsed = (tmp_path / name for name in ("source.png", "visible.png", "collapsed.png"))
    _image(source, dark_width=80)
    _image(visible, dark_width=40)
    _image(collapsed, dark_width=4)
    passing = assess(_scene(), source, visible)
    assert len(passing["assessed"]) == 1
    assert passing["failures"] == []
    failing = assess(_scene(), source, collapsed)
    assert len(failing["failures"]) == 1
    assert failing["failures"][0]["element_id"] == "code/text"
    assert failing["failures"][0]["evaluation_region_source"] == "affected_scene_owners"
    assert failing["failures"][0]["rendered_to_source_ratio"] == .05


def test_unsupported_style_is_not_claimed_as_assessed(tmp_path: Path) -> None:
    source, collapsed = tmp_path / "source.png", tmp_path / "collapsed.png"
    _image(source, dark_width=80)
    _image(collapsed, dark_width=4)
    assert assess(_scene(family="Arial"), source, collapsed) == {"assessed": [], "failures": []}
    assert assess(_scene(runs=False), source, collapsed) == {"assessed": [], "failures": []}


def test_relative_leading_native_text_collapse_is_audited(tmp_path: Path) -> None:
    source, visible, collapsed = (tmp_path / name for name in ("source.png", "visible.png", "collapsed.png"))
    _image(source, dark_width=80)
    _image(visible, dark_width=40)
    _image(collapsed, dark_width=0)
    scene = _scene(family="Arial", runs=False, line_spacing=1.2)
    assert assess(scene, source, visible)["failures"] == []
    failure = assess(scene, source, collapsed)["failures"][0]
    assert failure["mechanism"] == "relative-leading-render-collapse"
    assert failure["rendered_dark_pixels"] == 0
    assert assess(_scene(family="Arial", runs=False, line_spacing=50), source, collapsed) == {
        "assessed": [], "failures": [],
    }


def test_plain_dark_multiline_shrink_is_audited_without_flagging_colored_text(tmp_path: Path) -> None:
    source, collapsed = tmp_path / "source.png", tmp_path / "collapsed.png"
    _image(source, dark_width=80)
    _image(collapsed, dark_width=4)
    scene = _scene(family="Arial", runs=False)
    scene["elements"][0]["style"]["color"] = "#000000"
    failure = assess(scene, source, collapsed)["failures"][0]
    assert failure["mechanism"] == "dark-multiline-autofit-collapse"
    assert failure["minimum_ratio"] == 0.15
    scene["elements"][0]["style"]["color"] = "#509EFF"
    assert assess(scene, source, collapsed) == {"assessed": [], "failures": []}


def test_dark_styled_runs_are_assessed_when_base_style_is_transparent(tmp_path: Path) -> None:
    source, collapsed = tmp_path / "source.png", tmp_path / "collapsed.png"
    _image(source, dark_width=80)
    _image(collapsed, dark_width=0)
    scene = _scene(family="Arial", runs=True)
    scene["elements"][0]["style"]["color"] = "none"
    for run in scene["elements"][0]["runs"]:
        run["style"]["color"] = "#00668E"
    failure = assess(scene, source, collapsed)["failures"][0]
    assert failure["mechanism"] == "styled-dark-multiline-render-collapse"


def test_source_and_preview_must_have_same_dimensions(tmp_path: Path) -> None:
    source, preview = tmp_path / "source.png", tmp_path / "preview.png"
    _image(source, dark_width=80)
    Image.new("RGB", (120, 100), "white").save(preview)
    with pytest.raises(ValueError, match="dimensions differ"):
        assess(_scene(), source, preview)


def test_relative_leading_colored_ink_uses_declared_color_not_dark_pixels(tmp_path: Path) -> None:
    source, visible, missing, wrong_color = (
        tmp_path / name for name in ("source.png", "visible.png", "missing.png", "wrong.png")
    )
    _colored_image(source, width=80)
    _colored_image(visible, width=40)
    _colored_image(missing, width=0)
    _colored_image(wrong_color, width=80, color="#59AFC4")
    scene = _scene(family="Arial", runs=False, line_spacing=1.05)
    scene["elements"][0]["style"]["color"] = "#B17AC5"
    passing = assess(scene, source, visible)
    assert passing["failures"] == []
    assert passing["assessed"][0]["ink_measurement"] == "declared_color_match"
    assert passing["assessed"][0]["rendered_to_source_ratio"] == .5
    assert assess(scene, source, missing)["failures"][0]["rendered_dark_pixels"] == 0
    assert assess(scene, source, wrong_color)["failures"][0]["rendered_dark_pixels"] == 0


def test_colored_panel_is_not_counted_as_colored_text(tmp_path: Path) -> None:
    source, missing = tmp_path / "source.png", tmp_path / "missing.png"
    Image.new("RGB", (160, 100), "#B17AC5").save(source)
    Image.new("RGB", (160, 100), "white").save(missing)
    scene = _scene(family="Arial", runs=False, line_spacing=1.05)
    scene["elements"][0]["style"]["color"] = "#B17AC5"
    assert assess(scene, source, missing) == {"assessed": [], "failures": []}
