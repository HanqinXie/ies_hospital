"""Separate immutable v2 policy route over the frozen v1 batch machinery.

No v1 source, plan or attempt is changed. Only the policy factory, explicit
source binding and schema/verification namespace differ in this route.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tools.state_dispatch import closed_loop as v1

SCHEMA = "state_dispatch.matched_full_feedback.v2"
AUDIT_SCHEMA = "state_dispatch.matched_batch_audit.v2"
SOURCE_FILES = tuple(sorted(set(v1.SOURCE_FILES) | {
    "tools/state_dispatch/closed_loop_v2.py", "tools/state_dispatch/policy_v2.py"}))
PHASES = v1.PHASES
_sha, _read, _new_json = v1._sha, v1._read, v1._new_json
_isolated = v1._isolated
HospitalIESEnv = v1.HospitalIESEnv
window_targets = v1.window_targets
run_policy_episode = v1.run_policy_episode
_payload_binding = v1._payload_binding
_command_counts = v1._command_counts


def _source_binding():
    return {name: _sha(ROOT / name) for name in SOURCE_FILES}


def _factory(spec):
    if spec["kind"] in {"secure", "priority"}:
        return v1._factory(spec)
    if spec["kind"] != "state_dispatch":
        raise ValueError("Unregistered v2 state-dispatch policy kind")
    from tools.state_dispatch.policy_v2 import StateDispatchPolicy
    return StateDispatchPolicy(copy.deepcopy(spec["config"]), spec["policy_id"])


def _check_specs(policy_specs):
    namespace = dict(v1._check_specs.__globals__)
    namespace["_factory"] = _factory
    return _isolated(v1._check_specs, namespace)(policy_specs)


def _runner_namespace():
    namespace = v1._runner_namespace()
    namespace.update({"ROOT": ROOT, "SCHEMA": SCHEMA, "_check_specs": _check_specs,
        "_source_binding": _source_binding, "_factory": _factory,
        "verify_regime_batch": verify_batch, "_recover_interrupted": _recover_interrupted,
        "_summary": _summary, "HospitalIESEnv": HospitalIESEnv,
        "window_targets": window_targets, "run_policy_episode": run_policy_episode,
        "_payload_binding": _payload_binding, "_command_counts": _command_counts})
    return namespace


def register_batch(manifest_path, plan_path, *, scenario_ids, policy_specs,
                   preference_path, phase):
    return _isolated(v1.regime_runner.register_regime_batch, _runner_namespace())(
        manifest_path, plan_path, scenario_ids=scenario_ids, policy_specs=policy_specs,
        preference_path=preference_path, phase=phase)


def verify_batch(plan_path):
    return _isolated(v1.regime_runner.verify_regime_batch, _runner_namespace())(plan_path)


def _recover_interrupted(*args, **kwargs):
    return _isolated(v1.regime_runner._recover_interrupted, _runner_namespace())(*args, **kwargs)


def _summary(*args, **kwargs):
    return _isolated(v1.regime_runner._summary, _runner_namespace())(*args, **kwargs)


def run_batch(plan_path, out_dir, *, max_new_pairs=None):
    return _isolated(v1.regime_runner.run_regime_batch, _runner_namespace())(
        plan_path, out_dir, max_new_pairs=max_new_pairs)


def _brief_markdown(report):
    return v1._brief_markdown(report).replace("# v9 状态调度匹配", "# v9 状态调度v2匹配", 1)


def audit_batch(plan_path, results_dir, *, output_json=None, output_markdown=None,
                include_action_effect=True):
    namespace = dict(v1.audit_batch.__globals__)
    namespace.update({"ROOT": ROOT, "AUDIT_SCHEMA": AUDIT_SCHEMA,
        "verify_batch": verify_batch, "HospitalIESEnv": HospitalIESEnv,
        "window_targets": window_targets, "_payload_binding": _payload_binding,
        "_brief_markdown": _brief_markdown})
    return _isolated(v1.audit_batch, namespace)(plan_path, results_dir,
        output_json=output_json, output_markdown=output_markdown,
        include_action_effect=include_action_effect)


def main(argv=None):
    namespace = dict(v1.main.__globals__)
    namespace.update({"__doc__": __doc__, "register_batch": register_batch,
        "verify_batch": verify_batch, "run_batch": run_batch, "audit_batch": audit_batch})
    return _isolated(v1.main, namespace)(argv)


if __name__ == "__main__":
    raise SystemExit(main())
