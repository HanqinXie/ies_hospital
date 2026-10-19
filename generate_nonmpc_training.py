"""Closed-loop, policy-paired, non-MPC scenario generation.

Policies:
  priority_milp = v3.1 single-step P0>P1>P2>P3>cost lexicographic MILP.
  secure_milp   = SAME hard constraints/priorities, plus a GT-loading security
                  proxy in final tie-break (not an actual trip probability).

No optimizer is told the future scenario shock, initiating device or stage.
Independent trajectories share the same scenario/initial state for comparison.
"""
from __future__ import annotations
import argparse,hashlib,json,sys,time
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.control_env import HospitalIESEnv
from scenario_sampler import sample_scenarios,coverage_summary
from hospital_ies.training_schema import build_features

def _json_clean(v):
    if isinstance(v,dict):return {k:_json_clean(x) for k,x in v.items()}
    if isinstance(v,list):return [_json_clean(x) for x in v]
    if isinstance(v,tuple):return [_json_clean(x) for x in v]
    if isinstance(v,np.generic):return _json_clean(v.item())
    if isinstance(v,float) and not np.isfinite(v):return None
    return v
POLICIES=('priority_milp','secure_milp')


def _policy_action(policy:str,obs:dict,security_weight:float):
    if policy=='priority_milp':return {}
    if policy=='secure_milp':
        return {'security':{'gt_loading_penalty_USD_per_kWh':security_weight}}
    raise ValueError('Unknown policy: '+str(policy))


def add_future_risk_labels(df:pd.DataFrame,horizon:int=4)->pd.DataFrame:
    """Label future realized P0 service failure *conditional on the policy*.

    Consecutive next H complete decision intervals must exist. Never use a
    truncated rollout as negative evidence of future safety.
    """
    if horizon<1:raise ValueError('horizon must be positive')
    out=df.copy();out['future_P0_failure_H']=0;out['future_horizon_valid']=False
    for _,g in out.groupby('episode_id',sort=False):
        rows=g.sort_values('step')
        if not np.array_equal(rows.step.to_numpy(),np.arange(len(rows))):
            raise ValueError('Noncontiguous episode steps')
        values=rows.p0_failed.to_numpy(dtype=int)
        future=[int(np.any(values[i+1:i+horizon+1])) if i+horizon<len(values) else 0
                for i in range(len(values))]
        valid=[i+horizon<len(values) for i in range(len(values))]
        out.loc[rows.index,'future_P0_failure_H']=future
        out.loc[rows.index,'future_horizon_valid']=valid
    return out


def run(episodes_per_stage:int=4,steps:int=12,seed:int=20261012,
        out:Path|None=None,resume:bool=False,security_weight:float=0.35,
        risk_horizon:int=4,policies:tuple[str,...]=POLICIES,suite_id:str='design',
        execution_contract:dict|None=None,*,scenario_specs=None,root:Path|None=None,
        max_trajectories:int|None=None,schema_versions=None,label_horizons=(15,30,60)):
    from hospital_ies.dataset_export import generate_dataset
    return generate_dataset(root=Path(root or ROOT),out=Path(out or ROOT/'outputs/v43_p2a_preview'),
        episodes_per_stage=episodes_per_stage,steps=steps,seed=seed,resume=resume,
        security_weight=security_weight,risk_horizon=risk_horizon,policies=policies,
        suite_id=suite_id,execution_contract=execution_contract,scenario_specs=scenario_specs,
        max_trajectories=max_trajectories,schema_versions=schema_versions,label_horizons=label_horizons)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--episodes-per-stage',type=int,default=4)
    p.add_argument('--steps',type=int,default=12)
    p.add_argument('--seed',type=int,default=20261012)
    p.add_argument('--security-weight',type=float,default=0.35)
    p.add_argument('--risk-horizon',type=int,default=4)
    p.add_argument('--output-dir',type=Path,default=ROOT/'outputs/v43_p2a_preview')
    p.add_argument('--suite-id',type=str,default='design')
    p.add_argument('--resume',action='store_true')
    a=p.parse_args()
    print(json.dumps(run(a.episodes_per_stage,a.steps,a.seed,a.output_dir,a.resume,
                         a.security_weight,a.risk_horizon,suite_id=a.suite_id),indent=2,ensure_ascii=False))
