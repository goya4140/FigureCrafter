"""Source-backed fidelity repairs; no benchmark identifiers or paid calls."""
from copy import deepcopy
from pathlib import Path
from functools import lru_cache
import json,math,re,subprocess,hashlib
from .common import style

@lru_cache(maxsize=128)
def font_file(family,bold=False,italic=False):
    from PIL import ImageFont
    # User-supplied task fonts are isolated from the global font installation.
    import os
    for directory in os.environ.get('DECOMPOSER_FONT_DIRS','').split(os.pathsep):
        if not directory:continue
        for p in Path(directory).rglob('*.ttf'):
            try:
                name=ImageFont.truetype(str(p),20).getname()
                if name[0].casefold()==family.casefold():return str(p)
            except OSError:pass
    command=['/opt/homebrew/bin/fc-match',family+(':style=Bold' if bold else '')+(':slant=italic' if italic else ''),'-f','%{file}']
    try:
        p=subprocess.run(command,capture_output=True,text=True,timeout=10)
        return p.stdout.strip() if p.returncode==0 else None
    except OSError:return None

def source_rotation(obj,component,image):
    """Choose quarter-turn by source glyph shape, never by aspect ratio alone."""
    import numpy as np
    from PIL import Image,ImageDraw,ImageFont,ImageOps
    hint=obj.get('geometry_hint','').lower();text=str(obj.get('text') or '')
    if not text or '\n' in text or len(text)<3 or any(ord(c)>0x2fff for c in text):return None
    if not any(w in hint for w in ['vertical','rotat','竖排']) and obj['bbox'][2]>=.7*obj['bbox'][3]:return None
    ps=[p for p in component['primitives'] if p['kind']=='text']
    if len(ps)!=1 or ps[0].get('runs'):return None
    p=ps[0];st=p['style'];font=font_file(st['font_family'],st.get('bold'),st.get('italic'))
    if not font:return None
    x,y,w,h=obj['bbox'];crop=image.crop((int(x),int(y),math.ceil(x+w),math.ceil(y+h))).convert('L');arr=np.array(crop);mask=arr<170
    ys,xs=np.where(mask)
    if len(xs)<10:return None
    source=Image.fromarray((mask[min(ys):max(ys)+1,min(xs):max(xs)+1]*255).astype('uint8'));target_size=source.size
    face=ImageFont.truetype(font,60);box=face.getbbox(text);glyph=Image.new('L',(box[2]-box[0]+12,box[3]-box[1]+12),0);ImageDraw.Draw(glyph).text((6-box[0],6-box[1]),text,font=face,fill=255);glyph=glyph.crop(glyph.getbbox())
    a=np.array(source)>0;results=[]
    for angle in [90,270]:
        # OOXML positive rotation is clockwise, PIL positive is counterclockwise.
        template=glyph.rotate(-angle,expand=True).resize(target_size,Image.Resampling.LANCZOS);b=np.array(template)>95
        best=0
        for dx in [-1,0,1]:
            for dy in [-1,0,1]:
                shifted=np.zeros_like(b);sx=max(0,dx);sy=max(0,dy);ex=min(b.shape[1],b.shape[1]+dx);ey=min(b.shape[0],b.shape[0]+dy)
                shifted[sy:ey,sx:ex]=b[sy-dy:ey-dy,sx-dx:ex-dx];best=max(best,float((a&shifted).sum()/max(1,(a|shifted).sum())))
        results.append((best,angle))
    results.sort(reverse=True)
    if results[0][0]>=.23 and results[0][0]-results[1][0]>=.045:return {'angle':results[0][1],'score':round(results[0][0],4),'alternative_score':round(results[1][0],4),'font':font}
    return None

def recipe_overrides(directory):
    """Follow verified cache lineage to the original sparse style declarations."""
    seen=set();p=Path(directory)
    for _ in range(24):
        if p in seen:break
        seen.add(p)
        for name in ['merged-model-output.json','model-output.json']:
            f=p/'representation'/name
            if f.exists():
                j=json.loads(f.read_text())
                if 'plans' in j and 'styles' in j:
                    styles={s['id']:{k:v for k,v in s['value'].items() if v is not None} for s in j['styles']};out={}
                    for plan in j['plans']:
                        for op in plan.get('ops',[]):
                            for n,run in enumerate(op.get('runs',[])):out[(plan['label_id'],op['id'],n)]=(run.get('source_text',run.get('text','')),styles[run['style_id']])
                    return out
        f=p/'representation/canonical-checkpoint-import.json'
        if not f.exists():break
        p=Path(json.loads(f.read_text())['source'])
    return {}

