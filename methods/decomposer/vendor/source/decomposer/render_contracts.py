"""Deterministic, source-backed path, atomic image and occlusion contracts.
No sample IDs. Ambiguous legacy coordinate conventions remain untouched.
"""
from copy import deepcopy
from pathlib import Path
import hashlib, re, math
from .polygons import _path_commands
from .atomic_objects import text_crop_conflicts
from .common import style


def path_space(primitive):
    explicit = primitive.get('coordinate_space')
    if explicit in {'primitive', 'component'}:
        return explicit, 'explicit'
    try:
        subpaths = _path_commands(primitive.get('path',''))
    except ValueError:
        return None, 'unsupported_path_retained'
    pts=[]
    for sub in subpaths:
        x0=y0=0
        for cmd in sub:
            kind,*v=cmd
            if kind in {'M','L'}:
                x0,y0=v;pts.append((x0,y0))
            elif kind in {'Q','C'}:
                start=(x0,y0)
                for n in range(21):
                    t=n/20;u=1-t
                    if kind=='Q':pt=(u*u*start[0]+2*u*t*v[0]+t*t*v[2],u*u*start[1]+2*u*t*v[1]+t*t*v[3])
                    else:pt=(u**3*start[0]+3*u*u*t*v[0]+3*u*t*t*v[2]+t**3*v[4],u**3*start[1]+3*u*u*t*v[1]+3*u*t*t*v[3]+t**3*v[5])
                    pts.append(pt)
                x0,y0=v[-2:]

    if not pts:return None, 'empty_retained'
    x,y,w,h=primitive['bbox']
    # Curves may intentionally overshoot their frame slightly. Compare both
    # origins using normalized outside distance, not arbitrary text IDs.
    def loss(ox,oy):
        return sum(max(0,ox-a,a-ox-w)/max(1,w)+max(0,oy-b,b-oy-h)/max(1,h) for a,b in pts)/len(pts)
    local, component=loss(0,0),loss(x,y)
    # Full frame support disambiguates a local-origin graph or repeated path.
    # A tiny offset with incomplete support remains ambiguous (legacy policy).
    xs=[a for a,b in pts];ys=[b for a,b in pts]
    def frame_error(ox,oy):
        return max(abs(min(xs)-ox)/max(w,1),abs(max(xs)-ox-w)/max(w,1),abs(min(ys)-oy)/max(h,1),abs(max(ys)-oy-h)/max(h,1))
    le,ce=frame_error(0,0),frame_error(x,y)
    if le<.035 and ce-le>.12:return 'primitive','full_frame_legacy_local'
    if ce<.035 and le-ce>.12:return 'component','full_frame_legacy_component'
    if local <= .15 and component-local > .2:return 'primitive','unambiguous_legacy_local'
    if component <= .15 and local-component > .2:return 'component','unambiguous_legacy_component'
    return None,'ambiguous_legacy_retained'


def _intersect(a,b):
    x,y,w,h=a;u,v,c,d=b
    return max(0,min(x+w,u+c)-max(x,u))*max(0,min(y+h,v+d)-max(y,v))


def occlusion_order(recognition, order, representation=None):
    """Only explicit occluded-by hints plus unique ID match and intersection."""
    objects={o['id']:o for o in recognition['objects']};result=list(order);relations=[]
    for behind in order:
        o=objects[behind];hint=o.get('geometry_hint','').lower()
        match=re.search(r'occluded by (?:the )?([^.,;]+)',hint)
        if not match:continue
        phrase=set(re.findall(r'[a-z0-9]+',match.group(1)))
        candidates=[]
        for front in order:
            if front==behind:continue
            f=objects[front];tokens=set(re.findall(r'[a-z0-9]+',front.lower()))
            if f['category'] not in {'image','shape','chart'} or len(tokens)<2:continue
            if tokens.issubset(phrase) and _intersect(o['bbox'],f['bbox'])>0:candidates.append(front)
        if len(candidates)!=1:continue
        front=candidates[0]
        # Do not apply contradictory cycles; preserve original order on them.
        if any(x['behind']==front and x['front']==behind for x in relations):continue
        if result.index(behind)>result.index(front):
            result.remove(behind);result.insert(result.index(front),behind)
        relations.append({'behind':behind,'front':front,'basis':'explicit_occluded_by_hint_unique_id_overlap'})
    for relation in recognition.get('source_occlusion_constraints',[]):
        behind,front=relation['behind'],relation['front']
        if behind not in result or front not in result or behind==front:continue
        if any(x['behind']==front and x['front']==behind for x in relations):continue
        if result.index(behind)>result.index(front):result.remove(behind);result.insert(result.index(front),behind)
        relations.append(dict(relation))
    for asset in (representation or {}).get('assets',{}).values():
        if asset.get('source_basis')!='observed_atomic_visible_photo':continue
        behind=asset['source_label_id']
        for front in dict.fromkeys(x['owner'] for x in asset.get('native_removals',[])):
            if front==behind or front not in result or behind not in result:continue
            if result.index(behind)>result.index(front):
                result.remove(behind);result.insert(result.index(front),behind)
            record={'behind':behind,'front':front,'basis':'source_photo_annotation_removed_native_replacement'}
            if record not in relations:relations.append(record)
    return result,relations


