"""Version-bound original-row numerics; v3 requests and M0 execution retained."""
from __future__ import annotations
import copy
from . import closed_loop as previous
from . import closed_loop_v3 as margin_route
from .margin import MarginDispatchPolicy, PLANNING_INTERFACE, validate_action
from .numerical_route import (NUMERICAL_CONTRACT_ID, UnscaledHospitalIESEnv,
                              _require_numerical_contract, run_policy_episode)

ROOT = previous.ROOT
SCHEMA = 'autonomous_dispatch.unscaled.matched_full_feedback.v4'
AUDIT_SCHEMA = 'autonomous_dispatch.unscaled.matched_batch_audit.v4'
ACTION_SCHEMA = margin_route.ACTION_SCHEMA
SOURCE_FILES = tuple(sorted(set(margin_route.SOURCE_FILES) | {
    f'tools/autonomous_dispatch/{name}.py' for name in ('numerics', 'numerical_route', 'closed_loop_v4')}))
_sha, _read, _new_json, _isolated = previous._sha, previous._read, previous._new_json, previous._isolated
window_targets = previous.window_targets


def _source_binding():
    return {name: _sha(ROOT/name) for name in SOURCE_FILES}


def _factory(spec):
    if spec['kind'] == 'joint':
        return MarginDispatchPolicy(copy.deepcopy(spec['config']), spec['policy_id'])
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
    _require_numerical_contract(manifest)
    namespace = dict(previous._environment_factory.__globals__, SelectiveHospitalIESEnv=UnscaledHospitalIESEnv)
    return _isolated(previous._environment_factory, namespace)(plan, manifest)


def _payload_binding(payload, scenario, manifest):
    result = previous._payload_binding(payload, scenario, manifest)
    result['checks']['planning_interface_equal'] = payload.get('planning_interface') == PLANNING_INTERFACE
    result['checks']['numerical_contract_equal'] = (
        payload.get('numerical_contract_id') == NUMERICAL_CONTRACT_ID
        and manifest.get('numerical_contract_id') == NUMERICAL_CONTRACT_ID)
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
    _require_numerical_contract(_read(manifest_path))
    return _isolated(previous.register_batch, _globals())(manifest_path, plan_path, **options)


def verify_batch(*args, **kwargs):
    plan, manifest = _isolated(previous.verify_batch, _globals())(*args, **kwargs)
    _require_numerical_contract(manifest)
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
