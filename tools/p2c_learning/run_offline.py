"""Bounded P2C offline fits after audited uniform replay and explicit S gate."""
import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import warnings
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier
from threadpoolctl import threadpool_limits
from tools.p2b_learning.provenance import build_fit_provenance
from tools.p2b_learning.regression import regression_gate, valid_rows, regression_metrics, fit_candidate, serialize_model, sample_measure, parse_bool
from tools.p2b_learning.risk import build_sequences, SequencePreprocessor, choose_threshold, fit_risk_candidate
from tools.p2b_experiment.inference import reload_registered_risk, registered_torch_threads
from .contracts import ROOT, SEEDS, TARGETS, registry, canonical_digest, sha, bind_audited_dataset, enriched_features, conditional_gate, guarded_risk_metrics


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file))


def validate_S_gate(path):
    # Reuse the replay gate's actual 111 case arrays, retained witnesses, five
    # corrected layers and per-file hashes; counts alone cannot permit fitting.
    from tools.p2c_experiment.replay import validate_gate
    return validate_gate(path)


def validate_release_gate(path):
    gate = json.loads(Path(path).read_text(encoding='utf-8'))
    if gate.get('passed') is not True or gate.get('pytest_exit_code', 0) != 0 or gate.get('failed', 0) != 0 or not gate.get('source_sha256'):
        raise ValueError('final actual complete regression gate is required, not local 111-case success')
    required = {'command', 'stdout', 'stderr', 'junit', 'baseline_junit', 'independent_review', 'actual'}
    if not required <= set(gate):
        raise ValueError('complete raw regression evidence is required; Boolean release summaries are insufficient')
    # One authoritative gate binds real command exit, all original 259 JUnit
    # identities, current numerical sources and the independent matrix review.
    from tools.p2c_experiment.release import validate_release
    return validate_release(path)


def runtime_contract():
    """Use the existing registered two-thread context, restoring caller state."""
    return registered_torch_threads()


def validate_comparison(path, new_manifest):
    from tools.p2c_experiment.replay import validate_parent
    from tools.p2c_experiment.release import resolve_evidence
    comparison = json.loads(Path(path).read_text(encoding='utf-8'))
    required = {f'{version}_manifest_{field}' for version in ['new', 'parent'] for field in ['path', 'sha256', 'signature']}
    if comparison.get('passed') is not True or not required <= set(comparison):
        raise ValueError('uniform comparison must bind actual new and frozen parent manifest evidence')
    manifests = {}
    for version in ['new', 'parent']:
        manifest_path = resolve_evidence(comparison[f'{version}_manifest_path'])
        if sha(manifest_path) != comparison[f'{version}_manifest_sha256']:
            raise ValueError('uniform comparison manifest bytes changed: ' + version)
        value = json.loads(manifest_path.read_text(encoding='utf-8'))
        unsigned = copy.deepcopy(value); signature = unsigned.pop('signature')
        if canonical_digest(unsigned, ascii=True) != signature or signature != comparison[f'{version}_manifest_signature']:
            raise ValueError('uniform comparison manifest signature differs: ' + version)
        manifests[version] = value
    if manifests['new'] != new_manifest:
        raise ValueError('offline replay differs from compared new manifest')
    validate_parent(manifests['parent'], manifests['new'])
    return comparison


def validate_cohort_contracts(datasets):
    replay = next(d['manifest'] for d in datasets if d['cohort'] == 'replay')
    for dataset in datasets:
        manifest = dataset['manifest']
        for field in ['simulator_hash', 'execution_contract', 'execution_contract_id', 'assumption_set_id', 'policies', 'schemas', 'options']:
            if field not in manifest or manifest[field] != replay[field]:
                raise ValueError('cohort physical/execution/evaluation contract differs: ' + field)
        if dataset['cohort'] == 'probe' and manifest.get('p2c_parent_replay_signature') != replay['signature']:
            raise ValueError('probe cohort does not bind this uniform replay')


