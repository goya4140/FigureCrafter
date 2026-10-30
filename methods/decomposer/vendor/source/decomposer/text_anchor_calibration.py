"""Bounded font-metric calibration for independently anchored native labels.
Two synthetic native-PPTX atlases measure the actual renderer. No raster text,
source content substitution, free-form scene search or new language model calls.
Callers must validate the final native-PPTX render before adopting a proposal.
"""
from copy import deepcopy
from pathlib import Path
import math,re
import numpy as np
from PIL import Image
from .common import read_json,write_json
from .pptx import compile_scene

def pixels(path):
 with Image.open(path) as im:
  rgba=im.convert('RGBA');return np.asarray(Image.alpha_composite(Image.new('RGBA',rgba.size,'white'),rgba).convert('RGB'))
def mask(p):return np.max(p,axis=2)<190

def tight(m):
 ys,xs=np.where(m)
 if not len(xs):return None
 return [int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]
def clean(m):
 # Underlines are separate native geometry, not glyph height evidence.
 m=m.copy()
 if m.shape[1]>10:
  rows=np.flatnonzero(m.sum(axis=1)>.85*m.shape[1])
  for y in rows:
   if (y<2 or y>=m.shape[0]-4):m[y,:]=False
 return m

def overlap(source,candidate):
 best=None
 for dy in range(-2,3):
  for dx in range(-2,3):
   x0=min(0,dx);y0=min(0,dy);w=max(source.shape[1],dx+candidate.shape[1])-x0;h=max(source.shape[0],dy+candidate.shape[0])-y0
   a=np.zeros((h,w),bool);b=np.zeros((h,w),bool);a[-y0:-y0+source.shape[0],-x0:-x0+source.shape[1]]=source;b[dy-y0:dy-y0+candidate.shape[0],dx-x0:dx-x0+candidate.shape[1]]=candidate
   score=float((a&b).sum()/max(1,(a|b).sum()));key=(score,-abs(dx)-abs(dy),dx,dy)
   if best is None or key>best:best=key
 return best[0],best[2],best[3]

def anchors(scene,recognition,evidence,reference,allowed_ids=None):
 cs={c['id']:c for c in evidence.get('candidates',[]) if c.get('source')=='ocr'};owners={o['id']:o for o in recognition['objects']};out=[]
 for el in scene['elements']:
  if el['kind']!='text' or el.get('runs') or abs(el.get('rotation_deg',0))>.01:continue
  if allowed_ids is not None and el['source_label_id'] not in allowed_ids:continue
  text=el.get('text','');owner=owners.get(el['source_label_id'],{})
  if not re.fullmatch(r'[A-Za-z0-9_./ -]{1,60}',text):continue
  support=[cs[i] for i in owner.get('candidate_ids',[]) if i in cs and cs[i].get('text')==text]
  if len(support)!=1:continue
  font=float(el['style'].get('font_size',0))
  if not 8<=font<=70:continue
  x,y,w,h=support[0]['bbox'];x0,y0=max(0,math.floor(x)-2),max(0,math.floor(y)-2);x1,y1=min(reference.shape[1],math.ceil(x+w)+2),min(reference.shape[0],math.ceil(y+h)+2)
  foreground=clean(mask(reference[y0:y1,x0:x1]));b=tight(foreground)
  if b is None or foreground.sum()<12:continue
  a,b0,c,d=b;glyph=foreground[b0:d,a:c]
  out.append({'id':el['id'],'owner':el['source_label_id'],'text':text,'font':font,'style':deepcopy(el['style']),'evidence_id':support[0]['id'],'source_ink_bbox':[x0+a,y0+b0,c-a,d-b0],'glyph':glyph})
 return out

