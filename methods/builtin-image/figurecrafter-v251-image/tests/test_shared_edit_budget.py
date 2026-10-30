import sys,tempfile,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from edit_budget import reserve

def test_two_method_branches_share_budget_and_failures_do_not_reset():
 with tempfile.TemporaryDirectory() as folder:
  p=Path(folder)/'budget.json'
  assert reserve(p,'Image','001','a')==1
  assert reserve(p,'Hybrid','001','b')==2
  assert reserve(p,'Image','002','c')==3
  with pytest.raises(ValueError,match='exhausted'):reserve(p,'Hybrid','002','d')
  assert len(json.loads(p.read_text())['reservations'])==3

def test_changed_shared_budget_cannot_raise_limit():
 with tempfile.TemporaryDirectory() as folder:
  p=Path(folder)/'budget.json';reserve(p,'Image','001','a')
  with pytest.raises(ValueError,match='changed'):reserve(p,'Hybrid','001','b',limit=4)
