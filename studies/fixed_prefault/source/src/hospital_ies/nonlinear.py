"""Typed SF2 power-law equalities and independent analytic NL acceptance.

The shadow vector is validation-only. It is never returned as a repaired native
solution. Every original lock is checked after substituting true fuel, including
earlier cost locks when the current objective only counts GTs.
"""
from dataclasses import asdict,dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import math
import re
import time
import numpy as np

_EVIDENCE_SINK=ContextVar('hospital_ies_sf2_nonlinear_evidence_sink',default=None)
_LAYER_CONTEXT=ContextVar('hospital_ies_sf2_nonlinear_layer_context',default=None)


class NonlinearEvidenceError(ValueError):
    """Required NL domain I/O/identity failure; never an optional probe result."""


def _evidence_error(message):
    error=NonlinearEvidenceError(message)
    state=_EVIDENCE_SINK.get()
    if state is not None and state['failure'] is None:
        state['failure']=error;state['summary']['error']=message
    return error


def _canonical(value):
    from .solver_diagnostics import json_value
    return json.dumps(json_value(value),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')


@dataclass(frozen=True)
class NonlinearSourceContext:
    timestamp: str
    temperature_C: float
    role: str
    stage: str
    layer_name: str
    layer_rank: int
    dt_h: float
    observed_minute: float|None
    curve_identity: str
    curve_config_json: str
    current_snapshot_json: str

    def __post_init__(self):
        if (not isinstance(self.timestamp,str) or not self.timestamp or self.role not in ('current_planner','free_reference_probe')
                or not isinstance(self.stage,str) or not self.stage or not isinstance(self.layer_name,str) or not self.layer_name
                or type(self.layer_rank) is not int or self.layer_rank<0
                or type(self.temperature_C) not in (int,float) or not math.isfinite(self.temperature_C)
                or type(self.dt_h) not in (int,float) or not math.isfinite(self.dt_h) or self.dt_h<=0.
                or self.observed_minute is not None and (type(self.observed_minute) not in (int,float)
                    or not math.isfinite(self.observed_minute) or self.observed_minute<0.)):
            raise ValueError('Explicit current-source NL context required')
        curve=json.loads(self.curve_config_json);snapshot=json.loads(self.current_snapshot_json)
        # Curve identity uses the fuel contract's canonical ASCII JSON encoding.
        if (type(curve) is not dict or type(snapshot) is not dict
                or hashlib.sha256(json.dumps(curve,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()!=self.curve_identity):
            raise ValueError('NL evidence curve/source context digest mismatch')

    @classmethod
    def build(cls,*,curve,current_snapshot,**kwargs):
        return cls(**kwargs,curve_identity=curve.identity,curve_config_json=_canonical(curve.as_dict()).decode(),
            current_snapshot_json=_canonical(current_snapshot).decode())

    def as_dict(self):
        value=asdict(self)
        value['curve']=json.loads(value.pop('curve_config_json'))
        value['current_snapshot']=json.loads(value.pop('current_snapshot_json'))
        value['schema']='hospital_ies.sf2.nonlinear_source_context.v1'
        group={key:value[key] for key in ('timestamp','temperature_C','role','dt_h','observed_minute','curve_identity','current_snapshot')}
        value['candidate_group_sha256']=hashlib.sha256(_canonical(group)).hexdigest()
        value['context_sha256']=hashlib.sha256(_canonical(value)).hexdigest()
        return value


@contextmanager
def nonlinear_evidence_scope(sink):
    """Explicit F3 sink: write one detached full domain per NL solver call.

    A sink is mandatory for admitted trajectories, not installed by unit tests.
    It owns CAS/NPZ/index I/O; the core does not write trajectory files here.
    """
    if not callable(sink):raise ValueError('Callable explicit NL evidence sink required')
    if _EVIDENCE_SINK.get() is not None:raise _evidence_error('Nested NL evidence scopes are not permitted')
    summary=dict(schema='hospital_ies.sf2.nonlinear_evidence_session.v1',status='open',call_count=0,
        completed_call_count=0,records=[],error=None)
    state=dict(sink=sink,failure=None,summary=summary,record_ids=set())
    token=_EVIDENCE_SINK.set(state)
    try:
        yield summary
        if state['failure'] is not None:raise state['failure']
        if summary['completed_call_count']!=summary['call_count']:
            raise _evidence_error('NL evidence session has incomplete call results')
        summary['status']='closed_completed'
    except BaseException as error:
        summary['status']='closed_failed';summary['error']=str(error)
        raise
    finally:_EVIDENCE_SINK.reset(token)


def _domain(value):
    matrix=value['matrix'].tocsr()
    return dict(matrix=dict(format='csr',shape=list(matrix.shape),data=matrix.data.tolist(),
        indices=matrix.indices.tolist(),indptr=matrix.indptr.tolist()),
        **{key:np.asarray(value[key]).tolist() for key in
            ('lower','upper','bounds_lower','bounds_upper','integrality')})


def emit_nonlinear_domain(problem,original,options):
    """Return a small reference; never put a full matrix in every attempt."""
    started=time.perf_counter();state=_EVIDENCE_SINK.get();info=_LAYER_CONTEXT.get()
    context=None if info is None else info['source_context']
    if state is None:
        return dict(status='evidence_not_requested',required_scope_active=False,
            source_context=None if context is None else context.as_dict(),
            capture_seconds=time.perf_counter()-started,independent_domain_replay_qualified=False)
    state['summary']['call_count']+=1
    sequence=state['summary']['call_count']
    if type(context) is not NonlinearSourceContext:
        raise _evidence_error('NL evidence scope requires source/layer context before optimization')
    graph=problem['nonlinear']
    if any(v.curve_identity!=context.curve_identity for v in graph.equalities):
        raise _evidence_error('NL evidence source context differs from graph curve identity')
    payload=dict(schema='hospital_ies.sf2.nonlinear_domain.v1',source_context=context.as_dict(),
        nonlinear_graph=graph.as_dict(),original=_domain(original),native=_domain(problem),
        objective=np.asarray(problem['c']).tolist(),objective_constant=0.,options=deepcopy(options),
        rows=deepcopy(original['rows']),variables=deepcopy(info['variables']),locks=deepcopy(info['locks']),
        matrix_semantics='original unscaled domain and exact native presented domain; no primal repair',
        infinity_encoding='canonical JSON strings +Infinity/-Infinity; native arrays may use IEEE infinity')
    payload=json.loads(_canonical(payload))
    domain_sha=hashlib.sha256(_canonical(payload)).hexdigest();payload['domain_sha256']=domain_sha
    payload['scope_sequence']=sequence  # Transport identity is outside the deduplicated domain hash.
    try:reference=state['sink'](deepcopy(payload))
    except Exception as error:raise _evidence_error('NL evidence sink write failed: '+str(error)) from error
    if (type(reference) is not dict or reference.get('schema')!='hospital_ies.sf2.nonlinear_domain_reference.v1'
            or reference.get('domain_sha256')!=domain_sha or not isinstance(reference.get('record_id'),str)
            or not reference['record_id'] or reference['record_id'] in state['record_ids']
            or type(reference.get('scope_sequence')) is not int or reference['scope_sequence']!=sequence
            or not isinstance(reference.get('artifacts'),list) or not reference['artifacts']):
        raise _evidence_error('NL evidence sink missing valid exact domain reference/digest')
    for item in reference['artifacts']:
        if (type(item) is not dict or not isinstance(item.get('path'),str) or not item['path']
                or not isinstance(item.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',item['sha256'])):
            raise _evidence_error('NL evidence reference requires artifact paths and SHA256')
    state['record_ids'].add(reference['record_id'])
    state['summary']['records'].append(dict(scope_sequence=sequence,domain_reference=deepcopy(reference),result_reference=None))
    return dict(status='recorded',required_scope_active=True,reference=deepcopy(reference),
        source_context_sha256=context.as_dict()['context_sha256'],capture_seconds=time.perf_counter()-started,
        domain_sha256=domain_sha,independent_domain_replay_qualified=False,
        qualification='complete domain exposed; independent artifact/hash/numerical acceptance remains required')


def complete_nonlinear_call(result,evidence):
    """Retain complete attempts even for reset/free leaves with no old journal."""
    state=_EVIDENCE_SINK.get()
    if state is None:return
    started=time.perf_counter();reference=evidence['reference'];sequence=reference['scope_sequence']
    value=dict(schema='hospital_ies.sf2.nonlinear_result.v1',scope_sequence=sequence,
        domain_reference=deepcopy(reference),source_context_sha256=evidence['source_context_sha256'],
        status=int(result.status),raw_x=None if result.x is None else np.asarray(result.x).tolist(),
        native_fun=getattr(result,'fun',None),mip_dual_bound=getattr(result,'mip_dual_bound',None),
        mip_gap=getattr(result,'mip_gap',None),attempts=deepcopy(result.attempts),
        solver_elapsed_seconds=getattr(result,'elapsed_seconds',None),
        mathematical_result_or_raw_primal_changed=False)
    value=json.loads(_canonical(value));result_sha=hashlib.sha256(_canonical(value)).hexdigest()
    value['result_sha256']=result_sha
    try:ref=state['sink'](deepcopy(value))
    except Exception as error:raise _evidence_error('NL evidence result write failed: '+str(error)) from error
    if (type(ref) is not dict or ref.get('schema')!='hospital_ies.sf2.nonlinear_result_reference.v1'
            or ref.get('scope_sequence')!=sequence or ref.get('record_id')!=reference['record_id']
            or ref.get('result_sha256')!=result_sha or ref.get('domain_sha256')!=reference['domain_sha256']
            or type(ref.get('artifacts')) is not list or not ref['artifacts']):
        raise _evidence_error('NL evidence sink missing valid exact result reference/digest')
    for item in ref['artifacts']:
        if (type(item) is not dict or not isinstance(item.get('path'),str) or not item['path']
                or not isinstance(item.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',item['sha256'])):
            raise _evidence_error('NL evidence result reference requires artifact paths and SHA256')
    record=state['summary']['records'][sequence-1]
    if record['result_reference'] is not None:raise _evidence_error('Duplicate NL call result')
    record['result_reference']=deepcopy(ref);state['summary']['completed_call_count']+=1
    evidence['result_reference']=deepcopy(ref)
    evidence['completion_capture_seconds']=time.perf_counter()-started


@dataclass(frozen=True)
class PowerLawEquality:
    input_index: int
    output_index: int
    coefficient: float
    exponent: float
    curve_identity: str

    def __post_init__(self):
        if (any(type(v) is not int or v<0 for v in (self.input_index,self.output_index))
                or self.input_index==self.output_index or type(self.coefficient) not in (int,float)
                or not math.isfinite(self.coefficient) or self.coefficient<=0.
                or type(self.exponent) not in (int,float) or self.exponent != .673
                or not isinstance(self.curve_identity,str) or not re.fullmatch('[0-9a-f]{64}',self.curve_identity)):
            raise ValueError('Exact source-bound SF2 power-law equality required')


@dataclass(frozen=True)
class NonlinearSpec:
    equalities: tuple
    contract: str = 'hospital_ies.sf2.exact_power_graph.v1'

    def __post_init__(self):
        if (type(self.equalities) is not tuple or not 1<=len(self.equalities)<=3
                or any(type(v) is not PowerLawEquality for v in self.equalities)
                or self.contract!='hospital_ies.sf2.exact_power_graph.v1'):
            raise ValueError('Typed immutable one-to-three GT power graphs required')
        inputs=[v.input_index for v in self.equalities];outputs=[v.output_index for v in self.equalities]
        if len(set(inputs))!=len(inputs) or len(set(outputs))!=len(outputs) or set(inputs)&set(outputs):
            raise ValueError('Fuel coordinates must be unique and cannot feed another graph')

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

    def as_dict(self):return dict(asdict(self),identity=self.identity)

    def validate_domain(self,lower,upper):
        n=len(lower)
        for item in self.equalities:
            if max(item.input_index,item.output_index)>=n:
                raise ValueError('NL coordinate outside original variable domain')
            if lower[item.input_index]<0. or not math.isfinite(upper[item.input_index]) or lower[item.output_index]<0.:
                raise ValueError('SF2 power graph needs a bounded nonnegative power domain')


def pricing_shadow(spec,raw):
    """Analytic objective/lock evaluation only; never replaces a native primal."""
    if type(spec) is not NonlinearSpec:raise ValueError('Typed nonlinear spec required')
    true=np.asarray(raw,dtype=float).copy()
    if not np.isfinite(true).all():raise ValueError('Finite native NL primal required')
    for item in spec.equalities:
        power=float(true[item.input_index])
        if power<0.:raise ValueError('Negative native power cannot be clipped for a fractional fuel graph')
        true[item.output_index]=item.coefficient*power**item.exponent
    return true


def exact_solve_layer(solver,c,**kwargs):
    """Bypass linear-only incumbent interpolation while retaining diagnostics."""
    from .solver_diagnostics import solve_layer
    kwargs.pop('previous_candidate',None)
    context=kwargs.pop('nonlinear_context',None)
    if context is not None and (type(context) is not NonlinearSourceContext or context.layer_rank!=kwargs['rank']):
        raise ValueError('Exact NL layer source context/rank mismatch')
    token=_LAYER_CONTEXT.set(dict(source_context=context,variables=deepcopy(kwargs['variables']),locks=deepcopy(kwargs['locks'])))
    try:return solve_layer(solver,c,**kwargs)
    finally:_LAYER_CONTEXT.reset(token)


def evaluate_nonlinear(spec,raw,c,original):
    from .solver_diagnostics import validate_vector
    if type(spec) is not NonlinearSpec:raise ValueError('Typed nonlinear spec required')
    x=np.asarray(raw,dtype=float).copy();true=pricing_shadow(spec,x);residuals=[]
    spec.validate_domain(original['bounds_lower'],original['bounds_upper'])
    if not np.isfinite(x).all():raise ValueError('Finite native NL primal required')
    for item in spec.equalities:
        power=float(x[item.input_index])
        if power<0.:raise ValueError('Negative native power cannot be clipped for a fractional fuel graph')
        value=item.coefficient*power**item.exponent
        residuals.append(dict(input_index=item.input_index,output_index=item.output_index,
            actual_power_kW=power,raw_fuel_MMBtu_h=float(x[item.output_index]),
            analytic_fuel_MMBtu_h=value,residual_MMBtu_h=float(x[item.output_index]-value)))
        true[item.output_index]=value
    validation=validate_vector(original['matrix'],original['lower'],original['upper'],original['bounds_lower'],
        original['bounds_upper'],original['integrality'],true,original['rows'])
    maximum=max(abs(item['residual_MMBtu_h']) for item in residuals)
    return dict(raw_x=x.tolist(),raw_nonlinear_max_residual=maximum,equalities=residuals,
        true_objective=float(np.asarray(c)@true),raw_objective=float(np.asarray(c)@x),true_validation=validation,
        accepted=bool(maximum<=1e-7 and validation['max_original_violation']<=1e-7
            and validation['max_lock_violation']<=1e-10 and validation['max_integrality_distance']<=1e-9),
        validation_shadow_only=True,native_primal_repaired=False)
