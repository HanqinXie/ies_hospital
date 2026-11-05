"""Exact NL graph validation, including the independently rejected cost cut."""
import importlib
from pathlib import Path

import numpy as np
import pytest
from scipy import sparse
from scipy.optimize import Bounds,LinearConstraint

ROOT=Path(__file__).resolve().parents[1]


def api():
    assert (ROOT/'src/hospital_ies/nonlinear.py').is_file(), 'Typed exact nonlinear backend model missing'
    return importlib.import_module('hospital_ies.nonlinear')


def probe_fixture(rejected=True):
    m=api();K=.1065990073870326
    graph=m.NonlinearSpec(tuple(m.PowerLawEquality(3*i,3*i+1,K,.673,'a'*64) for i in range(3)))
    raw=np.array([319.9987449153992,5.172707831452507,1.,320.,5.172721494666112,1.,0.,0.,0.,
                  260.0011650846009,210.00117008460091,9e-5,8.5e-5])
    if not rejected:raw=np.array([320.,5.172721494666112,1.,320.,5.172721494666112,1.,0.,0.,0.,259.99991,209.999915,9e-5,8.5e-5])
    cost=np.zeros(13);cost[[1,4,7]]=1.21;cost[9]=.01364;cost[10]=.0075
    c=np.zeros(13);c[[2,5,8]]=1.
    original=dict(matrix=sparse.csr_matrix(cost.reshape(1,-1)),lower=np.array([-np.inf]),
        upper=np.array([17.639394151991993]),bounds_lower=np.zeros(13),bounds_upper=np.ones(13)*1000.,
        integrality=np.array([0,0,1]*3+[0]*4),rows=[dict(kind='priority_lock',name='previous_true_operating_cost')])
    return graph,raw,c,original


def test_raw_optimal_tie_point_cannot_hide_true_previous_cost_cut_violation():
    graph,raw,c,original=probe_fixture()
    assert float(c@raw)==2.
    check=api().evaluate_nonlinear(graph,raw,c,original)
    assert check['raw_nonlinear_max_residual'] < 1e-7
    assert check['true_objective'] == 2.
    assert check['true_validation']['max_lock_violation'] > 1e-8
    assert check['accepted'] is False
    assert np.array_equal(raw,check['raw_x']), 'Validation cannot repair or replace the native solution'


def test_exact_feasible_probe_point_satisfies_original_true_cut():
    graph,raw,c,original=probe_fixture(False)
    assert api().evaluate_nonlinear(graph,raw,c,original)['accepted'] is True


def test_backend_rejects_native_optimal_bad_true_cut_without_repair_or_linear_fallback(monkeypatch):
    from hospital_ies import solver_backend as backend
    from scipy.optimize import OptimizeResult
    graph,raw,c,original=probe_fixture()
    seen=[]
    def attempt(problem,parameters):
        seen.append(problem.get('nonlinear'))
        return OptimizeResult(status=0,native_status=2,x=raw.copy(),fun=2.,mip_gap=0.,mip_dual_bound=2.)
    monkeypatch.setattr(backend,'_gurobi_attempt',attempt)
    with backend.original_problem(original['matrix'],original['lower'],original['upper'],
            original['bounds_lower'],original['bounds_upper'],original['integrality'],original['rows']):
        result=backend.milp(c,integrality=original['integrality'],
            bounds=Bounds(original['bounds_lower'],original['bounds_upper']),
            constraints=LinearConstraint(original['matrix'],original['lower'],original['upper']),nonlinear=graph)
    assert result.status!=0 and len(seen)==2 and all(v is graph for v in seen)
    assert np.array_equal(result.x,raw)
    assert all(not v['accepted'] and v['nonlinear_validation']['true_validation']['max_lock_violation']>1e-8 for v in result.attempts)


def test_legacy_diagnostic_replay_explicitly_rejects_nonlinear_snapshot(tmp_path):
    import json
    from hospital_ies.solver_diagnostics import replay_layer
    meta=tmp_path/'layer.json'
    meta.write_text(json.dumps({'result':{'attempts':[{'nonlinear_graph':probe_fixture()[0].as_dict()}]}}))
    with pytest.raises(ValueError,match='nonlinear.*replay|replay.*nonlinear'):
        replay_layer(tmp_path/'absent.npz',meta)


def test_nonlinear_validation_exception_revokes_provisional_linear_acceptance(monkeypatch):
    from hospital_ies import solver_backend as backend
    from scipy.optimize import OptimizeResult
    graph,raw,c,original=probe_fixture(False)
    def attempt(problem,parameters):
        return OptimizeResult(status=0,native_status=2,x=raw.copy(),fun=2.,mip_gap=0.,mip_dual_bound=2.)
    def broken(*args,**kwargs):raise ValueError('injected analytic graph rejection')
    monkeypatch.setattr(backend,'_gurobi_attempt',attempt)
    monkeypatch.setattr(api(),'evaluate_nonlinear',broken)
    with backend.original_problem(original['matrix'],original['lower'],original['upper'],
            original['bounds_lower'],original['bounds_upper'],original['integrality'],original['rows']):
        result=backend.milp(c,integrality=original['integrality'],
            bounds=Bounds(original['bounds_lower'],original['bounds_upper']),
            constraints=LinearConstraint(original['matrix'],original['lower'],original['upper']),nonlinear=graph)
    assert result.status!=0
    assert len(result.attempts)==2 and all(not v['accepted'] for v in result.attempts)
    assert all(v['error']=='injected analytic graph rejection' for v in result.attempts)
    assert np.array_equal(result.x,raw)


@pytest.mark.parametrize('kind', ['index','negative_K','power','identity','duplicate'])
def test_nl_descriptor_rejects_unknown_graph_or_invalid_domain(kind):
    m=api()
    with pytest.raises(ValueError):
        if kind=='index':m.PowerLawEquality(True,1,1.,.673,'a'*64)
        elif kind=='negative_K':m.PowerLawEquality(0,1,-1.,.673,'a'*64)
        elif kind=='power':m.PowerLawEquality(0,1,1.,.5,'a'*64)
        elif kind=='identity':m.PowerLawEquality(0,1,1.,.673,'unbound')
        else:m.NonlinearSpec((m.PowerLawEquality(0,1,1.,.673,'a'*64),)*2)


def test_native_nl_component_exact_off_and_running_without_legacy_cache():
    from hospital_ies.solver_backend import milp
    m=api();graph=m.NonlinearSpec((m.PowerLawEquality(0,1,.1065990073870326,.673,'a'*64),))
    for power in (0.,128.,320.):
        result=milp(np.array([0.,1.]),bounds=Bounds([power,0.],[power,10.]),
            constraints=LinearConstraint(np.zeros((1,2)),[-np.inf],[np.inf]),options={'time_limit':6.,'mip_rel_gap':1e-6},nonlinear=graph)
        assert result.status==0, result
        assert result.x[1] == pytest.approx(.1065990073870326*power**.673,rel=1e-9,abs=1e-9)
        assert result.attempts[-1]['nonlinear_validation']['accepted'] is True
        assert result.attempts[-1]['result_cache_policy']['enabled'] is False
        assert result.attempts[-1]['nonlinear_graph']['identity']==graph.identity
