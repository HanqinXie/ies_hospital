"""Audit saved V18 raw, retaining every physical interval and lawful command record.

No simulation, optimizer, annual load, model import or source modification occurs.
Run one worker. A fresh labelled review directory is mandatory. Huge redundant
solver vectors/row witnesses alone are compacted; original raw stays untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
B = ROOT / 'outputs/v18_execution_recovery_20261018_run01'
SCHEMA = 'hospital_ies.v18.saved_raw_audit.v1'
RT_TO_KW = 3.51685284
POWER_TOL = 1e-4
WORK_TOL = 1e-8
TIME_TOL = 1e-7
WORK_FIELDS = ('mechanical_work_kWh', 'electrical_work_kWh', 'loss_work_kWh',
               'rotor_delta_kWh', 'fuel_budget_debit_kWh_equivalent')
ENCODER = json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Nonfinite recorded number')
    return value


def canonical_receipt(value, path):
    digest = hashlib.sha256()
    size = 0
    for chunk in ENCODER.iterencode(value):
        encoded = chunk.encode('utf8')
        digest.update(encoded)
        size += len(encoded)
    return dict(path='/' + '/'.join(str(k).replace('~', '~0').replace('/', '~1') for k in path),
                canonical_json_sha256=digest.hexdigest(), canonical_json_bytes=size,
                item_count=len(value), omitted='solver_witness_only; full value in indexed original raw')


def compact_witnesses(payload):
    """Compact explicit repeated planner/solver witnesses, preserving causal data."""
    omissions = []

    def scalar_header(value):
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if not isinstance(v, (dict, list))}
        return {}

    def solver_headers(layers):
        result = []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            header = scalar_header(layer)
            if isinstance(layer.get('incumbent_validation'),dict):
                header['incumbent_validation']=scalar_header(layer['incumbent_validation'])
            header['attempts'] = []
            for attempt in layer.get('attempts', []):
                item = scalar_header(attempt)
                if isinstance(attempt.get('parameters'), dict):
                    item['parameters'] = attempt['parameters']
                if isinstance(attempt.get('validation'), dict):
                    item['validation'] = scalar_header(attempt['validation'])
                if isinstance(attempt.get('incumbent_validation'),dict):
                    item['incumbent_validation']=scalar_header(attempt['incumbent_validation'])
                if isinstance(attempt.get('error'),dict):
                    item['error']=attempt['error']
                header['attempts'].append(item)
            result.append(header)
        return result

    def diagnostic_headers(value):
        result = scalar_header(value)
        for key in ('options', 'layers', 'objective_order', 'service_budget', 'preparation_profile', 'operating_options',
                    'late_preference_fallbacks','numerical_presentation_recoveries'):
            if key in value:
                result[key] = value[key]
        if isinstance(value.get('coordination'), dict):
            result['coordination'] = {key: child for key, child in value['coordination'].items()
                if key not in ('response_scenarios', 'eventual_scenarios')}
            result['coordination']['static_copy_names'] = {key:list(value['coordination'][key])
                for key in ('response_scenarios', 'eventual_scenarios') if key in value['coordination']}
        for key in ('readiness_phases', 'risk_scenarios'):
            if isinstance(value.get(key), dict):
                result[key] = {name:{k:v for k,v in record.items() if k in
                    ('status','joint_witness_status','actual_supply','source_basis',
                     'earliest_possible_effective_minute','assured_by_minute','diagnostic_not_actual_execution')}
                    if isinstance(record,dict) else {} for name,record in value[key].items()}
        if isinstance(value.get('engineering'), dict):
            engineering = value['engineering']
            result['engineering'] = scalar_header(engineering)
            for key in ('options', 'actual_GT_running_mask', 'normal_startup_preference', 'normal_commitment_preference'):
                if key in engineering:
                    result['engineering'][key] = engineering[key]
            result['engineering']['same_state_diagnostic_status'] = {
                name:scalar_header(record) for name,record in engineering.get('same_state_diagnostics',{}).items()}
        return result

    def visit(value, path):
        if path and path[0] in ('segments', 'error', 'solver_errors'):
            return
        if isinstance(value, dict):
            for key in list(value):
                child = value[key]
                diagnostic = any(p in ('layers', 'solver_backend_layers', 'diagnostic', 'joint_diagnostic') for p in path)
                if key == 'diagnostic' and path[-1:] == ('joint_dispatch',) and isinstance(child, dict) and 'schema' in child:
                    receipt = canonical_receipt(child, path + (key,))
                    omissions.append(receipt)
                    value[key] = dict(diagnostic_headers(child), saved_raw_witness_omitted=receipt,
                                      static_witness_summary_only=True)
                    # These retained branches still equal the original raw.
                    # Transformed risk/readiness headers must never be hashed
                    # a second time as if they were original-path witnesses.
                    for retained in ('layers','late_preference_fallbacks','numerical_presentation_recoveries'):
                        if retained in value[key]:
                            visit(value[key][retained],path+(key,retained))
                    continue
                static_copy = ('joint_dispatch' in path and 'diagnostic' in path and
                    (key in ('risk_scenarios', 'readiness_phases') or
                     (path[-1:] == ('coordination',) and key in ('response_scenarios', 'eventual_scenarios'))))
                if key == 'solver_backend_layers' and isinstance(child, list) and child:
                    receipt = canonical_receipt(child, path + (key,))
                    omissions.append(receipt)
                    value[key] = dict(saved_raw_witness_omitted=receipt, summary_layers=solver_headers(child))
                    continue
                if static_copy and isinstance(child, (dict, list)) and child:
                    receipt = canonical_receipt(child, path + (key,))
                    omissions.append(receipt)
                    # Names and scalar phase headers remain available. Exact
                    # counterfactual matrices are only in indexed full raw.
                    headers = {k: scalar_header(v) for k, v in child.items()} if isinstance(child, dict) else None
                    value[key] = dict(saved_raw_witness_omitted=receipt,
                                      scalar_headers=headers, diagnostic_not_actual_execution=True)
                    continue
                witness = ((key == 'raw_x' and diagnostic) or
                           (path and path[-1] in ('validation','incumbent_validation') and key in ('rows', 'bound_violation')))
                if witness and isinstance(child, (dict, list)) and child:
                    receipt = canonical_receipt(child, path + (key,))
                    omissions.append(receipt)
                    value[key] = {'saved_raw_witness_omitted': receipt}
                else:
                    visit(child, path + (key,))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + (index,))

    visit(payload, ())
    return omissions


DECISION_STORAGE = 'hospital_ies.v18.lossless_decision_columns.v1'


def columnar_decisions(rows):
    """Lossless storage: adjacent equal field names fit gzip's finite window."""
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise ValueError('Expected original decision row dictionaries')
    columns = {}
    for index,row in enumerate(rows):
        for key,value in row.items():
            column = columns.setdefault(key,dict(row_indices=[],values=[]))
            column['row_indices'].append(index);column['values'].append(value)
    return dict(storage_schema=DECISION_STORAGE,row_count=len(rows),columns=columns)


