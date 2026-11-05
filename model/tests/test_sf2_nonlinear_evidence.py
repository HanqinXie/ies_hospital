"""Exact recoverable NL domains, without a formal trajectory or native solve."""
from copy import deepcopy
from pathlib import Path
import hashlib,json
from types import SimpleNamespace
import numpy as np
import pytest
from scipy import sparse
from scipy.optimize import Bounds,LinearConstraint,OptimizeResult


def test_sink_receives_one_copied_complete_domain_and_each_attempt_keeps_ref(monkeypatch):
    from hospital_ies import nonlinear as nl,solver_backend as backend
    from hospital_ies.solver_diagnostics import solve_layer
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    from hospital_ies.model import load_plant
    root=Path(__file__).resolve().parents[1]
    curve=FuelCurve.from_source(root,load_plant(root/'configs/plant_configs.json','doe_capacity_matched_exploratory'))
    graph=nl.NonlinearSpec((nl.PowerLawEquality(0,1,curve.coefficient(15.),.673,curve.identity),))
    context=nl.NonlinearSourceContext.build(curve=curve,timestamp='2017-01-01 00:00:00',temperature_C=15.,
        role='current_planner',stage='current_plan',layer_name='cost',layer_rank=0,dt_h=.25,
        observed_minute=15.,current_snapshot={'gt_actual_kW':[128.,0.,0.]})
    from functools import partial
    solver=partial(backend.milp,nonlinear=graph)
    native=[];payloads=[];results=[]
    def attempt(problem,parameters):
        native.append(deepcopy(problem))
        raw=np.array([129. if len(native)==1 else 128.,curve.rate(128.,15.)])
        return OptimizeResult(status=0,native_status=2,x=raw,fun=raw[1],mip_dual_bound=raw[1],mip_gap=0.)
    monkeypatch.setattr(backend,'_gurobi_attempt',attempt)
    def sink(payload):
        if payload['schema']=='hospital_ies.sf2.nonlinear_result.v1':
            results.append(deepcopy(payload))
            return {'schema':'hospital_ies.sf2.nonlinear_result_reference.v1','record_id':'nl_1','scope_sequence':payload['scope_sequence'],
                'domain_sha256':payload['domain_reference']['domain_sha256'],'result_sha256':payload['result_sha256'],
                'artifacts':[{'path':'nl/one.result.json.gz','sha256':'b'*64}]}
        payloads.append(deepcopy(payload))
        ref={'schema':'hospital_ies.sf2.nonlinear_domain_reference.v1','record_id':'nl_1',
            'scope_sequence':payload['scope_sequence'],'domain_sha256':payload['domain_sha256'],'artifacts':[{'path':'nl/one.json.gz','sha256':'a'*64}]}
        # A diagnostic consumer receives only detached copies.
        payload['original']['matrix']['data'][0]=999.
        return ref
    with nl.nonlinear_evidence_scope(sink) as session:
        result=nl.exact_solve_layer(solver,np.array([0.,1.]),integrality=np.zeros(2),bounds=Bounds([128.,0.],[128.,10.]),
            constraints=[LinearConstraint([[1.,0.]],[128.],[128.])],options={'time_limit':6.,'mip_rel_gap':1e-6},
            rank=0,variables=[{'index':i,'name':str(i)} for i in range(2)],rows=[{'kind':'physical','name':'fixed_GP'}],
            locks=[],input_context={},nonlinear_context=context)
    assert result.status==0 and len(payloads)==1 and len(native)==2
    p=payloads[0]
    assert p['original']['matrix']['shape']==[1,2] and p['original']['matrix']['data']==[1.]
    assert p['native']['matrix']['data']==[1.] and native[0]['matrix'][0,0]==1.
    assert p['source_context']['temperature_C']==15. and p['source_context']['observed_minute']==15.
    assert p['source_context']['curve_identity']==curve.identity
    assert p['objective']==[0.,1.] and p['variables'][0]['index']==0 and p['locks']==[]
    assert result.attempts[0]['nonlinear_domain_evidence']['reference']['domain_sha256']==p['domain_sha256']
    assert result.attempts[0]['nonlinear_domain_evidence']['status']=='recorded'
    assert all(a['nonlinear_domain_evidence']['reference']==result.attempts[0]['nonlinear_domain_evidence']['reference'] for a in result.attempts)
    assert result.attempts[0]['accepted'] is False and result.attempts[1]['accepted'] is True
    assert session['status']=='closed_completed' and session['call_count']==session['completed_call_count']==1
    assert len(results)==1 and len(results[0]['attempts'])==2
    assert results[0]['attempts'][0]['raw_x'][0]==129.


