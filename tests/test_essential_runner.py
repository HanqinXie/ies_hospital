"""v16 registration, evidence size and same-prefix design; no physical batch."""
import copy
import csv
import importlib
import json
from pathlib import Path
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


@pytest.fixture
def tmp_path():
    """Runner evidence must stay on D even when pytest's global temp is C."""
    path = ROOT / 'outputs/v16_essential_engineering_20261017_run01/test_tmp' / ('runner_fixture_' + uuid.uuid4().hex)
    path.mkdir(parents=True, exist_ok=False)
    return path


def api(module='runner'):
    try:
        return importlib.import_module('tools.essential_fixes.' + module)
    except ModuleNotFoundError as exc:
        pytest.fail('Missing v16 runner/design: ' + str(exc), pytrace=False)


def proposal():
    from tools.operating_response.data_design import normal_proposal
    return normal_proposal()


def test_v16_policies_keep_original_e_k1_k2_identities():
    m = api()
    from tools.operating_response import runner as old
    assert set(m.policy_specs()) == {'E', 'K1', 'K2'}
    for key, spec in m.policy_specs().items():
        assert spec == old.policy_specs()[key]
        assert type(m.factory(spec)) is type(old.factory(spec))
    changed = copy.deepcopy(m.policy_specs()['E'])
    changed['config']['p0_preparation_before_commitment'] = False
    with pytest.raises(ValueError, match='[Ff]rozen|[Ii]dentity'):
        m.factory(changed)


@pytest.mark.parametrize('key', ('K1', 'K2'))
def test_known_k_policy_identity_cannot_be_reused_with_changed_configuration(key):
    m = api()
    changed = copy.deepcopy(m.policy_specs()[key])
    changed['config']['preparation_profile'] = 'K0'
    with pytest.raises(ValueError, match='[Ff]rozen|[Ii]dentity'):
        m.factory(changed)


def test_new_run_registration_is_immutable_and_independent_of_old_budget(tmp_path, monkeypatch):
    m = api()
    monkeypatch.setattr(m, 'RUN', tmp_path / 'v16')
    p = proposal()
    before = copy.deepcopy(p)
    directory = m.RUN / 'experiments' / 'one'
    plan = m.register(p, directory, [m.policy_specs()['E']], model_id='M15_local_response',
                      model_config={}, scenario_ids=[p['scenarios'][0]['id']])
    assert plan['registered_pairs'] == 1
    assert plan['canonical_run'] == str(m.RUN.resolve())
    assert plan['resource_limits']['max_actual_attempts'] == 600
    manifest = m.read(directory / 'manifest.json')
    assert manifest['manifest_version'].startswith('hospital_ies.essential_metadata.v16')
    assert manifest.get('policy_specs_sha256') == m.digest(plan['policy_specs'])
    assert p == before
    assert 'tools/essential_fixes/runner.py' in plan['source_sha256']
    with pytest.raises(FileExistsError):
        m.register(p, directory, [m.policy_specs()['E']], model_id='M15_local_response', model_config={})
    m.write(m.RUN / 'experiments' / 'budget' / 'plan.json', {'registered_pairs': 599})
    with pytest.raises(ValueError, match='[Cc]eiling|[Bb]udget'):
        m.register(p, m.RUN / 'experiments' / 'over', [m.policy_specs()['E']],
                   model_id='M15_local_response', model_config={}, scenario_ids=[p['scenarios'][0]['id']])
    assert not (m.RUN / 'experiments' / 'over').exists()


def test_m16_manifest_binds_new_model_and_preserves_the_explicit_legacy_contract(tmp_path, monkeypatch):
    m = api()
    monkeypatch.setattr(m, 'RUN', tmp_path / 'v16')
    p = proposal()
    directory = m.RUN / 'experiments' / 'm16'
    plan = m.register(p, directory, [m.policy_specs()['E']], model_id='M16_essential',
                      model_config={}, scenario_ids=[p['scenarios'][0]['id']])
    manifest = m.read(directory / 'manifest.json')
    assert manifest['execution_model_id'] == 'M16_essential'
    assert manifest['model_config'] == plan['model_config']
    assert manifest['model_config']['gt_floor_correction'] == {'enabled': True}
    assert manifest['execution_contract_id'].startswith(manifest['legacy_execution_contract_id'] + '__v16_')
    assert manifest['engineering_model_contract_id'] == 'hospital_ies.essential_engineering.v16.v1'
    assert manifest.get('study_contract_id') == 'hospital_ies.essential_engineering.v16.v1'
    assert '__v15_' not in manifest['execution_contract_id']


