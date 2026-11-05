"""Additive 47-source standby route; frozen 44-source runtime stays immutable."""
from __future__ import annotations
import copy
from . import closed_loop as previous
from . import closed_loop_v4 as numerical_route
from .standby import (ACTION_SCHEMA, NUMERICAL_CONTRACT_ID, STANDBY_CONTRACT_ID, PLANNING_INTERFACE,
    StandbyDispatchPolicy, StandbyHospitalIESEnv, _require_contracts, run_policy_episode, validate_action)

ROOT = previous.ROOT
SCHEMA = 'autonomous_dispatch.standby.matched_full_feedback.v5'
AUDIT_SCHEMA = 'autonomous_dispatch.standby.matched_batch_audit.v5'
SOURCE_FILES = tuple(sorted(set(numerical_route.SOURCE_FILES) | {
    f'tools/autonomous_dispatch/{name}.py' for name in ('planner_standby', 'standby', 'closed_loop_v5')}))
_sha, _read, _new_json, _isolated = previous._sha, previous._read, previous._new_json, previous._isolated
window_targets = previous.window_targets


def _source_binding():
    return {name: _sha(ROOT/name) for name in SOURCE_FILES}


def _factory(spec):
    if spec['kind'] == 'joint':
        return StandbyDispatchPolicy(copy.deepcopy(spec['config']), spec['policy_id'])
    return previous._factory(spec)


def _globals():
    return dict(previous.__dict__, ROOT=ROOT, SCHEMA=SCHEMA, AUDIT_SCHEMA=AUDIT_SCHEMA,
        SOURCE_FILES=SOURCE_FILES, _source_binding=_source_binding, _factory=_factory,
        _check_specs=_check_specs, _runner_namespace=_runner_namespace, verify_batch=verify_batch,
        _recover_interrupted=_recover_interrupted, _summary=_summary,
        run_policy_episode=run_policy_episode, _environment_factory=_environment_factory,
        check_confirmation_freeze=check_confirmation_freeze, _payload_binding=_payload_binding)


def _check_specs(specs):
    return _isolated(previous._check_specs, _globals())(specs)


def _environment_factory(plan, manifest):
    _require_contracts(manifest)
    namespace = dict(previous._environment_factory.__globals__, SelectiveHospitalIESEnv=StandbyHospitalIESEnv)
    return _isolated(previous._environment_factory, namespace)(plan, manifest)


def _payload_binding(payload, scenario, manifest):
    result = previous._payload_binding(payload, scenario, manifest)
    result['checks'].update(
        planning_interface_equal=payload.get('planning_interface') == manifest.get('planning_interface_id') == PLANNING_INTERFACE,
        numerical_contract_equal=payload.get('numerical_contract_id') == manifest.get('numerical_contract_id') == NUMERICAL_CONTRACT_ID,
        standby_contract_equal=payload.get('standby_contract_id') == manifest.get('standby_contract_id') == STANDBY_CONTRACT_ID,
        action_schema_equal=manifest.get('schemas', {}).get('action') == ACTION_SCHEMA)
    result['passed'] = all(result['checks'].values())
    return result


def check_confirmation_freeze(plan, manifest, root=ROOT):
    return _isolated(previous.check_confirmation_freeze, _globals())(plan, manifest, root)


def _runner_namespace(environment=None):
    namespace = previous._runner_namespace(environment)
    namespace.update(SCHEMA=SCHEMA, _source_binding=_source_binding, _check_specs=_check_specs,
        _factory=_factory, verify_regime_batch=verify_batch, _recover_interrupted=_recover_interrupted,
        _summary=_summary, run_policy_episode=run_policy_episode, _payload_binding=_payload_binding)
    return namespace


def register_batch(manifest_path, plan_path, **options):
    _require_contracts(_read(manifest_path))
    return _isolated(previous.register_batch, _globals())(manifest_path, plan_path, **options)


def verify_batch(*args, **kwargs):
    plan, manifest = _isolated(previous.verify_batch, _globals())(*args, **kwargs)
    _require_contracts(manifest)
    return plan, manifest


def _recover_interrupted(*args, **kwargs):
    return _isolated(previous._recover_interrupted, _globals())(*args, **kwargs)


def _summary(*args, **kwargs):
    return _isolated(previous._summary, _globals())(*args, **kwargs)


def run_batch(*args, **kwargs):
    return _isolated(previous.run_batch, _globals())(*args, **kwargs)


def audit_batch(*args, **kwargs):
    return _isolated(previous.audit_batch, _globals())(*args, **kwargs)


def analyze_batch(manifest, plan, results, audit, out_dir):
    from .analysis import analyze_batch as analyze
    verify_batch(plan)
    return analyze(manifest, plan, results, audit, out_dir,
                   validator=validate_action, runtime_source_binding=_source_binding)
