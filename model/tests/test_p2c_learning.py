"""P2C offline data integrity, sufficiency and representation contracts."""
import copy
import gzip
import hashlib
import json
import numpy as np
import pytest


def api():
    from tools.p2c_learning.contracts import registry, conditional_gate, enriched_features, bind_audited_dataset, guarded_risk_metrics
    return registry, conditional_gate, enriched_features, bind_audited_dataset, guarded_risk_metrics


def records():
    rows = []
    for role, count in [('pilot_train', 5), ('pilot_validation', 2)]:
        for index in range(count):
            for label in [0, 1]:
                for t in range(10):
                    rows.append({'scenario_id': f'{role}{index}_{label}', 'policy_id': 'priority_milp', 'root_family_id': f'{role}{index}',
                        'group_id': f'{role}_group{index}', 'role': role, 'decision_time_min': t * 15., 'label': label,
                        'trajectory_known_negative': label == 0, 'first_interval': [75., 90.] if label else None,
                        'known_abnormal_at_decision': False})
    return rows


def test_registry_is_bounded_and_fixed():
    registry, *_ = api()
    configs = registry()
    assert len(configs) == 6
    assert {c['class'] for c in configs} == {'DNIPC', 'RF', 'GRU'}
    assert not any(c.get('class') == 'DSPCE' for c in configs)
    assert [c['config_id'] for c in configs[:3]] == ['DNIPC_p2_original_inputs', 'RF_depth6_leaf2_original_inputs', 'RF_depth6_leaf2_enriched_inputs']


def test_conditional_gate_allows_same_root_positive_and_complete_negative_variants():
    _, gate, *_ = api()
    result = gate(records())
    assert result['eligible']
    assert result['roles']['pilot_train']['positive_negative_root_overlap'] == 5
    assert result['roles']['pilot_validation']['positive_negative_root_overlap'] == 2


def test_unknown_prefix_negative_windows_do_not_create_negative_accidents():
    _, gate, *_ = api()
    rows = records()
    for row in rows:
        if row['label'] == 0:
            row['trajectory_known_negative'] = False
    result = gate(rows)
    assert not result['eligible']
    assert result['roles']['pilot_train']['complete_negative_roots'] == 0


def test_family_or_background_overlap_between_roles_is_rejected():
    _, gate, *_ = api()
    rows = records(); rows[-1]['group_id'] = 'pilot_train_group0'
    with pytest.raises(ValueError, match='isolation'):
        gate(rows)


def test_enriched_offline_fields_use_unshocked_background_and_correct_boiler_unit():
    _, _, enrich, *_ = api()
    spec = {'offline_inputs': [1.5, .7, 100], 'score_interval_minutes': [60., 420.], 'start_hour': 100}
    background = {h: np.array([[10., 2., 4.], [20., 3., 6.], [0., 0., 0.], [30., 5., 10.]]) for h in range(101, 107)}
    initial = {'state': {'thermal_delta': {}}, 'executed': {'gt_electric_kW': [1., 2., 3.], 'grid_import_kW': 40., 'boiler_steam_lbph': 70.}}
    vector, fields = enrich(spec, initial, background)
    assert len(vector) == 16
    assert vector[:3] == [1.5, .7, 100.]
    assert vector[3:6] == pytest.approx([60., 60., 1/6])
    assert vector[-4:] == [6., 40., 70., 0.]
    assert fields[-2]['name'] == 'initial_boiler_steam_lbph'
    assert fields[-2]['unit'] == 'lb/h'
    assert all(f['online_risk_allowed'] is False for f in fields[3:])


def test_enriched_fields_do_not_replace_missing_initial_state_with_zero():
    _, _, enrich, *_ = api()
    with pytest.raises((ValueError, KeyError)):
        enrich({'offline_inputs': [0., 0., 100], 'start_hour': 100}, {}, {})


def test_single_class_auc_is_null():
    *_, metrics = api()
    rows = [r for r in records() if r['label'] == 0][:20]
    result = metrics(np.zeros(len(rows), int), np.linspace(0, 1, len(rows)), .5, rows)
    assert result['PR_AUC_average_precision'] is None
    assert result['ROC_AUC'] is None


