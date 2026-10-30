from copy import deepcopy
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
import pytest
from decomposer.asset_transforms import transformed_crop
from decomposer.representation_recipes import graph_members
from decomposer.rendered_text_ink import assess
from decomposer.layout import canonicalize

def test_occluded_photo_pixels_removed_without_changing_observed_background():
 im=Image.new('RGB',(80,60),'gray');ImageDraw.Draw(im).rectangle((20,20,39,39),fill='red')
 out=np.asarray(transformed_crop(im,[0,0,80,60],[{'op':'occluded_rectangle','bbox':[20,20,20,20],'label_id':'inset'}]))
 assert (out[20:40,20:40,3]==0).all();assert (out[:20,:,3]==255).all();assert np.array_equal(out[:,:,:3],np.asarray(im))

def test_native_red_outline_does_not_delete_red_content_inside_photo():
 im=Image.new('RGB',(80,60),'gray');d=ImageDraw.Draw(im);d.rectangle((0,0,79,59),outline='#ED1745',width=4);d.rectangle((30,25,40,35),fill='#ED1745')
 out=np.asarray(transformed_crop(im,[0,0,80,60],[{'op':'native_colored_stroke','bbox':[0,0,80,60],'color':'#ED1745','stroke_width':4,'label_id':'border'}]))
 assert out[0,0,3]==0;assert out[30,35,3]==255;assert np.array_equal(out[30,35,:3],[237,23,69])

def test_native_color_inpaint_changes_only_observed_foreground_color_pixels():
 im=Image.new('RGB',(80,60),(160,160,160));d=ImageDraw.Draw(im);d.rectangle((20,20,30,30),fill='#C00000');before=np.array(im)
 after=np.asarray(transformed_crop(im,[0,0,80,60],[{'op':'native_color_inpaint','bbox':[10,10,40,40],'color':'#C00000','label_id':'label'}]))
 mask=np.zeros((60,80),bool);mask[20:31,20:31]=True
 assert np.array_equal(before[~mask],after[:,:,:3][~mask]);assert (after[:,:,3]==255).all();assert np.ptp(after[25,25,:3].astype(int))<20

def test_unknown_transform_and_absent_foreground_fail_closed():
 im=Image.new('RGB',(30,30),'white')
 for transform in [{'op':'blur','bbox':[0,0,30,30]},{'op':'native_color_inpaint','bbox':[0,0,30,30],'color':'#C00000'}]:
  with pytest.raises(ValueError):transformed_crop(im,[0,0,30,30],[transform])

def test_dark_red_text_is_measured_by_color_not_photograph_background(tmp_path):
 ref=Image.new('RGB',(140,100),(90,90,90));d=ImageDraw.Draw(ref);d.rectangle((20,20,49,49),fill='#C00000');ref.save(tmp_path/'ref.png')
 candidate=Image.new('RGB',(140,100),'white');ImageDraw.Draw(candidate).rectangle((20,20,49,49),fill='#C00000');candidate.save(tmp_path/'candidate.png')
 scene={'elements':[{'id':'label/text','source_label_id':'label','kind':'text','bbox':[0,0,140,100],'text':'WATER\nMARK','style':{'color':'#C00000','font_family':'Times New Roman','line_spacing':1}}]}
 result=assess(scene,tmp_path/'ref.png',tmp_path/'candidate.png');assert not result['failures'];assert result['assessed'][0]['ink_measurement']=='declared_color_match'
 Image.new('RGB',(140,100),'white').save(tmp_path/'candidate.png');assert assess(scene,tmp_path/'ref.png',tmp_path/'candidate.png')['failures']

def test_nonpainting_document_group_requires_measured_coverage():
 owner={'id':'group','category':'shape','semantic_level':'container','bbox':[0,0,100,100],'text':'','geometry_hint':'Six separate document crops','style_hint':{'fill':'none','stroke':'none'}}
 children=[{'id':f'c{i}','category':'shape','bbox':[i*45,0,40,80],'text':'','geometry_hint':'Document crop'} for i in range(2)]
 r={'objects':[owner]+children};assert graph_members(owner,r)==['c0','c1']
 assert not graph_members({**owner,'text':'do not drop'},r)
 assert not graph_members({**owner,'style_hint':{'fill':'#FFFFFF','stroke':'none'}},r)
 assert not graph_members(owner,{'objects':[owner,children[0]]})

def test_containing_background_moves_behind_independent_text_only():
 from decomposer.common import style
 objects=[{'id':'text','category':'text','text':'Material\nEstimator','bbox':[20,20,60,40],'geometry_hint':''},{'id':'background','category':'shape','text':'','bbox':[0,0,100,100],'geometry_hint':''},{'id':'edge','category':'arrow','text':'','bbox':[150,0,20,20],'geometry_hint':''}]
 components=[{'label_id':'text','category':'text','primitives':[{'kind':'text','style':style({})}]},{'label_id':'background','category':'shape','primitives':[{'kind':'rect','style':style({'fill':'#AAAAAA'})}]},{'label_id':'edge','category':'arrow','primitives':[]}]
 r={'canvas':{'width':200,'height':200},'objects':objects};rep={'components':components};lay,report=canonicalize({'operations':[]},r,rep)
 assert lay['component_order'].index('background')<lay['component_order'].index('text');assert lay['component_order'].index('background')<lay['component_order'].index('edge');assert not lay['degraded'];assert len(report['native_text_layer_constraints'])==1

def test_partial_overlap_does_not_reorder_labels():
 from decomposer.common import style
 r={'canvas':{'width':200,'height':200},'objects':[{'id':'t','category':'text','text':'t','bbox':[10,10,100,100],'geometry_hint':''},{'id':'b','category':'shape','text':'','bbox':[90,10,100,100],'geometry_hint':''}]};rep={'components':[{'label_id':'t','category':'text','primitives':[]},{'label_id':'b','category':'shape','primitives':[{'kind':'rect','style':style({'fill':'#AAAAAA'})}]}]}
 assert canonicalize({'operations':[]},r,rep)[0]['component_order']==['t','b']


def test_explicit_highlight_behind_part_of_paragraph_is_not_foreground():
 from decomposer.common import style
 r={'canvas':{'width':200,'height':200},'objects':[{'id':'t','category':'text','text':'line one\nline two','bbox':[10,10,150,150],'geometry_hint':''},{'id':'h','category':'shape','text':'','bbox':[10,50,150,30],'geometry_hint':'Pale yellow highlight behind two excerpt lines'}]}
 rep={'components':[{'label_id':'t','category':'text','primitives':[]},{'label_id':'h','category':'shape','primitives':[{'kind':'rect','style':style({'fill':'#FFF3CC'})}]}]}
 assert canonicalize({'operations':[]},r,rep)[0]['component_order']==['h','t']
