"""Real empty-store/critical-deficit tests, and same-snapshot event closure."""
import copy
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_v23_recovery import make
from test_sci_environment import scenario

def test_empty_store_continuing_deficit_does_not_recharge_service_lock_slack():
    e=make(dict(sci_engineering=dict(cold_store=dict(capacity_kWh=10.,power_kW=100.,initial_soc=0.))))
    s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults={f'GT_{i}':0. for i in (1,2,3)})
    e.reset(scenario=s,seed=9);c=e.core;_,_,loads,f=c.data_at(0.)
    loads[:]=0.;loads[0]=[10.,0.,100.];f.update(ABS=0.,EC_1=.01,EC_2=0.,EC_3=0.)
    action=dict(c.executed,gt_electric_kW=[0.,0.,0.],grid_import_kW=100.,diesel_kW=0.,boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[100.,0.,0.])
    result=c._solve(loads,f,1./60.,'empty_test',{},execution_action=action)
    assert result['lost'][0,2]>1.
    assert result['m22_mode_selection']['cold_mode']=='idle'
    assert result['action']['cold_charge_kW']==0.
    assert c.cold_store.energy_kWh==0.

def test_same_snapshot_event_closure_does_not_commit_and_relocates_after_power_change():
    from tools.model_correctness_v23.environment import close_storage_interval
    from tools.sci_engineering.storage import FiniteStore
    store=FiniteStore(dict(capacity_kWh=10.,power_kW=100.,initial_soc=.08,discharge_efficiency=1.))
    probes=[]
    def solve(minutes):
        probes.append(minutes)
        return {'action':{'cold_discharge_kW':50.+minutes*10.,'cold_charge_kW':0.}}
    before=store.energy_kWh
    minute,result,receipt=close_storage_interval(0.,1.,solve,[('cold',store)])
    power=result['action']['cold_discharge_kW']
    assert minute*power/60.==pytest.approx(before,abs=1e-9)
    assert len(probes)>2 and store.energy_kWh==before
    assert receipt['converged'] and receipt['inventory_committed'] is False

def test_real_recharge_reopens_discharge_without_inventory_threshold_increase():
    e=make(dict(sci_engineering=dict(cold_store=dict(capacity_kWh=10.,power_kW=100.,initial_soc=0.))))
    e.reset(scenario=scenario(),seed=9);c=e.core;_,_,loads,f=c.data_at(0.)
    # This isolated port proof has no building fan ledger; full native
    # dependency integration is exercised by the convergence case below.
    e.config['sci_service_dependencies']=False
    loads[:]=0.;loads[0]=[10.,0.,100.];f.update(GT_1=0.,GT_2=0.,GT_3=0.,ABS=0.,EC_1=1.,EC_2=0.,EC_3=0.)
    action=dict(c.executed,gt_electric_kW=[0.,0.,0.],grid_import_kW=100.,diesel_kW=0.,boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[200.,0.,0.])
    result=c._solve(loads,f,.5/60.,'real_recharge',{},execution_action=action)
    assert result['action']['cold_charge_kW']>1.
    c.cold_store.commit(discharge_kW=0.,charge_kW=result['action']['cold_charge_kW'],dt_h=.5/60.)
    before=c.cold_store.energy_kWh;f['EC_1']=0.
    actual=c._solve(loads,f,.5/60.,'real_discharge',{},execution_action=action)
    assert actual['action']['cold_discharge_kW']>1. and before>0.
    assert c.cold_store.energy_kWh==before

def test_real_depletion_converges_with_fixed_upper_decision_clock():
    from tools.model_correctness_v23.environment import decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    outputs=[]
    for minutes in (1.,.5):
        e=make(dict(sci_engineering=dict(storage_max_segment_minutes=minutes,cold_store=dict(capacity_kWh=1.,power_kW=100.,initial_soc=1.))))
        s=scenario();s.update(fault_start_minute=0.,fault_duration_minutes=45.,faults=dict(GT_1=0.,GT_2=0.,GT_3=0.,ABS=0.,EC_1=.01,EC_2=0.,EC_3=0.,TOWER=.5))
        e.reset(scenario=s,seed=9);request,_=SciPolicy('R',plant=e.plant).decide([decision_snapshot(e)])
        command=e.core.generate_command(request);e.core.advance(3.)
        segments=e.segment_history
        assert len(e.core.pending_commands)<=1 and command['generated_minute']==0.
        assert all(x['action']['cold_charge_kW']==0. for x in segments)
        discharge=sum(x['action']['cold_discharge_kW']*(x['end_minute']-x['start_minute'])/60. for x in segments)
        ens=sum(float(np.asarray(x['unserved_kW'])[:2,2].sum())*(x['end_minute']-x['start_minute'])/60. for x in segments)
        assert e.core.cold_store.energy_kWh<1e-8
        assert discharge==pytest.approx(.95,abs=1e-8)
        assert all(abs(x['m21_storage']['cold_store']['balance_residual_kWh'])<1e-8 for x in segments)
        outputs.append((ens,discharge))
    assert outputs[0]==pytest.approx(outputs[1],abs=1e-5)
