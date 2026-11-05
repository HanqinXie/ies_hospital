"""Offline native service recovery and independent continuous-block accounting.

Only one raw block is decoded at a time. The whole-prefix service projection
contains clocks and the original request ledger, without primal solver rows,
physical state or simulated future values. No environment is constructed.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

from .journal import verify_journal
from tools.model_correctness_v23 import metrics as native_metrics
from tools.operating_rules_study import ledger
from tools.sci_engineering.design import digest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'hospital_ies.annual_readiness.offline_analysis.v1'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _sum(values):
    values = list(values)
    return math.fsum(values) if values and all(ledger._number(v) for v in values) else None


def _source_bindings(repo, bindings):
    receipts = {}
    for group in ('source', 'inputs'):
        for name, expected in bindings.get(group, {}).items():
            path = (repo/name).resolve()
            if not path.is_relative_to(repo.resolve()) or not path.is_file():
                raise ValueError('Missing or outside-repository source binding: '+name)
            if _sha(path) != expected:
                raise ValueError('Frozen source/input SHA mismatch: '+name)
            receipts[name] = expected
    return receipts


def _initial_preparation(block):
    value = block.get('initial_preparation')
    if isinstance(value, dict):
        return copy.deepcopy(value), 'first_block.actual_initial_preparation'
    for event in block.get('events', []):
        if event.get('kind') == 'm21_storage_initial_inventory':
            value = event.get('initial_charged_input_kWh')
            if isinstance(value, dict):
                return copy.deepcopy(value), 'actual_initial_inventory_event'
    state = block.get('terminal', {}).get('execution_state', {})
    value = {name:state.get(name, {}).get('initial_charged_input_kWh') for name in ('ups','cold_store')}
    return value, 'first_terminal.actual_initial_charged_input' if all(
        ledger._number(v) for v in value.values()) else 'unknown_missing_actual_initial_inputs'


def _combined_check(rows):
    checks = [row.get('check') for row in rows]
    known = [row.get('known_segment_maximum_absolute_residual') for row in rows]
    tolerance = rows[0].get('tolerance') if rows else None
    unit = rows[0].get('tolerance_unit') if rows else None
    if any(row.get('tolerance') != tolerance or row.get('tolerance_unit') != unit for row in rows):
        raise ValueError('Independent block tolerance/unit contract changed')
    return dict(check='fail' if 'fail' in checks else 'pass' if checks and all(v == 'pass' for v in checks) else 'unknown',
        maximum_absolute_residual=max((v for v in known if ledger._number(v)), default=None),
        tolerance=tolerance, tolerance_unit=unit, block_checks=checks)


def analyze_run(output, *, repo=ROOT):
    """Return detached observed-prefix evidence; never write into the producer."""
    output, repo = Path(output).resolve(), Path(repo).resolve()
    evidence = output/'evidence'
    verified = verify_journal(evidence)
    summary = _read(evidence/'SUMMARY.json')
    names = ('CASE.json','RESULT.json','BINDINGS.json','RESOLVED_CASE.json',
        'evidence/IDENTITY.json','evidence/SUMMARY.json','evidence/blocks.jsonl')
    before = {name:_sha(output/name) for name in names if (output/name).is_file()}
    saved = _read(output/'CASE.json') if 'CASE.json' in before else {}
    case = saved.get('case', {})
    result = _read(output/'RESULT.json') if 'RESULT.json' in before else {}
    bindings = _read(output/'BINDINGS.json') if 'BINDINGS.json' in before else {}
    source_receipts = _source_bindings(repo, bindings)
    identity = verified['identity']
    expected = {key:value for key,value in dict(case_id=case.get('id'),
        case_sha256=digest(case) if case else None,
        source_sha256=digest(bindings['source']) if 'source' in bindings else None,
        input_sha256=digest(bindings['inputs']) if 'inputs' in bindings else None,
        git_commit=bindings.get('git_commit')).items() if value is not None}
    if any(identity.get(key) != value for key,value in expected.items()):
        raise ValueError('Journal identity differs from case or frozen source bindings')
    for key in ('case_id','model_id','source_sha256','case_sha256'):
        if key in result and identity.get(key) != result[key]:
            raise ValueError('Producer RESULT and journal identity mismatch: '+key)
    observation = copy.deepcopy(summary['observation'])
    requested = observation['requested_end_minute']
    if case:
        scenario = case['scenario']
        case_requested = float(scenario['duration_steps'])*float(scenario.get('step_minutes',15.))
        if case_requested != requested:
            raise ValueError('Case requested duration differs from journal observation')
    if ledger._number(result.get('observed_until_minute')) and result['observed_until_minute'] != observation['observed_until_minute']:
        raise ValueError('Producer RESULT observed prefix differs from journal')
    resolved = _read(output/'RESOLVED_CASE.json') if 'RESOLVED_CASE.json' in before else {}
    model_config = resolved.get('model_config', result.get('resolved_model_config', {}))
    params, tariffs, accounting_receipts = {}, {}, {}
    accounting_error = None
    if bindings and case and verified['block_count']:
        annual = case.get('annual_program')
        from tools.annual_program import MODEL_ID as ANNUAL_MODEL_ID
        if isinstance(annual,dict) and annual.get('model_id') == ANNUAL_MODEL_ID:
            from tools.annual_program.analysis import observed_source_hours
            hours = observed_source_hours(case,observation['observed_until_minute'])
        else:
            hour = int(case['scenario']['start_hour'])
            hours = range(hour, hour+int(observation['observed_until_minute']//60.)+1)
        params, tariffs, accounting_receipts = ledger._frozen_parameters(repo, bindings, dict(
            model_config=model_config, segments=[dict(hour_of_year=h) for h in hours]))
    else:
        accounting_error = 'Missing source-bound case/accounting context or observed segments'

    compact, block_reports, source_rows, start_events = [], [], [], []
    first_output = {name:None for name in ('GT_1','GT_2','GT_3','GRID','DG','BOILER','HRSG','ABS','EC')}
    balance_rows = {'e':[], 'c':[]}
    diesel_checks = {name:[] for name in ('energy_balance','electrical_action_match','shaft_budget_match')}
    shaft_boundaries, shaft_debits = [], []
    fees = {name:[] for name in ('grid_fee_USD2001','GT_gas_fee_USD2001','boiler_gas_fee_USD2001',
        'DG_electric_fee_USD2001','DG_shaft_fee_USD2001','DG_shaft_difference_USD2001',
        'observed_operating_fee_USD2001')}
    preparation_fee, preparation_source = None, None
    issues, unknowns = [], []
    for index, record in enumerate(verified['raw_files_sha256']):
        path = evidence/record['file']
        if _sha(path) != record['sha256']:
            raise ValueError('Raw block SHA changed after journal verification')
        with gzip.open(path, 'rt', encoding='utf8') as stream:
            block = json.load(stream)
        segments = block['segments']
        preparation, source = _initial_preparation(block) if index == 0 else ({'ups':0.,'cold_store':0.}, 'continuation_not_recharged')
        if index == 0:
            preparation_source = source
        payload = dict(segments=segments, continuation_start_minute=block['start_minute'],
            summary={'requested_end_minute':block['end_minute']}, model_config=model_config,
            m21_initial_storage=preparation, terminal_snapshot=block['terminal'])
        audit = ledger.audit_payload(payload, parameters=params, tariffs_by_hour=tariffs)
        for carrier in balance_rows:
            balance_rows[carrier].append(audit['balances'][carrier])
        for name in diesel_checks:
            diesel_checks[name].append(audit['diesel'][name])
        for name in fees:
            fees[name].append(audit['economics'][name])
        if index == 0:
            preparation_fee = audit['economics']['initial_preparation_fee_USD2001']
        known_failures = [name for name in audit['failed_checks'] if name != 'execution.producer_status']
        issues.extend('block_'+str(index)+'.'+name for name in known_failures)
        unknowns.extend('block_'+str(index)+'.'+name for name in audit['unknown_checks']
            if not name.startswith(('execution.','identity.','economics.producer_fee_match')))
        block_reports.append(dict(index=index,start_minute=block['start_minute'],end_minute=block['end_minute'],
            balances={k:audit['balances'][k] for k in ('e','c')}, economics=audit['economics'],
            failed_checks=known_failures, error=copy.deepcopy(block.get('error')),
            solver_errors=copy.deepcopy(block.get('solver_errors',[]))))
        for event in block['events']:
            if event.get('kind') == 'm22_DG_dedicated_DC_start':
                start_events.append(copy.deepcopy(event))
        for segment in segments:
            compact.append({key:copy.deepcopy(segment.get(key)) for key in
                ('start_minute','end_minute','service_request_ledger')})
            action = segment.get('action') or {}
            gt = action.get('gt_electric_kW')
            values = dict(GRID=action.get('grid_import_kW'), DG=action.get('diesel_kW'),
                BOILER=action.get('boiler_steam_lbph'), HRSG=action.get('hrsg_steam_lbph'),
                ABS=action.get('absorption_cooling_kW'), EC=_sum(action.get('electric_chiller_cooling_kW',[])))
            values.update({name:gt[i] if isinstance(gt,list) and len(gt)==3 else None
                for i,name in enumerate(('GT_1','GT_2','GT_3'))})
            for name,value in values.items():
                if first_output[name] is None and ledger._number(value) and value > 0.:
                    first_output[name] = segment['start_minute']
            annual = segment.get('annual_readiness') or {}
            station = segment.get('m22_station') or {}
            source_rows.append(dict(start_minute=segment['start_minute'],end_minute=segment['end_minute'],
                actual_GT_kW=copy.deepcopy(gt),actual_GT_online_mask=[v > 0. for v in gt]
                    if isinstance(gt,list) and len(gt)==3 and all(ledger._number(v) for v in gt) else None,
                actual_source_flows=values,boiler_ready=station.get('boiler_ready'),
                qualification_before=copy.deepcopy(annual.get('qualification_before')),
                qualification_after=copy.deepcopy(annual.get('qualification_after')),
                actual_normal_source_proof=copy.deepcopy(annual.get('actual_normal_source_proof'))))
            initial = (segment.get('pre_state') or {}).get('diesel_energy_remaining_kWh')
            final = (segment.get('post_state') or {}).get('diesel_energy_remaining_kWh')
            shaft_boundaries.append((initial, final))
            receipt = segment.get('diesel_local_response') or {}
            receipt = receipt.get('energy_receipt',receipt)
            shaft_debits.append(receipt.get('fuel_budget_debit_kWh_equivalent'))
        del audit, payload, segments, block

    # This is the sole whole-prefix scientific projection. Native materiality,
    # joins, episode union and 60-second heat/cold confirmation stay unchanged.
    service_payload = dict(segments=compact, continuation_start_minute=0., summary={'requested_end_minute':requested})
    native, native_error = None, None
    try:
        native = native_metrics.build_reliability_metrics(service_payload)
    except (ValueError, TypeError) as exc:
        native_error = dict(type=type(exc).__name__,message=str(exc))
        issues.append('native_service_projection.invalid_original_ledger')
    discrepancies = []
    if native is not None:
        for priority in ledger.PRIORITIES:
            for carrier in ledger.CARRIERS:
                a = native['carrier_service'][priority][carrier]['ENS_kWh']
                b = verified['carrier_service'][priority][carrier]['ENS_kWh']
                difference = a-b if all(ledger._number(v) for v in (a,b)) else None
                discrepancies.append(dict(priority=priority,carrier=carrier,
                    native_cut_plus_connected_ENS_kWh=a,journal_request_minus_delivered_ENS_kWh=b,difference_kWh=difference))
    maximum = max((abs(r['difference_kWh']) for r in discrepancies if ledger._number(r['difference_kWh'])),default=None)
    service_check = 'fail' if maximum is not None and maximum > ledger.WORK_TOL else 'pass' if len(discrepancies)==12 and all(
        ledger._number(r['difference_kWh']) for r in discrepancies) else 'unknown'
    if service_check == 'fail':
        issues.append('native_vs_journal_ENS')
    elif service_check == 'unknown':
        unknowns.append('native_vs_journal_ENS')
    economics = {name:_sum(values) for name,values in fees.items()}
    economics.update(initial_preparation_fee_USD2001=preparation_fee,initial_preparation_source=preparation_source,
        initial_preparation_count=1 if verified['block_count'] else 0,
        actual_fee_USD2001=_sum([economics['observed_operating_fee_USD2001'],preparation_fee]),
        producer_economics_used_for_calculation=False,cost_USD_step_used=False,price_year=2001,currency='USD',
        DG_shaft_correction_applied_once=True,unobserved_tail_fee_USD2001=None,
        diesel_chemical_kWh=None,diesel_liters=None,accounting_error=accounting_error)
    producer_fee = result.get('economics',{}).get('actual_fee_USD2001')
    economics['producer_fee_USD2001_for_comparison'] = producer_fee
    economics['independent_minus_producer_USD2001'] = economics['actual_fee_USD2001']-producer_fee if all(
        ledger._number(v) for v in (economics['actual_fee_USD2001'],producer_fee)) else None
    economics['producer_fee_comparison_tolerance_USD2001'] = ledger.FEE_TOL
    delta = economics['independent_minus_producer_USD2001']
    if ledger._number(delta) and abs(delta) > ledger.FEE_TOL:
        issues.append('economics.independent_vs_producer_fee')
    elif not ledger._number(delta):
        unknowns.append('economics.independent_vs_producer_fee')
    initial = shaft_boundaries[0][0] if shaft_boundaries else None
    final = shaft_boundaries[-1][1] if shaft_boundaries else None
    debit = _sum(shaft_debits)
    residual = initial-debit-final if all(ledger._number(v) for v in (initial,debit,final)) else None
    gaps = [a[1]-b[0] if all(ledger._number(v) for v in (a[1],b[0])) else None
        for a,b in zip(shaft_boundaries,shaft_boundaries[1:])]
    gap = max(map(abs,gaps)) if gaps and all(ledger._number(v) for v in gaps) else 0. if len(shaft_boundaries)==1 else None
    stock_check = 'fail' if any(ledger._number(v) and abs(v)>ledger.WORK_TOL for v in (residual,gap)) else 'pass' if all(
        ledger._number(v) for v in (residual,gap)) else 'unknown'
    if stock_check == 'fail':
        issues.append('diesel.shaft_budget_chain_or_total')
    storage = copy.deepcopy(verified['storage'])
    for name,row in storage.items():
        values = [row.get(k) for k in ('energy_residual_kWh','receipt_chain_max_gap_kWh','segment_energy_max_absolute_residual_kWh')]
        row['check'] = 'fail' if any(ledger._number(v) and abs(v)>ledger.WORK_TOL for v in values) else 'pass' if all(
            ledger._number(v) for v in values) else 'unknown'
        row['tolerance_kWh'] = ledger.WORK_TOL
        if row['check'] == 'fail':
            issues.append('storage.'+name+'.global_receipt_chain')
    if not observation['complete']:
        unknowns.append('unobserved_tail')
    if result.get('status') != 'completed':
        issues.append('execution.producer_status')
    if result.get('source_and_inputs_unchanged') is False:
        issues.append('execution.source_or_input_drift')
    current_code = {name:_sha(path) for name,path in (
        ('tools/model_correctness_v23/metrics.py',native_metrics.__file__),
        ('tools/operating_rules_study/ledger.py',ledger.__file__))}
    code_bound = all(source_receipts.get(name)==value for name,value in current_code.items())
    if not code_bound:
        unknowns.append('native_and_accounting_code_not_bound_to_producer')
    after = {name:_sha(output/name) for name in before}
    if before != after or _source_bindings(repo,bindings) != source_receipts:
        raise RuntimeError('Producer or source files changed during offline analysis')
    return dict(schema=SCHEMA,observation=observation,native_reliability=native,native_service_error=native_error,
        service_reconciliation=dict(check=service_check,maximum_absolute_ENS_difference_kWh=maximum,
            tolerance_kWh=ledger.WORK_TOL,cells=discrepancies),economics=economics,
        balances=dict(**{k:_combined_check(v) for k,v in balance_rows.items()},steam_full_closure='unknown',
            steam_reason='No complete primal-row and discarded-steam binding was consumed; no full steam closure claim'),
        storage=storage,diesel=dict(initial_shaft_budget_kWh=initial,final_shaft_budget_kWh=final,
            fuel_budget_debit_kWh_equivalent=debit,stock_residual_kWh=residual,stock_chain_max_gap_kWh=gap,
            stock_check=stock_check,tolerance_kWh=ledger.WORK_TOL,
            interval_checks={k:_combined_check(v) for k,v in diesel_checks.items()},chemical_fuel_kWh=None,liters=None),
        sources=dict(first_positive_output_minute=first_output,first_positive_is_service_recovery=False,
            intervals=source_rows,actual_DG_start_events=start_events,DG_start_event_count=len(start_events)),
        execution=dict(status=result.get('status'),error=copy.deepcopy(result.get('error')),
            journal_status=summary.get('status'),journal_error=copy.deepcopy(summary.get('error'))),
        block_accounting=block_reports,terminal=copy.deepcopy(verified['terminal']),
        DC_energy_kWh=None,unobserved_tail_service=None,
        failed_checks=sorted(set(issues)),unknown_checks=sorted(set(unknowns)),
        audit_status='failed' if issues else 'unknown' if unknowns else 'passed',
        qualification='Observed arithmetic and unchanged native research criteria; no clinical, OEM, DC-energy or annual safety qualification',
        provenance=dict(run_path=str(output),producer_files_sha256=before,producer_files_unchanged=True,
            journal_integrity_verified=True,journal_identity_sha256=verified['identity_sha256'],
            block_files_sha256=verified['raw_files_sha256'],producer_identity=identity,
            accounting_inputs_sha256=accounting_receipts,source_bindings_verified=bool(source_receipts),
            scientific_code_sha256=current_code,scientific_code_matches_producer_binding=code_bound,
            analyzer_sha256=_sha(__file__),maximum_decoded_blocks=1,full_raw_trajectory_retained=False,
            projection_fields=['start_minute','end_minute','service_request_ledger'],
            projection_segment_count=len(compact),simulation_rerun=False,
            created_utc=datetime.now(timezone.utc).isoformat()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--repo',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,required=True,help='Fresh detached analysis directory')
    args = parser.parse_args(argv)
    destination, producer = args.output.resolve(), args.run.resolve()
    if destination.exists() or destination == producer or destination.is_relative_to(producer):
        raise ValueError('Analysis output must be fresh and outside the producer directory')
    out = analyze_run(producer,repo=args.repo)
    destination.mkdir(parents=True,exist_ok=False)
    (destination/'ANALYSIS.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps(dict(output=str(destination),audit_status=out['audit_status'],
        observed_until_minute=out['observation']['observed_until_minute'],
        unknown_tail_minutes=out['observation']['unknown_tail_minutes'],simulation_rerun=False)))
    return 2 if out['audit_status']=='failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
