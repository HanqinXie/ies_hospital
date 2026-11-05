"""Read-only gate over real server smoke products; never starts a model.

The integrating runner supplies six short N/FD producers and uninterrupted vs
resumed physical terminal exports. Missing runs are refusals, never successes.
"""
import argparse
import hashlib
import json
from pathlib import Path

from tools.cooling_dynamics_20261104.audit_campaign import audit_row

from . import OWNER_SCHEMA
from .bridge import METHODS, CoolingBridgeState, current_cause_ledger

SCHEMA = 'hospital_ies.annual_dynamic.server_smoke_inputs.v1'
EXPECTED = frozenset((mode, method) for mode in ('N', 'FD') for method in METHODS)
FILES = frozenset(('case', 'result', 'resolved', 'segments', 'bridge_checkpoint',
                   'uninterrupted_terminal', 'resumed_terminal'))
TERMINAL_FIELDS = frozenset(('time_minute', 'executed', 'pending_commands', 'state',
                             'ups', 'cold_store', 'station', 'annual_cooling_bridge'))


def read(path):
    def invalid(value):
        raise ValueError('Nonfinite JSON value: ' + value)
    return json.loads(Path(path).read_text('utf-8-sig'), parse_constant=invalid)


def pinned_files(entry):
    pins = entry.get('files')
    if type(pins) is not dict or set(pins) != FILES:
        raise ValueError('Complete actual smoke file pins required')
    result = {}
    for name, pin in pins.items():
        if type(pin) is not dict or set(pin) != {'path', 'sha256'}:
            raise ValueError('Exact path/SHA smoke file binding required')
        path = Path(pin['path'])
        if not path.is_absolute() or not path.is_file():
            raise ValueError('Actual smoke file missing: ' + str(path))
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                h.update(block)
        if h.hexdigest() != pin['sha256']:
            raise ValueError('Actual smoke file SHA mismatch: ' + name)
        result[name] = path
    return result


def smoke_case(value):
    if type(value) is not dict:
        raise ValueError('Actual smoke CASE object required')
    if 'case' in value:
        if type(value['case']) is not dict:
            raise ValueError('Actual smoke wrapped CASE object is invalid')
        return value['case']
    return value


def smoke_declared_calendar(case):
    revision=smoke_case(case).get('execution_revision',{})
    declared=revision.get('declared_calendar') if type(revision) is dict else None
    if type(declared) is not list:
        raise ValueError('Explicit execution_revision declared smoke calendar required')
    return declared


def validate_smoke(manifest):
    if type(manifest) is not dict or manifest.get('schema') != SCHEMA:
        raise ValueError('Explicit server smoke schema required')
    entries = manifest.get('entries')
    if type(entries) is not list or len(entries) != 6 or {(e.get('mode'), e.get('method')) for e in entries} != EXPECTED:
        raise ValueError('Exactly six distinct N/FD responsive method smoke producers required')
    producers = []
    for entry in entries:
        files = pinned_files(entry)
        case=smoke_case(read(files['case']))
        result,resolved=(read(files[name]) for name in ('result','resolved'))
        mode, method = entry['mode'], entry['method']
        parameters = case.get('annual_program', {}).get('parameters', {})
        if parameters.get('mode') != mode or parameters.get('method') != method:
            raise ValueError('Actual smoke CASE differs from selected arrangement')
        requested = parameters.get('duration_minutes')
        if type(requested) not in (int, float) or requested < 30.:
            raise ValueError('Smoke requires at least two actual 15-minute scheduling steps')
        if (result.get('status') != 'completed' or result.get('observed_until_minute') != requested
                or result.get('requested_end_minute') != requested or result.get('unknown_tail_minutes') != 0.):
            raise ValueError('Actual smoke producer is incomplete')
        if result.get('annual_dynamic_owner_contract') != OWNER_SCHEMA:
            raise ValueError('Actual smoke result lacks the distinct engineering owner contract')
        if resolved.get('annual_dynamic_input_cadence') != 'original_hourly_hold_15min_control_max1min_physics':
            raise ValueError('Actual smoke input/control/physical cadence is not registered')
        declared = smoke_declared_calendar(case)
        if type(declared) is not list or (mode == 'N' and declared):
            raise ValueError('Explicit matched declared smoke calendar required')
        cursor = 0.; stocks = dynamic = None; errors = {}; count = 0; active = set()
        with files['segments'].open(encoding='utf8') as stream:
            for line in stream:
                row = json.loads(line)
                start, end = row['start_minute'], row['end_minute']
                if start != cursor or not start < end <= requested:
                    raise ValueError('Actual smoke prefix has a gap/overlap/overrun')
                owner = row.get('annual_dynamic_actual_owner', {})
                if (owner.get('schema') != OWNER_SCHEMA or owner.get('legacy_annual_native_witness_claimed') is not False
                        or owner.get('dynamic_commit_count') != 1 or 'annual_program_actual_witness' in row
                        or row.get('sf2_running_fuel', {}).get('accounting_committed') is not True):
                    raise ValueError('Actual smoke segment owner/commit evidence is incompatible')
                expected = current_cause_ledger(declared, start, row['hardware_availability'])
                if expected != row.get('annual_dynamic_causal_ledger'):
                    raise ValueError('Actual smoke raw/functional causal receipt differs')
                active.update(expected['active_declared_causes'])
                check = audit_row(row, resolved, previous_stocks=stocks, previous_dynamic=dynamic, residuals=errors)
                stocks, dynamic = check['stocks'], check['dynamic']; cursor = end; count += 1
        if cursor != requested or count == 0:
            raise ValueError('Actual smoke evidence has an unknown tail')
        if mode == 'FD' and not {'TOWER_1', 'EC_1_PRIMARY_PUMP', 'TOWER'} <= active:
            raise ValueError('FD smoke must actually exercise the single tower, branch pump and common tower causes')
        checkpoint = read(files['bridge_checkpoint'])
        minute = checkpoint.get('committed_until_minute')
        if type(minute) not in (int,float) or not 0. < minute < requested:
            raise ValueError('Actual smoke checkpoint must be an interior acknowledged boundary')
        CoolingBridgeState.from_state(checkpoint, expected_minute=minute)
        left, right = (read(files[name]) for name in ('uninterrupted_terminal', 'resumed_terminal'))
        if type(left) is not dict or not TERMINAL_FIELDS <= set(left) or left != right:
            raise ValueError('Uninterrupted/resumed physical terminal states differ or omit required live state')
        if left['time_minute'] != requested:
            raise ValueError('Actual resumed terminal does not reach the registered end')
        CoolingBridgeState.from_state(left['annual_cooling_bridge'], expected_minute=requested)
        producers.append(dict(mode=mode, method=method, actual_segment_count=count,
            observed_until_minute=cursor, independently_recomputed_residuals=errors))
    return dict(schema='hospital_ies.annual_dynamic.server_smoke_gate.v1', status='passed_component_server_smoke',
        producers=producers, annual_completion_claimed=False,
        scope='Six actual short N/FD engineering ledgers and checkpoint continuation; no full annual or OEM qualification')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_smoke(read(args.manifest))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False)); return 0
    except Exception as exc:
        print(json.dumps(dict(status='refused_missing_or_invalid_server_smoke', error_type=type(exc).__name__,
            message=str(exc), annual_completion_claimed=False), ensure_ascii=False)); return 2


if __name__ == '__main__':
    raise SystemExit(main())
