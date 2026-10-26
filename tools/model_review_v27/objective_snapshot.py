"""Bound M25 execution snapshots for offline A0/A1 experiments, never policy inputs."""
from __future__ import annotations
import copy,importlib,json,math
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from tools.integrated_learning.snapshot import digest,rng_state,restore_rng

SCHEMA='hospital_ies.v27.complete_execution_snapshot.v1'
SHARED={'root','hourly','zones','groups','plant','thermal_cache'}
ALIASES={'event_history','segment_history','solver_errors'}
IO={'_solver_row_sidecar','domain_evidence','actual_segment_observer'}
PHYSICAL_CLASSES={
 'hospital_ies.resilience.RuntimeState',
 'tools.sci_engineering.storage.FiniteStore',
 'tools.operating_response.diesel.DieselRotor',
 'tools.engineering_model.execution.GTClockLedger',
 'tools.model_closure_v22.station.StationState',
 'tools.model_closure_v22.storage.ColdRechargeState',
 'tools.model_correctness_v23.protection.RestartState',
 'types.SimpleNamespace'}
OFFLINE={'labels','first_failure_observation_interval','post_outcome','outcome_mask',
 'observed_subintervals','failure_already_observed','recovered_at_decision','command_generated','actual_execution'}

def _class_name(value):return type(value).__module__+'.'+type(value).__qualname__
def _core_type(template):
    core=getattr(template,'core',None)
    if core is not None:return type(core)
    result=getattr(template,'engineering_core_type',None)
    if not isinstance(result,type):raise ValueError('Template requires an explicit original engineering core type')
    return result

def _encode(value):
    if isinstance(value,np.ndarray):
        if value.dtype.hasobject:raise TypeError('Unregistered snapshot object array')
        return {'type':'array','dtype':str(value.dtype),'shape':list(value.shape),'value':_encode(value.tolist())}
    if isinstance(value,np.generic):return _encode(value.item())
    if isinstance(value,dict):return {'type':'dict','value':[[_encode(k),_encode(v)] for k,v in value.items()]}
    if isinstance(value,tuple):return {'type':'tuple','value':[_encode(x) for x in value]}
    if isinstance(value,set):return {'type':'set','value':sorted([_encode(x) for x in value],key=lambda x:json.dumps(x,sort_keys=True))}
    if isinstance(value,list):return [_encode(x) for x in value]
    if isinstance(value,Path):return {'type':'path','value':str(value)}
    if isinstance(value,float) and not math.isfinite(value):return {'type':'nonfinite','value':'nan' if math.isnan(value) else 'inf' if value>0 else '-inf'}
    if isinstance(value,np.random.Generator):return {'type':'rng','class':type(value.bit_generator).__name__,'value':_encode(value.bit_generator.state)}
    if _class_name(value) in PHYSICAL_CLASSES:return {'type':'object','class':_class_name(value),'value':_encode(vars(value))}
    if value is None or isinstance(value,(str,bool,int,float)):return value
    raise TypeError('Unregistered snapshot mutable class: '+_class_name(value))

def _decode(value):
    if isinstance(value,list):return [_decode(x) for x in value]
    if not isinstance(value,dict):return value
    kind=value['type'];data=value.get('value')
    if kind=='dict':return {_decode(k):_decode(v) for k,v in data}
    if kind=='tuple':return tuple(_decode(x) for x in data)
    if kind=='set':return set(_decode(x) for x in data)
    if kind=='array':return np.asarray(_decode(data),dtype=value['dtype']).reshape(value['shape'])
    if kind=='path':return Path(data)
    if kind=='nonfinite':return {'nan':float('nan'),'inf':float('inf'),'-inf':float('-inf')}[data]
    if kind=='rng':
        if value['class'] not in {'PCG64','PCG64DXSM','MT19937','Philox','SFC64'}:raise ValueError('Unregistered RNG')
        result=np.random.Generator(getattr(np.random,value['class'])());result.bit_generator.state=_decode(data);return result
    if kind=='object':
        name=value['class']
        if name not in PHYSICAL_CLASSES:raise ValueError('Unregistered physical snapshot class')
        module,cls=name.rsplit('.',1);class_type=getattr(importlib.import_module(module),cls)
        result=class_type.__new__(class_type);result.__dict__.update(_decode(data));return result
    raise ValueError('Unknown snapshot type '+str(kind))

def validate_legal_history(history,current,minute):
    previous=-float('inf')
    for row in history:
        if not isinstance(row,dict) or OFFLINE&set(row):raise ValueError('Offline-labelled history is not causal policy history')
        t=float(row['decision_time_min'])
        if not previous<t<minute:raise ValueError('Policy history must be strictly before current decision')
        previous=t
    if not isinstance(current,dict) or OFFLINE&set(current):raise ValueError('Offline current observation is not causal')
    if float(current['decision_time_min'])!=minute:raise ValueError('Current decision clock mismatch')

