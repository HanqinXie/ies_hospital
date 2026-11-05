import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def api():
    return importlib.import_module('tools.autonomous_dispatch.closed_loop')


def test_registered_factory_keeps_original_baseline_and_joint_identity():
    a = api()
    specs = [{'policy_id': 'priority_milp', 'kind': 'priority'},
             {'policy_id': 'secure_milp', 'kind': 'secure'},
             {'policy_id': 'execution_reserve', 'kind': 'state_dispatch', 'config': {}},
             {'policy_id': 'joint_coupled_h2', 'kind': 'joint', 'config': {'variant': 'coupled', 'support_hours': 2.}}]
    assert a._check_specs(specs) == specs
    assert [a._factory(s).policy_id for s in specs] == [s['policy_id'] for s in specs]
    with pytest.raises(ValueError):
        a._check_specs(specs + [specs[-1]])


def test_all_new_runtime_implementations_are_explicitly_source_bound():
    a = api()
    for name in ('planner', 'contracts', 'environment', 'selective_data', 'episode', 'model_order', 'closed_loop'):
        assert f'tools/autonomous_dispatch/{name}.py' in a.SOURCE_FILES


def test_allowed_hours_are_metadata_only_and_include_observed_endpoint():
    a = api()
    manifest = {'scenarios': [{'id': 'one', 'start_hour': 100, 'duration_steps': 40, 'step_minutes': 15},
                              {'id': 'unused', 'start_hour': 500, 'duration_steps': 40, 'step_minutes': 15}]}
    assert a.allowed_hours_for(manifest, ['one']) == list(range(100, 111))


def test_original_final_identity_is_checked_before_constructing_an_environment():
    a = api()
    assert a._sealed_days() == [21, 26, 59, 77, 92, 127, 140, 196, 264, 284, 344, 359]
    manifest = {'scenarios': [{'id': 'sealed', 'start_hour': 505, 'duration_steps': 40, 'step_minutes': 15}]}
    with pytest.raises(ValueError, match='sealed'):
        a._environment_factory({'scenario_ids': ['sealed']}, manifest)


def test_confirmation_cannot_run_without_real_freeze_receipt(tmp_path):
    a = api()
    with pytest.raises(ValueError, match='freeze'):
        a.check_confirmation_freeze({'phase': 'confirmation', 'policy_specs': []}, {}, tmp_path)


def test_isolated_analysis_validator_does_not_modify_old_module():
    module = importlib.import_module('tools.autonomous_dispatch.analysis')
    from tools.state_dispatch import analysis as old
    from hospital_ies.data_contract import validate_action as original
    namespace = module.analysis_namespace()
    assert old.validate_action is original
    assert namespace['validate_action']({'joint': {'options': {'variant': 'economic'}}})
    assert namespace['_decisions'].__globals__ is namespace