def load_datasets(config_path, gate_path, comparison_path):
    validate_S_gate(gate_path)
    entries = json.loads(Path(config_path).read_text(encoding='utf-8'))
    if not isinstance(entries, list) or not 1 <= len(entries) <= 2:
        raise ValueError('one replay and at most one probe cohort required')
    results = []; seen_cohorts = set()
    for config in entries:
        cohort = config['cohort']
        if cohort not in ['replay', 'probe'] or cohort in seen_cohorts:
            raise ValueError('duplicate or unregistered dataset cohort')
        seen_cohorts.add(cohort)
        manifest = json.loads(Path(config['manifest_path']).read_text(encoding='utf-8'))
        audit = json.loads(Path(config['audit_path']).read_text(encoding='utf-8'))
        unsigned = copy.deepcopy(manifest); signature = unsigned.pop('signature')
        if canonical_digest(unsigned, ascii=True) != signature:
            raise ValueError('manifest canonical signature mismatch')
        scores = read_csv(config['scores_path'])
        bound = bind_audited_dataset(manifest, audit, scores, config['checkpoint_root'])
        if manifest.get('p2c_S_gate', {}).get('sha256') != sha(gate_path):
            raise ValueError('cohort is not bound to the verified S gate')
        if cohort == 'replay':
            if len(manifest['scenarios']) != 400 or bound['pair_count'] != 800 or not str(manifest.get('manifest_version', '')).startswith('p2c.uniform_replay'):
                raise ValueError('continuous data must use the entire uniform 400/800 replay')
            validate_comparison(comparison_path, manifest)
            initial_bindings = {r['scenario_id']: r for r in manifest.get('p2c_original_bindings', [])}
            if len(initial_bindings) != 400:
                raise ValueError('exact original initial-state bindings missing')
            for payload in bound['payloads']:
                sid = payload['scenario']['id']
                if payload.get('initial_state') is None or canonical_digest(payload['initial_state'], ascii=True) != initial_bindings[sid]['initial_sha256']:
                    raise ValueError('new replay initial state differs from old recorded initial state')
        elif len(manifest['scenarios']) > 120:
            raise ValueError('probe budget exceeds authorized 120 attempted specifications')
        results.append({'cohort': cohort, 'manifest': manifest, 'audit': audit, 'scores': scores, 'payloads': bound.pop('payloads'),
            'binding': bound, 'paths': config, 'file_sha256': {k: sha(config[k]) for k in ['manifest_path', 'audit_path', 'scores_path']}})
    if 'replay' not in seen_cohorts:
        raise ValueError('complete new-version replay is required before any offline fitting')
    validate_cohort_contracts(results)
    return results


def provenance(datasets, gate_path, comparison_path, prereg_path):
    base = build_fit_provenance(registry(), SEEDS)
    sources = [*sorted((ROOT / 'tools/p2c_learning').glob('*.py')), ROOT / 'tests/test_p2c_learning.py']
    base.update(format='p2c.offline.fit.provenance.v1', p2c_sources=[{'path': str(p.relative_to(ROOT)), 'sha256': sha(p)} for p in sources],
        preregistration={'path': str(prereg_path), 'sha256': sha(prereg_path)},
        S_gate={'path': str(gate_path), 'sha256': sha(gate_path)}, uniform_replay_comparison={'path': str(comparison_path), 'sha256': sha(comparison_path)},
        dataset_bindings=[{'cohort': d['cohort'], 'manifest_signature': d['manifest']['signature'], 'file_sha256': d['file_sha256'],
            'specifications': d['binding']['specification_count'], 'pairs': d['binding']['pair_count'], 'payload_digest_list': d['binding']['binding_digest']} for d in datasets],
        registered_torch_num_threads=2)
    base.pop('content_sha256', None); base['content_sha256'] = canonical_digest(base, ascii=True)
    return base


def background_groups(root):
    # Same read-only authoritative input transform as the core. No reset/step/solve.
    sys.path.insert(0, str(Path(root) / 'src'))
    import pandas as pd
    from hospital_ies.pipeline import read_data
    from hospital_ies.resilience import build_zone_groups
    hourly = read_data(Path(root)); zones = pd.read_csv(Path(root) / 'inputs/loads/critical_zones_8760.csv')
    grouped = build_zone_groups(zones, hourly)
    return {int(row.hour_of_year): np.array([[getattr(row, f'{p}_{c}_kW') for c in ['e', 'h', 'c']] for p in ['P0', 'P1', 'P2', 'P3']]) for row in grouped.itertuples(index=False)}


def continuous_metrics(y, prediction, train_y, groups, failure):
    result = regression_metrics(y, prediction, train_y, groups, failure, 0)
    groups = np.asarray(groups); unique = np.unique(groups); random = np.random.default_rng(4121)
    by_group = [np.flatnonzero(groups == g) for g in unique]; values = []
    for _ in range(1000):
        index = np.concatenate([by_group[j] for j in random.integers(len(unique), size=len(unique))])
        values.append(float(np.sqrt(np.mean((np.asarray(prediction)[index] - np.asarray(y)[index]) ** 2))))
    result['bootstrap'] = {'unit': 'connected_background_group', 'seed': 4121, 'repeats': 1000, 'groups': len(unique),
        'RMSE_95percent_interval': np.quantile(values, [.025, .975]).tolist(), 'scope': 'development complete-score subset; not full-domain or blind uncertainty'}
    return result


