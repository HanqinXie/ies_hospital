"""Regression for actual D01 min-off/powered-start clock inconsistency.

Observed at minute90, pending started105, required powered work10min.
The pending ready clock must not precede the legal physical start.
"""
import copy
from types import SimpleNamespace
import pytest
import sys
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'src')]

@pytest.mark.parametrize('minute,progress,expected',[(90.,0.,115.),(105.,0.,115.),(112.,7.,115.),(112.,3.,119.)])
def test_powered_start_waits_for_actual_minimum_off_eligibility(monkeypatch,minute,progress,expected):
    from tools.model_closure_v22 import environment as module
    seen=[]
    def inherited(self,dispatch,availability,normal_ramp=False):
        seen.append(copy.deepcopy(self.gt_start_pending))
        return copy.deepcopy(dispatch)
    monkeypatch.setattr(module.legacy.EngineeringCore,'_clamp',inherited)
    core=object.__new__(module.EngineeringCore)
    core.env=SimpleNamespace(execution_contract={'gt_start_delay_minutes':10.},config={'gt_ramp_kW_per_hour':900.})
    core.time=minute
    core.gt_start_pending={0:dict(started=105.,ready=115.,requested=126.83648,start_event_recorded=False)}
    core._m22_gt_progress={0:progress}
    before=copy.deepcopy(core.gt_start_pending)
    result=core._clamp({'gt_electric_kW':[126.83648,0.,0.]},{},normal_ramp=True)
    assert seen[0][0]['ready']==expected
    assert core.gt_start_pending[0]['ready']==expected
    assert core.gt_start_pending[0]['ready']>=core.gt_start_pending[0]['started']
    assert core.gt_start_pending[0]['started']==before[0]['started']
    assert result['gt_electric_kW'][0]==0. # no actual powered work credited by a projected clock
    assert core._m22_gt_progress=={0:progress}
    assert core.time==minute
