"""Import only the fixed scientific inputs from an unpacked production package."""
from pathlib import Path
import argparse, hashlib, json, shutil

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--production-package',required=True,type=Path)
a=p.parse_args()
source=a.production_package.resolve()/'production-inputs'
manifest=json.loads((ROOT/'manifests/scientific-inputs.json').read_text())
errors=[]
for sample in manifest['samples']:
    for item in sample['scientific_inputs']:
        file=source/sample['sample']/item['file']
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:
            errors.append(str(file))
if errors:raise SystemExit('Input hash/readability failure before import: '+str(errors))
destination=ROOT/'production-inputs'
if destination.exists():raise SystemExit('production-inputs already exists; verify it, never overwrite existing data')
destination.mkdir()
for sample in manifest['samples']:
    out=destination/sample['sample'];out.mkdir()
    for item in sample['scientific_inputs']:
        shutil.copyfile(source/sample['sample']/item['file'],out/item['file'])
    (out/'input-manifest.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2))
print(json.dumps({'samples_imported':len(manifest['samples']),'scientific_files':sum(len(x['scientific_inputs']) for x in manifest['samples']),'byte_identity_verified':True,'generation_started':False}))
