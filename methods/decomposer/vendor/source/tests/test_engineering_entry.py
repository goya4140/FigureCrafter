import pytest
from decomposer import model
from decomposer.refinement_context import bounded

def test_completed_call_reused_only_for_exact_identity(tmp_path,monkeypatch):
 image=tmp_path/'image';image.write_bytes(b'identity');out=tmp_path/'call';calls=[]
 def fake(stage,prompt,payload,schema,image,cfg,directory):
  calls.append(stage);model.write_json(directory/'metadata.json',{'status':'complete','fallback_used':False});(directory/'response.txt').write_text('{"value": 7}');return {'value':7},{'status':'complete','fallback_used':False}
 monkeypatch.setattr(model,'invoke_json',fake);cfg={'model':{'name':'gpt-6.1-sol','effort':'medium'}}
 args=('recognition','prompt',{'input':1},{'type':'object'},image,cfg,out,None)
 assert model.fixture_or_model(*args)[0]=={'value':7};assert model.fixture_or_model(*args)[0]=={'value':7};assert len(calls)==1
 with pytest.raises(ValueError,match='identity/status differs'):model.fixture_or_model('recognition','changed',{'input':1},{'type':'object'},image,cfg,out,None)
 assert len(calls)==1

def test_graph_parent_expands_to_selectable_leaf_and_not_parent():
 payload={'candidates':[{'candidate_id':'tile','region':[0,0,10,10],'label_ids':['group']}], 'recognition':{'objects':[{'id':'group','category':'chart','bbox':[0,0,10,10]},{'id':'node','category':'shape','bbox':[1,1,2,2]}]},'representation':{'components':[{'label_id':'group','group_only':True,'members':['node'],'primitives':[]},{'label_id':'node','primitives':[{'id':'ellipse'}]}],'assets':{}},'layout':{'placements':[{'label_id':'group'},{'label_id':'node'}],'component_order':['group','node']},'provenance':{'schema_version':'v1','labels':{'node':{}}},'applicable_memories':[]}
 result=bounded(payload);assert result['scope_policy']['selectable_owner_ids']==['node'];assert result['candidates'][0]['label_ids']==['node'];assert [x['label_id'] for x in result['representation']['components']]==['node'];assert payload['candidates'][0]['label_ids']==['group']

def test_capacity_retry_preserves_failed_attempt_and_model(tmp_path,monkeypatch):
 image=tmp_path/'image';image.write_bytes(b'identity');out=tmp_path/'call';calls=[]
 def fake(stage,prompt,payload,schema,image,cfg,directory):
  calls.append(cfg['model']['name']);ok=len(calls)>1;md={'status':'complete' if ok else 'degraded','fallback_used':not ok};model.write_json(directory/'metadata.json',md);(directory/'events.jsonl').write_text('' if ok else 'Selected model is at capacity')
  if ok:(directory/'response.txt').write_text('{"value":7}')
  return ({'value':7} if ok else {}),md
 monkeypatch.setattr(model,'invoke_json',fake);monkeypatch.setattr(model.time,'sleep',lambda _:None)
 result=model.fixture_or_model('recognition','prompt',{}, {},image,{'model':{'name':'gpt-6.1-sol'}},out,None)
 assert result[0]=={'value':7};assert calls==['gpt-6.1-sol']*2;assert (out/'attempt-001/metadata.json').exists();assert (out/'retry-provenance.json').exists()

def test_measured_plain_shape_connector_uses_geometry_macro_without_reclassification():
 from decomposer.representation_recipes import decode
 owner={'id':'bus','category':'shape','bbox':[10,20,20,20],'text':'','points':[[10,20],[30,40]]}
 proposal={'styles':[{'id':'s','value':{'stroke':'#000000'}}],'plans':[{'label_id':'bus','builder':'arrow','style_id':'s','arrow_start':False,'arrow_end':False}]}
 out=decode(proposal,{'objects':[owner]},{'decisions':[]});component=out['components'][0]
 assert component['category']=='shape';assert component['primitives'][0]['points']==[[0,0],[20,20]];assert not component['primitives'][0]['arrow_end']
 for patch in [{'text':'do not drop me'},{'category':'image'},{'points':[]}]:
  with pytest.raises(ValueError):decode(proposal,{'objects':[{**owner,**patch}]},{'decisions':[]})

def test_upstream_import_requires_full_request_identity_and_does_not_call_model(tmp_path,monkeypatch):
 import json
 old=tmp_path/'old';source=old/'calls/recognition';source.mkdir(parents=True);image=old/'input-normalized.png';image.write_bytes(b'pixels');cfg={'model':{'name':'gpt-6.1-sol','effort':'medium'},'engineering':{'reuse_upstream_from_cases':[str(old)]}}
 (old/'config.json').write_text(json.dumps(cfg));(source/'metadata.json').write_text(json.dumps({'status':'complete','fallback_used':False,'model_name':'gpt-6.1-sol','seconds':12}));(source/'input.json').write_text('{"x":1}');(source/'schema.json').write_text('{"type":"object"}');(source/'prompt.txt').write_text('prompt\nUse null for optional fields that do not apply.\nINPUT:\n{"x":1}');(source/'response.txt').write_text('{"value":7}')
 def forbidden(*a,**k):raise AssertionError('Unexpected new model call')
 monkeypatch.setattr(model,'invoke_json',forbidden)
 out=tmp_path/'new/calls/recognition';result=model.fixture_or_model('recognition','prompt',{'x':1},{'type':'object'},image,cfg,out,None)
 assert result[0]=={'value':7};assert result[1]['checkpoint_reused'];assert (out/'checkpoint-import.json').exists()
 with pytest.raises(AssertionError,match='Unexpected new model call'):model.fixture_or_model('recognition','changed',{'x':1},{'type':'object'},image,cfg,tmp_path/'other/calls/recognition',None)
