"""Portable staging/inspection only. Never interprets papers or generates layouts."""
from pathlib import Path
import argparse, hashlib, json, os, platform, shutil, subprocess, sys, tempfile

PACKAGE = Path(__file__).resolve().parents[1]

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2))
def config():
    path = PACKAGE / 'runtime/runtime.local.json'
    return json.loads(path.read_text()) if path.exists() else {}
def binary(value, default):
    candidate = value or shutil.which(default)
    if not candidate or not Path(candidate).is_file(): return None
    return str(Path(candidate).resolve())
def verify():
    manifest = json.loads((PACKAGE/'manifests/package-files.json').read_text())
    failures = []
    for name, expected in manifest['files'].items():
        p = PACKAGE/name
        if not p.is_file() or sha(p) != expected: failures.append(name)
    print(json.dumps({'checked':len(manifest['files']), 'errors':failures}, ensure_ascii=False))
    return not failures
def doctor(args):
    cfg=config(); errors=[]; checks={}
    if cfg.get('fontconfig_file'):os.environ['FONTCONFIG_FILE']=cfg['fontconfig_file']
    checks['python']={'version':platform.python_version(), 'executable':sys.executable}
    for mod in ['pymupdf','pptx','PIL','lxml','xlsxwriter']:
        try:
            m=__import__(mod); checks[mod]={'imported':True,'version':str(getattr(m,'__version__',getattr(m,'VersionBind','unknown')))}
        except ImportError as e: errors.append(str(e))
    node=binary(cfg.get('node'),'node');lo=binary(cfg.get('soffice'),'soffice') or binary(None,'libreoffice')
    checks['node']=node; checks['soffice']=lo
    if not node:errors.append('Node.js not found; configure runtime.local.json')
    if not lo:errors.append('LibreOffice not found; configure soffice or PATH')
    entry=cfg.get('artifact_tool_entry')
    if not entry or not Path(entry).is_file():errors.append('Legal local @oai/artifact-tool entry is required; it is not distributed in this package')
    elif node:
        code='import {pathToFileURL} from "node:url"; const m=await import(pathToFileURL(process.argv[1]).href); if(!m.Presentation||!m.PresentationFile)throw Error("Missing presentation API"); console.log(JSON.stringify({Presentation:typeof m.Presentation,PresentationFile:typeof m.PresentationFile}));'
        r=subprocess.run([node,'--input-type=module','-e',code,str(Path(entry).resolve())],capture_output=True,text=True)
        checks['artifact_import']={'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
        if r.returncode:errors.append('Artifact import failed; native OS/architecture must match')
        pkg=Path(entry).parent.parent/'package.json'
        if pkg.exists():
            checks['artifact_version']=json.loads(pkg.read_text()).get('version')
            if checks['artifact_version'] != cfg.get('expected_artifact_tool_version','2.8.91'):errors.append('Artifact version differs from fixed batch; record as changed condition before testing')
    skill=cfg.get('presentations_skill')
    if not skill or not (Path(skill)/'artifact_tool_docs/API_QUICK_START.md').exists():errors.append('Configure your installed presentations skill and authoring API docs')
    else:checks['presentations_skill']=str(Path(skill).resolve())
    checks['fonts']={}
    fc=shutil.which('fc-match')
    for name in ['Arial','Times New Roman']:
        if fc:
            r=subprocess.run([fc,'-f','%{family}|%{file}',name],capture_output=True,text=True)
            checks['fonts'][name]=r.stdout
        else:checks['fonts'][name]='fontconfig unavailable; verify actual exported PDF fonts in smoke test'
    checks['platform']=platform.platform();checks['machine']=platform.machine()
    checks['non_hermetic']=True
    report={'checks':checks,'errors':errors,'generation_ready':not errors,'scope':'Environment readiness; not drawing quality or license eligibility determination'}
    dest=Path(args.report).resolve();dest.parent.mkdir(parents=True,exist_ok=True);write(dest,report)
    print(json.dumps(report,ensure_ascii=False,indent=2));return not errors
def prepare(args):
    n=args.sample.zfill(3); item=json.loads((PACKAGE/'manifests/samples.json').read_text())['items']
    item=next(x for x in item if x['number']==n)
    if item['status']=='blocked':raise SystemExit(f'{n}: input blocked, see manifests/samples.json')
    if not verify():raise SystemExit('Package hash check failed')
    source=PACKAGE/'production-inputs'/n
    expected=next(x for x in json.loads((PACKAGE/'manifests/scientific-inputs.json').read_text())['samples'] if x['sample']==n)
    for entry in expected['scientific_inputs']:
        path=source/entry['file']
        if not path.is_file():raise SystemExit('Fixed input data not installed; run runtime/import-inputs.py first')
        if sha(path)!=entry['sha256']:raise SystemExit('Fixed scientific input changed: '+entry['file'])
    dest=Path(args.workspace).resolve()
    if dest.exists():raise SystemExit('Workspace must be new; existing experiments are never overwritten')
    dest.mkdir(parents=True)
    shutil.copytree(PACKAGE/'framework',dest/'framework')
    for name in ['MODEL_LED_DRAWING.zh-CN.md','OBJECT_COMPOSITION.zh-CN.md','STYLE_LEARNING.zh-CN.md','REFERENCE_JOINT_LEARNING.zh-CN.md']:
        shutil.copyfile(PACKAGE/'production-guides'/n/name,dest/'framework/harness'/name)
    shutil.copytree(PACKAGE/'runtime',dest/'runtime',ignore=shutil.ignore_patterns('runtime.local.json','.venv','__pycache__'))
    cfg=config()
    cfg['node']=binary(cfg.get('node'),'node');cfg['soffice']=binary(cfg.get('soffice'),'soffice') or binary(None,'libreoffice')
    write(dest/'runtime/runtime.local.json',cfg)
    sys.path.insert(0,str(dest/'framework'))
    from harness.model_led_drawing import prepare as inner_prepare
    inner_prepare(dest/'run',source/'paper-without-main-figure.pdf',[source/f'reference-{i:02d}.png' for i in [1,2,3]],guide_directory=dest/'framework/harness',outer_loop='human')
    prompt=(PACKAGE/'protocol/6-astra-production-prompt.txt').read_text().replace('{{WORKSPACE}}',str(dest)).replace('{{SAMPLE}}',n)
    (dest/'START-HERE.txt').write_text(prompt)
    write(dest/'model-settings.json',{'sample':n,'requested_model':args.model,'actual_model':None,'reasoning_effort':args.reasoning,'fresh_context_required':True,'production_inputs':4,'framework':'drawing-v2.5 recovered + portable mechanical adapter','generation_not_started':True,'note':'Fill actual model/session/tool versions at launch; staging does not call a model API'})
    print(json.dumps({'workspace':str(dest),'prompt':str(dest/'START-HERE.txt'),'next':'Start a fresh 6-astra agent context rooted only at this workspace'},ensure_ascii=False))
def harness(args):
    cfg=config();env=os.environ.copy();lo=binary(cfg.get('soffice'),'soffice') or binary(None,'libreoffice')
    if lo:env['PATH']=str(Path(lo).parent)+os.pathsep+env.get('PATH','')
    if cfg.get('fontconfig_file'):env['FONTCONFIG_FILE']=cfg['fontconfig_file']
    # run.py and core executor are copied unchanged. Prepared workspaces have the correct guides.
    command=[sys.executable,str(PACKAGE/'framework/run.py'),*args.arguments]
    return subprocess.call(command,env=env)==0
def contract_tests():
    with tempfile.TemporaryDirectory(prefix='drawing-contract-') as directory:
        fw=Path(directory)/'framework'
        shutil.copytree(PACKAGE/'framework',fw)
        for name in ['MODEL_LED_DRAWING.zh-CN.md','OBJECT_COMPOSITION.zh-CN.md','STYLE_LEARNING.zh-CN.md','REFERENCE_JOINT_LEARNING.zh-CN.md']:
            shutil.copyfile(PACKAGE/'production-guides/001'/name,fw/'harness'/name)
        env=os.environ.copy();env['PYTHONPATH']=str(fw)
        return subprocess.call([sys.executable,'-m','unittest','discover','-s',str(fw/'harness/tests'),'-p','test_model_led_drawing.py'],cwd=directory,env=env)==0
def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('verify');sub.add_parser('list');sub.add_parser('test')
    d=sub.add_parser('doctor');d.add_argument('--report',default='environment-check.json')
    s=sub.add_parser('prepare');s.add_argument('sample');s.add_argument('--workspace',required=True);s.add_argument('--model',default='6-astra');s.add_argument('--reasoning',default='record-at-launch')
    h=sub.add_parser('harness');h.add_argument('arguments',nargs=argparse.REMAINDER)
    args=p.parse_args()
    if args.command=='verify':ok=verify()
    elif args.command=='test':ok=contract_tests()
    elif args.command=='doctor':ok=doctor(args)
    elif args.command=='prepare':prepare(args);ok=True
    elif args.command=='harness':ok=harness(args)
    else:
        for x in json.loads((PACKAGE/'manifests/samples.json').read_text())['items']:print(x['number'],x['status'])
        ok=True
    raise SystemExit(0 if ok else 1)
if __name__=='__main__':main()