@pytest.mark.parametrize('model_id', ('M14_engineering', 'M15_local_response', 'M0'))
def test_reference_manifest_declares_actual_model_contract_separately_from_v16_study(model_id):
    d = api('data_design')
    m = api()
    from tools.engineering_model.execution import DYNAMICS_CONTRACT_ID as M14_CONTRACT
    from tools.operating_response.execution import DYNAMICS_CONTRACT_ID as M15_CONTRACT
    from tools.engineering_model.environment import model_contract as legacy_contract
    expected = {'M14_engineering': M14_CONTRACT, 'M15_local_response': M15_CONTRACT,
                'M0': legacy_contract('M0', {})[0]['name']}[model_id]
    _, config = m.model_contract(model_id, {})
    manifest = d.build_manifest(proposal(), m.source_binding(), model_id, config,
                                policies=[m.policy_specs()['E']['policy_id']])
    assert manifest['engineering_model_contract_id'] == expected
    assert manifest.get('study_contract_id') == 'hospital_ies.essential_engineering.v16.v1'


def test_no_c_outside_run_source_drift_or_completed_overwrite(tmp_path, monkeypatch):
    m = api()
    monkeypatch.setattr(m, 'RUN', tmp_path / 'v16')
    with pytest.raises(ValueError):
        m.check_output_path(Path('C:/v16_forbidden_data'))
    with pytest.raises(ValueError, match='[Cc]anonical|[Ee]xperiment'):
        m.register(proposal(), tmp_path / 'outside', [m.policy_specs()['E']],
                   model_id='M15_local_response', model_config={})
    p = proposal()
    directory = m.RUN / 'experiments' / 'drift'
    m.register(p, directory, [m.policy_specs()['E']], model_id='M15_local_response',
               model_config={}, scenario_ids=[p['scenarios'][0]['id']])
    monkeypatch.setattr(m, 'source_binding', lambda: {'changed.py': 'b' * 64})
    monkeypatch.setattr(m, 'OperatingHospitalIESEnv', lambda *a, **kw: pytest.fail('environment before source guard'))
    with pytest.raises(ValueError, match='[Ss]ource|[Cc]hanged'):
        m.run_batch(directory)
    assert not (directory / 'ATTEMPTS.jsonl').exists()


