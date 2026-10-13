"""Import the original 392 scientific inputs, after exact hash verification."""
from pathlib import Path
import argparse,hashlib,json,shutil,tempfile
ROOT=Path(__file__).resolve().parents[1]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
p=argparse.ArgumentParser();p.add_argument('--production-package',type=Path,required=True);a=p.parse_args()
source=a.production_package.resolve()/'production-inputs'
manifest=json.loads((ROOT/'manifests/scientific-inputs.json').read_text())
dest=ROOT/'production-inputs'
if dest.exists():raise SystemExit('Input directory exists; never overwrite an existing dataset')
for sample,files in manifest.items():
 for name,expected in files.items():
  path=source/sample/name.replace('style-reference-','reference-')
  if not path.is_file() or sha(path)!=expected:raise SystemExit('Input hash mismatch: '+str(path))
# Stage atomically so interrupted imports do not masquerade as complete.
with tempfile.TemporaryDirectory(prefix='.input-import-',dir=ROOT) as tmp:
 stage=Path(tmp)/'production-inputs';stage.mkdir()
 for sample,files in manifest.items():
  out=stage/sample;out.mkdir();records=[]
  for name,expected in files.items():
   filename=name.replace('style-reference-','reference-')
   shutil.copyfile(source/sample/filename,out/filename)
   if sha(out/filename)!=expected:raise SystemExit('Copy hash mismatch')
   records.append({'file':filename,'sha256':expected})
  (out/'input-manifest.json').write_text(json.dumps({'sample':sample,'scientific_inputs':records,'isolation':'fresh model context and access constraint, not OS sandbox'},ensure_ascii=False,indent=2))
 stage.rename(dest)
print(json.dumps({'samples':len(manifest),'scientific_files':sum(map(len,manifest.values())),'hashes_verified':True,'generation_started':False}))
