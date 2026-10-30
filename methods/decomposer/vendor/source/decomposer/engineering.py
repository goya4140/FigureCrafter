"""Unified engineering candidate. No detector fallback and no silent stage repair."""
from pathlib import Path
from copy import deepcopy
import json,time,shutil,traceback
from PIL import Image,ImageChops
from .common import read_json,write_json,sha256
from .preprocessing_cache import reuse
from .recognition import run as recognition_run,validate as validate_rec
from .atomic_objects import normalize
from .asset_policy import decide_with_photo_overlays
from .representation_recipes import project,decode,SCHEMA,PROMPT,approved_images,graph_members
from .representation import canonicalize,validate as validate_rep
from .layout import canonicalize as layout_canonicalize,validate as validate_layout
from .assemble import assemble
from .pptx import compile_scene,inspect_pptx
from .model import fixture_or_model
from .text_anchor_calibration import propose
from .quality import metrics
from .provenance import build as build_provenance
from .text_ownership import normalize_text_ownership
from .render_contracts import normalize_representation_contracts
from .refinement import run as refinement_run
from .validation import validate_run

RULES='''\nPreserve EVERY source text exactly, including spaces between tokens. For separate text primitives their text concatenation must equal source text; use trailing spaces or exact source_text runs. Never outline letters with paths. Ordinary text and Unicode equations use the text builder when possible. Rotated text uses text builder: Layout applies the rotation, so do not draw vertical glyph paths. Source photo IDs in prebuilt_image_ids are already complete; exclude them from plans. Do not return empty native ops unless a graph container is completely represented by separate typed child nodes, edges and labels. All visible text requires explicit non-none color. Math markup needs correct native styled runs, not literal markup. Model produces the build plan, not a screenshot. All coordinates/font sizes are source pixels. Path coordinate_space must be explicit: primitive means path coordinates start at the primitive bbox local origin0,0; component means path coordinates are relative to the whole owner component. Do not mix these conventions.'''

def _representation_once(image,r,a,cfg,out,visual_image=None,context=None):
 payload=project(r,a)
 if context:payload.update(context)
 proposal,meta=fixture_or_model('representation',PROMPT+RULES,payload,SCHEMA,visual_image or image,cfg,out/'calls/representation',None)
 write_json(out/'representation/model-output.json',proposal)
 if meta.get('status')!='complete' or meta.get('fallback_used'):raise RuntimeError('representation model did not complete')
 def build(p,d):
  raw=decode(p,r,a);write_json(d/'text-contract-normalizations.json',raw.get('text_contract_normalizations',[]));rep,report=canonicalize(raw,r,image,d,a);validate_rep(rep,r);write_json(d/'canonicalization-report.json',report);return rep,report
 issues=[];error=None
 try:
  rep,report=build(proposal,out/'representation');issues=[x['label_id'] for x in report['repairs']]
 except (ValueError,AssertionError,KeyError) as ex:
  issues.extend(getattr(ex,'label_ids',[]));error=str(ex);fixed=approved_images(r,a);plans={p.get('label_id'):p for p in proposal.get('plans',[])}
  known={st['id'] for st in proposal.get('styles',[])}
  for owner,p in plans.items():
   refs=[p.get('style_id')]+[op.get('style_id') for op in p.get('ops',[])]+[run.get('style_id') for op in p.get('ops',[]) for run in op.get('runs',[])]
   if any(v not in known for v in refs):issues.append(owner)
  for o in r['objects']:
   if o['id'] in fixed:continue
   p=plans.get(o['id'])
   if not p or (p.get('builder')=='native' and not p.get('ops') and not graph_members(o,r)) or (p.get('builder')=='arrow' and (len(o.get('points') or [])<2 or o.get('text') or o['category'] not in {'arrow','shape'})) or (p.get('builder')=='text' and o['category']=='equation' and any(c in o['text'] for c in '\\^{}')):issues.append(o['id'])
  if not issues:raise
 if issues:
  ids=set(issues);subset={**r,'objects':[o for o in r['objects'] if o['id'] in ids]};payload=project(subset,{'decisions':[]});payload.update(previous_plans=[p for p in proposal['plans'] if p['label_id'] in ids],previous_styles=proposal['styles'],contract_error=error,required='Correct only these owners. Preserve exact text INCLUDING SPACES, never glyph paths. Reuse source positions. For token rows keep separate roomy text boxes and trailing spaces except last. Text builder for rotated words; backend rotates them. New styles required.')
  patch,pm=fixture_or_model('representation',PROMPT+RULES+'\nBounded owner contract correction only.',payload,SCHEMA,image,cfg,out/'calls/contract-repair',None)
  write_json(out/'representation/contract-repair-output.json',patch)
  if pm.get('status')!='complete' or pm.get('fallback_used'):raise RuntimeError('contract repair failed')
  decode(patch,subset,{'decisions':[]})
  rename={s['id']:'repair_'+s['id'] for s in patch['styles']}
  assert not ({s['id'] for s in proposal['styles']}&set(rename.values()))
  for s in patch['styles']:s['id']=rename[s['id']]
  for p in patch['plans']:
   p['style_id']=rename[p['style_id']]
   for op in p.get('ops',[]):
    op['style_id']=rename[op['style_id']]
    for run in op.get('runs',[]):run['style_id']=rename[run['style_id']]
  proposal={'styles':proposal['styles']+patch['styles'],'plans':[p for p in proposal['plans'] if p['label_id'] not in ids]+patch['plans']}
  write_json(out/'representation/merged-model-output.json',proposal);rep,report=build(proposal,out/'representation')
 if rep['degraded']:raise ValueError('Representation remains degraded after bounded owner repair')
 write_json(out/'representation/representation.json',rep);return rep

