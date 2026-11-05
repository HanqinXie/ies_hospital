"""56-source executed-service observation route, retaining v12 M0 physics."""
from __future__ import annotations
import builtins
import copy
from pathlib import Path

from tools.coordinated_dispatch import closed_loop_adaptive as original
from tools.coordinated_dispatch import interface as joint
from tools.integrated_learning import episode as episode_base
from hospital_ies import dataset_export
from hospital_ies.data_contract import decision_snapshot as base_snapshot
from tools.policy_redesign import audit as audit_base
from .history import make_snapshot,validate_history,EXECUTED_SERVICE_HISTORY_CONTRACT_ID
from .policy import ExecutedServicePolicy,EXECUTED_SERVICE_POLICY_CONTRACT_ID

ROOT=original.ROOT
SCHEMA='executed_service.matched_feedback.v1'
AUDIT_SCHEMA='executed_service.matched_audit.v1'
SOURCE_FILES=tuple(sorted(set(original.SOURCE_FILES)|{
    'tools/executed_service/history.py','tools/executed_service/policy.py',
    'tools/executed_service/closed_loop.py'}))
PLANNING_INTERFACE=original.PLANNING_INTERFACE
ACTION_SCHEMA=original.ACTION_SCHEMA
NUMERICAL_CONTRACT_ID=original.NUMERICAL_CONTRACT_ID
STANDBY_CONTRACT_ID=original.STANDBY_CONTRACT_ID
SERVICE_SWITCH_CONTRACT_ID=original.SERVICE_SWITCH_CONTRACT_ID
BUDGET_FEEDBACK_CONTRACT_ID=original.BUDGET_FEEDBACK_CONTRACT_ID
_sha,_read,_new_json,_isolated=original._sha,original._read,original._new_json,original._isolated
window_targets=original.window_targets

def _source_binding():return {p:_sha(ROOT/p) for p in SOURCE_FILES}

def _require_contracts(manifest):
    for key,value in (('executed_service_history_contract_id',EXECUTED_SERVICE_HISTORY_CONTRACT_ID),
                      ('executed_service_policy_contract_id',EXECUTED_SERVICE_POLICY_CONTRACT_ID)):
        if manifest.get(key)!=value:raise ValueError('Missing/changed executed service contract: '+key)

def decision_snapshot(env):
    return make_snapshot(env,base_snapshot(env))

class ServicePolicyEnvironment(joint.CoordinatedPolicyEnvironment):
    step=_isolated(episode_base.PolicyEnvironment.step,dict(
        episode_base.PolicyEnvironment.step.__globals__,decision_snapshot=decision_snapshot,
        validate_action=joint.validate_action))

def episode_function():
    exporter=_isolated(dataset_export.run_trajectory,dict(
        dataset_export.run_trajectory.__globals__,decision_snapshot=decision_snapshot))
    return _isolated(episode_base.run_episode,dict(episode_base.run_episode.__globals__,
        PolicyEnvironment=ServicePolicyEnvironment,run_trajectory=exporter))

def _factory(spec):
    if spec['kind']=='executed_service':
        return ExecutedServicePolicy(copy.deepcopy(spec['config']),spec['policy_id'])
    return original._factory(spec)

def _check_specs(policy_specs):
    specs=copy.deepcopy(list(policy_specs))
    if not specs or len(specs)>8:raise ValueError('Finite explicit policy panel required')
    ids=[s.get('policy_id') for s in specs]
    if any(not isinstance(p,str) or not p for p in ids) or len(set(ids))!=len(ids):
        raise ValueError('Distinct policy identifiers required')
    for spec in specs:
        if spec.get('kind')=='executed_service':
            if (set(spec)!={'policy_id','kind','config'} or not isinstance(spec['config'],dict)
                    or spec['policy_id'] in ('execution_reserve','joint_standby_1p25_h2','priority_milp','secure_milp')):
                raise ValueError('New feedback must retain a distinct identity')
            _factory(spec)
        else:original._check_specs([spec])
    return specs

def _environment_factory(plan,manifest):
    _require_contracts(manifest)
    return original._environment_factory(plan,manifest)

def run_policy_episode(env,scenario,policy,seed,manifest,**options):
    _require_contracts(manifest)
    original._require_adaptive_contract(manifest)
    joint._require_contracts(manifest)
    if env.model_id!='M0':raise ValueError('Unchanged M0 required')
    bound=joint.BoundaryBoundPolicy(env,policy) if hasattr(policy,'bind_current') else policy
    payload=episode_function()(env,scenario,bound,seed,manifest,**options)
    payload.update(planning_interface=PLANNING_INTERFACE,action_schema=ACTION_SCHEMA,
        execution_model_id=env.model_id,input_access_receipt=env.input_access_receipt,
        numerical_contract_id=NUMERICAL_CONTRACT_ID,standby_contract_id=STANDBY_CONTRACT_ID,
        service_switch_contract_id=SERVICE_SWITCH_CONTRACT_ID,
        budget_feedback_contract_id=BUDGET_FEEDBACK_CONTRACT_ID,
        executed_service_history_contract_id=EXECUTED_SERVICE_HISTORY_CONTRACT_ID,
        executed_service_policy_contract_id=EXECUTED_SERVICE_POLICY_CONTRACT_ID)
    return payload

