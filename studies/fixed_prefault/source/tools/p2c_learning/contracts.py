"""Data version, completeness, grouping and representation checks for P2C."""
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
from tools.p2b_learning.regression import parse_bool
from tools.p2b_learning.risk import risk_metrics

SEEDS = (4101, 4102, 4103)
TARGETS = tuple(f'{p}_{c}_ENS_kWh' for p in ('P0', 'P3') for c in ('e', 'h', 'c')) + ('cost_USD',)
ROOT = Path(__file__).resolve().parents[2]


def registry():
    return [
        {'config_id': 'DNIPC_p2_original_inputs', 'class': 'DNIPC', 'task': 'consequence', 'degree': 2, 'representation': 'original', 'eligible': True},
        {'config_id': 'RF_depth6_leaf2_original_inputs', 'class': 'RF', 'task': 'consequence', 'n_estimators': 200, 'max_depth': 6, 'min_samples_leaf': 2, 'representation': 'original', 'eligible': True},
        {'config_id': 'RF_depth6_leaf2_enriched_inputs', 'class': 'RF', 'task': 'consequence', 'n_estimators': 200, 'max_depth': 6, 'min_samples_leaf': 2, 'representation': 'enriched', 'eligible': True},
        {'config_id': 'RF_depth4_leaf2', 'class': 'RF', 'task': 'risk', 'n_estimators': 200, 'max_depth': 4, 'min_samples_leaf': 2, 'eligible': True},
        {'config_id': 'RF_depth6_leaf2', 'class': 'RF', 'task': 'risk', 'n_estimators': 200, 'max_depth': 6, 'min_samples_leaf': 2, 'eligible': True},
        {'config_id': 'GRU16', 'class': 'GRU', 'task': 'risk', 'hidden_size': 16, 'epochs': 30, 'patience': 5, 'learning_rate': .001, 'batch_size': 64, 'eligible': True},
    ]


def canonical_digest(value, ascii=False):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=ascii, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def conditional_gate(records):
    seen = [(r['scenario_id'], r['policy_id'], r['decision_time_min']) for r in records]
    if len(set(seen)) != len(seen):
        raise ValueError('duplicate risk rows cannot inflate sufficiency')
    roles = {}; reasons = []
    for role, minimum_roots, minimum_groups, minimum_positive_windows in [('pilot_train', 5, 5, 10), ('pilot_validation', 2, 2, 4)]:
        rows = [r for r in records if r['role'] == role]
        positive = [r for r in rows if r['label'] == 1]
        negative = [r for r in rows if r['label'] == 0]
        complete_negative = [r for r in negative if r['trajectory_known_negative']]
        pr = {r['root_family_id'] for r in positive}; nr = {r['root_family_id'] for r in complete_negative}
        pg = {r['group_id'] for r in positive}; ng = {r['group_id'] for r in complete_negative}
        counts = {'positive_windows': len(positive), 'negative_windows': len(negative), 'positive_roots': len(pr),
            'complete_negative_roots': len(nr), 'positive_background_groups': len(pg), 'complete_negative_background_groups': len(ng),
            'positive_negative_root_overlap': len(pr & nr), 'positive_root_ids': sorted(pr), 'complete_negative_root_ids': sorted(nr)}
        roles[role] = counts
        for name, actual, minimum in [('positive_roots', len(pr), minimum_roots), ('complete_negative_roots', len(nr), minimum_roots),
            ('positive_background_groups', len(pg), minimum_groups), ('complete_negative_background_groups', len(ng), minimum_groups),
            ('positive_windows', len(positive), minimum_positive_windows), ('negative_windows', len(negative), 20)]:
            if actual < minimum:
                reasons.append(f'{role}:{name}={actual}<{minimum}')
    train = [r for r in records if r['role'] == 'pilot_train']; valid = [r for r in records if r['role'] == 'pilot_validation']
    for key in ['scenario_id', 'root_family_id', 'group_id']:
        if {r[key] for r in train} & {r[key] for r in valid}:
            raise ValueError('risk train/validation isolation violated: ' + key)
    return {'eligible': not reasons, 'reasons': reasons, 'roles': roles,
        'version': 'p2c.exploratory_conditional_sufficiency.v1',
        'near_variant_positive_negative_roots_may_overlap': True,
        'negative_interpretation': 'completed no-failure scenario variants; unknown prefixes do not count as negative accidents'}