def overlap(a,b):
    inter=max(0,min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0]))*max(0,min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1]));return inter/max(1,a[2]*a[3]+b[2]*b[3]-inter)

def normalize(recognition,representation,image_path,directory,config):
    from PIL import Image
    import numpy as np
    r=deepcopy(recognition);rep=deepcopy(representation);objects={o['id']:o for o in r['objects']};report={'version':'rc1h','rotations':[],'run_inheritance':[],'backgrounds':[],'duplicates':[],'activation_assets':[],'model_calls':0}
    origin=config.get('engineering',{}).get('reuse_canonical_representation_from');overrides=recipe_overrides(origin) if origin else {}
    prior_layout={}
    if origin and config.get('engineering',{}).get('preserve_checkpoint_layout') and (Path(origin)/'layout/layout.json').exists():prior_layout={p['label_id']:p for p in json.loads((Path(origin)/'layout/layout.json').read_text())['placements']}
    with Image.open(image_path) as im:
        image=im.convert('RGB')
        report['activation_assets']=activation_assets(r,rep,image,directory,image_path)
        for c in rep['components']:
            o=objects[c['label_id']]
            from .layout import _rotation_hint
            if not _rotation_hint(o):
                rot=source_rotation(o,c,image)
                if rot:o['geometry_hint']=o.get('geometry_hint','')+f"; source-glyph reading {'clockwise 90 degrees' if rot['angle']==90 else 'counterclockwise 270 degrees'}";report['rotations'].append({'owner':o['id'],**rot})
            for p in c['primitives']:
                if p['kind']!='text':continue
                for n,run in enumerate(p.get('runs',[])):
                    raw=overrides.get((o['id'],p['id'],n))
                    if not raw or raw[0]!=run.get('source_text',run.get('text','')):continue
                    declared=raw[1];previous=style(declared,text=True);changed=[]
                    for field in ['font_family','font_size','align','line_spacing']:
                        if field not in declared and field in p['style'] and run['style'].get(field)==previous.get(field):
                            if run['style'].get(field)!=p['style'][field]:run['style'][field]=p['style'][field];changed.append(field)
                    if changed:report['run_inheritance'].append({'owner':o['id'],'primitive':p['id'],'run':n,'inherited':changed})
            hint=o.get('geometry_hint','').lower()
            # A declared colored token background must be a separate native object.
            existing_bg=next((p for p in c['primitives'] if p['id']=='source_token_background'),None)
            if o.get('text') and ('background' in hint or 'token' in hint or 'token' in o['text'].lower()) and c['primitives'] and all(p['kind']=='text' or p['id']=='source_token_background' for p in c['primitives']):
                x,y,w,h=o['bbox'];arr=np.array(image.crop((int(x),int(y),math.ceil(x+w),math.ceil(y+h))));bright=arr[arr.max(2)>200]
                if len(bright)>.55*arr.shape[0]*arr.shape[1]:
                    color=np.median(bright,axis=0);consistent=(np.max(abs(bright.astype(float)-color),axis=1)<18).mean()
                    if color.min()<247 and color.max()-color.min()>10 and consistent>.65:
                        fill='#'+''.join(f'{int(v):02X}' for v in color);pid='source_token_background'
                        placement=prior_layout.get(o['id'],{}).get('bbox',o['bbox']);px,py,pw,ph=placement
                        bbox=[round((x-px)*c['width']/pw,4),round((y-py)*c['height']/ph,4),round(w*c['width']/pw,4),round(h*c['height']/ph,4)]
                        bg={'id':pid,'kind':'rect','bbox':bbox,'style':style({'fill':fill,'stroke':'none'}),'editable':True}
                        if existing_bg:existing_bg.update(bg)
                        else:c['primitives'].insert(0,bg);c['primitive_order'].insert(0,pid)
                        report['backgrounds'].append({'owner':o['id'],'fill':fill,'source_bbox':o['bbox'],'basis':'source_cell_frame_independent_of_text_expansion'})
        # Remove only a composite outline already owned by a single independent shape.
        independent=[]
        for c in rep['components']:
            o=objects[c['label_id']]
            if o['category']=='shape' and len(c['primitives'])==1:
                p=c['primitives'][0]
                if p['kind'] in {'ellipse','rect','round_rect'}:independent.append((c,o,p))
        for c in rep['components']:
            owner=objects[c['label_id']];remove=[]
            if len(c['primitives'])<2:continue
            for p in c['primitives']:
                if p['kind'] not in {'ellipse','rect','round_rect'}:continue
                b=p['bbox'];global_box=[owner['bbox'][0]+b[0],owner['bbox'][1]+b[1],b[2],b[3]]
                for other,obj,prim in independent:
                    if obj['id']==owner['id'] or obj['bbox'][2]*obj['bbox'][3]>.5*owner['bbox'][2]*owner['bbox'][3]:continue
                    if prim['kind']!=p['kind']:continue
                    def close_color(a,b):
                        if a=='none' or b=='none':return a==b
                        return max(abs(int(a[k:k+2],16)-int(b[k:k+2],16)) for k in [1,3,5])<=12
                    if not all(close_color(prim['style'].get(k,'none'),p['style'].get(k,'none')) for k in ['fill','stroke']):continue
                    if overlap(global_box,obj['bbox'])>.9:
                        remove.append(p['id']);report['duplicates'].append({'composite_owner':owner['id'],'primitive':p['id'],'independent_owner':obj['id'],'iou':overlap(global_box,obj['bbox'])});break
            if remove and len(remove)<len(c['primitives']):c['primitives']=[p for p in c['primitives'] if p['id'] not in remove];c['primitive_order']=[p for p in c['primitive_order'] if p not in remove]
    return r,rep,report

