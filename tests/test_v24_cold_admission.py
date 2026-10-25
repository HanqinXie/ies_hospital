"""Source02: stored cold needs an original non-cost purpose, not price alone."""
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_v24_cold_modes import cold_case
from test_sci_environment import scenario
from hospital_ies.resilience import lex_lock_tolerance

ORDER=(0,1,'start','bridge',2,3,'ups_current_service_only','charge','recovery')


def candidate(*,discharge=False,changes=None):
    hierarchy=[dict(name=name,value=0.,lock_tolerance=lex_lock_tolerance(name,0.) if type(name) is int else 0. if name=='ups_current_service_only' else 1e-7) for name in ORDER]
    hierarchy.append(dict(name='cost',value=1.,lock_tolerance=0.))
    for row in hierarchy:
        if changes and row['name'] in changes:row['value']=changes[row['name']]
    return dict(action={'cold_discharge_kW':100. if discharge else 0.,'cold_charge_kW':0.},
        _m23_objective_hierarchy=hierarchy,cost_USD_step=1.,solver_backend_layers=[]),dict(cold_discharge_kW=120. if discharge else 0.,cold_charge_kW=0.)


def test_healthy_full_store_does_not_discharge_solely_for_cost():
    env,permission=cold_case(initial_soc=1.);core=env.core
    env.model_closure_events=[dict(time_minute=0.,availability={'AUX_FAN_P0':1.})]
    permission['electric_chiller_cooling_kW']=[200.,0.,0.]
    _,_,loads,availability=core.data_at(0.)
    result=core._solve(loads,availability,1./60.,'source02_normal_stock',{},execution_action=permission)
    assert result['action']['cold_discharge_kW']==0.
    assert any(not x['eligible'] for x in result['m22_mode_selection']['discharge_admission']['decisions'])
    assert core.cold_store.energy_kWh==60. and core.time==0.