@pytest.mark.parametrize('failure',['missing_context','sink_error','missing_ref','bad_digest'])
def test_required_sink_failure_never_calls_optimizer(monkeypatch,failure):
    from hospital_ies import nonlinear as nl,solver_backend as backend
    from hospital_ies.model import load_plant
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    from functools import partial
    root=Path(__file__).resolve().parents[1]
    curve=FuelCurve.from_source(root,load_plant(root/'configs/plant_configs.json','doe_capacity_matched_exploratory'))
    graph=nl.NonlinearSpec((nl.PowerLawEquality(0,1,curve.coefficient(15.),.673,curve.identity),))
    context=nl.NonlinearSourceContext.build(curve=curve,timestamp='2017-01-01',temperature_C=15.,
        role='free_reference_probe',stage='test',layer_name='cost',layer_rank=0,dt_h=.25,
        observed_minute=None,current_snapshot={})
    def forbidden(*args,**kwargs):pytest.fail('unqualified domain cannot reach optimize')
    monkeypatch.setattr(backend,'_gurobi_attempt',forbidden)
    def sink(payload):
        if failure=='sink_error':raise OSError('native evidence write failed')
        if failure=='missing_ref':return None
        return {'schema':'hospital_ies.sf2.nonlinear_domain_reference.v1','record_id':'bad',
            'domain_sha256':'0'*64,'artifacts':[{'path':'one.npz','sha256':'b'*64}]}
    with pytest.raises((ValueError,OSError),match='context|evidence|reference|digest'):
        with nl.nonlinear_evidence_scope(sink):
            if failure=='missing_context':backend.milp([0.,1.],bounds=Bounds([0.,0.],[320.,10.]),nonlinear=graph)
            else:
                nl.exact_solve_layer(partial(backend.milp,nonlinear=graph),np.array([0.,1.]),integrality=np.zeros(2),
                    bounds=Bounds([0.,0.],[320.,10.]),constraints=[LinearConstraint([[0.,0.]],[-np.inf],[np.inf])],options={'time_limit':6.},rank=0,
                    variables=[],rows=[{'kind':'physical','name':'empty'}],locks=[],input_context={},nonlinear_context=context)


@pytest.mark.parametrize('failure',['write_error','missing_ref','wrong_digest','mutate_copy'])
def test_completion_io_and_reference_failure_never_publish_native_success(monkeypatch,tmp_path,failure):
    from hospital_ies import nonlinear as nl,solver_backend as backend
    from hospital_ies.model import load_plant
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    from sf2_evidence_fixture import make_domain_sink
    from functools import partial
    root=Path(__file__).resolve().parents[1]
    curve=FuelCurve.from_source(root,load_plant(root/'configs/plant_configs.json','doe_capacity_matched_exploratory'))
    graph=nl.NonlinearSpec((nl.PowerLawEquality(0,1,curve.coefficient(15.),.673,curve.identity),))
    context=nl.NonlinearSourceContext.build(curve=curve,timestamp='2017-01-01',temperature_C=15.,role='free_reference_probe',
        stage='test',layer_name='cost',layer_rank=0,dt_h=.25,observed_minute=None,current_snapshot={})
    native=OptimizeResult(status=0,native_status=2,x=np.zeros(2),fun=0.,mip_dual_bound=0.,mip_gap=0.)
    monkeypatch.setattr(backend,'_gurobi_attempt',lambda *a,**k:native)
    base,records=make_domain_sink(tmp_path/'evidence')
    def sink(payload):
        if payload['schema'].endswith('nonlinear_result.v1'):
            if failure=='write_error':raise OSError('result disk write error')
            if failure=='mutate_copy':payload['raw_x'][0]=99.
            ref=base(payload)
            if failure=='missing_ref':return None
            if failure=='wrong_digest':ref['result_sha256']='0'*64
            return ref
        return base(payload)
    with pytest.raises(nl.NonlinearEvidenceError,match='evidence'):
        with nl.nonlinear_evidence_scope(sink) as session:
            nl.exact_solve_layer(partial(backend.milp,nonlinear=graph),np.array([0.,1.]),integrality=np.zeros(2),
                bounds=Bounds([0.,0.],[320.,10.]),constraints=[LinearConstraint([[0.,0.]],[-np.inf],[np.inf])],
                options={'time_limit':6.},rank=0,variables=[],rows=[{'kind':'physical','name':'empty'}],locks=[],
                input_context={},nonlinear_context=context)
    assert session['status']=='closed_failed' and session['call_count']==1 and session['completed_call_count']==0
    assert native.x.tolist()==[0.,0.], 'sink receives a detached raw vector copy'
