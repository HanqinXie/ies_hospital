"""Read actual audited checkpoints; no simulation, fit, filtering or relabeling."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]


def metric_comparison(before, after, fields, atol=1e-6):
    if any(before.get(k) is None or after.get(k) is None
           or not math.isfinite(before[k]) or not math.isfinite(after[k]) for k in fields):
        return 'unknown'
    differences = [after[k]-before[k] for k in fields]
    better = any(x < -atol for x in differences)
    worse = any(x > atol for x in differences)
    return 'tradeoff' if better and worse else 'improved' if better else 'worse' if worse else 'same'


def measures(payload, env, start, end):
    from tools.p2b_experiment.audit import score_payload
    from .energy_metrics import window_energy_fee
    row = score_payload(payload, start, end)
    row.update(window_energy_fee(payload, env.plant, env.hourly, start, end))
    trips = [e for e in payload.get('events', []) if e.get('kind') == 'protection_trip']
    row['secondary_GT_trips'] = len(trips)
    row['first_secondary_GT_trip_minute'] = min((e['time_minute'] for e in trips), default=None)
    segments = payload.get('segments', [])
    total_minutes = sum(s['end_minute']-s['start_minute'] for s in segments)
    # Actual available capacity is read from each interval, not a new capacity.
    loads = []; margins = []
    for s in segments:
        actual = np.asarray(s['action']['gt_electric_kW'], float)
        availability = s['availability']
        f = [availability[f'GT_{i}'] for i in (1,2,3)] if isinstance(availability, dict) and all(f'GT_{i}' in availability for i in (1,2,3)) else None
        if f is not None:
            capacity = np.asarray(f, float)*env.plant.gt_rated_kW
            if capacity.shape == actual.shape:
                loads.extend((actual[capacity>0]/capacity[capacity>0]).tolist())
                margins.extend((capacity-actual).tolist())
    row['maximum_actual_GT_available_load_fraction'] = max(loads, default=None)
    row['minimum_GT_available_margin_kW'] = min(margins, default=None)
    row['subsegments'] = len(segments); row['physical_observed_minutes'] = total_minutes
    row['solver_calls'] = len(payload.get('solver_calls', []))
    row['solver_elapsed_seconds'] = sum(c.get('elapsed_seconds', 0.) for c in payload.get('solver_calls', []))
    row['decision_latency_ms_max'] = max((d.get('decision_ms', 0.) for d in payload.get('rows', [])), default=0.)
    return row


def summarize(manifest_path, audit_path, out, *, compare_original=False):
    from .runner import verify_manifest, POLICIES, OLD, original_payload
    from .dependency import sha, read, reference
    from hospital_ies.control_env import HospitalIESEnv
    from tools.p2b_experiment.runner import load_payload
    manifest_path = Path(manifest_path).resolve(); audit_path = Path(audit_path).resolve()
    manifest = read(manifest_path); verify_manifest(manifest); audit = read(audit_path)
    if audit['manifest_signature'] != manifest['signature'] or not audit['passed']:
        raise ValueError('Wrong actual audit')
    out = Path(out)
    if out.exists(): raise FileExistsError('Fresh analysis attempt required')
    out.mkdir(parents=True)
    env = HospitalIESEnv(ROOT, execution_contract=manifest['execution_contract'])
    start = manifest['options']['score_start_minute']; end = manifest['options']['score_end_minute']
    expected = {(x['scenario_id'], x['policy_id']): x for x in audit['checkpoint_digests']}
    old = read(OLD/'global_manifest.json') if compare_original else None
    specs = {s['id']: s for s in manifest['scenarios']}; results = []; old_changes = []
    aggregate = dict(solver_layer_count=0, solver_native_attempts=0, accepted_recovery_attempts=0,
        max_original_physical_row_violation=0., max_bound_violation=0., max_lock_violation=0., max_integer_distance=0.)
    layer_status = Counter(); numerical_versions = Counter()
    for shard in manifest['p2b_shards']:
        folder = manifest_path.parent/'shards'/shard['id']
        for sid in shard['scenario_ids']:
            for policy in POLICIES:
                ref = expected.get((sid, policy))
                if ref is None: continue
                path = manifest_path.parent/ref['path']
                if sha(path) != ref['checkpoint_sha256']: raise ValueError('Actual checkpoint bytes changed')
                p = load_payload(folder, sid, policy, manifest['signature'])
                result = dict(scenario_id=sid, policy_id=policy, status=p['summary']['status'],
                    condition_id=specs[sid].get('condition_id', specs[sid]['event_class']),
                    root_family_id=specs[sid]['root_family_id'], role=specs[sid]['experiment_role'],
                    checkpoint_sha256=ref['checkpoint_sha256'], **measures(p, env, start, end))
                results.append(result)
                for call in p.get('solver_calls', []):
                    for layer in call.get('solver_backend_layers', []):
                        aggregate['solver_layer_count'] += 1; layer_status[str(layer['status'])] += 1
                        numerical_versions[layer.get('numerical_version')] += 1
                        aggregate['solver_native_attempts'] += len(layer.get('attempts', []))
                        for a in layer.get('attempts', []):
                            if not a.get('accepted'): continue
                            aggregate['accepted_recovery_attempts'] += int(a['attempt'] > 1)
                            v = a['validation']
                            for key, field in [('max_original_physical_row_violation','max_physical_row_violation'),
                                ('max_bound_violation','max_bound_violation'),('max_lock_violation','max_lock_violation'),
                                ('max_integer_distance','max_integrality_distance')]:
                                aggregate[key] = max(aggregate[key], v[field])
                if old is not None:
                    prior = original_payload(old, sid, policy); previous = measures(prior, env, start, end)
                    old_changes.append(dict(scenario_id=sid, policy_id=policy, old_status=prior['summary']['status'],
                        new_status=result['status'], old_P0_failure=previous['score_P0_failure'], new_P0_failure=result['score_P0_failure'],
                        service_comparison=metric_comparison(previous, result, SERVICE_FIELDS),
                        old_outcome_complete=previous['score_outcome_valid'], new_outcome_complete=result['score_outcome_valid'],
                        old_first_failure=previous['first_failure_observed_minute'], new_first_failure=result['first_failure_observed_minute'],
                        metric_differences={k:None if previous.get(k) is None or result.get(k) is None else result[k]-previous[k]
                                            for k in SERVICE_FIELDS+['actual_energy_fee_USD']}))
    by_pair = {(r['scenario_id'], r['policy_id']): r for r in results}; pairs=[]
    for sid in specs:
        a = by_pair.get((sid, 'priority_milp')); b = by_pair.get((sid, 'secure_milp'))
        if a is None or b is None: continue
        pairs.append(dict(scenario_id=sid, condition_id=a['condition_id'], role=a['role'], root_family_id=a['root_family_id'],
            priority_P0_failure=a['score_P0_failure'], secure_P0_failure=b['score_P0_failure'],
            service_comparison=metric_comparison(a,b,SERVICE_FIELDS),
            fee_comparison=metric_comparison(a,b,['actual_energy_fee_USD']),
            metric_differences={k:None if a.get(k) is None or b.get(k) is None else b[k]-a[k]
                                for k in SERVICE_FIELDS+['actual_energy_fee_USD']},
            priority_first_failure=a['first_failure_observed_minute'],secure_first_failure=b['first_failure_observed_minute']))
    report=dict(manifest=reference(manifest_path),audit=reference(audit_path),planned_trajectories=audit['planned_trajectories'],
        attempted_trajectories=len(results),complete_budget=audit['complete_budget'],status_counts=dict(Counter(r['status'] for r in results)),
        service_metrics=SERVICE_FIELDS,metric_absolute_equality_tolerance=1e-6,lower_is_better=True,
        service_comparisons=dict(Counter(r['service_comparison'] for r in pairs)),fee_comparisons=dict(Counter(r['fee_comparison'] for r in pairs)),
        old_status_to_new=dict(Counter(r['old_status']+' -> '+r['new_status'] for r in old_changes)),
        old_success_regressions=[r for r in old_changes if r['old_status']=='completed' and r['new_status']!='completed'],
        native_layer_status=dict(layer_status),numerical_versions=dict(numerical_versions),**aggregate,
        actual_energy_fee_semantics='Original illustrative tariffs and actual GT/boiler/grid/diesel ports; excludes all safety/tracking/tie-break penalties',
        tradeoff_semantics='Coordinate comparison of original guarantee flag,12 ENS, modeled P0-P2 maxT and secondary GT trips. Cost separate; no new hospital failure standard.',
        source_sha256=sha(__file__))
    for name, value in [('trajectories.json',results),('paired_metric_differences.json',pairs),('old_to_new.json',old_changes),('SUMMARY.json',report)]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    verify_manifest(manifest)
    print(json.dumps(report,ensure_ascii=False,indent=2)); return report


SERVICE_FIELDS = ['score_P0_failure'] + [f'P{g}_{k}_ENS_kWh' for g in range(4) for k in ('e','h','c')] + [f'P{g}_max_temperature_C' for g in range(3)] + ['secondary_GT_trips']

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('manifest');p.add_argument('audit');p.add_argument('out');p.add_argument('--compare-original',action='store_true')
    a=p.parse_args();summarize(a.manifest,a.audit,a.out,compare_original=a.compare_original)
