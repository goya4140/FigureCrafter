"""Replayable removal of separately owned overlays; never flatten native groups."""
import math
import numpy as np
from PIL import Image,ImageDraw

def bounds(box):
 x,y,w,h=box
 return [int(x),int(y),max(int(x+w+.999),int(x)+1),max(int(y+h+.999),int(y)+1)]

def color_mask(array,color):
 rgb=np.array([int(color.lstrip('#')[i:i+2],16) for i in (0,2,4)],dtype=np.int32)
 d=((array.astype(np.int32)-rgb)**2).sum(axis=2)
 return (d<90**2)&(np.ptp(array.astype(np.int32),axis=2)>45)

def transformed_crop(image,box,transforms):
 crop=image.convert('RGBA').crop(bounds(box));pixels=np.array(crop);x0,y0,_,_=bounds(box)
 for t in transforms:
  a,b,c,d=bounds(t['bbox']);a=max(a-x0,0);b=max(b-y0,0);c=min(c-x0,crop.width);d=min(d-y0,crop.height)
  if a>=c or b>=d:raise ValueError('Transform outside asset')
  if t['op']=='occluded_rectangle':pixels[b:d,a:c,3]=0
  elif t['op']=='native_colored_stroke':
   mask=color_mask(pixels[b:d,a:c,:3],t['color']);yy,xx=np.indices(mask.shape);rim=float(t['stroke_width'])+2;mask &= (xx<rim)|(yy<rim)|(xx>=mask.shape[1]-rim)|(yy>=mask.shape[0]-rim);pixels[b:d,a:c,3][mask]=0
  elif t['op']=='native_color_inpaint':
   import cv2
   roi=pixels[b:d,a:c,:3].copy();mask=color_mask(roi,t['color']).astype('uint8')*255
   # Only observed colored foreground glyph pixels are reconstructed. All
   # other source pixels remain byte-identical; the unknown background is
   # explicitly an estimate, never claimed as recovered ground truth.
   if not mask.any():raise ValueError('Native foreground color not observed')
   inpaint=cv2.inpaint(roi,mask,3,cv2.INPAINT_TELEA)
   roi[mask>0]=inpaint[mask>0];pixels[b:d,a:c,:3]=roi
  else:raise ValueError('Unknown asset transform')
 return Image.fromarray(pixels)

def augment_decisions(result,recognition,evidence,image):
 from .atomic_objects import is_photographic_leaf,text_crop_conflicts
 objects={o['id']:o for o in recognition['objects']};canvas=recognition['canvas']['width']*recognition['canvas']['height']
 for d in result['decisions']:
  o=objects[d['label_id']]
  if d['decision']=='localized_raster' or o['category']!='image' or o.get('semantic_level')!='leaf' or not is_photographic_leaf(o):continue
  x,y,w,h=o['bbox'];transforms=[]
  for other in recognition['objects']:
   if other['id']==o['id']:continue
   a,b,c,e=other['bbox'];contained=a>=x and b>=y and a+c<=x+w and b+e<=y+h
   if not contained:continue
   if other['category']=='image' and is_photographic_leaf(other) and c*e<w*h*.5:
    transforms.append({'op':'occluded_rectangle','label_id':other['id'],'bbox':other['bbox']})
   elif other['category']=='shape' and 'rectangular outline' in other.get('geometry_hint','').lower():
    color=other.get('style_hint',{}).get('stroke','none')
    if color!='none' and max(int(color.lstrip('#')[i:i+2],16) for i in [0,2,4])-min(int(color.lstrip('#')[i:i+2],16) for i in [0,2,4])>45:
     transforms.append({'op':'native_colored_stroke','label_id':other['id'],'bbox':other['bbox'],'color':color,'stroke_width':other.get('style_hint',{}).get('stroke_width',1)})
  conflicts=text_crop_conflicts(o['bbox'],recognition,o['id'],evidence)
  color_targets=[]
  for target in dict.fromkeys(t['owner_id'] for t in conflicts):
   text=objects[target];color=text.get('style_hint',{}).get('color','none')
   if color=='none':break
   rgb=[int(color.lstrip('#')[i:i+2],16) for i in (0,2,4)]
   if max(rgb)-min(rgb)<60:break
   a,b,c,e=text['bbox'];box=[max(x,a-2),max(y,b-2),min(x+w,a+c+2)-max(x,a-2),min(y+h,b+e+2)-max(y,b-2)]
   color_targets.append({'op':'native_color_inpaint','label_id':target,'bbox':box,'color':color,'background_status':'estimated_only_at_observed_foreground_color_pixels'})
  else:
   if color_targets and 'grayscale' in o.get('geometry_hint','').lower():transforms.extend(color_targets)
   elif conflicts:continue
   if not transforms:continue
   # Large photo owners qualify only when removing independently recognized
   # foreground photos makes the remaining opaque source area obey the SAME
   # raster area cap. A large filled diagram never qualifies.
   if w*h/canvas>result['policy']['max_recognition_leaf_area_ratio'] and not any(t['op']=='occluded_rectangle' for t in transforms):continue
   try:crop=transformed_crop(image,o['bbox'],transforms)
   except ValueError:continue
   opaque=np.asarray(crop)[:,:,3]>0;visible=float(opaque.sum())/canvas
   if visible>result['policy']['max_recognition_leaf_area_ratio']:continue
   if color_targets:
    # Bound inpainting to small atomic photo crops with grayscale observed
    # background. This is not document/card screenshot permission.
    if w*h/canvas>.02:continue
    arr=np.asarray(image.convert('RGB').crop(bounds(o['bbox'])));gray=np.ptp(arr.astype('int32'),axis=2)<25
    if float(gray.mean())<.65:continue
   d.update(decision='localized_raster',approved_bbox=o['bbox'],source_candidate_id='recognition:'+o['id'],source_basis='recognition_atomic_photo_with_owned_overlays',area_ratio=round(visible,6),bbox_area_ratio=round(w*h/canvas,6),asset_transforms=transforms,native_text_exclusions=[],reason_codes=['photographic_leaf','independently_owned_foreground_removed','visible_opaque_area_below_original_cap','replayable_source_transform'])
 result['summary']={'localized_raster_count':sum(d['decision']=='localized_raster' for d in result['decisions']),'native_count':sum(d['decision']=='native' for d in result['decisions'])}
 return result
