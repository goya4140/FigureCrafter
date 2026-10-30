"""Conservative cross-owner claims for text embedded inside native composites."""
from copy import deepcopy
import re

def normalize_text_ownership(recognition, representation):
    rep = deepcopy(representation)
    objects = {o['id']:o for o in recognition['objects']}
    def normalized(s): return re.sub(r'\s+', ' ', str(s)).strip()
    def intersection(a,b):
        x,y,w,h=a;u,v,c,d=b
        return max(0,min(x+w,u+c)-max(x,u))*max(0,min(y+h,v+d)-max(y,v))
    leaves=[o for o in objects.values() if o['category'] in {'text','equation'} and normalized(o.get('text',''))]
    # The independent owner must itself have exactly one full, visible text claim.
    valid=set()
    for c in rep['components']:
        o=objects[c['label_id']]
        if o not in leaves: continue
        texts=[p for p in c['primitives'] if p['kind']=='text' and p.get('style',{}).get('color','none')!='none']
        if len(texts)==1 and normalized(texts[0].get('text',''))==normalized(o['text']): valid.add(o['id'])
    removed=[];ambiguous=[]
    for c in rep['components']:
        owner=objects[c['label_id']]
        if owner['category'] not in {'shape','chart'}:continue
        ox,oy,ow,oh=owner['bbox'];drop=set()
        for p in c['primitives']:
            if p['kind']!='text' or not normalized(p.get('text','')):continue
            x,y,w,h=p['bbox'];box=[ox+x*ow/c['width'],oy+y*oh/c['height'],w*ow/c['width'],h*oh/c['height']]
            matches=[]
            for leaf in leaves:
                if leaf['id'] not in valid or normalized(leaf['text'])!=normalized(p['text']):continue
                b=leaf['bbox'];area=max(1,b[2]*b[3]);inside=intersection(owner['bbox'],b)/area
                overlap=intersection(box,b)/max(1,min(box[2]*box[3],area))
                if inside>=.9 and overlap>=.5:matches.append(leaf)
            if len(matches)==1:
                drop.add(p['id']);removed.append({'composite_owner':owner['id'],'primitive_id':p['id'],'text':p['text'],'independent_owner':matches[0]['id'],'embedded_source_bbox':box,'independent_source_bbox':matches[0]['bbox']})
            elif len(matches)>1:ambiguous.append({'owner':owner['id'],'primitive':p['id'],'candidates':[x['id'] for x in matches]})
        if drop and len(drop)<len(c['primitives']):
            c['primitives']=[p for p in c['primitives'] if p['id'] not in drop];c['primitive_order']=[i for i in c['primitive_order'] if i not in drop]
        elif drop:
            # Avoid introducing an invalid empty component; fail open and report.
            removed=[r for r in removed if r['composite_owner']!=owner['id']]
            ambiguous.append({'owner':owner['id'],'reason':'would_empty_component'})
    return rep,{'removed_count':len(removed),'removed':removed,'ambiguous_retained':ambiguous,'policy':'Same source text + spatial overlap + containment + unique independently visible owner. Different locations and ambiguous matches retained.'}
