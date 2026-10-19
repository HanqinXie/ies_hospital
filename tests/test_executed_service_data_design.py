"""The v13 panel is metadata only; old physical inputs/results stay read-only."""
import copy
import importlib
import json
from collections import Counter
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def api():
    try:
        return importlib.import_module('tools.executed_service.data_design')
    except ModuleNotFoundError:
        pytest.fail('The independent executed-service metadata generator is missing')


def deny_physical_csv(*args, **kwargs):
    pytest.fail('Panel registration must not parse annual physical CSV rows')


def read_source(name):
    return json.loads((ROOT / 'outputs/v12_service_switch_20261017_run01/design' / name).read_text(encoding='utf-8'))


def physical(d, scene):
    return {key: scene.get(key) for key in d.PHYSICAL_FIELDS}


def test_development_is_exact_disclosed_twelve_plus_two_existing_six_class_roots(monkeypatch):
    monkeypatch.setattr(pd, 'read_csv', deny_physical_csv)
    d = api()
    source = read_source('TARGETED_DEVELOPMENT_PROPOSAL.json')
    distributed = read_source('DISTRIBUTED_DEVELOPMENT_PROPOSAL.json')
    expected = source['scenarios'] + [s for s in distributed['scenarios'] if s['source_day_index'] in (129, 212)]
    before = copy.deepcopy(expected)
    proposal = d.build_development(ROOT)
    assert len(proposal['scenarios']) == 24 and len(proposal['roots']) == 8
    assert Counter(s['analysis_class'] for s in proposal['scenarios']) == dict(L1=4, L2=3, L3=3, L4a=2, L4b=9, N2=3)
    original = {s['id']: s for s in expected}
    assert {s['source_v12_scenario_id'] for s in proposal['scenarios']} == set(original)
    for scene in proposal['scenarios']:
        old = original[scene['source_v12_scenario_id']]
        assert physical(d, scene) == physical(d, old)
        assert scene['exogenous_stream_id'] == old['exogenous_stream_id']
        assert scene['condition_id'] == old.get('condition_id')
        assert scene['source_role_metadata']['condition_id_present'] == ('condition_id' in old)
        assert scene['experiment_role'] == 'development' and scene['selection_allowed']
        assert not scene['fit_allowed'] and not scene['requires_policy_freeze']
        assert scene['previously_exposed_v12'] and not scene['is_new_physical_spec']
    assert expected == before
    assert proposal['planned_pair_count'] == 72 and proposal['planned_policy_count'] == 3
    assert proposal['resource_limits']['max_actual_attempts'] == 480


