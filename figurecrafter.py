#!/usr/bin/env python3
"""Portable dispatch into the original FigureCrafter method implementations.

This CLI routes tools and prepares files. The active model still reads, designs,
authors code, views actual pixels, reviews, corrects and chooses its output.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
METHODS = {
    'native': 'methods/method-text/drawing-v2.5.1/run.py',
    'image': 'methods/method-text/figurecrafter-v251-image/run.py',
    'hybrid': 'methods/method-text/figurecrafter-v251-hybrid/run.py',
    'image-builtin': 'methods/builtin-image/figurecrafter-v251-image/run.py',
    'hybrid-builtin': 'methods/builtin-image/figurecrafter-v251-hybrid/run.py',
    'native-pdf-historical': 'methods/drawing-v2.5.1/run.py',
}
HTTP_METHODS = tuple(m for m in METHODS if m != 'image-builtin')


def reconstruction_model(config_path=None, codex=None, timeout=1800):
    """Resolve an explicit transport; never choose another model or backend."""
    if codex:
        return {'backend': 'codex-cli', 'command': str(Path(codex).expanduser().resolve()),
                'name': 'gpt-6.1-sol', 'effort': 'medium', 'timeout_seconds': timeout}
    config = json.loads(Path(config_path).expanduser().read_text())
    model = dict(config['model'])
    if model.get('backend') != 'external-json' or not model.get('name'):
        raise ValueError('--model-config requires an explicit external-json backend and model.name')
    command = model.get('command')
    if not command or not isinstance(command, (str, list)):
        raise ValueError('model.command must be a nonempty string or string array')
    if isinstance(command, list) and not all(isinstance(v, str) and v for v in command):
        raise ValueError('model.command entries must be nonempty strings')
    values = {'{python}': sys.executable, '{repo}': str(ROOT)}
    def resolve(value):
        for marker, replacement in values.items():
            value = value.replace(marker, replacement)
        return value
    model['command'] = [resolve(v) for v in command] if isinstance(command, list) else resolve(command)
    if isinstance(model['command'], list) and str(ROOT / 'runtime/decomposer_http.py') in model['command']:
        arguments = model.get('arguments', [])
        for flag, expected in [('--model', model['name']), ('--reasoning-effort', model.get('effort'))]:
            if flag not in arguments or arguments.index(flag) + 1 >= len(arguments) or arguments[arguments.index(flag) + 1] != expected:
                raise ValueError(f'{flag} must match the explicit model configuration')
    model.setdefault('timeout_seconds', timeout)
    return model


def dispatch(method, arguments, soffice=None):
    env = dict(os.environ)
    if soffice:
        env['DRAWING_SOFFICE'] = str(Path(soffice).expanduser().resolve())
    return subprocess.run([sys.executable, str(ROOT / METHODS[method]), *arguments], env=env).returncode


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--soffice', help='Local LibreOffice executable; alternatively set DRAWING_SOFFICE')
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--method', choices=METHODS, required=True)
    a.add_argument('--inputs', required=True)
    a.add_argument('--workspace', required=True)
    a.add_argument('--model', default='gpt-6.1-sol')
    a.add_argument('--handoff')
    a.add_argument('--context', action='append', default=[])
    for name in ['call', 'tools', 'verify']:
        a = sub.add_parser(name)
        a.add_argument('--method', choices=METHODS, required=True)
        if name != 'tools': a.add_argument('--workspace', required=True)
        if name == 'call': a.add_argument('--json', required=True)
    a = sub.add_parser('run', help='Run the full model/tool loop over a configured HTTP API, without Codex')
    a.add_argument('--method', choices=HTTP_METHODS, required=True)
    a.add_argument('--workspace', required=True)
    a.add_argument('--config', required=True, help='Existing vision/tool model HTTP configuration; keys stay in environment')
    a.add_argument('--max-turns', type=int, default=80)
    sub.add_parser('doctor')
    a = sub.add_parser('detectors')
    for name in ['image','config','cache-root','sample-id','preflight']: a.add_argument('--'+name, required=True)
    a = sub.add_parser('reconstruct')
    for name in ['image','cache-root','sample-id','review','output','handoff']: a.add_argument('--'+name, required=True)
    backend = a.add_mutually_exclusive_group(required=True)
    backend.add_argument('--model-config', help='Explicit external-json Decomposer model configuration; no Codex required')
    backend.add_argument('--codex', help='Optional historical Codex CLI transport supporting gpt-6.1-sol')
    a.add_argument('--node', required=True)
    a.add_argument('--pdftoppm', required=True)
    a.add_argument('--model-timeout', type=int, default=1800)
    args = p.parse_args()
    if args.soffice: os.environ['DRAWING_SOFFICE'] = str(Path(args.soffice).expanduser().resolve())
    if args.command == 'doctor':
        print(json.dumps({'python':sys.version,'python_executable':sys.executable,
            'soffice':os.environ.get('DRAWING_SOFFICE') or shutil.which('soffice') or shutil.which('libreoffice'),
            'node':shutil.which('node'),'pdftoppm':shutil.which('pdftoppm'),'codex_optional':shutil.which('codex'),
            'codex_required':False,'private_package_required':False,
            'standalone_methods':['native','image','hybrid'],
            'historical_private_runtime':'runtime/portable.py is not the current entry point',
            'nano_key_configured':bool(os.environ.get('NANO_API_KEY')) or (ROOT/'configs/nano.local.json').is_file(),
            'note':'Read-only inventory; does not test model availability or figure quality. No model calls.'},indent=2))
        return
    if args.command == 'detectors':
        sys.path.insert(0,str(ROOT/'methods/decomposer/vendor/source'))
        from decomposer.detectors import collect
        from decomposer.evidence import build
        from PIL import Image
        image=Path(args.image).resolve();cache=Path(args.cache_root).resolve()/args.sample_id
        observations=cache/'observations';input_dir=cache/'input'
        if cache.exists():raise ValueError('Use a fresh cache directory; retain previous detector attempts')
        cfg=json.loads(Path(args.config).read_text())
        for name in ['sam','ocr']:
            if not cfg.get('detectors',{}).get(name,{}).get('command'):
                raise ValueError('Both actual SAM and OCR commands are required; no local fallback')
        with Image.open(image) as im:canvas=list(im.size)
        result=collect(image,tuple(canvas),cfg,observations)
        if not all(result['sources'].get(n,{}).get('status')=='ready' for n in ['sam','ocr']):
            raise RuntimeError('Server detector failed; retain evidence and resolve before Recognition')
        import hashlib
        digest=lambda q:hashlib.sha256(Path(q).read_bytes()).hexdigest()
        input_dir.mkdir(parents=True)
        shutil.copyfile(image,input_dir/'normalized.png')
        (input_dir/'input.json').write_text(json.dumps({'source':str(image),'source_sha256':digest(image),'canvas':canvas},indent=2))
        base=json.loads((ROOT/'methods/decomposer/vendor/config.json').read_text())
        (observations/'evidence-ir.json').write_text(json.dumps(build(result,tuple(canvas),base),ensure_ascii=False,indent=2))
        spec=importlib.util.spec_from_file_location('preflight',ROOT/'methods/decomposer/preflight.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        summary=m.inspect(image,observations/'candidates.json',Path(args.preflight).resolve())
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        print('Next: actually inspect all overlays/JSON; write hash-bound review.json before reconstruct.')
        return
    if args.command == 'reconstruct':
        soffice=os.environ.get('DRAWING_SOFFICE') or shutil.which('soffice') or shutil.which('libreoffice')
        if not soffice:raise ValueError('Set --soffice or DRAWING_SOFFICE')
        output=Path(args.output).resolve()
        if output.exists() or Path(args.handoff).resolve().exists():
            raise ValueError('Use fresh reconstruction and handoff directories')
        overlay=output.parent/(output.name+'-runtime.json');overlay.parent.mkdir(parents=True,exist_ok=True)
        if overlay.exists():raise ValueError('Existing runtime overlay: retain and inspect previous attempt')
        model = reconstruction_model(args.model_config, args.codex, args.model_timeout)
        overlay.write_text(json.dumps({'model': model,
            'runtime':{'node':str(Path(args.node).resolve()),'soffice':soffice,
            'pdftoppm':str(Path(args.pdftoppm).resolve()),'timeout_seconds':180},
            'refinement': {'max_rounds': 3}},indent=2))
        argv=[sys.executable,str(ROOT/'methods/decomposer/reconstruct.py')]
        for n in ['image','cache_root','sample_id','review','output','handoff']:
            argv+=['--'+n.replace('_','-'),str(getattr(args,n))]
        argv+=['--config-overlay',str(overlay)]
        result=subprocess.run(argv)
        if result.returncode:raise SystemExit(result.returncode)
        handoff=Path(args.handoff).resolve();image=Path(args.image).resolve()
        shutil.copyfile(image,handoff/'source-image.png')
        import hashlib
        receipt=json.loads((handoff/'handoff.json').read_text())
        receipt['decomposer_final']['source-image.png']=hashlib.sha256(image.read_bytes()).hexdigest()
        status=json.loads((output/'status.json').read_text())
        receipt['decomposer_quality']={'validation_passed':status.get('validation_passed'),
            'validation_failures':status.get('validation_failures'),'scientific_acceptance':'not established by reconstruction fidelity'}
        (handoff/'handoff.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
        return
    if args.command == 'run':
        if args.max_turns < 1: p.error('--max-turns must be positive')
        workspace = Path(args.workspace).expanduser().resolve()
        if (workspace / 'freeze.json').exists():
            raise ValueError('Cannot run or alter a frozen session')
        config = json.loads(Path(args.config).expanduser().read_text())
        session = json.loads((workspace / 'session.json').read_text())
        if config.get('model') != session['model']:
            raise ValueError('Configured model differs from the prepared session; prepare a new workspace with the intended model')
        if args.method == 'image':
            raise SystemExit(subprocess.run([sys.executable, str(ROOT / 'runtime/image_loop.py'),
                '--workspace', str(workspace), '--config', str(Path(args.config).expanduser().resolve()),
                '--max-turns', str(args.max_turns)]).returncode)
        raise SystemExit(dispatch(args.method, ['run', '--workspace', str(workspace),
            '--config', str(Path(args.config).expanduser().resolve()), '--max-turns', str(args.max_turns)], args.soffice))
    argv=[args.command]
    if args.command=='prepare':
        for n in ['inputs','workspace','model']:argv+=['--'+n,getattr(args,n)]
        if args.method.startswith('hybrid'):
            if not args.handoff:p.error('Hybrid prepare requires --handoff')
            argv+=['--handoff',args.handoff]
        elif args.handoff:p.error('--handoff is only supported by Hybrid')
        if args.method in ['native','native-pdf-historical'] and args.context:
            p.error('Native has no upstream generated context')
        for c in args.context:argv+=['--context',c]
    elif args.command in ['call','verify']:
        argv+=['--workspace',args.workspace]
        if args.command=='call':argv+=['--json',args.json]
    raise SystemExit(dispatch(args.method,argv,args.soffice))

if __name__=='__main__':main()