def test_safe_variants_remain_negative_alarm_denominators_with_shared_positive_roots():
    *_, metrics = api()
    rows = [r for r in records() if r['role'] == 'pilot_validation']
    y = np.array([r['label'] for r in rows])
    result = metrics(y, np.ones(len(y)), .5, rows)
    assert result['known_negative_family_denominator'] == 2
    assert result['known_negative_scenario_denominator'] == 2
    assert result['positive_negative_family_overlap'] == 2
    assert result['false_alarm_families'] == 2
    assert result['false_alarm_scenarios'] == 2


def test_already_known_abnormality_is_not_strict_early_detection():
    *_, metrics = api()
    rows = [r for r in records() if r['label'] == 1][:1]
    rows[0].update(decision_time_min=60., first_interval=[75., 90.], known_abnormal_at_decision=True)
    result = metrics([1], [1.], .5, rows)
    assert result['strict_early_detected_families'] == 0
    assert result['strict_early_detected_scenarios'] == 0
    assert result['interval_only_early_detected_families'] == 1
    assert result['alerts_with_already_known_abnormality'] == 1


def fixture(tmp_path):
    signature = 'new-version-signature'
    spec = {'id': 'probe1', 'experiment_role': 'pilot_train', 'root_family_id': 'r1', 'score_interval_minutes': [60., 420.]}
    manifest = {'manifest_version': 'p2c.probe.v1', 'signature': signature, 'scenarios': [spec], 'policies': ['priority_milp', 'secure_milp'],
        'splits': {'probe1': {'group_id': 'g1', 'experiment_role': 'pilot_train'}}, 'options': {'seed': 20261012}, 'files': []}
    payloads = []; entries = []; rows = []
    for policy in manifest['policies']:
        payload = {'scenario': spec, 'seed': 20261012, 'identity': {'scenario_id': 'probe1', 'policy_id': policy, 'group_id': 'g1', 'manifest_signature': signature},
            'summary': {'scenario_id': 'probe1', 'policy_id': policy, 'status': 'model_constraint_infeasible'}, 'segments': [], 'decisions': [], 'initial_state': {'state': {}, 'executed': {}}}
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        path = tmp_path / (policy + '.json.gz')
        path.write_bytes(gzip.compress(json.dumps({'manifest_signature': signature, 'scenario_id': 'probe1', 'policy_id': policy, 'payload': payload}).encode(), mtime=0))
        entries.append({'scenario_id': 'probe1', 'policy_id': policy, 'path': path.name, 'checkpoint_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'payload_sha256': hashlib.sha256(canonical).hexdigest()})
        rows.append({'scenario_id': 'probe1', 'policy_id': policy, 'experiment_role': 'pilot_train', 'group_id': 'g1', 'score_outcome_valid': 'False', 'score_P0_failure': '', 'cost_USD': '', **{f'{p}_{c}_ENS_kWh': '' for p in ['P0', 'P3'] for c in ['e', 'h', 'c']}})
    audit = {'passed': True, 'complete_budget': True, 'manifest_signature': signature, 'attempted_specifications': 1, 'attempted_trajectories': 2, 'checkpoint_digests': entries}
    return manifest, audit, rows, tmp_path


def test_small_probe_pair_budget_is_not_hardcoded_to_eight_hundred(tmp_path):
    *_, bind, metrics = api()
    manifest, audit, rows, root = fixture(tmp_path)
    result = bind(manifest, audit, rows, root, verify_source_files=False)
    assert len(result['payloads']) == 2
    assert result['pair_count'] == 2


def test_checkpoint_bytes_and_canonical_payload_are_both_bound(tmp_path):
    *_, bind, metrics = api()
    manifest, audit, rows, root = fixture(tmp_path)
    path = root / audit['checkpoint_digests'][0]['path']
    wrapper = json.loads(gzip.decompress(path.read_bytes())); wrapper['payload']['seed'] = 0
    path.write_bytes(gzip.compress(json.dumps(wrapper).encode(), mtime=0))
    audit['checkpoint_digests'][0]['checkpoint_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='canonical'):
        bind(manifest, audit, rows, root, verify_source_files=False)


def test_old_p2b_data_cannot_enter_new_learning_binding(tmp_path):
    *_, bind, metrics = api()
    manifest, audit, rows, root = fixture(tmp_path); manifest['manifest_version'] = 'p2b.manifest.v1'
    with pytest.raises(ValueError, match='P2C'):
        bind(manifest, audit, rows, root, verify_source_files=False)


def test_unknown_consequences_cannot_be_filled_with_zero(tmp_path):
    *_, bind, metrics = api()
    manifest, audit, rows, root = fixture(tmp_path); rows[0]['P0_e_ENS_kWh'] = '0'
    with pytest.raises(ValueError, match='unknown'):
        bind(manifest, audit, rows, root, verify_source_files=False)


def test_failed_final_regression_cannot_be_replaced_by_local_111_case_success(tmp_path):
    from tools.p2c_learning.run_offline import validate_release_gate
    path = tmp_path / 'release.json'
    path.write_text(json.dumps({'passed': True, 'pytest_exit_code': 1, 'failed': 4, 'source_sha256': {'tools/p2c_learning/contracts.py': 'ignored'}}), encoding='utf-8')
    with pytest.raises(ValueError, match='regression'):
        validate_release_gate(path)


def test_boolean_release_summary_without_raw_regression_evidence_is_rejected(tmp_path):
    from tools.p2c_learning.run_offline import validate_release_gate
    path = tmp_path / 'boolean_release.json'
    path.write_text(json.dumps({'passed': True, 'pytest_exit_code': 0, 'failed': 0,
        'source_sha256': {'tools/p2c_learning/contracts.py': 'ignored'}}), encoding='utf-8')
    with pytest.raises(ValueError, match='raw regression evidence'):
        validate_release_gate(path)


def test_release_summary_cannot_override_nonzero_raw_command_exit(tmp_path):
    from tools.p2c_learning.run_offline import validate_release_gate
    from tools.p2c_experiment.release import ROOT, sha
    # Deliberately synthetic evidence exercises binding, never claims a real run.
    def ref(name, text):
        path = tmp_path / name
        path.write_text(text, encoding='utf-8')
        return {'path': str(path), 'sha256': sha(path)}
    gate = {'passed': True, 'pytest_exit_code': 0, 'failed': 0,
        'source_sha256': {p: sha(ROOT / p) for p in ['src/hospital_ies/resilience.py', 'src/hospital_ies/solver_diagnostics.py']},
        'command': ref('synthetic.command.json', json.dumps({'command': ['python', '-m', 'pytest', '-q'], 'exit_code': 1})),
        'stdout': ref('synthetic.stdout.log', 'synthetic unit fixture'), 'stderr': ref('synthetic.stderr.log', ''),
        'junit': ref('synthetic.xml', '<testsuites/>'), 'baseline_junit': ref('synthetic_baseline.xml', '<testsuites/>'),
        'independent_review': ref('synthetic_review.json', '{"passed": true}'),
        'actual': {'tests': 259, 'failed': 0, 'errors': 0, 'skipped': 0}}
    path = tmp_path / 'release.json'; path.write_text(json.dumps(gate), encoding='utf-8')
    with pytest.raises(ValueError, match='regression did not exit zero'):
        validate_release_gate(path)


def test_s_gate_counts_without_original_and_corrected_matrix_evidence_are_rejected(tmp_path):
    from tools.p2c_learning.run_offline import validate_S_gate
    from tools.p2c_experiment.release import ROOT, sha
    gate = {'passed': True, 'attempted_original_failures': 111, 'reproduced_original_failures': 111,
        'corrected_full_lex_success': 111, 'unresolved_ids': [], 'tolerance_unchanged': True,
        'source_sha256': {p: sha(ROOT / p) for p in ['src/hospital_ies/resilience.py', 'src/hospital_ies/solver_diagnostics.py']}}
    path = tmp_path / 'synthetic_S_gate.json'; path.write_text(json.dumps(gate), encoding='utf-8')
    with pytest.raises(ValueError, match='raw evidence'):
        validate_S_gate(path)


def test_boolean_replay_comparison_without_frozen_manifest_bindings_is_rejected(tmp_path):
    from tools.p2c_learning.run_offline import validate_comparison
    path = tmp_path / 'synthetic_comparison.json'; path.write_text('{"passed": true}', encoding='utf-8')
    with pytest.raises(ValueError, match='manifest evidence'):
        validate_comparison(path, {})


def test_same_simulator_hash_does_not_permit_changed_secure_weight_between_cohorts():
    from tools.p2c_learning.run_offline import validate_cohort_contracts
    replay = {'signature': 'synthetic-replay', 'simulator_hash': 'same-physical-code',
        'execution_contract': {'local_descent': 'finite'}, 'execution_contract_id': 'same-contract',
        'assumption_set_id': 'same-assumptions', 'policies': ['priority_milp', 'secure_milp'],
        'schemas': {}, 'options': {'security_weight': 1}}
    probe = copy.deepcopy(replay); probe['p2c_parent_replay_signature'] = replay['signature']; probe['options']['security_weight'] = 2
    with pytest.raises(ValueError, match='options'):
        validate_cohort_contracts([{'cohort': 'replay', 'manifest': replay}, {'cohort': 'probe', 'manifest': probe}])


def test_risk_runtime_contract_restores_caller_threads():
    import torch
    from tools.p2c_learning.run_offline import runtime_contract
    previous = torch.get_num_threads()
    torch.set_num_threads(3)
    try:
        with runtime_contract():
            assert torch.get_num_threads() == 2
        assert torch.get_num_threads() == 3
    finally:
        torch.set_num_threads(previous)


def test_no_fault_condition_and_required_physical_partition_fields():
    from tools.p2c_learning.run_offline import condition_key
    spec = {'event_class': 'L4a', 'grid_limit_kW': 250., 'response_delay_minutes': 45., 'initiating_device': None, 'faults': None}
    assert condition_key(spec) == ('L4a', 250., 45., 'none')
    del spec['response_delay_minutes']
    with pytest.raises((KeyError, ValueError)):
        condition_key(spec)


def test_alarm_export_binds_strategy_split_model_threshold_and_hashes(tmp_path):
    import csv
    from tools.p2c_learning.run_offline import export_alarm_rows
    model = tmp_path / 'synthetic_model.bin'; model.write_bytes(b'synthetic test fixture, not hospital weights')
    rows = records()[:2]; rows[0]['role'] = 'pilot_validation'; rows[1]['role'] = 'pilot_validation'
    path = tmp_path / 'alarms.csv'
    metadata = export_alarm_rows(path, rows, [.7, .2], .5, model_id='synthetic_RF_seed4101',
        manifest_signature='synthetic-signature', model_files=[model], threshold_selection_scope='development_validation',
        threshold_rule='validation_recall>=0.9_max_precision_then_highest_threshold', fit_provenance={'synthetic_test': True})
    with path.open(encoding='utf-8-sig', newline='') as file:
        actual = list(csv.DictReader(file))
    assert [r['alarm'] for r in actual] == ['1', '0']
    assert actual[0]['policy_id'] == 'priority_milp'
    assert actual[0]['split'] == 'pilot_validation'
    assert actual[0]['model_id'] == 'synthetic_RF_seed4101'
    assert actual[0]['manifest_signature'] == 'synthetic-signature'
    assert actual[0]['threshold'] == '0.5'
    assert metadata['predictions_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert metadata['model_artifacts'][0]['sha256'] == hashlib.sha256(model.read_bytes()).hexdigest()
    assert metadata['threshold_selection_scope'] == 'development_validation'
    assert metadata['threshold_registered_before_validation'] is False
    assert json.loads(path.with_suffix('.metadata.json').read_text(encoding='utf-8')) == metadata


def test_alarm_export_cannot_silently_truncate_or_fill_unknown_predictions(tmp_path):
    from tools.p2c_learning.run_offline import export_alarm_rows
    with pytest.raises(ValueError, match='prediction count'):
        export_alarm_rows(tmp_path / 'length.csv', records()[:2], [.8], .5,
            model_id='synthetic', manifest_signature='synthetic', model_files=[],
            threshold_selection_scope='synthetic_test', threshold_rule='fixed0.5', fit_provenance={})
    with pytest.raises(ValueError, match='unknown probability'):
        export_alarm_rows(tmp_path / 'unknown.csv', records()[:1], [np.nan], .5,
            model_id='synthetic', manifest_signature='synthetic', model_files=[],
            threshold_selection_scope='synthetic_test', threshold_rule='fixed0.5', fit_provenance={})


def synthetic_sequences():
    # Toy observations test executable interfaces; no hospital inputs or fits.
    rows = records()
    for row in rows:
        t = row['decision_time_min']
        row['sequence'] = [{'grid_import_limit_kW': 250., 'control_available': True,
            'time_minute': t - 15 * (3 - i), 'hour_of_year': 100.,
            'toy_signal': row['label'] + .01 * i} for i in range(4)]
        row['padding_mask'] = [1] * 4
    return rows


def test_shortcut_diagnostics_preserve_fit_inputs_and_action_audit_identities(tmp_path):
    from tools.p2c_learning.run_offline import run_shortcuts
    rows = synthetic_sequences(); train = [r for r in rows if r['role'] == 'pilot_train']; valid = [r for r in rows if r['role'] == 'pilot_validation']
    folder = tmp_path / 'synthetic_shortcuts'
    result = run_shortcuts(train, valid, folder, implementation={'synthetic_test': True},
        manifest_signature='synthetic-signature', scope={'test_only': True})
    assert len(result['rules']) == 12
    for trial in result['rules']:
        destination = folder / trial['name']
        assert trial['reload_max_abs_error'] == 0
        metadata = json.loads((destination / 'alarms.metadata.json').read_text(encoding='utf-8'))
        assert metadata['row_count'] == len(valid)
        assert metadata['manifest_signature'] == 'synthetic-signature'
        arrays = np.load(destination / 'fitted_inputs.npz')
        assert arrays['train_X'].shape == (len(train), 2)
        assert arrays['validation_X'].shape == (len(valid), 2)
    assert (folder / 'constant_alarms.metadata.json').is_file()


@pytest.mark.parametrize('algorithm', ['RF', 'GRU'])
def test_registered_risk_candidate_reloads_identically_from_raw_toy_records(tmp_path, algorithm):
    from tools.p2c_learning.contracts import registry
    from tools.p2c_learning.run_offline import runtime_contract, probability
    from tools.p2b_learning.risk import SequencePreprocessor, fit_risk_candidate
    from tools.p2b_experiment.inference import reload_registered_risk
    config = next(c for c in registry() if c['task'] == 'risk' and c['class'] == algorithm)
    rows = synthetic_sequences(); train = [r for r in rows if r['role'] == 'pilot_train']; valid = [r for r in rows if r['role'] == 'pilot_validation']
    prep = SequencePreprocessor().fit(train); x = prep.transform(train); vx = prep.transform(valid)
    y = np.array([r['label'] for r in train]); vy = np.array([r['label'] for r in valid])
    prep_path = tmp_path / 'train_only_preprocessor.json'; prep_path.write_text(json.dumps(prep.to_dict()), encoding='utf-8')
    folder = tmp_path / algorithm
    with runtime_contract():
        prediction, diagnostics = fit_risk_candidate(config, 4101, x, y, vx, vy, folder)
    assert diagnostics['fit_device'] == ('cuda:0' if algorithm == 'GRU' else 'cpu')
    metadata = {'config': config, 'input_size': x.shape[-1], 'preprocessor_path': '../train_only_preprocessor.json',
        'training_device': diagnostics['fit_device'],
        'preprocessor_sha256': hashlib.sha256(prep_path.read_bytes()).hexdigest(),
        'files': [{'path': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in folder.iterdir() if p.is_file()]}
    (folder / 'provenance.json').write_text(json.dumps(metadata), encoding='utf-8')
    assert diagnostics['serialization_max_abs_error'] == 0
    assert np.max(np.abs(reload_registered_risk(folder, valid) - prediction)) == 0


def test_leave_background_out_fits_exclude_the_held_group_and_bind_raw_reload(tmp_path):
    from tools.p2c_learning.run_offline import rf_leave_background_out
    from tools.p2c_learning.contracts import registry
    config = next(c for c in registry() if c['task'] == 'risk' and c['class'] == 'RF')
    train = [r for r in synthetic_sequences() if r['role'] == 'pilot_train' and r['group_id'] in ['pilot_train_group0', 'pilot_train_group1', 'pilot_train_group2']]
    result = rf_leave_background_out(config, 4101, train, tmp_path / 'synthetic_lobo',
        implementation={'synthetic_test': True}, manifest_signature='synthetic', binding_digest='synthetic', scope={'synthetic_test': True})
    assert result['unknown_fold_rows'] == 0
    for fold in result['folds']:
        assert fold['held_group'] not in fold['train_groups']
        assert fold['raw_record_reload_max_abs_error'] == 0
