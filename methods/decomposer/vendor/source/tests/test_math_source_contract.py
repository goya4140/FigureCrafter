from copy import deepcopy
import pytest
from decomposer.representation_recipes import reconcile_text_claims, TextContractError, has_math_markup
from decomposer.representation import _primitive


def test_split_superscript_reassigns_source_without_losing_tokens_or_geometry():
    owner={'id':'loss','category':'equation','text':'L_rate^{ME}'}
    ops=[{'id':'base','kind':'text','text':'L_rate^{ME}','bbox':[0,0,80,40],
          'runs':[{'text':'L','source_text':'L'},{'text':'rate','source_text':'_rate'}]},
         {'id':'sup','kind':'text','text':'','bbox':[30,0,30,20],
          'runs':[{'text':'ME','source_text':'^{ME}'}]}]
    original=deepcopy(ops)
    repairs=reconcile_text_claims(ops,owner)
    assert len(repairs)==2
    assert ''.join(x['text'] for x in ops)==owner['text']
    assert [x['text'] for x in ops]==['L_rate','^{ME}']
    assert [x['bbox'] for x in ops]==[x['bbox'] for x in original]
    assert [x['runs'] for x in ops]==[x['runs'] for x in original]


@pytest.mark.parametrize('claim',['L_rate','L_rate^{MC}','L_rate^{ME}^{ME}'])
def test_missing_changed_and_duplicate_tokens_require_owner_repair(claim):
    with pytest.raises(TextContractError) as exc:
        reconcile_text_claims([{'kind':'text','id':'x','text':'L_rate^{ME}',
          'runs':[{'text':'X','source_text':claim}]}],
          {'id':'owner','category':'equation','text':'L_rate^{ME}'})
    assert exc.value.label_ids==['owner']


def test_invalid_runs_never_silently_fall_back_to_literal_source():
    with pytest.raises(ValueError,match='refusing literal fallback'):
        _primitive({'id':'x','kind':'text','text':'L^{ME}','bbox':[0,0,80,40],
          'runs':[{'text':'L','source_text':'L','style':{}}]},1,(80,40),set())


def test_plain_text_underscores_stay_literal_but_equations_require_runs():
    text={'id':'filename','category':'text','text':'model_name'}
    ops=[{'id':'x','kind':'text','text':'model_name'}]
    assert reconcile_text_claims(ops,text)==[]
    with pytest.raises(TextContractError):
        reconcile_text_claims(ops,{**text,'category':'equation'})
    assert has_math_markup('x_t') and has_math_markup('L^{ME}')
    assert not has_math_markup('Q̂')
