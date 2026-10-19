"""v14 metadata boundaries, without annual input or trajectory reads."""
import copy
import importlib
import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def api():
    try:
        return importlib.import_module('tools.engineering_model.data_design')
    except ModuleNotFoundError:
        pytest.fail('v14 engineering metadata is missing')


def test_twenty_development_cases_keep_known_counterexamples_and_paired_clocks(monkeypatch):
    monkeypatch.setattr(pd, 'read_csv', lambda *a, **k: pytest.fail('Metadata parsed physical rows'))
    d = api()
    panels = d.build_proposals(ROOT)
    assert set(panels) == {'development', 'long_normal', 'confirmation'}
    panel = panels['development']
    assert len(panel['scenarios']) == 20
    assert Counter(s['analysis_class'] for s in panel['scenarios']) == dict(L1=3, L2=3, L3=3, L4b=6, L4a=2, N2=3)
    sources = {s['source_v13_scenario_id'] for s in panel['scenarios']}
    assert {'v13_dev_bf706b06d55bd34cf598f317', 'v13_dev_c8533f9d38c2794e63855d5b',
            'v13_dev_beb7909e5e1b15dc70df2378', 'v13_confirm_fd05755beda001695e3284f5',
            'v13_confirm_23c13f8480c004a5c3e96adc', 'v13_dev_855a255879c25d138e593434'} <= sources
    for day in (47, 129, 212):
        group = {s['analysis_class']: s for s in panel['scenarios'] if s['source_day_index'] == day}
        a, b = group['L1'], group['L3']
        for key in ('start_hour', 'base_state_id', 'initialization_seed', 'faults', 'exogenous_events',
                    'response_delay_minutes', 'pre_event_warmup_steps'):
            assert a[key] == b[key]
        assert not a['enable_propagation'] and b['enable_propagation']
        assert not group['L2']['enable_propagation'] and not group['L2']['faults']
    assert all(s['id'].startswith('v14_') for s in panel['scenarios'])
    assert panel['strict_final_gate'] == 'not_verified' and not panel['original_final12_evaluated']


def test_long_support_is_real_1440_and_never_expands_into_final_twelve():
    d = api(); panels = d.build_proposals(ROOT)
    long = panels['long_normal']
    assert len(long['scenarios']) == 3
    assert {s['source_day_index'] for s in long['scenarios']} == {2, 47, 282}
    for s in long['scenarios']:
        assert s['duration_steps'] * s['step_minutes'] == 1440
        assert s['full_source_support'] == d.validate_observation_support(s)
        assert s['new_registered_support_window'] and not s['new_independent_background']
        assert s['physical_duplicate_of'] is None
        d.validate_sealed_support(s)
    bad = copy.deepcopy(next(s for s in long['scenarios'] if s['source_day_index'] == 282))
    bad['duration_steps'] = 192
    bad['full_source_support'] = d.validate_observation_support(bad)
    with pytest.raises(ValueError, match='[Ss]ealed'):
        d.validate_sealed_support(bad)


def test_final_eight_have_one_preregistered_new_phase_and_no_selection():
    d = api(); panels = d.build_proposals(ROOT)
    dev = {s['source_v13_scenario_id']: s for s in panels['development']['scenarios']}
    final = panels['confirmation']
    assert len(final['scenarios']) == 8
    assert Counter(s['analysis_class'] for s in final['scenarios']) == dict.fromkeys(('L1', 'L2', 'L3', 'L4b'), 2)
    for s in final['scenarios']:
        old = dev[s['source_v13_scenario_id']]
        assert s['experiment_role'] == 'confirmation' and s['requires_policy_freeze']
        assert not s['selection_allowed'] and not s['fit_allowed'] and not s['is_final_blind']
        assert s['start_hour'] == old['start_hour'] and s['full_source_support'] == old['full_source_support']
        for event, previous in zip(s['exogenous_events'], old['exogenous_events']):
            assert event['start_minute'] == previous['start_minute'] - 6.25
            assert event['end_minute'] == previous['end_minute'] - 6.25
            assert event.get('shock') == previous.get('shock')
    assert final['new_physical_spec_count'] == 8 and final['new_independent_background_count'] == 0


def test_manifest_explicitly_binds_new_model_without_relabelling_m0(monkeypatch):
    d = api(); proposal = d.build_proposals(ROOT)['development']
    base = dict(signature='old', simulator_hash='s', execution_contract_id='e', assumption_set_id='a',
                execution_contract={'name': 'legacy'}, schemas={'action': 'a'}, splits={}, scenarios=proposal['scenarios'])
    monkeypatch.setattr(d.legacy, 'build_manifest', lambda *a, **k: copy.deepcopy(base))
    config = {'gt_start_delay_minutes': 5., 'min_up_minutes': 30.}
    manifest = d.build_manifest(proposal, {'new.py': 'a' * 64}, 'M14_engineering', config, root=ROOT)
    assert manifest['execution_model_id'] == 'M14_engineering'
    assert manifest['model_config'] == config and manifest['model_config_signature'] == d.digest(config)
    assert manifest['signature'] == d.digest({k: v for k, v in manifest.items() if k != 'signature'})
    assert manifest['engineering_model_contract_id']
    assert manifest['planning_interface_id'] == 'engineering_model.ordinary_joint_command.v14'
    assert manifest['schemas']['action'] == 'engineering_model.engineering_joint.v14'
    suffix = '__v14_' + d.digest(config)[:16]
    assert manifest['execution_contract_id'] == 'e' + suffix
    assert manifest['assumption_set_id'] == 'a' + suffix
    assert manifest['legacy_execution_contract_id'] == 'e' and manifest['legacy_assumption_set_id'] == 'a'
    assert manifest['legacy_simulator_hash'] == 's'
    assert manifest['simulator_hash'] == d.digest({'new.py': 'a' * 64})
    unchanged = d.build_manifest(proposal, {'new.py': 'a' * 64}, 'M0', {}, root=ROOT)
    assert unchanged['execution_contract_id'] == 'e' and unchanged['assumption_set_id'] == 'a'
    with pytest.raises(ValueError, match='model'):
        d.build_manifest(proposal, {'new.py': 'a' * 64}, 'unregistered_model', config, root=ROOT)


def test_proposal_cli_creates_only_signed_json_and_keeps_existing_bytes(tmp_path):
    d = api(); destination = tmp_path / 'design'
    assert d.main(['--output', str(destination), '--root', str(ROOT)]) == 0
    path = destination / 'DEVELOPMENT_PROPOSAL.json'
    before = path.read_bytes()
    assert json.loads(before)['signature']
    assert len(list(destination.glob('*.json'))) == 4
    with pytest.raises(FileExistsError):
        d.main(['--output', str(destination), '--root', str(ROOT)])
    assert path.read_bytes() == before and not list(destination.glob('*.gz'))
