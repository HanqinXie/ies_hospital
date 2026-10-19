"""Additive static-margin route; the original v1/v2 batches stay immutable."""
from __future__ import annotations
import copy
from . import closed_loop as previous
from . import closed_loop_v2 as connections_route
from .margin import (MarginDispatchPolicy, MarginHospitalIESEnv, PLANNING_INTERFACE,
                     run_policy_episode, validate_action)

ROOT = previous.ROOT
SCHEMA = 'autonomous_dispatch.margin.matched_full_feedback.v3'
AUDIT_SCHEMA = 'autonomous_dispatch.margin.matched_batch_audit.v3'
ACTION_SCHEMA = 'autonomous_dispatch.legacy_or_joint_action.v3'
SOURCE_FILES = tuple(sorted(set(connections_route.SOURCE_FILES) | {
    f'tools/autonomous_dispatch/{name}.py' for name in ('planner_margin', 'margin', 'closed_loop_v3')}))
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
    namespace = dict(previous._environment_factory.__globals__, SelectiveHospitalIESEnv=MarginHospitalIESEnv)
    return _isolated(previous._environment_factory, namespace)(plan, manifest)


def _payload_binding(payload, scenario, manifest):
    result = previous._payload_binding(payload, scenario, manifest)
    result['checks']['planning_interface_equal'] = payload.get('planning_interface') == PLANNING_INTERFACE
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


def register_batch(*args, **kwargs):
    return _isolated(previous.register_batch, _globals())(*args, **kwargs)


def verify_batch(*args, **kwargs):
    return _isolated(previous.verify_batch, _globals())(*args, **kwargs)


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
