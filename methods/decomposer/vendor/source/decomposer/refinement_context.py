"""Bound model context to selectable native owners; full artifacts stay on disk."""
from copy import deepcopy

def bounded(payload):
 result=deepcopy(payload);components={c['label_id']:c for c in payload['representation']['components']};objects={o['id']:o for o in payload['recognition']['objects']};chosen=[];candidates=[]
 for candidate in payload['candidates'][:3]:
  ids=[]
  for owner in candidate['label_ids']:
   c=components.get(owner)
   if not c:continue
   members=c.get('members',[]) if c.get('group_only') else [owner]
   ids.extend(x for x in members if x in components and not components[x].get('group_only'))
  ids=list(dict.fromkeys(ids));ids.sort(key=lambda x:(objects[x].get('category')=='image',len(components[x]['primitives'])>12,objects[x]['bbox'][2]*objects[x]['bbox'][3]))
  ids=ids[:24];chosen.extend(ids);candidates.append({**candidate,'label_ids':ids})
 chosen=set(chosen);result['candidates']=candidates;result['recognition']={**payload['recognition'],'objects':[o for o in payload['recognition']['objects'] if o['id'] in chosen]};result['representation']={**payload['representation'],'components':[c for c in components.values() if c['label_id'] in chosen]};result['representation']['assets']={k:{z:v for z,v in a.items() if z not in ['path']} for k,a in payload['representation'].get('assets',{}).items()};result['layout']={**payload['layout'],'placements':[p for p in payload['layout']['placements'] if p['label_id'] in chosen],'component_order':[x for x in payload['layout']['component_order'] if x in chosen]};prov=payload['provenance'];result['provenance']={'schema_version':prov['schema_version'],'labels':{k:v for k,v in prov['labels'].items() if k in chosen}};result['scope_policy']={'selectable_owner_ids':sorted(chosen),'logical_groups':'Nonpainting groups expand to explicit child IDs. Only listed leaf owners may be patched.','omitted_owners':'Read-only and unavailable for patch; do not invent IDs or global layout changes.'};return result
