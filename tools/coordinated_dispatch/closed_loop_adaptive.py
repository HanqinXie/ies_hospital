"""Isolated 53-source route for a fixed/state by constant/adaptive budget panel."""
from __future__ import annotations
import copy
from pathlib import Path

from . import closed_loop as original
from .policy_adaptive import AdaptiveBudgetPolicy, BUDGET_FEEDBACK_CONTRACT_ID

ROOT = original.ROOT
SCHEMA = 'coordinated_dispatch.adaptive_budget.matched_full_feedback.v1'
AUDIT_SCHEMA = 'coordinated_dispatch.adaptive_budget.matched_batch_audit.v1'
SOURCE_FILES = tuple(sorted(set(original.SOURCE_FILES)|{
    'tools/coordinated_dispatch/policy_adaptive.py',
    'tools/coordinated_dispatch/closed_loop_adaptive.py'}))
ACTION_SCHEMA = original.ACTION_SCHEMA
PLANNING_INTERFACE = original.PLANNING_INTERFACE
NUMERICAL_CONTRACT_ID = original.NUMERICAL_CONTRACT_ID
STANDBY_CONTRACT_ID = original.STANDBY_CONTRACT_ID
SERVICE_SWITCH_CONTRACT_ID = original.SERVICE_SWITCH_CONTRACT_ID
_sha,_read,_new_json,_isolated = original._sha,original._read,original._new_json,original._isolated
window_targets = original.window_targets


def _source_binding():return {name:_sha(ROOT/name) for name in SOURCE_FILES}


def _require_adaptive_contract(manifest):
    if manifest.get('budget_feedback_contract_id') != BUDGET_FEEDBACK_CONTRACT_ID:
        raise ValueError('Missing or unsupported current-fuel budget feedback contract')


def _factory(spec):
    if spec['kind'] == 'coordinated':
        config = copy.deepcopy(spec['config'])
        feedback = config.pop('budget_feedback',False)
        return AdaptiveBudgetPolicy(config,spec['policy_id'],budget_feedback=feedback)
    return original._factory(spec)


def _check_specs(specs):
    function = original._check_specs
    return _isolated(function,dict(function.__globals__,_factory=_factory))(specs)


def _environment_factory(plan,manifest):
    _require_adaptive_contract(manifest)
    return original._environment_factory(plan,manifest)


def run_policy_episode(env,scenario,policy,seed,manifest,**options):
    _require_adaptive_contract(manifest)
    payload = original.run_policy_episode(env,scenario,policy,seed,manifest,**options)
    payload['budget_feedback_contract_id'] = BUDGET_FEEDBACK_CONTRACT_ID
    return payload


def _payload_binding(payload,scenario,manifest):
    result = original._payload_binding(payload,scenario,manifest)
    result['checks']['budget_feedback_contract_equal'] = (
        payload.get('budget_feedback_contract_id') == manifest.get('budget_feedback_contract_id')
        == BUDGET_FEEDBACK_CONTRACT_ID)
    result['passed'] = all(result['checks'].values())
    return result


def _globals():
    namespace = original._globals()
    namespace.update(ROOT=ROOT,SCHEMA=SCHEMA,AUDIT_SCHEMA=AUDIT_SCHEMA,SOURCE_FILES=SOURCE_FILES,
        _source_binding=_source_binding,_factory=_factory,_check_specs=_check_specs,
        _environment_factory=_environment_factory,run_policy_episode=run_policy_episode,
        _payload_binding=_payload_binding,_runner_namespace=_runner_namespace,verify_batch=verify_batch,
        _recover_interrupted=_recover_interrupted,_summary=_summary,check_confirmation_freeze=check_confirmation_freeze)
    return namespace


def _runner_namespace(environment=None):
    namespace = original._runner_namespace(environment)
    namespace.update(SCHEMA=SCHEMA,_source_binding=_source_binding,_check_specs=_check_specs,
        _factory=_factory,verify_regime_batch=verify_batch,_recover_interrupted=_recover_interrupted,
        _summary=_summary,run_policy_episode=run_policy_episode,_payload_binding=_payload_binding)
    return namespace


def check_confirmation_freeze(plan,manifest,root=ROOT):
    function = original.check_confirmation_freeze
    _isolated(function,dict(function.__globals__,_globals=_globals))(plan,manifest,root)
    _require_adaptive_contract(manifest)
    if plan.get('phase') != 'confirmation':return
    preference = _read(plan['preference_path'])
    path = Path(preference['confirmation_gate']['policy_freeze_path'])
    if not path.is_absolute():path = Path(root)/path
    if _read(path).get('budget_feedback_contract_id') != BUDGET_FEEDBACK_CONTRACT_ID:
        raise ValueError('Frozen current-fuel budget feedback contract differs')


def register_batch(manifest_path,plan_path,**options):
    _require_adaptive_contract(_read(manifest_path))
    return _isolated(original.base.register_batch,_globals())(manifest_path,plan_path,**options)


def verify_batch(*args,**kwargs):
    plan,manifest = _isolated(original.base.verify_batch,_globals())(*args,**kwargs)
    original._require_contracts(manifest)
    _require_adaptive_contract(manifest)
    return plan,manifest


def _recover_interrupted(*args,**kwargs):return _isolated(original.base._recover_interrupted,_globals())(*args,**kwargs)

def _summary(*args,**kwargs):return _isolated(original.base._summary,_globals())(*args,**kwargs)

def run_batch(*args,**kwargs):return _isolated(original.base.run_batch,_globals())(*args,**kwargs)

def audit_batch(*args,**kwargs):return _isolated(original.base.audit_batch,_globals())(*args,**kwargs)


def analyze_batch(manifest,plan,results,audit,out_dir):
    """Reuse saved analysis and append the missing full-53 source receipt.

    The untouched common-component receipt remains explicit about its 51-source
    inventory. No raw, score or common analysis output is opened again here.
    """
    verify_batch(plan)
    # Its internal verify_batch must also use 53 sources and the new factory;
    # calling the original wrapper directly would fall back to its 51 sources.
    function = original.analyze_batch
    report = _isolated(function,dict(function.__globals__,verify_batch=verify_batch))(
        manifest,plan,results,audit,out_dir)
    out = Path(out_dir)
    sources = _source_binding()
    receipt = dict(schema='coordinated_dispatch.adaptive_analysis_source_binding.v1',
        budget_feedback_contract_id=BUDGET_FEEDBACK_CONTRACT_ID,
        runtime_source_count=len(sources),registered_runtime_sources=sources,
        manifest_sha256=_sha(Path(manifest)),plan_sha256=_sha(Path(plan)),audit_sha256=_sha(Path(audit)),
        report_sha256=_sha(out/'SUMMARY.json'),extra_report_sha256=_sha(out/'SERVICE_SWITCH_SUMMARY.json'),
        common_component_receipt_sha256=_sha(out/'COORDINATED_SOURCE_BINDING.json'),
        common_component_receipt_scope='Unmodified 51-source common planner/interface; full adaptive source inventory is this receipt',
        physical_trajectories_started=0,extra_raw_opens=0)
    _new_json(out/'ADAPTIVE_SOURCE_BINDING.json',receipt)
    return report