def restore_decisions(packed):
    if not isinstance(packed,dict) or packed.get('storage_schema') != DECISION_STORAGE:
        raise ValueError('Unknown decision storage schema')
    count=packed.get('row_count')
    if type(count) is not int or count<0:
        raise ValueError('Invalid decision row count')
    rows=[{} for _ in range(count)]
    for key,column in packed['columns'].items():
        indices=column['row_indices'];values=column['values']
        if len(indices)!=len(values) or len(set(indices))!=len(indices):
            raise ValueError('Invalid decision column length/duplicate index')
        for index,value in zip(indices,values):
            if type(index) is not int or not 0<=index<count:
                raise ValueError('Invalid decision row index')
            rows[index][key]=value
    return rows


def audit_payload(payload, params):
    """Recompute observable balances/ledgers; do not complete an unknown prefix."""
    issues = []
    segments = payload.get('segments', [])
    observed = number(payload['summary']['observed_until_minute'])
    requested = number(payload['summary']['requested_end_minute'])
    maxima = dict(electric=None, cooling=None, auxiliary=None, recorded=None,
                  recorded_steam=None, DG_energy=None, DG_rotor=None, DG_electrical=None, DG_budget=None)
    minimum_steam = None
    totals = {key: 0. for key in WORK_FIELDS}
    valid_receipts = 0
    expected_start = 0.
    previous_fuel = None
    previous_rotor = None
    fuel_settlements = [dict(time_minute=e.get('time_minute'),
                             budget_roundoff_remainder_kWh=e.get('budget_roundoff_remainder_kWh'))
                        for e in payload.get('events', []) if e.get('kind') == 'diesel_fuel_exhausted']
    exhaustion = min((number(e['time_minute']) for e in fuel_settlements), default=None)

    def problem(kind, index=None, **details):
        issues.append(dict(kind=kind, segment_index=index, **details))

    def maximum(name, value):
        value = abs(number(value))
        maxima[name] = max(value, maxima[name] or 0.)
        return value

    for index, segment in enumerate(segments):
        try:
            start = number(segment['start_minute']); end = number(segment['end_minute'])
            if abs(start - expected_start) > TIME_TOL:
                problem('segment_coverage', index, expected_start_minute=expected_start, actual_start_minute=start)
            if end <= start:
                problem('nonpositive_interval', index, start_minute=start, end_minute=end)
            expected_start = end
            dt = (end - start) / 60.
            action = segment['action']
            loads = segment['loads_kW']; lost = segment['unserved_kW']
            if len(loads) != 4 or len(lost) != 4 or any(len(row) != 3 for row in loads + lost):
                raise ValueError('Expected actual 4x3 load and loss arrays')
            served = [sum(number(loads[p][c]) - number(lost[p][c]) for p in range(4)) for c in range(3)]
            diesel = number(action['diesel_kW'])
            aux = number(segment['auxiliary_electric_kW'])
            electric = sum(map(number, action['gt_electric_kW'])) + number(action['grid_import_kW']) + diesel - served[0] - aux
            cold_abs = number(action['absorption_cooling_kW'])
            cold_ec = sum(map(number, action['electric_chiller_cooling_kW']))
            cooling = cold_abs + cold_ec - served[2]
            if maximum('electric', electric) > POWER_TOL:
                problem('electric_balance', index, residual_kW=electric)
            if maximum('cooling', cooling) > POWER_TOL:
                problem('cooling_balance', index, residual_kW=cooling)
            aux_from_converters = (cold_abs * params['abs_electric_kW_per_rt'] + cold_ec * params['ec_electric_kW_per_rt']) / RT_TO_KW
            if maximum('auxiliary', aux - aux_from_converters) > POWER_TOL:
                problem('auxiliary_balance', index, residual_kW=aux - aux_from_converters)
            heat_per_lb = params['steam_kWh_per_lb'] * params['exchanger_eff']
            steam_surplus = number(action['hrsg_steam_lbph']) + number(action['boiler_steam_lbph']) - served[1] / heat_per_lb - cold_abs / RT_TO_KW * params['abs_steam_lb_per_rt']
            minimum_steam = steam_surplus if minimum_steam is None else min(minimum_steam, steam_surplus)
            if steam_surplus * heat_per_lb < -POWER_TOL:
                problem('heat_source_deficit', index, steam_surplus_lbph=steam_surplus)
            residuals = segment['energy_balance_residuals']
            if len(residuals) != 3:
                raise ValueError('Expected saved e/c/steam-equivalent residuals')
            for value in residuals:
                if maximum('recorded', value) > POWER_TOL:
                    problem('recorded_energy_residual', index, residuals=residuals)
                    break
            if maximum('recorded_steam', segment['steam_balance_residual_lbph']) > POWER_TOL:
                problem('recorded_steam_residual', index, residual_lbph=segment['steam_balance_residual_lbph'])
            f0 = number(segment['pre_state']['diesel_energy_remaining_kWh'])
            f1 = number(segment['post_state']['diesel_energy_remaining_kWh'])
            if min(f0, f1) < -WORK_TOL:
                problem('negative_fuel_budget', index, before=f0, after=f1)
            if f1 > f0 + WORK_TOL or (previous_fuel is not None and f0 > previous_fuel + WORK_TOL):
                problem('unmodeled_refuel', index, previous_terminal=previous_fuel, before=f0, after=f1)
            if previous_fuel is not None:
                settlement = sum(number(e['budget_roundoff_remainder_kWh']) for e in fuel_settlements
                                 if abs(number(e['time_minute']) - start) <= TIME_TOL)
                if abs(previous_fuel - f0 - settlement) > WORK_TOL:
                    problem('between_interval_fuel_discontinuity', index, previous_terminal=previous_fuel,
                            before=f0, declared_roundoff_settlement=settlement)
            previous_fuel = f1
            receipt = segment.get('diesel_local_response')
            if not isinstance(receipt, dict) or not receipt:
                problem('missing_dg_receipt', index, electrical_kW=diesel,
                        missing_work_is_unknown_even_at_zero_electric_power=True)
                continue
            work = {key: number(receipt[key]) for key in WORK_FIELDS}
            for key in ('mechanical_work_kWh','electrical_work_kWh','loss_work_kWh','fuel_budget_debit_kWh_equivalent'):
                if work[key] < -WORK_TOL:
                    problem('negative_DG_work_or_loss',index,field=key,value=work[key])
            for key, value in work.items():
                totals[key] += value
            valid_receipts += 1
            energy = work['mechanical_work_kWh'] - work['electrical_work_kWh'] - work['loss_work_kWh'] - work['rotor_delta_kWh']
            if maximum('DG_energy', energy) > WORK_TOL:
                problem('DG_energy_conservation', index, residual_kWh=energy)
            if abs(number(receipt['energy_residual_kWh']) - energy) > WORK_TOL:
                problem('DG_recorded_energy_residual_mismatch', index)
            before = receipt['state_before']; after = receipt['state_after']
            rotor0 = number(before['rotor_energy_kWs']); rotor1 = number(after['rotor_energy_kWs'])
            if min(rotor0,rotor1)/3600. < -WORK_TOL:
                problem('negative_DG_rotor_energy',index,before_kWs=rotor0,after_kWs=rotor1)
            rotor_delta = (rotor1 - rotor0) / 3600.
            if maximum('DG_rotor', rotor_delta - work['rotor_delta_kWh']) > WORK_TOL:
                problem('DG_rotor_delta_mismatch', index, recomputed_kWh=rotor_delta, recorded_kWh=work['rotor_delta_kWh'])
            if previous_rotor is not None and abs(rotor0 - previous_rotor) / 3600. > WORK_TOL:
                problem('DG_between_interval_rotor_discontinuity', index)
            previous_rotor = rotor1
            if maximum('DG_electrical', work['electrical_work_kWh'] - diesel * dt) > WORK_TOL:
                problem('DG_electrical_work_mismatch', index)
            budget_residual = f0 - f1 - work['fuel_budget_debit_kWh_equivalent']
            if maximum('DG_budget', budget_residual) > WORK_TOL:
                problem('DG_budget_debit_mismatch', index, residual_kWh=budget_residual)
            if abs(work['fuel_budget_debit_kWh_equivalent'] - work['mechanical_work_kWh']) > WORK_TOL:
                problem('DG_budget_not_mechanical_work', index)
            if abs(number(receipt['actual_seconds']) - (end-start)*60.) > TIME_TOL:
                problem('DG_receipt_interval_mismatch', index)
            if (f0 <= 0. or before.get('fuel_exhausted') is True or
                    segment['pre_state'].get('diesel_status') == 'EMPTY') and diesel > POWER_TOL:
                problem('positive_diesel_after_empty', index, electrical_kW=diesel)
            if exhaustion is not None and start >= exhaustion - TIME_TOL:
                if diesel > POWER_TOL or work['mechanical_work_kWh'] > WORK_TOL or f0 > WORK_TOL:
                    problem('diesel_restart_after_exhaustion', index)
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            problem('missing_or_invalid_physical_field', index, error_type=type(error).__name__, message=str(error))
    if abs(expected_start - observed) > TIME_TOL:
        problem('observed_prefix_coverage', expected_end_minute=observed, actual_end_minute=expected_start)
    if observed > requested + TIME_TOL:
        problem('observed_beyond_registered_end', observed=observed, requested=requested)
    for event in fuel_settlements:
        if event['budget_roundoff_remainder_kWh'] is None or not 0. <= number(event['budget_roundoff_remainder_kWh']) <= 1e-9:
            problem('undeclared_or_large_exhaustion_settlement', event=event)
    return dict(schema=SCHEMA, issues=issues, segment_count=len(segments),
                observed_until_minute=observed, requested_end_minute=requested,
                complete_registered_interval=observed >= requested-TIME_TOL,
                unknown_tail_minutes=[observed, requested] if observed < requested-TIME_TOL else None,
                max_recomputed_electric_balance_kW=maxima['electric'],
                max_recomputed_cooling_balance_kW=maxima['cooling'],
                max_recomputed_auxiliary_difference_kW=maxima['auxiliary'],
                minimum_steam_surplus_after_load_and_ABS_lbph=minimum_steam,
                independent_steam_equality_identified=False,
                heat_check='Recorded steam/equivalent equality plus independently recomputed source adequacy; unused-steam variable not saved',
                max_recorded_energy_residual_kW=maxima['recorded'],
                max_recorded_steam_residual_lbph=maxima['recorded_steam'],
                DG_receipt_segment_count=valid_receipts,
                DG_totals=totals if valid_receipts == len(segments) and segments else None,
                DG_max_residuals=maxima, numerical_fuel_settlements=fuel_settlements,
                power_audit_tolerance_kW=POWER_TOL, work_audit_tolerance_kWh=WORK_TOL,
                time_audit_tolerance_minutes=TIME_TOL,
                original_service_and_failure_thresholds_changed=False, no_tail_imputation=True)