@pytest.mark.parametrize('mutation', ('model_id', 'model_config', 'policy_specs', 'scenario_ids', 'registered_pairs'))
def test_changed_plan_physics_or_policy_is_rejected_before_environment(tmp_path, monkeypatch, mutation):
    m = api()
    monkeypatch.setattr(m, 'RUN', tmp_path / 'v16')
    p = proposal()
    directory = m.RUN / 'experiments' / 'changed_plan'
    m.register(p, directory, [m.policy_specs()['K1']], model_id='M16_essential',
               model_config={}, scenario_ids=[p['scenarios'][0]['id']])
    plan = m.read(directory / 'plan.json')
    if mutation == 'model_id': plan['model_id'] = 'M14_engineering'
    elif mutation == 'model_config': plan['model_config']['gt_dynamics']['minimum_up_minutes'] = 61.
    elif mutation == 'policy_specs': plan['policy_specs'][0]['config']['preparation_profile'] = 'K2'
    elif mutation == 'scenario_ids': plan['scenario_ids'] = [p['scenarios'][1]['id']]
    else: plan['registered_pairs'] = 2
    (directory / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
    monkeypatch.setattr(m, 'OperatingHospitalIESEnv',
                        lambda *a, **kw: pytest.fail('Unbound changed plan reached physical environment'))
    with pytest.raises(ValueError, match='[Rr]egistration|[Mm]anifest|[Bb]inding|[Pp]lan'):
        m.run_batch(directory)
    assert not (directory / 'ATTEMPTS.jsonl').exists()
    assert not (directory / 'pairs').exists()


def test_compact_analysis_keeps_windows_metrics_and_unknown_without_duplicate_rows():
    m = api()
    original = dict(windows={'registered_full': {'complete': False, 'observed_prefix': {'ENS': 2.}}},
                    unknown_tail_minutes=[15., 30.],
                    engineering={'gt': {'GT_1': {'runs': [{'start_minute': 0.}], 'count': 1}},
                                 'gt_transitions': [{'time_minute': 0.}]},
                    timelines={'events': [{'kind': 'fault'}], 'actual_intervals': [{'load': 12.}]},
                    model_domain={'termination': {'message': 'budget boundary', 'diagnostic': {
                        'events': [{'kind': 'fault'}], 'completed_segments': [{'load': 12.}],
                        'fuel_remaining': 0.}}})
    saved = copy.deepcopy(original)
    compact = m.compact_analysis(original)
    assert original == saved
    assert compact['windows'] == original['windows']
    assert compact['unknown_tail_minutes'] == [15., 30.]
    assert compact['timeline_row_counts'] == {'events': 1, 'actual_intervals': 1}
    assert 'timelines' not in compact
    assert 'runs' not in compact['engineering']['gt']['GT_1']
    assert compact['engineering']['gt']['GT_1']['count'] == 1
    termination = compact['model_domain']['termination']
    assert termination['message'] == 'budget boundary'
    assert termination['diagnostic']['fuel_remaining'] == 0.
    assert 'completed_segments' not in termination['diagnostic']
    assert termination['full_details_in_raw'] is True


def test_local_a_cases_preserve_physical_prefix_and_do_not_replace_normal_demand():
    d = api('data_design')
    p = d.local_a_proposal()
    assert len(p['scenarios']) == 9
    assert len({s['id'] for s in p['scenarios']}) == 9
    for day, source_path in ((315, 'FROZEN_INCIDENTS_PROPOSAL.json'), (280, 'DEV_V2_PROPOSAL.json')):
        old = json.loads((ROOT / 'outputs/v15_engineering_operation_20261017_run01/design' / source_path).read_text(encoding='utf8'))
        source = next(s for s in old['scenarios'] if s['source_day_index'] == day and s['analysis_class'] == 'L2')
        cases = [s for s in p['scenarios'] if s['source_day_index'] == day]
        assert len(cases) == 4
        for scene in cases:
            for key in ('start_hour', 'duration_steps', 'pre_event_warmup_steps', 'initialization_seed',
                        'grid_limit_kW', 'pre_event_grid_limit_kW', 'response_delay_minutes'):
                assert scene[key] == source[key]
            assert not scene['faults'] and not scene['enable_propagation']
            assert scene['source_v15_scenario_id'] == source['id']
            assert scene['is_final_blind'] is False
        shocked = [s for s in cases if s['shock']]
        assert sorted(s['phase_delta_minutes'] for s in shocked) == [-2.5, 0., 2.5]
        for scene in shocked:
            assert scene['shock'] == source['shock']
            assert scene['first_event_minute'] == source['first_event_minute'] + scene['phase_delta_minutes']
            event = scene['exogenous_events'][0]
            assert event['start_minute'] == scene['first_event_minute']
            assert event['end_minute'] - event['start_minute'] == source['shock_duration_minutes']
        normal = next(s for s in cases if not s['shock'])
        assert normal['exogenous_events'] == [] and normal['first_event_minute'] is None
        assert normal['analysis_class'] == 'N2'
    good = next(s for s in p['scenarios'] if s['source_day_index'] == 10)
    assert good['duration_steps'] == 96 and not good['exogenous_events']


def test_registered_full_status_survives_compaction_and_is_not_legacy600(tmp_path, monkeypatch):
    m = api()
    monkeypatch.setattr(m, 'RUN', tmp_path / 'v16')
    p = proposal()
    directory = m.RUN / 'experiments' / 'unknown'
    m.register(p, directory, [m.policy_specs()['E']], model_id='M15_local_response',
               model_config={}, scenario_ids=[p['scenarios'][0]['id']])
    class NoPhysics:
        def __init__(self, *a, **kw): self.model_id = kw['model_id']
    monkeypatch.setattr(m, 'OperatingHospitalIESEnv', NoPhysics)
    def fake_episode(env, scene, policy, seed, manifest):
        return dict(scenario=scene, initial_state={'state': {}}, segments=[],
                    summary={'status': 'model_domain_unknown', 'observed_until_minute': 600., 'requested_end_minute': 1440.},
                    error={'type': 'RealUnknown'})
    monkeypatch.setattr(m, 'run_episode', fake_episode)
    monkeypatch.setattr(m, 'analyze_payload', lambda p, e: dict(observed_until_minute=600., requested_end_minute=1440.,
        windows={'full': {'complete': True}, 'registered_full': {'complete': False}}, timelines={'events': []}))
    monkeypatch.setattr(m, 'export_rows', lambda records, out: {'synthetic_protocol_test_only': True, 'count': len(records)})
    result = m.run_batch(directory)
    assert result['actual_attempts'] == 1 and result['complete_full'] == 0 and result['unknown_full'] == 1
    assert result['complete_legacy_full600'] == 1
    record = m.read(next((directory / 'pairs').glob('*/RESULT.json')))
    assert record['error']['type'] == 'RealUnknown'
    assert record['analysis']['windows']['registered_full']['complete'] is False
    assert 'timelines' not in record['analysis']
    assert next((directory / 'pairs').glob('*/trajectory.json.gz')).exists()
    with pytest.raises(FileExistsError): m.run_batch(directory)


def test_exporter_preserves_service_and_command_rows_without_large_json_duplicate(tmp_path):
    m = api()
    window = dict(complete=False, observed_minutes=15., window_minutes=[0., 30.],
                  all_registered=None, observed_prefix={'P0_failure_minutes': 2.5},
                  original_19={'target_names': ['target_1'], 'targets': [3.], 'target_mask': [True]})
    analysis = dict(windows={'full': copy.deepcopy(window), 'registered_full': copy.deepcopy(window)},
        engineering={'gt': {'GT_1': {'runs': [{'start_minute': 0., 'observed_until_minute': 15.}]}},
                     'gt_transitions': [{'kind': 'activated', 'time_minute': 0.}]},
        timelines={'command_lifecycle': [{'command_id': 1, 'generation_minute': 0., 'executed_minute': 2.5}],
                   'execution_intervals': [{'command_id': 1, 'start_minute': 2.5, 'end_minute': 15.}],
                   'events': [{'kind': 'device_fault', 'time_minute': 10.}],
                   'actual_intervals': [{'start_minute': 0., 'end_minute': 15., 'action': {'grid_import_kW': 100.}}],
                   'diesel_local_response': [], 'clamp_events': []},
        thermal_coverage={'P3': {'status': 'not_modeled'}},
        model_domain={'registered_full_known': False, 'termination': {'message': 'explicit unknown'}})
    record = dict(scenario_id='synthetic_export_only', policy_id='E', error={'message': 'explicit unknown'},
                  analysis=analysis)
    original = copy.deepcopy(record)
    out = tmp_path / 'export'
    receipt = m.export_rows([record], out)
    assert record == original
    assert receipt['table_row_counts']['TRAJECTORY_WINDOWS'] == 2
    assert receipt['table_row_counts']['COMMAND_LIFECYCLE'] == 1
    assert receipt['unknown_registered_full_trajectory_count'] == 1
    compact = m.read(out / 'RESULTS.json')['records'][0]['analysis']
    assert compact['windows'] == analysis['windows'] and 'timelines' not in compact
    with (out / 'COMMAND_LIFECYCLE.csv').open(encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row['command_id'] == '1' and row['executed_minute'] == '2.5'
    with pytest.raises(FileExistsError): m.export_rows([record], out)