def source_occlusions(recognition,representation,image_path):
    """A line visible outside but absent through an observed image is behind it."""
    from PIL import Image
    import numpy as np
    objects={o['id']:o for o in recognition['objects']};images=[o for o in recognition['objects'] if o['category']=='image' and not o.get('text')];relations=[{'behind':a['source_label_id'],'front':m['owner'],'basis':'native_symbol_separated_from_activation'} for a in representation['assets'].values() if a.get('source_basis')=='observed_atomic_activation_field' for m in a.get('native_masks',[])]
    arr=np.array(Image.open(image_path).convert('RGB')).astype(int)
    for c in representation['components']:
        owner=objects[c['label_id']]
        for p in c['primitives']:
            if p['kind'] not in {'line','polyline'} or not p['style'].get('dashed'):continue
            col=p['style'].get('stroke','none')
            if col=='none' or max(int(col[k:k+2],16) for k in [1,3,5])-min(int(col[k:k+2],16) for k in [1,3,5])<40:continue
            rgb=np.array([int(col[k:k+2],16) for k in [1,3,5]]);pts=[[owner['bbox'][0]+a,owner['bbox'][1]+b] for a,b in p['points']];samples=[]
            for a,b in zip(pts,pts[1:]):
                n=max(2,int(math.dist(a,b)/3));samples.extend([[a[0]+(b[0]-a[0])*t/n,a[1]+(b[1]-a[1])*t/n] for t in range(n)])
            for front in images:
                ob,fb=owner['bbox'],front['bbox']
                if min(ob[0]+ob[2],fb[0]+fb[2])<=max(ob[0],fb[0]) or min(ob[1]+ob[3],fb[1]+fb[3])<=max(ob[1],fb[1]):continue
                x,y,w,h=front['bbox'];inside=[];outside=[];radius=max(3,math.ceil(p['style'].get('stroke_width',1)/2+2))
                for a,b in samples:
                    if not (0<=a<arr.shape[1] and 0<=b<arr.shape[0]):continue
                    crop=arr[max(0,int(b)-radius):min(arr.shape[0],int(b)+radius+1),max(0,int(a)-radius):min(arr.shape[1],int(a)+radius+1)];found=bool((np.linalg.norm(crop-rgb,axis=2)<55).any())
                    if x+.2*w<a<x+.8*w and y+.15*h<b<y+.85*h:inside.append(found)
                    elif not(x-8<a<x+w+8 and y-8<b<y+h+8):outside.append(found)
                if len(inside)>=12 and len(outside)>=15 and sum(inside)/len(inside)<.12 and sum(outside)/len(outside)>.45:relations.append({'behind':owner['id'],'front':front['id'],'basis':'source_colored_line_visible_outside_absent_inside_image','inside_hits':sum(inside),'inside_samples':len(inside),'outside_fraction':sum(outside)/len(outside)})
    return relations

