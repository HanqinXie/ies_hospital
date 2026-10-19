"""Read saved pilot raw once: dispatch binding, joint references and real ports.

No simulation, optimizer, policy selection or input mutation occurs here.
The original electricity/cooling balance threshold is 1e-4 kW; steam waste is
not serialized by the core, so its inferred nonnegative amount is reported
explicitly instead of claiming an independent exact steam equality check.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from hospital_ies.model import RT_TO_KW, load_plant, gt_level_properties


def read(path):
    path = Path(path)
    data = gzip.decompress(path.read_bytes()).decode('utf-8') if path.suffix == '.gz' else path.read_text(encoding='utf-8-sig')
    return json.loads(data)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def array(value, shape, label):
    data = np.asarray(value, dtype=float)
    if data.shape != shape or not np.isfinite(data).all():
        raise ValueError('Malformed saved '+label)
    return data


def inspect_payload(payload, spec, model_id, plant):
    decisions = payload.get('decisions', [])
    segments = payload.get('segments', [])
    failures = []
    refs = []
    expected = 'engineering_joint' if spec['kind'] == 'engineering' else 'coordinated_joint'
    requested = Counter()
    for row in decisions:
        command = row.get('command_generated')
        if command is None:
            continue
        route = sorted(command['request'])
        requested.update(route)
        if route != [expected]:
            failures.append(dict(type='wrong_generated_request_route', minute=row['decision_time_min'], actual=route, expected=expected))
        diagnostic = command.get('joint_dispatch', {}).get('diagnostic', {})
        engineering = diagnostic.get('engineering')
        if spec['kind'] == 'engineering' and engineering is None:
            failures.append(dict(type='missing_new_planner_diagnostic', minute=row['decision_time_min']))
        if engineering is not None:
            ref = dict(minute=row['decision_time_min'], command_id=command['id'],
                arrival_minute=command['arrival_minute'], generation_minute=command['generated_minute'],
                GT_command_kW=command['dispatch']['gt_electric_kW'], actual_pre_GT_kW=[row['features'][f'pre_GT_{i}_kW'] for i in (1, 2, 3)],
                actual_running_mask=engineering['actual_GT_running_mask'],
                planned_GT_changes=engineering['GT_command_state_changes'],
                planned_unserved_kW=diagnostic['planned_unserved_kW'], command_disconnected_kW=command['disconnected'],
                endurance_active=engineering['endurance_preparation_active'],
                cost_optimum_USD_step=engineering['cost_optimum_USD_step'], final_cost_USD_step=engineering['final_cost_USD_step'],
                cost_equivalence_USD_per_step=engineering['cost_equivalence_USD_per_step'],
                minimum_time_safety_release=engineering['minimum_time_safety_release'],
                same_state_diagnostics=engineering['same_state_diagnostics'])
            if ref['final_cost_USD_step'] > ref['cost_optimum_USD_step']+ref['cost_equivalence_USD_per_step']+1e-7:
                failures.append(dict(type='cost_equivalence_band_exceeded', minute=ref['minute']))
            if ref['actual_running_mask'] != [x > 1e-9 for x in ref['actual_pre_GT_kW']]:
                failures.append(dict(type='actual_running_mask_disagrees_with_current_ports', minute=ref['minute']))
            refs.append(ref)
    if payload.get('execution_model_id') != model_id:
        failures.append(dict(type='model_id_mismatch', actual=payload.get('execution_model_id'), expected=model_id))
    checks = []
    max_e = max_c = max_hrsg = max_fuel = max_contact = 0.
    min_inferred_unused_steam = None
    held_positive_during_pending = []
    for segment in segments:
        loads = array(segment['loads_kW'], (4, 3), 'demand')
        loss = array(segment['unserved_kW'], (4, 3), 'unserved')
        disconnected = array(segment['disconnected_loads_kW'], (4, 3), 'disconnected')
        connected = array(segment['connected_loads_kW'], (4, 3), 'connected')
        served = loads-loss
        a = segment['action']
        gt = array(a['gt_electric_kW'], (3,), 'actual GT')
        ec = array(a['electric_chiller_cooling_kW'], (3,), 'actual EC')
        absorption = float(a['absorption_cooling_kW'])
        aux = absorption/RT_TO_KW*plant.abs_electric_kW_per_rt+float(ec.sum())/RT_TO_KW*plant.ec_electric_kW_per_rt
        e = float(gt.sum()+a['grid_import_kW']+a['diesel_kW']-aux-served[:, 0].sum())
        c = float(absorption+ec.sum()-served[:, 2].sum())
        steam = float(a['hrsg_steam_lbph']+a['boiler_steam_lbph']
            -absorption/RT_TO_KW*plant.abs_steam_lb_per_rt
            -served[:, 1].sum()/(plant.steam_kWh_per_lb*plant.exchanger_eff))
        min_inferred_unused_steam = steam if min_inferred_unused_steam is None else min(min_inferred_unused_steam, steam)
        contact = float(np.max(np.abs(loads-connected-disconnected)))
        max_contact = max(max_contact, contact)
        row = next((r for r in decisions if r.get('decision_time_min') <= segment['start_minute'] < r.get('decision_time_min')+15.), None)
        hrsg_error = fuel_error = None
        if row is not None:
            powers, steam_curve, fuel_curve = gt_level_properties(plant, float(row['features']['outdoor_temperature_C']))
            expected_hrsg = float(sum(gt[i]*segment['availability'][f'HRSG_{i+1}'] for i in range(3))*steam_curve[-1]/powers[-1])
            expected_fuel = float(gt.sum()*fuel_curve[-1]/powers[-1])
            hrsg_error = float(a['hrsg_steam_lbph']-expected_hrsg)
            fuel_error = float(a['gt_fuel_MMBtu_h']-expected_fuel)
            max_hrsg = max(max_hrsg, abs(hrsg_error)); max_fuel = max(max_fuel, abs(fuel_error))
        max_e = max(max_e, abs(e)); max_c = max(max_c, abs(c))
        if max(abs(e), abs(c), contact) > 1e-4 or steam < -1e-4 or np.min(loss-disconnected) < -1e-6:
            failures.append(dict(type='actual_source_service_balance_or_contact_inconsistency', start_minute=segment['start_minute'],
                electric_residual_kW=e, cooling_residual_kW=c, inferred_unused_steam_lbph=steam,
                demand_contact_identity_kW=contact, minimum_connected_unserved_kW=float(np.min(loss-disconnected))))
        checks.append(dict(start_minute=segment['start_minute'], end_minute=segment['end_minute'], command_id=segment.get('command_id'),
            GT_kW=gt.tolist(), grid_kW=a['grid_import_kW'], diesel_kW=a['diesel_kW'], boiler_lbph=a['boiler_steam_lbph'],
            absorption_cooling_kW=absorption, electric_chiller_cooling_kW=ec.tolist(), actual_auxiliary_kW=aux,
            raw_e_demand_kW=float(loads[:, 0].sum()), disconnected_e_kW=float(disconnected[:, 0].sum()),
            connected_e_kW=float(connected[:, 0].sum()), served_e_kW=float(served[:, 0].sum()),
            connected_unserved_e_kW=float((loss-disconnected)[:, 0].sum()),
            electric_residual_kW=e, cooling_residual_kW=c, inferred_unused_steam_lbph=steam,
            hrsg_source_error_lbph=hrsg_error, fuel_source_error_MMBtu_h=fuel_error,
            stored_residuals_kW=segment['energy_balance_residuals']))
    return dict(schema='hospital_ies.readonly_planner_pilot.v14.v1', policy_id=spec['policy_id'],
        model_id=model_id, status=payload['summary']['status'], observed_until_minute=payload['summary']['observed_until_minute'],
        expected_route=expected, generated_request_counts=dict(requested),
        initial_state_sha256=digest(payload.get('initial_state')),
        actual_scenario_sha256=digest(payload['scenario']),
        actual_seed=payload['seed'], solver_phases=dict(Counter(r['phase'] for r in payload['solver_calls'])),
        side_diagnostic_status_counts=dict(Counter(name+':'+value['status'] for ref in refs
            for name, value in ref['same_state_diagnostics'].items())),
        maximum_recomputed_electric_residual_kW=max_e, maximum_recomputed_cooling_residual_kW=max_c,
        maximum_hrsg_source_error_lbph=max_hrsg, maximum_GT_fuel_source_error_MMBtu_h=max_fuel,
        maximum_demand_contact_identity_kW=max_contact, minimum_inferred_unused_steam_lbph=min_inferred_unused_steam,
        steam_exact_independent_equality_verified=False,
        steam_limit='unused steam variable is not serialized; nonnegative inferred waste plus saved residual only',
        planner_decisions=refs, actual_ports=checks, findings=failures,
        no_new_simulation=True, raw_input_unmodified=True)


def review_batch(batch, plant):
    batch = Path(batch); plan = read(batch/'plan.json')
    records = []; matching = defaultdict(list)
    for spec in plan['policy_specs']:
        for scenario_id in plan['scenario_ids']:
            pair = batch/'pairs'/(scenario_id+'__'+spec['policy_id'])
            raw = pair/'trajectory.json.gz'
            if not raw.exists():
                records.append(dict(scenario_id=scenario_id, policy_id=spec['policy_id'], inspection_status='raw_not_yet_present'))
                continue
            raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
            try:
                payload = read(raw)
                report = inspect_payload(payload, spec, plan['model_id'], plant)
                report.update(scenario_id=scenario_id, raw_path=str(raw.resolve()), raw_sha256=raw_sha, inspection_status='inspected')
                if (pair/'RESULT.json').exists() and read(pair/'RESULT.json')['raw_sha256'] != raw_sha:
                    report['findings'].append(dict(type='raw_SHA_disagrees_with_saved_result'))
                matching[scenario_id].append(report)
                records.append(report)
            except Exception as error:
                records.append(dict(scenario_id=scenario_id, policy_id=spec['policy_id'], inspection_status='inspection_error',
                    raw_path=str(raw.resolve()), raw_sha256=raw_sha, error_type=type(error).__name__, message=str(error)))
    match_rows = []
    for scene, rows in matching.items():
        match_rows.append(dict(scenario_id=scene, actual_raw_count=len(rows),
            actual_initial_states_identical=len({r['initial_state_sha256'] for r in rows}) == 1,
            actual_exogenous_scenarios_identical=len({r['actual_scenario_sha256'] for r in rows}) == 1,
            actual_seeds_identical=len({r['actual_seed'] for r in rows}) == 1,
            requires_at_least_two_policies=len(rows) >= 2))
    return dict(batch=str(batch.resolve()), plan_sha256=hashlib.sha256((batch/'plan.json').read_bytes()).hexdigest(),
        registered_pairs=plan['registered_pairs'], inspected_pairs=sum(r['inspection_status'] == 'inspected' for r in records),
        missing_or_inspection_error_pairs=sum(r['inspection_status'] != 'inspected' for r in records),
        actual_matching=match_rows, records=records, claimed_complete_batch=False,
        scope='saved observed prefix only; no optimizer and no new physical results')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Use a fresh output; never overwrite a previous pilot review')
    if args.output.resolve().drive.lower() == 'c:':
        raise ValueError('Pilot inspection output must be on D/E')
    plant = load_plant(ROOT/'configs/plant_configs.json', 'doe_capacity_matched_exploratory')
    result = review_batch(args.batch, plant)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(inspected_pairs=result['inspected_pairs'], registered_pairs=result['registered_pairs'],
        missing_or_inspection_error_pairs=result['missing_or_inspection_error_pairs'], output=str(args.output)), ensure_ascii=False))


if __name__ == '__main__':
    main()
