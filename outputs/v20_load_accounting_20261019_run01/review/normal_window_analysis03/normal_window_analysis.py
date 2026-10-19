"""Wait for immutable normal terminals and FINAL01, then analyze saved bytes.

External read-only engineering analysis. No registration, runner invocation,
simulation, raw trajectory read, annual-value parsing, resource control or
admission change is available through this helper.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'outputs/v20_load_accounting_20261019_run01'
REPORT = RUN / 'paper_results/FINAL01'
DOC = ROOT / 'docs/v20/NORMAL_WINDOW_ANALYSIS.md'
PROFILE = RUN / 'provenance/normal_resource_profile'
RESOURCE_RUN = PROFILE / 'supervision_canonical02'
PRIOR_RESOURCE_RUN = PROFILE / 'supervision_canonical01'
PRIOR_ANALYSIS = HERE.parent / 'normal_window_analysis01'
S3 = RUN / 'review/supervision03'
S4 = RUN / 'review/normal008_continuation01/L72_RUN02'
SUCCESSOR_CONTRACT = HERE / 'SUCCESSOR_CONTRACT.json'
IDLE_RUN01 = RUN / 'review/normal008_continuation01/L72_RUN01'
SUCCESSOR_CAPTURE = RUN / 'logs/normal008_lightweight_run02.command.json'
PRIOR_ANALYSIS02 = HERE.parent / 'normal_window_analysis02'
PREFLIGHT = RUN / 'review/NORMAL_CONTINUATION_PREFLIGHT03.json'
S3_SHA = '7cdc0eb91c2d52b1996f478478cf6b9762c73861bee6035257e6529b178bc117'
NORMAL_SHA = '50ab72194abf63dda9c1893595629f57d0b3ceb488eb2e64ff709fe273f8701b'
MODEL = 'M20_load_accounting'
SOURCE_FIELDS = ('GT_electric_kWh', 'GRID_electric_kWh', 'DG_electric_kWh', 'ABS_cooling_kWh',
    'EC_cooling_kWh', 'TOWER_rejection_kWh', 'HRSG_steam_lb', 'BOILER_steam_lb')
SERVICE_FIELDS = ('requested_kWh', 'delivered_kWh', 'ENS_kWh', 'disconnected_ENS_kWh', 'connected_unserved_ENS_kWh')
CO2_FIELDS = ('grid_CO2_kg', 'natural_gas_CO2_kg', 'grid_plus_gas_CO2_kg', 'DG_CO2_kg_conditional', 'total_CO2_kg_conditional')
META_DIFFERENCES = {'evaluation_role', 'background_status', 'original_final_release_authorized',
    'historical_unseen_claim', 'v20_seen_regression', 'v20_source_scene_id'}
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def require(value, message):
    if not value: raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def now(): return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')


def emit(state, **values):
    row = dict(utc=now(), state=state, **values)
    with (HERE / 'EVENTS.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    temporary = HERE / 'STATE_CURRENT.tmp'
    temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temporary, HERE / 'STATE_CURRENT.json')
    print(json.dumps(row, ensure_ascii=True, allow_nan=False), flush=True)


def number(value):
    require(type(value) in (int, float) or isinstance(value, str) and bool(value), 'Unknown numeric accounting value')
    require(not isinstance(value, bool), 'Boolean is not a numeric accounting value')
    result = float(value); require(math.isfinite(result), 'Nonfinite saved accounting value'); return result


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def dependency_shas():
    return dict(script_sha256=sha(Path(__file__)), release_gate_sha256=sha(ROOT / 'publication/release_gate.py'),
        normal_helper_sha256=sha(ROOT / 'publication/run_normal_serial.py'), frozen_report_script_sha256=sha(ROOT / 'scripts/v20_report.py'),
        supervision03_sha256=sha(S3 / 'SUPERVISOR_IMPLEMENTATION.py'), supervision03_start_sha256=sha(S3 / 'START_COMMAND.json'),
        continuation_preflight_sha256=sha(PREFLIGHT), prior_analysis_script_sha256=sha(PRIOR_ANALYSIS / 'normal_window_analysis.py'),
        successor_contract_sha256=sha(SUCCESSOR_CONTRACT), continue008_helper_sha256=sha(ROOT / 'publication/continue_normal008.py'))


def validate_prepared_dependencies(prepared, current):
    for field in current:
        require(isinstance(prepared.get(field), str) and len(prepared[field]) == 64 and prepared[field] == current.get(field),
                'Prepared external dependency changed: ' + field)


def validate_continuation_chain(old_normal, old_supervisor, preflight, new_supervisor=None):
    require(old_normal.get('name') == 'normal02' and old_normal.get('exit_code') == 2, 'Original normal02 failure must remain exit2')
    require(old_supervisor.get('status') == 'failed_unknown' and old_supervisor.get('source_modified') is False
            and old_supervisor.get('all_attempts_preserved') is True, 'Original supervision02 failure was not preserved')
    old = old_supervisor.get('checkpoint') or {}
    require(old.get('actual_started') == old.get('actual_finished') == 395 and old.get('active') == 0
            and old.get('not_started') == 11 and old.get('failed_terminal_journals') == [], 'Original drain denominator differs')
    names = {f'v20_normal_{i:03d}' for i in range(12) if i != 9}
    require(preflight.get('status') == 'passed' and preflight.get('scientific_source_unchanged') is True
            and preflight.get('prior_helper_failure_preserved') is True and preflight.get('prior_scientific_child_completed_and_drained') is True
            and preflight.get('qualified_existing_skipped') == ['v20_normal_009']
            and len(preflight.get('pending_not_started', [])) == 11 and set(preflight['pending_not_started']) == names,
            'Reviewed continuation must strictly skip only actual completed009')
    c = preflight.get('comparisons') or {}
    require(c.get('qualified') is True and c.get('expected_pairs') == c.get('qualified_pairs') == 394
            and c.get('pending') == c.get('failed') == c.get('report_issues') == [], '394 comparison prerequisite differs')
    if new_supervisor is not None:
        n = new_supervisor; cp = n.get('checkpoint') or {}
        require(n.get('status') == 'passed' and n.get('strict_batches') == 148 and n.get('source_modified') is False
                and n.get('continuation_of') == 'supervision02' and n.get('normal_capture_name') == 'normal03'
                and n.get('normal_supervision_name') == 'canonical02' and n.get('previous_helper_error_preserved') is True
                and n.get('existing_normal_009_strictly_skipped') is True and n.get('normal_009_resource_peak_unknown') is True,
                'New supervision03 continuation identity/status differs')
        require(cp.get('registered') == cp.get('actual_started') == cp.get('actual_finished') == 406 and cp.get('active') == 0
                and cp.get('not_started') == 0 and cp.get('failed_terminal_journals') == [], 'New canonical406 terminal denominator differs')
    return dict(prior_normal02_exit_code=2, prior_supervision02_status='failed_unknown', previous_failure_preserved=True,
                new_supervision='supervision03', new_capture='normal03', resource_run='canonical02', normal_009_resource_peak_unknown=True)


def validate_resource_continuation(summary, interrupted, old_samples):
    names = {f'v20_normal_{i:03d}' for i in range(12) if i != 9}
    require(summary.get('exit_code') == 0 and summary.get('state') == 'all_twelve_strictly_qualified'
            and summary.get('pending_not_started') == [] and summary.get('existing_qualified_skipped') == ['v20_normal_009']
            and summary.get('actual_scientific_children_started') == 11, 'New resource supervisor must start eleven and skip009')
    completed = summary.get('completed', []); launches = summary.get('successful_Popen_proofs', [])
    require(len(completed) == 11 and {x.get('batch') for x in completed} == names
            and all(x.get('passed') is True and x.get('exit_code') == 0 for x in completed), 'Resource completed eleven denominator differs')
    require(len(launches) == 11 and {x.get('batch') for x in launches} == names
            and all(type(x.get('pid')) is int and x['pid'] > 0 and x.get('successful_Popen') is True
                    and x.get('owned_child_drained') is True and x.get('exit_code') == 0 for x in launches), 'Resource launch eleven denominator differs')
    require(interrupted.get('batch') == 'v20_normal_009' and type(interrupted.get('pid')) is int and interrupted['pid'] > 0
            and interrupted.get('successful_Popen') is True and interrupted.get('owned_child_drained') is True
            and interrupted.get('exit_code') == 0 and interrupted.get('error_type') == 'TypeError' and old_samples == [],
            'Old009 interrupted sampling provenance differs; cannot invent a new peak')
    return dict(new_scientific_launches=11, strictly_skipped=['v20_normal_009'], normal_009_measured=False)


def historical_continuation_inputs():
    paths = [RUN / 'logs/normal02.command.json', RUN / 'logs/normal02.log', RUN / 'review/supervision02/RESULT.json',
             PRIOR_RESOURCE_RUN / 'SUMMARY.json', PRIOR_RESOURCE_RUN / 'v20_normal_009.interrupted.json', PRIOR_RESOURCE_RUN / 'SAMPLES.jsonl',
             PRIOR_ANALYSIS / 'RESULT.json', RUN / 'logs/normal_analysis_wait01.command.json',
             S3 / 'START_COMMAND.json', S3 / 'SUPERVISOR_IMPLEMENTATION.py', PREFLIGHT, RESOURCE_RUN / 'START.json']
    pre = read(PREFLIGHT); start = read(S3 / 'START_COMMAND.json'); resource = read(RESOURCE_RUN / 'START.json')
    validate_continuation_chain(read(paths[0]), read(paths[2]), pre)
    require(sha(S3 / 'SUPERVISOR_IMPLEMENTATION.py') == start.get('implementation_sha256') == S3_SHA,
            'Reviewed supervision03 implementation differs')
    require(Path(start['preflight_path']).resolve() == PREFLIGHT and start['preflight_sha256'] == sha(PREFLIGHT)
            and pre['helper_sha256'] == sha(ROOT / 'publication/run_normal_serial.py') == NORMAL_SHA
            and pre['freeze_sha256'] == sha(RUN / 'MODEL_FREEZE_ACCOUNTING01.json'), 'Continuation source/preflight binding differs')
    require(resource.get('supervisor_sha256') == NORMAL_SHA and resource.get('mode') == 'run_existing_registered_normals'
            and resource.get('growth_factor') == 1.2 and resource.get('host_reserve_bytes') == 6 * 1024**3,
            'New resource supervisor/admission identity differs')
    for row in pre.get('evidence_files', []):
        p = (RUN / row['path']).resolve(strict=True); require(p.is_relative_to(RUN), 'Continuation evidence escaped RUN')
        require(p.stat().st_size == row['bytes'] and sha(p) == row['sha256'], 'Prior failure evidence changed'); paths.append(p)
    prior = read(PRIOR_ANALYSIS / 'RESULT.json'); require(prior.get('status') == 'blocked_preserve_evidence', 'Failed analysis01 state changed')
    return list(dict.fromkeys(paths))


def validate_prepared_evidence(rows):
    for row in rows:
        p = Path(row['path'])
        require(p.is_file() and p.stat().st_size == row['bytes'] and sha(p) == row['sha256'], 'Prepared continuation evidence changed: ' + str(p))


def validate_stage_commands(normal, report, resource_start):
    normal_tail = ['-B', 'publication/run_normal_serial.py', '--run', '--name', 'canonical02', '--poll-seconds', '15', '--sample-seconds', '2']
    require(normal.get('name') == 'normal03' and normal.get('exit_code') == 0 and Path(normal.get('cwd', '')).resolve() == ROOT
            and normal.get('command', [])[1:] == normal_tail, 'Actual normal03 command/root/run differs')
    args = report.get('command', [])
    require(report.get('name') == 'final_readback01' and report.get('exit_code') == 0 and Path(report.get('cwd', '')).resolve() == ROOT
            and len(args) == 7 and args[1:6] == ['-B', 'scripts/v20_report.py', '--label', 'FINAL01', '--baseline-root'],
            'Actual FINAL01 report command/root/label differs')
    require(resource_start.get('command') == normal_tail[1:] and resource_start.get('mode') == 'run_existing_registered_normals'
            and resource_start.get('supervisor_sha256') == NORMAL_SHA, 'Actual canonical02 resource start differs')


def validate_predecessor_stop(normal03, supervisor03, canonical02):
    """Actual stopped predecessor; never reinterpret intentional125 as success."""
    require(normal03.get('name') == 'normal03' and normal03.get('exit_code') == 125,
            'Actual normal03 intentional exit125 must be retained')
    require(supervisor03.get('status') == 'failed_unknown' and supervisor03.get('source_modified') is False
            and supervisor03.get('all_attempts_preserved') is True, 'Actual failed supervision03 must be retained')
    cp = supervisor03.get('checkpoint') or {}
    require(cp.get('registered') == 406 and cp.get('actual_started') == cp.get('actual_finished') == 405
            and cp.get('active') == 0 and cp.get('not_started') == 1 and cp.get('failed_terminal_journals') == [],
            'Stopped predecessor must have405 actual complete and008 notstarted')
    names = {f'v20_normal_{i:03d}' for i in range(12) if i not in (8, 9)}
    require(canonical02.get('mode') == 'run' and canonical02.get('exit_code') == 125
            and canonical02.get('state') == 'explicit_stop_new_launches'
            and canonical02.get('existing_qualified_skipped') == ['v20_normal_009']
            and canonical02.get('pending_not_started') == ['v20_normal_008']
            and canonical02.get('actual_scientific_children_started') == 10,
            'Canonical02 must preserve ten actual launches,009 skip and008 pending')
    completed = canonical02.get('completed', []); launches = canonical02.get('successful_Popen_proofs', [])
    require(len(completed) == 10 and {r.get('batch') for r in completed} == names
            and all(r.get('passed') is True and r.get('exit_code') == 0 for r in completed),
            'Canonical02 ten completed resource records differ')
    require(len(launches) == 10 and {r.get('batch') for r in launches} == names
            and all(type(r.get('pid')) is int and r['pid'] > 0 and r.get('successful_Popen') is True
                    and r.get('owned_child_drained') is True and r.get('exit_code') == 0 for r in launches),
            'Canonical02 ten actual child launches differ')
    return dict(normal03_exit_code=125, supervision03_status='failed_unknown', canonical02_measured_batches=10,
                pending_new008=['v20_normal_008'], normal_009_resource_status='unknown', prior_failure_not_erased=True)


def assess_successor_readiness(*, s4_exists, normal_terminals, report_exists):
    require(type(normal_terminals) is int and 0 <= normal_terminals <= 12, 'Normal terminal denominator invalid')
    return dict(ready=bool(s4_exists and normal_terminals == 12 and report_exists),
                state='permission_to_validate_successor' if s4_exists and normal_terminals == 12 and report_exists else 'pending_no_project_import')


def successor_resource_sha(child):
    # Two explicitly documented publication-record key spellings, never inferred data.
    values = [child[k] for k in ('resource_result_sha', 'resource_result_sha256') if k in child]
    require(values and all(type(v) is str and len(v) == 64 and set(v) <= set('0123456789abcdef') for v in values)
            and len(set(values)) == 1, 'Successor actual resource-result SHA is absent or ambiguous')
    return values[0]


def validate_successor_final(summary, proof):
    require(summary.get('status') == 'passed' and summary.get('actual_scientific_children_started') == 1,
            'S4 must prove exactly one real new008 child')
    child = summary.get('last_child') or {}
    require(type(child.get('pid')) is int and child['pid'] > 0 and child.get('successful_Popen') is True
            and child.get('owned_child_drained') is True and child.get('exit_code') == 0,
            'S4 actual008 child is not proven drained and successful')
    successor_resource_sha(child)
    require(proof.get('schema') == 'hospital_ies.v20.publication_final_campaign_qualification.v1'
            and proof.get('status') == 'passed' and proof.get('model_id') == MODEL
            and proof.get('actual_complete_batches') == 148
            and proof.get('actual_complete_pairs') == proof.get('actual_started_attempts') == proof.get('actual_finished_attempts') == 406
            and proof.get('failed_or_unknown_canonical_pairs') == 0,
            'S4 final qualification must cover exact148 batches and406 actual pairs')
    require(proof.get('exact_registered_duration_and_service_ledger') is True and proof.get('actual_reset_matching') is True
            and proof.get('actual_applied_streams_recomputed') is True and proof.get('sealed_final12') == 'unopened'
            and proof.get('raw_files_opened') == proof.get('annual_values_parsed') == proof.get('physical_episodes_started') == 0,
            'S4 final saved-evidence qualification scope differs')
    return dict(new008_actual_child_count=1, actual_complete_pairs=406, actual_complete_batches=148,
                canonical02_measured_batches=10, new008_measured_batches=1, normal_009_resource_status='unknown',
                qualification_is_not406_new_replays=True)


def resource_context(batch, canonical02, successor):
    require(batch in {f'v20_normal_{i:03d}' for i in range(12)}, 'Foreign normal resource batch')
    if batch == 'v20_normal_009':
        return dict(origin='unknown_original_normal02', measurement_unknown=True, directory=PRIOR_RESOURCE_RUN, summary=canonical02)
    if batch == 'v20_normal_008':
        return dict(origin='S4_L72_RUN02', measurement_unknown=False, directory=S4, summary=successor)
    return dict(origin='canonical02', measurement_unknown=False, directory=RESOURCE_RUN, summary=canonical02)


def successor_contract():
    value = read(SUCCESSOR_CONTRACT)
    require(value.get('status') == 'bound_prepared' and value.get('successor') == 'normal008_continuation01/L72_RUN02'
            and value.get('outer_capture_name') == 'normal008_lightweight_run02', 'Bound successor contract identity differs')
    path = ROOT / 'publication/continue_normal008.py'
    require(value.get('successor_helper_sha256') == sha(path), 'Reviewed lightweight successor implementation changed')
    return value


def continuation_inputs():
    """Historical failures plus exact intentional125 handoff, never old success."""
    paths = historical_continuation_inputs(); contract = successor_contract()
    predecessor = [RUN / 'logs/normal03.command.json', S3 / 'RESULT.json', RESOURCE_RUN / 'SUMMARY.json']
    validate_predecessor_stop(*(read(p) for p in predecessor))
    for row in contract['predecessor_evidence']:
        path = (ROOT / row['path']).resolve(strict=True)
        require(path.is_relative_to(ROOT) and path.stat().st_size == row['bytes'] and sha(path) == row['sha256'],
                'Bound intentional handoff evidence changed')
        paths.append(path)
    require(sha(PRIOR_ANALYSIS02 / 'normal_window_analysis.py') == 'ff04ccf7c5d93ce98bfeac6455c43e140ce3447cf1e84d5b61b9dadf53cd2b62'
            and sha(PRIOR_ANALYSIS02 / 'collect_normal_once02.py') == '8c0798d79db5b977828a08778713ce666bf61b41b160e3df0ec1718b3a89ba7d',
            'Superseded analysis02 source changed')
    require(not any((PRIOR_ANALYSIS02 / n).exists() for n in ('ONESHOT_OWNER01.json', 'ONESHOT_RESULT01.json', 'COLLECTION_RECEIPT.json')),
            'Superseded analysis02 must remain unexecuted')
    validate_idle_predecessor(read(IDLE_RUN01 / 'IDLE_STOP_OUTCOME.json'), read(RUN / 'logs/normal008_lightweight_run01.command.json'))
    require(sha(IDLE_RUN01 / 'IMPLEMENTATION_AT_RUN01.py') == '68701e844ffca923e91f82fff52116d4b18a4357f1239d5c4a416135c8ed95a3', 'Preserved idleRUN01 source changed')
    require(not any((IDLE_RUN01 / n).exists() for n in ('SUMMARY.json','v20_normal_008.command.json','v20_normal_008.result.json','SAMPLES.jsonl')),
            'Aborted idleRUN01 must have no scientific result or sample ledger')
    paths.extend([IDLE_RUN01 / n for n in ('IDLE_STOP_OUTCOME.json','IDLE_STOP_INTENT.json','IMPLEMENTATION_AT_RUN01.py','START.json','preflight.json','preflight.command.json','ADMISSION_SAMPLES.jsonl')])
    paths.extend([RUN / 'logs/normal008_lightweight_run01.command.json', RUN / 'logs/normal008_lightweight_run01.log'])
    paths.extend([*predecessor, SUCCESSOR_CONTRACT, ROOT / 'publication/continue_normal008.py',
                  PRIOR_ANALYSIS02 / 'normal_window_analysis.py', PRIOR_ANALYSIS02 / 'collect_normal_once02.py'])
    return list(dict.fromkeys(paths))


def validate_successor_files(bound):
    summary = read(S4 / 'SUMMARY.json'); proof = read(S4 / 'FINAL_406_PROOF.json')
    validate_successor_final(summary, proof)
    outer = read(SUCCESSOR_CAPTURE)
    require(outer.get('name') == 'normal008_lightweight_run02' and outer.get('exit_code') == 0
            and Path(outer.get('cwd', '')).resolve() == ROOT, 'Actual successor capture is not successful at current ROOT')
    contract = successor_contract()
    require(summary.get('script_sha256') == contract['successor_helper_sha256'] and summary.get('exit_code') == 0
            and summary.get('no_frozen_science_modified') is True and summary.get('old_STOP_preserved') is True
            and summary.get('normal009_resource_peak_unknown') is True
            and summary.get('old_measured_resource_batches') == 10 and summary.get('new_measured_resource_batches') == 1,
            'Actual S4 source/resource partition differs')
    for name, filename in [('preflight','preflight.json'),('postflight','postflight.json'),('final_406_proof','FINAL_406_PROOF.json'),
                           ('resource_result','v20_normal_008.result.json'),('admission','ADMISSION.json')]:
        row = summary[name]; path = ROOT / row['path']
        require(path.resolve() == (S4 / filename).resolve(), 'Successor fingerprint redirects ' + name)
        require(path.stat().st_size == row['bytes'], 'Successor fingerprint byte size differs')
        bound(path, 'actual_successor_' + name, row['sha256'])
    for row in summary['predecessor']['evidence_files']:
        path = (ROOT / row['path']).resolve(strict=True); require(path.is_relative_to(ROOT), 'Successor predecessor path escaped ROOT')
        bound(path, 'actual_successor_preserved_predecessor', row['sha256'])
    for name, filename in [('command_receipt','final_readback01.command.json')]:
        row = summary['final_report'][name]
        require((ROOT / row['path']).resolve() == (RUN / 'logs' / filename).resolve(), 'Successor final-report command redirects')
        bound(ROOT / row['path'], 'actual_successor_final_report', row['sha256'])
    for p in [S4 / n for n in ('START.json','SUMMARY.json','preflight.command.json','postflight.command.json','final_qualification.command.json',
             'ADMISSION_SAMPLES.jsonl','SAMPLES.jsonl','v20_normal_008.command.json')]:
        bound(p, 'actual_successor_runtime_metadata')
    bound(SUCCESSOR_CAPTURE, 'actual_successor_outer_exit')
    for name in ('preflight','postflight','final_qualification'):
        receipt = read(S4 / (name + '.command.json'))
        require(receipt.get('name') == name and receipt.get('exit_code') == 0 and Path(receipt.get('cwd', '')).resolve() == ROOT,
                'Successor short qualification child failed/unknown')
    require(successor_resource_sha(summary['last_child']) == sha(S4 / 'v20_normal_008.result.json'),
            'Actual successor child/result byte SHA differs')
    return summary, proof


def integrate_actual_gt(rows, end, scenario_id, policy_id, raw_sha256):
    """Stored piecewise-linear GT source endpoints, never requested setpoints."""
    end = number(end); require(end > 0 and rows, 'Actual GT registered coverage is unknown')
    cursor = 0.; work = [[], [], []]
    for row in rows:
        require(row.get('scenario_id') == scenario_id and row.get('policy_id') == policy_id and row.get('raw_sha256') == raw_sha256,
                'Actual GT interval belongs to a foreign saved pair')
        a, b = number(row.get('start_minute')), number(row.get('end_minute'))
        require(abs(a - cursor) <= 1e-8 and b > a and b <= end + 1e-8, 'Actual GT interval has a gap, overlap or unknown tail')
        left = json.loads(row['source_start_gt_kW']); right = json.loads(row['source_end_gt_kW'])
        require(type(left) is list and type(right) is list and len(left) == len(right) == 3, 'Actual GT source vector shape unknown')
        for i, (x, y) in enumerate(zip(left, right)):
            x, y = number(x), number(y); require(x >= 0 and y >= 0, 'Actual GT power is invalid')
            work[i].append((x + y) * .5 * (b - a) / 60.)
        cursor = b
    require(abs(cursor - end) <= 1e-8, 'Actual GT saved interval coverage is incomplete')
    units = [math.fsum(x) for x in work]
    return dict(electric_kWh=math.fsum(units), unit_electric_kWh=units, window_minutes=[0., end],
                method='stored_actual_source_endpoints_trapezoid', action_or_arrived_setpoint_not_used=True)


def validate_idle_predecessor(outcome, capture):
    require(outcome.get('status') == 'owned_idle_monitor_stopped_no_science'
            and outcome.get('actual_wait_exit_code') == 15 and capture.get('name') == 'normal008_lightweight_run01'
            and capture.get('exit_code') == 15 and outcome.get('scientific_episodes_started') == 0
            and outcome.get('no_008_attempt_after_stop') is True and outcome.get('pid_no_longer_exists') is True
            and outcome.get('old_S3_and_normal03_records_not_modified') is True,
            'Aborted idleRUN01 must retain actual exit15 and zero scientific attempts')
    old_sha = '68701e844ffca923e91f82fff52116d4b18a4357f1239d5c4a416135c8ed95a3'
    require(outcome.get('source_sha256') == outcome.get('preserved_implementation_sha256') == old_sha,
            'Aborted idleRUN01 implementation provenance differs')
    return dict(RUN01_actual_exit_code=15, RUN01_scientific_episodes_started=0,
                RUN01_status='idle_monitor_stopped_cadence_gap_no_science', prior_failure_not_erased=True)


def validate_old_identity(record, plan, manifest, frozen, scene_id, alias):
    require(record.get('model_id') == plan.get('model_id') == manifest.get('execution_model_id') == frozen.get('model_id') == 'M19_model_closeout',
            'Old normal model identity differs')
    spec = frozen['policies'][alias]
    require(plan.get('policy_specs') == [spec] and plan.get('scenario_ids') == [scene_id] and plan.get('registered_pairs') == 1
            and manifest.get('policy_specs_sha256') == canonical_digest([spec]), 'Old actual frozen policy panel differs')
    require(record.get('policy_id') == spec['policy_id'] and record.get('scenario_id') == scene_id
            and [s['id'] for s in manifest.get('scenarios', [])] == [scene_id], 'Old RESULT scenario or policy identity differs')
    require(record.get('manifest_signature') == manifest.get('signature') and record.get('model_config_signature') == manifest.get('model_config_signature')
            and record.get('error') is None and record.get('analysis_error') is None, 'Old RESULT binding/error differs')
    # Legacy M19 manifests retain generic policies/phase metadata. The frozen
    # actual plan/spec SHA, RESULT, journal and terminal identify the real policy.


def validate_resource_identity(result, launch, samples, expected):
    require(result.get('batch') == launch.get('batch') == expected['batch'] and result.get('passed') is True and result.get('exit_code') == 0,
            'Resource result/launch batch identity differs')
    require(type(launch.get('pid')) is int and launch['pid'] > 0 and launch.get('successful_Popen') is True
            and launch.get('owned_child_drained') is True and launch.get('exit_code') == 0, 'Resource launch lacks an actual positive PID')
    require(all(type(s.get('pid')) is int and s['pid'] == launch['pid'] and s.get('batch') == expected['batch'] for s in samples),
            'Resource sample PID/batch differs from actual scientific child')
    q = result.get('qualification') or {}
    for field in ('qualified', 'batch', 'scenario_id', 'policy_id', 'window_minutes', 'result_sha256', 'terminal_sha256', 'raw_sha256'):
        require(q.get(field) == expected.get(field), 'Resource qualification identity differs: ' + field)


def compare_saved_metrics(actual, reported, fields, label):
    maximum = 0.
    for field in fields:
        left, right = number(actual.get(field)), number(reported.get(field)); difference = abs(left - right)
        require(difference <= 1e-6 + 1e-10 * abs(left), 'Saved/report metric differs: ' + label + '/' + field)
        maximum = max(maximum, difference)
    return maximum


def assess_readiness(states, *, normal_command, report_command, report_exists, supervisor):
    require(len(states) == 12 and len({s['batch'] for s in states}) == 12, 'Normal registration denominator differs')
    for label, receipt in (('normal03', normal_command), ('final_readback01', report_command)):
        if receipt is not None and receipt.get('exit_code') != 0:
            return dict(ready=False, state='blocked_external_terminal', reason=label + ' exit not zero', receipt=receipt)
    if supervisor is not None and supervisor.get('status') != 'passed':
        return dict(ready=False, state='blocked_external_terminal', reason='supervision03 did not pass', receipt=supervisor)
    missing = [s['batch'] for s in states if not s['terminal_exists']]
    if missing:
        return dict(ready=False, state='waiting_normal_terminal', pending_batches=missing,
                    terminal_files_present=12 - len(missing), attempt_files_present=sum(s['attempt_exists'] for s in states))
    if normal_command is None:
        return dict(ready=False, state='waiting_normal_supervisor_receipt', pending_batches=[])
    if not report_exists or report_command is None:
        return dict(ready=False, state='waiting_FINAL01_report', pending_batches=[])
    if supervisor is None:
        return dict(ready=False, state='waiting_supervisor_final_qualification', pending_batches=[])
    return dict(ready=True, state='ready_for_readonly_validation', pending_batches=[])


def dependencies():
    from publication import release_gate as gate
    api = gate.load_api(ROOT)
    frozen = read(RUN / 'MODEL_FREEZE_ACCOUNTING01.json')
    binding = api.runner.source_binding(); tests = api.gate.test_binding(); inventory = api.design.runtime_input_inventory(ROOT)
    require(binding == frozen['source_sha256'] and tests == frozen['source_bound_test_gate']['test_files_sha256']
            and inventory == frozen['runtime_inputs'], 'Source/tests/runtime input bytes differ from frozen M20')
    api.runner.validate_final_freeze(frozen, binding, MODEL, frozen['model_config'], list(api.runner.policy_specs().values()), runtime_inputs=inventory)
    jobs = [j for j in api.campaign.campaign_jobs(RUN / 'provenance/v19_design') if j['cohort'] == 'normal']
    require([j['name'] for j in jobs] == [f'v20_normal_{i:03d}' for i in range(12)], 'Expected twelve original immutable normal batches')
    return gate, api, frozen, jobs


def registration_preflight():
    gate, api, frozen, jobs = dependencies(); freeze_sha = sha(RUN / 'MODEL_FREEZE_ACCOUNTING01.json')
    scenes = {}; files = []
    for job in jobs:
        directory = RUN / 'experiments' / job['name']; plan = read(directory / 'plan.json'); manifest = read(directory / 'manifest.json')
        proposal, config = gate.expected_registration(ROOT, job, frozen, api)
        gate.qualify_registration(plan, manifest, job, frozen, freeze_sha, proposal, config, sha(directory / 'manifest.json'), api)
        require(len(job['policies']) == 1, 'One immutable policy per normal batch required')
        scene = manifest['scenarios'][0]; end = number(scene['duration_steps']) * number(scene.get('step_minutes', 15.))
        require(number(scene['normal_duration_hours']) * 60 == end, 'Normal duration metadata differs from actual registered window')
        if scene['id'] in scenes: require(scenes[scene['id']] == scene, 'Normal policy panels differ in complete registered scene')
        scenes[scene['id']] = scene
        files.extend(dict(path=(directory / name).relative_to(ROOT).as_posix(), sha256=sha(directory / name)) for name in ('plan.json', 'manifest.json'))
    require(len(scenes) == 4, 'Exactly four normal windows required')
    hours = [number(s['normal_duration_hours']) for s in scenes.values()]
    require(sorted(hours) == [24., 24., 48., 72.], 'Original normal windows differ')
    return dict(status='prepared', normal_batches=12, normal_windows=4, window_hours=hours,
                registered_files=files, freeze_sha256=freeze_sha, source_files=len(frozen['source_sha256']),
                no_new_registration=True, physical_episodes_started=0)


def validate_panel_matches(jobs, proofs):
    require(len(jobs) == len(proofs) == 12, 'Exactly twelve normal actual batch proofs required')
    by_name = {p['batch']: p for p in proofs}; require(len(by_name) == 12, 'Duplicate actual normal batch proof')
    grouped = defaultdict(list)
    for job in jobs:
        p = by_name[job['name']]; scene = job['scene']; end = number(scene['normal_duration_hours']) * 60.
        require(p['scenario_id'] == scene['id'] and p['window_minutes'] == [0., end]
                and number(scene['duration_steps']) * number(scene.get('step_minutes', 15.)) == end,
                'Normal registered window or identity differs')
        grouped[scene['id']].append((job, p))
    require(len(grouped) == 4, 'Four actual normal scene groups required')
    rows = []
    for scene_id, group in grouped.items():
        require(sorted(j['policies'][0] for j, p in group) == ['C19', 'E', 'K1'], 'Normal policy denominator differs')
        for (j, p), (k, q) in itertools.combinations(group, 2):
            for field in ('registered_scene_sha256', 'source_binding_sha256', 'model_config_sha256', 'runtime_inputs_sha256'):
                require(isinstance(p.get(field), str) and len(p[field]) == 64 and p[field] == q.get(field), 'Normal panels differ: ' + field)
            for field in ('actual_initial_state_sha256', 'registered_stream_sha256'):
                require(isinstance(p['matching'].get(field), str) and len(p['matching'][field]) == 64
                        and p['matching'][field] == q['matching'].get(field), 'Actual normal reset/exogenous stream differs: ' + field)
            rows.append(dict(scenario_id=scene_id, left_alias=j['policies'][0], right_alias=k['policies'][0],
                             left_batch=j['name'], right_batch=k['name'], window_minutes=p['window_minutes'], matched=True,
                             policy_panels_intentionally_distinct=True, actual_initial_state_sha256=p['matching']['actual_initial_state_sha256'],
                             actual_exogenous_sha256=p['matching']['registered_stream_sha256']))
    require(len(rows) == 12, 'Twelve normal same-window pair proofs required')
    return rows


def select_normal_primary(rows, jobs):
    selected = [r for r in rows if r.get('cohort') == 'normal' and r.get('is_primary_window') in (True, 'True')]
    expected = {(j['name'], j['scene']['id'], j['policies'][0]): j for j in jobs}
    require(len(selected) == len(expected) == 12, 'Normal primary table denominator differs')
    seen = set()
    for row in selected:
        key = (row['batch'], row['scenario_id'], row['alias'])
        require(key in expected and key not in seen, 'Duplicate or foreign normal primary row'); seen.add(key)
        end = number(expected[key]['scene']['normal_duration_hours']) * 60.
        require(row.get('window') == 'registered_full' and number(row['window_start_minute']) == 0.
                and number(row['window_end_minute']) == end and row.get('complete') in (True, 'True')
                and row.get('comparison_eligible') in (True, 'True'), 'Incomplete or mismatched registered normal primary window')
    return selected


def resource_projection(result, samples, profile):
    require(result.get('measurements_are_M20') is True, 'Resource record is not an actual M20 observation')
    require(profile.get('source_model_id') == 'M19_model_closeout', 'Historical resource profile identity differs')
    measured = [s for s in samples if s.get('process_sample_status') == 'measured']
    require(result.get('resource_samples') == len(samples)
            and result.get('unknown_process_samples') == len(samples) - len(measured), 'Resource sample denominator differs')
    mapping = dict(sampled_private_peak_bytes='private_bytes', sampled_working_set_peak_bytes='working_set_bytes',
                   observed_OS_lifetime_peak_working_set_bytes='OS_lifetime_peak_working_set_bytes')
    row = dict(batch=result.get('batch'), new_model_id=MODEL, old_profile_model_id='M19_model_closeout',
               resource_samples=len(samples), unknown_process_samples=len(samples) - len(measured),
               old_M19_observed_private_peak_bytes=profile.get('sampled_private_peak_bytes'),
               old_M19_observed_OS_lifetime_peak_working_set_bytes=profile.get('OS_lifetime_peak_working_set_bytes'),
               admission_margin_modified=False, sampled_private_peak_is_not_OS_lifetime_peak=True,
               new_observation_is_not_a_safe_new_admission_bound=True)
    for target, source in mapping.items():
        value = max((number(s[source]) for s in measured), default=None)
        require(result.get(target) == value, 'Actual resource summary differs from samples: ' + target)
        row['new_M20_' + target] = value
    row['new_M20_resource_status'] = 'observed' if measured else 'unknown_no_measured_sample'
    return row


def unknown_resource(batch, profile, reason):
    return dict(batch=batch, new_model_id=MODEL, old_profile_model_id='M19_model_closeout',
        new_M20_resource_status='unknown', unknown_reason=reason, resource_samples=None, unknown_process_samples=None,
        new_M20_sampled_private_peak_bytes=None, new_M20_sampled_working_set_peak_bytes=None,
        new_M20_observed_OS_lifetime_peak_working_set_bytes=None,
        old_M19_observed_private_peak_bytes=profile.get('sampled_private_peak_bytes'),
        old_M19_observed_OS_lifetime_peak_working_set_bytes=profile.get('OS_lifetime_peak_working_set_bytes'),
        admission_margin_modified=False, new_observation_is_not_a_safe_new_admission_bound=True)


def qualified_result_path(row, job, proof, policy_id):
    expected = RUN / 'experiments' / job['name'] / 'pairs' / (job['scene']['id'] + '__' + policy_id) / 'RESULT.json'
    require(Path(row['result_path']).resolve() == expected.resolve() and row.get('policy_id') == policy_id
            and row.get('model_id') == MODEL, 'Report RESULT path or policy/model identity is not the qualified actual pair')
    index = {v['path']: v['sha256'] for v in proof['evidence_files']}
    require(index.get(expected.relative_to(ROOT).as_posix()) == row.get('result_sha256'), 'Report RESULT SHA not bound to strict terminal proof')
    for field in ('source_binding_sha256', 'model_config_sha256', 'runtime_inputs_sha256'):
        require(row.get(field) == proof.get(field), 'Report/actual binding differs: ' + field)
    require(row.get('actual_initial_state_sha256') == proof['matching']['actual_initial_state_sha256']
            and row.get('actual_exogenous_sha256') == proof['matching']['registered_stream_sha256'],
            'Report fingerprint differs from actual terminal matching')
    return expected


def optional(path, issues):
    try: return read(path) if path.is_file() else None
    except (ValueError, OSError) as exc:
        issues.append(dict(path=str(path), state='receipt_read_unknown', error=type(exc).__name__ + ': ' + str(exc))); return None


def last_diagnostic(path):
    if not path.is_file(): return None
    with path.open('rb') as stream:
        stream.seek(0, 2); size = stream.tell(); stream.seek(max(0, size - 131072)); raw = stream.read()
    for line in reversed(raw.decode('utf-8', errors='replace').splitlines()):
        try:
            row = json.loads(line)
            if isinstance(row, dict): return row
        except ValueError: pass
    return dict(state='diagnostic_tail_unknown')


def probe(jobs):
    """Only standard-library evidence reads; no persistent numerical imports."""
    states = [dict(batch=j['name'], terminal_exists=(RUN / 'experiments' / j['name'] / 'BATCH_RESULT.json').is_file(),
                   attempt_exists=(RUN / 'experiments' / j['name'] / 'ATTEMPTS.jsonl').is_file()) for j in jobs]
    required = [SUCCESSOR_CONTRACT, S4 / 'SUMMARY.json', S4 / 'FINAL_406_PROOF.json', SUCCESSOR_CAPTURE,
                RUN / 'logs/final_readback01.command.json', REPORT / 'RECEIPT.json', REPORT / 'SUMMARY.json']
    missing = [str(p) for p in required if not p.is_file()]
    if missing or sum(s['terminal_exists'] for s in states) != 12:
        return dict(ready=False, state='pending_successor_no_project_import', successor='normal008_continuation01/L72_RUN02',
                    missing=missing, normal_states=states, terminal_files_present=sum(s['terminal_exists'] for s in states))
    validate_successor_final(read(S4 / 'SUMMARY.json'), read(S4 / 'FINAL_406_PROOF.json'))
    require(read(SUCCESSOR_CAPTURE).get('exit_code') == 0 and read(RUN / 'logs/final_readback01.command.json').get('exit_code') == 0,
            'Successor or final report actual command failed/unknown')
    return dict(ready=True, state='ready_for_successor_saved_evidence_validation', successor='normal008_continuation01/L72_RUN02', normal_states=states)


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream: return list(csv.DictReader(stream))


def collect():
    """Called only after readiness; independently requalifies every input."""
    prepared = read(HERE / 'PREPARATION.json')
    validate_prepared_dependencies(prepared, dependency_shas())
    validate_prepared_evidence(prepared['continuation_evidence'])
    gate, api, frozen, jobs = dependencies(); require(probe(jobs)['ready'], 'Normal/report readiness not fulfilled')
    inputs = {}

    def bound(path, kind, expected=None):
        path = Path(path).resolve(strict=True); value = sha(path)
        if expected is not None: require(value == expected, 'Changed bound input: ' + str(path))
        row = dict(path=str(path), bytes=path.stat().st_size, sha256=value, kind=kind)
        require(str(path) not in inputs or inputs[str(path)]['sha256'] == value, 'Input changed during analysis')
        inputs[str(path)] = row; return value

    for p in [*continuation_inputs(), RUN / 'logs/final_readback01.command.json', S4 / 'SUMMARY.json', S4 / 'FINAL_406_PROOF.json', SUCCESSOR_CAPTURE]:
        bound(p, 'actual_readiness_terminal_receipt')
    chain_proof = validate_predecessor_stop(read(RUN / 'logs/normal03.command.json'), read(S3 / 'RESULT.json'), read(RESOURCE_RUN / 'SUMMARY.json'))
    successor_summary, successor_proof = validate_successor_files(bound)
    report_receipt = read(REPORT / 'RECEIPT.json'); bound(REPORT / 'RECEIPT.json', 'FINAL01_report_receipt')
    require(report_receipt.get('schema') == 'hospital_ies.v20.report_receipt.v1'
            and report_receipt.get('script_sha256') == frozen['source_sha256']['scripts/v20_report.py']
            and report_receipt.get('raw_opened') is False and report_receipt.get('annual_values_read') is False,
            'FINAL01 is not the frozen read-only report')
    expected_outputs = {n + '.csv' for n in ('STATUS', 'WINDOWS', 'SERVICE', 'ACTUAL_SOURCES', 'V19_V20_COMPARISON',
        'V19_V20_EMISSIONS', 'STRATEGY_COMPARISONS', 'AGGREGATES', 'EMISSIONS')} | {'SUMMARY.json', 'INPUT_INDEX.json'}
    require(set(report_receipt.get('output_files', {})) == expected_outputs, 'Incomplete FINAL01 output census')
    for name, expected in report_receipt['output_files'].items(): bound(REPORT / name, 'FINAL01_output', expected)
    summary = read(REPORT / 'SUMMARY.json')
    require(summary.get('expected_pairs') == summary.get('actual_started_pairs') == summary.get('actual_finished_pairs')
            == summary.get('complete_registered_full_pairs') == summary.get('eligible_terminal_pairs') == 406
            and summary.get('unknown_full_pairs') == 0 and summary.get('status_counts') == {'completed': 406},
            'FINAL01 is not a complete eligible canonical report')
    index = {v['path']: v['sha256'] for v in read(REPORT / 'INPUT_INDEX.json')}
    table = {name: csv_rows(REPORT / (name + '.csv')) for name in ('WINDOWS', 'V19_V20_COMPARISON', 'EMISSIONS', 'V19_V20_EMISSIONS', 'STRATEGY_COMPARISONS')}
    windows = select_normal_primary(table['WINDOWS'], jobs)
    wm = {(w['scenario_id'], w['alias']): w for w in windows}
    versions = {(r['scenario_id'], r['alias']): r for r in table['V19_V20_COMPARISON'] if r['cohort'] == 'normal' and r['is_primary_window'] == 'True'}
    emissions = {(r['scenario_id'], r['alias'], r['emissions_factor_case_id']): r for r in table['EMISSIONS'] if r['cohort'] == 'normal' and r['is_primary_window'] == 'True'}
    oldemissions = {(r['scenario_id'], r['alias'], r['factor_case_id']): r for r in table['V19_V20_EMISSIONS'] if r['cohort'] == 'normal' and r['is_primary_window'] == 'True'}
    require(set(wm) == set(versions) and len(emissions) == len(oldemissions) == 48, 'Normal version/factor output denominator differs')
    registry_path = RUN / 'references/EMISSIONS_FACTOR_REGISTRY.json'; bound(registry_path, 'frozen_factor_registry', frozen['source_sha256'][registry_path.relative_to(ROOT).as_posix()]); registry = read(registry_path)
    proofs = [gate.qualify_registered_batch(ROOT, j, frozen, sha(RUN / 'MODEL_FREEZE_ACCOUNTING01.json'), api=api) for j in jobs]
    proof_index = {p['batch']: p for p in proofs}
    cross_panel = validate_panel_matches(jobs, proofs)
    for proof in proofs:
        for row in proof['evidence_files']:
            path = ROOT / row['path']; bound(path, 'strict_normal_evidence', row['sha256'])
            if path.name in ('RESULT.json', 'plan.json', 'manifest.json', 'ATTEMPTS.jsonl', 'BATCH_RESULT.json'):
                require(index.get(str(path)) == row['sha256'], 'Normal evidence not bound by FINAL01 INPUT_INDEX')
    old_root = Path(summary['baseline_root']); require(old_root.is_dir(), 'Complete old source-bound baseline unavailable')
    old_freeze_path = old_root / 'outputs/v19_model_closeout_20261018_run01/MODEL_FREEZE_CLOSEOUT01.json'
    old_freeze = read(old_freeze_path); bound(old_freeze_path, 'V19_final_freeze')
    profile_path = PROFILE / 'V19_R2_PROFILE.json'; profile = read(profile_path); bound(profile_path, 'old_M19_resource_profile')
    require(profile['source_model_id'] == 'M19_model_closeout' and profile['is_M20_measurement'] is False
            and profile['growth_factor'] == 1.2 and profile['host_reserve_bytes'] == 6 * 1024**3,
            'Reviewed historical admission profile differs')
    require(profile.get('signature') == gate.digest({k: v for k, v in profile.items() if k != 'signature'}), 'Historical resource profile signature differs')
    bound(PROFILE / 'V19_R2_SOURCE_SUMMARY.json', 'actual_historical_R2_source_receipt', profile['source_summary_sha256'])
    resource_samples_path = RESOURCE_RUN / 'SAMPLES.jsonl'; resource_samples = []
    resource_supervisor = None
    if resource_samples_path.is_file():
        bound(resource_samples_path, 'actual_M20_resource_samples')
        resource_samples = [json.loads(line) for line in resource_samples_path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if (RESOURCE_RUN / 'SUMMARY.json').is_file():
        bound(RESOURCE_RUN / 'SUMMARY.json', 'actual_M20_normal_resource_supervisor'); resource_supervisor = read(RESOURCE_RUN / 'SUMMARY.json')
    prior_samples = [json.loads(line) for line in (PRIOR_RESOURCE_RUN / 'SAMPLES.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    require(resource_supervisor is not None, 'New resource supervisor receipt unavailable')
    resource_chain = validate_successor_final(successor_summary, successor_proof)
    require(prior_samples == [], 'Original009 sampling must remain empty/unknown')
    require(successor_proof['signature'] == gate.digest({k:v for k,v in successor_proof.items() if k != 'signature'})
            and successor_proof['freeze_sha256'] == sha(RUN / 'MODEL_FREEZE_ACCOUNTING01.json')
            and successor_proof['source_binding_sha256'] == gate.digest(frozen['source_sha256'])
            and successor_proof['runtime_inputs_sha256'] == gate.digest(frozen['runtime_inputs']), 'S4 qualified proof source/config binding differs')
    new_samples = [json.loads(line) for line in (S4 / 'SAMPLES.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    require(all(s.get('batch') == 'v20_normal_008' for s in new_samples), 'New008 sampling contains a foreign batch')
    require(not any(s.get('batch') == 'v20_normal_009' for s in resource_samples), 'Strictly skipped009 cannot have new samples')
    service_rows = []; resources = []; sensitivity = []; version_rows = []; version_proofs = []; measured = []
    from tools.model_closeout.emissions import evaluate_emissions
    reconciliation = defaultdict(float)

    def equal(a, b, label):
        difference = abs(number(a) - number(b)); reconciliation[label] = max(reconciliation[label], difference)
        require(difference <= 1e-6 + 1e-10 * abs(number(b)), 'Saved numeric reconciliation failed: ' + label)

    for job in jobs:
        alias = job['policies'][0]; scene_id = job['scene']['id']; key = (scene_id, alias); w = wm[key]; v = versions[key]
        policy_id = frozen['policies'][alias]['policy_id']
        path = qualified_result_path(w, job, proof_index[job['name']], policy_id)
        bound(path, 'normal_actual_RESULT', w['result_sha256']); record = read(path)
        end = number(job['scene']['normal_duration_hours']) * 60.; ledger = record['analysis']['v20']['service_ledgers']['registered_full']
        window = record['analysis']['v18']['windows']['registered_full']['all_registered']
        common = dict(batch=job['name'], scenario_id=scene_id, alias=alias, window='registered_full', start_minute=0., end_minute=end,
                      duration_hours=end / 60., source_day_index=job['scene']['source_day_index'])
        row = dict(common)
        for source in SOURCE_FIELDS: row[source] = number(ledger['actual_source_integrals'][source]['value'])
        for field in api.report.ECON_FIELDS: row[field] = number(w[field]); equal(row[field], window['economics'][field], 'window_economics')
        equal(row['GRID_electric_kWh'], row['grid_purchase_kWh'], 'grid_source_economics')
        equal(row['DG_electric_kWh'], row['DG_electric_work_kWh'], 'DG_source_economics')
        equal(row['actual_energy_fee_USD'], sum(row[k] for k in ('grid_fee_USD', 'GT_gas_fee_USD', 'boiler_gas_fee_USD', 'DG_original_electric_fee_USD')), 'fee_components')
        row['DG_shaft_repriced_energy_proxy_USD'] = row['actual_energy_fee_USD'] - row['DG_original_electric_fee_USD'] + row['DG_shaft_equivalent_fee_USD']
        for field in ('DG_budget_start_kWh_equivalent', 'DG_budget_finish_kWh_equivalent'): row[field] = number(ledger[field])
        row['observed_DG_exhaustion_minutes'] = ledger['observed_DG_exhaustion_minutes']
        equal(row['DG_budget_start_kWh_equivalent'] - row['DG_budget_finish_kWh_equivalent'], row['DG_fuel_budget_debit_kWh_equivalent'], 'DG_budget_delta')
        saved_new_metrics = api.report.window_metrics(record, 'registered_full', [0., end], expected_model=MODEL)
        service_metric_fields = [p + '_material_union_minutes' for p in ('P0', 'P1', 'P2', 'P3')] + [p + '_' + c + '_' + f for p in ('P0', 'P1', 'P2', 'P3') for c in ('e', 'h', 'c') for f in SERVICE_FIELDS]
        reconciliation['new_report_service_readback'] = max(reconciliation['new_report_service_readback'], compare_saved_metrics(saved_new_metrics, w, service_metric_fields, 'new_normal'))
        for p in ('P0', 'P1', 'P2', 'P3'):
            row[p + '_material_union_minutes'] = number(w[p + '_material_union_minutes'])
            for c in ('e', 'h', 'c'):
                actual = ledger['all_registered'][p][c]; legacy = window['carrier_service'][p][c]
                item = dict(common, priority=p, carrier=c)
                for field in SERVICE_FIELDS: item[field] = number(actual[field])
                for field in ('ENS_kWh', 'disconnected_ENS_kWh', 'connected_unserved_ENS_kWh'): equal(actual[field], legacy[field], 'service_ledger_classifier')
                for field in ('material_minutes', 'longest_material_minutes', 'first_loss_minute', 'first_clean_minute',
                              'confirmed_30min_interval', 'recurrence_after_30min_count', 'recovery_status', 'loss_runs'):
                    item[field] = legacy.get(field)
                service_rows.append(item)
                for field in SERVICE_FIELDS: row[p + '_' + c + '_' + field] = item[field]
        # Independently bind and qualify the saved old-version normal, without
        # requiring identical reset states after a load-accounting correction.
        require(v.get('comparison_valid') == v.get('old_complete') == v.get('old_execution_qualified') == 'True', 'Old normal comparison is unknown/ineligible')
        op = Path(v['old_result_path']); bound(op, 'old_normal_RESULT', v['old_result_sha256']); require(index.get(str(op)) == v['old_result_sha256'], 'Old RESULT absent from FINAL01 INPUT_INDEX')
        old_record = read(op); od = op.parents[2]; nd = path.parents[2]
        old_meta = {}; new_meta = {}
        for directory, target in ((od, old_meta), (nd, new_meta)):
            for name in ('plan.json', 'manifest.json', 'BATCH_RESULT.json', 'ATTEMPTS.jsonl'):
                pth = directory / name; bound(pth, 'normal_version_binding', index.get(str(pth))); require(str(pth) in index, 'Version binding not listed in INPUT_INDEX')
                target[name] = read(pth) if name.endswith('.json') else [json.loads(line) for line in pth.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        pl, mf, tr = old_meta['plan.json'], old_meta['manifest.json'], old_meta['BATCH_RESULT.json']
        validate_old_identity(old_record, pl, mf, old_freeze, scene_id, alias)
        require(pl['source_sha256'] == mf['source_sha256'] == old_freeze['source_sha256']
                and mf['source_freeze_artifact']['sha256'] == sha(old_freeze_path), 'Old normal source/freeze binding differs')
        require(pl['manifest_sha256'] == tr['manifest_sha256'] == sha(od / 'manifest.json')
                and tr['plan_sha256'] == sha(od / 'plan.json') and mf['signature'] == gate.digest({k: val for k, val in mf.items() if k != 'signature'})
                and pl['manifest_signature'] == mf['signature'], 'Old normal terminal/registration bytes differ')
        require(tr['registered_pairs'] == tr['actual_attempts'] == tr['complete_full'] == 1
                and tr['unknown_full'] == 0 and tr['analysis_failure_or_unavailable_count'] == 0
                and tr['status_counts'] == {'completed': 1} and tr['source_unchanged'] is True, 'Old normal not strictly complete')
        qualified, reasons = api.report.execution_qualification(old_record, old_meta['ATTEMPTS.jsonl'], op, audit_key='v19')
        require(qualified and len(old_meta['ATTEMPTS.jsonl']) == 2, 'Old normal actual execution qualification failed: ' + str(reasons))
        require(old_record['manifest_signature'] == mf['signature'] and old_record['model_config_signature'] == mf['model_config_signature'], 'Old RESULT registration differs')
        os_scene = mf['scenarios'][0]; ns_scene = new_meta['manifest.json']['scenarios'][0]
        differences = {k: [os_scene.get(k), ns_scene.get(k)] for k in os_scene.keys() | ns_scene.keys() if os_scene.get(k) != ns_scene.get(k)}
        require(set(differences) <= META_DIFFERENCES, 'Unreviewed cross-version normal scene difference')
        require(pl['model_config'] == mf['model_config'] == new_meta['plan.json']['model_config'] == frozen['model_config'], 'Cross-version normal physical config differs')
        require(pl['policy_specs'][0]['config'] == new_meta['plan.json']['policy_specs'][0]['config'], 'Cross-version policy preference differs')
        om = gate.qualify_matching(tr['matching_evidence'][scene_id], os_scene, 1)
        nm = gate.qualify_matching(new_meta['BATCH_RESULT.json']['matching_evidence'][scene_id], ns_scene, 1)
        require(om['registered_stream_sha256'] == nm['registered_stream_sha256'], 'Actual cross-version exogenous stream differs')
        old_inputs = {k['path']: k['sha256'] for k in mf['files']}; new_inputs = {k['path']: k['sha256'] for k in new_meta['manifest.json']['files']}
        shared = old_inputs.keys() & new_inputs.keys(); require(len(shared) >= 26 and all(old_inputs[k] == new_inputs[k] for k in shared), 'Shared physical input SHA differs')
        old_window = old_record['analysis']['v18']['windows']['registered_full']
        require(old_window['window_minutes'] == [0., end] and old_window['complete'] is True and old_window['unknown_tail_minutes'] is None
                and old_record['analysis']['observed_until_minute'] == end, 'Old normal registered coverage differs')
        saved_old_metrics = api.report.window_metrics(old_record, 'registered_full', [0., end], expected_model='M19_model_closeout')
        old_service_fields = [p + '_material_union_minutes' for p in ('P0', 'P1', 'P2', 'P3')] + [p + '_' + c + '_ENS_kWh' for p in ('P0', 'P1', 'P2', 'P3') for c in ('e', 'h', 'c')]
        reconciliation['old_report_service_readback'] = max(reconciliation['old_report_service_readback'], compare_saved_metrics(saved_old_metrics, {f: v['old_' + f] for f in old_service_fields}, old_service_fields, 'old_normal'))
        comparison = dict(common, comparison_valid=True, old_request_delivery_unknown=True)
        actual_gt = {}
        for version, directory, terminal, saved_record in [('old', od, tr, old_record), ('new', nd, new_meta['BATCH_RESULT.json'], record)]:
            gt_csv = directory / 'analysis/ENGINEERING_INTERVALS.csv'
            gt_expected = terminal['analysis_receipt']['compatibility_export_receipt']['output_sha256']['ENGINEERING_INTERVALS.csv']
            bound(gt_csv, version + '_actual_GT_saved_intervals', gt_expected)
            actual_gt[version] = integrate_actual_gt(csv_rows(gt_csv), end, scene_id, saved_record['policy_id'], saved_record['raw_sha256'])
            comparison[version + '_GT_electric_kWh'] = actual_gt[version]['electric_kWh']
            comparison[version + '_GT_unit_electric_kWh'] = actual_gt[version]['unit_electric_kWh']
            actual_window = saved_record['analysis']['v18']['windows']['registered_full']['all_registered']
            for field in ('GT_unit_online_hours', 'GT_actual_start_count'):
                comparison[version + '_' + field] = number(actual_window[field])
            comparison[version + '_GT_actual_running_minutes'] = {device: number(v['observed_running_minutes']) for device, v in actual_window['GT_actual'].items()}
        equal(actual_gt['new']['electric_kWh'], row['GT_electric_kWh'], 'new_actual_GT_CSV_vs_source_ledger')
        for field in ('GT_electric_kWh', 'GT_unit_online_hours', 'GT_actual_start_count'):
            comparison['delta_' + field] = comparison['new_' + field] - comparison['old_' + field]

        for field in api.report.ECON_FIELDS:
            old_value = number(v['old_' + field]); equal(old_value, old_window['all_registered']['economics'][field], 'old_saved_economics')
            comparison['old_' + field] = old_value; comparison['new_' + field] = row[field]; comparison['delta_' + field] = row[field] - old_value
        for p in ('P0', 'P1', 'P2', 'P3'):
            field = p + '_material_union_minutes'; comparison['old_' + field] = number(v['old_' + field]); comparison['new_' + field] = row[field]
            comparison['delta_' + field] = row[field] - comparison['old_' + field]
            for c in ('e', 'h', 'c'):
                field = p + '_' + c + '_ENS_kWh'; comparison['old_' + field] = number(v['old_' + field]); comparison['new_' + field] = row[field]
                comparison['delta_' + field] = row[field] - comparison['old_' + field]
        for case, factor in registry['cases'].items():
            require(emissions[scene_id, alias, case]['emissions_factor_sha256'] == registry['case_canonical_sha256'][case]
                    and oldemissions[scene_id, alias, case]['factor_case_sha256'] == registry['case_canonical_sha256'][case],
                    'Reported factor case SHA differs from frozen registry')
            values = {field: row[field] for field in api.report.ECON_FIELDS}; values['DG_fuel_budget_debit_shaft_equivalent_kWh'] = row['DG_fuel_budget_debit_kWh_equivalent']
            old_values = {field: comparison['old_' + field] for field in api.report.ECON_FIELDS}; old_values['DG_fuel_budget_debit_shaft_equivalent_kWh'] = comparison['old_DG_fuel_budget_debit_kWh_equivalent']
            calculated = evaluate_emissions([values], factor)[0]; old_calculated = evaluate_emissions([old_values], factor)[0]
            require(calculated['DG_exact_CO2_unknown'] is True, 'Exact diesel chemical CO2 must remain unknown')
            sr = dict(common, case_id=case, factor_sha256=registry['case_canonical_sha256'][case], exact_DG_CO2_unknown=True)
            for field in CO2_FIELDS:
                equal(calculated[field], emissions[scene_id, alias, case][field], 'new_factor_recalculation')
                equal(old_calculated[field], oldemissions[scene_id, alias, case]['old_' + field], 'old_factor_recalculation')
                sr['new_' + field] = number(calculated[field]); sr['old_' + field] = number(old_calculated[field]); sr['delta_' + field] = sr['new_' + field] - sr['old_' + field]
                if case == registry['main_case_id']:
                    row[field] = sr['new_' + field]; comparison['old_' + field] = sr['old_' + field]; comparison['new_' + field] = sr['new_' + field]; comparison['delta_' + field] = sr['delta_' + field]
            sensitivity.append(sr)
        resources.append(row); version_rows.append(comparison)
        version_proofs.append(dict(batch=job['name'], scenario_id=scene_id, alias=alias, window_minutes=[0., end],
            old_result_sha256=sha(op), new_result_sha256=sha(path), scene_metadata_differences=differences,
            old_model='M19_model_closeout', new_model=MODEL, source_versions_intentionally_different=True,
            old_policy_id=old_record['policy_id'], new_policy_id=record['policy_id'],
            old_manifest_legacy_policies=mf.get('policies'), old_plan_phase=pl.get('phase'), actual_old_plan_spec_sha256=mf['policy_specs_sha256'],
            same_physical_config=True, same_preference_config=True, common_input_SHA_equal=True,
            same_actual_applied_exogenous_definition=True, old_actual_reset_sha256=om['actual_initial_state_sha256'],
            actual_GT_old_csv_sha256=sha(od / 'analysis/ENGINEERING_INTERVALS.csv'),
            actual_GT_new_csv_sha256=sha(nd / 'analysis/ENGINEERING_INTERVALS.csv'),
            actual_GT_uses_stored_linear_source_endpoints=True,
            new_actual_reset_sha256=nm['actual_initial_state_sha256'], cross_version_reset_equality_not_required=True))
        # New measurements and old admission profiles remain separate columns.
        context = resource_context(job['name'], resource_supervisor, successor_summary)
        own_resource_run = context['directory']
        rp = own_resource_run / (job['name'] + '.result.json')
        old_profile = next(p for p in profile['profiles'] if p['alias'] == alias and p['scenario_id'] == scene_id)
        require(old_profile['original_manifest_sha256'] == sha(od / 'manifest.json'), 'Historical resource profile does not bind the same normal specification')
        if rp.is_file(): bound(rp, 'actual_M20_resource_result_available')
        if job['name'] == 'v20_normal_009':
            require(not rp.exists(), 'Skipped009 cannot possess a new resource result')
            rm = unknown_resource(job['name'], old_profile, 'Actual009 completed under failed normal02; 0-row prior SAMPLES; skipped under normal03; no M20 peak imputed')
            rm['prior_interrupted_receipt_sha256'] = sha(PRIOR_RESOURCE_RUN / 'v20_normal_009.interrupted.json')
            rm['prior_empty_samples_sha256'] = sha(PRIOR_RESOURCE_RUN / 'SAMPLES.jsonl')
            rm['new_supervisor_strict_skip'] = True
        elif rp.is_file():
            bound(rp, 'actual_M20_resource_result'); rr = read(rp)
            launches = ([successor_summary['last_child']] if job['name'] == 'v20_normal_008' else
                        [v for v in resource_supervisor.get('successful_Popen_proofs', []) if v.get('batch') == job['name']])
            require(len(launches) == 1, 'Resource observation has no unique launch proof')
            own_samples = (new_samples if job['name'] == 'v20_normal_008' else [s for s in resource_samples if s.get('batch') == job['name']])
            expected_resource = dict(qualified=True, batch=job['name'], scenario_id=scene_id, policy_id=policy_id,
                window_minutes=[0., end], result_sha256=sha(path), terminal_sha256=sha(nd / 'BATCH_RESULT.json'), raw_sha256=record['raw_sha256'])
            validate_resource_identity(rr, launches[0], own_samples, expected_resource)
            cp = own_resource_run / (job['name'] + '.command.json'); bound(cp, 'actual_resource_scientific_command'); command = read(cp)
            expected_tail = ['-B', '-m', 'tools.load_accounting.runner', 'run', '--directory', nd.relative_to(ROOT).as_posix()]
            require(Path(command['cwd']).resolve() == ROOT and command['command'][1:] == expected_tail,
                    'Resource command is not the canonical scientific child')
            rm = resource_projection(rr, own_samples, old_profile)
            rm['actual_scientific_pid'] = launches[0]['pid']
            if job['name'] == 'v20_normal_008':
                admission = rr['admission']; require(admission['growth_factor'] == 1.1 and admission['selected_reserve_gib'] in (2,3)
                    and admission['stable_observed_seconds'] >= 30., 'New008 resource admission protocol differs')
                require(admission['historical_profile'] == old_profile, 'New008 admission profile is not same72h historicalC19')
                rm.update(admission_margin_modified=True, resource_growth_factor=1.1,
                    resource_selected_reserve_gib=admission['selected_reserve_gib'], resource_stable_seconds=admission['stable_observed_seconds'],
                    empirical_resource_budget_is_not_scientific_threshold=True)
            else: rm.update(resource_growth_factor=1.2, resource_selected_reserve_gib=6.)
        else:
            rm = unknown_resource(job['name'], old_profile, 'Missing saved result/sample/supervisor proof; no new peak imputed')
        rm.update(common); rm['resource_origin'] = context['origin']; measured.append(rm)
    require(len(resources) == len(version_rows) == len(measured) == 12 and len(service_rows) == 144 and len(sensitivity) == 48, 'Extracted normal table denominator differs')
    paired = []
    resource_index = {(r['scenario_id'], r['alias']): r for r in resources}
    for proof in cross_panel:
        left = resource_index[proof['scenario_id'], proof['left_alias']]; right = resource_index[proof['scenario_id'], proof['right_alias']]
        item = dict(proof)
        for field in [*api.report.ECON_FIELDS, *CO2_FIELDS, *SOURCE_FIELDS,
                      *[p + '_material_union_minutes' for p in ('P0', 'P1', 'P2', 'P3')],
                      *[p + '_' + c + '_' + f for p in ('P0', 'P1', 'P2', 'P3') for c in ('e', 'h', 'c') for f in SERVICE_FIELDS]]:
            item['left_' + field] = left[field]; item['right_' + field] = right[field]; item['delta_right_minus_left_' + field] = right[field] - left[field]
        paired.append(item)
    # Recheck every consumed byte before any numerical result is published.
    for item in inputs.values(): require(sha(item['path']) == item['sha256'], 'Input changed during normal extraction')
    outputs = []
    for name, rows in [('NORMAL_SOURCES_COST_CO2', resources), ('SERVICE_PRIORITY_CARRIER', service_rows),
                       ('FACTOR_SENSITIVITY', sensitivity), ('V19_VERSION_COMPARISON', version_rows),
                       ('MATCHED_POLICY_PAIRS', paired), ('RESOURCE_MEASUREMENTS', measured)]:
        path = HERE / (name + '.csv'); fields = list(dict.fromkeys(k for row in rows for k in row))
        with path.open('x', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
            writer.writerows({k: json.dumps(v, ensure_ascii=False, allow_nan=False) if isinstance(v, (list, dict)) else v for k, v in row.items()} for row in rows)
        outputs.append(dict(path=path.relative_to(ROOT).as_posix(), bytes=path.stat().st_size, sha256=sha(path), rows=len(rows)))
    match_path = HERE / 'MATCH_PROOFS.json'; write_new(match_path, dict(normal_batch_proofs=proofs, same_window_policy_pairs=cross_panel, version_pairs=version_proofs))
    outputs.append(dict(path=match_path.relative_to(ROOT).as_posix(), bytes=match_path.stat().st_size, sha256=sha(match_path)))
    render_document(resources, service_rows, sensitivity, version_rows, measured)
    with DOC.open('a', encoding='utf-8') as stream:
        stream.write('\nContinuation provenance: original normal02 exit2, supervision02 failure, and blocked analysis01 are retained by SHA. This analysis accepts only the successful S4 L72_RUN02 successor after actual normal03 intentional125 and failed supervision03. Ten canonical02 measurements and one new008 measurement remain distinct; normal009 empty sampling leaves its M20 peaks unknown. Historical R2 estimates remain separate.\n')
    outputs.append(dict(path=DOC.relative_to(ROOT).as_posix(), bytes=DOC.stat().st_size, sha256=sha(DOC)))
    receipt = dict(schema='hospital_ies.v20.normal_window_analysis.v3', status='passed', utc=now(),
        source_script_sha256=sha(Path(__file__)), continuation_chain=chain_proof, resource_continuation=resource_chain, consumed_report='FINAL01', report_receipt_sha256=sha(REPORT / 'RECEIPT.json'),
        normal_batches=12, scene_windows=4, actual_qualified_trajectories=12, policy_pair_comparisons=12,
        requested_delivered_service_rows=144, factor_rows=48, no_cross_window_total_or_ranking=True,
        inputs=list(inputs.values()), outputs=outputs, maximum_numeric_reconciliation_errors=dict(reconciliation),
        actual_M20_resources_distinct_from_old_M19_profile=True, admission_margin_modified=True,
        resource_admission_change_scope='new008 external1.1 plus selected2or3GiB; canonical02 retains1.2 plus6GiB; frozen science unchanged',
        old_request_delivery_remains_unknown=True, diesel_exact_chemical_CO2_unknown=True,
        physical_episodes_started=0, raw_files_opened=0, annual_values_parsed=0)
    receipt['signature'] = gate.digest(receipt); write_new(HERE / 'COLLECTION_RECEIPT.json', receipt)
    return receipt


def render_document(resources, services, factors, versions, measurements):
    lines = ['# 正常窗口的同窗工程分析', '',
        '状态：12条正常轨迹已逐批通过严格终态、完整registered_full账本和跨策略实际匹配；本表消费已完成的FINAL01冻结报告。未新跑调度。', '',
        '四窗口分别为24、48、72、24小时，每个窗口单列E/K1/C19。不同窗口的总费用、能源、ENS或排放不相加排名，不外推全年。公共母线没有可识别的源到P0/P1能流分配。', '',
        '费用沿用原示例价格，实际能源费=购电+GT气+锅炉气+DG原电量费；账本成本另列。轴功费用仅作替代诊断，不能与原DG费用重复相加。排放主情景为CAMX2023r2_DG75pct，天然气/柴油按HHV登记；grid+gas为可算小计，DG及总量仍为条件值，exact-DG CO₂未知。', '',
        '| 场景 | 小时 | 策略 | 实际能源费 USD | grid+gas CO₂ kg | DG条件CO₂ kg | 条件总CO₂ kg |',
        '|---|---:|---|---:|---:|---:|---:|']
    for row in resources:
        lines.append(f"| {row['scenario_id']} | {row['duration_hours']:.0f} | {row['alias']} | {row['actual_energy_fee_USD']:.3f} | {row['grid_plus_gas_CO2_kg']:.3f} | {row['DG_CO2_kg_conditional']:.3f} | {row['total_CO2_kg_conditional']:.3f} |")
    lines.extend(['', '## 实际源积分', '',
        '| 场景 | 策略 | GRID MWh电 | GT MWh电 | HRSG klb蒸汽 | 锅炉 klb蒸汽 | EC MWh冷 | ABS MWh冷 | 塔 MWh排热 | DG kWh电 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|'])
    for row in resources:
        fields = [('GRID_electric_kWh', 1000), ('GT_electric_kWh', 1000), ('HRSG_steam_lb', 1000), ('BOILER_steam_lb', 1000),
                  ('EC_cooling_kWh', 1000), ('ABS_cooling_kWh', 1000), ('TOWER_rejection_kWh', 1000), ('DG_electric_kWh', 1)]
        lines.append('| ' + row['scenario_id'] + ' | ' + row['alias'] + ' | ' + ' | '.join(f'{row[k]/scale:.3f}' for k, scale in fields) + ' |')
    lines.extend(['', '## 关键服务', '', 'ENS按e/h/c各自kWh单位分别列出；实质分钟为各优先级跨载能时间并集。', '',
        '| 场景 | 策略 | P0 e/h/c ENS | P1 e/h/c ENS | P0并集 min | P1并集 min |',
        '|---|---|---|---|---:|---:|'])
    for row in resources:
        parts = [' / '.join(f"{row[p+'_'+c+'_ENS_kWh']:.6g}" for c in ('e', 'h', 'c')) for p in ('P0', 'P1')]
        lines.append(f"| {row['scenario_id']} | {row['alias']} | {parts[0]} | {parts[1]} | {row['P0_material_union_minutes']:.6g} | {row['P1_material_union_minutes']:.6g} |")
    lines.extend(['', '## 一般服务请求与交付', '', '每个单元依次为e/h/c各自kWh；不同载能不相加。主动断开与连接少供分项保留在完整CSV。', '',
        '| 场景 | 策略 | P2请求 e/h/c | P2交付 e/h/c | P2 ENS e/h/c | P3请求 e/h/c | P3交付 e/h/c | P3 ENS e/h/c |',
        '|---|---|---|---|---|---|---|---|'])
    for row in resources:
        parts = [' / '.join(f"{row[p+'_'+c+'_'+field]:.6g}" for c in ('e', 'h', 'c'))
                 for p in ('P2', 'P3') for field in ('requested_kWh', 'delivered_kWh', 'ENS_kWh')]
        lines.append('| ' + row['scenario_id'] + ' | ' + row['alias'] + ' | ' + ' | '.join(parts) + ' |')
    lines.extend(['', '## 相同轨迹的四套因子', '',
        '条件总CO₂，kg；列分别为revision2+DG75%、revision2+DG100%、revision2+DG25%、旧Hub电网版次+DG75%。', '',
        '| 场景 | 策略 | r2 DG75 | r2 DG100 | r2 DG25 | 旧Hub DG75 |', '|---|---|---:|---:|---:|---:|'])
    factors_by_key = {(r['scenario_id'], r['alias'], r['case_id']): r for r in factors}
    cases = ('CAMX2023r2_DG75pct', 'CAMX2023r2_DG100pct', 'CAMX2023r2_DG25pct', 'CAMX2023_originalHub_DG75pct')
    for row in resources:
        values = [f"{factors_by_key[row['scenario_id'],row['alias'],case]['new_total_CO2_kg_conditional']:.3f}" for case in cases]
        lines.append('| ' + row['scenario_id'] + ' | ' + row['alias'] + ' | ' + ' | '.join(values) + ' |')
    lines.extend(['', '## 同策略版本变化', '', 'M20减M19；每行仍为相同登记窗口，不做四窗口总计。旧请求/交付未知，不重构。', '',
        '| 场景 | 策略 | 能源费变化 USD | 条件CO₂变化 kg | P0并集变化 min | P1并集变化 min |', '|---|---|---:|---:|---:|---:|'])
    for row in versions:
        values = [f"{row[k]:+.6g}" for k in ('delta_actual_energy_fee_USD', 'delta_total_CO2_kg_conditional', 'delta_P0_material_union_minutes', 'delta_P1_material_union_minutes')]
        lines.append('| ' + row['scenario_id'] + ' | ' + row['alias'] + ' | ' + ' | '.join(values) + ' |')
    lines.extend(['', '## 实际运行资源与历史准入依据', '',
        '| 场景 | 策略 | 新M20采样RSS峰 GiB | 新M20采样private峰 GiB | 新M20 OS峰读数 GiB | 旧M19 private峰 GiB | 样本/未知 |',
        '|---|---|---:|---:|---:|---:|---|'])
    for row in measurements:
        def gib(key): return 'unknown' if row.get(key) is None else f'{row[key]/1024**3:.3f}'
        values = [gib(k) for k in ('new_M20_sampled_working_set_peak_bytes', 'new_M20_sampled_private_peak_bytes',
                  'new_M20_observed_OS_lifetime_peak_working_set_bytes', 'old_M19_observed_private_peak_bytes')]
        count = 'unknown' if row['resource_samples'] is None else str(row['resource_samples']) + '/' + str(row['unknown_process_samples'])
        lines.append('| ' + row['scenario_id'] + ' | ' + row['alias'] + ' | ' + ' | '.join(values) + ' | ' + count + ' |')
    lines.extend(['', '逐P0/P1/P2/P3及e/h/c的请求、交付、ENS、主动断开和连接少供、实质分钟、最长段、恢复及复发字段见SERVICE_PRIORITY_CARRIER.csv（144行）。零ENS、零实质分钟和温度覆盖状态不互相替代。源表保留GRID/GT/DG电量、EC/ABS冷量、塔排热及HRSG/锅炉蒸汽lb；不同载能不可直接相加。', '',
        'FACTOR_SENSITIVITY.csv保留同12条轨迹的四套因子共48行，不是48次新试验；代表机组油耗点及旧电网版次不是统计区间或严格上下界。V19_VERSION_COMPARISON.csv保留同规格同偏好版本对照；两版本自身分别source-bound并满足实际资格，源码/模型/分组和reset可有意不同，不是同一源模型复跑。旧请求/交付缺账本仍unknown。', '',
        '新M20实际资源观测与旧M19 R2准入依据见RESOURCE_MEASUREMENTS.csv。sampled RSS/private峰仅覆盖已采样时刻；OS生命周期峰是采样读取到的OS计数，末次采样至退出仍可能有遗漏。私有内存不是RSS。Canonical02 used its original 1.2 factor plus6GiB resource protocol for ten measured runs. New008 used the explicitly authorized same72h-C19 historical profile, factor1.1 plus its actual selected2or3GiB reserve after a sampled stable window of at least30s. These are resource orchestration protocols, not physical-model or service-failure thresholds; no24h low peak is used to reset72h admission.', '',
        '全部输入与输出SHA、报告receipt/INPUT_INDEX/终态交叉绑定、四窗口12个跨策略匹配及新旧版本证明见MATCH_PROOFS.json和COLLECTION_RECEIPT.json。同窗差值见MATCHED_POLICY_PAIRS.csv；本说明不提供跨不等长窗口的合计排名。', ''])
    prefix = '../../outputs/v20_load_accounting_20261019_run01/review/normal_window_analysis03/'
    lines.extend(['## 表与证据', '', *['- [' + name + '](' + prefix + name + ')' for name in
        ('NORMAL_SOURCES_COST_CO2.csv', 'SERVICE_PRIORITY_CARRIER.csv', 'FACTOR_SENSITIVITY.csv', 'V19_VERSION_COMPARISON.csv',
         'MATCHED_POLICY_PAIRS.csv', 'RESOURCE_MEASUREMENTS.csv', 'MATCH_PROOFS.json', 'COLLECTION_RECEIPT.json')], ''])
    lines.extend(['', '## GT and DG actual-operation version readback', '', '| Scenario | Policy | GT electricity old/new kWh | GT unit-hours old/new | GT starts old/new | DG electric old/new kWh | DG shaft-budget debit old/new kWh-equivalent |', '|---|---|---:|---:|---:|---:|---:|'])
    for row in versions:
        cells = [f"{row['old_' + field]:.6g}/{row['new_' + field]:.6g}" for field in ('GT_electric_kWh','GT_unit_online_hours','GT_actual_start_count','DG_electric_work_kWh','DG_fuel_budget_debit_kWh_equivalent')]
        lines.append('| ' + row['scenario_id'] + ' | ' + row['alias'] + ' | ' + ' | '.join(cells) + ' |')
    lines.extend(['', 'Old/new GT electricity integrates only stored actual linear source endpoints from terminal-SHA-bound ENGINEERING_INTERVALS.csv, with full registered coverage. NewGT is reconciled to the saved source ledger. GT starts and unit-hours retain the frozen classifier. DG electricity, shaft/loss/rotor work and budget debits remain actual receipt accounting; budget debit is not chemical fuel. Source-to-priority allocation is not identifiable from these bus totals, and no no-DG counterfactual is asserted.', ''])
    DOC.write_text('\n'.join(lines), encoding='utf-8')


def prepare():
    if not SUCCESSOR_CONTRACT.is_file():
        return dict(status='prepared_pending_successor_contract', physical_episodes_started=0, numerical_project_imported=False)
    if (HERE / 'PREPARATION.json').exists():
        prior = read(HERE / 'PREPARATION.json'); validate_prepared_dependencies(prior, dependency_shas()); validate_prepared_evidence(prior['continuation_evidence'])
        return prior
    paths = continuation_inputs(); frozen_path = RUN / 'MODEL_FREEZE_ACCOUNTING01.json'; frozen = read(frozen_path)
    require(sha(frozen_path) == '48baed7a128d2f921d36323330ae4d22e2e6dc83214e8a505b6ba89273439b7b', 'Original scientific freeze bytes changed')
    source, tests, inventory = frozen['source_sha256'], frozen['source_bound_test_gate']['test_files_sha256'], frozen['runtime_inputs']
    require(len(source) == 366 and len(tests) == 241 and len(inventory) == 28, 'Frozen source/tests/input closure denominator differs')
    for mapping in (source, tests):
        for name, expected in mapping.items(): require(sha(ROOT / name) == expected, 'Frozen scientific/test bytes changed')
    for row in inventory: require(sha(ROOT / row['path']) == row['sha256'], 'Frozen runtime input bytes changed')
    baseline = read(PRIOR_ANALYSIS02 / 'PREPARATION.json')
    for row in baseline['registered_files']: require(sha(ROOT / row['path']) == row['sha256'], 'Original immutable normal registration changed')
    value = dict(status='prepared_pending_S4_no_numeric_results', normal_batches=12,normal_windows=4,window_hours=[24.,48.,72.,24.],
        registered_files=baseline['registered_files'], freeze_sha256=sha(frozen_path), source_files=366, test_files=241,runtime_inputs=28,
        continuation_evidence=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in paths],
        physical_episodes_started=0,numerical_project_imported=False,no_persistent_wait_mode=True,**dependency_shas())
    write_new(HERE / 'PREPARATION.json', value); return value


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepared-only normal03 saved-result analysis; use the separate one-shot bootstrap after S4.')
    parser.add_argument('--check-only', action='store_true', required=True)
    parser.parse_args(argv)
    print(json.dumps(dict(status='prepared_pending_successor_contract', physical_episodes_started=0, persistent_wait_available=False)))
    return 3


if __name__ == '__main__': raise SystemExit(main())
