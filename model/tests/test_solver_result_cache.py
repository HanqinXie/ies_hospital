"""Accepted-cache contract tests use real backend validation, without trajectories."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult
from hospital_ies import solver_backend as backend
from hospital_ies import solver_diagnostics


def scope(**kwargs):
    return backend.accepted_result_cache_scope(**kwargs)


def solve(*, c=None, matrix=None, lower=1., upper=np.inf, bound_upper=10., integrality=None, options=None):
    return backend.milp(np.array([1.,2.]) if c is None else c,
        integrality=np.zeros(2,dtype=int) if integrality is None else integrality,
        bounds=Bounds([0.,0.],[bound_upper,10.]),
        constraints=LinearConstraint([[1.,1.]] if matrix is None else matrix,[lower],[upper]),
        options=dict(time_limit=6.,mip_rel_gap=1e-6) if options is None else options)


@pytest.fixture
def native(monkeypatch):
    calls=[]
    def attempt(problem,parameters):
        calls.append((copy.deepcopy(problem),dict(parameters)))
        x=np.array([max(0.,problem['lower'][0]/problem['matrix'][0,0]),0.])
        if problem['integrality'][0]:x[0]=np.ceil(x[0])
        return OptimizeResult(status=0,x=x,fun=float(problem['c']@x),mip_dual_bound=float(problem['c']@x),
            mip_gap=0.,mip_node_count=0,native_status=2,message='controlled native oracle',backend='gurobipy')
    monkeypatch.setattr(backend,'_gurobi_attempt',attempt)
    return calls


def test_default_off_preserves_native_behavior_and_log_shape(native):
    first,second=solve(),solve()
    assert len(native)==2
    assert 'result_cache_provenance' not in first.attempts[0]
    assert 'native_optimize_performed' not in second.attempts[0]
    assert backend.accepted_result_cache_stats()['enabled'] is False


def test_exact_hit_has_fresh_validation_budget_and_detached_arrays(native):
    with scope(scope_id='unit-exact') as cache:
        first=solve(); first.x[0]=9.;first.attempts[0]['raw_x'][0]=9.
        second=solve()
        assert len(native)==1
        assert second.x.tolist()==[1.,0.]
        assert second.attempts[0]['validation']['max_original_violation']==0.
        a=second.attempts[0]; p=a['result_cache_provenance']
        assert p['mode']=='exact_verified_reuse' and not a['native_optimize_performed']
        assert p['fresh_original_validation'] is True
        assert p['call_id']==2 and p['origin_call_id']==1 and p['origin_accepted'] is True
        assert a['parameters']['TimeLimit']>0 and a['parameters']['TimeLimit']<=6.
        assert a['elapsed_seconds']>=0
        third=solve();second.x[:]=7.;second.attempts[0]['raw_x'][:]=[7.,7.]
        assert third.x.tolist()==[1.,0.]
        assert cache.stats()['hits']==2
    assert backend.accepted_result_cache_stats()['enabled'] is False
    assert cache.stats()['entries']==1


@pytest.mark.parametrize('change',[
    dict(c=np.array([1.1,2.])),dict(matrix=[[1.1,1.]]),dict(lower=.9),dict(upper=9.),
    dict(bound_upper=9.),dict(integrality=np.array([1,0])),
    dict(options=dict(time_limit=5.,mip_rel_gap=1e-6)),
    dict(options=dict(time_limit=6.,mip_rel_gap=1e-6,presolve=False)),
])
def test_every_actual_problem_or_options_change_misses(native,change):
    with scope() as cache:
        solve();solve(**change)
        assert len(native)==2
        assert cache.stats()['hits']==0


def test_original_unscaled_domain_and_row_metadata_are_in_key(native):
    with scope() as cache:
        for name,bound in [('first',10.),('second',10.),('second',9.)]:
            with backend.original_problem([[1.,1.]],[1.],[np.inf],[0.,0.],[bound,10.],
                    [0,0],[dict(kind='physical',name=name,unit='kW')]):
                solve()
        assert len(native)==3 and cache.stats()['hits']==0


def test_changed_numerical_policy_or_native_callable_misses(native,monkeypatch):
    with scope() as cache:
        solve()
        monkeypatch.setitem(backend._COMMON,'Seed',17)
        solve()
        old=backend._gurobi_attempt
        def other(*args):return old(*args)
        monkeypatch.setattr(backend,'_gurobi_attempt',other)
        solve()
        assert len(native)==3 and cache.stats()['hits']==0


def test_bad_cache_payload_rejected_then_fresh_native(native):
    with scope() as cache:
        solve()
        entry=next(iter(cache._entries.values()))
        entry['result']['x'][0]=99.
        result=solve()
        assert result.status==0 and result.x.tolist()==[1.,0.]
        assert len(native)==2 and cache.stats()['rejected_hits']==1
        assert result.attempts[0]['result_cache_rejection']['reason']=='payload_integrity_mismatch'


def test_hit_runs_current_validator_and_falls_back_if_it_rejects(native,monkeypatch):
    original=solver_diagnostics.validate_vector
    with scope() as cache:
        solve(); calls=[]
        def reject_once(*args,**kwargs):
            value=original(*args,**kwargs);calls.append(1)
            if len(calls)==1:value['max_original_violation']=1.
            return value
        monkeypatch.setattr(solver_diagnostics,'validate_vector',reject_once)
        result=solve()
        assert len(calls)==2 and len(native)==2
        assert result.status==0 and result.attempts[0]['native_optimize_performed']
        assert result.attempts[0]['result_cache_rejection']['validation']['max_original_violation']==1.
        assert cache.stats()['hits']==0 and cache.stats()['rejected_hits']==1


def test_failed_first_attempt_and_recovery_success_are_not_cached(native,monkeypatch):
    good=backend._gurobi_attempt;calls=[]
    def recovery(problem,parameters):
        calls.append(1)
        if len(calls)%2:return OptimizeResult(status=4,x=None,fun=None,native_status=12,message='injected failure')
        return good(problem,parameters)
    monkeypatch.setattr(backend,'_gurobi_attempt',recovery)
    with scope() as cache:
        assert solve().status==0
        assert solve().status==0
        assert len(calls)==4 and cache.stats()['entries']==0


def test_validation_failed_native_result_is_not_cached(native,monkeypatch):
    def invalid(problem,parameters):
        return OptimizeResult(status=0,x=np.array([-1.,0.]),fun=-1.,mip_dual_bound=-1.,mip_gap=0.,
            native_status=2,message='invalid native witness',backend='gurobipy')
    monkeypatch.setattr(backend,'_gurobi_attempt',invalid)
    with scope() as cache:
        assert solve().status!=0
        assert cache.stats()['entries']==0


def test_capacity_lru_bytes_and_scope_isolation(native):
    with scope(max_entries=2,max_bytes=32768) as outer:
        solve();solve(c=np.array([1.1,2.]));solve()
        solve(c=np.array([1.2,2.]));solve(c=np.array([1.1,2.]))
        assert outer.stats()['entries']<=2 and outer.stats()['evictions']>=1
        assert outer.stats()['estimated_bytes']<=32768
        with scope() as inner:
            solve();assert inner.stats()['hits']==0
        assert backend.accepted_result_cache_stats()['scope_id']==outer.stats()['scope_id']
    with scope(max_bytes=1) as small:
        solve();solve()
        assert small.stats()['entries']==0 and small.stats()['oversize_skips']==2
    # The reviewed ceiling increased from 512 to 2048; legacy explicit 512 is retained.
    for kwargs in [dict(max_entries=True),dict(max_entries=2049),dict(max_bytes=33554433),dict(max_bytes=0)]:
        with pytest.raises((ValueError,TypeError)):
            with scope(**kwargs):pass


def test_stable_source_and_complete_policy_fingerprints_are_reused(native,monkeypatch):
    from hospital_ies import solver_result_cache as helper
    code=helper.code_sha;desc=backend.descriptor;code_calls=[];desc_calls=[]
    def counted_code(value):code_calls.append(1);return code(value)
    def counted_desc():desc_calls.append(1);return desc()
    monkeypatch.setattr(helper,'code_sha',counted_code)
    monkeypatch.setattr(backend,'descriptor',counted_desc)
    with scope():
        for _ in range(8):solve()
        assert len(code_calls)==len(desc_calls)==1
        monkeypatch.setitem(backend._COMMON,'Seed',False)  # False must not alias integer zero.
        solve()
        assert len(desc_calls)==2 and len(code_calls)==1
        assert len(native)==2


def test_same_callable_new_code_invalidates_cached_code_certificate():
    from hospital_ies import solver_result_cache as helper
    def function():return 1
    def replacement():return 2
    def policy():return {'policy':1}
    with scope() as cache:
        first=cache.certificates(function,policy,('policy',1))
        function.__code__=replacement.__code__
        second=cache.certificates(function,policy,('policy',1))
        assert first[2]['native_code_sha256']!=second[2]['native_code_sha256']


def test_flat_json_row_signature_is_lossless_and_complex_rows_use_typed_fallback():
    from hospital_ies import solver_result_cache as helper
    plain=[{'kind':'physical','name':'é中文','unit':'kW','source':'quoted " \\ newline\n','value':1}]
    assert helper._row_metadata_signature(plain)[0]=='flat_json_v1'
    signatures=[]
    for value in (1,1.,True,0.,-0.):
        signatures.append(helper._row_metadata_signature([dict(plain[0],value=value)]))
    assert len(set(signatures))==5
    as_list=[dict(plain[0],value=[1,2])];as_tuple=[dict(plain[0],value=(1,2))]
    assert helper._row_metadata_signature(as_list)[0]=='typed_v1'
    assert helper._row_metadata_signature(as_list)!=helper._row_metadata_signature(as_tuple)


def test_capacity_2048_is_explicit_and_legacy_512_still_works(native):
    with scope(max_entries=2048,max_bytes=33554432) as cache:
        for i in range(600):solve(c=np.array([1.+i/10000.,2.]))
        solve()
        assert len(native)==600 and cache.stats()['hits']==1
        assert cache.stats()['entries']==600 and cache.stats()['estimated_bytes']<=33554432
    with scope(max_entries=512) as old:
        assert old.stats()['max_entries']==512


def test_provenance_certificate_binds_current_vector_and_origin(native):
    def digest(value):
        return hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')).hexdigest()
    with scope(scope_id='certificate-test'):
        first,second=solve(),solve()
    for result in (first,second):
        a=result.attempts[0];p=a['result_cache_provenance']
        assert p['raw_x_sha256']==digest(a['raw_x'])
        certificate={k:v for k,v in p.items() if k not in ('mode','native_optimize_performed','call_id',
            'fresh_original_validation','certificate_sha256')}
        assert p['certificate_sha256']==digest(certificate)
        assert p['process_id']==os.getpid() and p['origin_attempt']==1
        assert all(len(value)==64 for value in p['source'].values())
    assert first.attempts[0]['result_cache_provenance']['certificate_sha256']==second.attempts[0]['result_cache_provenance']['certificate_sha256']


def test_small_real_native_hit_preserves_original_solution(monkeypatch):
    # One tiny genuine native LP; second call must reuse its accepted certificate.
    original=backend._gurobi_attempt;calls=[]
    def counted(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(backend,'_gurobi_attempt',counted)
    with scope() as cache:
        first,second=solve(),solve()
        assert first.status==second.status==0 and len(calls)==1
        np.testing.assert_array_equal(first.x,second.x)
        assert cache.stats()['hits']==1


def test_native_code_certificate_is_stable_and_recompilable():
    from pathlib import Path
    from types import CodeType
    from hospital_ies import solver_result_cache as helper
    source=Path(backend.__file__).read_text(encoding='utf8')
    compiled=compile(source,str(Path(backend.__file__).resolve()),'exec')
    code=next(x for x in compiled.co_consts if isinstance(x,CodeType) and x.co_name=='_gurobi_attempt')
    assert helper.code_sha(code)==helper.code_sha(backend._gurobi_attempt.__code__)
    assert helper.code_sha(code)==helper.code_sha(code)


@pytest.mark.parametrize('field,value',[('fun',9.),('mip_gap',.01)])
def test_current_objective_and_gap_are_rechecked(native,monkeypatch,field,value):
    with scope() as cache:
        solve();lookup=cache.lookup
        def altered(key):
            candidate,error=lookup(key)
            if candidate is not None:candidate[0][field]=value
            return candidate,error
        monkeypatch.setattr(cache,'lookup',altered)
        result=solve()
        assert result.status==0 and len(native)==2
        assert result.attempts[0]['native_optimize_performed']
        assert cache.stats()['rejected_hits']==1 and cache.stats()['hits']==0


def test_cache_validation_cannot_overrun_current_budget_and_still_accept(native,monkeypatch):
    original=solver_diagnostics.validate_vector;clock=backend.time.perf_counter;offset=[0.]
    with scope() as cache:
        solve()
        def costly(*args,**kwargs):
            checked=original(*args,**kwargs);offset[0]+=10.;return checked
        monkeypatch.setattr(solver_diagnostics,'validate_vector',costly)
        monkeypatch.setattr(backend.time,'perf_counter',lambda:clock()+offset[0])
        result=solve()
        assert result.status!=0 and len(native)==1
        assert cache.stats()['hits']==0 and cache.stats()['rejected_hits']==1


def test_nonfinite_failed_native_vector_keeps_original_failure_shape(monkeypatch):
    def invalid(problem,parameters):
        return OptimizeResult(status=0,x=np.array([np.nan,0.]),fun=np.nan,mip_dual_bound=None,mip_gap=None,
            native_status=2,message='nonfinite injected native vector',backend='gurobipy')
    monkeypatch.setattr(backend,'_gurobi_attempt',invalid)
    with scope() as cache:
        result=solve()
        assert result.status==4 and len(result.attempts)==2 and cache.stats()['entries']==0
        assert all(a['error_type']=='ValueError' and np.isnan(a['raw_x'][0]) for a in result.attempts)
        assert all(a['result_cache_provenance']['raw_x_sha256'] is None for a in result.attempts)
