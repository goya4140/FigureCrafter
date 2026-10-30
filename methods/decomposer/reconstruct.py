"""Run the pinned Decomposer after an image-specific detector review."""
from pathlib import Path
import argparse,hashlib,json,os,sys,shutil
from PIL import Image
ROOT=Path(__file__).resolve().parent

def read(p): return json.loads(Path(p).read_text())
def write(p,v): Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def pinned():
    registry=read(ROOT/'decomposer-current.json')
    for key in ('source','config','knowledge_seed','runtime_fonts','source_manifest'):
        registry[key]=str((ROOT/registry[key]).resolve())
    manifest=Path(registry['source_manifest'])
    for rel,h in read(manifest).items():
        if sha(manifest.parent/rel)!=h: raise ValueError('Pinned Decomposer file changed: '+rel)
    return registry

def prepare(image,cache,sample_id,review,output,overlay=None,upstream=None):
    registry=pinned(); image=Path(image).resolve(); cache=Path(cache).resolve(); bundle=cache/sample_id
    review_path=Path(review).resolve(); review=read(review_path); record=read(bundle/'input/input.json'); candidates=bundle/'observations/candidates.json'
    if review.get('status')!='reviewed' or review.get('image_sha256')!=sha(image) or review.get('candidates_sha256')!=sha(candidates):
        raise ValueError('Detector review must match the actual image and raw candidates')
    if record['source_sha256']!=sha(image): raise ValueError('Detector source image changed')
    with Image.open(image) as im: w,h=im.size
    if w*9!=h*16: raise ValueError('Expected 16:9 canvas')
    if record['canvas']!=[w,h]: raise ValueError('Detector canvas changed')
    raw=read(candidates)
    if not all(raw['sources'][x]['status']=='ready' for x in ('sam','ocr')): raise ValueError('Both server detectors must be ready')
    cfg=read(registry['config'])
    if overlay:
        changes=read(overlay)
        for key in changes:
            if key not in {'model','runtime','refinement','engineering'}: raise ValueError('Unsupported override: '+key)
            cfg.setdefault(key,{}).update(changes[key])
    cfg['model']['method2_evidence_review_sha256']=sha(review_path)
    # Legacy completed requests predate the review-hash model identity field.
    # Import only when their captured full review and all model parameters match.
    if upstream and (Path(upstream)/'integration.json').exists():
        old_context=read(Path(upstream)/'integration.json')
        old_model=read(Path(upstream)/'config.json')['model']
        comparable={k:v for k,v in cfg['model'].items() if k!='method2_evidence_review_sha256'}
        if old_context.get('detector_review')==review and old_model==comparable:
            cfg['model']=old_model

    cfg['preprocessing']={'mode':'cache_only','cache_root':str(cache)}
    cfg['detectors']={'sam':{'command':None},'ocr':{'command':None}}
    cfg['engineering']['reuse_upstream_from_cases']=[str(Path(upstream).resolve())] if upstream else []
    cfg['engineering']['preserve_checkpoint_layout']=False
    output=Path(output).resolve()
    if output.exists(): raise ValueError('Use a fresh output directory; existing attempts are retained')
    sample={'sample_id':sample_id,'input':str(image),'sha256':sha(image),'width':w,'height':h}
    return registry,cfg,sample,review,output

def main():
    p=argparse.ArgumentParser();p.add_argument('--verify-version',action='store_true');p.add_argument('--image');p.add_argument('--cache-root');p.add_argument('--sample-id');p.add_argument('--review');p.add_argument('--output');p.add_argument('--config-overlay');p.add_argument('--upstream-case');p.add_argument('--handoff');p.add_argument('--check-only',action='store_true');a=p.parse_args()
    if a.verify_version:
        r=pinned();print(json.dumps({'status':'verified','version':r['version'],'source':r['source']},ensure_ascii=False));return
    for key in ['image','cache_root','sample_id','review','output']:
        if not getattr(a,key):p.error('--'+key.replace('_','-')+' is required')
    r,cfg,sample,review,out=prepare(a.image,a.cache_root,a.sample_id,a.review,a.output,a.config_overlay,a.upstream_case)
    if a.check_only: print(json.dumps({'status':'ready','version':r['version'],'sample':sample,'review':review['status']},ensure_ascii=False));return
    out.mkdir(parents=True);write(out/'integration.json',{'registry':r,'sample':sample,'detector_review':review,'config_overlay':a.config_overlay,'cold_e2e':not bool(a.upstream_case)})
    os.environ['DECOMPOSER_FONT_DIRS']=r['runtime_fonts'];sys.path.insert(0,r['source'])
    from decomposer.engineering import execute
    result=execute(sample,out,cfg,Path(r['knowledge_seed']));pinned();print(json.dumps(result,ensure_ascii=False),flush=True)
    if result['status']!='complete':sys.exit(2)
    if a.handoff:
        handoff=Path(a.handoff).resolve();handoff.mkdir(exist_ok=False)
        for name in ['editable.pptx','preview.png','scene.json']:shutil.copy2(out/'output'/name,handoff/name)
        write(handoff/'handoff.json',{'producer':'Decomposer-'+r['version'],'consumer':'FigureCrafter-method2-continuation.v1','source_image_sha256':sample['sha256'],'decomposer_final':{n:sha(handoff/n) for n in ['editable.pptx','preview.png','scene.json']},'decomposer_run':str(out),'immutable_reconstruction_checkpoint':True})
if __name__=='__main__':main()