def _atlas_batch(items,recipes,directory,config):
 cells=[];els=[];CW,CH,COLS=600,170,4
 for item,sizes in zip(items,recipes):
  for size in sorted(set(round(max(8,min(90,s)),2) for s in sizes)):
   i=len(cells);x=(i%COLS)*CW+10;y=(i//COLS)*CH+5
   st={**item['style'],'font_size':size,'line_spacing':1.0,'align':'left','color':'#000000'}
   # A single line in a deliberately roomy frame does not trigger shrink.
   e={'id':f'probe-{i}','source_label_id':item['owner'],'category':'text','kind':'text','bbox':[x,y,CW-20,CH-10],'style':st,'rotation_deg':0,'editable':True,'text':item['text']}
   els.append(e);cells.append({'item_id':item['id'],'size':size,'x':x,'y':y,'w':CW-20,'h':CH-10})
 if not cells:return []
 scene={'schema_version':'decomposer.scene.v1','canvas':{'width':CW*COLS,'height':math.ceil(len(cells)/COLS)*CH,'background':'#FFFFFF'},'elements':els,'z_order':[e['id'] for e in els],'assets':{},'degraded':False}
 directory=Path(directory);write_json(directory/'scene.json',scene);compile_scene(directory/'scene.json',directory,config,True);image=pixels(directory/'preview.png');out=[]
 for cell in cells:
  x,y,w,h=[cell[k] for k in ['x','y','w','h']];m=mask(image[y:y+h,x:x+w]);b=tight(m)
  if b is None:continue
  a,b0,c,d=b
  if c>=w-2 or d>=h-2:continue
  item=next(t for t in items if t['id']==cell['item_id']);score,dx,dy=overlap(item['glyph'],m[b0:d,a:c])
  out.append({**cell,'score':score,'shift_x':dx,'shift_y':dy,'glyph_offset':[a,b0],'glyph_size':[c-a,d-b0]})
 write_json(directory/'measurements.json',out);return out

def atlas(items,recipes,directory,config):
 # Keep synthetic slides below the native PowerPoint slide-size ceiling.
 groups=[];current=[];cells=0
 for item,sizes in zip(items,recipes):
  n=len(set(round(max(8,min(90,v)),2) for v in sizes))
  if current and cells+n>96:groups.append(current);current=[];cells=0
  current.append((item,sizes));cells+=n
 if current:groups.append(current)
 if len(groups)<=1:return _atlas_batch(items,recipes,directory,config)
 result=[]
 for i,group in enumerate(groups,1):
  result.extend(_atlas_batch([x[0] for x in group],[x[1] for x in group],Path(directory)/f'batch-{i:03d}',config))
 write_json(Path(directory)/'measurements.json',result);return result

def propose(scene,recognition,evidence,reference_path,directory,config,allowed_ids=None):
 directory=Path(directory);reference=pixels(reference_path);items=anchors(scene,recognition,evidence,reference,allowed_ids)
 if not items:return deepcopy(scene),{'status':'no_eligible_labels','proposals':[]}
 coarse=atlas(items,[[t['font']*s for s in [.6,.75,.9,1.,1.1,1.25]] for t in items],directory/'coarse-atlas',config)
 by_id={}
 for t in items:
  options=[p for p in coarse if p['item_id']==t['id']]
  if options:by_id[t['id']]=max(options,key=lambda p:(p['score'],-abs(p['size']-t['font'])))
 fine_items=[t for t in items if t['id'] in by_id]
 fine=atlas(fine_items,[[by_id[t['id']]['size']+d for d in [-2,-1,0,1,2]] for t in fine_items],directory/'fine-atlas',config)
 result=deepcopy(scene);elements={e['id']:e for e in result['elements']};proposals=[]
 for t in fine_items:
  best=max([p for p in coarse+fine if p['item_id']==t['id']],key=lambda p:(p['score'],-abs(p['size']-t['font'])))
  if best['score']<.35:continue
  x,y,w,h=t['source_ink_bbox'];ox,oy=best['glyph_offset'];font=best['size'];fx=x+best['shift_x']-ox;fy=y+best['shift_y']-oy
  fw=max(w+3*font,best['glyph_size'][0]+ox+2*font);fh=max(font*2.4,oy+h+font)
  fw=min(fw,scene['canvas']['width']-fx);fh=min(fh,scene['canvas']['height']-fy)
  if fx<0 or fy<0 or fw<ox+w+3 or fh<oy+h+3:continue
  el=elements[t['id']];old=deepcopy(el);el['bbox']=[round(v,4) for v in [fx,fy,fw,fh]];el['style'].update(font_size=font,line_spacing=1.,align='left')
  proposals.append({'id':t['id'],'owner':t['owner'],'text':t['text'],'source_ink_bbox':t['source_ink_bbox'],'evidence_id':t['evidence_id'],'old_bbox':old['bbox'],'new_bbox':el['bbox'],'old_font_size':t['font'],'new_font_size':font,'projected_glyph_iou':best['score']})
 report={'status':'candidate_requires_real_render_gate','proposals':proposals,'atlas_renders':sum(1 for _ in directory.glob('**/report.json')),'eligible_labels':len(items)}
 write_json(directory/'proposal.json',report);return result,report
