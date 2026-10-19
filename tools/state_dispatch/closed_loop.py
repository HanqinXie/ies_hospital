"""Version-bound state-dispatch batches using the existing execution and audit.

Only registration specs and the policy factory are new. Existing regime-runner
attempts, immutable raws, unknown masks and summaries are reused in isolated
function namespaces; no old module globals or registered results are changed.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from types import FunctionType

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tools.integrated_learning.policies import BaselinePolicy
from tools.policy_redesign import audit as original_audit
from tools.policy_redesign import closed_loop as original_runner
from tools.policy_redesign import regime_closed_loop as regime_runner


SCHEMA = "state_dispatch.matched_full_feedback.v1"
AUDIT_SCHEMA = "state_dispatch.matched_batch_audit.v1"
KINDS = frozenset({"secure", "priority", "state_dispatch"})
PHASES = regime_runner.PHASES
# Explicit dependencies only: later reports/plots in this directory do not
# invalidate a frozen batch. Physical/input bytes remain bound by the manifest.
SOURCE_FILES = tuple(sorted(set(original_runner.BASE_SOURCE_FILES) | {
    "tools/state_dispatch/closed_loop.py",
    "tools/state_dispatch/policy.py",
    "tools/policy_redesign/regime_closed_loop.py",
    "tools/policy_redesign/regime_policy.py",
    "tools/policy_redesign/reserve_policy.py",
    "tools/policy_redesign/audit.py",
    "tools/policy_redesign/action_effect.py",
    "tools/policy_search/preference.py",
    "tools/policy_search/score_recheck.py",
}))

_sha = regime_runner._sha
_read = regime_runner._read
_new_json = regime_runner._new_json
_journal = regime_runner._journal
_pair_path = regime_runner._pair_path
_attempts = regime_runner._attempts
_last = regime_runner._last
HospitalIESEnv = regime_runner.HospitalIESEnv
window_targets = regime_runner.window_targets
run_policy_episode = regime_runner.run_policy_episode
_payload_binding = regime_runner._payload_binding
_command_counts = regime_runner._command_counts


def _isolated(function, namespace):
    """Rebind dependencies without a process-global monkeypatch."""
    rebound = FunctionType(function.__code__, namespace, function.__name__,
                           function.__defaults__, function.__closure__)
    rebound.__kwdefaults__ = copy.copy(function.__kwdefaults__)
    return rebound


def _source_binding():
    return {name: _sha(ROOT / name) for name in SOURCE_FILES}


def _factory(spec):
    if spec["kind"] == "secure":
        return BaselinePolicy("secure_milp")
    if spec["kind"] == "priority":
        return BaselinePolicy("priority_milp")
    if spec["kind"] != "state_dispatch":
        raise ValueError("Unregistered state-dispatch policy kind")
    # Baseline-only diagnostics can import the runner before the new policy;
    # candidate construction validates its own registered configuration.
    from tools.state_dispatch.policy import StateDispatchPolicy
    return StateDispatchPolicy(copy.deepcopy(spec["config"]), spec["policy_id"])


def _check_specs(policy_specs):
    specs = copy.deepcopy(list(policy_specs))
    if not specs or len(specs) > 80 or any(not isinstance(spec, dict) for spec in specs):
        raise ValueError("State-dispatch policy specifications missing or over budget")
    ids = [spec.get("policy_id") for spec in specs]
    if (any(not isinstance(pid, str) or not pid for pid in ids)
            or len(ids) != len(set(ids))):
        raise ValueError("State-dispatch policy IDs must be distinct and nonempty")
    secure = {"policy_id": "secure_milp", "kind": "secure"}
    priority = {"policy_id": "priority_milp", "kind": "priority"}
    if secure not in specs:
        raise ValueError("Exact original secure baseline required")
    for spec in specs:
        kind = spec.get("kind")
        if kind not in KINDS:
            raise ValueError("Unregistered state-dispatch policy kind")
        if kind in ("secure", "priority"):
            if spec != (secure if kind == "secure" else priority):
                raise ValueError("Original baseline specification differs")
        else:
            if (set(spec) != {"policy_id", "kind", "config"}
                    or spec["policy_id"] in {"secure_milp", "priority_milp"}
                    or not isinstance(spec["config"], dict)):
                raise ValueError("State-dispatch specification requires a separate ID and config dict")
            try:
                json.dumps(spec["config"], allow_nan=False)
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError("State-dispatch config must be finite JSON") from error
            _factory(spec)
    return specs


def _runner_namespace():
    namespace = dict(regime_runner.__dict__)
    namespace.update({
        "ROOT": ROOT, "SCHEMA": SCHEMA, "PHASES": PHASES,
        "_check_specs": _check_specs, "_source_binding": _source_binding,
        "_factory": _factory, "verify_regime_batch": verify_batch,
        "_recover_interrupted": _recover_interrupted, "_summary": _summary,
        "HospitalIESEnv": HospitalIESEnv, "window_targets": window_targets,
        "run_policy_episode": run_policy_episode, "_payload_binding": _payload_binding,
        "_command_counts": _command_counts,
    })
    return namespace


def register_batch(manifest_path, plan_path, *, scenario_ids, policy_specs,
                   preference_path, phase):
    """Freeze current policy/source/input bytes before any evaluation."""
    return _isolated(regime_runner.register_regime_batch, _runner_namespace())(
        manifest_path, plan_path, scenario_ids=scenario_ids, policy_specs=policy_specs,
        preference_path=preference_path, phase=phase)


def verify_batch(plan_path):
    """Reject source, input, role, sealed-final, plan or preference drift."""
    return _isolated(regime_runner.verify_regime_batch, _runner_namespace())(plan_path)


def _recover_interrupted(*args, **kwargs):
    return _isolated(regime_runner._recover_interrupted, _runner_namespace())(*args, **kwargs)


def _summary(*args, **kwargs):
    return _isolated(regime_runner._summary, _runner_namespace())(*args, **kwargs)


def run_batch(plan_path, out_dir, *, max_new_pairs=None):
    """Reuse immutable attempts, original episodes and all unknown outcomes."""
    return _isolated(regime_runner.run_regime_batch, _runner_namespace())(
        plan_path, out_dir, max_new_pairs=max_new_pairs)


def _brief_markdown(report):
    return original_audit._brief_markdown(report).replace(
        "# v6 匹配批次原始轨迹审查", "# v9 状态调度匹配批次原始轨迹审查", 1)


def audit_batch(plan_path, results_dir, *, output_json=None, output_markdown=None,
                include_action_effect=True):
    """Recompute saved raw scores; this never advances a physical trajectory."""
    namespace = dict(original_audit.audit_batch.__globals__)
    namespace.update({
        "ROOT": ROOT, "SCHEMA": AUDIT_SCHEMA, "verify_batch": verify_batch,
        "HospitalIESEnv": HospitalIESEnv, "window_targets": window_targets,
        "_payload_binding": _payload_binding, "_brief_markdown": _brief_markdown,
    })
    return _isolated(original_audit.audit_batch, namespace)(
        plan_path, results_dir, output_json=output_json, output_markdown=output_markdown,
        include_action_effect=include_action_effect)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    register = sub.add_parser("register")
    register.add_argument("--manifest", type=Path, required=True)
    register.add_argument("--plan", type=Path, required=True)
    register.add_argument("--scenario-ids", type=Path, required=True)
    register.add_argument("--policy-specs", type=Path, required=True)
    register.add_argument("--preference", type=Path, required=True)
    register.add_argument("--phase", choices=sorted(PHASES), required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--plan", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--max-new-pairs", type=int)
    audit = sub.add_parser("audit")
    audit.add_argument("--plan", type=Path, required=True)
    audit.add_argument("--out", "--results", dest="out", type=Path, required=True)
    audit.add_argument("--json", type=Path, required=True)
    audit.add_argument("--markdown", type=Path, required=True)
    audit.add_argument("--skip-action-effect", action="store_true")
    args = parser.parse_args(argv)
    if args.operation == "register":
        record = register_batch(
            args.manifest, args.plan, scenario_ids=_read(args.scenario_ids),
            policy_specs=_read(args.policy_specs), preference_path=args.preference,
            phase=args.phase)
        value = {"plan_signature": record["signature"],
                 "registered_pair_count": record["registered_pair_count"]}
    elif args.operation == "verify":
        record, _ = verify_batch(args.plan)
        value = {"plan_signature": record["signature"],
                 "registered_pair_count": record["registered_pair_count"], "verified": True}
    elif args.operation == "run":
        record = run_batch(args.plan, args.out, max_new_pairs=args.max_new_pairs)
        value = {key: record[key] for key in ("registered_pair_count", "terminal_pair_count",
                                             "unknown_primary_pair_count", "attempted_this_call")}
    else:
        record = audit_batch(args.plan, args.out, output_json=args.json,
                             output_markdown=args.markdown,
                             include_action_effect=not args.skip_action_effect)
        value = {"passed": record["passed"],
                 "registered_pair_count": record["registered_pair_count"],
                 "recomputed_pair_count": record["recomputed_pair_count"],
                 "unknown_primary_pair_count": len(record["unknown_primary_pair_ids"]),
                 "error_count": len(record["errors"])}
    print(json.dumps(value, ensure_ascii=False))
    return 1 if args.operation == "audit" and not record["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