def test_confirmation_has_forty_new_event_coordinates_and_eight_explicit_normal_repeats(monkeypatch):
    monkeypatch.setattr(pd, 'read_csv', deny_physical_csv)
    d = api()
    proposal = d.build_confirmation(ROOT)
    distributed = read_source('DISTRIBUTED_DEVELOPMENT_PROPOSAL.json')
    source = {s['id']: s for s in distributed['scenarios']}
    assert len(proposal['scenarios']) == 48 and len(proposal['roots']) == 8
    assert Counter(s['analysis_class'] for s in proposal['scenarios']) == dict.fromkeys(d.CLASSES, 8)
    assert {s['source_day_index'] for s in proposal['scenarios']} == {2, 3, 123, 147, 222, 234, 282, 301}
    assert proposal['new_physical_spec_count'] == 40 and proposal['normal_physical_repeat_count'] == 8
    assert proposal['planned_pair_count'] == 240 and proposal['total_planned_pair_count'] == 312
    assignment = {}
    for scene in proposal['scenarios']:
        old = source[scene['source_v12_scenario_id']]
        assert scene['full_source_support'] == old['full_source_support']
        assert scene['start_hour'] == old['start_hour'] and scene['base_state_id'] == old['base_state_id']
        assert scene['condition_id'] == old.get('condition_id')
        assert scene['experiment_role'] == 'confirmation' and scene['requires_policy_freeze']
        assert not scene['selection_allowed'] and not scene['fit_allowed']
        assert scene['previously_exposed_v12'] and not scene['new_independent_background']
        d.validate_scenario(scene)
        if scene['analysis_class'] == 'N2':
            assert physical(d, scene) == physical(d, old)
            assert scene['physical_duplicate_of'] == old['id'] and not scene['is_new_physical_spec']
            assert scene['exogenous_stream_id'] == old['exogenous_stream_id']
        else:
            assert physical(d, scene) != physical(d, old) and scene['is_new_physical_spec']
            assert scene['exogenous_stream_id'] != old['exogenous_stream_id']
            for before, after in zip(old['exogenous_events'], scene['exogenous_events']):
                assert after['start_minute'] - before['start_minute'] == scene['recipe']['phase_delta_minutes']
                assert after['end_minute'] <= 480
        assignment.setdefault(scene['root_family_id'], set()).add(scene['recipe']['id'])
    assert all(len(values) == 1 for values in assignment.values())
    assert Counter(proposal['root_recipe_assignments'].values()) == {r['id']: 4 for r in proposal['recipes']}
    assert proposal['strict_final_gate'] == 'not_verified' and not proposal['original_final12_evaluated']
    assert proposal['source_load_values_read'] is False and proposal['raw_or_result_files_read'] is False


def test_confirmation_rejects_seen_recipe_and_out_of_support_tail(monkeypatch):
    monkeypatch.setattr(pd, 'read_csv', deny_physical_csv)
    d = api()
    repeated = copy.deepcopy(d.CONFIRMATION_RECIPES)
    repeated[0] = dict(id='seen', phase_delta_minutes=6.25, shock_scale=1., duration_scale=1.)
    with pytest.raises(ValueError, match='seen|duplicate|coordinate'):
        d.build_confirmation(ROOT, recipes=repeated)
    outside = copy.deepcopy(d.CONFIRMATION_RECIPES)
    outside[0]['duration_scale'] = 10.
    with pytest.raises(ValueError, match='support|tail|horizon'):
        d.build_confirmation(ROOT, recipes=outside)


def test_manifest_preserves_old_builder_contracts_and_rejects_final12_support(monkeypatch):
    monkeypatch.setattr(pd, 'read_csv', deny_physical_csv)
    d = api()
    proposal = d.build_development(ROOT)
    seen = []
    def builder(root, scenes, policies, contract, schemas, options, splits):
        seen.append(copy.deepcopy((scenes, contract, schemas, options, splits)))
        return dict(scenarios=copy.deepcopy(scenes), policies=policies, execution_contract=contract,
                    schemas=schemas, options=options, splits=splits, simulator_hash='same-physics',
                    execution_contract_id='same-contract')
    monkeypatch.setattr(d.legacy, '_build_manifest', builder)
    options = dict(source_sha256={'new_runtime.py': 'a' * 64}, planning_interface_id='executed_service.v13',
                   action_schema='executed_service.action.v13', extra_contracts={'service_history_contract_id': 'test.v1'},
                   policies=('fixed', 'feedback', 'zero'), workspace_root=ROOT)
    manifest = d.build_manifest(proposal['scenarios'], proposal['roots'], **options)
    assert len(seen) == 1 and manifest['study_name'] == 'hospital_ies_executed_service_v13'
    assert manifest['resource_limits']['max_new_physical_specs'] == 80
    assert manifest['service_history_contract_id'] == 'test.v1'
    assert manifest['signature'] == d.digest({k: v for k, v in manifest.items() if k != 'signature'})
    bad = copy.deepcopy(proposal['scenarios'][0])
    bad['start_hour'] = 21 * 24 + 1
    bad['full_source_support'] = d.validate_observation_support(bad)
    with pytest.raises(ValueError, match='[Ss]ealed|support'):
        d.build_manifest([bad], proposal['roots'], **options)
