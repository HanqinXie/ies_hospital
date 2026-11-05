"""Pending M23 release repair, run against frozen D source until RED is saved."""
import copy
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_v23_recovery import make
from test_sci_environment import scenario
from hospital_ies.model import RT_TO_KW

def witness_core(store='ups',source_shortfall=10.):
    settings=dict(capacity_kWh=.001,power_kW=1000.,initial_soc=1.)
    e=make(dict(sci_engineering={store if store=='ups' else 'cold_store':settings}))
    s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults={f'GT_{i}':0. for i in (1,2,3)})
    e.reset(scenario=s,seed=7);c=e.core;_,_,loads,_=c.data_at(0.)
    c.state.disconnected_loads[2:,:]=loads[2:,:]
    a=copy.deepcopy(c.executed);a.update(gt_electric_kW=[0.,0.,0.],grid_import_kW=10000.,diesel_kW=0.,boiler_steam_lbph=e.plant.boiler_steam_lbph,
        absorption_cooling_kW=0.,electric_chiller_cooling_kW=[q*RT_TO_KW for q in e.plant.electric_chillers_rt])
    c.executed=copy.deepcopy(a);c.arrived_dispatch=copy.deepcopy(a);c.station.boiler_ready=True
    if store=='ups':
        first=c.non_diesel_witness();a['grid_import_kW']=first['actual_action']['grid_import_kW']-source_shortfall
        c.executed=copy.deepcopy(a);c.arrived_dispatch=copy.deepcopy(a)
    elif store=='cold':
        c.env.model_closure_events=[dict(time_minute=0.,availability=dict(ABS=0.,EC_1=0.,EC_2=0.,EC_3=0.))]
    return e,c

@pytest.mark.parametrize('store',['ups','cold'])
def test_finite_instantaneous_store_is_not_sustained_release(store):
    e,c=witness_core(store)
    before=(c.ups.energy_kWh,c.cold_store.energy_kWh,copy.deepcopy(c.station.observe()),copy.deepcopy(c.executed))
    result=c.non_diesel_witness()
    assert not result['ready']
    assert result['instantaneous']['ready']
    assert not result['sustained_non_DG']['ready']
    bridge=result['finite_bridge']
    assert bridge[store]['actual_discharge_kW']>1.
    assert 0.<bridge[store]['support_minutes']<.01
    assert bridge['required_support_minutes'] is None
    assert bridge['supports_required_window'] is None
    assert bridge['release_authorized'] is False
    assert before==(c.ups.energy_kWh,c.cold_store.energy_kWh,c.station.observe(),c.executed)

def test_sustained_source_needs_no_inventory_or_idle_infinite_claim():
    e,c=witness_core('ups',source_shortfall=0.)
    result=c.non_diesel_witness()
    assert result['ready'] and result['sustained_non_DG']['ready']
    assert result['finite_bridge']['ups']['actual_discharge_kW']==0.
    assert result['finite_bridge']['ups']['support_minutes'] is None
    assert result['finite_bridge']['cold']['support_minutes'] is None
    assert result['sustained_non_DG']['actual_action']['ups_discharge_kW']==0.
    assert result['sustained_non_DG']['actual_action']['cold_discharge_kW']==0.

def test_known_started_gt_clock_is_diagnostic_and_not_unknown_outage_endpoint():
    e,c=witness_core('ups')
    c.env.model_closure_events=[dict(time_minute=0.,availability={'GT_2':1.})]
    c.gt_start_pending[1]=dict(started=0.,ready=10.,requested=128.)
    c.arrived_dispatch['gt_electric_kW'][1]=128.;c._m22_gt_progress[1]=0.
    result=c.non_diesel_witness();bridge=result['finite_bridge']
    assert not result['ready'] and bridge['required_support_minutes']==pytest.approx(10.)
    assert bridge['supports_required_window'] is False
    assert bridge['known_transition_clocks'][0]['kind']=='currently_started_GT'
    assert result['true_remaining_outage_used'] is False

def test_A_receives_sustained_false_despite_instantaneous_finite_bridge():
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy,reserve_reference
    e,c=witness_core('ups');snapshot=decision_snapshot(e)
    snapshot['features']['grid_outage_elapsed_minutes']=300.
    request,diagnostic=SciPolicy('A',plant=e.plant).decide([snapshot])
    options=request['state_coordinated_joint']['coordination']
    assert options['critical_non_diesel_ready'] is False
    assert diagnostic['release_witness']['instantaneous']['ready']
    loads=np.asarray([[snapshot['features'][f'P{g}_{carrier}_demand_kW'] for carrier in ('e','h','c')] for g in range(2)])
    reference=reserve_reference(loads,remaining_kWh=100.,ec_kWe_per_kWc=e.plant.ec_electric_kW_per_rt/RT_TO_KW,shaft_loss_bound_kW=0.,options=options)
    assert reference['causal_release_active'] is False