def run_consequences(replay, study, output, implementation):
    out = Path(output); out.mkdir(parents=True, exist_ok=False)
    manifest = replay['manifest']; specs = {s['id']: s for s in manifest['scenarios']}
    selected = [r for r in replay['scores'] if r['experiment_role'] in ['pilot_train', 'pilot_validation']]
    if len({r['scenario_id'] for r in selected}) != 300:
        raise ValueError('original p_study300 specifications must remain in their original roles')
    initial = {(p['scenario']['id'], p['summary']['policy_id']): p['initial_state'] for p in replay['payloads']}
    background = background_groups(ROOT); extra = {}; fields = None
    for row in selected:
        key = row['scenario_id'], row['policy_id']; extra[key], current_fields = enriched_features(specs[key[0]], initial[key], background)
        if fields is not None and current_fields != fields:
            raise ValueError('offline representation schema drift')
        fields = current_fields
    write(out / 'input_field_contract.json', {'original': fields[:3], 'enriched': fields, 'scope': 'full offline exogenous scenario only; appended future background forbidden for online risk'})
    reports = []; configs = [c for c in registry() if c['task'] == 'consequence']
    for stratum, distribution in study['strata'].items():
        basis = {seed: sample_measure(distribution['basis_measure'], 32768, seed) for seed in SEEDS}
        for policy in manifest['policies']:
            trall = [r for r in selected if r['study_stratum'] == stratum and r['policy_id'] == policy and r['experiment_role'] == 'pilot_train']
            vaall = [r for r in selected if r['study_stratum'] == stratum and r['policy_id'] == policy and r['experiment_role'] == 'pilot_validation']
            for target in TARGETS:
                name = f'{stratum}__{policy}__{target}'; folder = out / name; folder.mkdir()
                gate = regression_gate(trall, vaall, target); write(folder / 'gate.json', gate)
                train = valid_rows(trall, target); valid = valid_rows(vaall, target)
                result = {'condition': name, 'gate': gate, 'trials': [], 'selection': None}
                if not train or not valid:
                    result['status'] = 'insufficient_data'; reports.append(result); write(folder / 'result.json', result); continue
                x = np.array([specs[r['scenario_id']]['offline_inputs'] for r in train]); vx = np.array([specs[r['scenario_id']]['offline_inputs'] for r in valid])
                xe = np.array([extra[(r['scenario_id'], policy)] for r in train]); vxe = np.array([extra[(r['scenario_id'], policy)] for r in valid])
                y = np.array([float(r[target]) for r in train]); vy = np.array([float(r[target]) for r in valid])
                groups = [r['group_id'] for r in valid]; failure = [parse_bool(r['score_P0_failure']) for r in valid]
                np.savez_compressed(folder / 'dataset.npz', train_original_X=x, validation_original_X=vx, train_enriched_X=xe, validation_enriched_X=vxe, train_y=y, validation_y=vy)
                identities = {'train': [{k: r[k] for k in ['scenario_id', 'group_id', 'root_family_id']} for r in train],
                    'validation': [{k: r[k] for k in ['scenario_id', 'group_id', 'root_family_id']} for r in valid]}
                write(folder / 'dataset_ids.json', identities)
                result['constant_baseline'] = continuous_metrics(vy, np.full(len(vy), y.mean()), y, groups, failure)
                write(folder / 'constant_baseline.json', result['constant_baseline'])
                if not gate['eligible']:
                    result['status'] = 'constant_target' if y.std() <= 1e-8 else 'insufficient_data'; reports.append(result); write(folder / 'result.json', result); continue
                for config in configs:
                    if config['class'] == 'DNIPC' and not regression_gate(trall, vaall, target, 10)['eligible']:
                        result['trials'].append({'config_id': config['config_id'], 'status': 'skipped_PCE_sample_gate'}); continue
                    for seed in SEEDS:
                        trial_dir = folder / f"{config['config_id']}__seed{seed}"; trial_dir.mkdir()
                        trial = {'config_id': config['config_id'], 'seed': seed, 'status': 'attempted'}
                        tx, tvx = (xe, vxe) if config['representation'] == 'enriched' else (x, vx)
                        try:
                            with warnings.catch_warnings(record=True) as caught:
                                warnings.simplefilter('always')
                                model, diagnostics = fit_candidate(config, seed, tx, y, basis[seed], distribution['basis_measure'])
                            trial['warnings'] = [str(v.message) for v in caught]; trial['diagnostics'] = diagnostics
                            prediction_start = time.perf_counter(); predicted = model.predict(tvx); trial['prediction_seconds'] = time.perf_counter() - prediction_start
                            trial['metrics'] = continuous_metrics(vy, predicted, y, groups, failure)
                            restored = serialize_model(model, config, trial_dir / 'model', {'implementation': implementation, 'seed': seed,
                                'manifest_signature': manifest['signature'], 'dataset_sha256': sha(folder / 'dataset.npz'), 'dataset_ids': identities,
                                'input_fields': fields if config['representation'] == 'enriched' else fields[:3], 'target': target,
                                'target_unit': 'USD' if target == 'cost_USD' else 'kWh', 'condition': stratum, 'policy': policy,
                                'basis_measure': distribution['basis_measure'], 'dataset_binding_digest': replay['binding']['binding_digest']})
                            difference = float(np.max(np.abs(restored.predict(tvx) - predicted)))
                            if difference > 1e-12:
                                raise ValueError('serialized consequence prediction mismatch')
                            np.save(trial_dir / 'validation_predictions.npy', predicted)
                            trial.update(status='completed', serialization_max_abs_error=difference)
                        except Exception as error:
                            trial.update(status='failed', error_type=type(error).__name__, traceback=traceback.format_exc())
                            if hasattr(error, 'model'):
                                serialize_model(error.model, config, trial_dir / 'failed_model', {'status': 'failed_not_for_inference', 'implementation': implementation, 'seed': seed, 'failure': str(error)})
                        write(trial_dir / 'trial.json', trial); result['trials'].append(trial)
                candidates = []
                for config in configs:
                    good = [t for t in result['trials'] if t['config_id'] == config['config_id'] and t['status'] == 'completed']
                    if len(good) == 3:
                        candidates.append({'config_id': config['config_id'], 'mean_validation_normalized_RMSE': float(np.mean([t['metrics']['normalized_RMSE'] for t in good]))})
                result['selection'] = {'rule': 'three-seed mean development validation normalized RMSE; registered order breaks ties', 'candidates': candidates,
                    'selected': min(candidates, key=lambda r: r['mean_validation_normalized_RMSE']) if candidates else None}
                result['status'] = 'fitted' if candidates else 'all_candidates_failed_or_skipped'
                write(folder / 'result.json', result); reports.append(result)
                print('consequence', name, result['status'], flush=True)
    report = {'reports': reports, 'registry': configs, 'seeds': list(SEEDS), 'scope': 'new uniform replay fixed-policy p_study consequences, development validation only',
        'unknown_is_zero': False, 'complete_case_error_not_full_domain_error': True, 'new_control_branches': False}
    write(out / 'CONSEQUENCE_REPORT.json', report)
    return report


