"""New pure busy orchestration regressions; no scientific trajectory execution."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / relative)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_old_completed_busy_short_cannot_qualify_new_execution(tmp_path):
    validator = load('refresh_busy_validator', 'scripts/server_validation.py')
    tmp_path.joinpath('RESULT.json').write_text(json.dumps(dict(status='completed',observed_until_minute=180.,
        unknown_tail_minutes=0.,reset_count=1,source_and_inputs_unchanged=True,source_sha256='s',case_sha256='c')))
    with pytest.raises(ValueError): validator._completed(tmp_path,'s','c')


def test_busy_profile_keeps_standalone_deployment_probes_skipped():
    validator = load('refresh_busy_validator_scope', 'scripts/server_validation.py')
    assert validator.deployment_check_scope() == dict(license='skipped_by_user_request',
        environment_dependencies='skipped_by_user_request')


def test_short_gate_contract_binds_new_owner():
    helper = load('refresh_busy_helper', 'scripts/execution_profile.py')
    assert helper.execution_contract()['model_id'] == 'annual_responsive_dynamic_bridge_20261104.v1'
    assert helper.execution_contract()['owner_contract'] == 'hospital_ies.annual_dynamic.accepted_engineering_owner.v1'


def test_new_mechanism_plan_uses_independent_boiler_single_tower_and_primary_pump(monkeypatch):
    fake=types.ModuleType('tools.annual_program.registration')
    fake.make_case=lambda method,**params: dict(method=method,parameters=params,
        scenario=dict(model_closure_events=params.get('calendar') or [],start_hour=params['start_hour']))
    monkeypatch.setitem(sys.modules,'tools.annual_program.registration',fake)
    module=load('refresh_mechanisms','scripts/mechanism_validation.py')
    cases=module.mechanism_cases()
    assert set(cases)=={kind+'_'+phase for kind in ('ec','boiler','tower','pump') for phase in ('healthy','fault')}
    assert cases['boiler_healthy']['scenario']['model_closure_events']==[]
    assert all(set(event['availability'])=={'BOILER_1'} for event in cases['boiler_fault']['scenario']['model_closure_events'])
    assert cases['tower_fault']['scenario']['start_hour']==4137
    assert cases['pump_fault']['scenario']['start_hour']==5072
    assert all(case['parameters']['execution_revision']=='annual_responsive_dynamic_bridge_20261104.v1' for case in cases.values())