class BoundExecutionSnapshot:
    def __init__(self,document,template_env):
        self.document=copy.deepcopy(document);self.template_type=type(template_env);self.core_type=_core_type(template_env)
        self.shared={k:getattr(template_env,k) for k in document['coverage']['shared_fields']}

    @property
    def content_sha256(self):return digest(self.document)
    @property
    def physical_state_sha256(self):
        return digest({k:self.document[k] for k in ['minute','environment_type','core_type','dynamic','physical_state','core_state','rng']})
    @property
    def minute(self):return self.document['minute']
    @property
    def legal_history(self):return _decode(self.document['legal_history'])
    @property
    def current_observation(self):return _decode(self.document['current_observation'])
    @property
    def binding(self):return copy.deepcopy(self.document['binding'])

    def save(self,path):
        path=Path(path)
        with path.open('x',encoding='utf8') as f:json.dump({'payload':self.document,'content_sha256':self.content_sha256},f,ensure_ascii=False,allow_nan=False)

    @classmethod
    def load(cls,path,*,template_env,binding):
        wrapper=json.loads(Path(path).read_text(encoding='utf8'));d=wrapper['payload']
        if digest(d)!=wrapper['content_sha256'] or d.get('schema')!=SCHEMA:raise ValueError('Snapshot content SHA/schema mismatch')
        if d['binding']!=binding:raise ValueError('Snapshot source/input/config binding mismatch')
        core_type=_core_type(template_env)
        if d['environment_type']!=_class_name(template_env) or d['core_type']!=core_type.__module__+'.'+core_type.__qualname__:raise ValueError('Snapshot environment/core type mismatch')
        validate_legal_history(_decode(d['legal_history']),_decode(d['current_observation']),d['minute'])
        return cls(d,template_env)

    def fork(self,*,io_resources=None,restore_random=True):
        resources=dict(io_resources or {});required={k for k,v in self.document['external_resources'].items() if v['required_rebind']}
        missing={k for k in required if resources.get(k) is None}
        if set(resources)-IO or missing:raise ValueError('Snapshot external resource requires explicit rebind: '+str(sorted(missing)))
        for key,value in resources.items():
            prior=self.document['external_resources'].get(key,{})
            old_path=prior.get('original_path');new_path=getattr(value,'path',None)
            if old_path and new_path and Path(old_path).name==Path(new_path).name:raise ValueError('Rebound sidecar needs a distinct basename from original prefix references')
            old_dir=prior.get('original_directory');new_dir=getattr(value,'directory',None)
            if old_dir and new_dir and Path(old_dir).resolve()==Path(new_dir).resolve():raise ValueError('Rebound domain resource cannot write the original directory')
        env=self.template_type.__new__(self.template_type);env.__dict__.update(self.shared);env.__dict__.update(_decode(self.document['dynamic']))
        env.state=_decode(self.document['physical_state']);env.core=object.__new__(self.core_type);env.core.__dict__.update(_decode(self.document['core_state']));env.core.env=env
        for key in self.document['external_resources']:setattr(env,key,resources.get(key))
        # Keep every historical call/ref unchanged. Rebuild only the sink
        # behavior for new appends, bypassing constructors that re-log prefix.
        kind=self.document['solver_log_type'];calls=env.core.solver_calls
        if kind!='builtins.list':
            from tools.sci_engineering.solver_log import SolverCallLog
            from tools.model_correctness_v23.evidence import _AllRowsOrderedLog,_AllRowsLog
            classes={c.__module__+'.'+c.__qualname__:c for c in [SolverCallLog,_AllRowsOrderedLog,_AllRowsLog]}
            if kind not in classes:raise ValueError('Unregistered solver-log behavior '+kind)
            log=list.__new__(classes[kind]);list.__init__(log,calls)
            if issubclass(classes[kind],SolverCallLog):
                if getattr(env,'_solver_row_sidecar',None) is None:raise ValueError('Solver log needs rebound writer')
                log.writer=env._solver_row_sidecar
            if kind in {c.__module__+'.'+c.__qualname__ for c in [_AllRowsOrderedLog,_AllRowsLog]}:
                if getattr(env,'domain_evidence',None) is None:raise ValueError('Solver log needs rebound domain resource')
                log.store=env.domain_evidence
            env.core.solver_calls=log
        env.event_history=env.core.events;env.segment_history=env.core.segments;env.solver_errors=env.core.errors
        if restore_random:restore_rng(_decode(self.document['rng']))
        return env

def capture_execution(env,*,binding,legal_history,current_observation,export_prefix=None):
    if not binding or getattr(env,'core',None) is None:raise ValueError('Bound initialized environment required')
    minute=float(env.core.time);validate_legal_history(legal_history,current_observation,minute)
    fields=set(vars(env));core_fields=set(vars(env.core));external={}
    for k in sorted(fields&IO):
        value=getattr(env,k);external[k]=dict(type=_class_name(value),required_rebind=value is not None,
            original_directory=str(getattr(value,'directory','')) if value is not None else None,
            original_path=str(getattr(value,'path','')) if value is not None else None)
    dynamic={k:v for k,v in vars(env).items() if k not in SHARED|ALIASES|IO|{'state','core'}}
    core={k:(list(v) if k=='solver_calls' else v) for k,v in vars(env.core).items() if k!='env'}
    d=dict(schema=SCHEMA,binding=copy.deepcopy(binding),minute=minute,environment_type=_class_name(env),core_type=_class_name(env.core),
        dynamic=_encode(dynamic),physical_state=_encode(env.state),core_state=_encode(core),rng=_encode(rng_state()),
        legal_history=_encode(legal_history),current_observation=_encode(current_observation),export_prefix=_encode(export_prefix or {}),external_resources=external,solver_log_type=_class_name(env.core.solver_calls),
        coverage=dict(environment_fields=sorted(fields),dynamic_fields=sorted(dynamic),shared_fields=sorted(fields&SHARED),external_io_fields=sorted(external),alias_fields=sorted(fields&ALIASES),physical_state_field='state',core_fields=sorted(core_fields-{'env'}),core_back_reference='env',historical_solver_refs_preserved=True))
    return BoundExecutionSnapshot(d,env)
