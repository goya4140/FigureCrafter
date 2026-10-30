from PIL import Image,ImageDraw
from decomposer.render_contracts import path_space
from decomposer.representation_recipes import decode
from decomposer.fidelity_contracts import source_occlusions,normalize,activation_pixels
from decomposer.common import style

def test_frame_supported_origin_and_ambiguous_counterexample():
    assert path_space({'bbox':[34,34,158,158],'path':'M0 0L158 158M158 0L0 158'})[0]=='primitive'
    assert path_space({'bbox':[53,0,149,255],'path':'M0 0L149 255M0 255L149 0'})[0]=='primitive'
    assert path_space({'bbox':[4,4,218,238],'path':'M218 0H29Q0 0 0 29V209'})[0] is None

def test_sparse_run_inherits_font_and_respects_explicit_size():
    o={'id':'code','category':'text','text':'import x','bbox':[0,0,300,80],'geometry_hint':''}
    data={'styles':[{'id':'base','value':{'font_family':'Courier New','font_size':36,'color':'#111111'}},{'id':'red','value':{'color':'#FF0000'}},{'id':'small','value':{'color':'#111111','font_size':12}}], 'plans':[{'label_id':'code','builder':'native','style_id':'base','ops':[{'id':'code','kind':'text','style_id':'base','bbox':[0,0,300,80],'text':'import x','runs':[{'text':'import ','source_text':'import ','style_id':'red','script':'normal'},{'text':'x','source_text':'x','style_id':'small','script':'normal'}]}]}]}
    runs=decode(data,{'objects':[o]},{'decisions':[]})['components'][0]['primitives'][0]['runs'];assert runs[0]['style']['font_family']=='Courier New' and runs[0]['style']['font_size']==36;assert runs[1]['style']['font_size']==12

def test_token_background_is_native_and_text_is_preserved(tmp_path):
    image=Image.new('RGB',(60,60),'#FFF2CC');ImageDraw.Draw(image).rectangle((20,20,30,35),fill='black');p=tmp_path/'input.png';image.save(p)
    rec={'objects':[{'id':'token','category':'text','text':'6a','bbox':[0,0,60,60],'geometry_hint':'Token with pale yellow background'}]};prim={'id':'text','kind':'text','bbox':[0,0,60,60],'text':'6a','style':style({},text=True),'editable':True};rep={'components':[{'label_id':'token','category':'text','width':60,'height':60,'primitives':[prim],'primitive_order':['text']}],'assets':{}}
    r,out,report=normalize(rec,rep,p,tmp_path/'rep',{});assert len(report['backgrounds'])==1;assert out['components'][0]['primitives'][0]['style']['fill']=='#FFF2CC';assert out['components'][0]['primitives'][1]['text']=='6a';assert rep['components'][0]['primitive_order']==['text']

def test_line_requires_observed_occlusion(tmp_path):
    r={'objects':[{'id':'guide','category':'shape','bbox':[10,50,280,1]},{'id':'robot','category':'image','bbox':[110,20,80,60],'text':''}]};p={'id':'line','kind':'polyline','points':[[0,0],[280,0]],'style':{'stroke':'#FF0000','stroke_width':2,'dashed':True}};rep={'components':[{'label_id':'guide','primitives':[p]}],'assets':{}}
    im=Image.new('RGB',(300,100),'white');ImageDraw.Draw(im).line((10,50,290,50),fill='red',width=2);path=tmp_path/'source.png';im.save(path);assert source_occlusions(r,rep,path)==[]
    ImageDraw.Draw(im).rectangle((110,20,190,80),fill='gray');im.save(path);relations=source_occlusions(r,rep,path);assert len(relations)==1 and relations[0]['front']=='robot'

def test_atomic_field_masks_preserve_observed_pixels():
    im=Image.new('RGB',(40,40),'blue');pixels,box=activation_pixels(im,[0,0,40,40],[[0,0],[40,0],[40,40],[0,40]],2,[{'circles':[[10,10,10,10]]}]);assert pixels.getpixel((15,15))[3]==0;assert pixels.getpixel((25,25))==(0,0,255,255)

def graph_fixture():
    r={'canvas':{'width':300,'height':200},'objects':[{'id':'graph','category':'chart','bbox':[20,20,200,100],'text':''}]}
    ps=[{'id':v,'kind':'ellipse','bbox':b,'style':style({'fill':'#CCCCCC'}),'editable':True} for v,b in [('a',[10,10,30,30]),('b',[80,10,30,30])]]
    rep={'components':[{'label_id':'graph','category':'chart','width':200,'height':100,'primitives':ps,'primitive_order':['a','b']}],'assets':{}}
    lay={'placements':[{'label_id':'graph','bbox':[20,20,200,100],'rotation_deg':0,'visible':True,'font_scale':1}],'component_order':['graph']}
    return r,rep,lay

def test_distinct_group_primitives_commit_together_without_mutating_input():
    from decomposer.refinement import apply_patch
    r,rep,lay=graph_fixture();decision={'owner_stage':'representation','affected_label_ids':['graph'],'operations':[{'op':'primitive_bbox','label_id':'graph','primitive_id':v,'bbox':b} for v,b in [('a',[12,12,30,30]),('b',[82,12,30,30])]]};out=apply_patch(r,rep,lay,decision)[1];assert out['components'][0]['primitives'][0]['bbox']==[12,12,30,30];assert out['components'][0]['primitives'][1]['bbox']==[82,12,30,30];assert rep['components'][0]['primitives'][0]['bbox']==[10,10,30,30]

def test_duplicate_primitive_group_edit_is_rejected():
    import pytest
    from decomposer.refinement import apply_patch
    from decomposer.local_repair import RepairPreflightError
    r,rep,lay=graph_fixture();op={'op':'primitive_bbox','label_id':'graph','primitive_id':'a','bbox':[12,12,30,30]}
    with pytest.raises(RepairPreflightError):apply_patch(r,rep,lay,{'owner_stage':'representation','affected_label_ids':['graph'],'operations':[op,op]})

def test_rounding_requires_valid_radius_and_retains_editable_path():
    import pytest
    from decomposer.refinement import apply_patch
    r,rep,lay=graph_fixture();p=rep['components'][0]['primitives'][0];p['kind']='rect';op={'op':'primitive_rounding','label_id':'graph','primitive_id':'a','number':8};out=apply_patch(r,rep,lay,{'owner_stage':'representation','affected_label_ids':['graph'],'operations':[op]})[1];prim=out['components'][0]['primitives'][0];assert prim['kind']=='path' and prim['coordinate_space']=='primitive' and prim['editable'];op['number']=100
    with pytest.raises(ValueError):apply_patch(r,rep,lay,{'owner_stage':'representation','affected_label_ids':['graph'],'operations':[op]})