def guarded_risk_metrics(y, probability, threshold, rows):
    y = np.asarray(y, int); probability = np.asarray(probability, float)
    if not len(y):
        return {'N': 0, 'PR_AUC_average_precision': None, 'ROC_AUC': None, 'status': 'no_valid_rows'}
    result = risk_metrics(y, probability, threshold, rows)
    result['single_class'] = len(np.unique(y)) < 2
    positive_roots = {r['root_family_id'] for r in rows if r['label'] == 1}
    negative_rows = [r for r in rows if r['trajectory_known_negative']]
    negative_roots = {r['root_family_id'] for r in negative_rows}
    negative_scenarios = {r['scenario_id'] for r in negative_rows}
    result.update(known_negative_family_denominator=len(negative_roots),
        known_negative_scenario_denominator=len(negative_scenarios),
        positive_negative_family_overlap=len(positive_roots & negative_roots),
        negative_denominator_interpretation='complete negative scenario variants retained even when their root also has positive variants')
    if threshold is not None:
        alerts = probability >= threshold
        strict_rows = [r for r, label, alert in zip(rows, y, alerts) if label == 1 and alert
            and r.get('first_interval') is not None and r['decision_time_min'] < r['first_interval'][0]
            and not r['known_abnormal_at_decision']]
        strict_roots = {r['root_family_id'] for r in strict_rows}
        strict_scenarios = {r['scenario_id'] for r in strict_rows}
        result['interval_only_early_detected_families'] = result['strict_early_detected_families']
        result.update(strict_early_detected_families=len(strict_roots), strict_early_detected_family_ids=sorted(strict_roots),
            strict_early_detected_scenarios=len(strict_scenarios), strict_early_detected_scenario_ids=sorted(strict_scenarios),
            strict_early_definition='positive-window alarm before first classified-failure interval start AND no already observed P0 abnormality at decision')
        false_rows = [r for r, a in zip(rows, alerts) if a and r['trajectory_known_negative']]
        result.update(false_alarm_families=len({r['root_family_id'] for r in false_rows}),
            false_alarm_family_ids=sorted({r['root_family_id'] for r in false_rows}),
            false_alarm_scenarios=len({r['scenario_id'] for r in false_rows}),
            false_alarm_scenario_ids=sorted({r['scenario_id'] for r in false_rows}))
    return result


def enriched_features(spec, initial_state, background):
    original = [float(v) for v in spec['offline_inputs']]
    if len(original) != 3:
        raise ValueError('registered original three-input representation required')
    first = int(spec['start_hour']); start, end = spec.get('score_interval_minutes', [60., 420.])
    if start != 60 or end != 420:
        raise ValueError('enriched six-hour representation is registered only for [60,420)')
    hours = range(first + 1, first + 7)
    values = np.array([background[h] for h in hours], float)
    if values.shape != (6, 4, 3) or not np.all(np.isfinite(values)):
        raise ValueError('complete unshocked hourly four-priority three-carrier background required')
    state = initial_state['state']; executed = initial_state['executed']
    fields = [{'name': 'shock_e', 'unit': 'relative_increment', 'online_risk_allowed': False},
        {'name': 'shock_c', 'unit': 'relative_increment', 'online_risk_allowed': False},
        {'name': 'calendar_hour', 'unit': 'one_based_hour', 'online_risk_allowed': True}]
    output = original.copy()
    for j, carrier in enumerate(['e', 'h', 'c']):
        total = values[:, :, j].sum(axis=1); mean = float(total.mean())
        fraction = float(values[:, 0, j].mean() / mean) if mean > 0 else 0.
        output.extend([mean, float(total.max()), fraction])
        fields.extend([{'name': f'unshocked_6h_{carrier}_mean_kW', 'unit': 'kW', 'online_risk_allowed': False},
            {'name': f'unshocked_6h_{carrier}_peak_kW', 'unit': 'kW', 'online_risk_allowed': False},
            {'name': f'unshocked_6h_P0_{carrier}_fraction', 'unit': 'ratio', 'online_risk_allowed': False}])
    gt = np.asarray(executed['gt_electric_kW'], float); thermal = state['thermal_delta']
    if gt.shape != (3,) or not isinstance(thermal, dict):
        raise ValueError('complete recorded initial GT/thermal state required')
    output.extend([float(gt.sum()), float(executed['grid_import_kW']), float(executed['boiler_steam_lbph']), max(map(float, thermal.values()), default=0.)])
    fields.extend([{'name': 'initial_GT_sum_kW', 'unit': 'kW', 'online_risk_allowed': False},
        {'name': 'initial_grid_import_kW', 'unit': 'kW', 'online_risk_allowed': False},
        {'name': 'initial_boiler_steam_lbph', 'unit': 'lb/h', 'online_risk_allowed': False},
        {'name': 'initial_max_thermal_delta_C', 'unit': 'C', 'online_risk_allowed': False}])
    if not np.all(np.isfinite(output)):
        raise ValueError('missing or nonfinite offline representation cannot be zero imputed')
    return output, fields


