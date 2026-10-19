"""P0 red specifications: strict xfail documents defects, never certifies a fix.

Run with --runxfail to observe actual assertion failures on frozen v4.2.
Only deterministic software contracts are asserted here; ATS topology, the
physical protection denominator and thermal attribution remain diagnostics.
"""
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.control_env import HospitalIESEnv
from scenario_sampler import sample_scenarios
from tools.p0_audit.cases import (
    proxy_case,delay_case,derate_case,env_timing_case,legacy_timing_case,resume_case,
)

@pytest.fixture(scope='module')
def env():
    return HospitalIESEnv(ROOT)

def test_satisfied_persistent_shock_has_no_new_transfer(env):
    r=proxy_case(env,balanced=True)
    assert r['previous_GT_kW']==r['connected_electric_kW']==600.
    assert r['debug']['precontrol_GT_transfer_kW']==pytest.approx(0.,abs=1e-9),r

def test_delay_holds_setpoints_before_command_arrival(env):
    immediate=delay_case(env,0)['records'][1]
    delayed=delay_case(env,3)['records'][1]
    # Positive control: the event actually needs a changed operating point.
    assert np.max(np.abs(np.array(immediate['after'])-immediate['before']))>1.
    assert delayed['delayed'] is True
    np.testing.assert_allclose(delayed['after'],delayed['before'],atol=1e-4,rtol=0,
        err_msg='command not yet executable, no modeled automatic setpoint response')

def test_control_flag_does_not_erase_sustained_stress(env):
    a=proxy_case(env,controlled=False,sustained=True)
    b=proxy_case(env,controlled=True,sustained=True)
    assert a['stress']['GT_1']==b['stress']['GT_1']==1.125
    assert b['timer'][0]>=10.,b

def test_forced_derating_does_not_make_bounds_infeasible(env):
    r=derate_case(env,.1)
    assert r['success'],r

@pytest.mark.parametrize('availability',[0.,1.])
def test_total_trip_and_normal_ramp_controls(env,availability):
    # 200 kW is above the healthy GT minimum stable output (123.579 kW).
    # A 100 kW isolated demand cannot absorb that minimum and is not a valid
    # healthy positive control when export/dumping is unavailable.
    r=derate_case(env,availability,load_kw=200.)
    assert r['success'],r
    assert r['GT1_kW']<=r['capacity_kW']+1e-4
    if availability:
        assert r['GT1_kW']>=r['normal_ramp_lower_kW']-1e-4

def test_default_15_minute_env_trip_is_at_right_boundary(env):
    r=env_timing_case(env,15)
    first,second=r['records']
    assert first['new_trips']
    assert first['time']==15 and first['effective_step']==1
    assert sum(first['GT_kW'])>0
    assert sum(second['GT_kW'])==pytest.approx(0.,abs=1e-4)

def test_mid_interval_hold_expiry_keeps_actual_trigger_time(env):
    r=env_timing_case(env,30)
    assert r['records'][0]['new_trips']
    assert r['records'][0]['time']==r['hold_minutes'],r

def test_legacy_path_keeps_gt_available_until_hold_expires(env):
    r=legacy_timing_case(env)
    first=r['records'][0]
    assert first['new_trips'],r
    # Positive GT contribution must exist before the 15-minute hold expires.
    assert sum(first[f'gt_electric_kW_{i}'] for i in (1,2,3))>1e-4,r

@pytest.fixture(scope='module')
def sampled_specs(env):
    # The supplied range diagnostic already uses this finite design. These
    # are in-memory specifications, not additional simulated/training samples.
    return sample_scenarios(env.hourly,100,12,20261012)

@pytest.mark.parametrize('mode,carrier,half',[('e','e',.9),('h','h',.5),('mixed','e',.9),('mixed','h',.5)])
def test_active_mode_covers_upper_and_lower_half(sampled_specs,mode,carrier,half):
    values=[s['shock'][carrier] for s in sampled_specs if s['stage']==2 and s['shock_mode']==mode]
    assert min(values)<half<max(values),dict(mode=mode,carrier=carrier,minimum=min(values),maximum=max(values))

def test_repeated_device_design_varies_severity(sampled_specs):
    device_values={}
    for s in sampled_specs:
        if s['stage']==3:
            device_values.setdefault(s['initiating_device'],set()).add(s['faults'][s['initiating_device']])
    assert all(len(v)>1 for v in device_values.values()),device_values

def test_resume_rejects_changed_dependency_bytes(tmp_path):
    # P2A R09 migration: generate a NEW checkpoint using CURRENT code.
    # The original P0 fixture/log/assertion remains in the immutable baseline.
    import json,shutil
    from generate_nonmpc_training import run
    isolated=tmp_path/'isolated_root';isolated.mkdir()
    for name in ('src','configs','inputs'):
        shutil.copytree(ROOT/name,isolated/name,ignore=shutil.ignore_patterns('__pycache__'))
    for p in [*ROOT.glob('*.py'),*ROOT.glob('requirements*.txt')]:shutil.copy2(p,isolated/p.name)
    spec=dict(id='R09_current',stage=2,start_hour=4300,duration_steps=3,shock={'e':.1})
    args=dict(root=isolated,out=tmp_path/'new_checkpoint',steps=3,risk_horizon=1,
        scenario_specs=[spec],execution_contract=json.loads((isolated/'configs/p1b_execution_research.json').read_text()))
    assert run(**args)['steps']==6
    assert run(**dict(args,resume=True))['steps']==6
    p=isolated/'configs/plant_configs.json'
    before=p.read_bytes();p.write_bytes(before+b'\n')
    assert before!=p.read_bytes()
    with pytest.raises(ValueError,match='fingerprint|manifest|dependency'):
        run(**dict(args,resume=True))
