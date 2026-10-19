"""Read-only v6 matched-batch integrity, raw-score and consequence audit.

This reuses the original 19-target ``window_targets`` scorer. Recomputing a
saved trajectory checks score bookkeeping, not independent simulator physics.
All registered cases remain in the denominator, including failed, missing and
truncated attempts; their absent scores are null rather than zero.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.dataset_manifest import digest
from tools.control_improvement.branches import window_targets
from tools.control_improvement.ranking import TARGET_NAMES
from tools.policy_redesign.action_effect import diagnose_pair
from tools.policy_redesign.closed_loop import verify_batch
from tools.policy_search.preference import paired_metrics
from tools.policy_search.score_recheck import score_differences
from tools.reliability_first.closed_loop import _payload_binding


SCHEMA = "policy_redesign.v6.matched_batch_audit.v1"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        if isinstance(value, str):
            stream.write(value)
        else:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _null_score():
    return {"full_horizon": False, "targets": [None] * len(TARGET_NAMES)}


def _metric_row(sid, pid, *, status="missing", primary=None, full=None):
    return {"scenario_id": sid, "policy_id": pid, "status": status,
            "primary_score": _null_score() if primary is None else primary,
            "full_observation": _null_score() if full is None else full}


def _checked_raw_path(attempt, name):
    if name != "trajectory.json.gz":
        raise ValueError("Unexpected raw trajectory relative path")
    return attempt / name


def _inventory_attempts(results_dir, sid, pid, plan_signature, errors, warnings):
    pair = Path(results_dir) / "pairs" / digest([sid, pid])
    attempts = sorted(p for p in pair.glob("attempt_*") if p.is_dir()
                      and p.name[8:].isdigit())
    ledger = []
    latest = None
    latest_path = None
    for attempt in attempts:
        result_path = attempt / "result.json"
        raw_path = attempt / "trajectory.json.gz"
        item = {"attempt": attempt.name, "result_path": str(result_path),
                "result_sha256": None, "status": None,
                "terminal": None, "raw_path": str(raw_path) if raw_path.is_file() else None,
                "raw_sha256": _sha(raw_path) if raw_path.is_file() else None,
                "declared_raw_sha256": None, "error_sha256": None}
        if not result_path.is_file():
            errors.append(f"{sid}/{pid}/{attempt.name}: orphan attempt has no result receipt")
            ledger.append(item)
            continue
        try:
            row = _read(result_path)
            item.update(result_sha256=_sha(result_path), status=row.get("status"),
                        terminal=row.get("terminal"),
                        declared_raw_sha256=row.get("trajectory_sha256"))
            if (row.get("scenario_id") != sid or row.get("policy_id") != pid or
                    row.get("plan_signature") != plan_signature):
                errors.append(f"{sid}/{pid}/{attempt.name}: result/plan identity differs")
            declared_path = row.get("trajectory_path")
            if declared_path:
                _checked_raw_path(attempt, declared_path)
                if not raw_path.is_file():
                    errors.append(f"{sid}/{pid}/{attempt.name}: declared raw missing")
                elif item["raw_sha256"] != row.get("trajectory_sha256"):
                    errors.append(f"{sid}/{pid}/{attempt.name}: raw SHA differs from result")
            elif raw_path.is_file():
                warnings.append(f"{sid}/{pid}/{attempt.name}: failed/unknown raw is not bound by result SHA")
            if row.get("error_path"):
                if row["error_path"] != "error.json" or not (attempt / "error.json").is_file():
                    errors.append(f"{sid}/{pid}/{attempt.name}: error receipt missing")
                else:
                    item["error_sha256"] = _sha(attempt / "error.json")
            latest, latest_path = row, result_path
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{sid}/{pid}/{attempt.name}: malformed result {type(error).__name__}: {error}")
        ledger.append(item)
    return ledger, latest, latest_path


def _worst_cases(metric):
    result = {}
    for index, name in enumerate(TARGET_NAMES):
        observed = [(sid, case) for sid, case in metric["individual_cases"].items()
                    if case["complete_paired_window"]]
        if not observed:
            result[name] = None
            continue
        sid, case = max(observed, key=lambda item: (item[1]["delta_targets"][index], item[0]))
        result[name] = {"scenario_id": sid, "candidate_minus_secure":
                        case["delta_targets"][index],
                        "candidate_value": case["candidate_targets"][index],
                        "secure_value": case["baseline_targets"][index]}
    return result


def _compact_metric(metric):
    names = ("registered_case_count", "complete_paired_case_count", "unknown_case_ids",
             "new_P0_failure_cases", "resolved_P0_failure_cases",
             "largest_case_P0_failure_minutes_increase", "P0_failure_minutes_total_delta",
             "largest_case_P0_e_ENS_increase", "largest_case_P0_h_ENS_increase",
             "largest_case_P0_c_ENS_increase", "P0_e_ENS_kWh_delta",
             "P0_h_ENS_kWh_delta", "P0_c_ENS_kWh_delta", "total_delta_by_target")
    return {name: metric[name] for name in names}


def _subgroups(plan, scenes, metric_rows):
    specs = [spec["policy_id"] for spec in plan["policy_specs"]]

    def subgroup(ids):
        selected = [row for row in metric_rows if row["scenario_id"] in set(ids)]
        return {pid: {window: _compact_metric(paired_metrics(
                    selected, pid, score_field=field))
                for window, field in (("primary", "primary_score"),
                                      ("full", "full_observation"))}
                for pid in specs}

    by_condition = defaultdict(list)
    by_root = defaultdict(list)
    by_pressure = defaultdict(list)
    for sid in plan["scenario_ids"]:
        scene = scenes[sid]
        by_condition[scene.get("condition_id", "unregistered_condition")].append(sid)
        by_root[scene.get("root_family_id", sid)].append(sid)
        by_pressure[scene.get("pressure_point_index", "unregistered_pressure")].append(sid)
    conditions = []
    for condition, ids in sorted(by_condition.items()):
        conditions.append({"condition_id": condition, "scenario_ids": ids,
                           "root_family_ids": sorted({scenes[sid].get("root_family_id") for sid in ids}),
                           "pressure_point_indices": sorted({scenes[sid].get("pressure_point_index") for sid in ids}),
                           "policy_comparisons": subgroup(ids)})
    roots = []
    for root, ids in sorted(by_root.items()):
        pressure = [scenes[sid].get("pressure_point_index") for sid in ids]
        roots.append({"root_family_id": root,
                      "condition_ids": sorted({scenes[sid].get("condition_id") for sid in ids}),
                      "scenario_ids": ids, "pressure_point_indices": sorted(set(pressure)),
                      "registered_pressure_count": len(ids),
                      "complete_five_pressure_panel": len(ids) == 5 and set(pressure) == set(range(5)),
                      "policy_comparisons": subgroup(ids)})
    pressures = []
    for pressure, ids in sorted(by_pressure.items(), key=lambda item: str(item[0])):
        pressures.append({"pressure_point_index": pressure, "scenario_ids": ids,
                          "policy_comparisons": subgroup(ids)})
    return {"by_condition": conditions, "by_root": roots, "by_pressure_point": pressures,
            "five_pressure_design_indices": [0, 1, 2, 3, 4],
            "pressure_points_are_named_conditions_not_probability_weights": True}


def _action_effects(plan, raw_paths, warnings):
    effects = {}
    for spec in plan["policy_specs"]:
        pid = spec["policy_id"]
        if pid == "secure_milp":
            continue
        cases = []
        for sid in plan["scenario_ids"]:
            candidate = raw_paths.get((sid, pid))
            secure = raw_paths.get((sid, "secure_milp"))
            if candidate is None or secure is None:
                cases.append({"scenario_id": sid, "available": False,
                              "reason": "candidate_or_secure_verified_raw_missing"})
                continue
            try:
                diagnosis = diagnose_pair(candidate, secure)
                cases.append({"scenario_id": sid, "available": True,
                              "candidate_status": diagnosis["candidate_status"],
                              "secure_status": diagnosis["secure_status"],
                              "decision_count": diagnosis["decision_count"],
                              "request_changed_decisions": diagnosis["request_changed_decisions"],
                              "current_plan_changed_decisions": diagnosis["current_plan_changed_decisions"],
                              "parallel_execution_changed_decisions": diagnosis["parallel_execution_changed_decisions"],
                              "command_arrival_events": diagnosis["command_arrival_events"],
                              "request_changed_by_port": diagnosis["request_changed_by_port"],
                              "current_plan_changed_by_port": diagnosis["current_plan_changed_by_port"],
                              "parallel_execution_changed_by_port": diagnosis["parallel_execution_changed_by_port"]})
            except (OSError, ValueError, KeyError, TypeError) as error:
                warnings.append(f"{sid}/{pid}: action-effect diagnostic unavailable: {error}")
                cases.append({"scenario_id": sid, "available": False,
                              "reason": str(error)})
        available = [row for row in cases if row["available"]]
        fields = ("decision_count", "request_changed_decisions",
                  "current_plan_changed_decisions", "parallel_execution_changed_decisions",
                  "command_arrival_events")
        effects[pid] = {"registered_case_count": len(cases),
                        "available_case_count": len(available),
                        "totals_over_available_cases_only": {
                            name: sum(row[name] for row in available) for name in fields},
                        "cases": cases,
                        "parallel_execution_after_divergence_is_not_same_state": True}
    return effects


def _brief_markdown(report):
    lines = ["# v6 匹配批次原始轨迹审查", "",
             f"- 审查通过：{report['passed']}",
             f"- 登记/终局/重算：{report['registered_pair_count']}/"
             f"{report['terminal_pair_count']}/{report['recomputed_pair_count']}",
             f"- 主窗口未知：{len(report['unknown_primary_pair_ids'])}；完整观察未知："
             f"{len(report['unknown_full_pair_ids'])}",
             f"- 初态与外生流匹配：{report['matched_initial_state_and_stream']}",
             f"- 条件/根/完整五压力根：{len(report['subgroups']['by_condition'])}/"
             f"{len(report['subgroups']['by_root'])}/"
             f"{sum(x['complete_five_pressure_panel'] for x in report['subgroups']['by_root'])}",
             "", "原始轨迹使用原 19 项评分代码重算；这验证记录和算术，不构成独立物理模型验证。", ""]
    for pid, comparison in report["policy_comparisons"].items():
        if pid == "secure_milp":
            continue
        metric = comparison["primary"]
        lines.extend([f"## {pid}", "",
                      f"完整配对：{metric['complete_paired_case_count']}/"
                      f"{metric['registered_case_count']}；新增/消除 P0 失效："
                      f"{metric['new_P0_failure_cases']}/{metric['resolved_P0_failure_cases']}；"
                      f"P0 时长差：{metric['P0_failure_minutes_total_delta']} 分钟。", ""])
    if report["errors"]:
        lines.extend(["## 审查错误", ""])
        lines.extend(f"- {error}" for error in report["errors"])
        lines.append("")
    return "\n".join(lines)


def audit_batch(plan_path, results_dir, *, output_json=None, output_markdown=None,
                include_action_effect=True):
    """Audit one v6 batch without rerunning physics or changing saved attempts."""
    plan_path = Path(plan_path).resolve()
    results_dir = Path(results_dir).resolve()
    plan, manifest = verify_batch(plan_path)
    summaries = sorted(results_dir.glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("V6 batch has no saved summary")
    summary_path = summaries[-1]
    summary = _read(summary_path)
    errors, warnings = [], []
    if summary.get("plan_signature") != plan["signature"]:
        errors.append("Summary/registered plan signature differs")
    copy_path = results_dir / "registered_plan.json"
    if copy_path.is_file() and _read(copy_path) != plan:
        errors.append("Runner copy of registered plan differs")
    if not copy_path.is_file():
        warnings.append("Runner copy of registered plan absent")
    if plan.get("source_sha256", {}).get("tools/control_improvement/branches.py") != _sha(
            ROOT / "tools/control_improvement/branches.py"):
        errors.append("Original 19-target scoring source changed after registration")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    ids = plan["scenario_ids"]
    specs = plan["policy_specs"]
    expected = {(sid, spec["policy_id"]) for sid in ids for spec in specs}
    indexed = {}
    for row in summary.get("results", []):
        key = (row.get("scenario_id"), row.get("policy_id"))
        if key in indexed:
            errors.append(f"Duplicate summary pair: {key}")
        indexed[key] = row
    if set(indexed) - expected:
        errors.append("Summary has unregistered scenario/policy pairs")
    if summary.get("registered_pair_count") != len(expected):
        errors.append("Summary registered denominator differs from plan")
    if summary.get("recorded_pair_count") != len(summary.get("results", [])):
        errors.append("Summary recorded denominator differs from result rows")
    if summary.get("terminal_pair_count") != sum(bool(row.get("terminal")) for row in indexed.values()):
        errors.append("Summary terminal denominator differs from result rows")
    env = HospitalIESEnv(ROOT, execution_contract=manifest["execution_contract"])
    ledger, metric_rows, raw_paths = [], [], {}
    raw_checked = 0
    for sid in ids:
        scene = scenes.get(sid)
        if scene is None or scene.get("is_final_blind"):
            errors.append(f"{sid}: scene absent or sealed final")
            continue
        for spec in specs:
            pid = spec["policy_id"]
            label = f"{sid}/{pid}"
            attempts, latest, latest_path = _inventory_attempts(
                results_dir, sid, pid, plan["signature"], errors, warnings)
            summary_row = indexed.get((sid, pid))
            item = {"scenario_id": sid, "policy_id": pid, "kind": spec["kind"],
                    "condition_id": scene.get("condition_id"),
                    "root_family_id": scene.get("root_family_id"),
                    "pressure_point_index": scene.get("pressure_point_index"),
                    "status": None if latest is None else latest.get("status"),
                    "terminal": None if latest is None else latest.get("terminal"),
                    "attempts": attempts,
                    "latest_result_sha256": None if latest_path is None else _sha(latest_path),
                    "score_recomputed": False, "binding_passed": None,
                    "primary_full_horizon": None, "full_observation_horizon": None}
            row_for_metric = _metric_row(sid, pid,
                status="missing" if latest is None else latest.get("status", "unknown"))
            if latest is None:
                errors.append(f"{label}: registered pair has no result receipt")
            elif summary_row != latest:
                errors.append(f"{label}: summary row differs from latest result receipt")
            if latest is not None and latest.get("terminal") is True:
                try:
                    if not latest.get("trajectory_path") or not latest.get("trajectory_sha256"):
                        raise ValueError("terminal result has no declared raw")
                    raw_path = _checked_raw_path(latest_path.parent, latest["trajectory_path"])
                    if not raw_path.is_file() or _sha(raw_path) != latest["trajectory_sha256"]:
                        raise ValueError("raw SHA differs from terminal result")
                    with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
                        payload = json.load(stream, parse_constant=lambda value:
                                            (_ for _ in ()).throw(ValueError(value)))
                    if (payload.get("scenario", {}).get("id") != sid or
                            payload.get("identity", {}).get("policy_id") != pid or
                            payload.get("summary", {}).get("status") != latest.get("status")):
                        raise ValueError("raw identity/status differs from result")
                    binding = _payload_binding(payload, scene, manifest)
                    item["binding_passed"] = binding["passed"]
                    item["recomputed_binding"] = binding
                    if binding != latest.get("binding") or not binding["passed"]:
                        errors.append(f"{label}: raw/manifest matched binding differs or fails")
                    primary = window_targets(payload, 60.0, 360.0, env)
                    full = window_targets(payload, 0.0, 600.0, env)
                    if len(primary.get("targets", [])) != len(TARGET_NAMES) or len(
                            full.get("targets", [])) != len(TARGET_NAMES):
                        raise ValueError("recomputed target count is not 19")
                    differences = (score_differences(latest.get("primary_score"), primary,
                                                     "primary_score") +
                                   score_differences(latest.get("full_observation"), full,
                                                     "full_observation"))
                    errors.extend(f"{label}: {difference}" for difference in differences)
                    # An already observed P0 failure is decisive even when the
                    # remaining window is unknown.  Original window_targets
                    # may therefore expose target 0 while masking targets 1:.
                    if (not primary["full_horizon"] and
                            any(v is not None for v in primary["targets"][1:])):
                        errors.append(f"{label}: incomplete primary score contains non-null future target")
                    if (not full["full_horizon"] and
                            any(v is not None for v in full["targets"][1:])):
                        errors.append(f"{label}: incomplete full score contains non-null future target")
                    item.update(score_recomputed=True,
                                primary_full_horizon=primary["full_horizon"],
                                full_observation_horizon=full["full_horizon"],
                                recomputed_primary_score=primary,
                                recomputed_full_observation=full)
                    raw_checked += 1
                    if binding["passed"]:
                        row_for_metric = _metric_row(sid, pid, status=latest["status"],
                                                     primary=primary, full=full)
                        raw_paths[(sid, pid)] = raw_path
                except (OSError, ValueError, KeyError, TypeError, EOFError) as error:
                    errors.append(f"{label}: raw score recheck failed: {type(error).__name__}: {error}")
            elif latest is not None:
                errors.append(f"{label}: latest result is nonterminal; outcome remains unknown")
            ledger.append(item)
            metric_rows.append(row_for_metric)
    complete_rows = [item for item in ledger if item["terminal"] is True]
    matched = None
    if len(complete_rows) == len(expected):
        matched = all(item["binding_passed"] is True for item in ledger)
        if matched:
            matched = all(
                len({item["recomputed_binding"]["initial_state_sha256"]
                     for item in ledger if item["scenario_id"] == sid}) == 1
                and len({item["recomputed_binding"]["exogenous_stream_id"]
                         for item in ledger if item["scenario_id"] == sid}) == 1
                for sid in ids)
    if summary.get("matched_initial_state_and_stream") != matched:
        errors.append("Summary matched initial state/exogenous stream receipt differs")
    if matched is False:
        errors.append("Registered policies are not matched on initial state and stream")
    policy_comparisons = {}
    for spec in specs:
        pid = spec["policy_id"]
        primary = paired_metrics(metric_rows, pid, score_field="primary_score")
        full = paired_metrics(metric_rows, pid, score_field="full_observation")
        policy_comparisons[pid] = {"kind": spec["kind"], "primary": primary,
                                   "full": full,
                                   "worst_case_by_target": _worst_cases(primary),
                                   "full_worst_case_by_target": _worst_cases(full)}
    unknown_primary = [f"{row['scenario_id']}/{row['policy_id']}" for row in metric_rows
                       if not row["primary_score"]["full_horizon"]]
    unknown_full = [f"{row['scenario_id']}/{row['policy_id']}" for row in metric_rows
                    if not row["full_observation"]["full_horizon"]]
    if summary.get("unknown_primary_pair_count") is not None and summary[
            "unknown_primary_pair_count"] != sum(
                item["terminal"] is True and item["primary_full_horizon"] is False
                for item in ledger):
        errors.append("Summary unknown primary count differs from recomputed terminal rows")
    subgroups = _subgroups(plan, scenes, metric_rows)
    action_effect = _action_effects(plan, raw_paths, warnings) if include_action_effect else None
    for pid, value in (action_effect or {}).items():
        policy_comparisons[pid]["action_effect"] = value
    report = {"schema": SCHEMA,
              "passed": not errors and len(complete_rows) == len(expected) and matched is True,
              "errors": errors, "warnings": warnings,
              "method": "Original window_targets on saved gzip raw, exact field equality; not independent physics validation",
              "plan_path": str(plan_path), "plan_sha256": _sha(plan_path),
              "plan_signature": plan["signature"],
              "summary_path": str(summary_path), "summary_sha256": _sha(summary_path),
              "manifest_path": plan["manifest_path"],
              "manifest_sha256": _sha(plan["manifest_path"]),
              "scoring_source_sha256": _sha(ROOT / "tools/control_improvement/branches.py"),
              "phase": plan["phase"], "target_names": list(TARGET_NAMES),
              "registered_pair_count": len(expected),
              "recorded_pair_count": len(indexed),
              "terminal_pair_count": len(complete_rows),
              "recomputed_pair_count": raw_checked,
              "unknown_primary_pair_ids": sorted(unknown_primary),
              "unknown_full_pair_ids": sorted(unknown_full),
              "unknown_is_null_not_zero": True,
              "matched_initial_state_and_stream": matched,
              "pair_ledger": ledger, "policy_comparisons": policy_comparisons,
              "subgroups": subgroups, "action_effect_included": include_action_effect,
              "all_registered_denominator_includes_unknown": True,
              "final_blind_opened": False}
    if output_json is not None:
        _write_new(output_json, report)
    if output_markdown is not None:
        _write_new(output_markdown, _brief_markdown(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--skip-action-effect", action="store_true")
    args = parser.parse_args(argv)
    record = audit_batch(args.plan, args.results, output_json=args.json,
                         output_markdown=args.markdown,
                         include_action_effect=not args.skip_action_effect)
    print(json.dumps({"passed": record["passed"],
                      "registered_pair_count": record["registered_pair_count"],
                      "recomputed_pair_count": record["recomputed_pair_count"],
                      "unknown_primary_pair_count": len(record["unknown_primary_pair_ids"]),
                      "error_count": len(record["errors"]),
                      "json": str(args.json), "markdown": str(args.markdown)},
                     ensure_ascii=False))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
