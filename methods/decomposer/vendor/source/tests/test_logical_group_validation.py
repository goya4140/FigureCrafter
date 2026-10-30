from copy import deepcopy
import pytest
from decomposer.validation import _covered_logical_groups

def fixture():
 rec={'objects':[{'id':'g','semantic_level':'container','text':''},{'id':'a'},{'id':'b'}]}
 rep={'components':[{'label_id':'g','group_only':True,'members':['a','b'],'primitives':[]}]}
 lay={'placements':[{'label_id':i,'visible':True} for i in ['g','a','b']]}
 scene={'elements':[{'source_label_id':i} for i in ['a','b']]}
 return rec,rep,lay,scene

def test_complete_native_member_coverage_accepts_nonpainting_group():
 args=fixture();before=deepcopy(args);assert _covered_logical_groups(*args)=={'g'};assert args==before

@pytest.mark.parametrize('defect',['missing_member','hidden_member','own_text','own_primitive','not_container','empty_members','duplicate_member','self_cycle'])
def test_group_coverage_does_not_mask_physical_defects(defect):
 rec,rep,lay,scene=fixture();c=rep['components'][0]
 if defect=='missing_member':scene['elements'].pop()
 if defect=='hidden_member':lay['placements'][-1]['visible']=False
 if defect=='own_text':rec['objects'][0]['text']='visible heading'
 if defect=='own_primitive':c['primitives']=[{'id':'outline'}]
 if defect=='not_container':rec['objects'][0]['semantic_level']='atomic'
 if defect=='empty_members':c['members']=[]
 if defect=='duplicate_member':c['members']=['a','a']
 if defect=='self_cycle':c['members']=['g']
 assert _covered_logical_groups(rec,rep,lay,scene)==set()

def test_unresolved_mutual_group_cycle_is_not_covered():
 rec,rep,lay,scene=fixture();rec['objects'].append({'id':'h','semantic_level':'container','text':''});rep['components'][0]['members']=['h'];rep['components'].append({'label_id':'h','group_only':True,'members':['g'],'primitives':[]});lay['placements'].append({'label_id':'h','visible':True});assert _covered_logical_groups(rec,rep,lay,scene)==set()