def _raster(image, box, owner, tag, directory, masks=(), outline=None, native_removals=()):
    from PIL import Image,ImageDraw
    x,y,w,h=box;left,top=math.floor(x),math.floor(y);right,bottom=math.ceil(x+w),math.ceil(y+h)
    crop=image.convert('RGBA').crop((left,top,right,bottom));alpha=crop.getchannel('A');draw=ImageDraw.Draw(alpha)
    for a,b,c,d in masks:draw.rectangle((math.floor(a-left),math.floor(b-top),math.ceil(a+c-left)-1,math.ceil(b+d-top)-1),fill=0)
    if outline:
        # Source observed border removed only where a separate native outline
        # will cover it; no hidden photograph pixels are synthesized.
        a,b,c,d,width=outline;draw.rectangle((a-left,b-top,a+c-left,b+d-top),outline=0,width=max(1,math.ceil(width)))
    if native_removals:
        # Remove only observed foreground annotation pixels; never erase wide
        # rectangles/stripes through a photographic base. Background estimates
        # are explicitly limited to the observed ink pixels and reproducible.
        import cv2,numpy as np
        pixels=np.array(crop);mask=np.zeros((crop.height,crop.width),dtype=np.uint8)
        for item in native_removals:
            region=Image.new('L',crop.size,0);rd=ImageDraw.Draw(region)
            if item['kind']=='text':
                a,b,c,d=item['bbox'];rd.rectangle((a-left,b-top,a+c-left,b+d-top),fill=255)
            elif item['kind'] in {'line','polyline'}:
                rd.line([(a-left,b-top) for a,b in item['points']],fill=255,width=max(3,math.ceil(item['width']+10)))
            arr=pixels[:,:,:3].astype('int32');color=item.get('color','#000000');rgb=[int(color.lstrip('#')[k:k+2],16) for k in (0,2,4)]
            if max(rgb)-min(rgb)>40:
                # Scientific annotation red/pink; reject yellow photo pixels.
                observed=((arr-np.array(rgb))**2).sum(2)<100**2
                observed &= (arr[:,:,0]-arr[:,:,1]>12)&(arr[:,:,0]-arr[:,:,2]>15)
            else:observed=arr.max(2)<220
            mask[(np.array(region)>0)&observed]=255
        mask=cv2.dilate(mask,np.ones((3,3),dtype=np.uint8),iterations=1)
        if float((mask>0).mean())>.15:raise ValueError('Annotation removal exceeds atomic photo pixel budget')
        if mask.any():
            restored=cv2.inpaint(pixels[:,:,:3],mask,3,cv2.INPAINT_TELEA);pixels[:,:,:3][mask>0]=restored[mask>0]
        from PIL import Image
        crop=Image.fromarray(pixels)
    crop.putalpha(alpha);directory=Path(directory)/'assets';directory.mkdir(parents=True,exist_ok=True)
    key='asset-'+hashlib.sha256((owner+tag+repr(box)+repr(masks)+repr(outline)+repr(native_removals)).encode()).hexdigest()[:16];p=directory/(key+'.png');crop.save(p)
    return key,{'status':'ready','source_label_id':owner,'source_bbox':list(box),'source_basis':'observed_atomic_visible_photo','source_candidate_id':'recognition:'+owner,'mode':'source_crop','path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'atomic_raster_unit':True,'contains_reconstructable_content':False,'visible_only_masks':[list(b) for b in masks],'native_outline':outline,'native_removals':list(native_removals),'annotation_background_status':'estimated_only_at_observed_ink_pixels' if native_removals else 'unmodified','decomposition_note':'One visible photographic field; occluded pixels transparent; labels/frames are separate native primitives. No hidden content reconstructed.'},[left,top,right-left,bottom-top]


def _image_primitive(pid, asset, box, owner_box):
    x,y,w,h=box;a,b,_,_=owner_box
    return {'id':pid,'kind':'image','style':style({}),'bbox':[x-a,y-b,w,h],'asset_id':asset,'editable':False,'fallback_reason':'source_atomic_photograph_with_native_annotations'}


def stack_rectangles(image, owner_box, front_box):
    """Recover only visible axis-aligned leaves with observed long edges.
    Nominal hidden extent comes from the observed SAM front leaf; all occluded
    source pixels are masked, so there is no claim of hidden photo recovery.
    """
    import cv2,numpy as np
    x,y,w,h=owner_box;fx,fy,fw,fh=front_box
    left=max(0,int(x)-8);top=max(0,int(y)-8);crop=np.array(image.convert('RGB').crop((left,top,math.ceil(x+w+10),math.ceil(y+h+8))))
    lines=cv2.HoughLinesP(cv2.Canny(crop,40,100),1,np.pi/180,threshold=40,minLineLength=int(.65*min(fw,fh)),maxLineGap=12)
    if lines is None:return []
    rects=[]
    # Back leaves extend right: exposed right vertical edge identifies each.
    if abs(fx-x) < abs(x+w-fx-fw):
        candidates=[]
        for [[a,b,c,d]] in lines:
            a+=left;c+=left;b+=top;d+=top
            if abs(a-c)<=2 and a>fx+fw+8 and a<=x+w+8 and abs(d-b)>=.7*fh:
                candidates.append([float(a),float(max(b,d))])
        groups=[]
        for a,b in sorted(candidates):
            if groups and a-groups[-1][-1][0]<=5:groups[-1].append((a,b))
            else:groups.append([(a,b)])
        for group in groups:
            a=max(t[0] for t in group);bottom=max(t[1] for t in group)
            box=[a-fw,bottom-fh,fw,fh]
            if box[1]<fy-3 and box[0]>fx+5:rects.append(box)
    # Back leaves extend left: their exposed horizontal top edges identify them.
    else:
        candidates=[]
        for [[a,b,c,d]] in lines:
            a+=left;c+=left;b+=top;d+=top
            if abs(b-d)<=2 and min(a,c)<fx-8 and b<fy-6 and y-8<=b and .8*fw<=abs(c-a)<=1.15*fw:
                candidates.append((float(b),float(min(a,c)),float(abs(c-a))))
        groups=[]
        for row in sorted(candidates):
            if groups and row[0]-groups[-1][-1][0]<=6:groups[-1].append(row)
            else:groups.append([row])
        for group in groups:
            b,a,length=min(group,key=lambda t:abs(t[2]-fw));rects.append([a,b,fw,fh])
    # Require a real stack, bounded consistent offsets, and source containment.
    rects=[b for b in rects if _intersect(b,owner_box)/(b[2]*b[3])>.9 and abs(b[0]-fx)<.5*fw and abs(b[1]-fy)<.5*fh]
    if not 1<=len(rects)<=4:return []
    return sorted(rects,key=lambda b:abs(b[0]-fx),reverse=True)+[list(front_box)]



def observed_patch_frame(image, predicted):
    """Read a thin colored rectangular annotation near an existing patch box.
    Do not infer a new photo region when no paired source edges exist.
    """
    import cv2,numpy as np
    x,y,w,h=predicted;margin=max(8,round(.2*max(w,h)));left=max(0,int(x)-margin);top=max(0,int(y)-margin)
    arr=np.array(image.convert('RGB').crop((left,top,math.ceil(x+w+margin),math.ceil(y+h+margin)))).astype('int32')
    red=(arr[:,:,0]>150)&(arr[:,:,0]-arr[:,:,1]>65)&(arr[:,:,0]-arr[:,:,2]>50)
    lines=cv2.HoughLinesP(red.astype('uint8')*255,1,np.pi/180,threshold=max(15,int(.3*h)),minLineLength=int(.6*h),maxLineGap=5)
    if lines is None:return None
    vertical=[(int((a+c)/2)+left,min(b,d)+top,max(b,d)+top) for [[a,b,c,d]] in lines if abs(a-c)<3 and abs(b-d)>=.6*h]
    if len(vertical)<2:return None
    lo=min(vertical);hi=max(vertical);a=lo[0];b=min(lo[1],hi[1]);c=hi[0];d=max(lo[2],hi[2])
    if not .8*w<=c-a<=1.2*w or not .8*h<=d-b<=1.2*h:return None
    colors=arr[red];color='#'+''.join(f'{int(z):02X}' for z in np.median(colors,axis=0))
    return [float(a),float(b),float(c-a),float(d-b)],color

def normalize_representation_contracts(recognition, representation, evidence, image_path, directory, config):
    from PIL import Image
    out=deepcopy(representation);objects={o['id']:o for o in recognition['objects']};report={'paths':[],'assets':[],'model_calls':0}
    from .common import write_json,sha256
    directory=Path(directory);input_file=directory/'render-contracts-input.json'
    if not any(c.get('asset_contract_version') for c in representation['components']):write_json(input_file,representation)
    with Image.open(image_path) as original:
        image=original.convert('RGB');canvas=recognition['canvas']['width']*recognition['canvas']['height']
        for component in out['components']:
            o=objects[component['label_id']];hint=o.get('geometry_hint','').lower();primitives=component['primitives']
            for p in primitives:
                if p['kind']!='path':continue
                selected,basis=path_space(p)
                if selected:p['coordinate_space']=selected
                report['paths'].append({'owner':o['id'],'primitive':p['id'],'coordinate_space':selected,'basis':basis})
            if component.get('asset_contract_version')==1:continue
            if o.get('text') or o.get('confidence',0)<.9 or o['bbox'][2]*o['bbox'][3]/canvas>.02 :continue
            conflicts=text_crop_conflicts(o['bbox'],recognition,o['id'],evidence)
            is_stack=o['category']=='image' and 'stack' in hint and any(t in hint for t in ['photo','image patch'])
            if is_stack and not conflicts:
                photos=[p for p in primitives if p['kind']=='image']
                if len(photos)!=1:continue
                old=out['assets'][photos[0]['asset_id']];front=old['source_bbox'];boxes=stack_rectangles(image,o['bbox'],front)
                if not boxes:report['assets'].append({'owner':o['id'],'action':'ambiguous_stack_retained'});continue
                new=[];border='dark border' in hint
                for n,box in enumerate(boxes):
                    masks=boxes[n+1:];outline=[*box,3] if border else None
                    key,asset,bounds=_raster(image,box,o['id'],f'leaf{n}',directory,masks,outline);out['assets'][key]=asset;new.append(_image_primitive(f'photo_leaf_{n}',key,bounds,o['bbox']))
                    if border:
                        a,b,w,h=box;x,y,_,_=o['bbox'];new.append({'id':f'photo_frame_{n}','kind':'rect','style':style({'fill':'none','stroke':'#000000','stroke_width':3}),'bbox':[a-x,b-y,w,h],'editable':True})
                component.update(primitives=new,primitive_order=[p['id'] for p in new],asset_contract_version=1)
                report['assets'].append({'owner':o['id'],'action':'observed_visible_stack_leaves','boxes':boxes,'occluded_pixels':'transparent','leaf_count':len(boxes)});continue
            # Admit a mixed scientific photo only with explicit complex-object
            # source description and independently represented patch outline.
            photographic=any(t in hint for t in ['photograph','photographic','construction vehicle'])
            mixed=any(t in hint for t in ['patch','highlighted square','overlay'])
            overlays=[p for p in primitives if p['kind']=='rect' and p['style'].get('fill')=='none' and p['style'].get('stroke','none')!='none' and p['bbox'][2]*p['bbox'][3]<.3*component['width']*component['height']]
            if o['category']=='shape' and photographic and mixed and len(overlays)==1:
                overlay=deepcopy(overlays[0]);a,b,w,h=overlay['bbox'];x,y,_,_=o['bbox'];line=overlay['style'].get('stroke_width',2)
                removals=[]
                for target in recognition['objects']:
                    intersects=_intersect(o['bbox'],target['bbox'])>0
                    text_owner=target['category'] in {'text','equation'} and bool(target.get('text'))
                    small_silhouette=target['category']=='shape' and 'silhouette' in target.get('geometry_hint','').lower() and target['bbox'][2]*target['bbox'][3]<.3*o['bbox'][2]*o['bbox'][3]
                    if intersects and (text_owner or small_silhouette):
                        removals.append({'owner':target['id'],'kind':'text','bbox':target['bbox']})
                for auxiliary in representation['components']:
                    other=objects[auxiliary['label_id']]
                    if other['id']==o['id'] or other['category'] not in {'arrow','chart'} and 'rays' not in other.get('geometry_hint','').lower():continue
                    if not _intersect(o['bbox'],other['bbox']):continue
                    ax,ay,_,_=other['bbox']
                    for prim in auxiliary['primitives']:
                        if prim['kind'] in {'line','polyline'}:
                            removals.append({'owner':other['id'],'kind':prim['kind'],'points':[[ax+u,ay+v] for u,v in prim['points']],'width':prim['style'].get('stroke_width',1),'color':prim['style'].get('stroke','#000000')})
                observed=observed_patch_frame(image,[x+a,y+b,w,h])
                if observed is None:
                    report['assets'].append({'owner':o['id'],'action':'mixed_patch_frame_ambiguous_retained'});continue
                patch_box,stroke_color=observed;px,py,pw,ph=patch_box
                overlay['bbox']=[px-x,py-y,pw,ph];overlay['style'].update(stroke=stroke_color,stroke_width=1.2)
                key,asset,bounds=_raster(image,o['bbox'],o['id'],'photo_base',directory,masks=[patch_box],native_removals=removals);out['assets'][key]=asset
                base=_image_primitive('photographic_base',key,bounds,o['bbox'])
                key,asset,bounds=_raster(image,patch_box,o['id'],'observed_patch_photo',directory,outline=[*patch_box,1],native_removals=removals);out['assets'][key]=asset
                new=[base,_image_primitive('patch_photo',key,bounds,o['bbox']),overlay];component.update(primitives=new,primitive_order=[p['id'] for p in new],asset_contract_version=1)
                report['assets'].append({'owner':o['id'],'action':'mixed_photo_with_separate_native_overlay','native_overlay_ids':[overlay['id']],'source_bbox':o['bbox']})
    for asset in out['assets'].values():
        if asset.get('source_basis')=='observed_atomic_visible_photo':asset['contract_input_sha256']=sha256(input_file)
    used={p['asset_id'] for c in out['components'] for p in c['primitives'] if p['kind']=='image'};out['assets']={k:v for k,v in out['assets'].items() if k in used}
    return out,report


def verify_atomic_asset(asset, recognition, layout, scene, image, config):
    """Independent replay from the frozen pre-contract representation + source.
    Accept only reproducible atomic crops and visible native replacements.
    """
    import tempfile
    from PIL import Image
    from .common import read_json,sha256
    try:
        path=Path(asset['path']);root=next(p for p in path.parents if (p/'input-normalized.png').is_file() and (p/'observations/evidence-ir.json').is_file() and sha256(p/'input-normalized.png')==sha256(image))
        source=root/'representation/render-contracts-input.json'
        if sha256(source)!=asset.get('contract_input_sha256') or sha256(path)!=asset['sha256']:return False
        raw=read_json(source);evidence=read_json(root/'observations/evidence-ir.json')
        with tempfile.TemporaryDirectory(prefix='decomposer-atomic-replay-') as temp:
            replay,_=normalize_representation_contracts(recognition,raw,evidence,image,Path(temp),config)
            matches=[x for x in replay['assets'].values() if x.get('source_basis')=='observed_atomic_visible_photo' and x['source_label_id']==asset['source_label_id'] and x['source_bbox']==asset['source_bbox']]
            if len(matches)!=1:return False
            expected=matches[0]
            for key in ['sha256','source_candidate_id','source_basis','visible_only_masks','native_outline','native_removals']:
                if expected.get(key)!=asset.get(key):return False
        visible={p['label_id'] for p in layout['placements'] if p['visible']}
        native={e['source_label_id'] for e in scene['elements'] if e.get('editable')}
        replacements={x['owner'] for x in asset.get('native_removals',[])}
        if not replacements<=visible&native:return False
        image_indices=[i for i,e in enumerate(scene['elements']) if e.get('asset_id') and scene['assets'].get(e['asset_id'],{}).get('source_label_id')==asset['source_label_id']]
        for owner in replacements:
            if image_indices and min(i for i,e in enumerate(scene['elements']) if e['source_label_id']==owner)<=max(image_indices):return False
        if asset.get('native_outline'):
            owner=asset['source_label_id']
            if not any(e['source_label_id']==owner and e['kind']=='rect' and e.get('editable') for e in scene['elements']):return False
        return True
    except (OSError,ValueError,TypeError,KeyError,StopIteration):return False
