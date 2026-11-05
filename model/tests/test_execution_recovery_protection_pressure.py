"""Original gross-stress proxy and real same-state source replacement."""
import copy,importlib.util
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v18_stress_saved',ROOT/'outputs/v18_execution_recovery_20261018_run01/review/c18_constraint_localization/replay.py')
replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)
OBSERVED_GROSS_KW=.00374313232568


def inputs(support='DIESEL',cold_kW=0.):
    f=copy.deepcopy(replay.fixture());f['row']['outdoor_temperature_C']=0.
    f['loads_kW']=np.zeros((4,3)).tolist();f['loads_kW'][0]=[200.,0.,cold_kW]
    f['loads_kW'][1][0]=100.;f['loads_kW'][3][0]=50.+OBSERVED_GROSS_KW
    f['request']['risk_loads_kW']=np.zeros((4,3)).tolist()
    f['availability'].update(GT_1=1.,GT_2=0.,GT_3=0.,GRID=float(support=='GRID'))
    f['state'].update(gt_prev=[336.,0.,0.],last_diesel_kW=14. if support=='DIESEL' else 0.,
        last_grid_import_kW=14. if support=='GRID' else 0.,auxiliary_request_kW=0.,cooling_reference_kW=0.,
        diesel_energy_remaining_kWh=2400.,diesel_status='CONNECTED' if support=='DIESEL' else 'OFF',
        overload_minutes=[14.,0.,0.],extra_faulted=[],disconnected_loads=np.zeros((4,3)).tolist())
    c=f['readiness_context'];c.update(actual_gt_kW=[336.,0.,0.],gt_ramp_anchor_kW=[336.,0.,0.],
        gt_physical_status=['RUNNING','EXOGENOUS_UNAVAILABLE','EXOGENOUS_UNAVAILABLE'],
        gt_can_stop=[False,True,True],pending_starts=[None]*3,actual_disconnected_kW=np.zeros((4,3)).tolist(),
        observed_critical_unserved_kW=np.zeros((2,3)).tolist(),diesel_connected=support=='DIESEL',
        actual_diesel_kW=14. if support=='DIESEL' else 0.,diesel_arrived_cap_kW=900. if support=='DIESEL' else 0.)
    c['arrived_dispatch'].update(gt_electric_kW=[336.,0.,0.],grid_import_kW=850. if support=='GRID' else 0.,
        diesel_kW=900. if support=='DIESEL' else 0.,boiler_steam_lbph=0.)
    c['diesel_local_response'].update(actual_electric_kW=c['actual_diesel_kW'],arrived_ceiling_kW=c['diesel_arrived_cap_kW'],permission_status=f['state']['diesel_status'])
    rotor=c['diesel_local_response']['rotor'];rotor.update(rotor_energy_kWs=rotor['nominal_rotor_energy_kWs'],speed_pu=1.,mechanical_kW=c['actual_diesel_kW'],fuel_exhausted=False)
    kwargs=replay.inputs(f);kwargs['coordination_options']['execution_recovery']=True
    kwargs['execution_limits']=dict(schema='hospital_ies.execution_limits.v18.v1',
        hardware_grid_available_fraction=f['availability']['GRID'],grid_bus_state='GRID' if support=='GRID' else 'ISLAND',
        effective_grid_import_limit_kW=850. if support=='GRID' else 0.,effective_diesel_limit_kW=c['diesel_arrived_cap_kW'],
        diesel_conditionally_permitted=True,grid_transfer_remaining_minutes=None,gt_protection_reference_kW=320.,
        gt_trip_multiple=1.05,gt_hold_minutes=15.,gt_overload_minutes=[14.,0.,0.])
    return kwargs


def original_proxy(kwargs,*,gt=None,replacement_DG=0.,replacement_GRID=0.):
    from hospital_ies.resilience import pre_control_cascade
    state=copy.deepcopy(kwargs['state'])
    if gt is not None:state.gt_prev=np.asarray(gt)
    state.last_diesel_kW+=replacement_DG;state.last_grid_import_kW+=replacement_GRID
    config={**kwargs['config'],'precontrol_load_shed_enabled':False}
    return pre_control_cascade(state,kwargs['availability'],np.asarray(kwargs['loads']),kwargs['plant'],config,0.,False,
        grid_import_limit_kW=state.last_grid_import_kW if kwargs['availability']['GRID'] else 0.,
        diesel_response_kW=state.last_diesel_kW,apply_trips=False)


