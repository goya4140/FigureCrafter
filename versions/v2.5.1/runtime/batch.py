"""Mechanical per-sample staging; science and drawing remain model decisions."""
from pathlib import Path
import argparse, hashlib, json, shutil, subprocess, sys, tempfile, os
ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def read(p):return json.loads(Path(p).read_text())
def command(args):subprocess.run([sys.executable,*map(str,args)],check=True)
def prepare(n,w,model):
 items={x['number']:x for x in read(ROOT/'manifests/samples.json')['items']}
 if n not in items or items[n]['status']!='eligible':raise ValueError('Unknown or blocked sample: '+n)
 if w.exists():raise ValueError('Use a fresh, nonexistent attempt directory')
 src=ROOT/'production-inputs'/n
 evidence=read(src/'input-manifest.json')['scientific_inputs']
 for x in evidence:
  if sha(src/x['file'])!=x['sha256']:raise ValueError('Changed scientific input: '+x['file'])
 w.mkdir(parents=True)
 # Shared source has no sample-specific guides; stage only this case's guides.
 shutil.copytree(ROOT/'framework',w/'framework',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 shutil.copytree(ROOT/'production-guides'/n,w/'framework/guides')
 inp=w/'inputs';inp.mkdir();hashes={}
 for x in evidence:
  name=x['file'].replace('reference-','style-reference-')
  shutil.copyfile(src/x['file'],inp/name);hashes[name]=x['sha256']
 (inp/'input-hashes.json').write_text(json.dumps(hashes,indent=2))
 command([w/'framework/run.py','prepare','--workspace',w/'session','--inputs',inp,'--model',model])
 prompt=(ROOT/'protocol/6-astra-production-prompt.txt').read_text().replace('{{SAMPLE}}',n).replace('{{WORKSPACE}}',str(w))
 (w/'START-HERE.txt').write_text(prompt)
 (w/'sample.json').write_text(json.dumps({'number':n,'version':'2.5.1','scientific_inputs':hashes,'model_requested':model},indent=2))
 return w
p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='cmd',required=True)
s.add_parser('list');s.add_parser('verify');s.add_parser('doctor');s.add_parser('test')
a=s.add_parser('prepare');a.add_argument('sample');a.add_argument('--workspace',required=True);a.add_argument('--model',default='6-astra')
a=s.add_parser('batch');a.add_argument('--workspaces',required=True);a.add_argument('--config',required=True);a.add_argument('--samples',help='Comma-separated eligible sample numbers; default all eligible');a.add_argument('--max-turns',type=int,default=80)
a=s.add_parser('verify-frozen');a.add_argument('--workspace',required=True)
a=p.parse_args()
if a.cmd=='list':print(json.dumps(read(ROOT/'manifests/samples.json'),ensure_ascii=False,indent=2))
elif a.cmd=='verify':
 files=read(ROOT/'manifests/package-files.json')['files'];bad=[n for n,h in files.items() if not (ROOT/n).is_file() or sha(ROOT/n)!=h]
 print(json.dumps({'checked':len(files),'errors':bad}));sys.exit(bool(bad))
elif a.cmd=='doctor':command([ROOT/'framework/run.py','doctor'])
elif a.cmd=='test':
 with tempfile.TemporaryDirectory() as tmp:
  framework=Path(tmp)/'framework'
  shutil.copytree(ROOT/'framework',framework,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
  shutil.copytree(ROOT/'production-guides/001',framework/'guides')
  subprocess.run([sys.executable,'-m','unittest','discover','-s','tests'],cwd=framework,env={**os.environ,'PYTHONPATH':str(framework),'PYTHONNOUSERSITE':'1'},check=True)
elif a.cmd=='prepare':print(prepare(a.sample.zfill(3),Path(a.workspace).resolve(),a.model))
elif a.cmd=='verify-frozen':
 w=Path(a.workspace).resolve();command([w/'framework/run.py','verify','--workspace',w/'session'])
elif a.cmd=='batch':
 cfg=read(a.config);base=Path(a.workspaces).resolve();base.mkdir(parents=True,exist_ok=True)
 nums=a.samples.split(',') if a.samples else [x['number'] for x in read(ROOT/'manifests/samples.json')['items'] if x['status']=='eligible']
 for n in nums:
  n=n.zfill(3);w=base/n
  if w.exists():
   if (w/'session/freeze.json').exists():command([w/'framework/run.py','verify','--workspace',w/'session']);print('Already frozen:',n);continue
   raise ValueError('Existing unfinished attempt; retain evidence and use a new --workspaces directory: '+n)
  prepare(n,w,cfg['model'])
  # New process and conversation for every case; stop at first failure, no hidden retries.
  command([w/'framework/run.py','run','--workspace',w/'session','--config',Path(a.config).resolve(),'--max-turns',a.max_turns])
  command([w/'framework/run.py','verify','--workspace',w/'session'])