def condition_key(spec):
    device = spec.get('initiating_device')
    if device is None:
        objects = sorted(spec.get('faults') or {}); device = '+'.join(objects) if objects else 'none'
    return (spec['event_class'], float(spec['grid_limit_kW']), float(spec['response_delay_minutes']), str(device))


def condition_name(key):
    return '_'.join(map(str, key)) + '__' + canonical_digest(list(key))[:8]


def probability(model, values):
    if 1 not in model.classes_:
        return np.zeros(len(values))
    return model.predict_proba(values)[:, list(model.classes_).index(1)]


def export_alarm_rows(path, rows, predicted, threshold, *, model_id, manifest_signature, model_files,
        threshold_selection_scope, threshold_rule, fit_provenance):
    predicted = np.asarray(predicted, float)
    if predicted.shape != (len(rows),):
        raise ValueError('prediction count differs from decision identities')
    if not np.all(np.isfinite(predicted)):
        raise ValueError('unknown probability cannot become an alarm or a negative prediction')
    fields = ['scenario_id', 'policy_id', 'root_family_id', 'group_id', 'decision_time_min', 'split', 'model_id',
        'manifest_signature', 'threshold', 'label', 'probability', 'alarm', 'alert',
        'first_failure_interval_start', 'first_failure_interval_end', 'known_abnormal_at_decision', 'trajectory_known_negative']
    with Path(path).open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields); writer.writeheader()
        for row, p in zip(rows, predicted):
            interval = row.get('first_interval')
            output = {k: row[k] for k in ['scenario_id', 'policy_id', 'root_family_id', 'group_id', 'decision_time_min', 'label', 'known_abnormal_at_decision', 'trajectory_known_negative']}
            output.update(probability=float(p), alarm=int(p >= threshold) if threshold is not None else None,
                alert=bool(p >= threshold) if threshold is not None else None, split=row['role'], model_id=model_id,
                manifest_signature=manifest_signature, threshold=threshold,
                first_failure_interval_start=interval[0] if interval else None, first_failure_interval_end=interval[1] if interval else None)
            writer.writerow(output)
    def portable_ref(item):
        if item is None:
            return None
        artifact = Path(item['path'])
        if not artifact.is_absolute():
            artifact = ROOT / artifact
        if sha(artifact) != item['sha256']:
            raise ValueError('registered fit/preregistration reference changed')
        return {'path': os.path.relpath(artifact.resolve(), Path(path).parent.resolve()).replace('\\', '/'), 'sha256': item['sha256']}
    metadata = {'schema': 'p2c.alarm_predictions.v1', 'model_id': model_id, 'manifest_signature': manifest_signature,
        'threshold': threshold, 'alarm_unknown_when_threshold_unavailable': threshold is None,
        'threshold_selection_scope': threshold_selection_scope, 'threshold_rule': threshold_rule,
        'threshold_registered_before_validation': threshold_selection_scope.startswith('fixed'),
        'preregistration': portable_ref(fit_provenance.get('preregistration')),
        'fit_run_manifest': portable_ref(fit_provenance.get('fit_run_manifest')),
        'predictions_path': Path(path).name, 'predictions_sha256': sha(path), 'row_count': len(rows),
        'identity_digest': canonical_digest([[r['scenario_id'], r['policy_id'], r['role'], r['decision_time_min']] for r in rows]),
        'exporter_sha256': sha(Path(__file__)),
        'model_artifacts': [{'path': os.path.relpath(Path(p).resolve(), Path(path).parent.resolve()).replace('\\', '/'), 'sha256': sha(p)} for p in model_files]}
    write(Path(path).with_suffix('.metadata.json'), metadata)
    return metadata


def subgroup_metrics(rows, predicted, threshold):
    groups = sorted({r['group_id'] for r in rows}); result = {}
    for group in groups:
        indices = [i for i, r in enumerate(rows) if r['group_id'] == group]
        result[group] = guarded_risk_metrics([rows[i]['label'] for i in indices], np.asarray(predicted)[indices], threshold, [rows[i] for i in indices])
    return result


