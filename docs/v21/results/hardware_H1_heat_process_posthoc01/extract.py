"""Posthoc read-only process explanation from already exported, hash-bound HW data."""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

B = Path('E:/hospital_ies_v21_20261023/reviews/hardware_figures_actual126_01')
O = Path(__file__).parent
csv.field_size_limit(64 * 1024 * 1024)

def ref(p):
    p = Path(p)
    return dict(path=str(p), bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())

def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))

manifest_path = B / 'hw18_source01/HW18_ACTUAL_MANIFEST.json'
assert ref(manifest_path)['sha256'] == '8fa4872d98493c137f4abe4dabe3f1dacc694ff392aefa7632b5c5c24b1875a0'
manifest = read(manifest_path)
bindings = [ref(manifest_path)]
rows, summaries = [], []
for number in (10, 11, 13, 14, 16, 17):
    case = manifest['cases'][number - 1]
    assert case['fault_target'] == 'GRID+ALL_GT' and case['configuration_id'] in ('H0', 'H1')
    folder = B / 'hw18_commands01/exports' / f'{number:02d}'
    erp = folder / 'EXPORT_RECEIPT.json'
    assert ref(erp)['sha256'] == case['export_receipt_sha256']
    er = read(erp)
    assert er['scenario_id'] == case['scenario_id'] and er['policy_id'] == case['policy_id'] and er['configuration_id'] == case['configuration_id']
    bindings.append(ref(erp))
    states_path = folder / 'SEGMENT_STATES.csv'
    state_binding = next(a for a in er['artifacts'] if a['path'] == 'SEGMENT_STATES.csv')
    assert ref(states_path)['sha256'] == state_binding['sha256']
    bindings.append(ref(states_path))
    process_path = B / 'hw18_source01' / case['folder'] / 'PROCESS.csv'
    assert ref(process_path)['sha256'] == case['csv_sha256']
    bindings.append(ref(process_path))
    with process_path.open(encoding='utf-8-sig', newline='') as f:
        process = list(csv.DictReader(f))
    local = []
    with states_path.open(encoding='utf-8-sig', newline='') as f:
        for s in csv.DictReader(f):
            start, end = float(s['start_minute']), float(s['end_minute'])
            if start >= 90:
                continue
            p = process[int(s['process_row_index'])]
            assert float(p['start_minute']) == start and float(p['end_minute']) == end
            permissions = json.loads(s['arrived_source_permissions_json'])
            limits = json.loads(s['execution_limits_json'])
            deps = json.loads(s['service_dependencies_json'])
            seg = json.loads(s['segment_json'])
            r = dict(entry=number, policy_id=case['policy_id'], configuration_id=case['configuration_id'],
                     start_minute=start, end_minute=end, command_id=s['command_id'],
                     grid_bus_state=s['grid_bus_state'], pre_diesel_status=s['pre_diesel_status'],
                     arrived_boiler_permission_lbph=permissions['boiler_steam_lbph'],
                     effective_boiler_limit_lbph=limits['effective_dispatch']['boiler_steam_lbph'],
                     actual_boiler_steam_lbph=float(p['boiler_steam_lbph']),
                     actual_hrsg_steam_lbph=float(p['hrsg_steam_lbph']),
                     actual_grid_kW=float(p['grid_import_kW']),
                     actual_GT_kW=sum(float(p[f'gt_electric_kW_{i}']) for i in (1, 2, 3)),
                     actual_DG_kW=float(p['diesel_kW']), actual_UPS_discharge_kW=float(p['ups_discharge_kW']),
                     actual_absorption_cooling_kW=float(p['absorption_cooling_kW']),
                     saved_steam_balance_residual_lbph=seg['steam_balance_residual_lbph'])
            for i, group in enumerate(('P0', 'P1', 'P2', 'P3')):
                for carrier in ('e', 'h', 'c'):
                    for kind in ('requested', 'delivered'):
                        k = f'{group}_{carrier}_{kind}_kW'
                        r[k] = float(p[k])
                for j, name in enumerate(('fan', 'CHW_pump', 'heat_pump')):
                    r[f'{group}_{name}_delivered_kW'] = deps['auxiliary_delivered_kW'][i][j]
                r[f'{group}_direct_electric_delivered_kW'] = deps['direct_electric_delivered_kW'][i]
            rows.append(r)
            if start >= 60 and end <= 75:
                local.append(r)
    assert local and local[0]['start_minute'] == 60 and local[-1]['end_minute'] == 75
    assert all(a['end_minute'] == z['start_minute'] for a, z in zip(local, local[1:]))
    numeric = {k: dict(min=min(r[k] for r in local), max=max(r[k] for r in local))
               for k in local[0] if isinstance(local[0][k], (float, int)) and k != 'entry'}
    summaries.append(dict(entry=number, policy_id=case['policy_id'], configuration_id=case['configuration_id'],
                          interval=[60, 75], segments=len(local), observed_numeric_ranges=numeric,
                          command_ids=sorted({r['command_id'] for r in local}),
                          source_process=ref(process_path), source_segment_states=ref(states_path)))
for binding in bindings:
    assert ref(binding['path']) == binding
with (O / 'OBSERVED_NATIVE_SEGMENTS_0_TO90.csv').open('x', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
value = dict(schema='hospital_ies.posthoc_H1_heat_observation.v1', utc=datetime.now(timezone.utc).isoformat(),
             scope='First already predeclared winter compound C/A/M H0/H1; posthoc effect explanation only',
             source_bindings=bindings, all_source_SHA_unchanged_after=True,
             rows=len(rows), summaries=summaries, raw_or_sidecar_reads=0,
             new_physical_attempts=0, project_modules_imported=False,
             final_scientific_values_read=False, native_clock_and_process_row_exact=True,
             individual_UPS_to_fan_or_pump_source_allocation='UNKNOWN_NOT_RECORDED',
             controls_single_factor_preparation_causality='NOT_IDENTIFIED_BY_C_VS_M_COMPARISON',
             new_independent_backgrounds=0, original_representative_priorities_changed=False,
             files=[ref(O / 'OBSERVED_NATIVE_SEGMENTS_0_TO90.csv')])
with (O / 'OBSERVATION_RECEIPT.json').open('x', encoding='utf-8') as f:
    json.dump(value, f, ensure_ascii=False, indent=2)
    f.write('\n')
print(json.dumps(dict(receipt=ref(O / 'OBSERVATION_RECEIPT.json'), rows=len(rows),
                     case_windows=[dict(entry=s['entry'], segments=s['segments'],
                                        boiler=s['observed_numeric_ranges']['actual_boiler_steam_lbph'],
                                        permission=s['observed_numeric_ranges']['arrived_boiler_permission_lbph'],
                                        UPS=s['observed_numeric_ranges']['actual_UPS_discharge_kW'],
                                        P0_heat=s['observed_numeric_ranges']['P0_h_delivered_kW'],
                                        P1_heat=s['observed_numeric_ranges']['P1_h_delivered_kW']) for s in summaries])))