def representation(image,r,a,cfg,out):
 """Bound work per model response, independently validate and merge by owner ID."""
 canonical_source=cfg.get('engineering',{}).get('reuse_canonical_representation_from')
 if canonical_source:
  previous=Path(canonical_source)
  identity=next((previous/'recognition'/name for name in ['upstream-identity-recognition.json','pre-fidelity-recognition.json','recognition.json'] if (previous/'recognition'/name).exists()))
  oldr=read_json(identity)
  if oldr!=r:raise ValueError('Canonical Representation checkpoint Recognition identity changed')
  # Canonical representation may contain a verified symbol/asset conversion.
  # Reuse its paired final Recognition only after raw upstream identity matches.
  final_recognition=read_json(previous/'recognition/recognition.json')
  r.clear();r.update(deepcopy(final_recognition))
  rep=deepcopy(read_json(previous/'representation/representation.json'));validate_rep(rep,r)
  for name in ['render-contracts-input.json','fidelity-contracts-input.json']:
   if (previous/'representation'/name).exists():
    (out/'representation').mkdir(parents=True,exist_ok=True);shutil.copy2(previous/'representation'/name,out/'representation'/name)
  if rep['degraded']:raise ValueError('Cannot resume degraded canonical Representation')
  for asset in rep['assets'].values():
   oldpath=Path(asset['path']);dest=out/'representation/assets'/oldpath.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(oldpath,dest)
   if sha256(dest)!=asset['sha256']:raise ValueError('Canonical checkpoint asset changed')
   asset['path']=str(dest.resolve())
  write_json(out/'representation/representation.json',rep);write_json(out/'representation/canonical-checkpoint-import.json',{'source':str(previous.resolve()),'source_sha256':sha256(previous/'representation/representation.json'),'recognition_identity_verified':True,'assets_verified':True,'new_representation_model_calls':0})
  return rep
 retained=cfg.get('engineering',{}).get('reuse_recipe_plans_from')
 if retained:
  previous=Path(retained);oldr=read_json(previous/'recognition/recognition.json');oldobjects={o['id']:o for o in oldr['objects']}
  path=previous/'representation/merged-model-output.json'
  if not path.exists():path=previous/'representation/model-output.json'
  proposal=read_json(path);fixed=approved_images(r,a);groups={o['id']:graph_members(o,r) for o in r['objects'] if graph_members(o,r)}
  plans={p['label_id']:deepcopy(p) for p in proposal['plans']};chosen=[]
  for owner in r['objects']:
   if owner['id'] in fixed:continue
   if owner['id'] not in oldobjects or owner!=oldobjects[owner['id']]:raise ValueError('Retained plan owner changed; local new model planning required')
   plan=plans[owner['id']]
   if owner['id'] in groups:plan={'label_id':owner['id'],'builder':'native','style_id':plan['style_id'],'ops':[]}
   chosen.append(plan)
  proposal={'styles':proposal['styles'],'plans':chosen};raw=decode(proposal,r,a);rep,report=canonicalize(raw,r,image,out/'representation',a);validate_rep(rep,r)
  if rep['degraded']:raise ValueError('Retained plans require semantic model correction')
  write_json(out/'representation/retained-plan-provenance.json',{'source':str(path.resolve()),'source_sha256':sha256(path),'unchanged_owner_identity_verified':True,'new_approved_photo_leaves':sorted(fixed-set(oldobjects)),'nonpainting_groups':groups,'new_representation_model_calls':0})
  write_json(out/'representation/model-output.json',proposal);write_json(out/'representation/canonicalization-report.json',report);write_json(out/'representation/representation.json',rep)
  return rep
 limit=int(cfg.get('engineering',{}).get('representation_shard_size',40))
 threshold=int(cfg.get('engineering',{}).get('representation_shard_threshold',96))
 if len(r['objects'])<=threshold:return _representation_once(image,r,a,cfg,out)
 from concurrent.futures import ThreadPoolExecutor
 import time
 started=time.perf_counter()
 ordered=sorted(r['objects'],key=lambda o:(o['bbox'][1]+o['bbox'][3]/2,o['bbox'][0],o['id']))
 chunks=[ordered[i:i+limit] for i in range(0,len(ordered),limit)]
 write_json(out/'representation/shard-plan.json',{'max_owners_per_call':limit,'chunks':[[o['id'] for o in c] for c in chunks],'model':cfg['model'],'workers':2,'no_whole_stage_retry':True})
 def one(item):
  index,objects=item;part=out/'calls'/f'representation-shard-{index:03d}';part.mkdir(parents=True,exist_ok=True)
  x=max(0,int(min(o['bbox'][0] for o in objects))-24);y=max(0,int(min(o['bbox'][1] for o in objects))-24)
  right=min(r['canvas']['width'],int(max(o['bbox'][0]+o['bbox'][2] for o in objects))+24);bottom=min(r['canvas']['height'],int(max(o['bbox'][1]+o['bbox'][3] for o in objects))+24)
  with Image.open(image) as original:original.crop((x,y,right,bottom)).save(part/'visual-context.png')
  rr={**r,'objects':objects};cc=deepcopy(cfg);cc['engineering']['reuse_upstream_from_cases']=[]
  context={'attached_image_global_crop_bbox':[x,y,right-x,bottom-y],'visual_context_instruction':'Attached image is this source-global crop. Owner boxes remain source-global. Native op coordinates remain COMPONENT-LOCAL. Do not subtract crop offset from component-local ops. Return only supplied owners, never other visible neighbors.'}
  value=_representation_once(image,rr,a,cc,part,part/'visual-context.png',context)
  return value
 with ThreadPoolExecutor(max_workers=2) as pool:parts=list(pool.map(one,enumerate(chunks,1)))
 components={c['label_id']:c for v in parts for c in v['components']};assets={k:a for v in parts for k,a in v['assets'].items()}
 if len(components)!=sum(len(v['components']) for v in parts):raise ValueError('Duplicate cross-shard owner')
 if len(assets)!=sum(len(v['assets']) for v in parts):raise ValueError('Duplicate cross-shard asset')
 rep={'schema_version':'decomposer.representation.v1','components':[components[o['id']] for o in r['objects']],'assets':assets,'degraded':any(v['degraded'] for v in parts)}
 validate_rep(rep,r);write_json(out/'representation/representation.json',rep)
 write_json(out/'representation/shard-result.json',{'chunks':len(parts),'seconds':time.perf_counter()-started,'complete_owner_coverage':True,'components':len(components),'assets':len(assets),'model':cfg['model']})
 return rep

