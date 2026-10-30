"""Small source-verified photo/native-label boundary contracts; no hidden pixels."""
from pathlib import Path
from PIL import Image,ImageDraw
from .atomic_objects import is_photographic_leaf,text_crop_conflicts
from .common import read_json,sha256

def intersect(a,b):
 x=max(a[0],b[0]);y=max(a[1],b[1]);return [x,y,max(0,min(a[0]+a[2],b[0]+b[2])-x),max(0,min(a[1]+a[3],b[1]+b[3])-y)]
def white_border(im,b):
 x,y,w,h=map(int,b)
 if x<0 or y<0 or x+w>im.width or y+h>im.height:return False
 pixels=[im.getpixel((i,j))[:3] for i in range(x,x+w) for j in range(y,y+h) if i in (x,x+w-1) or j in (y,y+h-1)]
 return bool(pixels) and sum(min(p)>=240 for p in pixels)/len(pixels)>=.98

def augment(result,r,e,image):
 objects={o['id']:o for o in r['objects']};canvas=r['canvas']['width']*r['canvas']['height']
 with Image.open(image) as source:
  im=source.convert('RGB')
  for d in result['decisions']:
   o=objects[d['label_id']]
   if d['decision']=='localized_raster' or o['category']!='image' or o.get('semantic_level')!='leaf' or not is_photographic_leaf(o):continue
   original=list(o['bbox']);box=list(original);x,y,w,h=box
   if w*h/canvas>result['policy']['max_recognition_leaf_area_ratio']:continue
   conflicts=text_crop_conflicts(box,r,o['id'],e)
   if not conflicts:continue
   patches=[];trimmed=[];failed=False
   for tid in dict.fromkeys(c['owner_id'] for c in conflicts):
    t=objects[tid];a,b,c,z=t['bbox'];padded=[int(a)-2,int(b)-2,int(c)+4,int(z)+4]
    overlap=intersect(box,padded)
    # This extension admits only confidently recognized continuation dots.
    # Scientific text/equations keep the stricter original ownership policy.
    dots=t.get('text','').strip() in {'…','...','⋯'} and float(t.get('confidence',0))>=.9
    if dots and white_border(im,padded) and overlap[2]*overlap[3]/(w*h)<=.1:
     patches.append({'label_id':tid,'bbox':overlap,'verification_bbox':padded,'fill':'#FFFFFF','source_text':t['text']});continue
    # Preserve source coordinates: omit at most a thin edge strip; never stretch
    # the resulting crop over the original owner. Canonicalization already maps it.
    options=[]
    bx,by,bw,bh=box
    if a>bx and a<bx+bw:options.append([bx,by,a-bx,bh])
    if a+c>bx and a+c<bx+bw:options.append([a+c,by,bx+bw-a-c,bh])
    if b>by and b<by+bh:options.append([bx,by,bw,b-by])
    if b+z>by and b+z<by+bh:options.append([bx,b+z,bw,by+bh-b-z])
    options=[q for q in options if q[2]*q[3]/(w*h)>=.95 and not intersect(q,t['bbox'])[2]*intersect(q,t['bbox'])[3]]
    if not options:failed=True;break
    box=max(options,key=lambda q:q[2]*q[3]);trimmed.append({'label_id':tid,'reason':'native label at photo edge; source strip omitted, no background reconstruction'})
   if failed or sum(p['bbox'][2]*p['bbox'][3] for p in patches)/(w*h)>.12:continue
   # Every remaining conflict must be explicitly white-patched.
   remaining=text_crop_conflicts(box,r,o['id'],e)
   if any(c['owner_id'] not in {p['label_id'] for p in patches} for c in remaining):continue
   d.update(decision='localized_raster',approved_bbox=box,source_candidate_id='recognition:'+o['id'],source_basis='observed_photo_native_label_boundary',area_ratio=round(box[2]*box[3]/canvas,6),native_text_exclusions=patches,asset_transforms=[],boundary_contract={'source_owner_bbox':original,'source_area_retained':box[2]*box[3]/(w*h),'edge_trims':trimmed},reason_codes=['high_confidence_photo','native_labels_remain_editable','source_white_patch_or_bounded_edge_crop','no_hidden_pixel_reconstruction'])
 result['summary']={'localized_raster_count':sum(d['decision']=='localized_raster' for d in result['decisions']),'native_count':sum(d['decision']=='native' for d in result['decisions'])}
 return result

def verify(asset,r,layout,scene,image,config):
 try:
  from .asset_policy import decide_with_photo_overlays
  path=Path(asset['path']);root=next(p for p in path.parents if (p/'input-normalized.png').is_file() and (p/'observations/evidence-ir.json').is_file())
  if sha256(root/'input-normalized.png')!=sha256(image) or sha256(path)!=asset['sha256']:return False
  decision=next(d for d in decide_with_photo_overlays(r,read_json(root/'observations/evidence-ir.json'),config,image)['decisions'] if d['label_id']==asset['source_label_id'])
  if any(decision.get(a)!=asset.get(b) for a,b in [('source_basis','source_basis'),('approved_bbox','source_bbox'),('native_text_exclusions','native_text_exclusions')]):return False
  painted={e['source_label_id'] for e in scene['elements'] if e['kind']=='text' and e.get('editable')};visible={p['label_id'] for p in layout['placements'] if p['visible']}
  if not {p['label_id'] for p in decision['native_text_exclusions']}<=painted&visible:return False
  if not {p['label_id'] for p in decision['boundary_contract']['edge_trims']}<=painted&visible:return False
  x,y,w,h=asset['source_bbox'];bounds=[int(x),int(y),int(x+w+.999),int(y+h+.999)]
  with Image.open(image) as im,Image.open(path) as actual:
   expected=im.convert('RGBA').crop(bounds)
   for p in decision['native_text_exclusions']:
    a,b,c,d=p['bbox'];ImageDraw.Draw(expected).rectangle([int(a)-bounds[0],int(b)-bounds[1],int(a+c)-bounds[0]-1,int(b+d)-bounds[1]-1],fill='white')
   return expected.size==actual.size and expected.tobytes()==actual.convert('RGBA').tobytes()
 except (OSError,ValueError,KeyError,StopIteration):return False