def evaluation_metadata(spec, row):
    """Evaluation-only strata; none of this is appended to model observations."""
    faults = spec.get('faults') or {}
    onset = spec.get('shock_start_minute', spec.get('fault_start_minute'))
    if onset is None:
        onset = spec.get('fault_start_minute')
    elapsed = row['decision_time_min'] - float(onset) if onset is not None else None
    if elapsed is None:
        elapsed_bin = 'not_defined'
    elif elapsed < 0:
        elapsed_bin = 'pre_event'
    elif elapsed < 15:
        elapsed_bin = '0_to_15'
    elif elapsed < 30:
        elapsed_bin = '15_to_30'
    elif elapsed < 60:
        elapsed_bin = '30_to_60'
    else:
        elapsed_bin = '60_or_more'
    start = spec.get('fault_start_minute'); shock = spec.get('shock_start_minute')
    return {'fault_availability': canonical_digest(faults)[:8] + ':' + json.dumps(faults, sort_keys=True),
        'duration_minutes': spec.get('shock_duration_minutes'),
        'fault_offset_minutes': float(start) - float(shock) if start is not None and shock is not None else None,
        'current_control_available': row['sequence'][-1].get('control_available'),
        'event_elapsed_minutes_bin': elapsed_bin,
        'event_elapsed_source': 'registered actual exogenous onset, evaluation partition only; never an input or added detector'}


def metadata_partition_metrics(rows, predicted, threshold):
    result = {}
    for field in ['fault_availability', 'duration_minutes', 'fault_offset_minutes', 'current_control_available', 'event_elapsed_minutes_bin']:
        values = sorted({str(r['evaluation_metadata'][field]) for r in rows})
        result[field] = {}
        for value in values:
            indices = [i for i, r in enumerate(rows) if str(r['evaluation_metadata'][field]) == value]
            result[field][value] = guarded_risk_metrics([rows[i]['label'] for i in indices], np.asarray(predicted)[indices], threshold, [rows[i] for i in indices])
    return result


def row_identities(rows):
    return [{k: r[k] for k in ['scenario_id', 'policy_id', 'root_family_id', 'group_id', 'role', 'decision_time_min']} for r in rows]


def run_shortcuts(train, valid, folder, *, implementation, manifest_signature, scope):
    folder.mkdir(); report = {'constant': None, 'rules': []}
    if not train or not valid:
        report['status'] = 'insufficient_rows'; write(folder / 'report.json', report); return report
    y = np.array([r['label'] for r in train]); vy = np.array([r['label'] for r in valid])
    constant_prediction = np.full(len(vy), y.mean())
    report['constant'] = guarded_risk_metrics(vy, constant_prediction, .5, valid)
    write(folder / 'dataset_ids.json', {'train': row_identities(train), 'validation': row_identities(valid)})
    np.savez_compressed(folder / 'constant_inputs_and_predictions.npz', train_y=y, validation_y=vy, validation_probability=constant_prediction)
    write(folder / 'constant_provenance.json', {'implementation': implementation, 'manifest_signature': manifest_signature,
        'scope': scope, 'training_positive_fraction': float(y.mean()), 'threshold': .5,
        'dataset_ids_sha256': sha(folder / 'dataset_ids.json'),
        'fitted_arrays_sha256': sha(folder / 'constant_inputs_and_predictions.npz'),
        'interpretation': 'training label mean diagnostic; no hospital outcome imputation'})
    export_alarm_rows(folder / 'constant_alarms.csv', valid, constant_prediction, .5,
        model_id=canonical_digest(scope)[:12] + '__train_mean_constant', manifest_signature=manifest_signature,
        model_files=[folder / 'constant_provenance.json', folder / 'constant_inputs_and_predictions.npz'],
        threshold_selection_scope='fixed_diagnostic', threshold_rule='fixed0.5', fit_provenance=implementation)
    for names in [('grid_import_limit_kW', 'control_available'), ('time_minute', 'hour_of_year')]:
        tx = np.array([[r['sequence'][-1][k] for k in names] for r in train], float)
        vx = np.array([[r['sequence'][-1][k] for k in names] for r in valid], float)
        if not np.all(np.isfinite(tx)) or not np.all(np.isfinite(vx)):
            report['rules'].append({'fields': names, 'status': 'missing_rule_inputs_not_zero_imputed'}); continue
        for depth in [1, 2]:
            for seed in SEEDS:
                name = f"{'_'.join(names)}__depth{depth}__seed{seed}"; trial_dir = folder / name; trial_dir.mkdir()
                model = DecisionTreeClassifier(max_depth=depth, random_state=seed).fit(tx, y)
                pred = probability(model, vx); selected = choose_threshold(vy, pred)
                threshold = selected['threshold'] if selected['threshold'] is not None else .5
                result = {'name': name, 'fields': list(names), 'depth': depth, 'seed': seed, 'status': 'diagnostic_rule',
                    'threshold': selected if selected['threshold'] is not None else {'threshold': .5, 'status': 'fixed_diagnostic_threshold_single_class'},
                    'metrics': guarded_risk_metrics(vy, pred, threshold, valid), 'validation_by_background': subgroup_metrics(valid, pred, threshold),
                    'tree_text': None, 'training_rows': len(train), 'validation_rows': len(valid)}
                from sklearn.tree import export_text
                result['tree_text'] = export_text(model, feature_names=list(names))
                joblib.dump(model, trial_dir / 'model.joblib', compress=3); np.save(trial_dir / 'validation_probabilities.npy', pred)
                np.savez_compressed(trial_dir / 'fitted_inputs.npz', train_X=tx, train_y=y, validation_X=vx, validation_y=vy)
                restored = probability(joblib.load(trial_dir / 'model.joblib'), vx)
                result['reload_max_abs_error'] = float(np.max(np.abs(restored - pred)))
                if result['reload_max_abs_error'] > 1e-12:
                    raise ValueError('serialized shortcut probability mismatch')
                write(trial_dir / 'provenance.json', {'implementation': implementation, 'manifest_signature': manifest_signature,
                    'scope': scope, 'fields': list(names), 'depth': depth, 'seed': seed,
                    'dataset_ids_path': '../dataset_ids.json', 'dataset_ids_sha256': sha(folder / 'dataset_ids.json'),
                    'fitted_input_sha256': sha(trial_dir / 'fitted_inputs.npz'), 'model_sha256': sha(trial_dir / 'model.joblib')})
                export_alarm_rows(trial_dir / 'alarms.csv', valid, pred, threshold, model_id=canonical_digest(scope)[:12] + '__' + name,
                    manifest_signature=manifest_signature, model_files=[trial_dir / 'model.joblib', trial_dir / 'provenance.json'],
                    threshold_selection_scope='development_validation' if selected['threshold'] is not None else 'fixed_diagnostic_single_class',
                    threshold_rule='validation_recall>=0.9_max_precision_then_highest_threshold' if selected['threshold'] is not None else 'fixed0.5',
                    fit_provenance=implementation)
                write(trial_dir / 'result.json', result); report['rules'].append(result)
    write(folder / 'report.json', report); return report


