"""Three coupled terminal balances; an energy diagnostic, not temperature QA.

The same representative OR1 state continues through unavailable source power,
an unavailable fan path, and actual metered active recovery. No clinical demand
or fitted thermal parameter changes, and no room state resets on restoration.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import csv
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'src'))
from hospital_ies.model import load_plant
from hospital_ies.resilience import RuntimeState, empty_faults
from hospital_ies.idf_parameters import zone_parameter_table
from tools.operating_response.physical_balance import solve_physical
from .temperature import ThermalState, commit, recovery_request_kW


def run(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError('Recovery diagnostic requires a fresh directory')
    paths = [root/'inputs/energyplus/Hospital_SanDiego.idf', root/'configs/plant_configs.json',
             Path(__file__), root/'tools/model_closure_v22/temperature.py',
             root/'tools/operating_response/physical_balance.py']
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    sources = {str(path): sha(path) for path in paths}
    original = next(row for row in zone_parameter_table(paths[0], .5, 1.5) if row['zone_name']=='OR1_FLR_2')
    thermal = ThermalState(4., original['effective_C_kWh_K'], original['assumed_UA_kW_K'])
    plant = load_plant(paths[1], 'doe_capacity_matched_exploratory')
    row = SimpleNamespace(timestamp=pd.Timestamp('2017-07-10 12:00'), outdoor_temperature_C=25.)
    state = RuntimeState(np.zeros(3), 2400., set(), np.zeros(3), {})
    loads = np.zeros((4, 3)); loads[0, 0] = 10.
    intervals = []
    for index, (grid, fan) in enumerate(((10., 1.), (100., 0.), (100., 1.))):
        request = recovery_request_kW(thermal, known_horizon_h=1., max_power_kW=20.)
        faults = empty_faults()
        faults.update(GT_1=0., GT_2=0., GT_3=0., ABS=0., DIESEL=0., AUX_FAN_P0=fan)
        config = dict(sci_service_dependencies=True, _model_closure_v22=dict(
            recovery_cooling_request_kW=request, recovery_pump_kW_per_kW=.02,
            recovery_fan_kW_per_kW=.1))
        action = dict(gt_electric_kW=[0., 0., 0.], grid_import_kW=grid, diesel_kW=0.,
            boiler_steam_lbph=0., absorption_cooling_kW=0., electric_chiller_cooling_kW=[100., 0., 0.])
        result = solve_physical(row, plant, loads, faults, state, 1., grid, False, 900., 6., config,
            execution_action=action, execution_contract=dict(diesel_mode='legacy_explicit_permission',
                diesel_dispatch_mode='arrived_ceiling', grid_import_mode='arrived_ceiling'))
        actual = result['action']
        advanced = commit(thermal, actual['recovery_cooling_kW'], 1.)
        intervals.append(dict(start_minute=index*60., end_minute=(index+1)*60.,
            delta_before_C=thermal.delta_C, delta_after_C=advanced.state.delta_C,
            recovery_request_kW=request, actual_extra_cooling_kWh=advanced.actual_extra_cooling_kWh,
            actual_fan_kWh=actual['recovery_fan_electric_kW'], actual_pump_kWh=actual['recovery_pump_electric_kW'],
            actual_EC_cooling_kWh=sum(actual['electric_chiller_cooling_kW']),
            actual_grid_kWh=actual['grid_import_kW'], building_electric_request_kWh=10.,
            actual_building_electric_served_kWh=float(result['served'][0, 0]),
            passive_heat_rejection_kWh=advanced.passive_heat_rejection_kWh,
            thermal_inventory_change_kWh=advanced.thermal_energy_change_kWh,
            thermal_balance_residual_kWh=advanced.balance_residual_kWh,
            maximum_source_balance_residual=max(map(abs, result['energy_balance_residuals'])),
            fan_path_available=fan, arrived_grid_ceiling_kW=grid, actual_segment_cost_USD=result['cost_USD_step']))
        thermal = advanced.state
    result = dict(status='actual_coupled_energy_diagnostic', actual_IES_episode_attempts=0,
        actual_terminal_balance_solves=3, precision_acceptance_claimed=False,
        initial_delta_C=4., final_state=asdict(thermal), sources_sha256=sources,
        sources_unchanged=all(sha(Path(path))==value for path, value in sources.items()),
        assumptions=['Constructed 10kW building demand, two source ceilings and fan path interruption',
            'Original representative OR1 C/U, no zone multiplier and no fitted parameters',
            'One representative-zone sensible kWh equals one additional plant cold kWh for this diagnostic',
            'Incremental fan .1kWe/kWc and pump .02kWe/kWc are explicit diagnostic assumptions',
            'No claim about actual hospital temperature precision or clinical criteria'], intervals=intervals)
    if not result['sources_unchanged']:
        result['status'] = 'failed_source_drift'
    output.mkdir(parents=True, exist_ok=False)
    with (output/'RECOVERY_ENERGY.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(intervals[0]))
        writer.writeheader(); writer.writerows(intervals)
    (output/'RESULT.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = run(Path(__file__).resolve().parents[2], args.output)
    print(json.dumps({key: result[key] for key in ('status', 'actual_IES_episode_attempts',
        'actual_terminal_balance_solves', 'sources_unchanged', 'final_state')}))
    return 0 if result['status']=='actual_coupled_energy_diagnostic' else 1


if __name__ == '__main__':
    raise SystemExit(main())
