"""Original P2B first-error conditions, without sampling or relaxed locks."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import pandas as pd
import pytest
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult, milp
from hospital_ies.model import Plant
import hospital_ies.resilience as resilience
from hospital_ies.execution import ModelConstraintInfeasible

CASES=json.loads((Path(__file__).parent/'fixtures/p2c_lex_counterexamples.json').read_text(encoding='utf-8'))
CORE_CASES=json.loads((Path(__file__).parent/'fixtures/p2c_core_lex_counterexamples.json').read_text(encoding='utf-8'))

def solve_core_case(case):
    d=case['input_context'];values=dict(d['state'])
    for key in ('gt_prev','overload_minutes','disconnected_loads'):values[key]=np.asarray(values[key],dtype=float)
    values['extra_faulted']=set(values['extra_faulted'])
    row=SimpleNamespace(timestamp=pd.Timestamp(d['row']['timestamp']),outdoor_temperature_C=d['row']['outdoor_temperature_C'])
    return resilience._solve_milp(row,Plant(**d['plant']),np.asarray(d['loads_kW']),d['availability'],resilience.RuntimeState(**values),
        d['dt_h'],d['grid_import_limit_kW'],d['diesel_start_available'],d['gt_ramp_kW_per_h'],d['config']['solver_time_limit_sec'],
        d['config'],tracking_targets=d['tracking_targets'],security_gt_penalty_USD_per_kWh=d['security_gt_penalty_USD_per_kWh'],
        execution_action=d['execution_action'],execution_contract=d['execution_contract'])

@pytest.mark.parametrize('case',CORE_CASES,ids=lambda x:x['call_id']+'-'+x['test_condition'])
def test_exact_old_core_timing_input_completes_all_layers(case):
    from hospital_ies.solver_diagnostics import LayerCollector,collect_layers
    collector=LayerCollector()
    with collect_layers(collector):result=solve_core_case(case)
    assert len(collector.layers)==5
    assert all(l['result']['status']==0 for l in collector.layers)
    assert all(l['validation']['max_original_violation']<=1e-7 and l['validation']['max_lock_violation']<=1e-10 for l in collector.layers)
    assert len(result['lex_values'])==4

def test_entailed_component_bounds_preserve_total_lock_and_round_outward():
    from fractions import Fraction
    from hospital_ies.solver_diagnostics import entailed_ens_bounds
    bounds=Bounds([0.,1.,2.],[np.inf,np.inf,np.inf])
    lock=dict(source_rank=0,objective=[0.,2.,3.],upper=10.)
    new,proofs=entailed_ens_bounds(bounds,[lock])
    assert np.array_equal(new.lb,bounds.lb)
    assert len(proofs)==2
    assert new.ub[1]==2.
    assert Fraction.from_float(new.ub[2])>=Fraction(8,3)
    assert Fraction.from_float(float(np.nextafter(new.ub[2],-np.inf)))<Fraction(8,3)
    assert np.isinf(bounds.ub).all() # original objects never mutated
    assert lock['upper']==10. and lock['objective']==[0.,2.,3.]

def solve_case(case):
    d=case['diagnostic']; values=dict(d['state'])
    for key in ('gt_prev','overload_minutes','disconnected_loads'):values[key]=np.array(values[key],dtype=float)
    values['extra_faulted']=set(values['extra_faulted'])
    state=resilience.RuntimeState(**values)
    row=SimpleNamespace(timestamp=pd.Timestamp(d['row']['timestamp']),outdoor_temperature_C=d['row']['outdoor_temperature_C'])
    cmd=d['command'] or {}; planning=d['phase']=='command_generation'
    request=cmd if planning else cmd.get('request',{})
    weight=request.get('security',{}).get('gt_loading_penalty_USD_per_kWh',0.) if planning else cmd.get('weight',0.)
    return resilience._solve_milp(row,Plant(**d['plant']),np.array(d['loads_kW']),d['availability'],state,d['dt_h'],
        d['scenario']['grid_limit_kW'],False,d['config']['gt_ramp_kW_per_hour'],d['config']['solver_time_limit_sec'],
        d['config'],tracking_targets=request.get('targets',{}),security_gt_penalty_USD_per_kWh=weight,
        execution_action=None if planning else d['executed_action'],execution_contract=d['execution_contract'])

@pytest.mark.parametrize('case',CASES,ids=lambda x:f"{x['policy']}-{x['diagnostic']['phase']}-rank{x['expected_old_rank']}-{x['scenario_id']}")
def test_original_lex_inheritance_counterexample_completes(case):
    result=solve_case(case)
    assert len(result['lex_values'])==4
    assert result['optimizer_warning']==''
    assert max(map(abs,result['energy_balance_residuals']))<=1e-4
    assert abs(result['steam_balance_residual_lbph'])<=1e-4

def test_economic_layer_failure_is_not_silently_skipped(monkeypatch):
    real=resilience.milp; ranks=[]
    def injected(c,**kwargs):
        ranks.append(len(ranks))
        if len(ranks)==5:return OptimizeResult(status=2,x=None,message='injected final-layer error')
        return real(c,**dict(kwargs,options=dict(kwargs['options'],presolve=False)))
    monkeypatch.setattr(resilience,'milp',injected)
    with pytest.raises(ModelConstraintInfeasible,match='rank=4'):
        solve_case(CASES[0])
    assert ranks==list(range(5))

def test_real_fixed_source_surplus_remains_infeasible():
    case=json.loads(json.dumps(CASES[0]));d=case['diagnostic']
    d['loads_kW']=(np.array(d['loads_kW'])*0).tolist()
    d['execution_contract']['grid_import_mode']='fixed'
    d['executed_action']['grid_import_kW']=250.
    with pytest.raises(ModelConstraintInfeasible,match='rank=0'):
        solve_case(case)

def test_layer_collector_isolated_export_and_standalone_replay(tmp_path):
    from hospital_ies.solver_diagnostics import LayerCollector, collect_layers, replay_layer
    a=LayerCollector({'call_id':'first'});b=LayerCollector({'call_id':'second'})
    with collect_layers(a):solve_case(CASES[0])
    with collect_layers(b):solve_case(CASES[1])
    assert len(a.layers)==len(b.layers)==5
    assert a.layers[0]['context']['call_id']=='first'
    assert b.layers[0]['context']['call_id']=='second'
    before=len(a.layers);solve_case(CASES[0]);assert len(a.layers)==before
    destination=tmp_path/'export';a.export(destination)
    replay=replay_layer(destination/'layer_02.npz',destination/'layer_02.json')
    assert replay['status']==0
    assert replay['validation']['max_original_violation']<=1e-7
    assert replay['validation']['max_lock_violation']<=1e-10
    assert replay['validation']['max_integrality_distance']<=1e-9
    for layer in a.layers:
        assert layer['matrix'].shape[1]==26
        assert layer['result']['status']==0
        assert layer['result']['x_raw'] is not None
        assert layer['variables'][0]['unit']=='kW'
    assert a.layers[-1]['processed_x'] is not None

def test_collector_reset_on_exception():
    from hospital_ies.solver_diagnostics import LayerCollector, collect_layers
    a=LayerCollector({'call_id':'failure'});case=json.loads(json.dumps(CASES[0]))
    case['diagnostic']['loads_kW']=(np.array(case['diagnostic']['loads_kW'])*0).tolist()
    case['diagnostic']['execution_contract']['grid_import_mode']='fixed'
    with pytest.raises(ModelConstraintInfeasible):
        with collect_layers(a):solve_case(case)
    assert len(a.layers)==1 and a.layers[0]['result']['status']==2
    solve_case(CASES[0]);assert len(a.layers)==1