def rf_leave_background_out(config, seed, train, folder, *, implementation, manifest_signature, binding_digest, scope):
    folder.mkdir(); group_ids = sorted({r['group_id'] for r in train}); oof = np.full(len(train), np.nan); folds = []
    for index, held in enumerate(group_ids):
        fit_indices = [i for i, r in enumerate(train) if r['group_id'] != held]; held_indices = [i for i, r in enumerate(train) if r['group_id'] == held]
        tr = [train[i] for i in fit_indices]; va = [train[i] for i in held_indices]; y = np.array([r['label'] for r in tr]); vy = np.array([r['label'] for r in va])
        fold = {'held_group': held, 'train_groups': sorted({r['group_id'] for r in tr}), 'train_rows': len(tr), 'held_rows': len(va)}
        if len(np.unique(y)) < 2 or len(fold['train_groups']) < 2:
            fold['status'] = 'insufficient_fold_training'; folds.append(fold); continue
        prep = SequencePreprocessor().fit(tr); x = prep.transform(tr); vx = prep.transform(va)
        model = RandomForestClassifier(n_estimators=200, max_depth=config['max_depth'], min_samples_leaf=2, random_state=seed, n_jobs=1).fit(x.reshape(len(x), -1), y)
        p = probability(model, vx.reshape(len(vx), -1)); oof[held_indices] = p
        fold.update(status='diagnostic_fitted', metrics=guarded_risk_metrics(vy, p, .5, va), held_single_class=len(np.unique(vy)) < 2)
        destination = folder / f'fold{index:02d}'; destination.mkdir()
        write(destination / 'train_only_preprocessor.json', prep.to_dict()); joblib.dump(model, destination / 'model.joblib', compress=3)
        np.savez_compressed(destination / 'fitted_inputs.npz', train_X=x, train_y=y, held_X=vx, held_y=vy, held_indices=np.asarray(held_indices), probabilities=p)
        write(destination / 'row_ids.json', {'train': row_identities(tr), 'held': row_identities(va)})
        restored_prep = SequencePreprocessor.from_dict(json.loads((destination / 'train_only_preprocessor.json').read_text(encoding='utf-8')))
        restored_x = restored_prep.transform(va)
        restored = probability(joblib.load(destination / 'model.joblib'), restored_x.reshape(len(restored_x), -1))
        fold['raw_record_reload_max_abs_error'] = float(np.max(np.abs(restored - p)))
        if fold['raw_record_reload_max_abs_error'] > 1e-12:
            raise ValueError('serialized leave-background-out probability mismatch')
        write(destination / 'provenance.json', {'implementation': implementation, 'manifest_signature': manifest_signature,
            'dataset_binding_digest': binding_digest, 'config': config, 'seed': seed, 'held_group': held, 'scope': scope,
            'train_only_preprocessor_sha256': sha(destination / 'train_only_preprocessor.json'),
            'input_arrays_sha256': sha(destination / 'fitted_inputs.npz'), 'row_ids_sha256': sha(destination / 'row_ids.json'),
            'model_sha256': sha(destination / 'model.joblib'), 'threshold': .5, 'threshold_selected_on_held_group': False})
        export_alarm_rows(destination / 'alarms.csv', va, p, .5, model_id=f"{canonical_digest(scope)[:12]}__{config['config_id']}__seed{seed}__fold{index}",
            manifest_signature=manifest_signature, model_files=[destination / 'model.joblib', destination / 'provenance.json'],
            threshold_selection_scope='fixed_OOF_diagnostic', threshold_rule='fixed0.5', fit_provenance=implementation)
        write(destination / 'result.json', fold); folds.append(fold)
    known = np.isfinite(oof); np.savez_compressed(folder / 'OOF_probabilities_and_mask.npz', probability=oof, valid=known)
    valid_rows = [r for r, mask in zip(train, known) if mask]
    result = {'config_id': config['config_id'], 'seed': seed, 'folds': folds, 'valid_OOF_rows': int(known.sum()),
        'unknown_fold_rows': int((~known).sum()), 'threshold': .5, 'scope': 'leave-one-training-background-out diagnostic, no fold threshold selection',
        'metrics': guarded_risk_metrics([r['label'] for r in valid_rows], oof[known], .5, valid_rows)}
    write(folder / 'report.json', result); return result


