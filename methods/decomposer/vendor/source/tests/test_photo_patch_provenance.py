from copy import deepcopy
import json
from pathlib import Path
import pytest
from PIL import Image,ImageDraw
from decomposer.common import sha256
from decomposer.validation import _grounded_asset
from decomposer import asset_policy

def fixture(tmp_path,monkeypatch):
 root=tmp_path/'run';path=root/'representation/assets/photo.png';path.parent.mkdir(parents=True);(root/'observations').mkdir();(root/'observations/evidence-ir.json').write_text('{}')
 image=root/'input-normalized.png';source=Image.new('RGB',(200,200),'white');ImageDraw.Draw(source).rectangle((13,13,20,20),fill='black');ImageDraw.Draw(source).rectangle((60,60,90,90),fill='blue');source.save(image)
 ex=[{'label_id':'label','bbox':[10,10,14,14],'fill':'#FFFFFF','source_text':'q','white_border_ratio':1.0}];crop=source.crop((0,0,100,100)).convert('RGBA');ImageDraw.Draw(crop).rectangle((10,10,23,23),fill='white');crop.save(path)
 asset={'source_label_id':'photo','source_candidate_id':'recognition:photo','source_bbox':[0,0,100,100],'source_basis':'recognition_bbox_with_verified_white_native_label_patches','mode':'source_crop','path':str(path),'sha256':sha256(path),'native_text_exclusions':ex}
 approved={'label_id':'photo','decision':'localized_raster','source_candidate_id':asset['source_candidate_id'],'source_basis':asset['source_basis'],'approved_bbox':asset['source_bbox'],'native_text_exclusions':deepcopy(ex)};monkeypatch.setattr(asset_policy,'decide_with_photo_overlays',lambda *a:{'decisions':[approved]})
 return asset,{}, {'placements':[{'label_id':'label','visible':True}]}, {'elements':[{'source_label_id':'label','kind':'text','editable':True}]},image,{}

def test_verified_patch_requires_exact_source_transform_and_native_text(tmp_path,monkeypatch):
 args=fixture(tmp_path,monkeypatch);assert _grounded_asset(*args)

@pytest.mark.parametrize('defect',['pixel_inside','pixel_outside','hash','hidden','missing_native','uneditable','bbox','nonwhite_border','wrong_source','unknown_basis'])
def test_photo_patch_proof_rejects_unsupported_changes(tmp_path,monkeypatch,defect):
 asset,rec,layout,scene,image,cfg=fixture(tmp_path,monkeypatch);path=Path(asset['path'])
 if defect in ['pixel_inside','pixel_outside','hash']:
  with Image.open(path) as raw:im=raw.copy()
  im.putpixel((12,12) if defect=='pixel_inside' else (50,50),(255,0,0,255));im.save(path)
  if defect!='hash':asset['sha256']=sha256(path)
 if defect=='hidden':layout['placements'][0]['visible']=False
 if defect=='missing_native':scene['elements']=[]
 if defect=='uneditable':scene['elements'][0]['editable']=False
 if defect=='bbox':asset['source_bbox']=[1,0,100,100]
 if defect=='nonwhite_border':
  with Image.open(image) as raw:source=raw.copy()
  ImageDraw.Draw(source).rectangle((10,10,23,10),fill='black');source.save(image)
 if defect=='wrong_source':image=tmp_path/'different.png';Image.new('RGB',(200,200),'red').save(image)
 if defect=='unknown_basis':asset['source_basis']='arbitrary_patch'
 assert not _grounded_asset(asset,rec,layout,scene,image,cfg)

def test_replayed_asset_policy_must_independently_approve(tmp_path,monkeypatch):
 args=fixture(tmp_path,monkeypatch);monkeypatch.setattr(asset_policy,'decide_with_photo_overlays',lambda *a:{'decisions':[]});assert not _grounded_asset(*args)


def test_candidate_round_asset_uses_verified_origin_evidence(tmp_path,monkeypatch):
 args=list(fixture(tmp_path,monkeypatch));asset=args[0];origin=Path(asset['path']);dest=origin.parents[2]/'refinement/round-001/representation/assets/photo.png';dest.parent.mkdir(parents=True);dest.write_bytes(origin.read_bytes());asset['path']=str(dest);assert _grounded_asset(*args)
