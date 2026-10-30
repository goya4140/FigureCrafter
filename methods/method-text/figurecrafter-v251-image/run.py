#!/usr/bin/env python3
"""FigureCrafter scientific loop with a raster Nano Banana Pro generate/edit backend."""
from pathlib import Path
import argparse,hashlib,json,shutil,sys,math
from datetime import datetime,timezone
from PIL import Image
import pymupdf
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT));BASE=ROOT.parent/'drawing-v2.5.1';sys.path.insert(0,str(BASE))
from harness import standalone as fc
VERSION='figurecrafter-v251-image.nanopro.noedit60.1'
MAX_EDIT_REQUESTS=0
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def read(p):return json.loads(Path(p).read_text())
def source_hashes():
 files=[ROOT/'run.py',ROOT/'WORKFLOW.md',ROOT/'edit_budget.py',ROOT/'nano_transport.py']+[p for folder in ['harness','guides','protocol'] for p in (BASE/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.md','.txt'}]
 return {str(p):sha(p) for p in files}
def prepare(workspace,inputs,model,context=()):
 for p in map(Path,context):
  if not p.is_file() or p.suffix not in {'.md','.txt'}:raise ValueError('Only explicit upstream planning text is allowed')
 w=fc.prepare(workspace,inputs,model);(w/'image-iterations').mkdir();(w/'requests').mkdir();(w/'context').mkdir()
 for i,p in enumerate(map(Path,context)):shutil.copy2(p,w/'context'/f'planning-{i:02d}{p.suffix}')
 shutil.copy2(w/'MODEL.txt',w/'MODEL.native-inherited.txt');shutil.copy2(ROOT/'WORKFLOW.md',w/'IMAGE_WORKFLOW.md')
 (w/'MODEL.txt').write_text((w/'MODEL.native-inherited.txt').read_text()+'\n\n'+(ROOT/'WORKFLOW.md').read_text())
 write(w/'image-contract.json',{'version':VERSION,'scientific_input_contract':'verbatim dataset Method + three ordered reference PNGs','output':'raster PNG and raster PDF','native_authoring':False,'max_edit_requests':MAX_EDIT_REQUESTS,'edit_budget_scope':'all edit requests across branches in this sample session; generation never resets budget','upstream_context':{str(p.relative_to(w)):sha(p) for p in (w/'context').iterdir()},'framework_source_hashes':source_hashes()})
 return w
READ_TOOLS={'list_files','read_text','write_file','view_image'}
TOOLS=[t for t in fc.TOOLS if t['name'] in READ_TOOLS]+[
 fc.schema('image_request','Bind generate/edit intent to scientific notes and reviewed source image. Returns Nano Banana Pro arguments.',{'mode':fc.S,'prompt':fc.S,'parent':fc.S,'findings':{'type':'array','items':fc.S}},['mode','prompt']),
 fc.schema('image_result','Register actual Nano Banana Pro output; no figure content is authored by this importer.',{'request':fc.S,'image':fc.S,'receipt':fc.S}),
 fc.schema('image_review','Record full-image and publication visual/scientific review after actual pixel observation.',{'iteration':fc.S,'path':fc.S}),
 fc.schema('finish','Select accepted raster iteration and freeze final PNG/PDF plus review/caption.',{'iteration':fc.S,'caption':fc.S})]
TOOLS += [fc.schema('export_initial','Export reviewed initial image even when needs_edit; explicitly NOT scientific acceptance.',{'iteration':fc.S,'caption':fc.S})]
TOOLS += [fc.schema('provider_submit','Submit exactly one reserved Nano Banana Pro request; durable intent prevents duplicate submission. No scientific planning occurs in transport.',{'request':fc.S}), fc.schema('provider_poll','Poll existing task once, import actual successful bytes and return image paths. Never automatically retry generation.',{'request':fc.S})]
class Session(fc.Session):
 def validate(self):
  c=read(self.root/'image-contract.json')
  for name,h in {**c['framework_source_hashes'],**{str(self.root/p):h for p,h in c['upstream_context'].items()}}.items():
   if sha(name)!=h:raise ValueError('Frozen framework/planning input changed: '+name)
  fc.core.verify(self.run)
  for folder in (self.root/'requests').iterdir():
   if (folder/'request.json').exists():
    v=read(folder/'request.json')
    if sha(folder/'prompt.txt')!=v['prompt_sha256']:raise ValueError('Request prompt changed')
    for p,h in v['note_hashes'].items():
     if sha(folder/p)!=h:raise ValueError('Request scientific notes changed')
    if v.get('parent_image') and sha(self.root/v['parent_image'])!=v['parent_sha256']:raise ValueError('Edit parent changed')
  for folder in (self.root/'image-iterations').iterdir():
   if (folder/'iteration.json').exists():
    v=read(folder/'iteration.json')
    for name,h in v['hashes'].items():
     if sha(folder/name)!=h:raise ValueError('Image iteration changed: '+name)
    if (folder/'review-record.json').exists():
     rv=read(folder/'review-record.json')
     if sha(folder/'review.json')!=rv['review_sha256']:raise ValueError('Recorded image review changed')
  return c
 def observed_required(self,paths):
  events=[json.loads(l) for l in self.events.read_text().splitlines()] if self.events.exists() else []
  seen={e['sha256'] for e in events if e['type']=='image_observed'}
  if any(sha(self.root/p) not in seen for p in paths):raise ValueError('Actual pixel observations required')
 def iteration(self,index):
  if len(index)!=3 or not index.isdigit():raise ValueError('Iteration must be3 digits')
  folder=self.root/'image-iterations'/index
  if not (folder/'iteration.json').exists():raise ValueError('Unknown iteration')
  return folder
 def call(self,name,a):
  if (self.root/'freeze.json').exists():raise ValueError('Session frozen')
  if name not in {t['name'] for t in TOOLS}:raise ValueError('Native authoring is disabled for FigureCrafter-Image')
  spec=next(t['parameters'] for t in TOOLS if t['name']==name)
  if set(a)-set(spec['properties']) or set(spec['required'])-set(a):raise ValueError('Unexpected/missing arguments')
  self.log({'type':'tool_call','name':name,'arguments':a});self.validate()
  try:result=self.dispatch(name,a)
  except Exception as ex:self.log({'type':'tool_error','name':name,'error':str(ex)});raise
  self.log({'type':'tool_result','name':name,'result':result})
  if name in {'finish','export_initial'}:write(self.root/'freeze.json',{'version':VERSION,'selected_iteration':a['iteration'],'files':{str(p.relative_to(self.root)):sha(p) for p in self.root.rglob('*') if p.is_file()},'quality':read(self.root/'delivery/visual-review.json')['status'], 'human_acceptance':'pending', 'export_kind':name})
  return result
 def dispatch(self,name,a):
  if name in {'provider_submit','provider_poll'}:
   from nano_transport import submit,poll
   index=a['request']
   if len(index)!=3 or not index.isdigit():raise ValueError('Request index must be3 digits')
   folder=self.root/'requests'/index;req=read(folder/'request.json');transport=self.root/'work/provider'/index
   if name=='provider_submit':return submit(req,transport)
   status=poll(req,transport)
   if status['status']=='success':
    if (self.root/'image-iterations'/index).exists():
     status.update({'iteration':index,'image_path':str(self.root/'image-iterations'/index/'candidate.png'),'publication':f'image-iterations/{index}/publication.png'});return status
    result=self.dispatch('image_result',{'request':index,'image':str(transport/'result-original.bin'),'receipt':str((transport/'framework-receipt.json').relative_to(self.root))});status.update(result)
   return status
  if name in READ_TOOLS:return super().dispatch(name,a)
  if name=='image_request':
   if a['mode']!='generate':raise ValueError('NO_EDIT_PROFILE: semantic edits forbidden before any provider dispatch')
   if any((self.root/'requests').glob('*/request.json')):raise ValueError('ONE_INITIAL_ONLY: resume existing request; never regenerate')
   prompt=self.path(a['prompt']);notes=[self.root/'work'/f'{n}.md' for n in ['understanding','representation','decision']]
   if any(not p.is_file() or not p.read_text().strip() for p in notes):raise ValueError('Scientific planning notes required')
   self.observed_required([f'run/inputs/style-reference-{i:02d}.png' for i in [1,2,3]])
   events=[json.loads(l) for l in self.events.read_text().splitlines()]
   if not any(e['type']=='method_text_read' and e['sha256']==sha(self.root/'run/inputs/method.txt') for e in events):raise ValueError('Hash-bound Method text reading required')
   parent=None;findings=a.get('findings',[])
   if a['mode']=='edit':
    # Reserve budget before any provider dispatch; failed/abandoned requests
    # count too, so branching or another generation cannot reset the limit.
    limit=min(MAX_EDIT_REQUESTS,int(read(self.root/'image-contract.json').get('max_edit_requests',MAX_EDIT_REQUESTS)))
    used=sum(read(p)['mode']=='edit' for p in (self.root/'requests').glob('*/request.json'))
    if used>=limit:raise ValueError(f'Image edit budget exhausted: {used}/{limit}; retain and review existing outputs, do not issue another edit')
    parent=self.iteration(a.get('parent',''));rv=read(parent/'review.json')
    available={x['id'] for x in rv['findings']}
    if not findings or not set(findings)<=available:raise ValueError('Edit must identify parent review findings')
    self.observed_required([str((parent/n).relative_to(self.root)) for n in ['candidate.png','publication.png']])
   elif a.get('parent'):raise ValueError('Generation cannot consume an edited result')
   index=f'{len(list((self.root/"requests").iterdir())):03}'
   if a['mode']=='edit':
    pool=read(self.root/'image-contract.json').get('shared_edit_budget')
    if pool:
     from edit_budget import reserve
     reserve(Path(pool),str(self.root),index,sha(prompt),limit=0)
   folder=self.root/'requests'/index;folder.mkdir();shutil.copy2(prompt,folder/'prompt.txt')
   for p in notes:shutil.copy2(p,folder/p.name)
   params={'prompt':prompt.read_text(),'resolution':'4K','referenced_image_paths':[str(parent/'candidate.png')] if parent else [str(self.root/f'run/inputs/style-reference-{i:02d}.png') for i in [1,2,3]]}
   v={'id':index,'mode':a['mode'],'parent':a.get('parent'),'findings':findings,'parent_image':str((parent/'candidate.png').relative_to(self.root)) if parent else None,'parent_sha256':sha(parent/'candidate.png') if parent else None,'prompt_sha256':sha(folder/'prompt.txt'),'note_hashes':{p.name:sha(folder/p.name) for p in notes},'image_provider_arguments':params,'tool':'80ai.nano-banana-pro','provider_model_id':'banana_pro_edit','status':'requested'}
   write(folder/'request.json',v);return {'request':index,'image_provider_arguments':params,'execution':'operator must call actual Nano Banana Pro task API then image_result; no simulated image backend'}
  if name=='image_result':
   index=a['request']
   if len(index)!=3 or not index.isdigit():raise ValueError('Invalid request')
   req=read(self.root/'requests'/index/'request.json');out=self.root/'image-iterations'/index
   if out.exists():raise ValueError('Request already has a result; use a new request')
   receipt=read(self.path(a['receipt']));image=Path(a['image']).resolve()
   if receipt.get('tool')!='80ai.nano-banana-pro' or receipt.get('request')!=index or receipt.get('source_output_sha256')!=sha(image):raise ValueError('Actual output receipt mismatch')
   with Image.open(image) as im:
    im.verify()
   with Image.open(image) as im:w,h=im.size
   if abs(w/h-16/9)>.025:raise ValueError('Request a16:9 image; gross aspect mismatch is not silently corrected')
   out.mkdir();shutil.copy2(image,out/'raw.png');shutil.copy2(self.path(a['receipt']),out/'provider-receipt.json')
   k=max(math.ceil(w/16),math.ceil(h/9));target=(16*k,9*k);offset=((target[0]-w)//2,(target[1]-h)//2)
   with Image.open(image) as im:
    if im.size==target and im.format=='PNG':shutil.copy2(image,out/'candidate.png')
    elif im.size==target:im.convert('RGB').save(out/'candidate.png')
    else:
     canvas=Image.new('RGB',target,'white');canvas.paste(im.convert('RGB'),offset);canvas.save(out/'candidate.png')
   with Image.open(out/'candidate.png') as im:im.resize((1063,598),Image.Resampling.LANCZOS).save(out/'publication.png')
   write(out/'iteration.json',{'request':index,'mode':req['mode'],'parent':req['parent'],'parent_sha256':req['parent_sha256'],'findings':req['findings'],'raw_dimensions':[w,h],'canvas':list(target),'padding_offset':list(offset),'padding':'white borders if needed and lossless PNG encoding; no figure content editing/stretch/crop','provider_execution':'actual Nano Banana Pro task API; receipt is operator attestation','hashes':{n:sha(out/n) for n in ['raw.png','candidate.png','publication.png','provider-receipt.json']}})
   return {'iteration':index,'image_path':str(out/'candidate.png'),'publication':str((out/'publication.png').relative_to(self.root))}
  if name=='image_review':
   folder=self.iteration(a['iteration']);self.observed_required([str((folder/n).relative_to(self.root)) for n in ['candidate.png','publication.png']]);v=read(self.path(a['path']))
   if (folder/'review.json').exists():raise ValueError('Review already recorded')
   if v.get('status') not in {'accepted','needs_edit'} or not v.get('observations') or not isinstance(v.get('findings'),list) or 'limitations' not in v:raise ValueError('Incomplete scientific/visual review')
   ids=[]
   for x in v['findings']:
    if not all(k in x for k in ['id','severity','region','problem','source']) or x['severity'] not in {'high','medium','low'}:raise ValueError('Invalid review finding')
    ids.append(x['id'])
   if len(ids)!=len(set(ids)):raise ValueError('Duplicate finding IDs')
   if v['status']=='accepted' and any(x['severity']=='high' for x in v['findings']):raise ValueError('Unresolved high-severity finding blocks selection')
   shutil.copy2(self.path(a['path']),folder/'review.json');write(folder/'review-record.json',{'review_sha256':sha(folder/'review.json'),'candidate_sha256':sha(folder/'candidate.png'),'reviewer':'active model; not independent human acceptance'});return {'status':v['status'],'review':str((folder/'review.json').relative_to(self.root))}
  if name in {'finish','export_initial'}:
   if a['iteration']!='000':raise ValueError('Only the first initial image is exportable')
   folder=self.iteration(a['iteration']);rv=read(folder/'review.json')
   if name=='finish' and rv['status']!='accepted':raise ValueError('Selected iteration must have accepted review')
   self.observed_required([str((folder/n).relative_to(self.root)) for n in ['candidate.png','publication.png']]);caption=self.path(a['caption'])
   if not caption.read_text().strip():raise ValueError('Caption required')
   out=self.root/'delivery';out.mkdir(exist_ok=False)
   for source,dest in [('candidate.png','main-figure.png'),('publication.png','publication-preview.png'),('review.json','visual-review.json'),('iteration.json','image-lineage.json')]:shutil.copy2(folder/source,out/dest)
   shutil.copy2(caption,out/'figure-caption.md')
   doc=pymupdf.open();p=doc.new_page(width=960,height=540);p.insert_image(p.rect,filename=str(folder/'candidate.png'));doc.set_metadata({'title':'FigureCrafter-Image: raster scientific figure'});doc.save(out/'main-figure.pdf');doc.close()
   with pymupdf.open(out/'main-figure.pdf') as doc:doc[0].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5)).save(out/'pdf-preview.png')
   write(out/'delivery.json',{'version':VERSION,'selected_iteration':a['iteration'],'format':'raster only; no native object-level editability','figure_edits':'disabled; zero edit experiment','quality_status':rv['status'],'export_kind':name,'outer_evolution':False,'human_acceptance':'pending','hashes':{p.name:sha(p) for p in out.iterdir() if p.is_file()}})
   return {'delivery':str(out),'selected_iteration':a['iteration'],'format':'PNG + raster PDF','human_acceptance':'pending'}
  raise ValueError('Unknown tool')
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest='command',required=True)
 a=sp.add_parser('prepare')
 for n in ['workspace','inputs','model']:a.add_argument('--'+n,required=True)
 a.add_argument('--context',action='append',default=[])
 a=sp.add_parser('call');a.add_argument('--workspace',required=True);a.add_argument('--json',required=True)
 a=sp.add_parser('verify');a.add_argument('--workspace',required=True)
 sp.add_parser('tools');a=p.parse_args()
 if a.command=='prepare':v={'workspace':str(prepare(a.workspace,a.inputs,a.model,a.context))}
 elif a.command=='call':c=read(a.json);v=Session(a.workspace).call(c['name'],c.get('arguments',{}))
 elif a.command=='verify':Session(a.workspace).validate();v=fc.verify_frozen(a.workspace)
 else:v=TOOLS
 print(json.dumps(v,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