def run_risk(datasets, output, implementation):
    out = Path(output); out.mkdir(parents=True, exist_ok=False); reports = []
    for dataset in datasets:
        manifest = dataset['manifest']; specs = {s['id']: s for s in manifest['scenarios']}
        for policy in manifest['policies']:
            records = build_sequences(dataset['payloads'], manifest['splits'], policy)
            for row in records:
                row['evaluation_metadata'] = evaluation_metadata(specs[row['scenario_id']], row)
            folder = out / f"{dataset['cohort']}__{policy}"; folder.mkdir()
            write(folder / 'raw_records.json', records)
            train = [r for r in records if r['role'] == 'pilot_train']; valid = [r for r in records if r['role'] == 'pilot_validation']
            global_rules = run_shortcuts(train, valid, folder / 'mixture_shortcut_diagnostics', implementation=implementation,
                manifest_signature=manifest['signature'], scope={'cohort': dataset['cohort'], 'policy': policy, 'condition': 'mixture_diagnostic'})
            conditions = sorted({condition_key(specs[r['scenario_id']]) for r in records})
            for key in conditions:
                rows = [r for r in records if condition_key(specs[r['scenario_id']]) == key]
                name = condition_name(key); destination = folder / name; destination.mkdir()
                tr = [r for r in rows if r['role'] == 'pilot_train']; va = [r for r in rows if r['role'] == 'pilot_validation']
                gate = conditional_gate(rows); write(destination / 'gate.json', gate)
                write(destination / 'raw_records.json', rows)
                shortcut = run_shortcuts(tr, va, destination / 'conditional_shortcuts', implementation=implementation,
                    manifest_signature=manifest['signature'], scope={'cohort': dataset['cohort'], 'policy': policy, 'condition': list(key)})
                result = {'cohort': dataset['cohort'], 'policy': policy, 'condition': list(key), 'gate': gate, 'shortcut': shortcut, 'trials': []}
                if not gate['eligible']:
                    result.update(status='insufficient_conditional_data', selection=None)
                    write(destination / 'result.json', result); reports.append(result); print('risk gate', dataset['cohort'], policy, name, gate['reasons'], flush=True); continue
                prep = SequencePreprocessor().fit(tr); x = prep.transform(tr); vx = prep.transform(va)
                y = np.array([r['label'] for r in tr]); vy = np.array([r['label'] for r in va])
                write(destination / 'train_only_preprocessor.json', prep.to_dict()); np.savez_compressed(destination / 'fitted_inputs.npz', train_X=x, train_y=y, validation_X=vx, validation_y=vy)
                for config in [c for c in registry() if c['task'] == 'risk']:
                    for seed in SEEDS:
                        trial_dir = destination / f"{config['config_id']}__seed{seed}"; trial = {'config_id': config['config_id'], 'seed': seed, 'status': 'attempted'}
                        try:
                            with runtime_contract():
                                predicted, diag = fit_risk_candidate(config, seed, x, y, vx, vy, trial_dir)
                            chosen = choose_threshold(vy, predicted); threshold = chosen['threshold']
                            trial.update(status='completed', diagnostics=diag, threshold=chosen, metrics=guarded_risk_metrics(vy, predicted, threshold, va),
                                validation_by_background=subgroup_metrics(va, predicted, threshold),
                                validation_metadata_partitions=metadata_partition_metrics(va, predicted, threshold))
                            np.save(trial_dir / 'validation_probabilities.npy', predicted)
                            write(trial_dir / 'provenance.json', {'implementation': implementation, 'manifest_signature': manifest['signature'], 'config': config, 'seed': seed,
                                'fit_dataset_ids': sorted({r['scenario_id'] for r in tr}), 'fit_row_ids': [[r['scenario_id'], r['decision_time_min']] for r in tr],
                                'dataset_binding_digest': dataset['binding']['binding_digest'], 'fitted_input_npz_sha256': sha(destination / 'fitted_inputs.npz'),
                                'input_size': x.shape[-1], 'preprocessor_path': '../train_only_preprocessor.json', 'preprocessor_sha256': sha(destination / 'train_only_preprocessor.json'),
                                'requested_device': 'cuda:0' if config['class'] == 'GRU' else 'cpu',
                                'training_device': diag['fit_device'],
                                'torch_num_threads': 2, 'torch_num_threads_semantics': 'host intra-op compatibility; not a GRU device declaration',
                                'feature_source': 'same four decision.features history vectors + padding; train-only preprocessing',
                                'files': [{'path': p.name, 'sha256': sha(p)} for p in trial_dir.iterdir() if p.is_file()]})
                            restored = reload_registered_risk(trial_dir, va)
                            difference = float(np.max(np.abs(restored - predicted))); trial['raw_record_reload_max_abs_error'] = difference
                            if difference > 1e-12:
                                raise ValueError('raw record/preprocessing/weight reload mismatch')
                            export_alarm_rows(trial_dir / 'alarms.csv', va, predicted, threshold,
                                model_id=f"{dataset['cohort']}__{policy}__{name}__{config['config_id']}__seed{seed}",
                                manifest_signature=manifest['signature'],
                                model_files=[trial_dir / ('model.joblib' if config['class'] == 'RF' else 'state_dict.pt'), trial_dir / 'provenance.json'],
                                threshold_selection_scope='development_validation',
                                threshold_rule='validation_recall>=0.9_max_precision_then_highest_threshold', fit_provenance=implementation)
                            if config['class'] == 'RF':
                                trial['leave_background_out'] = rf_leave_background_out(config, seed, tr, trial_dir / 'leave_background_out',
                                    implementation=implementation, manifest_signature=manifest['signature'], binding_digest=dataset['binding']['binding_digest'],
                                    scope={'cohort': dataset['cohort'], 'policy': policy, 'condition': list(key)})
                        except Exception as error:
                            trial.update(status='failed', error_type=type(error).__name__, traceback=traceback.format_exc())
                        trial_dir.mkdir(exist_ok=True); write(trial_dir / 'trial.json', trial); result['trials'].append(trial)
                candidates = []
                for config in [c for c in registry() if c['task'] == 'risk']:
                    good = [t for t in result['trials'] if t['config_id'] == config['config_id'] and t['status'] == 'completed']
                    if len(good) == 3:
                        candidates.append({'config_id': config['config_id'], 'mean_validation_average_precision': float(np.mean([t['metrics']['PR_AUC_average_precision'] for t in good]))})
                result.update(status='fitted' if candidates else 'all_candidates_failed', selection={'rule': 'three-seed mean development conditional AP; registered order breaks ties',
                    'candidates': candidates, 'selected': max(candidates, key=lambda r: r['mean_validation_average_precision']) if candidates else None})
                write(destination / 'result.json', result); reports.append(result); print('risk', dataset['cohort'], policy, name, result['status'], flush=True)
    report = {'reports': reports, 'registry': [c for c in registry() if c['task'] == 'risk'], 'seeds': list(SEEDS),
        'risk_horizon_minutes': 60, 'observation_history_length': 4, 'scope': 'separate historical replay and probe development cohorts, exact-condition comparison; no new blind evaluation',
        'no_environment_actions': True, 'unknown_labels_fabricated': False, 'cohort_mixing': False}
    write(out / 'RISK_REPORT.json', report); return report


