"""Pure forward resource accounting; no optimization or episodes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.model import load_plant
from hospital_ies.resilience import empty_faults


def payload():
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    row=SimpleNamespace(outdoor_temperature_C=25.,timestamp=pd.Timestamp('2020-01-01'))
    loads=np.array([[20.,0.,50.],[10.,0.,25.],[10.,0.,25.],[10.,0.,25.]])
    action=dict(gt_electric_kW=[0.,0.,0.],grid_import_kW=500.,diesel_kW=0.,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[125.,1000.,1000.])
    reference=dict(action,electric_chiller_cooling_kW=[125.,0.,0.])
    context=dict(station_ac_available=True,boiler_ready=True,boiler_fixed_kW=.5,
        boiler_kW_per_kWsteam=.012,gt_start_kW=0.,boiler_restart_kW=0.,recovery_cooling_request_kW=0.)
    stores={k:dict(energy_kWh=0.,capacity_kWh=0.,power_kW=0.) for k in ('ups','cold')}
    return dict(row=row,plant=plant,loads=loads,availability=empty_faults(),execution_action=action,
        reference=reference,context=context,stores=stores,dt_h=1./60.,config={'grid_import_limit_kW':500.,'cold_pump_kW_per_kW':.02},
        execution_contract={'diesel_mode':'emergency_bus','diesel_allowed_priorities':['P0','P1']})


def test_holding_actual_reference_cannot_enable_unused_backup_and_balances():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    p=payload();before=deepcopy(p['reference'])
    result=evaluate_forward(**p)
    assert result['action']['electric_chiller_cooling_kW']==pytest.approx([125.,0.,0.])
    assert result['served']==pytest.approx(p['loads'])
    assert max(map(abs,result['energy_balance_residuals']))<1e-4
    assert p['reference']==before and result['dispatch_optimizer_calls']==0
    p['availability']['EC_1']=0.
    fault=evaluate_forward(**p)
    assert fault['action']['electric_chiller_cooling_kW']==[0.,0.,0.]
    assert fault['lost'][:,2]==pytest.approx(p['loads'][:,2])
    assert fault['served'][:,0]==pytest.approx(p['loads'][:,0])


def test_priority_allocation_and_storage_are_energy_bounded():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    p=payload();p['execution_action']['grid_import_kW']=15.
    p['stores']['ups']=dict(energy_kWh=1.,capacity_kWh=1.,power_kW=10.)
    r=evaluate_forward(**p)
    assert r['action']['ups_discharge_kW']<=10.
    assert r['action']['ups_discharge_kW']<=r['served'][:2,0].sum()+1e-7
    assert r['served'][2:,0].sum()==0.
    assert max(map(abs,r['energy_balance_residuals']))<1e-4


def test_post60_forward_hook_does_not_delegate_to_native_optimizer():
    from tools.noopt_unattended_20261104.runtime import ArithmeticCoreMixin
    from tools.cooling_dynamics_20261104 import CoolingDynamics
    p=payload()
    class NativeForbidden:
        def _solve(self,*args,**kwargs):raise AssertionError('native optimization delegated')
    class Core(ArithmeticCoreMixin,NativeForbidden):
        def data_at(self,time):return 1,p['row'],p['loads'],p['availability']
        def _current_context(self,f):return p['context']
    core=object.__new__(Core);core.time=60.
    core.env=SimpleNamespace(plant=p['plant'],minute1_variant='unattended',scenario={},config=p['config'],
        sci_config={'cold_pump_kW_per_kW':.02},operating_execution_contract=p['execution_contract'],single_fault_fuel_curve=None)
    core._noopt_reference=p['reference'];core._last_running_actual=p['reference'];core.executed=p['execution_action']
    core.cooling_dynamics=CoolingDynamics(initial_cooling_kW=[0.,125.,0.,0.])
    core.forward_evaluations=[]
    for name in ('ups','cold'):
        d=p['stores'][name];setattr(core,'ups' if name=='ups' else 'cold_store',SimpleNamespace(config=d,energy_kWh=d['energy_kWh']))
    r=core._solve(p['loads'],p['availability'],p['dt_h'],'fixed_execution',None,execution_action=p['execution_action'])
    assert r['dispatch_optimizer_calls']==0 and len(core.forward_evaluations)==1
    assert max(map(abs,r['energy_balance_residuals']))<1e-4


def test_zero_call_guard_rejects_optimizer_before_its_body():
    from tools.noopt_unattended_20261104.runtime import forbid_optimization,ForbiddenDispatchOptimization
    from tools.operating_response.physical_balance import solve_physical
    core=SimpleNamespace(time=60.,env=SimpleNamespace(minute1_variant='unattended',dt_minutes=1.),_noopt_optimizer_attempts=[])
    with pytest.raises(ForbiddenDispatchOptimization):
        with forbid_optimization(core):solve_physical(None,None,None,None,None,None,None,None,None)
    assert len(core._noopt_optimizer_attempts)==1
    assert core._noopt_optimizer_attempts[0]['blocked_before_body'] is True


def test_emergency_diesel_and_building_ups_cannot_power_station_or_cooling():
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    p=payload();p['loads']=np.zeros((4,3));p['loads'][0]=[50.,20.,50.]
    p['availability']['GRID']=0.;p['execution_action'].update(grid_import_kW=0.,diesel_kW=50.,boiler_steam_lbph=1000.)
    p['reference'].update(boiler_steam_lbph=1000.)
    p['stores']['ups']=dict(energy_kWh=20.,capacity_kWh=20.,power_kW=20.)
    r=evaluate_forward(**p)
    assert r['action']['station_auxiliary_kW']==0.
    assert sum(r['action']['electric_chiller_cooling_kW'])==0.
    assert r['action']['diesel_kW']<=r['served'][:2,0].sum()-r['action']['ups_discharge_kW']+1e-7


@pytest.mark.parametrize('mode',['empty','full'])
def test_forward_uses_original_continuous_storage_event_split_before_commit(mode):
    from tools.noopt_unattended_20261104.forward import evaluate_forward
    from tools.single_fault_model_20261102.storage import ContinuousLossStore
    from tools.model_correctness_v23.environment import close_storage_interval
    p=payload()
    store=ContinuousLossStore(dict(capacity_kWh=1.,power_kW=10.,initial_soc=.01 if mode=='empty' else .99,
        charge_efficiency=.8,discharge_efficiency=.8,loss_per_hour=.1))
    if mode=='empty':p['execution_action']['grid_import_kW']=0.
    def solve(minutes):
        current=dict(store.config,energy_kWh=store.energy_kWh)
        return evaluate_forward(**dict(p,dt_h=minutes/60.,stores=dict(p['stores'],ups=current)))
    finish,result,receipt=close_storage_interval(60.,61.,solve,[('ups',store)])
    assert 60.<finish<61. and receipt['converged']
    action=result['action']
    actual=store.commit(discharge_kW=action['ups_discharge_kW'],charge_kW=action['ups_charge_kW'],dt_h=(finish-60.)/60.)
    assert actual['final_kWh']==pytest.approx(0. if mode=='empty' else 1.,abs=1e-7)
    assert abs(actual['balance_residual_kWh'])<1e-7
    assert max(action['ups_discharge_kW'],action['ups_charge_kW'])==pytest.approx(10.)
    assert not min(action['ups_discharge_kW'],action['ups_charge_kW'])


def test_native_strict_emergency_partition_matches_forward_topology():
    from hospital_ies.resilience import RuntimeState
    from tools.operating_response.physical_balance import solve_physical
    p=payload();loads=np.zeros((4,3));loads[0]=[50.,100.,0.]
    action=dict(p['execution_action'],grid_import_kW=0.,diesel_kW=50.,
        boiler_steam_lbph=100./(p['plant'].steam_kWh_per_lb*p['plant'].exchanger_eff),
        electric_chiller_cooling_kW=[0.,0.,0.])
    f=dict(p['availability'],GRID=0.)
    state=RuntimeState(np.zeros(3),100.,set(),np.zeros(3),{})
    context=dict(p['context'],preserve_bridge=False,ups_energy_kWh=20.,ups_target_kWh=0.)
    config=dict(strict_emergency_source_partition=True,
        _model_closure_v22=context,_sci_storage_ports=dict(ups_discharge_kW=20.,ups_charge_kW=0.,
            cold_discharge_kW=0.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02))
    result=solve_physical(p['row'],p['plant'],loads,f,state,p['dt_h'],500.,True,900.,6.,config,
        execution_action=action,execution_contract=dict(p['execution_contract'],diesel_dispatch_mode='arrived_ceiling',grid_import_mode='arrived_ceiling'))
    a=result['action']
    assert a['diesel_kW']+a['ups_discharge_kW']<=result['served'][:2,0].sum()+1e-7
    assert a['station_auxiliary_kW']==pytest.approx(0.,abs=1e-7)
    assert result['served'][0,1]==pytest.approx(0.,abs=1e-7)


@pytest.mark.parametrize('variant,minute,installed',[
    ('normal',60.,False),('responsive',60.,False),
    ('unattended',58.,False),('unattended',59.,True),('unattended',60.,True)])
def test_optimizer_profile_is_installed_only_at_strict_u_boundary(variant,minute,installed):
    from tools.noopt_unattended_20261104.runtime import forbid_optimization
    core=SimpleNamespace(time=minute,env=SimpleNamespace(minute1_variant=variant,dt_minutes=1.),_noopt_optimizer_attempts=[])
    previous=sys.getprofile()
    with forbid_optimization(core):
        assert (sys.getprofile() is not previous) is installed
    assert sys.getprofile() is previous
