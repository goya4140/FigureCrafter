"""Prototype native build recipes. Canonical scene and semantic IDs are unchanged."""
from copy import deepcopy
from .schemas import object_schema,STYLE_SCHEMA,NUMBER_ARRAY,POINT_ARRAY
from .common import style

def op(kind,fields):
 props={'id':{'type':'string'},'kind':{'enum':[kind]},'style_id':{'type':'string'},**fields}
 return object_schema(props,tuple(props))
RUN=object_schema({'text':{'type':'string'},'source_text':{'type':'string'},'style_id':{'type':'string'},'script':{'enum':['normal','subscript','superscript']}},('text','source_text','style_id','script'))
OP={'anyOf':[
 *[op(k,{'bbox':NUMBER_ARRAY}) for k in ['rect','round_rect','ellipse']],
 *[op(k,{'points':POINT_ARRAY,'arrow_start':{'type':'boolean'},'arrow_end':{'type':'boolean'}}) for k in ['line','polyline']],
 op('polygon',{'points':POINT_ARRAY}),op('path',{'bbox':NUMBER_ARRAY,'path':{'type':'string'},'coordinate_space':{'enum':['primitive','component']},'arrow_start':{'type':'boolean'},'arrow_end':{'type':'boolean'}}),
 op('text',{'bbox':NUMBER_ARRAY,'text':{'type':'string'},'runs':{'type':'array','items':RUN}})]}
def plan(builder,fields=None):
 props={'label_id':{'type':'string'},'builder':{'enum':[builder]},'style_id':{'type':'string'},**(fields or {})};return object_schema(props,tuple(props))
PLAN={'anyOf':[
 plan('text'),*[plan(k) for k in ['rect','round_rect','ellipse']],
 plan('arrow',{'arrow_start':{'type':'boolean'},'arrow_end':{'type':'boolean'}}),
 plan('native',{'ops':{'type':'array','items':OP}})]}
SCHEMA=object_schema({'styles':{'type':'array','items':object_schema({'id':{'type':'string'},'value':STYLE_SCHEMA},('id','value'))},'plans':{'type':'array','items':PLAN}},('styles','plans'))

def approved_images(recognition,decisions):
 by={x['label_id']:x for x in decisions['decisions']}
 return {o['id'] for o in recognition['objects'] if o['category']=='image' and not o.get('text') and by.get(o['id'],{}).get('decision')=='localized_raster'}

def project(recognition,decisions):
 fixed=approved_images(recognition,decisions);objects=[]
 for o in recognition['objects']:
  if o['id'] in fixed:continue
  st={k:v for k,v in o['style_hint'].items() if v is not None and v!='none'}
  objects.append({'id':o['id'],'type':o['category'],'box':o['bbox'],'text':o['text'],'hint':o['geometry_hint'],'points':o.get('points',[]),'style_hint':st})
 return {'canvas':recognition['canvas'],'objects':objects,'prebuilt_image_ids':sorted(fixed),'coordinate_system':'Component-local xywh; arrow builder uses global Recognition points automatically.'}

def graph_members(owner,recognition):
 """Admit nonpainting entity-graph containers only with typed child coverage."""
 import re
 if owner.get('semantic_level')!='container' or str(owner.get('text') or '').strip():return []
 document_group = owner['category']=='shape' and 'separate document crops' in owner.get('geometry_hint','').lower() and all(owner.get('style_hint',{}).get(k,'none')=='none' for k in ['fill','stroke'])
 if not document_group and (owner['category']!='chart' or 'graph' not in owner.get('geometry_hint','').lower()):return []
 x,y,w,h=owner['bbox'];children=[]
 for obj in recognition['objects']:
  if obj['id']==owner['id'] or obj.get('category')=='chart':continue
  a,b,c,d=obj['bbox'];intersection=max(0,min(x+w,a+c)-max(x,a))*max(0,min(y+h,b+d)-max(y,b))
  if intersection/max(1,c*d)>=.95:children.append(obj)
 if document_group:
  leaves=[o for o in children if o['category']=='shape' and 'document crop' in o.get('geometry_hint','').lower()]
  area=sum(o['bbox'][2]*o['bbox'][3] for o in leaves)
  return [o['id'] for o in children] if len(leaves)>=2 and .3<=area/max(1,w*h)<=1.05 else []
 nodes=sum(o['category']=='shape' and bool(re.search(r'oval|ellipse|node',o.get('geometry_hint','').lower())) for o in children)
 edges=sum(o['category']=='arrow' or o['category']=='shape' and bool(re.search(r'arrow|edge|relationship',o.get('geometry_hint','').lower())) for o in children)
 labels=sum(o['category']=='text' and bool(o['text']) for o in children)
 return [o['id'] for o in children] if nodes>=4 and edges>=3 and labels>=4 else []

class TextContractError(ValueError):
 def __init__(self, owner, detail):
  self.label_ids=[owner]
  super().__init__(f"Text contract for {owner}: {detail}")

def has_math_markup(text):
 import re
 return bool(re.search(r"[_^](?:\{[^{}]+\}|[A-Za-z0-9])|\\[A-Za-z]+",text))

def reconcile_text_claims(primitives, owner):
 """Repair only a provable source partition; never guess missing math tokens."""
 if owner['category'] not in {'text','equation'} or not owner.get('text'):return []
 texts=[p for p in primitives if p['kind']=='text'];claims=[]
 for p in texts:
  runs=p.get('runs') or []
  claim=''.join(str(r.get('source_text',r.get('text',''))) for r in runs) if runs else p.get('text','')
  claims.append(claim)
 if ''.join(claims)!=owner['text']:
  raise TextContractError(owner['id'],'run source partition must exactly preserve owner text')
 repairs=[]
 for p,claim in zip(texts,claims):
  if p.get('runs') and p.get('text','')!=claim:
   if p.get('text','') not in {'',owner['text']}:
    raise TextContractError(owner['id'],'conflicting primitive and run source claims')
   repairs.append({'primitive_id':p['id'],'previous_text':p.get('text',''),'text':claim})
   p['text']=claim
  if owner['category']=='equation' and has_math_markup(p.get('text','')) and not p.get('runs'):
   raise TextContractError(owner['id'],'math markup requires native styled runs')
 return repairs