def main():
    parser = argparse.ArgumentParser()
    for name in ['datasets', 'S-gate', 'release-gate', 'replay-comparison', 'study-distribution', 'preregistration', 'out']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--task', choices=['consequence', 'risk', 'both'], default='both')
    args = parser.parse_args(); validate_release_gate(args.release_gate)
    datasets = load_datasets(args.datasets, args.S_gate, args.replay_comparison)
    study = json.loads(Path(args.study_distribution).read_text(encoding='utf-8'))
    implementation = provenance(datasets, args.S_gate, args.replay_comparison, args.preregistration)
    implementation['release_gate'] = {'path': args.release_gate, 'sha256': sha(args.release_gate)}
    implementation['study_distribution'] = {'path': args.study_distribution, 'sha256': sha(args.study_distribution)}
    implementation.pop('content_sha256'); implementation['content_sha256'] = canonical_digest(implementation, ascii=True)
    out = Path(args.out)
    if out.exists():
        raise FileExistsError('new immutable offline attempt directory required')
    out.mkdir(parents=True); write(out / 'FIT_RUN_MANIFEST.json', implementation); write(out / 'registry.json', registry())
    implementation = copy.deepcopy(implementation)
    implementation['fit_run_manifest'] = {'path': str((out / 'FIT_RUN_MANIFEST.json').resolve()), 'sha256': sha(out / 'FIT_RUN_MANIFEST.json')}
    implementation.pop('content_sha256'); implementation['content_sha256'] = canonical_digest(implementation, ascii=True)
    summary = {'task': args.task, 'registry_configuration_count': 6, 'seeds': list(SEEDS), 'start_clock': time.perf_counter()}
    with threadpool_limits(2):
        if args.task in ['consequence', 'both']:
            replay = next(d for d in datasets if d['cohort'] == 'replay')
            report = run_consequences(replay, study, out / 'consequence', implementation)
            summary['consequence_conditions'] = len(report['reports'])
        if args.task in ['risk', 'both']:
            report = run_risk(datasets, out / 'risk', implementation)
            summary['risk_conditions'] = len(report['reports']); summary['risk_conditions_fitted'] = sum(r['status'] == 'fitted' for r in report['reports'])
    summary['elapsed_seconds'] = time.perf_counter() - summary.pop('start_clock'); write(out / 'SUMMARY.json', summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