def test_actual_healthy_five_minutes_preserve_stock_except_original_standing_loss():
    from tools.engineering_completion_v24.environment import OperatingHospitalIESEnv,decision_snapshot
    from tools.model_correctness_v23.policy import SciPolicy
    authority=json.loads((ROOT/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf8'))
    sealed=[int(x['time_block_id'].rsplit('_',1)[1]) for x in authority['p2b_time_blocks'] if x['reserved_only']]
    env=OperatingHospitalIESEnv(ROOT,allowed_hours=[4001,4002],sealed_days=sealed,model_config={
        'sci_engineering':{'cold_store':{'capacity_kWh':60.,'power_kW':120.,'initial_soc':1.,'loss_per_hour':.01}}})
    scene=scenario();scene.update(faults={'GRID':1.},fault_start_minute=0.,fault_duration_minutes=15.)
    env.reset(scenario=scene,seed=9)
    request,_=SciPolicy('C',plant=env.plant).decide([decision_snapshot(env)])
    env.core.generate_command(request);env.core.advance(5.)
    assert env.segment_history and env.segment_history[-1]['end_minute']==5.
    assert all(x['action']['cold_discharge_kW']==0. and x['action']['cold_charge_kW']==0. for x in env.segment_history)
    assert env.core.cold_store.energy_kWh==pytest.approx(60.*math.exp(-.01*5./60.),abs=1e-8)
    assert all(abs(x['m21_storage']['cold_store']['balance_residual_kWh'])<1e-8 for x in env.segment_history)


def test_actual_source_shortage_still_uses_cold_for_reachable_service():
    env,permission=cold_case(initial_soc=.1);core=env.core
    env.model_closure_events=[dict(time_minute=0.,availability={'AUX_FAN_P0':1.,'EC_1':0.})]
    core.advance(.5)
    assert any(x['action']['cold_discharge_kW']>1. for x in env.segment_history)
    assert core.cold_store.energy_kWh<6.
    assert all(abs(x['m21_storage']['cold_store']['balance_residual_kWh'])<1e-8 for x in env.segment_history)


@pytest.mark.parametrize('name',ORDER)
def test_admission_preserves_every_original_non_cost_improvement(name):
    from tools.engineering_completion_v24.cold_modes import admit_discharge_candidates
    idle=candidate();row=next(x for x in idle[0]['_m23_objective_hierarchy'] if x['name']==name)
    discharge=candidate(discharge=True,changes={name:-2.*row['lock_tolerance']-1.})
    admitted,diagnostic=admit_discharge_candidates([idle,discharge])
    assert any(result is discharge[0] for result,_ in admitted)
    assert diagnostic['decisions'][0]['eligible']


def test_admission_keeps_original_bands_and_does_not_compensate_higher_priority_loss():
    from tools.engineering_completion_v24.cold_modes import admit_discharge_candidates
    idle=candidate();tol=idle[0]['_m23_objective_hierarchy'][0]['lock_tolerance']
    for discharge in (candidate(discharge=True),candidate(discharge=True,changes={0:-tol/2.}),candidate(discharge=True,changes={0:2.*tol,3:-100.})):
        admitted,diagnostic=admit_discharge_candidates([idle,discharge])
        assert all(result is not discharge[0] for result,_ in admitted)
        assert not diagnostic['decisions'][0]['eligible']
    discharge=candidate(discharge=True)
    assert admit_discharge_candidates([discharge])[0][0][0] is discharge[0]


@pytest.mark.parametrize('defect',['unknown','duplicate','missing'])
def test_objective_contract_errors_fail_explicitly(defect):
    from tools.engineering_completion_v24.cold_modes import admit_discharge_candidates
    idle=candidate();discharge=candidate(discharge=True);rows=discharge[0]['_m23_objective_hierarchy']
    if defect=='unknown':rows.append(dict(name='unknown_future_goal',value=0.,lock_tolerance=0.))
    elif defect=='duplicate':rows.append(copy.deepcopy(rows[0]))
    else:rows.pop(0)
    with pytest.raises(ValueError,match='candidate.*objective') as error:admit_discharge_candidates([idle,discharge])
    assert error.value.category=='cold_discharge_objective_contract'
    assert error.value.command_compilation_diagnostic['candidate_index']==1


def fake_core():
    transcript=[];result,_=candidate()
    result['lost']=[[0.,0.,0.] for _ in range(4)]
    result['solver_backend_layers']=[dict(rank=0,layer=0,backend='test_transcript',status=0,attempts=[])]
    core=SimpleNamespace(time=0.,solver_calls=[],env=SimpleNamespace(correctness_config={'storage_fix':True}),
        ups=SimpleNamespace(energy_kWh=0.,config={'capacity_kWh':0.}),cold_store=SimpleNamespace(energy_kWh=0.,config={'capacity_kWh':0.}),
        cold_recharge=SimpleNamespace(allowed=lambda energy,capacity:False))
    def ports(**kwargs):
        transcript.append(('ports',copy.deepcopy(kwargs)))
        return dict(ups_discharge_kW=0.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02)
    core._base_ports=ports
    def solve(p,extra=None):transcript.append(('solve',copy.deepcopy(p),copy.deepcopy(extra)));return result
    return core,solve,result,transcript


def test_capacity_zero_bypasses_filter_and_matches_exact_source01_transcript(monkeypatch):
    from tools.engineering_completion_v24 import cold_modes
    archive_namespace={'__name__':'source01_cold_modes_snapshot'}
    snapshot=ROOT/'tests/fixtures/v24_source01_cold_modes_snapshot.py'
    exec(compile(snapshot.read_bytes(),str(snapshot),'exec'),archive_namespace)
    def forbidden(*args,**kwargs):raise AssertionError('capacity-zero path entered Source02 admission')
    monkeypatch.setattr(cold_modes,'admit_discharge_candidates',forbidden)
    old,old_solve,old_result,old_calls=fake_core();new,new_solve,new_result,new_calls=fake_core()
    previous=archive_namespace['search'](old,old_solve,old_result,np.zeros((4,3)),{},1./60.)
    current=cold_modes.search(new,new_solve,new_result,np.zeros((4,3)),{},1./60.)
    assert current==previous
    assert new_calls==old_calls and new.solver_calls==old.solver_calls
