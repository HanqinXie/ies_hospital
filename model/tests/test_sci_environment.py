from pathlib import Path
import sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
try:
    from tools.sci_engineering.environment import OperatingHospitalIESEnv
except ImportError:
    OperatingHospitalIESEnv=None

def scenario(aux=False):
    s=dict(id='sci_core_test',stage=1,event_class='L1',start_hour=4001,
        duration_steps=3,step_minutes=15.,faults={} if aux else {'GRID':0.},
        fault_start_minute=15.,fault_duration_minutes=15.,enable_propagation=False,
        response_delay_minutes=0.,shock={},design_family='sci_core_test',sampling_cohort='development',
        sampling_method='deterministic_regression')
    if aux:s['auxiliary_faults']={'AUX_CHW_PUMP':0.}
    return s

def env(config=None):
    assert OperatingHospitalIESEnv is not None, 'M21 needs a real executed environment'
    import json
    authority=json.loads((ROOT/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    sealed=[int(x['time_block_id'].rsplit('_',1)[1]) for x in authority['p2b_time_blocks'] if x['reserved_only']]
    return OperatingHospitalIESEnv(ROOT,allowed_hours=[4001,4002],sealed_days=sealed,model_config=config or {})

def test_auxiliary_failure_and_recovery_reach_the_actual_service_balance():
    from tools.sci_engineering.policy import SciPolicy
    from tools.sci_engineering.environment import decision_snapshot
    e=env();s=scenario(aux=True);e.reset(scenario=s,seed=1)
    p=SciPolicy('M',plant=e.plant)
    for _ in range(3):
        action,_=p.decide([decision_snapshot(e)]);e.step(action)
    fault=[x for x in e.segment_history if 15.<=x['start_minute']<30.]
    assert fault
    assert all(np.asarray(x['action']['electric_chiller_cooling_kW']).sum()>=0. for x in fault)
    assert all(np.asarray(x['loads_kW'])[0,2]-np.asarray(x['unserved_kW'])[0,2]<1e-4 for x in fault)
    assert any(x['service_dependencies']['auxiliary_delivered_kW'][0][1]>0.
        for x in e.segment_history if x['start_minute']>=30.)

def test_same_ordinary_command_is_independent_of_policy_identity():
    from tools.sci_engineering.policy import SciPolicy
    from tools.sci_engineering.environment import decision_snapshot
    a=env();b=env();s=scenario();a.reset(scenario=s,seed=2);b.reset(scenario=s,seed=2)
    p=SciPolicy('R',plant=a.plant)
    command,_=p.decide([decision_snapshot(a)])
    ca=a.core.generate_command(command);cb=b.core.generate_command(command)
    ca['policy_id']='first';cb['policy_id']='different'
    a.core.advance(15.);b.core.advance(15.)
    assert np.allclose(a.segment_history[-1]['unserved_kW'],b.segment_history[-1]['unserved_kW'],rtol=0.,atol=1e-7)
    assert a.segment_history[-1]['action']==b.segment_history[-1]['action']
    assert ca['joint_dispatch']['diagnostic']['upper_optimizer_called'] is False

def test_storage_commit_is_bound_to_every_actual_interval():
    from tools.sci_engineering.policy import SciPolicy
    from tools.sci_engineering.environment import decision_snapshot
    cfg=dict(sci_engineering=dict(configuration_id='H1',ups=dict(capacity_kWh=30.,power_kW=250.,initial_soc=1.)))
    e=env(cfg);s=scenario();s['faults'].update({f'GT_{i}':0. for i in (1,2,3)})
    e.reset(scenario=s,seed=3);p=SciPolicy('M',plant=e.plant)
    for _ in range(3):
        request,_=p.decide([decision_snapshot(e)]);e.step(request)
    receipts=[x['m21_storage']['ups'] for x in e.segment_history]
    assert sum(x['discharged_kWh'] for x in receipts)>0.
    assert max(abs(x['balance_residual_kWh']) for x in receipts)<1e-7
    assert all(x['final_kWh']>=0. for x in receipts)

def test_named_department_shock_preserves_original_demand_and_adds_necessary_auxiliary_power():
    e=env();s=scenario(aux=True);s.update(stage=2,event_class='L2',auxiliary_faults={},
        shock_by_zone={'OR1_FLR_2':{'e':.5,'h':.5,'c':.5}},shock_start_minute=15.,shock_duration_minutes=15.)
    e.reset(scenario=s,seed=4)
    before=e.core.data_at(0.)[2];during=e.core.data_at(15.)[2]
    assert during[0,0]>before[0,0]
    assert during[0,2]>before[0,2]
    assert np.allclose(during[1:],before[1:],rtol=0.,atol=1e-9)
    row=e.core.data_at(15.)[1]
    assert row.sci_clinical_increment_kW[0][0]>0.
    assert row.sci_auxiliary_increment_kW[0]>0.

def test_reference_response_clock_is_a_shared_explicit_configuration():
    e=env(dict(sci_engineering=dict(diesel_start_delay_minutes=1./6.)))
    assert e.config['diesel_start_delay_minutes']==pytest.approx(1./6.)
    assert env().config['diesel_start_delay_minutes']==pytest.approx(15.)
def test_protection_sensitivity_is_explicit_common_configuration():
    from tools.sci_engineering.environment import model_contract
    from tools.sci_engineering import MODEL_ID
    _,resolved=model_contract(MODEL_ID,{'sci_engineering':{
        'gt_protection_trip_multiple':1.10,'gt_overload_hold_minutes':7.5}})
    assert resolved['sci_engineering']['gt_protection_trip_multiple']==1.10
    assert resolved['sci_engineering']['gt_overload_hold_minutes']==7.5