def calibrate(image,r,e,rep,lay,cfg,out):
 t=time.perf_counter();scene=read_json(out/'output/scene.json');ref=Image.open(image).convert('RGB');raw=Image.open(out/'output/preview.png').convert('RGB');eligible={c['label_id'] for c in rep['components'] if len(c['primitives'])==1 and c['primitives'][0]['kind']=='text'}
 candidate,report=propose(scene,r,e,image,out/'calibration',cfg,allowed_ids=eligible)
 if not report['proposals']:
  write_json(out/'calibration/acceptance.json',{'accepted_ids':[],'seconds':time.perf_counter()-t,'model_calls':0,'reason':'no_eligible_proposals'});return rep,lay
 write_json(out/'calibration-candidate/scene.json',candidate);compile_scene(out/'calibration-candidate/scene.json',out/'calibration-candidate',cfg,True);preview=Image.open(out/'calibration-candidate/preview.png').convert('RGB');originals={x['id']:x for x in scene['elements']};accept=set();gates=[]
 for p in report['proposals']:
  x,y,w,h=p['source_ink_bbox'];roi=[max(0,x-6),max(0,y-10),w+12,h+20];before=metrics(ref,raw,roi);after=metrics(ref,preview,roi);ok=after['pixel_similarity']>=before['pixel_similarity'] and after['edge_iou']>=before['edge_iou'];gates.append({'id':p['id'],'accepted':ok,'before':before,'after':after})
  if ok:accept.add(p['id'])
 proposed={p['id'] for p in report['proposals']};candidate['elements']=[originals[x['id']] if x['id'] in proposed-accept else x for x in candidate['elements']]
 rr,ll=deepcopy(rep),deepcopy(lay);cs={c['label_id']:c for c in rr['components']};ps={p['label_id']:p for p in ll['placements']}
 for element in candidate['elements']:
  if element['id'] not in accept:continue
  c=cs[element['source_label_id']];p=ps[element['source_label_id']];x,y,w,h=element['bbox'];c['width']=w;c['height']=h;c['primitives'][0]['bbox']=[0,0,w,h];c['primitives'][0]['style']=deepcopy(element['style']);p.update(bbox=[x,y,w,h],font_scale=1.)
 validate_rep(rr,r);validate_layout(ll,rr,r);replay=assemble(r,rr,ll)
 assert replay==candidate,'Canonical stage replay differs from calibrated scene'
 write_json(out/'calibration-final/scene.json',replay);compile_scene(out/'calibration-final/scene.json',out/'calibration-final',cfg,True);final=Image.open(out/'calibration-final/preview.png').convert('RGB');before=metrics(ref,raw);after=metrics(ref,final);ok=after['pixel_similarity']>=before['pixel_similarity'] and after['edge_iou']>=before['edge_iou']
 if ok:
  shutil.copytree(out/'calibration-final',out/'output',dirs_exist_ok=True);rep,lay=rr,ll;write_json(out/'representation/representation.json',rep);write_json(out/'layout/layout.json',lay)
 write_json(out/'calibration/acceptance.json',{'accepted_ids':sorted(accept) if ok else [],'overall_accepted':ok,'before':before,'after':after,'gates':gates,'canonical_scene_replay_equal':True,'model_calls':0,'seconds':time.perf_counter()-t});return rep,lay

