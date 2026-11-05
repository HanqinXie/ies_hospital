"""Read-only, source-bound audit of frozen probe-v2 set-order identity unknowns.

This never runs a continuation or changes the official probe status. It reads
saved compressed trajectories and canonicalizes only RuntimeState.extra_faulted,
which is set[str] in the physical core but an unordered JSON list on export.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_export import json_clean
from tools.control_improvement.branches import window_targets
from tools.reliability_first.branches import _execution_class
from tools.reliability_first.diagnostics import opportunity_rows
from tools.reliability_first import soft_objective_probe as probe


SCHEMA = "reliability_first.soft_probe_identity_order_audit.v1"
IDENTITY_ERROR = "New current-action continuation identity mismatch"
PRIORITIES = ("P0", "P1", "P2", "P3")
LABELS = ("better", "tie", "worse", "unknown")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _within(run_dir: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Provenance path escapes registered run")
    return run_dir / path


def _extra_faulted_list(initial: dict) -> list[str]:
    try:
        values = initial["state"]["extra_faulted"]
    except (KeyError, TypeError) as exc:
        raise ValueError("initial_state.state.extra_faulted is absent") from exc
    if (not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values)
            or len(values) != len(set(values))):
        raise ValueError("initial_state.state.extra_faulted must be a unique nonempty string list")
    return values


def verify_initial_state_order_only(reference: dict, actual: dict) -> bool:
    """Require exact non-set state; return whether the one set field changed order."""
    old = _extra_faulted_list(reference)
    new = _extra_faulted_list(actual)
    if sorted(old) != sorted(new):
        raise ValueError("initial_state.state.extra_faulted membership differs")
    canonical_reference = deepcopy(reference)
    canonical_actual = deepcopy(actual)
    canonical_reference["state"]["extra_faulted"] = sorted(old)
    canonical_actual["state"]["extra_faulted"] = sorted(new)
    if canonical_reference != canonical_actual:
        raise ValueError("initial_state has a non-set field mismatch")
    return old != new


def _verified_raw(run_dir: Path, relative: str, expected: str, result: dict,
                  summary: dict, raw_hashes: dict) -> dict:
    path = _within(run_dir, relative)
    if (result["raw_trajectory_hashes"].get(relative) != expected
            or summary["raw_trajectory_hashes"].get(relative) != expected
            or probe.sha(path) != expected):
        raise ValueError(f"Frozen raw trajectory SHA mismatch: {relative}")
    raw_hashes[relative] = expected
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _same_window(saved: dict, recalculated: dict) -> None:
    if (saved["targets"] != json_clean(recalculated["targets"])
            or saved["target_mask"] != recalculated["target_mask"]):
        raise ValueError("Recomputed secure window differs from frozen v1 targets")


def _validate_candidate_raw(payload: dict, *, future_scenario: dict, reference: dict,
                            entry: dict, manifest: dict, candidate: dict,
                            future_id: str, candidate_index: int) -> bool:
    summary = payload.get("summary", {})
    decisions = payload.get("decisions", [])
    if (payload.get("scenario") != future_scenario
            or payload.get("seed") != manifest["options"]["seed"]
            or float(payload.get("continuation_start_minute", -1)) != float(entry["snapshot_minute"])
            or summary.get("scenario_id") != entry["scenario_id"]
            or summary.get("group_id") != entry["group_id"]
            or summary.get("experiment_role") != "pilot_train"
            or summary.get("manifest_signature") != manifest["signature"]
            or summary.get("policy_id") != f"v4_soft_probe_{future_id}_{candidate_index:02d}"
            or not decisions
            or float(decisions[0].get("decision_time_min", -1)) != float(entry["snapshot_minute"])
            or decisions[0].get("requested_command") != candidate["request"]):
        raise ValueError("Saved candidate scenario/group/manifest/seed/request identity mismatch")
    return verify_initial_state_order_only(reference["initial_state"], payload["initial_state"])


def _audit_one(run_dir: Path, index: int, entry: dict, manifest: dict,
               summary: dict, result_record: dict, binding: dict, env: HospitalIESEnv,
               raw_hashes: dict) -> dict:
    result_path = _within(run_dir, result_record["result_path"])
    if probe.sha(result_path) != result_record["result_sha256"]:
        raise ValueError("Frozen probe result SHA mismatch")
    result = _load(result_path)
    attempt = result_path.parent
    attempt_plan_path = attempt / "plan.json"
    attempt_plan = _load(attempt_plan_path)
    if (result.get("schema") != "reliability_first.soft_objective_probe_result.v1"
            or result.get("status") != "unknown" or result.get("index") != index
            or result.get("scenario_id") != entry["scenario_id"]
            or result.get("group_id") != entry["group_id"]
            or result.get("snapshot_minute") != entry["snapshot_minute"]
            or result.get("role") != "train" or result.get("source_binding") != binding
            or attempt_plan.get("entry") != entry
            or attempt_plan.get("source_binding") != binding
            or attempt_plan.get("design_sha256") != binding["design_sha256"]):
        raise ValueError("Frozen probe result/plan registration mismatch")
    source = probe.verify_saved_source(run_dir, index, entry, manifest,
                                       binding["branch_source_sha256"])
    if result.get("source_result_sha256") != source["source_result_sha256"]:
        raise ValueError("Frozen v1 branch result SHA differs from v2 result")
    if result.get("saved_execution_snapshot_content_sha256") != source["snapshot_content_sha256"]:
        raise ValueError("Frozen execution snapshot binding differs")
    expected_candidates = probe.make_candidate_requests(
        source["current"]["context"], source["current"]["candidates"][0])
    candidates = result["candidates"]
    if [(c["candidate_id"], c["request"]) for c in candidates] != [
            (c["candidate_id"], c["request"]) for c in expected_candidates]:
        raise ValueError("Saved candidate requests differ from registered current-only design")
    if candidates[0]["current_command"] != source["current"]["candidates"][0]["current_command"]:
        raise ValueError("Saved secure full dispatch differs from original source")
    baseline_outcomes = {}
    rows = []
    for future_id in probe.FUTURE_IDS:
        old = source["baseline_outcomes"][future_id]
        relative = (source["root"] / old["trajectory_file"]).relative_to(run_dir).as_posix()
        payload = _verified_raw(run_dir, relative, old["trajectory_sha256"], result,
                                summary, raw_hashes)
        if payload != source["baseline_payloads"][future_id]:
            raise ValueError("Re-read secure raw differs from registered source")
        if payload["seed"] != manifest["options"]["seed"]:
            raise ValueError("Secure future seed differs from manifest")
        window = window_targets(payload, entry["snapshot_minute"], 120., env)
        _same_window({"targets": old["targets"], "target_mask": old["window"]["target_mask"]}, window)
        stored = next((o for o in result["outcomes"] if o["future_id"] == future_id
                       and o["candidate_id"] == "secure_baseline"), None)
        if (stored is None or stored["trajectory_file"] != relative
                or stored["trajectory_sha256"] != old["trajectory_sha256"]
                or stored["targets"] != window["targets"]
                or stored["target_mask"] != window["target_mask"]):
            raise ValueError("Frozen v2 secure row differs from recomputed raw")
        baseline_outcomes[future_id] = (payload, window)
        rows.append({"future_id": future_id, "candidate_id": "secure_baseline",
                     "status": payload["summary"]["status"],
                     "target_names": window["target_names"], "targets": window["targets"],
                     "target_mask": window["target_mask"],
                     "effective_execution_class": _execution_class(payload, entry["snapshot_minute"])})
    error_keys = {(e.get("future_id"), e.get("candidate_id")) for e in result["errors"]
                  if e.get("type") == "ValueError" and e.get("message") == IDENTITY_ERROR}
    if len(error_keys) != len(result["errors"]):
        raise ValueError("Official unknown includes a cause other than documented identity comparison")
    journal = [json.loads(line) for line in (attempt / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    pairs = []
    for future_index, future_id in enumerate(probe.FUTURE_IDS):
        reference, _ = baseline_outcomes[future_id]
        future_scenario = source["futures"]["scenarios"][future_index]
        for candidate_index, candidate in enumerate(candidates[1:], 1):
            cid = candidate["candidate_id"]
            if candidate.get("projection_status") != "success":
                raise ValueError("Official unknown includes an unprojected candidate")
            if (future_id, cid) not in error_keys:
                raise ValueError("Official continuation error is not the documented identity mismatch")
            stored = [o for o in result["outcomes"] if o["future_id"] == future_id
                      and o["candidate_id"] == cid]
            if len(stored) != 1 or stored[0]["status"] != "continuation_error":
                raise ValueError("Official unknown outcome not retained as continuation_error")
            relative = (attempt / f"future_{future_index:02d}_candidate_{candidate_index:02d}.json.gz").relative_to(run_dir).as_posix()
            expected_sha = result["raw_trajectory_hashes"].get(relative)
            if not expected_sha:
                raise ValueError("Documented identity unknown has no saved raw")
            starts = [j for j in journal if j.get("event") == "started"
                      and j.get("attempt_type") == "current_action_future_continuation"
                      and j.get("future_id") == future_id and j.get("candidate_id") == cid]
            finishes = [j for j in journal if j.get("event") == "finished"
                        and j.get("attempt_type") == "current_action_future_continuation"
                        and j.get("future_id") == future_id and j.get("candidate_id") == cid]
            if (len(starts) != 1 or len(finishes) != 1
                    or finishes[0].get("status") != "error"
                    or finishes[0].get("message") != IDENTITY_ERROR
                    or finishes[0].get("raw_sha256") != expected_sha):
                raise ValueError("Continuation attempt journal differs from frozen identity error")
            payload = _verified_raw(run_dir, relative, expected_sha, result, summary, raw_hashes)
            reordered = _validate_candidate_raw(payload, future_scenario=future_scenario,
                reference=reference, entry=entry, manifest=manifest, candidate=candidate,
                future_id=future_id, candidate_index=candidate_index)
            if not reordered:
                raise ValueError("Frozen identity error is not explained by extra_faulted order")
            window = window_targets(payload, entry["snapshot_minute"], 120., env)
            rows.append({"future_id": future_id, "candidate_id": cid,
                         "status": payload["summary"]["status"],
                         "target_names": window["target_names"], "targets": window["targets"],
                         "target_mask": window["target_mask"],
                         "effective_execution_class": _execution_class(payload, entry["snapshot_minute"])})
            pairs.append({"future_id": future_id, "candidate_id": cid,
                          "candidate_index": candidate_index, "raw_path": relative,
                          "raw_sha256": expected_sha, "original_v2_status": stored[0]["status"],
                          "derived_status": payload["summary"]["status"],
                          "extra_faulted_order_only": True,
                          "window_targets": window["targets"],
                          "window_target_mask": window["target_mask"]})
    expected_raw_count = len(probe.FUTURE_IDS) * len(candidates)
    if len(result["raw_trajectory_hashes"]) != expected_raw_count or len(pairs) != len(error_keys):
        raise ValueError("Frozen unknown has unaccounted raw or identity error")
    opportunities = probe.opportunity_rows(rows, baseline_candidate_id="secure_baseline")
    by_opportunity = {o["candidate_id"]: o for o in opportunities}
    from tools.reliability_first.soft_objective_probe import classify_paired_targets
    row_by_key = {(r["candidate_id"], r["future_id"]): r for r in rows}
    for pair in pairs:
        cid, future_id = pair["candidate_id"], pair["future_id"]
        candidate_row = row_by_key[(cid, future_id)]
        baseline_row = row_by_key[("secure_baseline", future_id)]
        pair["P0_comparison"] = next(f["comparison"] for f in by_opportunity[cid]["per_future"]
                                      if f["future_id"] == future_id)
        service = classify_paired_targets(candidate_row["targets"], baseline_row["targets"],
            actual_mask=candidate_row["target_mask"], reference_mask=baseline_row["target_mask"])
        pair["service_by_priority"] = service["by_priority"]
        pair["delta_targets_original_units"] = service["delta"]
    return {"index": index, "scenario_id": entry["scenario_id"],
            "group_id": entry["group_id"], "role": "train",
            "snapshot_minute": entry["snapshot_minute"], "source_result_sha256": result["source_result_sha256"],
            "probe_result_sha256": result_record["result_sha256"],
            "attempt_plan_sha256": probe.sha(attempt_plan_path),
            "secure_raw_count": len(baseline_outcomes), "new_raw_count": len(pairs),
            "pairs": pairs, "opportunities": opportunities}


def build_identity_order_audit(run_dir: Path) -> dict:
    """Derive a separate, immutable candidate/future audit from registered raw only."""
    run_dir = Path(run_dir).resolve()
    design, manifest, plan, binding = probe._validate_design(run_dir)
    summary_path = run_dir / design["output_directory"] / "PROBE_SUMMARY.json"
    summary = _load(summary_path)
    if (summary.get("schema") != "reliability_first.soft_objective_probe_summary.v1"
            or summary.get("status") != "complete"
            or summary.get("source_binding") != binding
            or len(summary.get("results", [])) != design["registered_snapshots"]
            or [r["index"] for r in summary["results"]] != design["selected_snapshot_indices"]):
        raise ValueError("Frozen probe summary/registration mismatch")
    unknowns = [r for r in summary["results"] if r["status"] == "unknown"]
    if (summary["unknown_snapshots"] != len(unknowns)
            or summary["completed_snapshots"] + len(unknowns) != design["registered_snapshots"]):
        raise ValueError("Frozen probe terminal counts mismatch")
    env = HospitalIESEnv(probe.ROOT, execution_contract=manifest["execution_contract"])
    raw_hashes = {}
    cases = []
    for record in unknowns:
        index = record["index"]
        entry = plan[index]
        if entry["experiment_role"] != "pilot_train" or entry["is_final_blind"]:
            raise ValueError("Order audit requires registered training snapshots only")
        cases.append(_audit_one(run_dir, index, entry, manifest, summary, record,
                                binding, env, raw_hashes))
    per_candidate = defaultdict(Counter)
    per_future = defaultdict(Counter)
    service_by_candidate = defaultdict(lambda: defaultdict(Counter))
    service_by_future = defaultdict(lambda: defaultdict(Counter))
    strict_candidate_count = 0
    strict_snapshot_indices = set()
    for case in cases:
        for opportunity in case["opportunities"]:
            if opportunity["candidate_id"] == "secure_baseline":
                continue
            if opportunity["strict_opportunity_across_stress_set"] is True:
                strict_candidate_count += 1
                strict_snapshot_indices.add(case["index"])
        for pair in case["pairs"]:
            label = pair["P0_comparison"]
            per_candidate[pair["candidate_id"]][label] += 1
            per_future[pair["future_id"]][label] += 1
            for priority, service_label in pair["service_by_priority"].items():
                service_by_candidate[pair["candidate_id"]][priority][service_label] += 1
                service_by_future[pair["future_id"]][priority][service_label] += 1
    source_dir = Path(__file__).resolve().parent
    repository_root = source_dir.parents[1]
    source_binding = dict(binding)
    source_binding.update({
        "audit_source_sha256": probe.sha(__file__),
        "probe_summary_sha256": probe.sha(summary_path),
        "result_sha256_by_index": {str(c["index"]): c["probe_result_sha256"] for c in cases},
        "attempt_plan_sha256_by_index": {str(c["index"]): c["attempt_plan_sha256"] for c in cases},
        "source_result_sha256_by_index": {str(c["index"]): c["source_result_sha256"] for c in cases},
        "window_targets_source_sha256": probe.sha(repository_root / "tools/control_improvement/branches.py"),
        "window_outcome_source_sha256": probe.sha(repository_root / "tools/integrated_learning/truth.py"),
        "diagnostics_source_sha256": probe.sha(source_dir / "diagnostics.py"),
        "energy_fee_source_sha256": probe.sha(repository_root / "tools/integrated_experiment/energy_metrics.py"),
        "target_names_source_sha256": probe.sha(repository_root / "tools/control_improvement/ranking.py"),
        "physical_state_source_sha256": probe.sha(repository_root / "src/hospital_ies/resilience.py"),
        "initial_state_serialization_source_sha256": probe.sha(repository_root / "src/hospital_ies/dataset_export.py"),
        "energy_tariff_source_sha256": probe.sha(repository_root / "src/hospital_ies/model.py"),
    })
    return {"schema": SCHEMA, "status": "complete",
            "scope": "training-side ex-post adjudication of frozen probe-v2 identity unknowns",
            "derived_from_saved_raw_only": True, "new_rollout_attempts": 0,
            "official_v2_status_preserved": {"completed_snapshots": summary["completed_snapshots"],
                                             "unknown_snapshots": summary["unknown_snapshots"]},
            "unknown_snapshot_indices_examined": [r["index"] for r in unknowns],
            "affected_snapshot_indices": [c["index"] for c in cases],
            "verified_new_raw_count": sum(c["new_raw_count"] for c in cases),
            "verified_secure_raw_count": sum(c["secure_raw_count"] for c in cases),
            "order_only_verified_pair_count": sum(len(c["pairs"]) for c in cases),
            "unresolved_pair_count": 0,
            "per_candidate": {k: {label: v[label] for label in LABELS}
                              for k, v in sorted(per_candidate.items())},
            "per_future": {k: {label: per_future[k][label] for label in LABELS}
                           for k in probe.FUTURE_IDS},
            "service_by_candidate": {k: {p: {label: v[p][label] for label in LABELS}
                                         for p in PRIORITIES}
                                     for k, v in sorted(service_by_candidate.items())},
            "service_by_future": {k: {p: {label: service_by_future[k][p][label] for label in LABELS}
                                  for p in PRIORITIES} for k in probe.FUTURE_IDS},
            "strict_opportunity_candidate_count": strict_candidate_count,
            "strict_opportunity_snapshot_count": len(strict_snapshot_indices),
            "source_binding": source_binding,
            "raw_trajectory_hashes": dict(sorted(raw_hashes.items())),
            "cases": cases,
            "interpretation": "Derived paired outcomes are separate diagnostics; frozen probe v2 unknowns and its summary remain unchanged.",
            "limitation": "Identity proof covers saved exported initial_state and registered source/raw bytes only. It does not independently inspect hidden in-memory state or clinically validate the surrogate; no new rollout was run."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--write", action="store_true", help="Write one immutable sidecar; default is read-only check")
    args = parser.parse_args(argv)
    audit = build_identity_order_audit(args.run_dir)
    if args.write:
        path = args.run_dir / "soft_objective_probe_v2/IDENTITY_ORDER_AUDIT_v1.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(json_clean(audit), stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({k: audit[k] for k in ("schema", "status", "affected_snapshot_indices",
          "verified_new_raw_count", "verified_secure_raw_count", "order_only_verified_pair_count",
          "unresolved_pair_count", "strict_opportunity_candidate_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
