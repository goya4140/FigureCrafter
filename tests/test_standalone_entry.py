"""Local HTTP fixtures only: no remote models, Nano requests or Codex process."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def endpoint(reply):
    bodies = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            bodies.append(body)
            message = reply(len(bodies) - 1, body)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'model': 'offline-fixture', 'choices': [{'message': message}]}).encode())
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', bodies
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def cli(*args, env=None):
    return subprocess.run([sys.executable, str(ROOT / 'figurecrafter.py'), *map(str, args)],
        capture_output=True, text=True, env=env, cwd=tempfile.gettempdir(), timeout=180)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StandaloneEntryTests(unittest.TestCase):
    def test_native_and_hybrid_http_loops_render_and_freeze_without_codex(self):
        soffice = os.environ.get('DRAWING_SOFFICE') or shutil.which('soffice')
        if not soffice:
            self.skipTest('Set DRAWING_SOFFICE for actual LibreOffice rendering')
        for method in ['native', 'hybrid']:
            with self.subTest(method=method), tempfile.TemporaryDirectory(prefix='Standalone 中文 ') as tmp:
                d = Path(tmp)
                w = d / 'case'
                # Child PATH has ordinary system tools only, no Codex/runtime bin.
                env = dict(os.environ, PATH='/usr/bin:/bin:/usr/sbin:/sbin')
                prepare = ['--soffice', soffice, 'prepare', '--method', method, '--inputs',
                    ROOT / 'examples/roboscape-method', '--workspace', w, '--model', 'offline-fixture']
                if method == 'hybrid':
                    import hashlib
                    handoff = d / 'handoff'
                    handoff.mkdir()
                    case = ROOT / 'showcases/figurecrafter/roboscape'
                    for src, dst in [('decomposer.pptx', 'editable.pptx'), ('decomposer.png', 'preview.png'),
                                     ('hybrid-source.png', 'source-image.png')]:
                        shutil.copyfile(case / src, handoff / dst)
                    (handoff / 'scene.json').write_text('{"fixture_only":true}')
                    (handoff / 'handoff.json').write_text(json.dumps({
                        'producer': 'Decomposer-v1.2-rc1h1-method2.2', 'decomposer_final': {
                            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in handoff.iterdir()}}))
                    prepare += ['--handoff', handoff]
                result = cli(*prepare, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                builder = '''from pptx import Presentation
from pptx.util import Inches
from pathlib import Path
import json, sys, hashlib
out=Path(sys.argv[1])
'''
                if method == 'native':
                    builder += '''p=Presentation(); p.slide_width=Inches(16); p.slide_height=Inches(9)
s=p.slides.add_slide(p.slide_layouts[6])
s.shapes.add_textbox(Inches(1),Inches(1),Inches(8),Inches(1)).text='Offline transport fixture'
'''
                else:
                    builder += f'''source=Path({str(w / 'work/imported/editable.pptx')!r})
p=Presentation(source)
p.slides[0].shapes.add_textbox(Inches(.1),Inches(.1),Inches(3),Inches(.3)).text='Offline fixture'
(out/'continuation-receipt.json').write_text(json.dumps({{'input_pptx_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'edits':['Added offline fixture label']}}))
'''
                builder += "p.save(out/'candidate.pptx')\n"
                steps = [('read_text', {'path': 'MODEL.txt'}), ('read_text', {'path': 'run/inputs/method.txt'})]
                steps += [('view_image', {'path': f'run/inputs/style-reference-{i:02}.png'}) for i in (1, 2, 3)]
                if method == 'hybrid':
                    steps += [('view_image', {'path': 'work/imported/render/slide-01.png'})]
                for name, content in [('understanding', 'Fixture, no scientific claim'), ('representation', 'Fixture'),
                        ('decision', 'Fixture'), ('builder', builder), ('caption', 'Offline transport fixture')]:
                    suffix = 'py' if name == 'builder' else 'md'
                    steps.append(('write_file', {'path': f'work/{name}.{suffix}', 'content': content}))
                steps += [('execute', {'builder': 'work/builder.py', 'decision': 'work/decision.md',
                    'understanding': 'work/understanding.md', 'representation': 'work/representation.md'}),
                    ('inspect', {'iteration': '000'}),
                    ('view_image', {'path': 'run/iterations/000/render/slide-01.png'}),
                    ('view_image', {'path': 'run/iterations/000/render/slide-01-publication.png'}),
                    ('write_file', {'path': 'work/review.md', 'content': 'Offline fixture review; no science assertion'}),
                    ('review', {'iteration': '000', 'path': 'work/review.md'}),
                    ('finish', {'iteration': '000', 'caption': 'work/caption.md'})]
                def reply(i, body):
                    name, arguments = steps[i]
                    return {'content': '', 'tool_calls': [{'id': str(i), 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                with endpoint(reply) as (base_url, bodies):
                    config = d / 'model.json'
                    config.write_text(json.dumps({'provider': 'openai-compatible', 'model': 'offline-fixture',
                        'endpoint': base_url + '/chat/completions', 'require_key': False}))
                    result = cli('--soffice', soffice, 'run', '--method', method, '--workspace', w,
                        '--config', config, '--max-turns', len(steps), env=env)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertTrue((w / 'delivery/main-figure.pptx').is_file())
                self.assertTrue((w / 'delivery/main-figure.pdf').is_file())
                self.assertIn('data:image/png;base64,', json.dumps(bodies))
                verification = cli('verify', '--method', method, '--workspace', w, env=env)
                self.assertEqual(verification.returncode, 0, verification.stderr)

    def test_image_loop_uses_original_gates_and_resumes_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            w = d / 'image'
            result = cli('prepare', '--method', 'image', '--inputs', ROOT / 'examples/roboscape-method',
                '--workspace', w, '--model', 'offline-fixture')
            self.assertEqual(result.returncode, 0, result.stderr)
            steps = [('read_text', {'path': 'run/inputs/method.txt'})]
            steps += [('view_image', {'path': f'run/inputs/style-reference-{i:02}.png'}) for i in (1, 2, 3)]
            steps += [('write_file', {'path': f'work/{name}.md', 'content': 'Offline fixture'})
                      for name in ['understanding', 'representation', 'decision']]
            steps += [('write_file', {'path': 'work/prompt.txt', 'content': 'Offline fixture, never submit'}),
                      ('image_request', {'mode': 'generate', 'prompt': 'work/prompt.txt'})]
            def reply(i, body):
                name, arguments = steps[i % len(steps)]
                return {'content': '', 'tool_calls': [{'id': str(i), 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            with endpoint(reply) as (base_url, bodies):
                config = d / 'model.json'
                config.write_text(json.dumps({'provider': 'openai-compatible', 'model': 'offline-fixture',
                    'endpoint': base_url + '/chat/completions', 'require_key': False}))
                args = ['run', '--method', 'image', '--workspace', w, '--config', config, '--max-turns', len(steps)]
                first = cli(*args)
                second = cli(*args)
            self.assertNotEqual(first.returncode, 0)  # No manufactured delivery at the turn limit.
            self.assertNotEqual(second.returncode, 0)
            events = [json.loads(line) for line in (w / 'events.jsonl').read_text().splitlines()]
            self.assertTrue(any(e.get('type') == 'tool_error' and 'ONE_INITIAL_ONLY' in e.get('error', '') for e in events))
            self.assertEqual(len(list((w / 'requests').glob('*/request.json'))), 1)
            self.assertFalse(list(w.rglob('submission.json')))
            self.assertIn('data:image/png;base64,', json.dumps(bodies))
            self.assertFalse((w / 'freeze.json').exists())

    def test_decomposer_http_adapter_preserves_schema_image_and_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            image = d / 'image.png'
            Image.new('RGB', (32, 18)).save(image)
            schema = d / 'schema.json'
            spec = {'type': 'object', 'properties': {'objects': {'type': 'array'}}, 'required': ['objects']}
            schema.write_text(json.dumps(spec))
            with endpoint(lambda i, body: {'content': '{"objects":[]}'}) as (base_url, bodies):
                env = dict(os.environ, MODEL_BASE_URL=base_url, MODEL_API_KEY='offline-fixture', MODEL_NAME='offline-fixture')
                result = subprocess.run([sys.executable, str(ROOT / 'runtime/decomposer_http.py'), '--stage',
                    'recognition', '--schema', str(schema), '--image', str(image), '--model', 'offline-fixture'],
                    input='Unchanged fixture stage prompt', capture_output=True, text=True, env=env)
                env['MODEL_NAME'] = 'different-model'
                rejected = subprocess.run([sys.executable, str(ROOT / 'runtime/decomposer_http.py'), '--stage',
                    'recognition', '--schema', str(schema), '--image', str(image), '--model', 'offline-fixture'],
                    input='fixture', capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'objects': []})
            self.assertEqual(len(bodies), 1)
            self.assertEqual(bodies[0]['reasoning_effort'], 'medium')
            self.assertEqual(bodies[0]['response_format']['json_schema']['schema'], spec)
            self.assertIn('Unchanged fixture stage prompt', json.dumps(bodies[0]))
            self.assertIn('data:image/png;base64,', json.dumps(bodies[0]))
            self.assertNotEqual(rejected.returncode, 0)

    def test_reconstruct_passes_portable_model_and_three_round_overlay(self):
        entry = load('portable_entry', ROOT / 'figurecrafter.py')
        reconstruct = load('portable_reconstruct', ROOT / 'methods/decomposer/reconstruct.py')
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp).resolve()
            image = d / 'source.png'
            Image.new('RGB', (32, 18)).save(image)
            args = ['figurecrafter.py', '--soffice', sys.executable, 'reconstruct', '--image', str(image),
                '--cache-root', str(d / 'cache'), '--sample-id', 'fixture', '--review', str(d / 'review.json'),
                '--output', str(d / 'output'), '--handoff', str(d / 'handoff'), '--model-config',
                str(ROOT / 'configs/decomposer.example.json'), '--node', sys.executable, '--pdftoppm', sys.executable]
            with patch.object(sys, 'argv', args), patch.object(entry.subprocess, 'run', return_value=subprocess.CompletedProcess([], 23)) as run:
                with self.assertRaises(SystemExit) as stopped:
                    entry.main()
            self.assertEqual(stopped.exception.code, 23)
            overlay_path = d / 'output-runtime.json'
            self.assertIn(str(overlay_path), run.call_args.args[0])
            overlay = json.loads(overlay_path.read_text())
            self.assertEqual(overlay['refinement']['max_rounds'], 3)
            self.assertEqual(overlay['model']['backend'], 'external-json')
            self.assertEqual(overlay['model']['command'], [sys.executable, str(ROOT / 'runtime/decomposer_http.py')])
            import hashlib
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            bundle = d / 'cache/fixture'
            (bundle / 'input').mkdir(parents=True)
            (bundle / 'observations').mkdir()
            candidates = bundle / 'observations/candidates.json'
            candidates.write_text(json.dumps({'sources': {'sam': {'status': 'ready'}, 'ocr': {'status': 'ready'}}}))
            (bundle / 'input/input.json').write_text(json.dumps({'source_sha256': sha(image), 'canvas': [32, 18]}))
            review = d / 'review.json'
            review.write_text(json.dumps({'status': 'reviewed', 'image_sha256': sha(image), 'candidates_sha256': sha(candidates)}))
            _, effective, *_ = reconstruct.prepare(image, d / 'cache', 'fixture', review, d / 'output', overlay_path)
            self.assertEqual(effective['refinement']['max_rounds'], 3)
            self.assertEqual(effective['model']['backend'], 'external-json')
            self.assertEqual(effective['preprocessing']['cache_root'], str(d / 'cache'))
            self.assertEqual(effective['detectors'], {'sam': {'command': None}, 'ocr': {'command': None}})
            self.assertNotIn('codex-primary-runtime', json.dumps(effective))


if __name__ == '__main__':
    unittest.main()