def bind_audited_dataset(manifest, audit, score_rows, checkpoint_root, verify_source_files=True):
    if not str(manifest.get('manifest_version', '')).startswith('p2c.'):
        raise ValueError('new P2C manifest required; old P2B data cannot be mixed')
    if not audit.get('passed') or audit.get('complete_budget') is not True or audit.get('manifest_signature') != manifest['signature']:
        raise ValueError('final complete actual audit does not bind manifest')
    specs = {s['id']: s for s in manifest['scenarios']}
    if len(specs) != len(manifest['scenarios']) or set(manifest['policies']) != {'priority_milp', 'secure_milp'}:
        raise ValueError('duplicate specs or unapproved policies')
    expected = {(sid, policy) for sid in specs for policy in manifest['policies']}
    keys = [(r['scenario_id'], r['policy_id']) for r in score_rows]
    entries = audit.get('checkpoint_digests', [])
    entry_keys = [(e['scenario_id'], e['policy_id']) for e in entries]
    if len(keys) != len(expected) or set(keys) != expected or len(set(keys)) != len(keys):
        raise ValueError('score pair coverage must include every attempt exactly once')
    if len(entry_keys) != len(expected) or set(entry_keys) != expected or len(set(entry_keys)) != len(entry_keys):
        raise ValueError('checkpoint digest coverage incomplete or duplicate')
    if audit.get('attempted_specifications') != len(specs) or audit.get('attempted_trajectories') != len(expected):
        raise ValueError('actual audit budget differs from registered specs')
    if any(s.get('is_final_blind') or manifest['splits'][sid].get('is_final_blind') for sid, s in specs.items()):
        raise ValueError('sealed final blocks are prohibited')
    if verify_source_files:
        for row in manifest.get('files', []):
            if sha(ROOT / row['path']) != row['sha256']:
                raise ValueError('simulation source/input drift after audit: ' + row['path'])
    rows = {key: r for key, r in zip(keys, score_rows)}; payloads = []; bindings = []
    initial = {}
    for entry in entries:
        sid, policy = entry['scenario_id'], entry['policy_id']; spec = specs[sid]; row = rows[(sid, policy)]
        path = Path(entry['path'])
        if not path.is_absolute():
            path = Path(checkpoint_root) / path
        if sha(path) != entry['checkpoint_sha256']:
            raise ValueError('checkpoint bytes changed after audit')
        wrapper = json.loads(gzip.decompress(path.read_bytes())); payload = wrapper['payload']
        if canonical_digest(payload) != entry['payload_sha256']:
            raise ValueError('canonical payload changed after audit')
        if wrapper['manifest_signature'] != manifest['signature'] or wrapper['scenario_id'] != sid or wrapper['policy_id'] != policy:
            raise ValueError('checkpoint identity differs from final manifest')
        if payload['scenario'] != spec or payload.get('seed', manifest['options']['seed']) != manifest['options']['seed']:
            raise ValueError('scenario or initial seed changed')
        if row['experiment_role'] != spec['experiment_role'] or row['group_id'] != manifest['splits'][sid]['group_id']:
            raise ValueError('score role/group differs from registered split')
        identity = payload.get('identity')
        if identity is not None:
            for key, value in {'scenario_id': sid, 'policy_id': policy, 'group_id': manifest['splits'][sid]['group_id'], 'manifest_signature': manifest['signature']}.items():
                if identity.get(key) != value:
                    raise ValueError('observation identity differs from dataset manifest')
        summary = payload['summary']
        if summary.get('scenario_id') != sid or summary.get('policy_id') != policy:
            raise ValueError('payload summary identity differs')
        state = payload.get('initial_state')
        if state is not None:
            if sid in initial and state != initial[sid]:
                raise ValueError('paired strategies have different initial states')
            initial[sid] = state
        segments = payload.get('segments', []); observed = segments[-1]['end_minute'] if segments else 0.
        start, end = spec.get('score_interval_minutes', [60., 420.]); complete = observed >= end - 1e-9
        if parse_bool(row['score_outcome_valid']) != complete:
            raise ValueError('score completeness differs from actual observed support')
        energy = np.zeros((4, 3)); cost = 0.; failed = False
        for segment in segments:
            dt = max(0., min(end, segment['end_minute']) - max(start, segment['start_minute']))
            if dt:
                energy += np.asarray(segment['unserved_kW']) * dt / 60
                cost += segment['cost_USD_step'] * dt / (segment['end_minute'] - segment['start_minute'])
                failed = failed or bool(segment['classification']['p0_guarantee_failure'])
        for target in TARGETS:
            actual = cost if target == 'cost_USD' else energy[0 if target.startswith('P0') else 3, ['e', 'h', 'c'].index(target.split('_')[1])]
            value = row[target]
            if not complete:
                if value is not None and value != '':
                    raise ValueError('unknown full consequence must not be filled with zero')
            elif value is None or value == '' or abs(float(value) - actual) > 1e-9:
                raise ValueError('score target differs from audited subsegment integral: ' + target)
        expected_failure = 1 if failed else 0 if complete else None
        if parse_bool(row['score_P0_failure']) != (bool(expected_failure) if expected_failure is not None else None):
            raise ValueError('binary score label disagrees with observed prefix')
        payloads.append(payload); bindings.append({'scenario_id': sid, 'policy_id': policy, 'path': str(path),
            'checkpoint_sha256': entry['checkpoint_sha256'], 'payload_sha256': entry['payload_sha256']})
    return {'payloads': payloads, 'pair_count': len(expected), 'specification_count': len(specs),
        'binding_digest': canonical_digest(bindings), 'bindings': bindings}
