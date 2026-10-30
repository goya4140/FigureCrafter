from copy import deepcopy
import pytest
from decomposer.layout import _rotation_hint,_visible_bbox,default_layout
from decomposer.text_ownership import normalize_text_ownership
from decomposer.pptx import _libreoffice_font_environment

@pytest.mark.parametrize('hint,angle',[("Red bold text rotated vertically, reading upward.",270),("vertical text reading downward",90),("rotated counterclockwise",270),("rotated clockwise",90),("Tall label",0),("vertical box",0),("竖排文字向上阅读",270)])
def test_explicit_reading_direction(hint,angle):assert _rotation_hint({'geometry_hint':hint})==angle

def test_visible_box_keeps_edge_label_center():
 r={'objects':[{'id':'label','bbox':[84,1502,65,581],'geometry_hint':'Rotated vertically, reading upward'}]};p=default_layout(r)['placements'][0];assert p['bbox'][0]<0;assert _visible_bbox(p['bbox'],p['rotation_deg'])==[84,1502,65,581]

def fixture_duplicate(other_box=None,other_text='formed in'):
 rec={'objects':[{'id':'graph','category':'chart','bbox':[0,0,100,100],'text':''},{'id':'label','category':'text','bbox':other_box or [10,10,40,10],'text':other_text}]}
 style={'color':'#000000'}
 rep={'components':[{'label_id':'graph','width':100,'height':100,'primitives':[{'id':'edge','kind':'line'},{'id':'embedded','kind':'text','text':'formed in','bbox':[10,10,40,10],'style':style}],'primitive_order':['edge','embedded']},{'label_id':'label','width':40,'height':10,'primitives':[{'id':'text','kind':'text','text':other_text,'bbox':[0,0,40,10],'style':style}],'primitive_order':['text']}]}
 return rec,rep

def test_dedup_idempotent_and_source_preserving():
 rec,rep=fixture_duplicate();original=deepcopy(rep);new,a=normalize_text_ownership(rec,rep);assert a['removed_count']==1;assert rep==original;assert new['components'][0]['primitive_order']==['edge'];assert new['components'][1]==rep['components'][1];again,b=normalize_text_ownership(rec,new);assert again==new and b['removed_count']==0

@pytest.mark.parametrize('box,text',[([60,60,40,10],'formed in'),([10,10,40,10],'is part of')])
def test_repeated_text_elsewhere_or_different_claim_retained(box,text):
 rec,rep=fixture_duplicate(box,text);new,a=normalize_text_ownership(rec,rep);assert new==rep and a['removed_count']==0

def test_ambiguous_claim_not_deleted():
 rec,rep=fixture_duplicate();other=deepcopy(rec['objects'][1]);other['id']='other';rec['objects'].append(other);c=deepcopy(rep['components'][1]);c['label_id']='other';rep['components'].append(c);new,a=normalize_text_ownership(rec,rep);assert new==rep and a['ambiguous_retained']

def test_font_environment_does_not_depend_on_shell_wrapper_layout(tmp_path):
 env,report=_libreoffice_font_environment('/fake/wrapper/soffice',tmp_path);assert report['configured'];assert 'Supplemental' in env['SAL_FONTPATH'];assert '<cachedir>' in (tmp_path/'fonts.conf').read_text()
