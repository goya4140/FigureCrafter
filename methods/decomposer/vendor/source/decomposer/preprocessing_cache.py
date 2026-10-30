"""Load an existing SAM/OCR evidence bundle without invoking any detectors."""
from pathlib import Path
import shutil
from PIL import Image,ImageChops
from .common import read_json,write_json,sha256

def reuse(sample,destination,cache_root):
 destination=Path(destination);root=Path(cache_root)/sample['sample_id'];record=read_json(root/'input/input.json');canvas=[sample['width'],sample['height']]
 if record['source_sha256']!=sample['sha256'] or sha256(sample['local_input'])!=sample['sha256']:raise ValueError('Cached input hash mismatch')
 if record['canvas']!=canvas:raise ValueError('Cached canvas mismatch')
 with Image.open(root/'input/normalized.png') as normalized,Image.open(sample['local_input']) as original:
  if list(normalized.size)!=canvas or normalized.size!=original.size:raise ValueError('Normalized image geometry mismatch')
  if ImageChops.difference(normalized.convert('RGB'),original.convert('RGB')).getbbox() is not None:raise ValueError('Cached normalized pixels differ from source')
 c=read_json(root/'observations/candidates.json');e=read_json(root/'observations/evidence-ir.json')
 if c['canvas']!=canvas or e['canvas']!={'width':canvas[0],'height':canvas[1]}:raise ValueError('Cached evidence geometry mismatch')
 for detector in ['sam','ocr']:
  if c['sources'].get(detector,{}).get('status')!='ready' or e['detector_sources'].get(detector,{}).get('status')!='ready':raise ValueError(f'Cached {detector} unavailable; detector fallback is disabled')
 if not e.get('schema_version','').startswith('decomposer.evidence.') or not e.get('candidates'):raise ValueError('Invalid cached evidence')
 ids=[x['id'] for x in e['candidates']]
 if len(ids)!=len(set(ids)):raise ValueError('Duplicate cached evidence IDs')
 destination.mkdir(parents=True,exist_ok=True);shutil.copy2(root/'input/normalized.png',destination/'input-normalized.png');shutil.copytree(root/'observations',destination/'observations',dirs_exist_ok=True)
 files=['input/normalized.png','input/input.json','observations/candidates.json','observations/evidence-ir.json'];provenance={'mode':'cache_only','source_bundle':str(root.resolve()),'source_sha256':sample['sha256'],'canvas':canvas,'sources':c['sources'],'file_sha256':{f:sha256(root/f) for f in files},'detector_invocations':0,'evidence_rebuild_invocations':0}
 if sha256(destination/'observations/evidence-ir.json')!=provenance['file_sha256']['observations/evidence-ir.json']:raise AssertionError('Evidence copy differs')
 write_json(destination/'preprocessing-provenance.json',provenance);return c,e