def _payload_binding(payload,scenario,manifest):
    result=original._payload_binding(payload,scenario,manifest)
    for key,value in (('executed_service_history_contract_id',EXECUTED_SERVICE_HISTORY_CONTRACT_ID),
                      ('executed_service_policy_contract_id',EXECUTED_SERVICE_POLICY_CONTRACT_ID)):
        result['checks'][key+'_equal']=payload.get(key)==manifest.get(key)==value
    try:
        for row in payload.get('decisions',[]):
            validate_history(row['executed_service_history'],float(row['decision_time_min']))
        valid=True
    except (ValueError,TypeError,KeyError):valid=False
    result['checks']['completed_service_observation_valid']=valid
    result['passed']=all(result['checks'].values())
    return result

def _globals():
    ns=original._globals()
    ns.update(ROOT=ROOT,SCHEMA=SCHEMA,AUDIT_SCHEMA=AUDIT_SCHEMA,SOURCE_FILES=SOURCE_FILES,
        _source_binding=_source_binding,_factory=_factory,_check_specs=_check_specs,
        _environment_factory=_environment_factory,run_policy_episode=run_policy_episode,
        _payload_binding=_payload_binding,_runner_namespace=_runner_namespace,verify_batch=verify_batch,
        _recover_interrupted=_recover_interrupted,_summary=_summary,
        check_confirmation_freeze=check_confirmation_freeze)
    return ns

def _runner_namespace(environment=None):
    ns=original._runner_namespace(environment)
    ns.update(SCHEMA=SCHEMA,_source_binding=_source_binding,_factory=_factory,
        _check_specs=_check_specs,verify_regime_batch=verify_batch,
        _recover_interrupted=_recover_interrupted,_summary=_summary,
        run_policy_episode=run_policy_episode,_payload_binding=_payload_binding)
    return ns

def check_confirmation_freeze(plan,manifest,root=ROOT):
    _isolated(original.check_confirmation_freeze,dict(
        original.check_confirmation_freeze.__globals__,_globals=_globals))(plan,manifest,root)
    _require_contracts(manifest)
    if plan.get('phase')!='confirmation':return
    path=Path(_read(plan['preference_path'])['confirmation_gate']['policy_freeze_path'])
    if not path.is_absolute():path=Path(root)/path
    _require_contracts(_read(path))

def register_batch(manifest_path,plan_path,**options):
    _require_contracts(_read(manifest_path))
    return _isolated(original.original.base.register_batch,_globals())(manifest_path,plan_path,**options)

def verify_batch(*args,**kwargs):
    plan,manifest=_isolated(original.original.base.verify_batch,_globals())(*args,**kwargs)
    original.original._require_contracts(manifest)
    original._require_adaptive_contract(manifest)
    _require_contracts(manifest)
    return plan,manifest

def _recover_interrupted(*args,**kwargs):return _isolated(original.original.base._recover_interrupted,_globals())(*args,**kwargs)
def _summary(*args,**kwargs):return _isolated(original.original.base._summary,_globals())(*args,**kwargs)
def run_batch(*args,**kwargs):return _isolated(original.original.base.run_batch,_globals())(*args,**kwargs)

def _display_sorted(values,*,key=None,reverse=False):
    items=list(values)
    try:return builtins.sorted(items,key=key,reverse=reverse)
    except TypeError:
        if key is not None:raise
        return builtins.sorted(items,key=lambda x:(type(x).__name__,repr(x)),reverse=reverse)

def audit_batch(plan_path,results_dir,*,output_json=None,output_markdown=None,include_action_effect=False):
    if include_action_effect:raise ValueError('Legacy projection-only audit is not applicable')
    plan,manifest=verify_batch(plan_path)
    groups=_isolated(audit_base._subgroups,dict(audit_base._subgroups.__globals__,sorted=_display_sorted))
    ns=dict(audit_base.audit_batch.__globals__,ROOT=ROOT,SCHEMA=AUDIT_SCHEMA,
        verify_batch=verify_batch,HospitalIESEnv=_environment_factory(plan,manifest),
        window_targets=window_targets,_payload_binding=_payload_binding,
        _brief_markdown=audit_base._brief_markdown,_subgroups=groups)
    return _isolated(audit_base.audit_batch,ns)(plan_path,results_dir,
        output_json=output_json,output_markdown=output_markdown,include_action_effect=False)

def analyze_batch(manifest,plan,results,audit,out_dir):
    from .analysis import analyze_batch as analyze
    return analyze(manifest,plan,results,audit,out_dir)