def rounded_path(w,h,r):
    r=max(0,min(r,min(w,h)/2));return f'M {r} 0 H {w-r} Q {w} 0 {w} {r} V {h-r} Q {w} {h} {w-r} {h} H {r} Q 0 {h} 0 {h-r} V {r} Q 0 0 {r} 0 Z'

def activation_pixels(image,box,polygon,stroke_width,native_masks=()):
    """Observed empirical field only, transparent outside native outline."""
    from PIL import Image,ImageDraw,ImageFilter
    x,y,w,h=box;left,top=math.floor(x),math.floor(y);right,bottom=math.ceil(x+w),math.ceil(y+h)
    crop=image.convert('RGBA').crop((left,top,right,bottom));mask=Image.new('L',crop.size,0);ImageDraw.Draw(mask).polygon([(x+a-left,y+b-top) for a,b in polygon],fill=255)
    radius=max(1,math.ceil(stroke_width/2));mask=mask.filter(ImageFilter.MinFilter(2*radius+1));draw=ImageDraw.Draw(mask)
    for item in native_masks:
        for a,b,c,d in item['circles']:draw.ellipse((a-left+1,b-top+1,a+c-left-2,b+d-top-2),fill=0)
    crop.putalpha(mask);return crop,[left,top,right-left,bottom-top]

def activation_assets(r,rep,image,directory,image_path):
    from .atomic_objects import text_crop_conflicts
    from .common import write_json,sha256
    directory=Path(directory);report=[];evidence_file=Path(image_path).parent/'observations/evidence-ir.json'
    evidence=json.loads(evidence_file.read_text()) if evidence_file.exists() else {}
    source=directory/'fidelity-contracts-input.json'
    if not any(c.get('empirical_contract_version') for c in rep['components']):write_json(source,rep)
    by={c['label_id']:c for c in rep['components']}
    for o in r['objects']:
        c=by[o['id']];hint=o.get('geometry_hint','').lower()
        if c.get('empirical_contract_version')==1:o['category']='image';c['category']='image';continue
        if o['category']!='shape' or 'activation map' not in hint or o.get('text') or min(o['bbox'][2:])<20:continue
        if any(p['kind']=='image' for p in c['primitives']):continue
        conflicts=text_crop_conflicts(o['bbox'],r,o['id'],evidence);masks=[];blocked=False
        for conflict in conflicts:
            label=next((v for v in r['objects'] if v['id']==conflict['owner_id']),None)
            circles=source_ellipsis(image,label) if label and label.get('text') in {'…','...'} else None
            if not circles:blocked=True;break
            masks.append({'owner':label['id'],'circles':circles})
        if blocked:continue
        outlines=[p for p in c['primitives'] if p['kind']=='polygon' and len(p['points'])==4 and p['style']['stroke']!='none']
        if len(outlines)!=1:continue
        outline=deepcopy(outlines[0]);pixels,bounds=activation_pixels(image,o['bbox'],outline['points'],outline['style']['stroke_width'],masks);aid='activation-'+hashlib.sha256((o['id']+repr(o['bbox'])).encode()).hexdigest()[:16];path=directory/'assets'/(aid+'.png');path.parent.mkdir(parents=True,exist_ok=True);pixels.save(path)
        asset={'status':'ready','source_label_id':o['id'],'source_bbox':list(o['bbox']),'source_basis':'observed_atomic_activation_field','source_candidate_id':'recognition:'+o['id'],'mode':'source_crop','path':str(path.resolve()),'sha256':sha256(path),'atomic_raster_unit':True,'contains_reconstructable_content':False,'native_masks':masks,'native_outline_id':outline['id'],'polygon':outline['points'],'outline_stroke_width':outline['style']['stroke_width'],'contract_input_sha256':sha256(source),'decomposition_note':'One observed portrait activation field, not a synthetic silhouette. Native outer polygon retained separately; outside/edge pixels transparent.'}
        for item in masks:
            label=next(v for v in r['objects'] if v['id']==item['owner']);symbol=by[label['id']];label['recognized_symbol']=label['text'];label.update(category='shape',text='');symbol['category']='shape';symbol['primitives']=[{'id':f'dot_{n}','kind':'ellipse','bbox':[a-label['bbox'][0],b-label['bbox'][1],w,h],'style':style({'fill':'#000000','stroke':'none'}),'editable':True} for n,(a,b,w,h) in enumerate(item['circles'])];symbol['primitive_order']=[p['id'] for p in symbol['primitives']]
        outline['style']['fill']='none';x,y,_,_=o['bbox'];image_prim={'id':'observed_activation','kind':'image','bbox':[bounds[0]-x,bounds[1]-y,bounds[2],bounds[3]],'style':style({}),'asset_id':aid,'editable':False,'fallback_reason':'observed_empirical_activation_field'}
        rep['assets'][aid]=asset;c.update(primitives=[image_prim,outline],primitive_order=[image_prim['id'],outline['id']],category='image',empirical_contract_version=1);o['category']='image';o['representation_preference']='localized_raster';report.append({'owner':o['id'],'source_bbox':o['bbox'],'native_outline':outline['id'],'asset':aid})
    return report

