"""Recorded day100 mathematical replay; no annual reads, reset or step."""
from __future__ import annotations
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
import numpy as np
import pandas as pd
from hospital_ies.model import Plant, load_plant
from hospital_ies.resilience import RuntimeState
from hospital_ies.solver_diagnostics import flatten_constraints, json_value


def fixture():
    path = HERE / 'fixture.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    old = ROOT / 'outputs/v17_state_coordinated_20261018_run01'
    compact = old / 'review/raw_audit_long_C4_final24/recorded_timeseries/long_development04_005__97ac92e8635994c975b8.json.gz'
    spec = importlib.util.spec_from_file_location('v17_saved_review', old / 'review_saved.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    saved = json.loads(gzip.decompress(compact.read_bytes()))
    observation = next(r for r in module.restore_decisions(saved['decisions']) if r['decision_time_min'] == 420.)
    segment = next(s for s in saved['segments'] if s['start_minute'] == 420.)
    previous = next(s for s in saved['segments'] if s['end_minute'] == 420.)
    row = next(r for r in saved['rows'] if r['minute'] == 420.)
    command = next(e['command'] for e in saved['events'] if e['kind'] == 'command_generated' and e['time_minute'] == 420.)
    state = copy.deepcopy(previous['post_state'])
    state['disconnected_loads'] = copy.deepcopy(observation['operating_readiness']['actual_disconnected_kW'])
    context = copy.deepcopy(observation['operating_readiness'])
    context['diesel_local_response'] = copy.deepcopy(observation['diesel_local_response'])
    context['diesel_start_minute'] = state['diesel_start_minute']
    value = dict(schema='hospital_ies.v18.saved_day100_420.v1', time_minute=420.,
        source_compact=str(compact.relative_to(ROOT)), source_compact_sha256=hashlib.sha256(compact.read_bytes()).hexdigest(),
        original_raw_opened=False, annual_rows_read=0, environment_reset_or_step_calls=0,
        row=dict(timestamp=row['timestamp'],outdoor_temperature_C=observation['features']['outdoor_temperature_C']),
        state=state, loads_kW=segment['loads_kW'], availability=segment['availability'],
        readiness_context=context, request=observation['requested_command']['state_coordinated_joint'],
        plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory').as_dict(),
        config=json.loads((ROOT/'configs/resilience_assumptions.json').read_text(encoding='utf-8')),
        execution_contract={k:v for k,v in saved['operating_execution_contract'].items() if k!='diesel_dispatch_mode'},
        original_command=command, original_actual_segment=segment)
    path.write_text(json.dumps(json_value(value),indent=2)+'\n',encoding='utf-8')
    return value


def inputs(value):
    s=copy.deepcopy(value['state'])
    s['gt_prev']=np.asarray(s['gt_prev']);s['disconnected_loads']=np.asarray(s['disconnected_loads'])
    s['extra_faulted']=set(s['extra_faulted'])
    request=copy.deepcopy(value['request']);config=value['config']
    return dict(row=SimpleNamespace(**{**value['row'],'timestamp':pd.Timestamp(value['row']['timestamp'])}),
        plant=Plant(**value['plant']),loads=value['loads_kW'],availability=value['availability'],state=RuntimeState(**s),
        dt_h=.25,grid_import_limit_kW=config['grid_import_limit_kW'],
        diesel_start_available=(value['readiness_context']['diesel_connected'] and value['availability']['GRID']<1.
            and value['availability']['DIESEL']>0. and s['diesel_energy_remaining_kWh']>0.),
        gt_ramp_kW_per_h=config['gt_ramp_kW_per_hour'],solver_time_limit_sec=config['solver_time_limit_sec'],config=config,
        execution_contract=value['execution_contract'],joint_options=request['options'],risk_loads=request['risk_loads_kW'],
        coordination_options=request['coordination'],readiness_context=value['readiness_context'])


def replay(planner, *, execution_recovery=None):
    value=fixture();kwargs=inputs(value)
    if execution_recovery is not None:
        kwargs['coordination_options']['execution_recovery']=execution_recovery
    captured={}
    def trace(frame,event,arg):
        if frame.f_code is planner.solve_joint.__code__ and event=='return':captured.update(frame.f_locals)
        return trace
    sys.settrace(trace)
    try:result=planner.solve_joint(**kwargs)
    finally:sys.settrace(None)
    return result,captured


def main():
    from tools.state_coordinated import planner
    value=fixture();result,captured=replay(planner)
    assert result['action']==value['original_command']['dispatch']
    matrix,lo,hi=flatten_constraints(captured['constraints'])
    np.savez_compressed(HERE/'original_domain.npz',matrix=matrix.toarray(),lower=lo,upper=hi,
        bounds_lower=captured['lower'],bounds_upper=captured['upper'],integrality=captured['integrality'],
        selected_x=captured['selected_x'])
    (HERE/'original_rows_and_variables.json').write_text(json.dumps(dict(rows=captured['metadata'],
        variables=captured['variables'],layers=captured['layers']),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(command_exactly_reproduced=True,dimensions=matrix.shape,
        original_raw_opened=False,annual_rows_read=0,reset_or_step=0)))


if __name__=='__main__':main()
