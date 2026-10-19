"""Small v12 adapter over the existing selective M0 attempts/audit runner."""
from __future__ import annotations
import copy
import json
from pathlib import Path

from tools.autonomous_dispatch import closed_loop as base
from tools.autonomous_dispatch import closed_loop_v5 as legacy
from .interface import (ACTION_SCHEMA, NUMERICAL_CONTRACT_ID, STANDBY_CONTRACT_ID,
    SERVICE_SWITCH_CONTRACT_ID, PLANNING_INTERFACE, CoordinatedHospitalIESEnv,
    _require_contracts, run_policy_episode, validate_action)
from .policy import CoordinatedDispatchPolicy

ROOT=base.ROOT
SCHEMA='coordinated_dispatch.matched_full_feedback.v1'
AUDIT_SCHEMA='coordinated_dispatch.matched_batch_audit.v1'
SOURCE_FILES=tuple(sorted(set(legacy.SOURCE_FILES)|{
    f'tools/coordinated_dispatch/{name}.py' for name in ('planner','policy','interface','closed_loop')}))
_sha,_read,_new_json,_isolated=base._sha,base._read,base._new_json,base._isolated
window_targets=base.window_targets

def _source_binding():return {name:_sha(ROOT/name) for name in SOURCE_FILES}

def _factory(spec):
    if spec['kind']=='coordinated':
        return CoordinatedDispatchPolicy(copy.deepcopy(spec['config']),spec['policy_id'])
    return legacy._factory(spec)

def _check_specs(policy_specs):
    specs=copy.deepcopy(list(policy_specs))
    if not specs or len(specs)>24 or any(not isinstance(s,dict) for s in specs):
        raise ValueError('Registered finite policy panel required')
    ids=[s.get('policy_id') for s in specs]
    if any(not isinstance(p,str) or not p for p in ids) or len(ids)!=len(set(ids)):
        raise ValueError('Distinct registered policy identities required')
    original={'priority':{'policy_id':'priority_milp','kind':'priority'},
              'secure':{'policy_id':'secure_milp','kind':'secure'}}
    for spec in specs:
        kind=spec.get('kind');pid=spec['policy_id']
        if kind in original:
            if spec!=original[kind]:raise ValueError('Original baseline identity changed')
        elif kind in {'state_dispatch','joint','coordinated'}:
            if (set(spec)!={'policy_id','kind','config'} or not isinstance(spec['config'],dict)
                    or pid in {'priority_milp','secure_milp'}):
                raise ValueError('Separate policy identity and explicit config required')
            if pid=='execution_reserve' and (kind!='state_dispatch' or spec['config']!={}):
                raise ValueError('Historical execution_reserve identity changed')
            json.dumps(spec['config'],allow_nan=False)
        else:raise ValueError('Unknown registered policy kind')
        _factory(spec)
    return specs

def _environment_factory(plan,manifest):
    _require_contracts(manifest)
    if manifest.get('execution_model_id')!='M0':raise ValueError('Main v12 research uses unchanged M0')
    namespace=dict(base._environment_factory.__globals__,SelectiveHospitalIESEnv=CoordinatedHospitalIESEnv)
    return _isolated(base._environment_factory,namespace)(plan,manifest)

def _payload_binding(payload,scenario,manifest):
    result=base._payload_binding(payload,scenario,manifest)
    result['checks'].update(
        planning_interface_equal=payload.get('planning_interface')==manifest.get('planning_interface_id')==PLANNING_INTERFACE,
        numerical_contract_equal=payload.get('numerical_contract_id')==manifest.get('numerical_contract_id')==NUMERICAL_CONTRACT_ID,
        standby_contract_equal=payload.get('standby_contract_id')==manifest.get('standby_contract_id')==STANDBY_CONTRACT_ID,
        service_switch_contract_equal=payload.get('service_switch_contract_id')==manifest.get('service_switch_contract_id')==SERVICE_SWITCH_CONTRACT_ID,
        action_schema_equal=manifest.get('schemas',{}).get('action')==ACTION_SCHEMA)
    result['passed']=all(result['checks'].values())
    return result

def _globals():
    return dict(base.__dict__,ROOT=ROOT,SCHEMA=SCHEMA,AUDIT_SCHEMA=AUDIT_SCHEMA,
        SOURCE_FILES=SOURCE_FILES,_source_binding=_source_binding,_factory=_factory,
        _check_specs=_check_specs,_runner_namespace=_runner_namespace,verify_batch=verify_batch,
        _recover_interrupted=_recover_interrupted,_summary=_summary,
        run_policy_episode=run_policy_episode,_environment_factory=_environment_factory,
        check_confirmation_freeze=check_confirmation_freeze,_payload_binding=_payload_binding)

def check_confirmation_freeze(plan,manifest,root=ROOT):
    _isolated(base.check_confirmation_freeze,_globals())(plan,manifest,root)
    if plan.get('phase')!='confirmation':return
    preference=_read(plan['preference_path'])
    path=Path(preference['confirmation_gate']['policy_freeze_path'])
    if not path.is_absolute():path=Path(root)/path
    freeze=_read(path)
    for key,value in (('numerical_contract_id',NUMERICAL_CONTRACT_ID),
        ('service_switch_contract_id',SERVICE_SWITCH_CONTRACT_ID),('planning_interface_id',PLANNING_INTERFACE)):
        if freeze.get(key)!=value or manifest.get(key)!=value:
            raise ValueError('Frozen service/switch method contract differs')

def _runner_namespace(environment=None):
    namespace=base._runner_namespace(environment)
    namespace.update(SCHEMA=SCHEMA,_source_binding=_source_binding,_check_specs=_check_specs,
        _factory=_factory,verify_regime_batch=verify_batch,_recover_interrupted=_recover_interrupted,
        _summary=_summary,run_policy_episode=run_policy_episode,_payload_binding=_payload_binding)
    return namespace

def register_batch(manifest_path,plan_path,**options):
    _require_contracts(_read(manifest_path))
    return _isolated(base.register_batch,_globals())(manifest_path,plan_path,**options)

def verify_batch(*args,**kwargs):
    plan,manifest=_isolated(base.verify_batch,_globals())(*args,**kwargs)
    _require_contracts(manifest)
    return plan,manifest

def _recover_interrupted(*args,**kwargs):return _isolated(base._recover_interrupted,_globals())(*args,**kwargs)
def _summary(*args,**kwargs):return _isolated(base._summary,_globals())(*args,**kwargs)
def run_batch(*args,**kwargs):return _isolated(base.run_batch,_globals())(*args,**kwargs)
def audit_batch(*args,**kwargs):return _isolated(base.audit_batch,_globals())(*args,**kwargs)

def analyze_batch(manifest,plan,results,audit,out_dir):
    from .analysis import analyze_batch as analyze
    verify_batch(plan)
    return analyze(manifest,plan,results,audit,out_dir)