def decode(data,recognition,decisions):
 objects={o['id']:o for o in recognition['objects']};fixed=approved_images(recognition,decisions);expected=set(objects)-fixed
 styles={}
 for row in data['styles']:
  if row['id'] in styles:raise ValueError('Duplicate style ID')
  styles[row['id']]={k:v for k,v in row['value'].items() if v is not None}
 plans=data['plans'];ids=[p['label_id'] for p in plans]
 if len(ids)!=len(set(ids)) or set(ids)!=expected:raise ValueError('Missing, duplicate, or foreign recipe owners')
 def getstyle(sid,text=False,parent=None):
  if sid not in styles:raise ValueError('Unknown shared style')
  st=style({**(parent or {}),**styles[sid]},text=text)
  if text and st['color']=='none':raise ValueError('Invisible text style is invalid')
  return st
 def primitive(p):
  v={k:deepcopy(x) for k,x in p.items() if k not in ['style_id','runs']};v['style']=getstyle(p['style_id'],p['kind']=='text')
  if p.get('runs'):
   v['runs']=[{**{k:x for k,x in r.items() if k!='style_id'},'style':getstyle(r['style_id'],True,v['style'])} for r in p['runs']]
  return v
 by={};text_repairs=[]
 for p in plans:
  o=objects[p['label_id']];x,y,w,h=o['bbox'];kind=p['builder'];st=getstyle(p['style_id'],kind=='text')
  if kind=='text':
   if o['category'] not in {'text','equation'} or not o['text']:raise ValueError('Text builder needs a text/equation owner')
   if o['category']=='equation' and has_math_markup(o['text']):raise TextContractError(o['id'],'math markup requires explicit native text runs')
   primitives=[{'id':'text','kind':'text','bbox':[0,0,w,h],'text':o['text'],'style':st}]
  elif kind=='arrow':
   if o['category'] not in {'arrow','shape'} or str(o.get('text') or '').strip() or len(o.get('points',[]))<2:raise ValueError('Line/arrow builder needs an empty-text native owner and measured Recognition points')
   primitives=[{'id':'arrow','kind':'polyline','points':[[a-x,b-y] for a,b in o['points']],'arrow_start':p['arrow_start'],'arrow_end':p['arrow_end'],'style':st}]
  elif kind in ['rect','round_rect','ellipse']:
   if o.get('text'):raise ValueError('Primitive shape builder cannot drop owner text')
   primitives=[{'id':'shape','kind':kind,'bbox':[0,0,w,h],'style':st}]
  elif kind=='native':
   primitives=[primitive(v) for v in p['ops']]
   if not primitives:
    members=graph_members(o,recognition)
    if not members:raise ValueError('Empty owner lacks verified child graph coverage')
    by[o['id']]={'label_id':o['id'],'category':o['category'],'width':w,'height':h,'primitives':[],'primitive_order':[],'group_only':True,'members':members}
    continue
  else:raise ValueError('Unknown builder')
  changes=reconcile_text_claims(primitives,o)
  if changes:text_repairs.append({'label_id':o['id'],'action':'verified_owner_run_source_partition','changes':changes})
  by[o['id']]={'label_id':o['id'],'category':o['category'],'width':w,'height':h,'primitives':primitives,'primitive_order':[v['id'] for v in primitives]}
 for sid in fixed:
  o=objects[sid];w,h=o['bbox'][2:];by[sid]={'label_id':sid,'category':'image','width':w,'height':h,'primitives':[{'id':'content','kind':'image','bbox':[0,0,w,h],'style':style({})}]}
 return {'components':[by[o['id']] for o in recognition['objects']],'text_contract_normalizations':text_repairs}

PROMPT='''Reconstruct the supplied reference as editable native PowerPoint objects using short build recipes. Return exactly one plan for each objects[].id; omit prebuilt_image_ids, which the backend injects from approved source crops. Reuse shared styles across plans. This is raster reconstruction, so preserve reference positions, colors, font families, sizes in source pixels, text weights, borders, arrows, and internal structure. Do not invent details or use screenshots for native diagrams.
Builders: text copies an independent text owner's exact string and box automatically; rect/round_rect/ellipse constructs one plain shape in its owner's box, only if no internal structures or text would be lost. arrow copies Recognition points and lets you set arrowheads/style; use native instead when that route needs correction. native emits explicit component-local ops for complex shapes, equations, charts, rich text and compound diagrams. Use native whenever a basic builder would oversimplify the reference. Native text ops must preserve exact source-owner text for text/equation owners; represent math with native text/runs and Unicode where possible. Shared font size is in source image pixels. Specify an opaque color for visible text and stroke none explicitly for unoutlined shapes. Do not reuse a body shape as a photo substitute; follow the supplied owner's category, and do not add image ops. No full-component width/height/order output is needed; the backend supplies Recognition dimensions and preserves plan op order.''' 

PROMPT += ''' Each native text op's text must equal concatenation of its runs[].source_text. Across ops, those source fragments must concatenate exactly to the owner text. A split superscript op owns its ^{...} source fragment, not an empty text string; the main op excludes that fragment. Retain every combining hat and mathematical identifier. Do not render source markup literally.'''
