"""Future scripts must not change a current-only component observation."""
from pathlib import Path
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'src')]
from tools.annual_dynamic_20261104.bridge import current_cause_ledger


def test_future_branch_event_does_not_disclose_itself_through_current_field_set():
    hardware={'EC_1':1.,'TOWER':1.,'AUX_CHW_PUMP':1.}
    future=[dict(time_minute=60.,availability={'EC_1_PRIMARY_PUMP':0.}),
            dict(time_minute=120.,availability={'EC_1_PRIMARY_PUMP':1.})]
    baseline=current_cause_ledger([],0.,hardware)
    assert current_cause_ledger(future,0.,hardware)==baseline
    assert baseline['raw_component_availability']['EC_1_PRIMARY_PUMP']==1.
    actual=current_cause_ledger(future,60.,dict(hardware,EC_1=0.))
    assert actual['raw_component_availability']['EC_1']==1.
    assert actual['raw_component_availability']['EC_1_PRIMARY_PUMP']==0.
    assert actual['active_declared_causes']==['EC_1_PRIMARY_PUMP']
