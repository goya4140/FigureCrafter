from decomposer.text_visibility import assess


def _scene(*, color: str = "none") -> dict:
    return {"canvas": {"width": 200, "height": 100}, "elements": [{
        "id": "logo/text", "source_label_id": "logo", "category": "image",
        "kind": "text", "editable": True, "text": "BRAND", "bbox": [10, 20, 80, 30],
        "style": {"color": color},
    }]}


def _recognition(*, category: str = "image") -> dict:
    return {"objects": [{"id": "logo", "category": category,
                         "text": "BRAND", "bbox": [10, 20, 80, 30]}]}


def test_non_equation_image_category_text_cannot_be_transparent() -> None:
    result = assess(_recognition(), _scene())
    assert result["assessed"] == 1
    failure = result["failures"][0]
    assert failure["affected_label_ids"] == ["logo"]
    assert failure["affected_primitive_ids"] == ["logo/text"]
    assert failure["evaluation_region_source"] == "affected_scene_owners"
    assert failure["visible_native_source_text"] == ""
    assert failure["mechanism"] == "native-text-transparent-or-missing"


def test_transparent_text_over_same_owner_local_raster_is_distinguished() -> None:
    scene = _scene()
    scene["elements"].append({
        "id": "logo/content", "source_label_id": "logo", "kind": "image",
        "bbox": [12, 21, 76, 28],
    })
    failure = assess(_recognition(), scene)["failures"][0]
    assert failure["mechanism"] == "native-text-transparent-over-local-raster"
    assert failure["same_owner_local_raster_overlap_ratio"] > 0.5
    assert failure["visible_native_source_text"] == ""


def test_visible_text_and_equation_exception() -> None:
    assert assess(_recognition(), _scene(color="#000000"))["failures"] == []
    assert assess(_recognition(category="equation"), _scene())["failures"] == []


def test_visible_styled_runs_override_transparent_base_style() -> None:
    scene = _scene()
    scene["elements"][0]["runs"] = [{
        "text": "BRAND", "source_text": "BRAND", "style": {"color": "#000000"},
    }]
    assert assess(_recognition(), scene)["failures"] == []


def test_invisible_split_line_cannot_satisfy_full_source_identity() -> None:
    recognition = {"objects": [{"id": "caption", "category": "text", "text": "A\nB"}]}
    scene = {"canvas": {"width": 100, "height": 100}, "elements": [
        {"id": "caption/line-1", "source_label_id": "caption", "kind": "text",
         "editable": True, "text": "A", "line_break_after": True,
         "bbox": [5, 5, 40, 20], "style": {"color": "#000000"}},
        {"id": "caption/line-2", "source_label_id": "caption", "kind": "text",
         "editable": True, "text": "B", "line_break_after": False,
         "bbox": [5, 30, 40, 20], "style": {"color": "none"}},
    ]}
    failure = assess(recognition, scene)["failures"][0]
    assert failure["visible_native_source_text"] == "A\n"
    assert failure["hidden_primitive_ids"] == ["caption/line-2"]
