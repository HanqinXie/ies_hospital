"""Reject corrupted registrations and plans before any physical episode."""
import copy
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.engineering_basis_study.registration import load_registry, make_case, worker_registry


def api(name):
    try:
        return importlib.import_module('tools.engineering_basis_study.'+name)
    except ModuleNotFoundError:
        pytest.fail('Named new execution entry point is not implemented: '+name, pytrace=False)


def case():
    return make_case(load_registry(), 'b07_L4_GT_ER_disturbed_M', 'C_available', start_hour=33)


def test_saved_worker_registry_must_match_only_the_registered_named_assumption():
    c, reg = case(), load_registry()
    registration = dict(case=c, registry=worker_registry(c, reg))
    assert api('validation').trusted_worker_registration(registration, reg) == c
    bad = copy.deepcopy(registration)
    bad['registry']['model_closure_v22']['station']['control_autonomy_minutes'] = 999999.
    with pytest.raises(ValueError):
        api('validation').trusted_worker_registration(bad, reg)
    bad = copy.deepcopy(registration)
    bad['registry']['hardware_profiles']['H0']['ups'] = {'capacity_kWh': 999999.}
    with pytest.raises(ValueError):
        api('validation').trusted_worker_registration(bad, reg)


def test_worker_rejects_boolean_numeric_substitution():
    c, reg = case(), load_registry()
    saved = dict(case=c, registry=worker_registry(c, reg))
    saved['registry']['model_closure_v22']['station']['boiler_restart_minutes'] = True
    with pytest.raises(ValueError):
        api('validation').trusted_worker_registration(saved, reg)


def test_preflight_does_not_accept_a_changed_case():
    c = case()
    c['scenario']['grid_limit_kW'] *= 2
    with pytest.raises(ValueError):
        api('validation').preflight(c)


def write_case(tmp_path, c):
    path = tmp_path/(c['id']+'.json')
    path.write_text(json.dumps(c, allow_nan=False), encoding='utf8')
    return path


def test_plan_binds_full_cases_new_identity_and_fresh_outputs(tmp_path):
    c = case()
    path = write_case(tmp_path, c)
    plan_path = tmp_path/'PLAN.json'
    base = tmp_path/'study'
    plan = api('campaign').register_plan([path], plan_path, study_root=base)
    assert plan['study_id'] == 'hospital_ies.engineering_basis.20261029.v1'
    assert plan['items'][0]['case_id'] == c['id']
    assert plan['items'][0]['method'] == 'C_available'
    assert plan['items'][0]['output'] == str(base.resolve()/'runs'/c['id'])
    assert plan['identity'] == api('campaign').identity()
    assert plan['items'][0]['case_sha256'] == api('campaign').sha(path)
    with pytest.raises(FileExistsError):
        api('campaign').register_plan([path], plan_path, study_root=base)


def test_duplicate_actual_cases_cannot_be_counted_as_new(tmp_path):
    path = write_case(tmp_path, case())
    dest = tmp_path/'INVALID.json'
    with pytest.raises(ValueError):
        api('campaign').register_plan([path, path], dest, study_root=tmp_path/'study')
    assert not dest.exists()


@pytest.mark.parametrize('kwargs', [dict(max_attempts=513), dict(max_attempts=0),
                                   dict(max_attempts=True), dict(max_wall_hours=25),
                                   dict(max_wall_hours=0), dict(max_wall_hours=float('nan'))])
def test_invalid_budget_is_rejected_before_plan_creation(tmp_path, kwargs):
    path = write_case(tmp_path, case())
    dest = tmp_path/'INVALID.json'
    with pytest.raises(ValueError):
        api('campaign').register_plan([path], dest, study_root=tmp_path/'study', **kwargs)
    assert not dest.exists()


def test_forged_case_cannot_enter_a_frozen_plan(tmp_path):
    c = case()
    c['policy']['config']['online_GT_count_order'] = 'before_cost'
    path = write_case(tmp_path, c)
    with pytest.raises(ValueError):
        api('campaign').register_plan([path], tmp_path/'INVALID.json', study_root=tmp_path/'study')


def test_verify_entry_rejects_current_source_identity_drift_before_result_reads(tmp_path):
    module = api('campaign')
    path = write_case(tmp_path, case())
    plan_path = tmp_path/'PLAN.json'
    plan = module.register_plan([path], plan_path, study_root=tmp_path/'study')
    plan['identity']['source_sha256'] = 'stale-source'
    plan_path.write_text(json.dumps(plan), encoding='utf8')
    with pytest.raises(RuntimeError, match='identity drift'):
        module.verify_completed_run(plan['items'][0], plan, plan_path)


def test_verify_entry_binds_exact_case_bytes_even_if_json_meaning_is_equal(tmp_path):
    module = api('campaign')
    path = write_case(tmp_path, case())
    plan_path = tmp_path/'PLAN.json'
    plan = module.register_plan([path], plan_path, study_root=tmp_path/'study')
    path.write_text(path.read_text(encoding='utf8')+'\n', encoding='utf8')
    with pytest.raises(RuntimeError, match='case bytes'):
        module.verify_completed_run(plan['items'][0], plan, plan_path)


def test_verify_entry_rejects_plan_object_different_from_frozen_file(tmp_path):
    module = api('campaign')
    path = write_case(tmp_path, case())
    plan_path = tmp_path/'PLAN.json'
    plan = module.register_plan([path], plan_path, study_root=tmp_path/'study')
    plan['source_history_scope'] = 'forged manuscript claim'
    with pytest.raises(RuntimeError, match='plan bytes'):
        module.verify_completed_run(plan['items'][0], plan, plan_path)
