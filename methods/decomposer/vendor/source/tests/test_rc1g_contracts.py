from copy import deepcopy
import pytest
from decomposer.render_contracts import path_space,occlusion_order,_raster,stack_rectangles
from PIL import Image

@pytest.mark.parametrize('box,path,expected',[([130,3,17,14],'M1 12C-5 3 12 -5 16 3Z','primitive'),([130,3,17,14],'M131 15L145 10','component'),([0,0,17,14],'M1 12L15 10',None),([4,4,218,238],'M218 0H29Q0 0 0 29V209',None)])
def test_legacy_origin_requires_disambiguating_geometry(box,path,expected):
 p={'bbox':box,'path':path};assert path_space(p)[0]==expected

def test_explicit_origin_survives_ambiguous_bounds():assert path_space({'bbox':[3,3,30,30],'path':'M0 0H30','coordinate_space':'primitive'})==('primitive','explicit')

def fixture():
 return {'objects':[{'id':'new_policy','category':'image','bbox':[10,10,20,20],'geometry_hint':'Robot'}, {'id':'training_arrow','category':'arrow','bbox':[5,20,40,2],'geometry_hint':'Gray arrow, partially occluded by new-policy robot.'},{'id':'other','category':'text','bbox':[0,0,1,1],'geometry_hint':''}]}

def test_only_explicit_overlapping_unique_occlusion_reordered():
 r=fixture();order=['new_policy','training_arrow','other'];new,report=occlusion_order(r,order);assert order==['new_policy','training_arrow','other'];assert new==['training_arrow','new_policy','other'];assert report[0]['front']=='new_policy';assert occlusion_order(r,new)[0]==new

@pytest.mark.parametrize('hint,box',[('Arrow near robot',[5,20,40,2]),('Arrow occluded by new-policy robot',[100,100,20,20])])
def test_no_global_arrows_behind_rule(hint,box):
 r=fixture();r['objects'][1].update(geometry_hint=hint,bbox=box);assert occlusion_order(r,['new_policy','training_arrow','other'])[0]==['new_policy','training_arrow','other']

def test_occluded_photo_pixels_transparent_and_source_preserved(tmp_path):
 im=Image.new('RGB',(20,20),'red');before=im.tobytes();key,a,box=_raster(im,[0,0,20,20],'owner','back',tmp_path,masks=[[5,5,10,10]]);asset=Image.open(a['path']);assert asset.getpixel((8,8))[3]==0;assert asset.getpixel((1,1))==(255,0,0,255);assert im.tobytes()==before;assert a['atomic_raster_unit'] and not a['contains_reconstructable_content']

def test_no_observed_stack_edges_does_not_invent_leaves():assert stack_rectangles(Image.new('RGB',(100,100),'white'),[0,0,100,100],[20,20,60,60])==[]
