"""Read-only V30 export of native actual-service, resource and terminal ledgers.

No environment, controller or solver is constructed. Historical metrics remain
producer evidence; this module creates a separately identified offline export.
The raw-price audit deliberately does not call the producer's economic_window,
read its cost_USD_step, or use its economics aggregates to calculate a fee.
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

SCHEMA = 'hospital_ies.model_review_v30.actual_evaluation.v1'
PRIORITIES = ('P0', 'P1', 'P2', 'P3')
CARRIERS = ('e', 'h', 'c')


def _number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def _sum_known(values):
    values = list(values)
    return sum(values) if values and all(_number(v) for v in values) else None


def _measure(value, carrier, source):
    return dict(value=value, carrier=carrier, unit='kWh_'+carrier, source=source,
                status='observed_or_derived' if value is not None else 'unknown')


def _observation(payload):
    segments = payload.get('segments', [])
    origin = float(payload.get('continuation_start_minute') or 0.)
    observed = origin
    contiguous = bool(segments)
    for segment in segments:
        a, b = segment.get('start_minute'), segment.get('end_minute')
        if not all(_number(v) for v in (a, b)) or b <= a or a < observed-1e-8:
            raise ValueError('Invalid or overlapping actual segment times')
        contiguous = contiguous and abs(a-observed) <= 1e-8
        observed = float(b)
    requested = payload.get('summary', {}).get('requested_end_minute')
    if not _number(requested):
        scene = payload.get('scenario', {})
        requested = scene.get('duration_steps', 0)*scene.get('step_minutes', 15.) or None
    complete = bool(contiguous and _number(requested) and abs(observed-requested) <= 1e-7)
    return dict(observed_from_minute=origin, observed_until_minute=observed,
        observed_duration_minutes=sum(s['end_minute']-s['start_minute'] for s in segments),
        requested_end_minute=requested, complete=complete, contiguous=contiguous,
        unknown_tail_minutes=max(0., requested-observed) if _number(requested) else None,
        tail_status='fully_observed' if complete else 'unknown_tail', segment_count=len(segments),
        quantities_are_observed_prefix_only=True)


def _preparation(payload, parameters, total_recorded_grid=None):
    inventory = payload.get('m21_initial_storage') or {}
    inputs = {}
    for name in ('ups', 'cold_store'):
        value = inventory.get(name)
        inputs[name] = value.get('initial_charged_input_kWh') if isinstance(value, dict) else value
        if not _number(inputs[name]): inputs[name] = None
    grid = {'ups': inputs['ups'], 'cold_store': None}
    required = ('ec_electric_kW_per_rt', 'RT_TO_KW', 'cold_pump_kW_per_kW')
    if all(_number(parameters.get(k)) for k in required) and inputs['cold_store'] is not None:
        grid['cold_store'] = inputs['cold_store']*(parameters['ec_electric_kW_per_rt']/parameters['RT_TO_KW']+
            parameters['cold_pump_kW_per_kW'])
    elif inputs['cold_store'] == 0.:
        grid['cold_store'] = 0.
    elif _number(total_recorded_grid) and _number(grid['ups']):
        # This allocation is producer evidence, not the independent audit.
        grid['cold_store'] = total_recorded_grid-grid['ups']
    return inputs, grid


def independent_energy_audit(payload, *, accounting_parameters=None, tariffs_by_hour=None):
    """Integrate raw *whole* segments with explicit frozen prices/parameters.

    DG work is read from actual integrated shaft receipts, never interpolated
    from electrical output. Missing receipts/parameters remain unknown. Caller
    supplies accounting_parameters and optionally integer hour -> tariff.
    """
    parameters = dict(accounting_parameters or payload.get('accounting_parameters') or {})
    tariffs = tariffs_by_hour if tariffs_by_hour is not None else payload.get('audit_tariffs_by_hour', {})
    observations = _observation(payload)
    series = {k: [] for k in ('grid_purchase_kWh', 'grid_fee_USD2001', 'GT_gas_MMBtu',
        'boiler_gas_MMBtu', 'DG_shaft_work_kWh', 'DG_electric_work_kWh')}
    for segment in payload.get('segments', []):
        dt = (segment['end_minute']-segment['start_minute'])/60.
        action = segment.get('action', {})
        tariff = segment.get('grid_tariff_USD_kWh', action.get('grid_tariff_USD_kWh'))
        if not _number(tariff):
            hour = segment.get('hour_of_year')
            tariff = tariffs.get(hour, tariffs.get(str(hour)))
        imported = action.get('grid_import_kW')
        series['grid_purchase_kWh'].append(imported*dt if _number(imported) else None)
        series['grid_fee_USD2001'].append(imported*dt*tariff if all(_number(v) for v in (imported, tariff)) else None)
        gas = action.get('gt_fuel_MMBtu_h')
        series['GT_gas_MMBtu'].append(gas*dt if _number(gas) else None)
        boiler = action.get('boiler_steam_lbph')
        keys = ('steam_kWh_per_lb', 'boiler_eff', 'kWh_per_MMBtu')
        series['boiler_gas_MMBtu'].append(boiler*dt*parameters['steam_kWh_per_lb']/
            parameters['boiler_eff']/parameters['kWh_per_MMBtu']
            if _number(boiler) and all(_number(parameters.get(k)) for k in keys) else None)
        receipt = segment.get('diesel_local_response') or {}
        receipt = receipt.get('energy_receipt', receipt)
        for key, field in (('DG_shaft_work_kWh', 'mechanical_work_kWh'),
                           ('DG_electric_work_kWh', 'electrical_work_kWh')):
            value = receipt.get(field)
            series[key].append(value if _number(value) else None)
    result = {key: _sum_known(values) for key, values in series.items()}
    gas_price = parameters.get('gas_price_usd_per_mmbtu')
    shaft_price = parameters.get('dg_shaft_fee_USD_per_kWh')
    for key in ('GT', 'boiler'):
        work = result[key+'_gas_MMBtu']
        result[key+'_gas_fee_USD2001'] = work*gas_price if all(_number(v) for v in (work, gas_price)) else None
    work = result['DG_shaft_work_kWh']
    result['DG_shaft_fee_USD2001'] = work*shaft_price if all(_number(v) for v in (work, shaft_price)) else None
    result['observed_operating_fee_USD2001'] = _sum_known(result[k] for k in
        ('grid_fee_USD2001', 'GT_gas_fee_USD2001', 'boiler_gas_fee_USD2001', 'DG_shaft_fee_USD2001'))
    _, grid = _preparation(payload, parameters)
    result['preparation_grid_purchase_kWh_e'] = _sum_known(grid.values())
    first = payload.get('segments', [{}])[0] if payload.get('segments') else {}
    initial_tariff = first.get('grid_tariff_USD_kWh', first.get('action', {}).get('grid_tariff_USD_kWh'))
    if not _number(initial_tariff):
        hour = first.get('hour_of_year'); initial_tariff = tariffs.get(hour, tariffs.get(str(hour)))
    initial_input = result['preparation_grid_purchase_kWh_e']
    result['initial_preparation_fee_USD2001'] = initial_input*initial_tariff if all(
        _number(v) for v in (initial_input, initial_tariff)) and payload.get('segments') else None
    result['actual_fee_USD2001'] = _sum_known([result['observed_operating_fee_USD2001'],
        result['initial_preparation_fee_USD2001']])
    result['unknown_fields'] = [k for k, value in result.items() if value is None]
    result.update(schema='hospital_ies.v30.independent_raw_energy_audit.v1', observation=observations,
        accounting_objective_cost_used=False, producer_economic_aggregation_used=False,
        calculation='sum(grid_kW*hours*tariff + GT_MMBtu_h*hours*gas_price + boiler_lb_h*hours*steam_kWh_lb/eff/kWh_MMBtu*gas_price + actual_DG_shaft_kWh*shaft_price) + initial_preparation_grid_kWh*first_tariff',
        DG_shaft_budget_is_chemical_fuel=False, diesel_liters=None, diesel_chemical_kWh=None,
        unobserved_tail_fee_USD2001=None, accounting_parameters=parameters)
    return result


def _economics(payload, result, observation, audit):
    old = result.get('metrics', {}).get('economics', {})
    # Refuse a full-result aggregate when caller supplies a shorter raw prefix.
    saved_end = result.get('observed_until_minute')
    source_window = old.get('source_economics', {}).get('window_minutes')
    if saved_end is None and isinstance(source_window, list): saved_end = source_window[-1]
    matches = bool(observation['segment_count'] and _number(saved_end) and
                   abs(saved_end-observation['observed_until_minute']) <= 1e-7)
    source_fee = old.get('source_economics', {}).get('actual_energy_fee_USD') if matches else None
    correction = old.get('DG_fee_correction_USD') if matches else None
    prep = old.get('initial_inventory_fee_USD_conditional') if matches else None
    actual = _sum_known([source_fee, correction, prep])
    basis = 'producer_source_actual_fee_plus_DG_difference_plus_initial_preparation_once'
    if actual is None and audit['actual_fee_USD2001'] is not None:
        actual = audit['actual_fee_USD2001']; basis = 'independent_raw_observed_prefix_recomputation'
    return dict(actual_fee_USD2001=actual, source_actual_energy_fee_USD2001=source_fee,
        DG_fee_correction_USD=correction, initial_preparation_fee_USD2001=prep,
        legacy_energy_cost_USD=old.get('energy_cost_USD'), basis=basis,
        producer_aggregate_matches_observed_prefix=matches, price_year=2001, currency='USD',
        observed_until_minute=observation['observed_until_minute'],
        complete_horizon_fee=observation['complete'], unobserved_tail_fee_USD2001=None,
        recharge_fee_added_again=False, DG_difference_added_once=True,
        independent_raw_fee_USD2001=audit['actual_fee_USD2001'],
        independent_minus_export_USD2001=(audit['actual_fee_USD2001']-actual
            if all(_number(v) for v in (audit['actual_fee_USD2001'], actual)) else None))


def _storage(payload, result):
    economics = result.get('metrics', {}).get('economics', {})
    parameters = dict(payload.get('accounting_parameters') or {})
    inputs, grid = _preparation(payload, parameters,
        economics.get('initial_inventory_grid_input_kWh_conditional'))
    stores = {}
    for name, carrier in (('ups', 'e'), ('cold_store', 'c')):
        receipts = [s.get('m21_storage', {}).get(name) for s in payload.get('segments', [])]
        available = bool(receipts) and all(isinstance(r, dict) for r in receipts)
        first, last = (receipts[0], receipts[-1]) if available else ({}, {})
        row = dict(initial_stock=_measure(first.get('initial_kWh'), carrier, 'first actual m21_storage receipt.initial_kWh'),
            preparation_input=_measure(inputs[name], carrier, 'payload.m21_initial_storage initial_charged_input; not stock'),
            preparation_grid_purchase=_measure(grid[name], 'e',
                'frozen EC electric conversion plus cold-pump ratio' if parameters else 'producer conditional initial grid-purchase allocation'),
            final_stock=_measure(last.get('final_kWh'), carrier, 'last actual m21_storage receipt.final_kWh'))
        for label, field in (('actual_charge_input', 'charged_input_kWh'),
            ('actual_discharge', 'discharged_kWh'), ('standing_loss', 'standing_loss_kWh'),
            ('conversion_loss', 'conversion_loss_kWh'), ('recorded_balance_residual_sum', 'balance_residual_kWh')):
            value = _sum_known(r.get(field) for r in receipts) if available else None
            row[label] = _measure(value, carrier, 'sum of actual m21_storage receipts.'+field)
        terms = [row[k]['value'] for k in ('initial_stock', 'actual_charge_input', 'actual_discharge',
            'standing_loss', 'conversion_loss', 'final_stock')]
        residual = terms[0]+terms[1]-sum(terms[2:]) if all(_number(v) for v in terms) else None
        row['balance_residual'] = _measure(residual, carrier,
            'independent initial_stock + charge_input - useful_discharge - standing_loss - conversion_loss - final_stock')
        row['receipt_chain_max_gap_kWh'] = max((abs(a['final_kWh']-b['initial_kWh'])
            for a, b in zip(receipts, receipts[1:])), default=0.) if available else None
        stores[name] = row
    return stores


def _services(payload, result, observation):
    from tools.model_correctness_v23.metrics import build_reliability_metrics
    # Native existing definitions are evaluated on exactly this observed prefix.
    reliability = build_reliability_metrics(payload, references=result.get('recovery_references'))
    service = copy.deepcopy(reliability['carrier_service'])
    for ip, priority in enumerate(PRIORITIES):
        for ic, carrier in enumerate(CARRIERS):
            row = service[priority][carrier]
            values = {key: [] for key in ('requested_kWh', 'delivered_kWh', 'active_cut_kWh',
                'connected_unserved_kWh', 'ENS_kWh')}
            for segment in payload.get('segments', []):
                ledger = segment['service_request_ledger']; dt = (segment['end_minute']-segment['start_minute'])/60.
                request = ledger['original_demand_kW'][ip][ic]; delivered = ledger['delivered_kW'][ip][ic]
                for key, value in (('requested_kWh', request), ('delivered_kWh', delivered),
                    ('active_cut_kWh', ledger['active_cut_kW'][ip][ic]),
                    ('connected_unserved_kWh', ledger['connected_unserved_kW'][ip][ic]),
                    ('ENS_kWh', max(request-delivered, 0.))):
                    values[key].append(value*dt)
            producer_ens = row['ENS_kWh']
            row.update({k: _sum_known(v) for k, v in values.items()})
            row.update(carrier=carrier, unit='kWh_'+carrier, integration_source='actual service_request_ledger; request minus delivered',
                reliability_ENS_kWh=producer_ens, independent_minus_reliability_ENS_kWh=(row['ENS_kWh']-producer_ens
                    if all(_number(v) for v in (row['ENS_kWh'], producer_ens)) else None),
                actual_recovery_minute=row.get('recovery_absolute_minute'),
                confirmed_recovery_minute=row.get('recovery_confirmation_minute'),
                unknown_after_minute=None if observation['complete'] else observation['observed_until_minute'])
            if not payload.get('segments'):
                row['final_status'] = 'unknown_no_observed_service'
            else:
                ledger = payload['segments'][-1]['service_request_ledger']
                request = ledger['original_demand_kW'][ip][ic]
                shortage = max(request-ledger['delivered_kW'][ip][ic], 0.)
                material = shortage > .001 and (shortage >= .01 or (request > .001 and shortage/request >= .001))
                row['final_status'] = 'unserved' if material else 'served_with_submaterial_deficit' if shortage else 'served'
                if not observation['complete']: row['final_status'] += '_at_observation_end_unknown_tail'
    return service, reliability


def _electric_components(payload):
    parameters = payload.get('accounting_parameters') or {}
    rows = {p: {kind: {'requested_kWh_e': [], 'delivered_kWh_e': [], 'ENS_kWh_e': []}
        for kind in ('direct', 'auxiliary')} for p in PRIORITIES}
    plant_fields = ('gt_start_electric_kW', 'boiler_restart_electric_kW', 'station_auxiliary_kW',
        'cold_storage_pump_kW', 'boiler_auxiliary_kW', 'recovery_pump_electric_kW', 'recovery_fan_electric_kW')
    plant = {field.replace('_kW', '_kWh_e'): [] for field in plant_fields}
    plant.update(electric_chiller_input_kWh_e=[], absorption_auxiliary_kWh_e=[])
    for segment in payload.get('segments', []):
        dt = (segment['end_minute']-segment['start_minute'])/60.
        components = segment.get('m28_demand_components', {})
        original = components.get('original_request', {})
        for ip, priority in enumerate(PRIORITIES):
            direct_req = original.get('direct_request_kW', [None]*4)[ip]
            aux_req = original.get('auxiliary_request_kW', [None]*4)[ip]
            aux_req = _sum_known(aux_req) if isinstance(aux_req, list) else None
            direct_served = components.get('direct_delivered_kW', [None]*4)[ip]
            aux_served = components.get('auxiliary_delivered_kW', [None]*4)[ip]
            aux_served = _sum_known(aux_served) if isinstance(aux_served, list) else None
            for kind, req, served in (('direct', direct_req, direct_served), ('auxiliary', aux_req, aux_served)):
                row = rows[priority][kind]
                row['requested_kWh_e'].append(req*dt if _number(req) else None)
                row['delivered_kWh_e'].append(served*dt if _number(served) else None)
                row['ENS_kWh_e'].append(max(req-served, 0.)*dt if all(_number(v) for v in (req, served)) else None)
        for field in plant_fields:
            value = segment.get('action', {}).get(field)
            plant[field.replace('_kW', '_kWh_e')].append(value*dt if _number(value) else None)
        action = segment.get('action', {})
        ec = action.get('electric_chiller_cooling_kW')
        ec = _sum_known(ec) if isinstance(ec, list) else None
        absorption = action.get('absorption_cooling_kW')
        for field, output, coefficient in (('electric_chiller_input_kWh_e', ec, 'ec_electric_kW_per_rt'),
                ('absorption_auxiliary_kWh_e', absorption, 'abs_aux_kW_per_rt')):
            ratio = parameters.get(coefficient); rt = parameters.get('RT_TO_KW')
            plant[field].append(output*ratio/rt*dt if all(_number(v) for v in (output, ratio, rt)) else None)
    return dict(priority={p: {kind: {key: _sum_known(values) for key, values in row.items()}
        for kind, row in kinds.items()} for p, kinds in rows.items()},
        plant_conversion_and_startup={key: _sum_known(values) for key, values in plant.items()},
        source='m28_demand_components original_request and actual delivered; separate actual action plant fields',
        plant_is_priority_direct_demand=False)


def _terminal(payload, observation):
    if isinstance(payload.get('terminal_snapshot'), dict):
        return copy.deepcopy(payload['terminal_snapshot'])
    segments = payload.get('segments', [])
    last = segments[-1] if segments else {}
    post = last.get('post_state', {})
    finish = last.get('end_minute')
    events = [e for e in payload.get('events', []) if _number(e.get('time_minute')) and
              finish is not None and e['time_minute'] <= finish]
    gt = {}
    outputs = post.get('gt_prev')
    for index in range(3):
        device = 'GT_'+str(index+1)
        seen = [e for e in events if e.get('device') == device]
        completed = [e['time_minute'] for e in seen if e.get('kind') == 'gt_start_completed']
        requested = [e['time_minute'] for e in seen if e.get('kind') in ('gt_start_requested', 'gt_start_process_begun')]
        output = outputs[index] if isinstance(outputs, list) and len(outputs) > index else None
        pending = bool(requested and (not completed or max(requested) > max(completed)))
        gt[device] = dict(actual_output_kW_e=output,
            actual_state='online' if _number(output) and output > 0. else 'unknown_pending_start' if pending
                else 'not_producing' if _number(output) else 'unknown',
            last_actual_start_completed_minute=max(completed) if completed else None,
            incomplete_start_progress=None, remaining_start_requirement=None,
            pending_start_evidence=copy.deepcopy(seen[-1]) if pending and seen else None)
    inventory = {name: copy.deepcopy(last.get('m21_storage', {}).get(name, {}).get('final_kWh'))
        for name in ('ups', 'cold_store')}
    contacts = [copy.deepcopy(e) for e in events if e.get('kind') in
        ('grid_bus_state_changed', 'load_connection_restored', 'local_down_completed')]
    return dict(time_minute=finish, source='last_actual_segment.post_state + actual inventory receipts + occurred events',
        completeness='partial_historical_reconstruction', GT=gt,
        DG=dict(actual_state=post.get('diesel_status'), actual_output_kW_e=post.get('last_diesel_kW'),
            remaining_shaft_budget_kWh=post.get('diesel_energy_remaining_kWh'),
            shaft_budget_is_chemical_fuel=False, local_state=copy.deepcopy(last.get('diesel_local_response', {}).get('state_after'))),
        other_device_preparation=copy.deepcopy(last.get('m22_station')),
        transfer_contact_state=copy.deepcopy(contacts[-1]) if contacts else None,
        pending_commands=None, recovery_queue=None, actual_inventory=inventory,
        post_state=copy.deepcopy(post) if post else None,
        unknown_fields=['pending_commands', 'recovery_queue', 'incomplete_start_progress', 'remaining_start_requirement'],
        actual_observation_complete=observation['complete'], last_decision_is_not_terminal=True)


def export_evaluation(payload, result):
    """Return a detached evaluation without mutating payload/result or solving."""
    observation = _observation(payload)
    audit = independent_energy_audit(payload)
    service, reliability = _services(payload, result, observation)
    return dict(schema=SCHEMA,
        provenance=dict(producer_git_commit=result.get('git_commit'), producer_source_sha256=result.get('source_sha256'),
            producer_input_sha256=result.get('input_sha256'), producer_raw_sha256=result.get('raw_sha256'),
            producer_case_id=result.get('case_id'), exporter=SCHEMA,
            evidence_kind=payload.get('evidence_kind', 'offline_export_of_recorded_trajectory'),
            simulation_rerun=False),
        observation=observation, economics=_economics(payload, result, observation, audit),
        storage=_storage(payload, result), carrier_service=service,
        critical_union=copy.deepcopy(reliability['critical_union']),
        priority_union=copy.deepcopy(reliability['priority_union']),
        service_definition=copy.deepcopy(reliability['definition']),
        electric_components=_electric_components(payload), terminal_snapshot=_terminal(payload, observation),
        execution_status=dict(producer_status=result.get('status'), payload_status=payload.get('summary', {}).get('status'),
            computation_completed=bool(payload.get('summary', {}).get('completed')),
            solver_errors=copy.deepcopy(payload.get('solver_errors', [])), error=copy.deepcopy(payload.get('error')),
            legacy_physical_service_failure=payload.get('summary', {}).get('physical_service_failure'),
            legacy_p0_failed_any=payload.get('summary', {}).get('p0_failed_any'),
            actual_service_material_interruption=(any(row['interruption_count'] for p in service.values()
                for row in p.values() if row['interruption_count'] is not None)
                if reliability['support_complete'] else None)), independent_audit=audit)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def _load_frozen_accounting(repo, bindings, segments):
    """Read source-bound frozen parameters and only the saved hours' tariffs."""
    names = ('configs/plant_configs.json', 'inputs/loads/hospital_ies_demand_8760.csv')
    receipts = {}
    for name in names:
        expected = bindings.get('source', {}).get(name) or bindings.get('inputs', {}).get(name)
        actual = _sha(repo/name)
        if expected is None or actual != expected:
            raise ValueError('Frozen accounting input not bound or SHA mismatch: '+name)
        receipts[name] = actual
    config = json.loads((repo/names[0]).read_text(encoding='utf-8'))
    assumptions = config['engineering_assumptions_not_from_source']
    performance = config['historical_pnnl_performance']; prices = config['historical_2001_prices_USD']
    parameters = dict(steam_kWh_per_lb=assumptions['kWh_thermal_per_lb_steam'],
        boiler_eff=performance['boiler_thermal_efficiency'], gas_price_usd_per_mmbtu=prices['gas_per_MMBtu'],
        ec_electric_kW_per_rt=performance['existing_electric_chiller_all_in_kW_per_RT'],
        abs_aux_kW_per_rt=performance.get('existing_absorption_aux_kW_per_RT'),
        # Native exact conversion constants, units rather than fitted parameters.
        kWh_per_MMBtu=293.0710702, RT_TO_KW=3.51685284, dg_shaft_fee_USD_per_kWh=.30)
    wanted = {s.get('hour_of_year') for s in segments}; tariffs = {}
    with (repo/names[1]).open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            hour = int(row['hour_of_year'])
            if hour not in wanted: continue
            stamp = datetime.fromisoformat(row['timestamp'])
            # Independent arithmetic for the frozen illustrative TOU convention.
            band = 'offpeak' if stamp.weekday() >= 5 or stamp.hour < 8 or stamp.hour >= 22 else (
                'onpeak' if 12 <= stamp.hour < 18 else 'midpeak')
            tariffs[hour] = prices['electricity_'+band+'_per_kWh']
    if set(tariffs) != wanted: raise ValueError('Missing exact native hour tariff')
    return parameters, tariffs, receipts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='Fresh offline export directory')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    if args.output.exists(): raise ValueError('Output must be a fresh directory')
    paths = {name: args.run/name for name in ('trajectory.json.gz', 'RESULT.json', 'BINDINGS.json')}
    before = {name: _sha(path) for name, path in paths.items()}
    with gzip.open(paths['trajectory.json.gz'], 'rt', encoding='utf-8') as stream: payload = json.load(stream)
    result = json.loads(paths['RESULT.json'].read_text(encoding='utf-8'))
    bindings = json.loads(paths['BINDINGS.json'].read_text(encoding='utf-8'))
    parameters, tariffs, sources = _load_frozen_accounting(args.repo, bindings, payload.get('segments', []))
    parameters['cold_pump_kW_per_kW'] = payload.get('model_config', {}).get('sci_engineering', {}).get('cold_pump_kW_per_kW')
    payload['accounting_parameters'] = parameters; payload['audit_tariffs_by_hour'] = tariffs
    evaluation = export_evaluation(payload, result)
    after = {name: _sha(path) for name, path in paths.items()}
    if after != before: raise RuntimeError('Producer evidence changed during read-only export')
    receipt = dict(schema=SCHEMA, created_utc=datetime.now(timezone.utc).isoformat(), run_path=str(args.run.resolve()),
        output_path=str(args.output.resolve()), producer_files_sha256=before, producer_files_unchanged=True,
        exporter_sha256=_sha(__file__), frozen_accounting_inputs_sha256=sources,
        producer=result.get('git_commit'), export_only_no_simulation=True,
        independent_actual_fee_minus_export_USD2001=evaluation['economics']['independent_minus_export_USD2001'])
    args.output.mkdir(parents=True)
    for name, value in (('EVALUATION.json', evaluation), ('INDEPENDENT_AUDIT.json', evaluation['independent_audit']),
                        ('EXPORT_RECEIPT.json', receipt)):
        (args.output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(args.output), actual_fee_USD2001=evaluation['economics']['actual_fee_USD2001'],
        independent_fee_difference_USD2001=receipt['independent_actual_fee_minus_export_USD2001'],
        producer_files_unchanged=True)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
