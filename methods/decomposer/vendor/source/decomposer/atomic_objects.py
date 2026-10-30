"""Conservative evidence-backed normalization, no inferred strings or geometry.
Keep prose/code blocks; split spatially separated, exactly OCR-matched labels.
"""
from copy import deepcopy
import re

def is_photographic_leaf(obj):
    return bool(re.search(r"\bphoto(?:graph(?:ic)?)?\b",str(obj.get("geometry_hint", "")),re.I)) and not str(obj.get("text") or "").strip() and float(obj.get("confidence",0)) >= .9


def text_crop_conflicts(bbox, recognition, owner_id, evidence=None):
    x,y,w,h=bbox;found=[]
    cs={c['id']:c for c in (evidence or {}).get('candidates',[]) if c.get('source')=='ocr'}
    for obj in recognition['objects']:
        if obj['id']==owner_id or obj['category'] not in {'text','equation'} or not str(obj.get('text') or '').strip():continue
        support=[cs[i] for i in obj.get('candidate_ids',[]) if i in cs]
        boxes=[(c['bbox'],c['id']) for c in support] or [(obj['bbox'],None)]
        for box,cid in boxes:
            a,b,c,d=box;iw=max(0,min(x+w,a+c)-max(x,a));ih=max(0,min(y+h,b+d)-max(y,b))
            # Ignore anti-aliasing/tight-box border touches; use line-level
            # evidence rather than the whitespace inside multi-line frames.
            if iw>2 and ih>2 and iw/max(c,1)>.1 and ih/max(d,1)>.5:
                found.append({'owner_id':obj['id'],'text':obj['text'],'evidence_id':cid,'evidence_bbox':box,'intersection_bbox':[max(x,a),max(y,b),iw,ih],'intersection_area':iw*ih})
    return found

def normalize(recognition,evidence):
    out=deepcopy(recognition);cs={c['id']:c for c in evidence.get('candidates',[])};result=[];changes=[];skips=[]
    referenced={v for o in out['objects'] for v in (o.get('connects') or {}).values() if isinstance(v,str)}
    for obj in out['objects']:
        # An already preferred-native composite conflicting with separate labels
        # must be described by geometry, not by an image crop.
        conflicts=text_crop_conflicts(obj['bbox'],out,obj['id'],evidence) if obj['category']=='image' else []
        if conflicts and obj.get('representation_preference')=='native' and not is_photographic_leaf(obj):
            obj['category']='shape';obj['semantic_level']='container'
            changes.append({'action':'native_image_conflict_to_geometry','id':obj['id'],'native_text_conflicts':conflicts})
        lines=str(obj.get('text') or '').split('\n')
        mono=any(s in str(obj.get('style_hint',{}).get('font_family','')).lower() for s in ['courier','mono','consolas'])
        if obj['category']!='text' or len(lines)<2 or any(not s.strip() for s in lines) or mono or obj['id'] in referenced:
            result.append(obj);continue
        candidates=[cs[i] for i in obj.get('candidate_ids',[]) if i in cs and cs[i].get('source')=='ocr']
        if len(candidates)!=len(lines):result.append(obj);continue
        ordered=sorted(candidates,key=lambda c:(c['bbox'][1],c['bbox'][0]))
        if [c.get('text') for c in ordered]!=lines:result.append(obj);continue
        # Large empty gaps distinguish label lists from tightly led paragraphs.
        if any(b['bbox'][1]-(a['bbox'][1]+a['bbox'][3]) < .4*min(a['bbox'][3],b['bbox'][3]) for a,b in zip(ordered,ordered[1:])):
            result.append(obj);continue
        new_ids=[]
        for n,(line,c) in enumerate(zip(lines,ordered),1):
            child=deepcopy(obj);child.update(id=f"{obj['id']}__line{n}",text=line,bbox=list(c['bbox']),candidate_ids=[c['id']])
            child['style_hint'].pop('line_spacing',None);child['semantic_level']='leaf';result.append(child);new_ids.append(child['id'])
        changes.append({'action':'split_independent_label_list','parent_id':obj['id'],'child_ids':new_ids,'source_candidate_ids':[c['id'] for c in ordered]})
    out['objects']=result
    assert len({o['id'] for o in result})==len(result)
    return out,{'changes':changes,'input_objects':len(recognition['objects']),'output_objects':len(result)}
