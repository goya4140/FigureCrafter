"""80AI Nano Banana Pro transport ONLY. No science, layout, review or prompt rewriting."""
from pathlib import Path
import json,hashlib,base64,requests
from urllib.parse import urlparse
from PIL import Image
import os
PRIVATE=Path(os.environ.get('FIGURECRAFTER_API_CONFIG',str(Path(__file__).resolve().parents[3]/'configs/nano.local.json')))
API=(os.environ.get('NANO_BASE_URL') or (json.loads(PRIVATE.read_text(encoding='utf-8'))['nano']['base_url'] if PRIVATE.exists() else 'https://api.80ai.net')).rstrip('/')
if API!='https://api.80ai.net':raise ValueError('Unexpected endpoint; no silent provider switch')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):Path(p).parent.mkdir(parents=True,exist_ok=True);Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2))
def key():
 v=os.environ.get('NANO_API_KEY') or (json.loads(PRIVATE.read_text())['nano'].get('api_key') if PRIVATE.exists() else None)
 if not isinstance(v,str) or not v:raise ValueError('Private Nano credential unavailable')
 return v
def clean(v,secret):
 if isinstance(v,str):return v.replace(secret,'[REDACTED]')
 if isinstance(v,list):return [clean(x,secret) for x in v]
 if isinstance(v,dict):return {k:clean(x,secret) for k,x in v.items()}
 return v
def data_url(p):
 with Image.open(p) as im:mime=Image.MIME.get(im.format,'image/png')
 return 'data:'+mime+';base64,'+base64.b64encode(Path(p).read_bytes()).decode('ascii')
def submit(req,out):
 out=Path(out);out.mkdir(parents=True,exist_ok=True)
 if (out/'intent.json').exists():
  if (out/'submission.json').exists():return {'status':'already_submitted','task_id':json.loads((out/'submission.json').read_text())['task_id'],'instruction':'Poll existing request; no duplicate submission'}
  raise ValueError('Durable submission intent exists but outcome unknown/failed; do not resubmit automatically')
 if req['mode']!='generate' or req['id']!='000' or req.get('parent'):raise ValueError('NO_EDIT_PROFILE: only initial request 000 permitted')
 a=req['image_provider_arguments'];prompt=a['prompt'];refs=a.get('referenced_image_paths',[])
 if not prompt.strip():raise ValueError('Empty image prompt') # Provider restriction lifted by user; never truncate scientific prompt.
 if len(refs)>6:raise ValueError('Nano reference count exceeds live6-image limit')
 if req['mode']=='edit':
  if len(refs)!=1 or sha(refs[0])!=req['parent_sha256']:raise ValueError('Edit must use exact reviewed parent bytes')
 elif len(refs)!=3:raise ValueError('Initial generation must retain three supplied references')
 scene='banana_pro_edit' if refs else 'banana_pro';resolution=a.get('resolution','4K')
 if resolution!='4K':raise ValueError('This experiment requests4K consistently; no silent downgrade')
 payload={'mode':'generate','model':scene,'prompt':prompt,'size':'16:9','resolution':resolution,'reference_images':[data_url(p) for p in refs]}
 intent={'request':req['id'],'semantic_mode':req['mode'],'scene':scene,'resolution':resolution,'size':'16:9','prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'prompt_characters':len(prompt),'references':[{'path':str(Path(p).resolve()),'sha256':sha(p)} for p in refs],'endpoint':API+'/api/tasks/submit','status':'intent_before_POST'}
 write(out/'intent.json',intent);secret=key()
 try:r=requests.post(API+'/api/tasks/submit',json=payload,headers={'X-API-Key':secret},timeout=(15,90),allow_redirects=False)
 except requests.RequestException as e:
  write(out/'submission-error.json',{'status':'unknown_outcome','exception_type':type(e).__name__});raise RuntimeError('Nano submission network failure with unknown outcome; preserve intent, do not duplicate') from None
 try:v=r.json()
 except ValueError:v={'message':r.text[:500]}
 v=clean(v,secret)
 if r.status_code!=200:
  write(out/'submission-error.json',{'http_status':r.status_code,'response':v,'status':'rejected'});raise RuntimeError('Nano submission rejected, HTTP '+str(r.status_code)+'; inspect redacted submission-error.json')
 task=v.get('task_id') or (v.get('task_ids') or [None])[0]
 if not task:write(out/'submission-error.json',{'http_status':r.status_code,'response':v,'status':'unknown_outcome'});raise RuntimeError('Nano missing task_id; do not duplicate')
 write(out/'submission.json',{**v,'task_id':task});return {'status':'submitted','request':req['id'],'task_id':task,'requested_model':scene,'resolution':resolution,'semantic_mode':req['mode'],'next':'Call provider_poll with same request; no resubmit'}
def poll(req,out):
 out=Path(out);submission=json.loads((out/'submission.json').read_text());task=submission['task_id'];secret=key()
 try:r=requests.get(API+'/api/tasks/'+task,headers={'X-API-Key':secret},timeout=(15,35),allow_redirects=False)
 except requests.RequestException as e:raise RuntimeError('Nano safe GET poll network failure; poll same task again, not resubmit') from None
 if r.status_code!=200:raise RuntimeError('Nano poll HTTP '+str(r.status_code)+'; retry same GET, no POST')
 v=clean(r.json(),secret);write(out/'status.json',v);intent=json.loads((out/'intent.json').read_text())
 if v.get('model') and v['model']!=intent['scene']:raise RuntimeError('Provider reported another model; keep evidence and do not accept fallback')
 state=v.get('status','unknown');ret={'request':req['id'],'task_id':task,'status':state,'reported_model':v.get('model'),'credit_cost':v.get('credit_cost'),'credit_refunded':v.get('credit_refunded')}
 if state=='success':
  image=next((x for x in v.get('images',[]) if x.get('status')=='success' and x.get('image_url')),None)
  if not image:raise RuntimeError('Nano task reports success but has no actual image; preserve evidence')
  dest=out/'result-original.bin'
  if not dest.exists():
   url=image['image_url'];u=urlparse(url)
   if u.scheme!='https' or not u.hostname:raise ValueError('Provider asset URL must be HTTPS')
   # Credentials are never forwarded to generated-image CDN or redirects.
   response=requests.get(url,timeout=(15,90));response.raise_for_status();tmp=out/'download.partial';tmp.write_bytes(response.content)
   with Image.open(tmp) as im:im.verify()
   tmp.replace(dest)
  with Image.open(dest) as im:dimensions=list(im.size);fmt=im.format
  receipt={'tool':'80ai.nano-banana-pro','request':req['id'],'task_id':task,'requested_model':intent['scene'],'reported_model':v.get('model'),'credit_cost':v.get('credit_cost'),'credit_refunded':v.get('credit_refunded'),'source_output_sha256':sha(dest),'source_output_path':str(dest.resolve()),'actual_dimensions':dimensions,'actual_format':fmt,'requested_resolution':intent['resolution'],'actual_endpoint':API+'/api/tasks','status':'success','identity_scope':'Gateway report, not independent underlying-weights attestation'}
  write(out/'framework-receipt.json',receipt);ret.update({'source_output_path':str(dest.resolve()),'dimensions':dimensions,'format':fmt})
 elif state in ['failed','error','cancelled','canceled']:
  ret['error']=v.get('error_message');ret['next']='Retain failed request; no automatic regenerate. Failed edits consume budget.'
 else:ret['next']='Wait a bounded interval then poll this same request. Do not submit again.'
 return ret
