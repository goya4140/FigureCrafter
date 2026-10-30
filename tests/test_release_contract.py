"""Offline publication checks. No remote model calls or image generation."""
from pathlib import Path
from html.parser import HTMLParser
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlparse, unquote

ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def cli(*args,check=True):
    return subprocess.run([sys.executable,str(ROOT/'figurecrafter.py'),*map(str,args)],
        capture_output=True,text=True,check=check)

class ReleaseContractTests(unittest.TestCase):
    def test_original_baseline_and_guides_are_byte_identical(self):
        manifest=read(ROOT/'provenance/figurecrafter-source-manifest.json')['baseline_files']
        self.assertEqual(len(manifest),19)
        for p,h in manifest.items():self.assertEqual(sha(ROOT/'methods/drawing-v2.5.1'/p),h,p)
        for mode in ['method-text','builtin-image']:
            for p,h in manifest.items():
                if p.startswith('guides/'):
                    self.assertEqual(sha(ROOT/'methods'/mode/'drawing-v2.5.1'/p),h)

    def test_packaged_source_manifest_matches_actual_bytes(self):
        for rel,item in read(ROOT/'provenance/figurecrafter-source-manifest.json')['sources'].items():
            self.assertEqual(sha(ROOT/rel),item['packaged_sha256'],rel)

    def test_decomposer_is_portable_and_hash_verified(self):
        p=subprocess.run([sys.executable,str(ROOT/'methods/decomposer/reconstruct.py'),'--verify-version'],
            capture_output=True,text=True,check=True,cwd=tempfile.gettempdir())
        self.assertEqual(json.loads(p.stdout)['version'],'v1.2-rc1h1-method2.2')

    def test_tool_routes_preserve_backend_boundaries(self):
        names={}
        for method in ['native','image','hybrid','image-builtin','hybrid-builtin']:
            names[method]={x['name'] for x in json.loads(cli('tools','--method',method).stdout)}
        self.assertIn('execute',names['native']);self.assertIn('execute',names['hybrid'])
        self.assertNotIn('execute',names['image']);self.assertIn('provider_submit',names['image'])
        self.assertIn('export_initial',names['image']);self.assertNotIn('provider_submit',names['image-builtin'])

    def test_method_input_prepare_and_write_boundary(self):
        with tempfile.TemporaryDirectory(prefix='FigureCrafter portable 中文 ') as folder:
            for method in ['native','image','image-builtin']:
                w=Path(folder)/method
                cli('prepare','--method',method,'--inputs',ROOT/'examples/roboscape-method',
                    '--workspace',w,'--model','gpt-6.1-sol')
                self.assertEqual(sha(w/'run/inputs/method.txt'),sha(ROOT/'examples/roboscape-method/method.txt'))
                self.assertFalse(list((w/'run/inputs').glob('*.pdf')))
                q=w/'work/test-request.json'
                q.write_text(json.dumps({'name':'write_file','arguments':{'path':'run/inputs/method.txt','content':'tamper'}}))
                result=cli('call','--method',method,'--workspace',w,'--json',q,check=False)
                self.assertNotEqual(result.returncode,0)

    def test_nano_zero_edit_profile_and_second_initial_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            w=Path(folder)/'image'
            cli('prepare','--method','image','--inputs',ROOT/'examples/roboscape-method','--workspace',w,'--model','gpt-6.1-sol')
            def call(name,args):
                q=w/'work/test-request.json';q.write_text(json.dumps({'name':name,'arguments':args}))
                return cli('call','--method','image','--workspace',w,'--json',q,check=False)
            for n in ['understanding','representation','decision']:
                self.assertEqual(call('write_file',{'path':f'work/{n}.md','content':'Synthetic offline contract fixture'}).returncode,0)
            call('write_file',{'path':'work/prompt.txt','content':'Synthetic offline prompt; never dispatched'})
            for n in range(1,4):call('view_image',{'path':f'run/inputs/style-reference-{n:02}.png'})
            call('read_text',{'path':'run/inputs/method.txt'})
            first=call('image_request',{'mode':'generate','prompt':'work/prompt.txt'})
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertEqual(json.loads(first.stdout)['request'],'000')
            self.assertNotEqual(call('image_request',{'mode':'generate','prompt':'work/prompt.txt'}).returncode,0)
            self.assertNotEqual(call('image_request',{'mode':'edit','prompt':'work/prompt.txt','parent':'000'}).returncode,0)
            self.assertEqual(len(list((w/'requests').glob('*/request.json'))),1)
            self.assertFalse(list(w.rglob('submission.json')))

    def test_hybrid_import_renders_and_guards_actual_pptx(self):
        binary=os.environ.get('DRAWING_SOFFICE') or shutil.which('soffice')
        if not binary:self.skipTest('Set DRAWING_SOFFICE for actual renderer check')
        with tempfile.TemporaryDirectory(prefix='FigureCrafter import ') as folder:
            d=Path(folder);handoff=d/'handoff';handoff.mkdir();case=ROOT/'showcases/figurecrafter/roboscape'
            for source,dest in [('decomposer.pptx','editable.pptx'),('decomposer.png','preview.png'),('hybrid-source.png','source-image.png')]:
                shutil.copyfile(case/source,handoff/dest)
            (handoff/'scene.json').write_text('{"fixture_only":"import/hash validation, no science assertion"}')
            names=['editable.pptx','preview.png','scene.json','source-image.png']
            (handoff/'handoff.json').write_text(json.dumps({'producer':'Decomposer-v1.2-rc1h1-method2.2','decomposer_final':{n:sha(handoff/n) for n in names}}))
            w=d/'native'
            cli('--soffice',binary,'prepare','--method','hybrid','--inputs',ROOT/'examples/roboscape-method',
                '--workspace',w,'--handoff',handoff,'--model','gpt-6.1-sol')
            self.assertTrue((w/'work/imported/render/slide-01.png').is_file())
            self.assertEqual(sha(w/'work/imported/editable.pptx'),sha(handoff/'editable.pptx'))
            self.assertGreater(len(read(w/'work/imported/native-inventory.json')),1)
            (w/'work/imported/scene.json').write_text('{"tampered":true}')
            q=w/'work/probe.json';q.write_text(json.dumps({'name':'read_text','arguments':{'path':'run/inputs/method.txt'}}))
            self.assertNotEqual(cli('call','--method','hybrid','--workspace',w,'--json',q,check=False).returncode,0)

    def test_showcase_assets_and_local_html_links_exist(self):
        root=ROOT/'showcases/figurecrafter';case=read(root/'cases.json')
        for row in case['files']:self.assertEqual(sha(root/row['file']),row['sha256'])
        data=read(root/'image52/manifest.json');self.assertEqual(len(data['images']),52)
        self.assertEqual(len(data['skipped']),8)
        for row in data['images']:self.assertTrue((root/'image52'/row['preview_file']).is_file())
        class Links(HTMLParser):
            def __init__(self):super().__init__();self.links=[]
            def handle_starttag(self,tag,attrs):
                self.links.extend(v for k,v in attrs if k in ['src','href'] and v)
        for p in root.rglob('*.html'):
            parser=Links();parser.feed(p.read_text())
            for url in parser.links:
                parsed=urlparse(url)
                if not parsed.scheme and parsed.path:
                    self.assertTrue((p.parent/unquote(parsed.path)).exists(),str(p)+': '+url)

if __name__=='__main__':unittest.main()
