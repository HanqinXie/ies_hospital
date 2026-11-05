"""Accepted shared disturbance calendar and fail-closed normal-reference binding.

This module reads no model inputs and starts no optimizer. A candidate calendar
cannot authorize a year. Resolving requires the closed original normal M_ready
year, its complete index chain, and raw actual segments for every tested anchor.
Only those selected windows are re-audited; this is not a full annual raw audit.
"""
from __future__ import annotations

import copy
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'hospital_ies.busy_tear.calendar.v1'
PROOF_SCHEMA = 'hospital_ies.busy_tear.normal_reference.v1'
JOURNAL_SCHEMA = 'hospital_ies.annual_readiness.block_journal.v1'
MODEL_ID = 'hospital_ies.annual_program.20261030.v1'
YEAR_MINUTES = 525600.
METHODS = ['M_available', 'M_ready', 'C_ready']
REFERENCE_COMMIT = 'b4d1ed20dc37e6add516a20a96979c2e29efdd36'
REFERENCE_SOURCE_SHA256 = '4512618aee63dc7ca79483973499ce096ab2622d5fac4e2556cf550928456eaa'
REFERENCE_INPUT_SHA256 = 'f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15'
REFERENCE_CASE_SHA256 = 'e395f115c08b3df1ec1e8ad298b12b0b5787667e2aa3112584c4bed31354eb71'
REFERENCE_SOURCE_COUNT = 1111
EXECUTION_REVISION = 'annual_responsive_dynamic_bridge_20261104.v1'
ANCHOR_IDS = ('E1','E2','E3','E8')
SEALED_DAYS = frozenset((21, 26, 59, 77, 92, 127, 140, 196, 264, 284, 344, 359))
REFERENCE_FILES = ('CASE.json', 'BINDINGS.json', 'RESULT.json',
                   'evidence/IDENTITY.json', 'evidence/SUMMARY.json', 'evidence/blocks.jsonl')


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf8')).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('A finite real production/clock value is required')
    return float(value)


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _load(path):
    def reject(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    try:
        return json.loads(Path(path).read_text('utf-8-sig'), parse_constant=reject)
    except (OSError, ValueError) as exc:
        raise ValueError('Missing or invalid reference JSON: ' + str(path)) from exc


def _minute(month, day, hour=0, minute=0):
    return float((datetime(2001, month, day, hour, minute) - datetime(2001, 1, 1)).total_seconds() / 60)


def plan_digest(plan):
    return canonical_sha256({key: value for key, value in plan.items() if key != 'plan_sha256'})


def _base_plan():
    faults = [
        ('E1', 'GT_3', _minute(1, 15, 10), 60., 0.),
        ('E2', 'BOILER_1', _minute(2, 15, 10), 1440., 0.),
        ('E3', 'EC_1', _minute(4, 15, 14), 60., 0.),
        ('E4', 'ABS', _minute(5, 15, 10), 2880., 0.),
        ('E5', 'TOWER_1', _minute(7, 22, 14), 60., 0.),
        ('E6a', 'TOWER', _minute(8, 5, 14), 30., .01),
        ('E6b', 'TOWER', _minute(8, 5, 14, 35), 45., .01),
        ('E7', 'GRID', _minute(11, 15, 10), 60., 0.),
        ('E8', 'EC_1_PRIMARY_PUMP', _minute(9, 15, 14), 60., 0.)]
    return dict(schema=SCHEMA, status='candidate', year=2001, horizon_minutes=YEAR_MINUTES,
        configuration='H2', methods=METHODS.copy(),
        clock='source nonleap simulation clock; Jan01 00:00 = minute0; half-open intervals',
        demand=dict(zone='ER_TRAUMA1_FLR_1', carrier='c', amplitude=.25,
            start_minute=280800., duration_minutes=40320.),
        fault_intervals=[dict(event_id=i, device=d, start_minute=s, end_minute=s+t, availability=a)
                         for i, d, s, t, a in faults],
        anchor_rule=dict(reference_method='M_ready', reference_mode='N',
            events=list(ANCHOR_IDS), previous_minutes=60., max_forward_days=6,
            selection='first daily same-clock candidate with positive actual preceding production',
            sealed_support_excluded=True, no_post_disturbance_selection=True),
        reference_proof=None)


def candidate_plan():
    result = _base_plan()
    result['plan_sha256'] = plan_digest(result)
    return result


def selection_support_allowed(start, end):
    start, end = _number(start), _number(end)
    return (0. <= start < end <= YEAR_MINUTES and not any(
        start < (day + 1) * 1440. and end > day * 1440. for day in SEALED_DAYS))


def _allowed_anchor(original, actual):
    delta = _number(actual) - original
    return delta in tuple(float(day * 1440) for day in range(7)) and selection_support_allowed(actual-60., actual)


def _validate_proof(proof, plan):
    if not isinstance(proof, dict) or proof.get('schema') != PROOF_SCHEMA or proof.get('status') != 'resolved_from_closed_normal_year':
        raise ValueError('Actual closed normal-year reference proof is required')
    if proof.get('signature') != canonical_sha256({k: v for k, v in proof.items() if k != 'signature'}):
        raise ValueError('Reference proof identity changed')
    expected = dict(git_commit=REFERENCE_COMMIT, source_sha256=REFERENCE_SOURCE_SHA256,
                    input_sha256=REFERENCE_INPUT_SHA256, case_sha256=REFERENCE_CASE_SHA256)
    if proof.get('reference_identity') != expected:
        raise ValueError('Reference is not the original frozen normal M_ready producer')
    if not isinstance(proof.get('reference_output'), str) or not Path(proof['reference_output']).is_absolute():
        raise ValueError('An explicit original reference output is required')
    if set(proof.get('files', {})) != set(REFERENCE_FILES) or not all(_sha(v) for v in proof['files'].values()):
        raise ValueError('Reference headers/index must all be byte bound')
    if proof.get('full_annual_raw_reaudit') is not False or proof.get('scope') != 'complete index chain plus actual preceding60min candidate windows':
        raise ValueError('Reference evidence scope changed')
    chosen = proof.get('selected_anchors', {})
    if set(chosen) != set(ANCHOR_IDS):
        raise ValueError('All four active reference anchors are required')
    checks = proof.get('candidate_checks')
    if not isinstance(checks, list):
        raise ValueError('Candidate selection ledger is required')
    expected_checks = []
    by_id={r['event_id']:r for r in plan['fault_intervals']}
    for original in (r for r in _base_plan()['fault_intervals'] if r['event_id'] in ANCHOR_IDS):
        selected=by_id[original['event_id']]
        event_id = original['event_id']
        unit = 'lb' if original['device'] == 'BOILER_1' else 'kWh_e' if original['device'] == 'GT_3' else 'kWh_c'
        if chosen[event_id] != dict(start_minute=selected['start_minute'],
            end_minute=selected['end_minute'], device=selected['device'], unit=unit):
            raise ValueError('Chosen anchor and reference proof disagree')
        for day in range(7):
            anchor = original['start_minute'] + day*1440.
            if not selection_support_allowed(anchor-60., anchor):
                continue
            found = [row for row in checks if row.get('event_id') == event_id and row.get('anchor_minute') == anchor]
            if len(found) != 1:
                raise ValueError('Every tested earlier candidate needs actual evidence')
            check = found[0]
            integral = _number(check.get('actual_integral'))
            if integral < 0. or check.get('window') != [anchor-60., anchor] or check.get('unit') != unit:
                raise ValueError('Invalid actual-production selection record')
            raw = check.get('raw_files')
            if not isinstance(raw, list) or not raw or any(
                not isinstance(r, dict) or set(r) != {'file', 'sha256', 'index_digest'}
                or not _sha(r['sha256']) or not _sha(r['index_digest']) for r in raw):
                raise ValueError('Raw candidate blocks must be hash and index bound')
            expected_checks.append(check)
            if anchor == selected['start_minute']:
                if integral <= 0.:
                    raise ValueError('Selected equipment had no actual production')
                break
            if integral != 0.:
                raise ValueError('Selection skipped an earlier active candidate')
        else:
            raise ValueError('No eligible active reference candidate')
    if checks != expected_checks:
        raise ValueError('Unexpected, unordered or post-selection reference records')


def validate_plan(plan, *, require_frozen=False):
    if not isinstance(plan, dict) or plan.get('plan_sha256') != plan_digest(plan):
        raise ValueError('Calendar plan identity changed')
    base = _base_plan()
    if set(plan) != set(base) | {'plan_sha256'}:
        raise ValueError('Unknown or missing calendar plan fields')
    for key, expected in base.items():
        if key not in ('status', 'fault_intervals', 'reference_proof') and plan.get(key) != expected:
            raise ValueError('Accepted calendar condition changed: ' + key)
    status = plan.get('status')
    if status not in ('candidate', 'frozen') or (require_frozen and status != 'frozen'):
        raise ValueError('Resolved frozen calendar is required')
    intervals = plan.get('fault_intervals')
    if not isinstance(intervals, list) or len(intervals) != len(base['fault_intervals']):
        raise ValueError('All accepted fault intervals must remain present')
    for n, (actual, original) in enumerate(zip(intervals, base['fault_intervals'])):
        if not isinstance(actual, dict) or set(actual) != set(original):
            raise ValueError('Malformed fault interval')
        for field in ('start_minute', 'end_minute', 'availability'):
            _number(actual[field])
        expected = copy.deepcopy(original)
        if original['event_id'] in ANCHOR_IDS and status == 'frozen':
            if not _allowed_anchor(original['start_minute'], actual.get('start_minute')):
                raise ValueError('Anchor changed outside its registered selection window')
            expected['start_minute'] = float(actual['start_minute'])
            expected['end_minute'] = expected['start_minute'] + original['end_minute']-original['start_minute']
        if actual != expected:
            raise ValueError('Accepted fault condition changed: ' + original['event_id'])
    if status == 'candidate':
        if plan['reference_proof'] is not None:
            raise ValueError('Candidate cannot claim reference qualification')
    else:
        _validate_proof(plan['reference_proof'], plan)
    return copy.deepcopy(plan)


def availability_events(plan):
    validate_plan(plan)
    rows = []
    for interval in plan['fault_intervals']:
        rows.extend([dict(time_minute=interval['start_minute'], availability={interval['device']:interval['availability']}),
                     dict(time_minute=interval['end_minute'], availability={interval['device']:1.})])
    return sorted(rows, key=lambda row: row['time_minute'])


def exposure_hours(plan):
    validate_plan(plan)
    demand = plan['demand']
    ds, de = demand['start_minute'], demand['start_minute']+demand['duration_minutes']
    points = sorted({0., YEAR_MINUTES, ds, de} | {row[key]
        for row in plan['fault_intervals'] for key in ('start_minute', 'end_minute')})
    totals = dict(N=0., D=0., F=0., FD=0.)
    for a, b in zip(points, points[1:]):
        fault = any(row['start_minute'] <= a < row['end_minute'] for row in plan['fault_intervals'])
        growth = ds <= a < de
        key = 'FD' if fault and growth else 'F' if fault else 'D' if growth else 'N'
        totals[key] += (b-a)/60.
    return totals


def actual_production(action, device):
    if not isinstance(action, dict):
        raise ValueError('Actual action ledger is missing')
    if device == 'BOILER_1':
        value = _number(action.get('boiler_steam_lbph'))
    else:
        key, index = ('gt_electric_kW', 2) if device == 'GT_3' else ('electric_chiller_cooling_kW', 0) if device in ('EC_1','EC_1_PRIMARY_PUMP') else (None, None)
        values = action.get(key)
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError('Three actual converter outputs are required')
        numeric = [_number(v) for v in values]
        if any(v < 0. for v in numeric):
            raise ValueError('Actual output cannot be negative')
        value = numeric[index]
    if value < 0.:
        raise ValueError('Actual production cannot be negative')
    return value


def integrate_window(segments, start, end, device):
    start, end = _number(start), _number(end)
    if not start < end:
        raise ValueError('A positive actual observation window is required')
    cursor, terms = start, []
    for segment in segments:
        a, b = _number(segment.get('start_minute')), _number(segment.get('end_minute'))
        if not a < b:
            raise ValueError('Actual segment has invalid duration')
        if b <= start or a >= end:
            continue
        clipped_start, clipped_end = max(a, start), min(b, end)
        if clipped_start != cursor:
            raise ValueError('Actual production window has a gap, overlap or unordered segment')
        terms.append(actual_production(segment.get('action'), device) * (clipped_end-clipped_start)/60.)
        cursor = clipped_end
    if cursor != end:
        raise ValueError('Actual production window is incomplete')
    return math.fsum(terms)


class _Reference:
    def __init__(self, output):
        raw = Path(output).absolute()
        if raw != raw.resolve() or any(path.is_symlink() for path in (raw, *raw.parents)):
            raise ValueError('Reference output cannot use symlink or alias paths')
        self.output = raw.resolve()
        self.files = {name: sha256(self._path(name)) for name in REFERENCE_FILES}
        saved = _load(self._path('CASE.json'))
        case = saved.get('case')
        if not isinstance(case, dict) or canonical_sha256(case) != REFERENCE_CASE_SHA256:
            raise ValueError('Reference must be the exact original normal M_ready annual case')
        parameters = case.get('annual_program', {}).get('parameters', {})
        if any(parameters.get(k) != v for k, v in dict(method='M_ready', mode='N',
            configuration='H2', start_hour=1, duration_minutes=YEAR_MINUTES, calendar=[],
            execution_revision=EXECUTION_REVISION).items()):
            raise ValueError('Reference is not a continuous source-hour1 normal M_ready year')
        if case.get('execution_revision',{}).get('model_id')!=EXECUTION_REVISION:
            raise ValueError('Reference lacks the current equipment-dynamic execution revision')
        bindings = _load(self._path('BINDINGS.json'))
        source, inputs = bindings.get('source'), bindings.get('inputs')
        if (bindings.get('git_commit') != REFERENCE_COMMIT or not isinstance(source, dict)
            or len(source) != REFERENCE_SOURCE_COUNT or canonical_sha256(source) != REFERENCE_SOURCE_SHA256
            or not isinstance(inputs, dict) or len(inputs) != 36
            or canonical_sha256(inputs) != REFERENCE_INPUT_SHA256):
            raise ValueError('Original normal source/input binding differs')
        identity = _load(self._path('evidence/IDENTITY.json'))
        expected_identity = dict(case_id=case.get('id'), model_id=MODEL_ID, git_commit=REFERENCE_COMMIT,
            source_sha256=REFERENCE_SOURCE_SHA256, input_sha256=REFERENCE_INPUT_SHA256,
            case_sha256=REFERENCE_CASE_SHA256)
        if identity != expected_identity:
            raise ValueError('Journal identity and original normal case disagree')
        result = _load(self._path('RESULT.json'))
        required = dict(status='completed', observed_until_minute=YEAR_MINUTES,
            requested_end_minute=YEAR_MINUTES, unknown_tail_minutes=0., source_and_inputs_unchanged=True,
            case_id=case.get('id'), model_id=MODEL_ID, git_commit=REFERENCE_COMMIT,
            case_sha256=REFERENCE_CASE_SHA256, source_sha256=REFERENCE_SOURCE_SHA256,
            execution_model_id=EXECUTION_REVISION,
            annual_dynamic_owner_contract='hospital_ies.annual_dynamic.accepted_engineering_owner.v1')
        if (any(result.get(k) != v for k, v in required.items())
            or result.get('source_and_inputs_unchanged') is not True
            or result.get('input_sha256') not in (inputs, REFERENCE_INPUT_SHA256)):
            raise ValueError('Reference result is not an unchanged completed normal year')
        self.identity_sha = self.files['evidence/IDENTITY.json']
        self.summary = _load(self._path('evidence/SUMMARY.json'))
        summary = self.summary
        observation = dict(observed_from_minute=0., observed_until_minute=YEAR_MINUTES,
            observed_duration_minutes=YEAR_MINUTES, requested_end_minute=YEAR_MINUTES,
            unknown_tail_minutes=0., complete=True, contiguous=True, quantities_are_observed_prefix_only=True)
        if (summary.get('schema') != JOURNAL_SCHEMA or summary.get('status') != 'completed'
            or summary.get('integrity_verified') is not True or summary.get('journal_write_error')
            or summary.get('identity') != identity or summary.get('identity_sha256') != self.identity_sha
            or summary.get('observation') != observation):
            raise ValueError('Reference journal is not a closed complete normal year')
        for key in ('complete', 'contiguous', 'quantities_are_observed_prefix_only'):
            if summary['observation'].get(key) is not True:
                raise ValueError('Reference observation booleans must be explicit')
        self.records = self._index()
        self.read_raw = {}

    def _path(self, relative):
        path = self.output / relative
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(self.output):
            raise ValueError('Reference evidence file missing or outside reference root: ' + relative)
        return path

    def _index(self):
        records, previous, cursor = [], None, 0.
        with self._path('evidence/blocks.jsonl').open('r', encoding='utf8') as stream:
            for n, line in enumerate(stream):
                if not line.endswith('\n'):
                    raise ValueError('Incomplete reference index record')
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('Reference index record must be an object')
                unsigned = {k:v for k,v in row.items() if k != 'digest'}
                encoded = (json.dumps(unsigned, sort_keys=True, ensure_ascii=False, allow_nan=False,
                    separators=(',', ':'))+'\n').encode('utf8')
                expected_file = 'blocks/block_' + format(n, '06d') + '.json.gz'
                a, b = _number(row.get('start_minute')), _number(row.get('end_minute'))
                if (row.get('schema') != JOURNAL_SCHEMA or type(row.get('index')) is not int
                    or row['index'] != n or row.get('file') != expected_file
                    or row.get('previous_digest') != previous or row.get('identity_sha256') != self.identity_sha
                    or row.get('digest') != hashlib.sha256(encoded).hexdigest()
                    or not _sha(row.get('sha256')) or a != cursor or b != a+15.
                    or type(row.get('segment_count')) is not int or row['segment_count'] <= 0
                    or type(row.get('solver_call_count')) is not int or row['solver_call_count'] < 0):
                    raise ValueError('Reference index identity, hash chain or 15-minute coverage differs')
                records.append(row)
                cursor, previous = b, row['digest']
        expected_raw = [dict(file=r['file'], sha256=r['sha256']) for r in records]
        if (cursor != YEAR_MINUTES or len(records) != 35040 or self.summary.get('block_count') != len(records)
            or self.summary.get('last_digest') != previous or self.summary.get('raw_files_sha256') != expected_raw):
            raise ValueError('Reference index is not the complete bound normal-year chain')
        return records

    def window(self, anchor, device):
        start, end = anchor-60., anchor
        segments, raw_files = [], []
        for row in self.records:
            if row['end_minute'] <= start:
                continue
            if row['start_minute'] >= end:
                break
            path = self._path('evidence/' + row['file'])
            if sha256(path) != row['sha256']:
                raise ValueError('Reference actual raw block SHA differs: ' + row['file'])
            self.read_raw['evidence/'+row['file']] = row['sha256']
            with gzip.open(path, 'rt', encoding='utf8') as stream:
                block = json.load(stream)
            # Reject JSON NaN/Infinity anywhere in a selected raw witness.
            json.dumps(block, allow_nan=False)
            if (not isinstance(block, dict) or _number(block.get('start_minute')) != row['start_minute']
                or _number(block.get('end_minute')) != row['end_minute']
                or not isinstance(block.get('segments'), list) or len(block['segments']) != row['segment_count']
                or not isinstance(block.get('solver_calls'), list) or len(block['solver_calls']) != row['solver_call_count']):
                raise ValueError('Reference actual raw block and index disagree')
            cursor = row['start_minute']
            for segment in block['segments']:
                a, b = _number(segment.get('start_minute')), _number(segment.get('end_minute'))
                if a != cursor or not a < b <= row['end_minute']:
                    raise ValueError('Actual selected block has a segment gap or overlap')
                cursor = b
            if cursor != row['end_minute']:
                raise ValueError('Actual selected block has an incomplete segment tail')
            segments.extend(block['segments'])
            raw_files.append(dict(file='evidence/'+row['file'], sha256=row['sha256'], index_digest=row['digest']))
        integral = integrate_window(segments, start, end, device)
        return dict(window=[start, end], actual_integral=integral,
            unit='lb' if device == 'BOILER_1' else 'kWh_e' if device == 'GT_3' else 'kWh_c', raw_files=raw_files)

    def unchanged(self):
        if self.files != {name:sha256(self._path(name)) for name in REFERENCE_FILES}:
            raise ValueError('Normal reference changed during calendar resolution')
        if any(sha256(self._path(name)) != digest for name, digest in self.read_raw.items()):
            raise ValueError('Actual candidate raw evidence changed during calendar resolution')


def resolve_plan(reference_output):
    reference = _Reference(reference_output)
    plan = _base_plan()
    checks, selected = [], {}
    for interval in (r for r in plan['fault_intervals'] if r['event_id'] in ANCHOR_IDS):
        original_start = interval['start_minute']
        duration = interval['end_minute']-original_start
        for day in range(7):
            anchor = original_start + day*1440.
            if not selection_support_allowed(anchor-60., anchor):
                continue
            check = reference.window(anchor, interval['device'])
            checks.append(dict(event_id=interval['event_id'], anchor_minute=anchor, **check))
            if check['actual_integral'] > 0.:
                interval.update(start_minute=anchor, end_minute=anchor+duration)
                selected[interval['event_id']] = dict(start_minute=anchor, end_minute=anchor+duration,
                    device=interval['device'], unit=check['unit'])
                break
        else:
            raise ValueError('No active actual-production anchor for '+interval['event_id']+'; calendar remains unresolved')
    reference.unchanged()
    proof = dict(schema=PROOF_SCHEMA, status='resolved_from_closed_normal_year',
        reference_output=str(reference.output), reference_identity=dict(git_commit=REFERENCE_COMMIT,
            source_sha256=REFERENCE_SOURCE_SHA256, input_sha256=REFERENCE_INPUT_SHA256,
            case_sha256=REFERENCE_CASE_SHA256), files=reference.files,
        selected_anchors=selected, candidate_checks=checks,
        scope='complete index chain plus actual preceding60min candidate windows', full_annual_raw_reaudit=False)
    proof['signature'] = canonical_sha256(proof)
    plan.update(status='frozen', reference_proof=proof)
    plan['plan_sha256'] = plan_digest(plan)
    return validate_plan(plan, require_frozen=True)


def verify_reference_proof(plan):
    validate_plan(plan, require_frozen=True)
    rebuilt = resolve_plan(plan['reference_proof']['reference_output'])
    if rebuilt != plan:
        raise ValueError('Frozen calendar no longer matches the original actual normal reference')
    return True
