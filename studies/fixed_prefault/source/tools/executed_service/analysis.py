"""Add v13 history/timeline tables to the unchanged saved-outcome analyzer.

The hook sees the very same in-memory payload as the existing _decisions call.
It never opens trajectories, changes scores, runs an environment or draws figures.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from types import FunctionType

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from tools.coordinated_dispatch import analysis as coordinated
from .history import EXECUTED_SERVICE_HISTORY_CONTRACT_ID, ACCUMULATORS, validate_history
from .timeline import extract_command_timeline

SCHEMA = 'executed_service.saved_analysis.v1'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _new_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def _runtime():
    from . import closed_loop
    from .policy import EXECUTED_SERVICE_POLICY_CONTRACT_ID
    return closed_loop, EXECUTED_SERVICE_POLICY_CONTRACT_ID


def _isolated(function, namespace):
    rebound = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__)
    rebound.__kwdefaults__ = function.__kwdefaults__
    return rebound


def new_collection():
    return dict(history_decisions=[], command_lifecycle=[], execution_intervals=[], trajectories=[])


def _identity(scene, pid):
    return dict(scenario_id=scene['id'], policy_id=pid, root_family_id=scene['root_family_id'],
        analysis_class=scene['analysis_class'], analysis_cohort=scene['analysis_cohort'],
        condition_id=scene.get('condition_id'))


def _budget(decision):
    diagnostic = decision.get('policy_diagnostic') or {}
    request = decision.get('requested_command') or {}
    options = (request.get('coordinated_joint') or {}).get('options') or {}
    effective = diagnostic.get('effective_service_budget_fractions', options.get('service_budget_fractions'))
    if effective is not None:
        if (not isinstance(effective, (list, tuple)) or len(effective) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    or not 0. <= v <= 1. for v in effective)):
            raise ValueError('Invalid saved effective service allowance')
        effective = [float(v) for v in effective]
    return effective


def _history_rows(scene, pid, payload):
    rows = []; identity = _identity(scene, pid)
    for decision in payload.get('decisions', []):
        now = float(decision['decision_time_min'])
        raw_history = decision.get('executed_service_history')
        history = None if raw_history is None else validate_history(raw_history, now)
        last = None if history is None else history['last_executed_segment']
        command = decision.get('command_generated')
        diagnostic = decision.get('policy_diagnostic') or {}
        row = dict(identity, decision_time_min=now,
            history_status='not_recorded' if history is None else 'observed_completed_prefix',
            history_available_time_min=None if history is None else history['available_time_min'],
            history_valid_time_min=None if history is None else history['valid_time_min'],
            history_end_min=None if history is None else history['history_end_min'],
            executed_service_history_json=history,
            last_completed_segment_json=copy.deepcopy(last),
            last_completed_segment_command_id=None if last is None else last['command_id'],
            arrived_command_id=decision.get('arrived_command_id', diagnostic.get('arrived_command_id')),
            known_command_queue_json=copy.deepcopy(decision.get('known_command_queue', diagnostic.get('known_command_queue'))),
            generated_command_id=None if command is None else command.get('id'),
            generated_minute=None if command is None else command.get('generated_minute'),
            candidate_planned_arrival_minute=None if command is None else command.get('arrival_minute'),
            effective_service_budget_fractions_json=_budget(decision),
            generated_command_is_current_execution=False,
            past_ENS_is_not_repaid=True, observation_is_completed_interval_not_instantaneous=True)
        for key in ACCUMULATORS:
            row[key + '_json'] = None if history is None else copy.deepcopy(history[key])
            for p, group in enumerate(('P2', 'P3')):
                for k, carrier in enumerate(('e', 'h', 'c')):
                    row[f'{group}_{carrier}_{key}'] = None if history is None else history[key][p][k]
        rows.append(row)
    return rows


def analysis_namespace(references, *, collected):
    namespace = coordinated.analysis_namespace(references)
    old_decisions = namespace['_decisions']
    def decisions(scene, pid, payload):
        history_rows = _history_rows(scene, pid, payload)
        timeline = extract_command_timeline(payload, scene)
        identity = _identity(scene, pid)
        collected['history_decisions'].extend(history_rows)
        collected['command_lifecycle'].extend({**identity, **row} for row in timeline['command_rows'])
        collected['execution_intervals'].extend({**identity, **row} for row in timeline['execution_intervals'])
        last = history_rows[-1] if history_rows else None
        collected['trajectories'].append(dict(identity,
            history_decision_count=len(history_rows),
            missing_history_decision_count=sum(r['history_status'] == 'not_recorded' for r in history_rows),
            last_saved_decision_time_min=None if last is None else last['decision_time_min'],
            last_saved_decision_history=None if last is None else last['executed_service_history_json'],
            last_saved_decision_history_is_final_trajectory_ENS=False,
            observed_until_minute=timeline['observed_until_minute'],
            requested_end_minute=timeline['requested_end_minute'],
            unknown_tail_minutes=timeline['unknown_tail_minutes'],
            command_lifecycle_count=len(timeline['command_rows']),
            command_lifecycle_status_counts=dict(Counter(r['lifecycle_status'] for r in timeline['command_rows'])),
            eventual_execution_status_counts=dict(Counter(r['eventual_execution_status'] for r in timeline['command_rows'])),
            execution_interval_count=len(timeline['execution_intervals']),
            right_censored_execution_interval_count=sum(r['right_censored'] for r in timeline['execution_intervals']),
            execution_intervals_with_coverage_holes=sum(bool(r['coverage_holes_minutes']) for r in timeline['execution_intervals']),
            clamp_events=timeline['clamp_events'],
            initial_unattributed_segments=timeline['initial_unattributed_segments'],
            hard_execution_budget_claim=False))
        # Do not replace or reinterpret any old nineteen-target/decision output.
        return old_decisions(scene, pid, payload)
    namespace['_decisions'] = decisions
    return namespace


def analyze_batch(manifest, plan, results, audit, out_dir):
    runtime, policy_contract = _runtime()
    _, verified_manifest = runtime.verify_batch(plan)
    for key, expected in (
            ('executed_service_history_contract_id', EXECUTED_SERVICE_HISTORY_CONTRACT_ID),
            ('executed_service_policy_contract_id', policy_contract)):
        if verified_manifest.get(key) != expected:
            raise ValueError('Missing or unsupported v13 analysis contract: ' + key)
    collected = new_collection()
    def namespace_hook(references):
        return analysis_namespace(references, collected=collected)
    function = coordinated.analyze_batch
    # Rebind only this call; old modules and common-component receipt stay intact.
    report = _isolated(function, dict(function.__globals__, analysis_namespace=namespace_hook))(
        manifest, plan, results, audit, out_dir)
    if len(collected['trajectories']) != report['registered_trajectory_count']:
        raise ValueError('Executed-service extras lost registered outcomes')
    out = Path(out_dir)
    service = _read(out / 'SERVICE_SWITCH_SUMMARY.json')
    service_rows = {(row['scenario_id'], row['policy_id']): row for row in service['trajectories']}
    if len(service_rows) != len(collected['trajectories']):
        raise ValueError('Original service metrics and v13 extra denominator differ')
    for row in collected['trajectories']:
        saved = service_rows[(row['scenario_id'], row['policy_id'])]
        for window in ('primary', 'full'):
            row[window + '_service_complete'] = saved[window]['complete']
            row[window + '_service_all_registered'] = copy.deepcopy(saved[window]['all_registered'])
    names = dict(history_decisions='EXECUTED_SERVICE_HISTORY_DECISIONS.csv',
                 command_lifecycle='COMMAND_LIFECYCLE.csv', execution_intervals='COMMAND_EXECUTION_INTERVALS.csv')
    for key, name in names.items():
        coordinated.original._write_table(out / name, collected[key])
    extra_report = dict(schema=SCHEMA, registered_trajectory_count=len(collected['trajectories']),
        history_decision_count=len(collected['history_decisions']),
        missing_history_decision_count=sum(r['missing_history_decision_count'] for r in collected['trajectories']),
        command_lifecycle_count=len(collected['command_lifecycle']),
        execution_interval_count=len(collected['execution_intervals']),
        original_19_targets_unchanged=True, inherited_service_recovery_budget_unchanged=True,
        unknown_values_are_null=True, all_registered_incomplete_service_is_null=True,
        planned_budget_is_actual_ENS_guarantee=False, no_figures_generated=True,
        additional_raw_opens=0, physical_trajectories_started=0,
        raw_access_scope='Existing audited analyzer only; extras reuse its already-open payload',
        executed_service_history_contract_id=EXECUTED_SERVICE_HISTORY_CONTRACT_ID,
        executed_service_policy_contract_id=policy_contract,
        trajectories=collected['trajectories'])
    _new_json(out / 'EXECUTED_SERVICE_SUMMARY.json', extra_report)
    sources = runtime._source_binding()
    common_path = out / 'COORDINATED_SOURCE_BINDING.json'
    common = _read(common_path)['registered_runtime_sources']
    if any(sources.get(name) != sha for name, sha in common.items()):
        raise ValueError('Common-component source receipt disagrees with full v13 runtime')
    extras = [*names.values(), 'EXECUTED_SERVICE_SUMMARY.json']
    receipt = dict(schema='executed_service.analysis_source_binding.v1',
        executed_service_history_contract_id=EXECUTED_SERVICE_HISTORY_CONTRACT_ID,
        executed_service_policy_contract_id=policy_contract,
        runtime_source_count=len(sources), registered_runtime_sources=sources,
        common_component_source_count=len(common), common_component_receipt_sha256=_sha(common_path),
        common_component_receipt_scope='Unmodified common planner/interface components only; full v13 runtime is bound by this receipt',
        manifest_sha256=_sha(manifest), plan_sha256=_sha(plan), audit_sha256=_sha(audit),
        report_sha256=_sha(out / 'SUMMARY.json'), inherited_extra_report_sha256=_sha(out / 'SERVICE_SWITCH_SUMMARY.json'),
        additional_output_sha256={name: _sha(out / name) for name in extras},
        analysis_program_sha256=_sha(__file__), history_analysis_source_sha256=_sha(Path(__file__).with_name('history.py')),
        timeline_analysis_source_sha256=_sha(Path(__file__).with_name('timeline.py')),
        physical_trajectories_started=0, extra_raw_opens=0)
    _new_json(out / 'EXECUTED_SERVICE_SOURCE_BINDING.json', receipt)
    return report
