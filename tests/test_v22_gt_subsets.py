"""A failed all-unit floor candidate is not proof every held subset fails."""
import copy
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_essential_execution import small_core
from tools.engineering_model.execution import GTClockLedger

def test_failed_all_GT_floor_keeps_a_feasible_held_subset():
    c,loads,f,row=small_core(gt=130.,load=350.,budget=1.)
    c.env.closure_config={};c.executed['gt_electric_kW']=[130.,130.,130.];c.state.gt_prev[:]=130.
    c.ramp_anchor=np.array([130.,130.,130.]);c.gt_clocks=GTClockLedger([130.,130.,130.])
    for i in (1,2,3):f[f'GT_{i}']=1.
    c.data_at=lambda minute:(1,row,loads.copy(),dict(f))
    before=copy.deepcopy(c.executed)
    assert c._correct_gt_floor(loads,f,row)
    assert sum(v>0. for v in c.executed['gt_electric_kW'])==2
    assert sum(c.executed['gt_electric_kW'])<loads[:,0].sum()
    assert c.executed['boiler_steam_lbph']==before['boiler_steam_lbph']
    assert not c.failed

def test_no_sink_still_requires_GT_isolation():
    c,loads,f,row=small_core(gt=324.,load=200.,budget=1.)
    c.env.closure_config={};c._surplus_interlock(loads,f,row)
    assert c.executed['gt_electric_kW']==[0.,0.,0.]
    assert 'GT_1' in c.interlock_latched
