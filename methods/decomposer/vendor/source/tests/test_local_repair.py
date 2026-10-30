from copy import deepcopy
from pathlib import Path
import tempfile

import pytest
from PIL import Image, ImageDraw

from decomposer.local_repair import RepairPreflightError, expand_text_owner, local_guard, preflight


def owner():
    p = {"id": "text", "kind": "text", "bbox": [0, 0, 100, 30]}
    c = {"width": 100, "height": 30, "primitives": [p]}
    l = {"bbox": [200, 100, 200, 60], "rotation_deg": 0}
    return c,p,l


def test_text_expansion_preserves_requested_world_coordinates_and_scale():
    c,p,l = owner()
    assert expand_text_owner(c,p,l,[-5,-5,110,40], {"width":1000,"height":500})
    assert l["bbox"] == [190,90,220,80]
    assert p["bbox"] == [0,0,110,40]
    assert l["bbox"][2]/c["width"] == 2


def test_composite_rotated_or_off_canvas_expansion_is_explicit_failure():
    for mode in ("composite", "rotation", "canvas"):
        c,p,l = owner()
        if mode == "composite": c["primitives"].append(deepcopy(p))
        if mode == "rotation": l["rotation_deg"] = 20
        if mode == "canvas": l["bbox"][0] = 0
        before = deepcopy((c,p,l))
        with pytest.raises(RepairPreflightError):
            expand_text_owner(c,p,l,[-5,-5,110,40], {"width":1000,"height":500})
        assert (c,p,l) == before


def scene():
    return {"canvas":{"width":200,"height":100}, "assets":{}, "z_order":["a/text", "b/text"],
            "elements":[{"id":f"{v}/text", "source_label_id":v,"kind":"text", "text":"label",
                         "style":{"color":"#000000"}, "bbox":box}
                        for v,box in [("a",[10,10,100,30]),("b",[110,10,80,30])]]}


def test_preflight_noop_and_outside_scope_are_rejected():
    b = scene()
    with pytest.raises(RepairPreflightError, match="canonical_scene_noop"):
        preflight(b,deepcopy(b),{"affected_label_ids":["a"]})
    a = deepcopy(b); a["elements"][1]["bbox"][1] += 1
    with pytest.raises(RepairPreflightError, match="undeclared_scene_owner"):
        preflight(b,a,{"affected_label_ids":["a"]})


def test_guard_rejects_further_single_line_collapse_even_if_already_small():
    s = scene(); after = deepcopy(s); after["elements"][0]["style"]["font_size"] = 30
    ex = preflight(s,after,{"affected_label_ids":["a"]})
    with tempfile.TemporaryDirectory() as name:
        paths=[]
        for label, rect in [("ref",(20,18,80,28)),("before",(20,18,60,25)),("after",(20,18,30,20))]:
            im=Image.new("RGB",(200,100),"white"); ImageDraw.Draw(im).rectangle(rect,fill="black")
            p=Path(name)/f"{label}.png";im.save(p);paths.append(p)
        result=local_guard(paths[0],s,paths[1],after,paths[2],ex)
        assert not result["passed"]
        assert any("rendered_text_ink_loss_away_from_reference" in f["reasons"] for f in result["failures"])


def test_guard_allows_ink_reduction_toward_reference():
    s=scene();a=deepcopy(s);a["elements"][0]["style"]["font_size"]=10
    ex=preflight(s,a,{"affected_label_ids":["a"]})
    with tempfile.TemporaryDirectory() as name:
        paths=[]
        for label,rect in [("ref",(20,18,40,23)),("before",(20,18,80,28)),("after",(20,18,40,23))]:
            im=Image.new("RGB",(200,100),"white");ImageDraw.Draw(im).rectangle(rect,fill="black")
            p=Path(name)/f"{label}.png";im.save(p);paths.append(p)
        assert local_guard(paths[0],s,paths[1],a,paths[2],ex)["passed"]


def test_guard_rejects_occluding_unchanged_neighbor():
    s=scene();a=deepcopy(s);a["elements"][0]["bbox"]=[10,10,170,30]
    ex=preflight(s,a,{"affected_label_ids":["a"]})
    with tempfile.TemporaryDirectory() as name:
        r=Image.new("RGB",(200,100),"white");ImageDraw.Draw(r).rectangle((120,18,170,28),fill="black")
        c=Image.new("RGB",(200,100),"white")
        rp,bp,ap=[Path(name)/f"{k}.png" for k in ("ref","before","after")]
        r.save(rp);r.save(bp);c.save(ap)
        guard=local_guard(rp,s,bp,a,ap,ex)
        assert not guard["passed"]
        assert any(row["element_id"]=="b/text" for row in guard["failures"])