def verify_activation_asset(asset,recognition,layout,scene,image):
    from PIL import Image
    from .common import sha256
    try:
        path=Path(asset['path']);root=next(p for p in path.parents if (p/'input-normalized.png').exists())
        rawfile=root/'representation/fidelity-contracts-input.json'
        assert sha256(rawfile)==asset['contract_input_sha256']
        raw=json.loads(rawfile.read_text());c=next(c for c in raw['components'] if c['label_id']==asset['source_label_id'])
        o=next(o for o in recognition['objects'] if o['id']==asset['source_label_id'])
        assert 'activation map' in o.get('geometry_hint','').lower() and not o.get('text') and asset['source_bbox']==o['bbox']
        outline=next(p for p in c['primitives'] if p['id']==asset['native_outline_id'])
        assert outline['kind']=='polygon' and outline['points']==asset['polygon'] and outline['style']['stroke_width']==asset['outline_stroke_width']
        expected,_=activation_pixels(Image.open(image),o['bbox'],outline['points'],outline['style']['stroke_width'],asset.get('native_masks',[]))
        assert sha256(path)==asset['sha256']
        actual=Image.open(path).convert('RGBA')
        assert expected.size==actual.size and expected.tobytes()==actual.tobytes()
        for item in asset.get('native_masks',[]):
            label=next(v for v in recognition['objects'] if v['id']==item['owner'])
            assert source_ellipsis(Image.open(image),label)==item['circles']
            for n,box in enumerate(item['circles']):
                assert any(e['id']==label['id']+f'/dot_{n}' and e['kind']=='ellipse' and e['bbox']==box for e in scene['elements'])
        assert any(e['id']==o['id']+'/'+outline['id'] and e['editable'] and e['style']['fill']=='none' for e in scene['elements'])
        return True
    except (AssertionError,KeyError,OSError,StopIteration,ValueError):return False


def source_ellipsis(image,obj):
    import cv2,numpy as np
    x,y,w,h=obj['bbox'];crop=np.array(image.convert('RGB').crop((int(x),int(y),math.ceil(x+w),math.ceil(y+h))));mask=(crop.max(2)<90).astype('uint8');count,labels,stats,centers=cv2.connectedComponentsWithStats(mask,8);circles=[]
    for a,b,c,d,area in stats[1:]:
        if min(c,d)>=4 and .72<c/max(1,d)<1.38 and .58<area/max(1,c*d)<.9:circles.append([int(x+a),int(y+b),int(c),int(d)])
    circles.sort()
    if len(circles)!=3 or max(b+d/2 for a,b,c,d in circles)-min(b+d/2 for a,b,c,d in circles)>3:return None
    distances=[circles[n+1][0]-circles[n][0] for n in [0,1]]
    if min(distances)<=0 or max(distances)/min(distances)>1.3:return None
    return circles
