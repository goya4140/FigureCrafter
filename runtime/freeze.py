from pathlib import Path
import argparse,hashlib,json,sys
p=argparse.ArgumentParser();p.add_argument('--workspace',required=True);p.add_argument('--delivery',required=True);p.add_argument('--iteration',required=True);a=p.parse_args()
w=Path(a.workspace).resolve();d=Path(a.delivery).resolve()
if not d.is_relative_to(w):raise SystemExit('Delivery must be inside sample workspace')
required=['main-figure.pptx','main-figure.pdf','main-figure.png','publication-preview.png','figure-caption.md','model-understanding.md','model-representation.md','visual-review.md']
missing=[x for x in required if not (d/x).is_file()]
if missing:raise SystemExit('Missing delivery records: '+str(missing))
sys.path.insert(0,str(w/'framework'))
from harness.model_led_drawing import verify
verify(w/'run')
record=json.loads((d/'delivery.json').read_text())
if record['iteration']!=a.iteration:raise SystemExit('Selected iteration differs from actual delivery')
for name,expected in record['hashes'].items():
 if hashlib.sha256((d/name).read_bytes()).hexdigest()!=expected:raise SystemExit('Delivered file changed: '+name)
candidate=w/'run/iterations'/a.iteration/'candidate.json'
if json.loads(candidate.read_text())['pptx_sha256']!=hashlib.sha256((d/'main-figure.pptx').read_bytes()).hexdigest():raise SystemExit('Delivery does not match selected candidate')
settings=json.loads((w/'model-settings.json').read_text())
if not settings.get('actual_model'):raise SystemExit('Record actual model identity in model-settings.json before freeze')
files={str(x.relative_to(w)):hashlib.sha256(x.read_bytes()).hexdigest() for folder in [d,w/'run',w/'work'] if folder.exists() for x in folder.rglob('*') if x.is_file() and x.name!='freeze.json'}
files['model-settings.json']=hashlib.sha256((w/'model-settings.json').read_bytes()).hexdigest()
out=w/'freeze.json'
with out.open('x') as f:json.dump({'schema':'astra-isolated-freeze.v1','selected_iteration':a.iteration,'hashes':files,'quality':'Mechanical freeze only; model visual review must precede it','scientific_comparison_after_freeze':True},f,ensure_ascii=False,indent=2)
print(out)
