"""Finite read-only analysis guards; no scientific process is launched."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent


def module():
    path = HERE / 'normal_window_analysis.py'
    assert path.is_file(), 'Normal engineering extraction must have explicit terminal/report/matching guards'
    if '_normal_window_analysis_tested' not in sys.modules:
        spec = importlib.util.spec_from_file_location('_normal_window_analysis_tested', path)
        value = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = value
        spec.loader.exec_module(value)
    return sys.modules['_normal_window_analysis_tested']


def test_partial_normal_cannot_be_a_complete_normal_panel():
    states = [dict(batch=f'v20_normal_{i:03d}', terminal_exists=i < 11, attempt_exists=i < 12) for i in range(12)]
    value = module().assess_readiness(states, normal_command=None, report_command=None, report_exists=False, supervisor=None)
    assert value['ready'] is False and value['state'] == 'waiting_normal_terminal'
    assert value['pending_batches'] == ['v20_normal_011']


def test_normal_twelve_terminal_files_do_not_substitute_for_final_report_receipt():
    states = [dict(batch=f'v20_normal_{i:03d}', terminal_exists=True, attempt_exists=True) for i in range(12)]
    value = module().assess_readiness(states, normal_command={'exit_code': 0}, report_command=None, report_exists=False, supervisor=None)
    assert value['ready'] is False and value['state'] == 'waiting_FINAL01_report'


@pytest.mark.parametrize('failure', ['normal_exit', 'report_exit', 'supervisor'])
def test_failed_external_stage_is_not_silently_retried_or_simulated(failure):
    states = [dict(batch=f'v20_normal_{i:03d}', terminal_exists=True, attempt_exists=True) for i in range(12)]
    normal = dict(exit_code=0); report = dict(exit_code=0); supervisor = dict(status='passed')
    if failure == 'normal_exit': normal['exit_code'] = 2
    if failure == 'report_exit': report['exit_code'] = 1
    if failure == 'supervisor': supervisor['status'] = 'failed_unknown'
    value = module().assess_readiness(states, normal_command=normal, report_command=report, report_exists=True, supervisor=supervisor)
    assert value['ready'] is False and value['state'] == 'blocked_external_terminal'


def test_synthetic_ready_state_is_only_permission_to_validate_not_actual_result():
    states = [dict(batch=f'v20_normal_{i:03d}', terminal_exists=True, attempt_exists=True) for i in range(12)]
    value = module().assess_readiness(states, normal_command={'exit_code': 0}, report_command={'exit_code': 0}, report_exists=True, supervisor={'status': 'passed'})
    assert value['ready'] is True and value['state'] == 'ready_for_readonly_validation'


def panel_fixture():
    jobs = []; proofs = []
    for scene, hours in enumerate([24, 48, 72, 24]):
        for index, alias in enumerate(['E', 'K1', 'C19']):
            name = f'v20_normal_{scene * 3 + index:03d}'
            jobs.append(dict(name=name, policies=[alias], scene=dict(id=f'synthetic_{scene}', normal_duration_hours=hours, duration_steps=hours * 4, step_minutes=15.)))
            proofs.append(dict(batch=name, scenario_id=f'synthetic_{scene}', window_minutes=[0., hours * 60.],
                registered_scene_sha256='a' * 64, source_binding_sha256='b' * 64, model_config_sha256='c' * 64, runtime_inputs_sha256='d' * 64,
                matching=dict(actual_initial_state_sha256='e' * 64, registered_stream_sha256='f' * 64)))
    return jobs, proofs


def test_four_windows_have_exactly_twelve_cross_policy_comparisons():
    jobs, proofs = panel_fixture()
    rows = module().validate_panel_matches(jobs, proofs)
    assert len(rows) == 12 and len({x['scenario_id'] for x in rows}) == 4


@pytest.mark.parametrize('field', ['reset', 'stream', 'source_binding_sha256', 'model_config_sha256', 'runtime_inputs_sha256', 'registered_scene_sha256', 'window'])
def test_single_policy_terminal_matching_is_insufficient_across_panels(field):
    jobs, proofs = panel_fixture()
    if field == 'reset': proofs[0]['matching']['actual_initial_state_sha256'] = '0' * 64
    elif field == 'stream': proofs[0]['matching']['registered_stream_sha256'] = '0' * 64
    elif field == 'window': proofs[0]['window_minutes'] = [0., 600.]
    else: proofs[0][field] = '0' * 64
    with pytest.raises(ValueError): module().validate_panel_matches(jobs, proofs)


@pytest.mark.parametrize('mutation', ['duplicate', 'legacy600', 'wrong_end', 'unknown', 'not_eligible'])
def test_only_twelve_unique_registered_full_primary_rows_can_be_extracted(mutation):
    jobs, _ = panel_fixture()
    rows = [dict(batch=j['name'], scenario_id=j['scene']['id'], alias=j['policies'][0], cohort='normal',
                 is_primary_window='True', window='registered_full', window_start_minute='0.0',
                 window_end_minute=str(j['scene']['normal_duration_hours'] * 60.), complete='True', comparison_eligible='True') for j in jobs]
    if mutation == 'duplicate': rows[-1] = copy.deepcopy(rows[0])
    if mutation == 'legacy600': rows[0]['window'] = 'full'
    if mutation == 'wrong_end': rows[0]['window_end_minute'] = '600'
    if mutation == 'unknown': rows[0]['complete'] = 'False'
    if mutation == 'not_eligible': rows[0]['comparison_eligible'] = 'False'
    with pytest.raises(ValueError): module().select_normal_primary(rows, jobs)


def test_unknown_M20_resource_samples_are_unknown_not_zero_or_old_estimates():
    result = dict(batch='v20_normal_000', measurements_are_M20=True, resource_samples=1, unknown_process_samples=1,
                  sampled_private_peak_bytes=None, sampled_working_set_peak_bytes=None, observed_OS_lifetime_peak_working_set_bytes=None)
    samples = [dict(batch='v20_normal_000', process_sample_status='unknown', private_bytes=None, working_set_bytes=None, OS_lifetime_peak_working_set_bytes=None)]
    profile = dict(source_model_id='M19_model_closeout', sampled_private_peak_bytes=100, OS_lifetime_peak_working_set_bytes=80)
    row = module().resource_projection(result, samples, profile)
    assert row['new_M20_sampled_private_peak_bytes'] is None and row['old_M19_observed_private_peak_bytes'] == 100
    assert row['unknown_process_samples'] == 1 and row['admission_margin_modified'] is False


def test_foreign_resource_model_is_not_a_new_M20_measurement():
    with pytest.raises(ValueError): module().resource_projection(dict(measurements_are_M20=False), [], {})


def test_absent_sampling_evidence_stays_unknown_without_blocking_scientific_service_tables():
    row = module().unknown_resource('v20_normal_000', {'source_model_id': 'M19_model_closeout', 'sampled_private_peak_bytes': 100}, 'no saved new sampling result')
    assert row['new_M20_sampled_private_peak_bytes'] is None
    assert row['resource_samples'] is None and row['new_M20_resource_status'] == 'unknown'
    assert row['old_M19_observed_private_peak_bytes'] == 100


def row_binding_fixture():
    job = dict(name='v20_normal_000', policies=['E'], scene=dict(id='synthetic_scene'))
    path = module().RUN / 'experiments/v20_normal_000/pairs/synthetic_scene__synthetic_E/RESULT.json'
    proof = dict(evidence_files=[dict(path=path.relative_to(module().ROOT).as_posix(), sha256='a' * 64)],
                 source_binding_sha256='b' * 64, model_config_sha256='c' * 64, runtime_inputs_sha256='d' * 64,
                 matching=dict(actual_initial_state_sha256='e' * 64, registered_stream_sha256='f' * 64))
    row = dict(result_path=str(path), result_sha256='a' * 64, policy_id='synthetic_E', model_id='M20_load_accounting',
               source_binding_sha256='b' * 64, model_config_sha256='c' * 64, runtime_inputs_sha256='d' * 64,
               actual_initial_state_sha256='e' * 64, actual_exogenous_sha256='f' * 64)
    return row, job, proof


def test_report_row_is_bound_to_the_actual_qualified_RESULT():
    row, job, proof = row_binding_fixture()
    assert module().qualified_result_path(row, job, proof, 'synthetic_E').name == 'RESULT.json'


@pytest.mark.parametrize('field', ['result_path', 'result_sha256', 'policy_id', 'actual_initial_state_sha256', 'actual_exogenous_sha256', 'source_binding_sha256'])
def test_report_cannot_redirect_to_an_unqualified_saved_record(field):
    row, job, proof = row_binding_fixture()
    row[field] = str(module().ROOT / 'other/RESULT.json') if field == 'result_path' else '0' * 64
    with pytest.raises(ValueError): module().qualified_result_path(row, job, proof, 'synthetic_E')


def test_actual_registration_preflight_has_twelve_immutable_normal_batches():
    value = module().registration_preflight()
    assert value['normal_batches'] == 12 and value['normal_windows'] == 4
    assert sorted(value['window_hours']) == [24., 24., 48., 72.]
    assert value['physical_episodes_started'] == 0


@pytest.mark.parametrize('field', ['script_sha256', 'release_gate_sha256', 'normal_helper_sha256', 'frozen_report_script_sha256'])
def test_prepared_external_dependencies_cannot_drift_before_collection(field):
    prepared = {name: 'a' * 64 for name in ['script_sha256', 'release_gate_sha256', 'normal_helper_sha256', 'frozen_report_script_sha256']}
    current = copy.deepcopy(prepared); current[field] = 'b' * 64
    with pytest.raises(ValueError): module().validate_prepared_dependencies(prepared, current)


def old_identity_fixture():
    spec = dict(policy_id='old_E', config=dict(mode='original'), kind='engineering')
    manifest = dict(execution_model_id='M19_model_closeout', scenarios=[dict(id='old_scene')],
                    policies=['priority_milp', 'secure_milp'], signature='s', model_config_signature='c',
                    policy_specs_sha256=module().canonical_digest([spec]))
    plan = dict(model_id='M19_model_closeout', policy_specs=[spec], scenario_ids=['old_scene'], registered_pairs=1)
    record = dict(model_id='M19_model_closeout', scenario_id='old_scene', policy_id='old_E',
                  manifest_signature='s', model_config_signature='c', error=None, analysis_error=None)
    frozen = dict(model_id='M19_model_closeout', policies=dict(E=spec))
    return record, plan, manifest, frozen


def test_old_actual_policy_spec_is_authoritative_over_legacy_generic_manifest_names():
    record, plan, manifest, frozen = old_identity_fixture()
    module().validate_old_identity(record, plan, manifest, frozen, 'old_scene', 'E')


@pytest.mark.parametrize('field', ['model_id', 'scenario_id', 'policy_id', 'manifest_signature', 'model_config_signature', 'error'])
def test_old_result_cannot_borrow_another_panel_qualification(field):
    record, plan, manifest, frozen = old_identity_fixture()
    record[field] = {} if field == 'error' else 'foreign'
    with pytest.raises(ValueError): module().validate_old_identity(record, plan, manifest, frozen, 'old_scene', 'E')


def resource_identity_fixture():
    q = dict(qualified=True, batch='v20_normal_000', scenario_id='synthetic', policy_id='new_E',
             window_minutes=[0., 1440.], result_sha256='a' * 64, terminal_sha256='b' * 64, raw_sha256='c' * 64)
    rr = dict(batch='v20_normal_000', passed=True, exit_code=0, qualification=q)
    launch = dict(batch='v20_normal_000', pid=123, successful_Popen=True, owned_child_drained=True, exit_code=0)
    samples = [dict(batch='v20_normal_000', pid=123, process_sample_status='measured')]
    return rr, launch, samples, q


@pytest.mark.parametrize('mutation', ['result_batch', 'launch_batch', 'missing_pid', 'boolean_pid', 'sample_pid', 'sample_batch', 'qualification_policy', 'qualification_window'])
def test_resource_identity_cannot_be_masked_by_later_common_row_update(mutation):
    rr, launch, samples, expected = resource_identity_fixture(); expected = copy.deepcopy(expected)
    if mutation == 'result_batch': rr['batch'] = 'foreign'
    if mutation == 'launch_batch': launch['batch'] = 'foreign'
    if mutation == 'missing_pid': launch['pid'] = None; samples[0]['pid'] = None
    if mutation == 'boolean_pid': launch['pid'] = True; samples[0]['pid'] = True
    if mutation == 'sample_pid': samples[0]['pid'] = 456
    if mutation == 'sample_batch': samples[0]['batch'] = 'foreign'
    if mutation == 'qualification_policy': rr['qualification']['policy_id'] = 'foreign'
    if mutation == 'qualification_window': rr['qualification']['window_minutes'] = [0., 600.]
    with pytest.raises(ValueError): module().validate_resource_identity(rr, launch, samples, expected)


def test_actual_resource_identity_requires_positive_PID_and_same_qualified_pair():
    rr, launch, samples, expected = resource_identity_fixture()
    module().validate_resource_identity(rr, launch, samples, expected)


@pytest.mark.parametrize('mutation', ['old_union', 'old_ENS', 'new_delivered'])
def test_pinned_report_values_must_still_equal_recomputed_saved_window(mutation):
    actual = dict(P0_material_union_minutes=7.5, P1_c_ENS_kWh=1., P3_e_delivered_kWh=100.)
    reported = copy.deepcopy(actual)
    field = dict(old_union='P0_material_union_minutes', old_ENS='P1_c_ENS_kWh', new_delivered='P3_e_delivered_kWh')[mutation]
    reported[field] += 10
    with pytest.raises(ValueError): module().compare_saved_metrics(actual, reported, list(actual), 'synthetic_readback')


def test_actual_old_normal_reference_and_service_readback_schema():
    # A saved old reference is not a newly completed M20 normal. No raw or
    # annual values are opened; the independent reviewer also audited all12.
    m = module()
    old_root = Path(m.read(m.RUN / 'provenance/STARTING_POINT.json')['source_root'])
    old_run = old_root / 'outputs/v19_model_closeout_20261018_run01'
    directory = old_run / 'experiments/normal_E_final_r1_000'
    plan = m.read(directory / 'plan.json'); manifest = m.read(directory / 'manifest.json')
    spec = plan['policy_specs'][0]; scene = manifest['scenarios'][0]
    record = m.read(directory / 'pairs' / (scene['id'] + '__' + spec['policy_id']) / 'RESULT.json')
    m.validate_old_identity(record, plan, manifest, m.read(old_run / 'MODEL_FREEZE_CLOSEOUT01.json'), scene['id'], 'E')
    from scripts.v20_report import window_metrics
    values = window_metrics(record, 'registered_full', [0., 1440.], expected_model='M19_model_closeout')
    assert values['complete'] is True and values['P0_e_requested_kWh'] is None
    assert isinstance(values['P0_e_ENS_kWh'], (float, int))
