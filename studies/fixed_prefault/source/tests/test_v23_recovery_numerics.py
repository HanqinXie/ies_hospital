"""Exact current-port regression for the source04 winter470 recovery lock."""
import copy
import importlib
import os
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
import json
from types import SimpleNamespace
import pandas as pd
from hospital_ies.model import Plant
from hospital_ies.resilience import RuntimeState
from hospital_ies.solver_diagnostics import LayerCollector,collect_layers
from tools.operating_response.physical_balance import solve_physical
REQUEST=1.0095654820244402e-7

def fixture():
    p=json.loads((ROOT/'tests/fixtures/v23_winter470_current_port.json').read_text(encoding='utf8'))
    p['row']['timestamp']=pd.Timestamp(p['row']['timestamp']);p['row']=SimpleNamespace(**p['row'])
    p['plant']=Plant(**p['plant']);p['loads']=np.asarray(p['loads'])
    for key in ('gt_prev','overload_minutes','disconnected_loads'):p['state'][key]=np.asarray(p['state'][key])
    p['state']['extra_faulted']=set(p['state']['extra_faulted']);p['state']=RuntimeState(**p['state'])
    return p

def replay(arguments):
    collector=LayerCollector();result=error=None
    with collect_layers(collector):
        try:result=solve_physical(**copy.deepcopy(arguments))
        except Exception as exc:error=exc
    return result,error,collector
from hospital_ies import solver_diagnostics as diagnostics
from hospital_ies.solver_backend import milp,original_problem
from hospital_ies.solver_diagnostics import flatten_constraints,validate_vector
from scipy.optimize import Bounds,LinearConstraint

@pytest.fixture(autouse=True)
def presentation(monkeypatch):
    from tools.model_correctness_v23.numerics import solve_layer
    monkeypatch.setattr(diagnostics,'solve_layer',solve_layer)

@pytest.fixture(scope='module')
def current_port():return fixture()

def assert_valid(collector):
    for layer in collector.layers:
        assert layer['result']['status']==0
        assert layer['validation']['max_original_violation']<=1e-7
        assert layer['validation']['max_lock_violation']<=1e-10
        assert layer['validation']['max_integrality_distance']<=1e-9

def test_actual_winter470_native_domain_accepts_without_dropping_true_recovery(current_port):
    result,error,collector=replay(current_port)
    assert error is None, str(error)
    assert_valid(collector)
    # The request and the original raw 1e-7 kW recovery lock remain visible.
    cold=result['action']['recovery_cooling_kW']
    assert 0.<cold<=REQUEST+1e-10
    for layer in (collector.layers[6],collector.layers[13]):
        row=next(x for x in layer['validation']['rows'] if x['name']=='m22.actual_recovery_after_all_service')
        assert row['upper']==-(REQUEST-1e-7)
        assert row['violation']<=1e-10
    assert current_port['config']['_model_closure_v22']['recovery_cooling_request_kW']==REQUEST

@pytest.mark.parametrize('demand',[0.,1e-12,1e-8,1e-7,1.0011e-7,1.01e-7,1e-6,.1,100.])
def test_actual_positive_and_tiny_optional_requests_keep_original_service_locks(current_port,demand):
    p=copy.deepcopy(current_port);p['config']['_model_closure_v22']['recovery_cooling_request_kW']=demand
    result,error,collector=replay(p)
    assert error is None,str(error)
    assert_valid(collector)
    assert result['action']['recovery_cooling_kW']<=demand+1e-10
    if demand>1e-7:
        assert result['action']['recovery_cooling_kW']>=demand-1e-7-1e-10

def test_absent_recovery_leaves_original_matrices_and_actual_vector_bitwise_equal(current_port,monkeypatch):
    candidate=diagnostics.solve_layer
    p=copy.deepcopy(current_port);p['config']['_model_closure_v22']['recovery_cooling_request_kW']=0.
    with monkeypatch.context() as unpatched:
        if hasattr(candidate,'original_solve_layer'):
            unpatched.setattr(diagnostics,'solve_layer',candidate.original_solve_layer)
        baseline,error,before=replay(p)
    after_result,error2,after=replay(p)
    assert error is error2 is None
    assert baseline['action']==after_result['action']
    for old,new in zip(before.layers,after.layers,strict=True):
        for key in ('matrix','solver_matrix'):
            assert np.array_equal(old[key].toarray(),new[key].toarray())
        for key in ('lower','upper','objective','bounds_lower','bounds_upper','solver_lower','solver_upper',
                    'solver_bounds_lower','solver_bounds_upper','integrality','row_scales'):
            assert np.array_equal(old[key],new[key]),key
        np.testing.assert_array_equal(old['result']['x_raw'],new['result']['x_raw'])

def test_equivalent_recovery_row_cannot_admit_no_capacity_phantom():
    # Original physical x=0 and genuine recovery lock x>=1 are contradictory.
    rows=[dict(name='physical_no_source',kind='physical'),
          dict(name='m22.actual_recovery_after_all_service',kind='priority_lock')]
    constraints=[LinearConstraint([[1.],[-1.]],[0.,-np.inf],[0.,-1.])]
    result=diagnostics.solve_layer(milp,np.array([1.]),integrality=np.array([0]),bounds=Bounds([0.],[2.]),
        constraints=constraints,options={'time_limit':6.,'mip_rel_gap':1e-6},rank=1,
        variables=[dict(name='m22.recovery_cooling',unit='kW',index=0)],rows=rows,locks=[],input_context={},row_scaling=True)
    assert result.status==2 and result.x is None

def test_presentation_only_scales_recovery_lock_in_solver_coordinates():
    rows=[dict(name='physical.electric',kind='physical'),
          dict(name='m22.actual_recovery_after_all_service',kind='priority_lock')]
    constraints=[LinearConstraint([[2.],[-1.]],[0.,-np.inf],[2.,-1e-9])]
    captured={}
    def observe(c,**kw):
        from scipy.optimize import OptimizeResult
        captured.update(original=copy.deepcopy(__import__('hospital_ies.solver_backend',fromlist=['active_original_problem']).active_original_problem()),
            solver=flatten_constraints(kw['constraints']))
        return OptimizeResult(status=0,x=np.array([1e-9]),fun=1e-9)
    diagnostics.solve_layer(observe,np.array([1.]),integrality=np.array([0]),bounds=Bounds([0.],[2.]),
        constraints=constraints,options={},rank=1,variables=[],rows=rows,locks=[],input_context={},row_scaling=True)
    original=captured['original'];matrix,lower,upper=captured['solver']
    np.testing.assert_array_equal(original['matrix'].toarray(),[[2.],[-1.]])
    np.testing.assert_array_equal(original['upper'],[2.,-1e-9])
    np.testing.assert_array_equal(matrix.toarray(),[[1.],[-16.]])
    np.testing.assert_array_equal(upper,[1.,-16e-9])