def gzip_write(path, value):
    with Path(path).open('xb') as target:
        with gzip.GzipFile(fileobj=target, mode='wb', mtime=0, filename='') as compressed:
            with __import__('io').TextIOWrapper(compressed, encoding='utf8') as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def audit_pair(result_path, out, params, context=None):
    result_path = Path(result_path); out = Path(out)
    record = read(result_path)
    key = result_path.parents[2].name + '__' + hashlib.sha256(str(result_path.resolve()).encode('utf8')).hexdigest()[:20]
    destination = out/'recorded_timeseries'/(key+'.json.gz')
    receipt_path = out/'pairs'/(key+'.receipt.json')
    if destination.exists() or receipt_path.exists():
        raise FileExistsError('Previous pair audit is immutable; use a fresh label')
    identity = {k: record.get(k) for k in ('scenario_id', 'policy_id', 'model_id', 'analysis_class', 'source_day_index', 'status')}
    raw = result_path.parent/'trajectory.json.gz'
    actual_sha = sha(raw) if raw.is_file() else None
    matches = bool(actual_sha and actual_sha == record.get('raw_sha256'))
    if raw.is_file():
        with gzip.open(raw, 'rt', encoding='utf8') as stream:
            payload = json.load(stream)
        audit = audit_payload(payload, params)
        if not matches:
            audit['issues'].append(dict(kind='raw_sha_mismatch', registered=record.get('raw_sha256'), actual=actual_sha))
        for key, actual in (('scenario_id',payload['summary'].get('scenario_id')),
                            ('policy_id',payload['summary'].get('policy_id')),
                            ('model_id',payload.get('execution_model_id')),
                            ('manifest_signature',payload['summary'].get('manifest_signature')),
                            ('model_config_signature',payload.get('model_config_signature'))):
            if record.get(key) is not None and actual is not None and record[key] != actual:
                audit['issues'].append(dict(kind='raw_result_identity_mismatch',field=key,
                                            registered=record[key],actual=actual))
        if context and record.get('manifest_signature') != context['manifest_signature']:
            audit['issues'].append(dict(kind='result_manifest_signature_mismatch'))
        omissions = compact_witnesses(payload)
        if 'decisions' in payload:
            decisions = payload['decisions']
            packed = columnar_decisions(decisions)
            if restore_decisions(packed) != decisions:
                raise ValueError('Decision column storage roundtrip failed')
            payload['decisions'] = packed
    else:
        payload = dict(raw_missing=True, saved_result=record)
        failure = result_path.parent/'UNHANDLED_ATTEMPT_ERROR.json'
        if failure.exists():
            payload['UNHANDLED_ATTEMPT_ERROR'] = read(failure)
        audit = dict(issues=[dict(kind='raw_missing', physical_outcome='unknown')], segment_count=None,
                     DG_totals=None, complete_registered_interval=None, unknown_tail_minutes=None)
        omissions = []
    if 'audit_retention' in payload:
        raise ValueError('Raw already has reserved audit_retention field')
    payload['audit_retention'] = dict(schema=SCHEMA, raw_path=str(raw.resolve()), raw_sha256=actual_sha,
        result_path=str(result_path.resolve()), result_sha256=sha(result_path),
        original_top_level_fields_preserved=True, every_physical_segment_unchanged=True,
        commands_queue_observations_readiness_and_error_retained=True, omitted_witnesses=omissions,
        decision_storage_schema=DECISION_STORAGE, decision_roundtrip_checked=True,
        omitted_fields='Repeated solver_backend_layers witnesses (scalar layer/attempt/error/validation maxima retained); joint_dispatch.diagnostic static witnesses (scalar/options/objective/layers/status, coordination R/Dmax/support/shared-ready envelopes retained); other raw_x and validation.rows/bound_violation arrays only. Every actual physical interval, command, readiness_context, observation and error remains. Decisions use lossless field columns; restore_decisions reconstructs rows.',
        selected_full_raw_required_for='Exact omitted optimizer vectors and per-row validation witnesses',
        full_raw_remains_local=True, no_replay=True, no_tail_imputation=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    gzip_write(destination, payload)
    receipt = dict(schema=SCHEMA, audit_program_sha256=sha(Path(__file__)), identity=identity, batch=result_path.parents[2].name,
        result_path=str(result_path.resolve()), result_sha256=sha(result_path),
        raw_path=str(raw.resolve()), registered_raw_sha256=record.get('raw_sha256'), raw_sha256=actual_sha,
        raw_sha_matches=matches, raw_bytes=raw.stat().st_size if raw.exists() else None,
        timeseries_path=str(destination.relative_to(out)), timeseries_sha256=sha(destination),
        timeseries_bytes=destination.stat().st_size, audit=audit,
        omitted_witness_count=len(omissions), omitted_canonical_json_bytes=sum(x['canonical_json_bytes'] for x in omissions),
        omitted_paths_and_hashes='Inside gzip audit_retention.omitted_witnesses',
        error=record.get('error'), analysis_error=record.get('analysis_error'), registration=context)
    write(receipt_path, receipt)
    return dict(receipt, receipt_path=str(receipt_path.relative_to(out)), receipt_sha256=sha(receipt_path))


def restore_file(source, destination):
    """Reconstruct canonical decision rows and full retained physical evidence.

    Omitted optimizer witnesses stay explicitly omitted; their indexed full raw
    is required to recover those diagnostics. Never call this a full raw copy.
    """
    source=Path(source);destination=Path(destination)
    if destination.exists():
        raise FileExistsError('Restoration destination must be new')
    with gzip.open(source,'rt',encoding='utf8') as stream:
        payload=json.load(stream)
    if isinstance(payload.get('decisions'),dict):
        payload['decisions']=restore_decisions(payload['decisions'])
    destination.parent.mkdir(parents=True,exist_ok=True)
    gzip_write(destination,payload)
    return dict(source=str(source),source_sha256=sha(source),destination=str(destination),
                destination_sha256=sha(destination),omitted_solver_witnesses_restored=False,
                actual_physical_segments_retained=True,canonical_decision_rows_restored=True)


def peak_working_set_bytes():
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
             'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return counters.PeakWorkingSetSize
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label')
    parser.add_argument('--restore', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--batch-glob', default='*')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--workers', type=int, choices=(1,), default=1)
    args = parser.parse_args(argv)
    if args.restore is not None:
        if args.destination is None or args.label is not None:
            raise ValueError('Use --restore INPUT --destination NEW_OUTPUT without --label')
        print(json.dumps(restore_file(args.restore,args.destination)),flush=True)
        return 0
    if args.destination is not None:
        raise ValueError('--destination requires --restore')
    if not args.label or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.label):
        raise ValueError('Use a simple fresh label')
    if args.limit is not None and args.limit < 1:
        raise ValueError('Positive limit required')
    results = sorted(p for directory in (B/'experiments').glob(args.batch_glob) if directory.is_dir()
                     for p in (directory/'pairs').glob('*/RESULT.json'))
    if args.limit is not None:
        results = results[:args.limit]
    if not results:
        raise ValueError('No saved RESULT pairs selected')
    out = B/'review'/('raw_audit_'+args.label)
    out.mkdir(parents=True, exist_ok=False)
    config = ROOT/'configs/plant_configs.json'; config_sha = sha(config)
    cfg = read(config); a = cfg['engineering_assumptions_not_from_source']; h = cfg['historical_pnnl_performance']
    params = dict(steam_kWh_per_lb=number(a['kWh_thermal_per_lb_steam']),
                  exchanger_eff=number(a['steam_to_building_heat_exchanger_efficiency']),
                  abs_steam_lb_per_rt=number(h['existing_absorption_steam_lb_per_RT_hr']),
                  abs_electric_kW_per_rt=number(h['existing_absorption_aux_kW_per_RT']),
                  ec_electric_kW_per_rt=number(h['existing_electric_chiller_all_in_kW_per_RT']))
    index = []; failures = []; contexts = {}
    for result in results:
        batch = result.parents[2]
        try:
            if batch not in contexts:
                plan = read(batch/'plan.json'); manifest = read(batch/'manifest.json')
                if manifest['source_sha256'].get('configs/plant_configs.json') != config_sha:
                    raise ValueError('Frozen plant config SHA differs; refuse recomputation using other physics')
                contexts[batch] = dict(phase=plan['phase'], plan_sha256=sha(batch/'plan.json'),
                    manifest_sha256=sha(batch/'manifest.json'), manifest_signature=manifest['signature'],
                    plant_config_sha256=config_sha, plant_balance_parameters=params,
                    physical_source_binding_sha256=hashlib.sha256(ENCODER.encode(plan['source_sha256']).encode('utf8')).hexdigest())
            receipt = audit_pair(result, out, params, contexts[batch])
            index.append({key: receipt[key] for key in ('identity', 'batch', 'receipt_path', 'receipt_sha256',
                'raw_path', 'raw_sha256', 'raw_bytes', 'timeseries_path', 'timeseries_sha256', 'timeseries_bytes')})
            print(json.dumps(dict(audited=len(index), selected=len(results), pair=result.parent.name,
                issues=len(receipt['audit']['issues']), compact_bytes=receipt['timeseries_bytes'],
                peak_working_set_bytes=peak_working_set_bytes())), flush=True)
            if receipt['audit']['issues']:
                failures.append(dict(result=str(result), receipt_path=receipt['receipt_path'], issues=receipt['audit']['issues']))
        except Exception as error:
            failure = dict(result_path=str(result.resolve()), result_sha256=sha(result),
                type=type(error).__name__, message=str(error), traceback=traceback.format_exc(),
                physical_outcome='unknown; audit failed, no substitute trajectory created')
            failures.append(failure)
            write(out/'failures'/(hashlib.sha256(str(result).encode('utf8')).hexdigest()[:20]+'.json'), failure)
            print(json.dumps(dict(audit_error=str(result), type=type(error).__name__, message=str(error))), flush=True)
    summary = dict(schema=SCHEMA, audit_program_sha256=sha(Path(__file__)), selected_pairs=len(results),
        audited_pairs=len(index), failed_or_issue_pairs=len(failures), failures=failures,
        index=index, physical_episodes_started=0, annual_inputs_loaded=0, workers=1,
        total_timeseries_bytes=sum(x['timeseries_bytes'] for x in index),
        total_original_raw_bytes=sum(x['raw_bytes'] or 0 for x in index),
        peak_working_set_bytes=peak_working_set_bytes(), partial_selection=args.limit is not None,
        batches=dict(Counter(x['batch'] for x in index)),
        compact_files_are_package_candidates=True, full_original_raw_unchanged=True,
        scope='Saved recorded physical/causal evidence only; numerical audit, not replay or hospital validation')
    write(out/'RAW_AUDIT_INDEX.json', summary)
    print(json.dumps({key:summary[key] for key in ('selected_pairs', 'audited_pairs', 'failed_or_issue_pairs',
        'total_timeseries_bytes', 'peak_working_set_bytes')}), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
