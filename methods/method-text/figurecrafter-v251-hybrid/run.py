#!/usr/bin/env python3
"""Method 2 continuation: reuse FigureCrafter's native execute/review/deliver loop."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / 'drawing-v2.5.1'
sys.path.insert(0, str(BASE))
from harness import standalone as fc
from pptx import Presentation

VERSION = 'figurecrafter-v251-hybrid.1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2))


def objects(path):
    """Stable slide/shape identities plus exact visual XML and picture bytes."""
    prs = Presentation(path)
    result = {}
    def visit(shapes, prefix):
        for s in shapes:
            key = prefix + '/' + str(s.shape_id)
            item = {'name': s.name, 'type': str(s.shape_type),
                    'xml_sha256': hashlib.sha256(s._element.xml.encode()).hexdigest()}
            if s.has_text_frame:
                item['text'] = s.text
            if hasattr(s, 'image'):
                item['image_sha256'] = hashlib.sha256(s.image.blob).hexdigest()
            result[key] = item
            if hasattr(s, 'shapes'):
                visit(s.shapes, key)
    for n, slide in enumerate(prs.slides):
        visit(slide.shapes, str(n))
    return result


def differences(before, after):
    common = before.keys() & after.keys()
    b = Counter(v['image_sha256'] for v in before.values() if 'image_sha256' in v)
    a = Counter(v['image_sha256'] for v in after.values() if 'image_sha256' in v)
    return {'before_count': len(before), 'after_count': len(after),
            'retained_identity_count': len(common),
            'unchanged': sorted(k for k in common if before[k] == after[k]),
            'changed': [{'id': k, 'before': before[k], 'after': after[k]}
                        for k in sorted(common) if before[k] != after[k]],
            'removed': [{'id': k, **before[k]} for k in sorted(before.keys() - after.keys())],
            'added': [{'id': k, **after[k]} for k in sorted(after.keys() - before.keys())],
            'pictures': {'before': sum(b.values()), 'after': sum(a.values()),
                         'retained_exact_bytes': sum((a & b).values()),
                         'new_or_reencoded': sum((a - b).values())},
            'interpretation': 'Identity/byte evidence only; no automated semantic or aesthetic score.'}


def prepare(workspace, inputs, handoff, model, context=()):
    source = Path(handoff).resolve()
    for path in map(Path, context):
        if not path.is_file() or path.suffix.lower() not in {'.md', '.txt', '.json'}:
            raise ValueError('Upstream planning context must be an existing text file')
    receipt = json.loads((source / 'handoff.json').read_text())
    names = ('editable.pptx', 'preview.png', 'scene.json')
    if (source / 'source-image.png').is_file():
        names = (*names, 'source-image.png')
    for name in names:
        if sha(source / name) != receipt['decomposer_final'][name]:
            raise ValueError('Decomposer handoff hash mismatch: ' + name)
    fc.core.figure_contract(source / 'editable.pptx', {'aspect_ratio': [16, 9]})
    # Standard scientific inputs, general guides, and native authoring tools are reused.
    w = fc.prepare(workspace, inputs, model)
    imported = w / 'work/imported'
    imported.mkdir()
    for name in (*names, 'handoff.json'):
        shutil.copy2(source / name, imported / name)
    for i, path in enumerate(context):
        path = Path(path)
        if path.suffix.lower() not in {'.md', '.txt', '.json'}:
            raise ValueError('Upstream planning context must be text, not a teacher PDF/PPTX')
        shutil.copy2(path, imported / f'context-{i:02d}{path.suffix.lower()}')
    dump(imported / 'native-inventory.json', objects(imported / 'editable.pptx'))
    # Render the actual PPTX again; the supplied preview is not assumed authoritative.
    fc.core.render(imported / 'editable.pptx', imported / 'render')
    manifest = {str(p.relative_to(w)): sha(p) for p in imported.rglob('*') if p.is_file()}
    dump(w / 'continuation.json', {'version': VERSION, 'mode': 'continue_editable_pptx',
         'producer': receipt['producer'], 'input_pptx_sha256': sha(imported / 'editable.pptx'),
         'immutable_files': manifest,
         'framework_source_hashes': {str(p.relative_to(BASE)): sha(p) for p in BASE.rglob('*')
            if p.is_file() and p.suffix in {'.py', '.md', '.txt'}
            and p.relative_to(BASE).parts[0] in {'harness', 'guides', 'protocol'}},
         'upstream_checkpoint_reused': True, 'outer_evolution': False})
    shutil.copy2(w / 'MODEL.txt', w / 'MODEL.direct-drawing.txt')
    (w / 'CONTINUATION.md').write_text((ROOT / 'CONTINUATION.md').read_text())
    (w / 'MODEL.txt').write_text((w / 'MODEL.direct-drawing.txt').read_text() + '\n\n' + (ROOT / 'CONTINUATION.md').read_text())
    session = json.loads((w / 'session.json').read_text())
    session['workflow'] = VERSION
    session['input_contract'] = 'paper + three references + Decomposer PPTX/preview/scene + optional upstream plan'
    dump(w / 'session.json', session)
    return w


class Session(fc.Session):
    def validate_input(self):
        contract = json.loads((self.root / 'continuation.json').read_text())
        for name, expected in contract['immutable_files'].items():
            if sha(self.path(name)) != expected:
                raise ValueError('Immutable continuation input changed: ' + name)
        for name, expected in contract['framework_source_hashes'].items():
            if sha(BASE / name) != expected:
                raise ValueError('Underlying FigureCrafter source changed: ' + name)
        return contract

    def require_seen(self, paths):
        events = [json.loads(x) for x in self.events.read_text().splitlines()] if self.events.exists() else []
        seen = {e['sha256'] for e in events if e['type'] == 'image_observed'}
        if any(not self.path(p).is_file() or sha(self.path(p)) not in seen for p in paths):
            raise ValueError('Continuation requires pixel observation: ' + ', '.join(paths))

    def dispatch(self, name, a):
        contract = self.validate_input()
        if name in {'execute', 'review', 'finish'}:
            self.require_seen(['work/imported/render/slide-01.png'])
        if name == 'execute':
            result = super().dispatch(name, a)
            self.validate_input()
            folder = self.run / 'iterations' / result['iteration']
            attest = folder / 'continuation-receipt.json'
            if not attest.is_file():
                raise ValueError('Builder must save continuation-receipt.json with base hash and edit rationale')
            receipt = json.loads(attest.read_text())
            if receipt.get('input_pptx_sha256') != contract['input_pptx_sha256'] or not receipt.get('edits'):
                raise ValueError('Continuation receipt requires matching input hash and nonempty edits')
            report = differences(objects(self.root / 'work/imported/editable.pptx'), objects(folder / 'candidate.pptx'))
            if not report['retained_identity_count']:
                raise ValueError('No retained native identities; whole-figure replacement is not continuation')
            report['builder_attestation'] = receipt
            report['input_pptx_sha256'] = contract['input_pptx_sha256']
            report['candidate_pptx_sha256'] = sha(folder / 'candidate.pptx')
            dump(folder / 'continuation-diff.json', report)
            result['continuation_diff'] = str((folder / 'continuation-diff.json').relative_to(self.root))
            return result
        if name in {'review', 'finish'}:
            folder = self.run / 'iterations' / a['iteration']
            if not (folder / 'continuation-diff.json').is_file():
                raise ValueError('No successful continuation audit for this iteration')
            report = json.loads((folder / 'continuation-diff.json').read_text())
            if (report['candidate_pptx_sha256'] != sha(folder / 'candidate.pptx') or
                report['builder_attestation'] != json.loads((folder / 'continuation-receipt.json').read_text())):
                raise ValueError('Continuation candidate or edit receipt changed after audit')
            self.require_seen([f'run/iterations/{a["iteration"]}/render/slide-01-publication.png'])
        if name == 'finish':
            result = super().dispatch(name, a)
            folder = self.run / 'iterations' / a['iteration']
            for filename in ('continuation-diff.json', 'continuation-receipt.json'):
                shutil.copy2(folder / filename, self.root / 'delivery' / filename)
            shutil.copy2(self.root / 'continuation.json', self.root / 'delivery' / 'continuation.json')
            return result
        return super().dispatch(name, a)


def loop(workspace, config, max_turns):
    """Transport glue only. All scientific decisions stay with the execution model."""
    s = Session(workspace)
    if (s.root / 'freeze.json').exists():
        raise ValueError('Cannot resume a frozen session')
    provider = fc.create(config)
    path = s.root / 'conversation.json'
    messages = json.loads(path.read_text()) if path.exists() else [
        {'role': 'system', 'text': (s.root / 'MODEL.txt').read_text()},
        {'role': 'user', 'text': 'Continue editing the imported Decomposer PPTX using CONTINUATION.md; inspect, execute, review, finish.'}]
    for _ in range(max_turns):
        response = provider.complete(messages, fc.TOOLS)
        s.log({'type': 'model_response', 'provider': config['provider'], 'configured_model': config['model'], 'response': response})
        messages.append({'role': 'assistant', **response})
        dump(path, messages)
        if not response['calls']:
            messages.append({'role': 'user', 'text': 'Not delivered yet. Continue tools or state the blocker.'})
        for c in response['calls']:
            try:
                result = s.call(c['name'], c['arguments'])
            except Exception as error:
                result = {'error': str(error), 'automatic_repair': False}
            m = {'role': 'tool', 'call_id': c['id'], 'name': c['name'], 'result': result}
            for key in ('provider_call_id',):
                if c.get(key): m[key] = c[key]
            if result.get('image_path'): m['image_path'] = result['image_path']
            messages.append(m)
            if c['name'] == 'finish' and 'error' not in result:
                return result
        dump(path, messages)
    raise RuntimeError('Turn limit reached without delivery; conversation retained')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--soffice')
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare')
    for key in ('workspace', 'inputs', 'handoff', 'model'): a.add_argument('--' + key, required=True)
    a.add_argument('--context', action='append', default=[])
    a = sub.add_parser('call'); a.add_argument('--workspace', required=True); a.add_argument('--json', required=True)
    a = sub.add_parser('run'); a.add_argument('--workspace', required=True); a.add_argument('--config', required=True); a.add_argument('--max-turns', type=int, default=80)
    a = sub.add_parser('verify'); a.add_argument('--workspace', required=True)
    sub.add_parser('tools')
    args = p.parse_args(); fc.configure(args.soffice)
    if args.command == 'prepare':
        result = {'workspace': str(prepare(args.workspace, args.inputs, args.handoff, args.model, args.context))}
    elif args.command == 'call':
        c = json.loads(Path(args.json).read_text()); result = Session(args.workspace).call(c['name'], c['arguments'])
    elif args.command == 'run':
        cfg = json.loads(Path(args.config).read_text())
        s = Session(args.workspace)
        if (s.root / 'freeze.json').exists(): raise ValueError('Cannot alter a frozen session')
        data = json.loads((s.root / 'session.json').read_text()); data.update({'provider': cfg['provider'], 'model': cfg['model']}); dump(s.root / 'session.json', data)
        result = loop(args.workspace, cfg, args.max_turns)
    elif args.command == 'verify':
        Session(args.workspace).validate_input()
        result = fc.verify_frozen(args.workspace)
        if result['errors'] or result['unexpected_files']: raise ValueError(result)
    else: result = fc.TOOLS
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
