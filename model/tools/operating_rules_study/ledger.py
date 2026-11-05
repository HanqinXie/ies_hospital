"""Independent read-only arithmetic over recorded actual trajectory segments.

Only the Python standard library is imported. This module never constructs an
environment, imports production evaluation, or runs an optimizer. Missing data
remain None. Steam disposal is not recorded in existing native actions, so a
nonnegative source surplus is a sufficiency check, not a full closure proof.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'hospital_ies.operating_rules.independent_ledger.v1'
PRIORITIES = ('P0', 'P1', 'P2', 'P3')
CARRIERS = ('e', 'h', 'c')
POWER_TOL = 1e-4
STEAM_TOL = 1e-4
WORK_TOL = 1e-8
TIME_TOL = 1e-7
FEE_TOL = 1e-7


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _sum(values):
    values = list(values)
    return math.fsum(values) if values and all(_number(v) for v in values) else None


def _calculate(function, *values):
    return function(*values) if all(_number(v) for v in values) else None


def _matrix(matrix, row, column):
    if isinstance(matrix, list) and len(matrix) == 4 and all(isinstance(r, list) and len(r) == 3 for r in matrix):
        value = matrix[row][column]
        return value if _number(value) else None
    return None


def _residuals(values, durations, tolerance, unit='kW'):
    known = [v for v in values if _number(v)]
    complete = bool(values) and len(known) == len(values)
    peak = max(map(abs, known)) if known else None
    row = dict(check='fail' if peak is not None and peak > tolerance else 'pass' if complete else 'unknown',
        tolerance=tolerance, tolerance_unit=unit, missing_segment_count=len(values)-len(known),
        known_segment_maximum_absolute_residual=peak)
    row['maximum_absolute_residual_'+unit] = peak if complete else None
    if unit == 'kW':
        row['signed_residual_kWh'] = _sum(_calculate(lambda v, d: v*d, v, d) for v, d in zip(values, durations))
        row['absolute_residual_kWh'] = _sum(_calculate(lambda v, d: abs(v)*d, v, d) for v, d in zip(values, durations))
    else:
        row['signed_residual_'+unit] = _sum(values)
        row['absolute_residual_'+unit] = _sum(abs(v) if _number(v) else None for v in values)
    return row


def _observation(payload):
    origin = payload.get('continuation_start_minute')
    origin = 0. if origin is None else origin
    if not _number(origin):
        raise ValueError('Invalid segment observation origin')
    previous = origin
    contiguous = bool(payload.get('segments'))
    durations = []
    for segment in payload.get('segments', []):
        start, end = segment.get('start_minute'), segment.get('end_minute')
        if not all(_number(v) for v in (start, end)) or end <= start or start < previous-TIME_TOL:
            raise ValueError('Invalid, overlapping, or nonpositive actual segment')
        contiguous = contiguous and abs(start-previous) <= TIME_TOL
        durations.append((end-start)/60.)
        previous = end
    requested = payload.get('summary', {}).get('requested_end_minute')
    return dict(observed_from_minute=origin, observed_until_minute=previous,
        observed_duration_minutes=math.fsum(durations)*60., segment_count=len(durations),
        requested_end_minute=requested if _number(requested) else None,
        complete=bool(contiguous and _number(requested) and abs(previous-requested) <= TIME_TOL),
        contiguous=contiguous, unknown_tail_minutes=max(0., requested-previous) if _number(requested) else None,
        quantities_are_observed_segments_only=True), durations


def _services(segments, durations):
    service = {}
    identities = []
    for ip, priority in enumerate(PRIORITIES):
        service[priority] = {}
        for ic, carrier in enumerate(CARRIERS):
            sums = {k: [] for k in ('requested_kWh', 'delivered_kWh', 'active_cut_kWh', 'connected_unserved_kWh', 'ENS_kWh')}
            residual = []
            for segment, dt in zip(segments, durations):
                raw = segment.get('service_request_ledger') or {}
                values = [_matrix(raw.get(field), ip, ic) for field in
                    ('original_demand_kW', 'delivered_kW', 'active_cut_kW', 'connected_unserved_kW')]
                request, delivered, cut, unserved = values
                for key, value in zip(sums, values+[_calculate(lambda a,b: max(a-b,0.), request,delivered)]):
                    sums[key].append(_calculate(lambda v: v*dt, value))
                residual.append(_calculate(lambda r,d,c,u: r-d-c-u, *values))
            identity = _residuals(residual, durations, POWER_TOL)
            service[priority][carrier] = dict(**{key:_sum(values) for key,values in sums.items()},
                carrier=carrier, unit='kWh_'+carrier, request_identity=identity,
                source='actual service_request_ledger; actual elapsed segment duration',
                material_classification_recomputed=False)
            identities.append(identity['check'])
    return service, identities


def _balances(segments, durations, params):
    electric, cooling, steam, closure = [], [], [], []
    details = []
    for segment in segments:
        action = segment.get('action') or {}
        delivered = (segment.get('service_request_ledger') or {}).get('delivered_kW')
        served = [_sum(_matrix(delivered, p, c) for p in range(4)) for c in range(3)]
        ec = _sum(action.get('electric_chiller_cooling_kW', []))
        absorption = action.get('absorption_cooling_kW')
        converters = _calculate(lambda e,a,er,ar,rt: (e*er+a*ar)/rt,
            ec, absorption, params.get('ec_electric_kW_per_rt'), params.get('abs_aux_kW_per_rt'), params.get('RT_TO_KW'))
        sources = _sum([_sum(action.get('gt_electric_kW', [])),action.get('grid_import_kW'),
            action.get('diesel_kW'),action.get('ups_discharge_kW')])
        # station_auxiliary_kW already aggregates boiler/start/restart; do not add those twice.
        sinks = _sum([served[0],converters,action.get('ups_charge_kW'),action.get('cold_storage_pump_kW'),
            action.get('station_auxiliary_kW'),action.get('recovery_pump_electric_kW'),action.get('recovery_fan_electric_kW')])
        e = _calculate(lambda a,b:a-b,sources,sinks)
        c = _calculate(lambda a,b,d,ch,r,s:a+b+d-ch-r-s,absorption,ec,action.get('cold_discharge_kW'),
            action.get('cold_charge_kW'),action.get('recovery_cooling_kW'),served[2])
        heat_steam = _calculate(lambda h,energy,eta:h/(energy*eta),served[1],params.get('steam_kWh_per_lb'),params.get('exchanger_eff'))
        abs_steam = _calculate(lambda c,rt,ratio:c/rt*ratio,absorption,params.get('RT_TO_KW'),params.get('abs_steam_lb_per_rt'))
        excess = _calculate(lambda h,b,d,a:h+b-d-a,action.get('hrsg_steam_lbph'),action.get('boiler_steam_lbph'),heat_steam,abs_steam)
        dump = action.get('unused_steam_lbph')
        residual = _calculate(lambda a,b:a-b, excess,dump)
        electric.append(e); cooling.append(c); steam.append(excess); closure.append(residual)
        details.append(dict(start_minute=segment['start_minute'],end_minute=segment['end_minute'],
            electric_residual_kW=e,cooling_residual_kW=c,steam_source_surplus_lbph=excess,
            metered_unused_steam_lbph=dump,steam_closure_residual_lbph=residual))
    known = [v for v in steam if _number(v)]
    complete = bool(steam) and len(known) == len(steam)
    minimum = min(known) if known else None
    steam_result = _residuals(closure, durations, STEAM_TOL, 'lbph')
    steam_result.update(full_closure_check=steam_result.pop('check'),
        minimum_source_surplus_lbph=minimum if complete else None,
        source_sufficiency_check='fail' if minimum is not None and minimum < -STEAM_TOL else 'pass' if complete else 'unknown',
        signed_source_surplus_lb=_sum(_calculate(lambda v,dt:v*dt,v,dt) for v,dt in zip(steam,durations)),
        absolute_source_surplus_lb=_sum(_calculate(lambda v,dt:abs(v)*dt,v,dt) for v,dt in zip(steam,durations)),
        unmetered_steam_disposal_is_not_zero=True,
        limitation='Native actions omit discarded steam; a source surplus alone is not independent full steam closure.')
    return dict(e=_residuals(electric,durations,POWER_TOL),c=_residuals(cooling,durations,POWER_TOL),steam=steam_result), details


def _diesel(segments, durations):
    fields = ('mechanical_work_kWh','electrical_work_kWh','loss_work_kWh','rotor_delta_kWh','fuel_budget_debit_kWh_equivalent')
    totals = {key:[] for key in fields}
    energy, electrical, budget, chain = [], [], [], []
    previous = None
    for segment, dt in zip(segments,durations):
        receipt = segment.get('diesel_local_response') or {}
        receipt = receipt.get('energy_receipt',receipt)
        for field in fields: totals[field].append(receipt.get(field))
        energy.append(_calculate(lambda m,e,l,r:m-e-l-r,*(receipt.get(k) for k in fields[:4])))
        electrical.append(_calculate(lambda e,p:e-p*dt,receipt.get('electrical_work_kWh'),segment.get('action',{}).get('diesel_kW')))
        initial = segment.get('pre_state',{}).get('diesel_energy_remaining_kWh')
        final = segment.get('post_state',{}).get('diesel_energy_remaining_kWh')
        budget.append(_calculate(lambda a,b,w:a-b-w,initial,final,receipt.get('fuel_budget_debit_kWh_equivalent')))
        if previous is not None: chain.append(_calculate(lambda a,b:a-b,previous,initial))
        previous = final
    return dict(**{key:_sum(values) for key,values in totals.items()},
        energy_balance=_residuals(energy,durations,WORK_TOL,'kWh'),
        electrical_action_match=_residuals(electrical,durations,WORK_TOL,'kWh'),
        shaft_budget_match=_residuals(budget,durations,WORK_TOL,'kWh'),
        shaft_budget_chain=_residuals(chain,durations,WORK_TOL,'kWh'),
        chemical_fuel_kWh=None,diesel_liters=None,shaft_budget_is_chemical_fuel=False)


def _storage(payload, durations):
    stores = {}
    segments = payload.get('segments',[])
    terminal = payload.get('terminal_snapshot') or {}
    terminal_time_matches = _number(terminal.get('snapshot_time_minute')) and bool(segments) and abs(terminal['snapshot_time_minute']-segments[-1]['end_minute']) <= TIME_TOL
    for name, carrier, prefix in (('ups','e','ups'),('cold_store','c','cold')):
        receipts = [(s.get('m21_storage') or {}).get(name) or {} for s in segments]
        initial = receipts[0].get('initial_kWh') if receipts else None
        final = receipts[-1].get('final_kWh') if receipts else None
        totals = {field:_sum(r.get(field) for r in receipts) for field in
            ('charged_input_kWh','discharged_kWh','standing_loss_kWh','conversion_loss_kWh')}
        balance = _calculate(lambda i,c,d,s,l,f:i+c-d-s-l-f, initial,
            *(totals[key] for key in totals),final)
        gaps = [_calculate(lambda a,b:a-b,a.get('final_kWh'),b.get('initial_kWh')) for a,b in zip(receipts,receipts[1:])]
        gap = max(map(abs,gaps)) if gaps and all(_number(g) for g in gaps) else 0. if len(receipts)==1 and _number(initial) and _number(final) else None
        terminal_value = (terminal.get('inventories') or {}).get(name,{}).get('stock_kWh') if terminal_time_matches else None
        terminal_delta = _calculate(lambda a,b:a-b,terminal_value,final)
        config = payload.get('model_config',{}).get('sci_engineering',{}).get(name) or {}
        dynamic, charge_match, discharge_match = [], [], []
        for s,r,dt in zip(segments,receipts,durations):
            action = s.get('action') or {}
            charge, discharge = action.get(prefix+'_charge_kW'),action.get(prefix+'_discharge_kW')
            prediction = _calculate(lambda i,ch,d,eta_c,eta_d,loss: i*math.exp(-loss*dt)+ch*eta_c*dt-d/eta_d*dt,
                r.get('initial_kWh'),charge,discharge,config.get('charge_efficiency'),config.get('discharge_efficiency'),config.get('loss_per_hour'))
            dynamic.append(_calculate(lambda f,p:f-p,r.get('final_kWh'),prediction))
            charge_match.append(_calculate(lambda actual,p:actual-p*dt,r.get('charged_input_kWh'),charge))
            discharge_match.append(_calculate(lambda actual,p:actual-p*dt,r.get('discharged_kWh'),discharge))
        dynamics = _residuals(dynamic,durations,WORK_TOL,'kWh')
        charges = _residuals(charge_match,durations,WORK_TOL,'kWh')
        discharges = _residuals(discharge_match,durations,WORK_TOL,'kWh')
        numbers = [balance,gap,terminal_delta]
        failed = any(_number(v) and abs(v)>WORK_TOL for v in numbers) or any(r['check']=='fail' for r in (dynamics,charges,discharges))
        complete = all(_number(v) for v in (balance,gap))
        stores[name] = dict(carrier=carrier,unit='kWh_'+carrier,initial_stock_kWh=initial,final_stock_kWh=final,
            **totals,balance_residual_kWh=balance,receipt_chain_maximum_absolute_gap_kWh=gap,
            terminal_stock_kWh=terminal_value,terminal_minus_receipt_kWh=terminal_delta,
            terminal_time_matches_observed_end=terminal_time_matches,dynamics=dynamics,
            actual_charge_match=charges,actual_discharge_match=discharges,
            check='fail' if failed else 'pass' if complete else 'unknown',
            full_dynamics_check=dynamics['check'],tolerance_kWh=WORK_TOL,
            missing_terminal_is_unknown=terminal_value is None)
    return stores


def _economics(payload, result, params, tariffs, durations, diesel):
    rows = {k:[] for k in ('grid_purchase_kWh_e','grid_fee_USD2001','GT_gas_MMBtu','boiler_gas_MMBtu')}
    first_tariff = None
    for i,(s,dt) in enumerate(zip(payload.get('segments',[]),durations)):
        action = s.get('action') or {}
        tariff = s.get('grid_tariff_USD_kWh',action.get('grid_tariff_USD_kWh'))
        if not _number(tariff): tariff = tariffs.get(s.get('hour_of_year'),tariffs.get(str(s.get('hour_of_year'))))
        if i == 0: first_tariff = tariff
        work = _calculate(lambda p:p*dt,action.get('grid_import_kW'))
        rows['grid_purchase_kWh_e'].append(work)
        rows['grid_fee_USD2001'].append(_calculate(lambda w,t:w*t,work,tariff))
        rows['GT_gas_MMBtu'].append(_calculate(lambda p:p*dt,action.get('gt_fuel_MMBtu_h')))
        rows['boiler_gas_MMBtu'].append(_calculate(lambda p,h,eff,conversion:p*dt*h/eff/conversion,
            action.get('boiler_steam_lbph'),params.get('steam_kWh_per_lb'),params.get('boiler_eff'),params.get('kWh_per_MMBtu')))
    out = {key:_sum(values) for key,values in rows.items()}
    for source in ('GT','boiler'):
        out[source+'_gas_fee_USD2001'] = _calculate(lambda q,p:q*p,out[source+'_gas_MMBtu'],params.get('gas_price_usd_per_mmbtu'))
    for kind,key in (('electric','electrical_work_kWh'),('shaft','mechanical_work_kWh')):
        out['DG_'+kind+'_fee_USD2001'] = _calculate(lambda w,p:w*p,diesel[key],params.get('dg_shaft_fee_USD_per_kWh'))
    out['DG_shaft_difference_USD2001'] = _calculate(lambda m,e:m-e,out['DG_shaft_fee_USD2001'],out['DG_electric_fee_USD2001'])
    base = _sum(out[k] for k in ('grid_fee_USD2001','GT_gas_fee_USD2001','boiler_gas_fee_USD2001','DG_electric_fee_USD2001'))
    out['source_electric_basis_fee_USD2001'] = base
    out['observed_operating_fee_USD2001'] = _calculate(lambda b,d:b+d,base,out['DG_shaft_difference_USD2001'])
    inventory = payload.get('m21_initial_storage') or {}
    initial = {}
    for name in ('ups','cold_store'):
        value = inventory.get(name)
        initial[name] = value.get('initial_charged_input_kWh') if isinstance(value,dict) else value
    cold = _calculate(lambda q,ec,rt,pump:q*(ec/rt+pump),initial['cold_store'],
        params.get('ec_electric_kW_per_rt'),params.get('RT_TO_KW'),params.get('cold_pump_kW_per_kW'))
    prep = _sum([initial['ups'],cold])
    if (payload.get('continuation_start_minute') or 0.) > TIME_TOL:
        prep, fee, reason = 0.,0.,'continuation_not_recharged'
    else:
        fee = _calculate(lambda p,t:p*t,prep,first_tariff) if durations else None
        reason = 'conditional_original_preparation_once_at_first_observed_tariff'
    out.update(initial_preparation_grid_kWh_e=prep,initial_preparation_fee_USD2001=fee,initial_preparation_reason=reason,
        actual_fee_USD2001=_sum([out['observed_operating_fee_USD2001'],fee]),
        price_year=2001,currency='USD',producer_economics_used=False,cost_USD_step_used=False,
        actual_recharge_is_already_in_grid_purchase=True,unobserved_tail_fee_USD2001=None)
    observation,_ = _observation(payload)
    old = result.get('metrics',{}).get('economics',{})
    old_end = result.get('observed_until_minute')
    source_window = old.get('source_economics',{}).get('window_minutes')
    if old_end is None and isinstance(source_window,list) and source_window: old_end = source_window[-1]
    matches = _number(old_end) and abs(old_end-observation['observed_until_minute']) <= TIME_TOL
    old_fee = old.get('actual_fee_USD2001') if matches else None
    out['producer_observed_fee_USD2001_for_comparison'] = old_fee
    out['independent_minus_producer_USD2001'] = _calculate(lambda a,b:a-b,out['actual_fee_USD2001'],old_fee)
    return out


def audit_payload(payload, result=None, *, parameters=None, tariffs_by_hour=None):
    """Return detached independent arithmetic; explicit parameters keep units auditable."""
    result = result or {}
    if not isinstance(payload.get('terminal_snapshot'),dict) and isinstance(result.get('terminal_snapshot'),dict):
        payload = dict(payload,terminal_snapshot=result['terminal_snapshot'])
    params = dict(parameters or {})
    observation, durations = _observation(payload)
    segments = payload.get('segments',[])
    service, _ = _services(segments,durations)
    balances, rows = _balances(segments,durations,params)
    diesel = _diesel(segments,durations)
    out = dict(schema=SCHEMA,observation=observation,carrier_service=service,balances=balances,
        diesel=diesel,storage=_storage(payload,durations),
        economics=_economics(payload,result,params,tariffs_by_hour or {},durations,diesel),
        segment_balance_rows=rows,accounting_parameters=params,
        provenance=dict(producer_git_commit=result.get('git_commit'),producer_source_sha256=result.get('source_sha256'),
            producer_raw_sha256=result.get('raw_sha256'),evidence_kind='read_only_recomputation_of_saved_actual_segments',
            simulation_rerun=False,production_evaluation_imported=False),
        execution_status=dict(producer_status=result.get('status'),payload_status=payload.get('summary',{}).get('status'),
            producer_error=copy.deepcopy(result.get('error')),payload_error=copy.deepcopy(payload.get('error')),
            solver_errors=copy.deepcopy(payload.get('solver_errors',[]))),
        identity_checks=dict(producer_raw_sha256='unknown',frozen_accounting_inputs='unknown'),
        qualification='Arithmetic consistency only; no physical, clinical, OEM, or annual-operation qualification.',
        thresholds=dict(power_kW=POWER_TOL,steam_lbph=STEAM_TOL,work_kWh=WORK_TOL,time_minutes=TIME_TOL,fee_USD2001=FEE_TOL))
    return _audit_status(out)


def _audit_status(out):
    """A check cannot pass because its evidence is missing or a tail is unseen."""
    checks = dict()
    checks['observation.complete'] = 'pass' if out['observation']['complete'] else 'unknown'
    for carrier in ('e','c'): checks['balance.'+carrier] = out['balances'][carrier]['check']
    checks['steam.full_closure'] = out['balances']['steam']['full_closure_check']
    checks['steam.source_sufficiency'] = out['balances']['steam']['source_sufficiency_check']
    for priority,carriers in out['carrier_service'].items():
        for carrier,row in carriers.items(): checks['request_identity.'+priority+'.'+carrier] = row['request_identity']['check']
    for name,row in out['storage'].items():
        checks['storage.'+name+'.balance'] = row['check']
        checks['storage.'+name+'.dynamics'] = row['full_dynamics_check']
        value = row['terminal_minus_receipt_kWh']
        checks['storage.'+name+'.terminal'] = 'unknown' if not _number(value) else 'pass' if abs(value)<=WORK_TOL else 'fail'
    for name in ('energy_balance','electrical_action_match','shaft_budget_match','shaft_budget_chain'):
        checks['diesel.'+name] = out['diesel'][name]['check']
    difference = out['economics']['independent_minus_producer_USD2001']
    checks['economics.producer_fee_match'] = 'unknown' if not _number(difference) else 'pass' if abs(difference)<=FEE_TOL else 'fail'
    checks['economics.raw_fee_complete'] = 'pass' if _number(out['economics']['actual_fee_USD2001']) else 'unknown'
    for key,value in out['identity_checks'].items(): checks['identity.'+key] = value
    status = out['execution_status']
    producer_status = status['producer_status']
    checks['execution.producer_status'] = 'pass' if producer_status=='completed' else 'fail' if producer_status in ('failed','error') else 'unknown'
    if status['solver_errors']: checks['execution.solver_error_resolution'] = 'unknown'
    out['checks_passed'] = [k for k,v in checks.items() if v=='pass']
    out['failed_checks'] = [k for k,v in checks.items() if v=='fail']
    out['unknown_checks'] = [k for k,v in checks.items() if v=='unknown']
    out['audit_status'] = 'failed' if out['failed_checks'] else 'unknown' if out['unknown_checks'] else 'passed'
    out['audit_status_semantics'] = 'failed is a definite observed inconsistency; unknown retains missing evidence or unobserved intervals; passed is arithmetic only'
    return out


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()


def _frozen_parameters(repo, bindings, payload):
    paths = ('configs/plant_configs.json','inputs/loads/hospital_ies_demand_8760.csv')
    receipts = {}
    for name in paths:
        actual = _sha(repo/name)
        expected = bindings.get('source',{}).get(name) or bindings.get('inputs',{}).get(name)
        if actual != expected: raise ValueError('Frozen accounting source not bound or SHA mismatch: '+name)
        receipts[name] = actual
    config = json.loads((repo/paths[0]).read_text(encoding='utf8'))
    assumptions = config['engineering_assumptions_not_from_source']
    performance = config['historical_pnnl_performance']
    prices = config['historical_2001_prices_USD']
    params = dict(RT_TO_KW=3.51685284,kWh_per_MMBtu=293.0710702,dg_shaft_fee_USD_per_kWh=.30,
        steam_kWh_per_lb=assumptions['kWh_thermal_per_lb_steam'],
        exchanger_eff=assumptions['steam_to_building_heat_exchanger_efficiency'],
        abs_steam_lb_per_rt=performance['existing_absorption_steam_lb_per_RT_hr'],
        abs_aux_kW_per_rt=performance['existing_absorption_aux_kW_per_RT'],
        ec_electric_kW_per_rt=performance['existing_electric_chiller_all_in_kW_per_RT'],
        boiler_eff=performance['boiler_thermal_efficiency'],gas_price_usd_per_mmbtu=prices['gas_per_MMBtu'],
        cold_pump_kW_per_kW=payload.get('model_config',{}).get('sci_engineering',{}).get('cold_pump_kW_per_kW'))
    wanted = {s.get('hour_of_year') for s in payload.get('segments',[])}
    tariffs = {}
    with (repo/paths[1]).open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            hour = int(row['hour_of_year'])
            if hour not in wanted: continue
            stamp = datetime.fromisoformat(row['timestamp'])
            band = 'offpeak' if stamp.weekday() >= 5 or stamp.hour < 8 or stamp.hour >= 22 else 'onpeak' if 12 <= stamp.hour < 18 else 'midpeak'
            tariffs[hour] = prices['electricity_'+band+'_per_kWh']
    if wanted != set(tariffs): raise ValueError('Recorded hour not found in frozen hourly input')
    return params,tariffs,receipts


def audit_run(run, *, repo=None):
    """Read a producer directory and hash-check exact frozen accounting sources."""
    run = Path(run)
    repo = Path(repo) if repo is not None else Path(__file__).resolve().parents[2]
    names = ('trajectory.json.gz','RESULT.json','BINDINGS.json')
    before = {name:_sha(run/name) for name in names if (run/name).is_file()}
    if names[0] not in before: raise FileNotFoundError('Actual trajectory is required: '+str(run/names[0]))
    with gzip.open(run/names[0],'rt',encoding='utf8') as stream: payload = json.load(stream)
    result = json.loads((run/names[1]).read_text(encoding='utf8')) if names[1] in before else {}
    bindings = json.loads((run/names[2]).read_text(encoding='utf8')) if names[2] in before else None
    if result.get('raw_sha256') is not None and result['raw_sha256'] != before[names[0]]:
        raise ValueError('Producer RESULT raw SHA does not match actual saved trajectory')
    params,tariffs,sources = _frozen_parameters(repo,bindings,payload) if bindings is not None else ({},{},{})
    out = audit_payload(payload,result,parameters=params,tariffs_by_hour=tariffs)
    after = {name:_sha(run/name) for name in names if (run/name).is_file()}
    if after != before: raise RuntimeError('Producer files changed during independent read-only audit')
    out['provenance'].update(run_path=str(run.resolve()),producer_files_sha256=before,
        missing_producer_files=[name for name in names if name not in before],
        producer_files_unchanged=True,frozen_accounting_inputs_sha256=sources,auditor_sha256=_sha(__file__),
        created_utc=datetime.now(timezone.utc).isoformat())
    out['identity_checks'] = dict(producer_raw_sha256='pass' if result.get('raw_sha256') else 'unknown',
        frozen_accounting_inputs='pass' if bindings is not None else 'unknown',producer_files_unchanged='pass')
    return _audit_status(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True,type=Path)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output',required=True,type=Path,help='Fresh audit directory; producer evidence stays read-only')
    args = parser.parse_args(argv)
    if args.output.exists(): raise ValueError('Independent audit output must be a fresh directory')
    out = audit_run(args.run,repo=args.repo)
    args.output.mkdir(parents=True)
    (args.output/'LEDGER.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
    print(json.dumps(dict(output=str(args.output),actual_fee_USD2001=out['economics']['actual_fee_USD2001'],
        independent_minus_producer_USD2001=out['economics']['independent_minus_producer_USD2001'],
        electric_check=out['balances']['e']['check'],cooling_check=out['balances']['c']['check'],
        steam_full_closure_check=out['balances']['steam']['full_closure_check'],audit_status=out['audit_status'],
        failed_checks=out['failed_checks'],unknown_checks=out['unknown_checks'],simulation_rerun=False)))
    return 2 if out['audit_status']=='failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
