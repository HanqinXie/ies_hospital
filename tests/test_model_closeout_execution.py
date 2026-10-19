"""M19 finite grid return, without crediting pending GT or closed GRID."""
import importlib
import sys
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.engineering_model.environment import isolated
from test_execution_recovery_core import small_core as inherited_fixture

def implementation():
    try:return importlib.import_module('tools.model_closeout.execution')
    except ModuleNotFoundError:return importlib.import_module('tools.execution_recovery.execution')

def core(**kwargs):
    return isolated(inherited_fixture,dict(inherited_fixture.__globals__,implementation=implementation))(**kwargs)

def supported_island():
    loads=np.zeros((4,3));loads[0,0]=50.;loads[1,0]=60.
    c,loads,f,row=core(gt=0.,loads=loads)
    f['GRID']=0.;c.boundary();c.time=15.;c.boundary()
    c.segments.append(dict(loads_kW=loads.tolist(),unserved_kW=np.zeros((4,3)).tolist()))
    f['GRID']=1.;c.time=16.;c.boundary()
    return c,loads,f

def test_working_diesel_waits_for_real_gt_open_bridge():
    c,_,_=supported_island()
    assert c.grid_bus_state=='ISLAND'
    assert c.executed['diesel_kW']==500. and c.executed['grid_import_kW']==0.
    assert any(e['kind']=='grid_open_bridge_wait' for e in c.events)

def test_bridge_wait_is_finite_and_logs_unbridged_fallback():
    c,_,_=supported_island()
    c.time=46.;c.boundary()
    assert c.grid_bus_state=='RETURN_PREP'
    c.time=46.25;c.boundary()
    assert c.grid_bus_state=='TRANSFER_OPEN'
    assert c.executed['grid_import_kW']==c.executed['diesel_kW']==0.
    c.time=46.5;c.boundary()
    assert c.grid_bus_state=='GRID'
    assert any(e['kind']=='grid_open_bridge_fallback' and e['reason']=='bounded_wait_expired' for e in c.events)

def test_diesel_failure_does_not_deadlock_grid_recovery():
    c,_,f=supported_island()
    f['DIESEL']=0.;c.time=17.;c.boundary()
    assert c.grid_bus_state=='RETURN_PREP'
    c.time=17.25;c.boundary()
    assert c.grid_bus_state=='TRANSFER_OPEN'
    assert c.executed['diesel_kW']==c.executed['grid_import_kW']==0.

def test_invalid_bridge_clock_rejected():
    assert hasattr(implementation(),'validate_closeout')
    with pytest.raises(ValueError):implementation().validate_closeout({'grid_bridge_wait_minutes':float('inf')})

@pytest.mark.parametrize('test_name',[
    'test_grid_permission_survives_failure_and_activates_only_after_open_transfer',
    'test_starting_diesel_keeps_arrived_permission_without_early_energy',
    'test_new_zero_permission_revokes_and_is_not_resurrected_by_recovery',
    'test_faulted_grid_saved_permission_cannot_create_local_down_headroom',
    'test_real_disconnected_electric_contacts_restore_before_surplus_isolation',
    'test_insufficient_real_load_retains_no_export_interlock',
    'test_execution_limits_separates_saved_effective_and_fixed_protection_reference',
    'test_execution_limits_exposes_masked_boiler_effective_dispatch_separate_from_arrived_permission',
    'test_exhausted_energy_masks_but_does_not_delete_permission_and_grid_still_returns',
])
def test_inherited_causal_execution_contract_on_m19(test_name):
    import test_execution_recovery_core as retained
    test=getattr(retained,test_name)
    isolated(test,dict(test.__globals__,small_core=core))()