def execute(sample,out,cfg,knowledge_seed):
 out=Path(out);out.mkdir(parents=True,exist_ok=True);cfg=deepcopy(cfg);t=time.perf_counter();timings={};phase='input';write_json(out/'status.json',{'status':'running','phase':phase,'sample_id':sample['sample_id']})
 def stage(name,fn):
  nonlocal phase
  phase=name;write_json(out/'status.json',{'status':'running','phase':phase,'sample_id':sample['sample_id']});tick=time.perf_counter();v=fn();timings[name]=time.perf_counter()-tick;write_json(out/'timings.json',timings);return v
 try:
  _,e=stage('preprocessing_cache',lambda:reuse({**sample,'local_input':sample['input']},out,cfg['preprocessing']['cache_root']));image=out/'input-normalized.png';(out/'input').mkdir(exist_ok=True);shutil.copy2(image,out/'input/normalized.png');write_json(out/'input/input.json',{'source':sample['input'],'source_sha256':sample['sha256'],'canvas':[sample['width'],sample['height']]})
  
  if not (out/'knowledge').exists():shutil.copytree(knowledge_seed,out/'knowledge')
  cfg['refinement']['knowledge_path']=str((out/'knowledge').resolve());write_json(out/'config.json',cfg)
  raw=stage('recognition',lambda:recognition_run(image,(sample['width'],sample['height']),e,cfg,out/'recognition',out/'calls'))
  md=read_json(out/'calls/recognition/metadata.json')
  if md['status']!='complete' or md.get('fallback_used'):raise RuntimeError('Recognition did not complete')
  write_json(out/'recognition/raw-recognition.json',raw);r,norm=normalize(raw,e);validate_rec(r);write_json(out/'recognition/normalization.json',norm)
  write_json(out/'recognition/recognition.json',r);write_json(out/'recognition/normalized-recognition.json',r)
  a=stage('asset_policy',lambda:decide_with_photo_overlays(r,e,cfg,image));write_json(out/'representation/asset-decisions.json',a)
  write_json(out/'recognition/upstream-identity-recognition.json',r)
  rep=stage('representation',lambda:representation(image,r,a,cfg,out))
  rep,contracts=normalize_representation_contracts(r,rep,e,image,out/'representation',cfg);validate_rep(rep,r);write_json(out/'representation/render-contracts.json',contracts)
  from .fidelity_contracts import normalize as fidelity_normalize, source_occlusions
  write_json(out/'recognition/pre-fidelity-recognition.json',r)
  r,rep,fidelity=fidelity_normalize(r,rep,image,out/'representation',cfg)
  r['source_occlusion_constraints']=source_occlusions(r,rep,image)
  fidelity['source_occlusions']=r['source_occlusion_constraints']
  write_json(out/'recognition/recognition.json',r);write_json(out/'recognition/normalized-recognition.json',r)
  write_json(out/'representation/fidelity-contracts.json',fidelity);validate_rec(r);validate_rep(rep,r)
  rep,ownership=normalize_text_ownership(r,rep);validate_rep(rep,r);write_json(out/'representation/text-ownership-report.json',ownership);write_json(out/'representation/representation.json',rep)
  lay,lr=layout_canonicalize({'operations':[]},r,rep);
  if cfg.get('engineering',{}).get('reuse_canonical_representation_from') and cfg.get('engineering',{}).get('preserve_checkpoint_layout',False):
   previous_layout=read_json(Path(cfg['engineering']['reuse_canonical_representation_from'])/'layout/layout.json')
   previous_placements={p['label_id']:p for p in previous_layout['placements']}
   rotated={x['owner'] for x in fidelity['rotations']}
   # Keep measured accepted upstream placements, while applying new source-
   # proven directions and fresh occlusion order. No stale whole-layout reset.
   lay['placements']=[p if p['label_id'] in rotated else previous_placements[p['label_id']] for p in lay['placements']]
   validate_layout(lay,rep,r)
  write_json(out/'layout/layout.json',lay);write_json(out/'layout/report.json',{'mode':'deterministic','model_calls':0,**lr})
  def initial():
   scene=assemble(r,rep,lay);write_json(out/'output/scene.json',scene);compile_scene(out/'output/scene.json',out/'output',cfg,True)
  stage('composition_compile',initial);rep,lay=stage('text_calibration',lambda:calibrate(image,r,e,rep,lay,cfg,out));build_provenance(read_json(out/'output/scene.json'),out/'output/editable.pptx',out/'output');validate_run(out,image,cfg);shutil.copytree(out/'output',out/'checkpoints/pre-refinement',dirs_exist_ok=True)
  refinement=stage('refinement',lambda:refinement_run(out,image,cfg))
  if refinement.get('stop_reason') in {'candidate_rejected'} and refinement.get('accepted_rounds',0)==0:
   trace=read_json(out/'refinement/round-001/assessment.json') if (out/'refinement/round-001/assessment.json').exists() else {}
   if trace.get('rejection_reason') in {'local_text_or_neighbor_regression','affected_native_text_ink_collapse'} and trace.get('accepted') is False:
    cfg['refinement']['max_rounds']=2;write_json(out/'refinement/exception-policy.json',{'max_rounds':2,'reason':trace.get('rejection_reason'),'same_loop':True,'new_actionable_attempt_required':True});refinement=stage('refinement_exception',lambda:refinement_run(out,image,cfg))
  if refinement.get('stop_reason')=='diagnosis_failed':raise RuntimeError('Refinement diagnosis model did not complete')
  scene=read_json(out/'output/scene.json');assert not scene['degraded'];structure=inspect_pptx(out/'output/editable.pptx');assert not structure['is_single_screenshot_like'];validation=stage('validation',lambda:validate_run(out,image,cfg))
  hard=[x for x in validation['failures'] if x.get('code')!='visual-quality-below-threshold'];assert not hard,hard
  imports=[read_json(p) for p in (out/'calls').glob('*/checkpoint-import.json')]
  reused_seconds=sum(x.get('historical_successful_model_seconds') or 0 for x in imports)
  result={'replayed_upstream_model_calls':len(imports),'reused_historical_model_seconds':reused_seconds,'active_execution_seconds_including_reused_model_work':time.perf_counter()-t+reused_seconds,'time_scope':'seconds is current revision execution; imported upstream historical API seconds recorded separately, not a newly rerun cold E2E','status':'complete','sample_id':sample['sample_id'],'seconds':time.perf_counter()-t,'timings':timings,'refinement':refinement,'validation_passed':validation['passed'],'validation_failures':validation['failures'],'structure':structure,'final_scene_sha256':sha256(out/'output/scene.json'),'final_preview_sha256':sha256(out/'output/preview.png'),'detector_calls':0};write_json(out/'status.json',result);return result
 except Exception as ex:
  (out/'error.log').write_text(traceback.format_exc());result={'status':'failed','sample_id':sample['sample_id'],'phase':phase,'error':str(ex),'seconds':time.perf_counter()-t,'timings':timings};write_json(out/'status.json',result);return result
