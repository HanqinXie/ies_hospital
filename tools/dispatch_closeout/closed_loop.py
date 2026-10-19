"""Version-bound closeout route over the existing matched execution machinery.

Old namespaces and raw evidence remain untouched. Only registered policies,
schema and explicit source dependencies differ from the v9 runner.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tools.state_dispatch import closed_loop as v1
from tools.state_dispatch import closed_loop_v2 as previous

SCHEMA = "dispatch_closeout.matched_full_feedback.v1"
AUDIT_SCHEMA = "dispatch_closeout.matched_batch_audit.v1"
ANALYSIS_SCHEMA = "dispatch_closeout.audited_saved_raw_analysis.v1"
SOURCE_FILES = tuple(sorted(set(previous.SOURCE_FILES) | {
    "tools/dispatch_closeout/closed_loop.py", "tools/dispatch_closeout/policy.py"}))
PHASES = previous.PHASES
_sha, _read, _new_json = previous._sha, previous._read, previous._new_json
_isolated = previous._isolated
HospitalIESEnv = previous.HospitalIESEnv
window_targets = previous.window_targets
run_policy_episode = previous.run_policy_episode
_payload_binding = previous._payload_binding
_command_counts = previous._command_counts


def _source_binding():
    return {name: _sha(ROOT / name) for name in SOURCE_FILES}


def _factory(spec):
    if spec["kind"] in {"secure", "priority"}:
        return previous._factory(spec)
    if spec["kind"] != "state_dispatch":
        raise ValueError("Unregistered closeout policy kind")
    if spec["policy_id"] == "state_guarded_spread":
        return previous._factory(spec)
    if spec["policy_id"] not in {"execution_reserve", "execution_reserve_no_sustain"}:
        raise ValueError("Unregistered closeout state-dispatch policy ID")
    from tools.dispatch_closeout.policy import StateDispatchPolicy
    return StateDispatchPolicy(copy.deepcopy(spec["config"]), spec["policy_id"])


def _check_specs(policy_specs):
    namespace = dict(v1._check_specs.__globals__)
    namespace["_factory"] = _factory
    return _isolated(v1._check_specs, namespace)(policy_specs)


def _runner_namespace():
    namespace = previous._runner_namespace()
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
    return v1._brief_markdown(report).replace("# v9 状态调度匹配", "# v10 调度收口匹配", 1)


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


def analyze_batch(manifest, plan, results, audit, out_dir):
    """Read audited saved raws using v9 metric definitions and new provenance.

The reusable analysis already verifies every result/raw hash and denominator.
A separate receipt binds this thin wrapper and the registered current sources.
"""
    if _read(plan).get("schema") != SCHEMA or _read(audit).get("schema") != AUDIT_SCHEMA:
        raise ValueError("closeout plan/audit schema required")
    verify_batch(plan)
    from tools.state_dispatch import analysis
    namespace = dict(analysis.__dict__)
    namespace.update(ROOT=ROOT, SCHEMA=ANALYSIS_SCHEMA)
    report = _isolated(analysis.analyze_batch, namespace)(manifest, plan, results, audit, out_dir)
    receipt = {"schema": ANALYSIS_SCHEMA + ".source_binding",
        "summary_sha256": _sha(Path(out_dir) / "SUMMARY.json"),
        "plan_sha256": _sha(plan), "audit_sha256": _sha(audit),
        "manifest_sha256": _sha(manifest), "registered_runtime_source_sha256": _source_binding(),
        "analysis_source_sha256": report["analysis_source_sha256"],
        "wrapper_source_sha256": _sha(Path(__file__)),
        "saved_raw_recomputed": True, "physical_trajectories_started": 0}
    _new_json(Path(out_dir) / "CLOSEOUT_SOURCE_BINDING.json", receipt)
    return report


def main(argv=None):
    namespace = dict(v1.main.__globals__)
    namespace.update({"__doc__": __doc__, "register_batch": register_batch,
        "verify_batch": verify_batch, "run_batch": run_batch, "audit_batch": audit_batch})
    return _isolated(v1.main, namespace)(argv)


if __name__ == "__main__":
    raise SystemExit(main())