def test_lowering_GT_without_real_replacement_does_not_reduce_original_stress():
    kwargs=inputs();before=original_proxy(kwargs)
    no_replacement=original_proxy(kwargs,gt=[335.98,0.,0.])
    replaced=original_proxy(kwargs,gt=[335.98,0.,0.],replacement_DG=.02)
    assert before[1]['GT_1']>1.05
    assert no_replacement[1]['GT_1']==pytest.approx(before[1]['GT_1'],abs=1e-14)
    assert replaced[1]['GT_1']<1.05
    assert kwargs['state'].overload_minutes==[14.,0.,0.]


@pytest.mark.parametrize('support',['DIESEL','GRID'])
def test_soft_margin_has_real_balanced_replacement_and_clears_observed_stress(support):
    from tools.execution_recovery import planner
    kwargs=inputs(support);result=planner.solve_joint(**kwargs)
    target=result['action']['gt_electric_kW'][0];reduction=336.-target
    assert target<=335.99+1e-7
    used=result['planned_current_action']['diesel_kW' if support=='DIESEL' else 'grid_import_kW']
    assert used>=14.+reduction-1e-7
    # Credit only a real support increment covered by the balanced plan,
    # retaining the observed gross residual rather than hiding it as a cut.
    witness=original_proxy(kwargs,gt=[target,0.,0.],**{'replacement_DG' if support=='DIESEL' else 'replacement_GRID':reduction})
    assert witness[1]['GT_1']<1.05
    assert np.asarray(result['lost']).max()<.001
    observed=result['joint_diagnostic']['coordination']['protection_observation']
    assert observed['current_precontrol_GT_transfer_kW']==pytest.approx(OBSERVED_GROSS_KW,abs=1e-10)
    assert observed['dispatch_margin_kW']==.01
    assert observed['observed_running_GT_count']==1
    assert result['joint_diagnostic']['command_finalization']==result['joint_diagnostic']['engineering']['command_finalization']


def test_unavailable_replacement_keeps_critical_service_with_explicit_soft_excess():
    from tools.execution_recovery import planner
    kwargs=inputs();kwargs['loads']=np.zeros((4,3));kwargs['loads'][0,0]=336.
    kwargs['state'].diesel_energy_remaining_kWh=0.;kwargs['state'].last_diesel_kW=0.;kwargs['state'].diesel_status='EMPTY'
    c=kwargs['readiness_context'];c.update(diesel_connected=False,actual_diesel_kW=0.,diesel_arrived_cap_kW=0.)
    c['arrived_dispatch']['diesel_kW']=0.;c['diesel_local_response'].update(actual_electric_kW=0.,arrived_ceiling_kW=0.,permission_status='EMPTY')
    c['diesel_local_response']['rotor']['fuel_exhausted']=True
    kwargs['execution_limits'].update(effective_diesel_limit_kW=0.,diesel_conditionally_permitted=False)
    result=planner.solve_joint(**kwargs)
    assert result['lost'][0,0]<.001
    assert result['action']['gt_electric_kW'][0]==pytest.approx(336.,abs=.001)
    assert result['joint_diagnostic']['engineering']['protection_target_excess_kW'][0]>=.009


def test_cold_proxy_is_protection_risk_without_becoming_actual_extra_electric_load():
    from hospital_ies.model import RT_TO_KW
    from tools.execution_recovery import planner
    kwargs=inputs(cold_kW=10.);result=planner.solve_joint(**kwargs)
    d=result['joint_diagnostic']['coordination']['protection_observation']
    assert d['current_cooling_proxy_kW']>0.
    action=result['planned_current_action'];plant=kwargs['plant']
    aux=(action['absorption_cooling_kW']*plant.abs_electric_kW_per_rt+
        sum(action['electric_chiller_cooling_kW'])*plant.ec_electric_kW_per_rt)/RT_TO_KW
    sources=sum(action['gt_electric_kW'])+action['grid_import_kW']+action['diesel_kW']
    assert sources-aux==pytest.approx(np.asarray(result['served'])[:,0].sum(),abs=1e-7)
    assert max(map(abs,result['energy_balance_residuals']))<1e-7
