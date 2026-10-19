"""Reproduce one saved planner call; no physical trajectory or future query."""
import argparse
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.model import Plant
from hospital_ies.resilience import RuntimeState
from hospital_ies.solver_diagnostics import LayerCollector,collect_layers
from tools.model_closeout.planner import solve_joint

def load_input(path):
    data=json.loads(Path(path).read_text(encoding='utf8'))
    state=copy.deepcopy(data['state'])
    for key in ('gt_prev','overload_minutes','disconnected_loads'):state[key]=np.asarray(state[key],float)
    state['extra_faulted']=set(state['extra_faulted'])
    row=copy.deepcopy(data['row']);row['timestamp']=pd.Timestamp(row['timestamp'])
    readiness=copy.deepcopy(data['decision']['operating_readiness'])
    readiness['diesel_local_response']=copy.deepcopy(data['decision']['diesel_local_response'])
    readiness['diesel_start_minute']=state['diesel_start_minute']
    joint=data['request']['state_coordinated_joint']
    kwargs=dict(row=SimpleNamespace(**row),plant=Plant(**data['plant']),loads=np.asarray(data['loads']),
        availability=data['availability'],state=RuntimeState(**state),dt_h=data['dt_h'],
        grid_import_limit_kW=data['scenario'].get('grid_limit_kW',data['config']['grid_import_limit_kW']),
        diesel_start_available=readiness['diesel_connected'],gt_ramp_kW_per_h=data['config']['gt_ramp_kW_per_hour'],
        solver_time_limit_sec=data['config']['solver_time_limit_sec'],config=data['config'],
        execution_contract=data['execution_contract'],joint_options=joint['options'],
        risk_loads=joint.get('risk_loads_kW'),coordination_options=joint['coordination'],
        readiness_context=readiness,execution_limits=data['decision']['execution_limits'],
        grid_bridge_request=data['decision']['model_closeout']['grid_bridge_wait_started_minute'] is not None)
    return kwargs

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    collector=LayerCollector({'saved_replay':str(a.input)})
    try:
        with collect_layers(collector):result=solve_joint(**load_input(a.input))
    except Exception as error:
        summary=dict(status='error',error_type=type(error).__name__,message=str(error),layers=len(collector.layers))
        code=1
    else:
        summary=dict(status='success',layers=len(collector.layers),action=result['action'],
            command_disconnected=np.asarray(result['command_disconnected']).tolist())
        code=0
    collector.export(out/'layers')
    with (out/'RESULT.json').open('x',encoding='utf8') as f:json.dump(summary,f,indent=2)
    print(json.dumps(summary));return code

if __name__=='__main__':raise SystemExit(main())
