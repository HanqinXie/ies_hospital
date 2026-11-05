"""Gate and compose the v5 Chinese final report from audited, registered data.

Preview is read-only. --write creates POLICY_SEARCH_REPORT.md exclusively, only
after the development evidence and final run accounting pass exact-byte checks.
The report's recommendation labels are conservative review aids, never a
clinical safety certificate or an automatic deployment decision.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import re

from tools.policy_search.closed_loop import verify_batch
from tools.policy_search.run_accounting import collect_accounting
from tools.policy_search.sensitivity import verify_preregistration


ROOT = Path(__file__).resolve().parents[2]
REQUIRED = (
    "design/POLICY_REPORT_PREDEV_DRAFT.md",
    "development/registered_plan.json",
    "development/AUDIT.json",
    "development/SCORE_RECHECK.json",
    "development/ANALYSIS_PREFERENCE_V2.json",
    "development/ANALYSIS_FULL_WINDOW.json",
    "development/SENSITIVITY_PREREGISTRATION.json",
    "development/SENSITIVITY_BY_CONDITION_PRESSURE.json",
    "review/RUN_ACCOUNTING.json",
)
P0_NAMES = ("P0_failure", "P0_failure_minutes", "P0_e_ENS_kWh",
            "P0_h_ENS_kWh", "P0_c_ENS_kWh", "P0_max_temperature_C")
STATIC_HEADINGS = (
    "不变边界、来源和评价口径",
    "动作自由度、命令执行和损失机制",
    "训练侧完整策略搜索和 CUDA 学习",
    "独立选择与校准：按同根共同支持比较",
    "模式修复支线的证据等级",
)


class ReportBlocked(ValueError):
    """Required evidence is missing, stale or not complete."""


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def missing_required_artifacts(run_dir: Path) -> list[str]:
    run = Path(run_dir)
    return [f"missing {name}" for name in REQUIRED if not (run / name).is_file()]


def _require(value: bool, message: str) -> None:
    if not value:
        raise ReportBlocked(message)


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _case_complete(row: dict) -> bool:
    return row.get("complete_paired_window") is True


def _target_names(metrics: dict, policy_ids: list[str]) -> list[str]:
    names = metrics[policy_ids[0]].get("target_names")
    if (not isinstance(names, list) or len(names) != 19 or len(set(names)) != 19 or
            not set(P0_NAMES) <= set(names) or "actual_energy_fee_USD" not in names):
        raise ReportBlocked("audited 19-target names or P0/cost columns differ")
    if any(metrics[pid].get("target_names") != names for pid in policy_ids):
        raise ReportBlocked("policy target-name order differs")
    return names


def _metric_cases(metric: dict, registered_ids: list[str]) -> list[str]:
    cases = metric.get("individual_cases") or {}
    if set(cases) != set(registered_ids):
        raise ReportBlocked("audited metric case inventory differs from registration")
    return [sid for sid in registered_ids if _case_complete(cases[sid])]


def _common_support(metrics: dict, policy_ids: list[str],
                    registered_ids: list[str]) -> list[str]:
    for pid in policy_ids:
        _metric_cases(metrics[pid], registered_ids)
    return [sid for sid in registered_ids if all(
        _case_complete(metrics[pid]["individual_cases"][sid]) for pid in policy_ids)]


def validate_full_common_metrics(own_metrics: dict, common_metrics: dict,
                                 policy_ids: list[str], registered_ids: list[str],
                                 audited_common_ids: list[str]) -> list[str]:
    """Full common metrics intentionally omit unknown registered cases."""
    computed = _common_support(own_metrics, policy_ids, registered_ids)
    if set(computed) != set(audited_common_ids) or len(computed) != len(audited_common_ids):
        raise ReportBlocked("full analysis all-policy common support differs")
    for pid in policy_ids:
        cases = common_metrics[pid].get("individual_cases") or {}
        if set(cases) != set(computed) or not all(_case_complete(row) for row in cases.values()):
            raise ReportBlocked("full common-support metric case inventory differs")
    return computed


def _sum_on_support(metric: dict, case_ids: list[str]) -> list[float | None]:
    values = [[] for _ in range(19)]
    for sid in case_ids:
        row = metric["individual_cases"][sid]
        if not _case_complete(row):
            raise ReportBlocked("common support includes incomplete paired case")
        delta = row.get("delta_targets")
        if not isinstance(delta, list) or len(delta) != 19:
            raise ReportBlocked("complete paired case lacks 19 target deltas")
        for index, item in enumerate(delta):
            if item is not None and not _numeric(item):
                raise ReportBlocked("nonnumeric audited target delta")
            values[index].append(item)
    return [sum(col) if col and all(item is not None for item in col) else None
            for col in values]


def recommendation_tier(policy_id: str, primary_metric: dict,
                        full_metric: dict, registered_ids: list[str]) -> dict:
    """Exact P0 evidence controls tier; Pareto membership and cost never do."""
    if policy_id == "secure_milp":
        return {"code": "frozen_secure_reference", "label": "原 secure 对照",
                "reason": "冻结的比较基准；不以自身差值声称收益。"}
    harms, benefits, unknown = [], [], []
    for window, metric in (("主窗", primary_metric), ("全窗", full_metric)):
        names = metric["target_names"]
        if len(names) != 19 or not set(P0_NAMES) <= set(names):
            raise ValueError("Missing P0 target names in recommendation evidence")
        cases = metric["individual_cases"]
        if set(cases) != set(registered_ids):
            raise ValueError("Recommendation case inventory differs")
        for sid in registered_ids:
            row = cases[sid]
            if not _case_complete(row):
                unknown.append(f"{window}:{sid}")
                continue
            delta = row.get("delta_targets")
            if not isinstance(delta, list) or len(delta) != 19:
                raise ValueError("Complete recommendation case lacks 19 targets")
            for name in P0_NAMES:
                value = delta[names.index(name)]
                if not _numeric(value):
                    raise ValueError("Unknown P0 delta on completed case")
                if value > 0:
                    harms.append(f"{window}:{sid}:{name}={value!r}")
                elif value < 0:
                    benefits.append(f"{window}:{sid}:{name}={value!r}")
    if harms:
        return {"code": "observed_p0_harm", "label": "已见 P0 恶化，不推荐",
                "reason": "; ".join(harms[:6]) +
                (f"；其余 {len(harms)-6} 项见逐例原始分析" if len(harms) > 6 else ""),
                "unknown_case_windows": unknown}
    if unknown:
        return {"code": "unknown_blocks_recommendation", "label": "关键全窗未知，不推荐",
                "reason": f"有 {len(unknown)} 个策略窗配对不完整；已见收益不得补零外推。",
                "unknown_case_windows": unknown}
    if benefits:
        return {"code": "p0_improvement_candidate", "label": "P0 改善研究候选（待人工复核）",
                "reason": "完整支持上未见 P0 正差且至少一项 P0 原值为负；仍须逐例审查低优先级取舍和假设。"}
    return {"code": "no_observed_p0_gain", "label": "未见 P0 改善，不作可靠性推荐",
            "reason": "共同完整支持上 P0 原值没有严格改善；费用或低优先级变化不算保障收益。"}


def _fmt(value) -> str:
    if value is None:
        return "?"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ReportBlocked("nonfinite reported value")
        return repr(value)
    return str(value)


def format_sensitivity_cell(group: dict, target_names: list[str]) -> str:
    """Keep each target's observed denominator next to its actual sum."""
    registered = group["registered_case_count"]
    complete = group["complete_paired_case_count"]
    unknown_ids = group["unknown_case_ids"]
    counts = group["paired_observable_count_by_target"]
    sums = group["sum_delta_on_observed"]
    if (not isinstance(registered, int) or not isinstance(complete, int) or
            not isinstance(unknown_ids, list) or complete + len(unknown_ids) != registered or
            not isinstance(counts, list) or len(counts) != 19 or
            not isinstance(sums, list) or len(sums) != 19):
        raise ReportBlocked("sensitivity group denominator/target schema differs")
    def item(name: str, label: str) -> str:
        index = target_names.index(name)
        n = counts[index]
        value = sums[index]
        if n == 0:
            _require(value is None, "zero-observation sensitivity target was filled")
        elif not _numeric(value):
            raise ReportBlocked("observed sensitivity target is nonnumeric")
        return f"{label}={_fmt(value)}(n={n}/{registered})"
    return (f"{complete}/{registered}，未知{len(unknown_ids)}；" +
            "，".join(item(name, label) for name, label in (
                ("P0_failure_minutes", "P0分钟"),
                ("P0_e_ENS_kWh", "电"),
                ("P0_h_ENS_kWh", "热"),
                ("P0_c_ENS_kWh", "冷"),
                ("actual_energy_fee_USD", "费"))))


def _validate_score_sources(score: dict) -> None:
    _require(score.get("source_sha256") == _sha(ROOT / "tools/policy_search/score_recheck.py"),
             "score-recheck source SHA differs")
    sources = score.get("scoring_source_sha256") or {}
    _require(bool(sources), "score-recheck scoring-source inventory absent")
    for name, expected in sources.items():
        _require(_sha(ROOT / name) == expected, f"scoring source SHA differs: {name}")


def load_final_evidence(run_dir: Path) -> dict:
    """Check every gate against current exact bytes; never run a solver."""
    run = Path(run_dir).resolve()
    missing = missing_required_artifacts(run)
    if missing:
        raise ReportBlocked("; ".join(missing))
    development = run / "development"
    plan_path = development / "registered_plan.json"
    plan, manifest = verify_batch(plan_path)
    policy_ids = [item["policy_id"] for item in plan["policy_specs"]]
    registered = list(plan["scenario_ids"])
    expected_pairs = len(policy_ids) * len(registered)
    _require(plan.get("phase") == "development" and len(registered) == 60 and
             len(policy_ids) == 8 and expected_pairs == 480 and expected_pairs ==
             plan.get("registered_pair_count") and "secure_milp" in policy_ids,
             "development registered plan/pair denominator differs")
    paths = {name: development / name for name in (
        "AUDIT.json", "SCORE_RECHECK.json", "ANALYSIS_PREFERENCE_V2.json",
        "ANALYSIS_FULL_WINDOW.json", "SENSITIVITY_BY_CONDITION_PRESSURE.json")}
    audit, score, v2, full, sensitivity = (_read(paths[name]) for name in paths)
    plan_sha = _sha(plan_path)
    audit_sha = _sha(paths["AUDIT.json"])
    summaries = sorted((development / "results").glob("summary_*.json"))
    _require(bool(summaries), "development summary is absent")
    summary_path = summaries[-1]
    summary_sha = _sha(summary_path)
    summary = _read(summary_path)
    _require(audit.get("passed") is True and not audit.get("errors") and
             audit.get("final_blind_opened") is False and
             audit.get("plan_sha256") == plan_sha and
             audit.get("plan_signature") == plan["signature"] and
             audit.get("summary_sha256") == summary_sha and
             audit.get("manifest_sha256") == plan["manifest_sha256"] and
             audit.get("registered_pair_count") == expected_pairs and
             audit.get("observed_pair_count") == expected_pairs,
             "development AUDIT failed or source/summary denominator differs")
    _require(score.get("passed") is True and not score.get("errors") and
             score.get("plan_sha256") == plan_sha and
             score.get("plan_signature") == plan["signature"] and
             score.get("audit_sha256") == audit_sha and
             score.get("summary_sha256") == summary_sha and
             score.get("recomputed_pair_count") == expected_pairs and
             score.get("unknown_primary_pair_ids") == audit.get("unknown_primary_pair_ids") and
             score.get("unknown_full_pair_ids") == audit.get("unknown_full_pair_ids"),
             "development SCORE_RECHECK failed or differs from AUDIT")
    _validate_score_sources(score)
    revision_sha = _sha(run / "design/PREFERENCE_V2_SOURCE_FREEZE.json")
    for name, value in (("v2", v2), ("full", full)):
        _require(value.get("plan_sha256") == plan_sha and
                 value.get("plan_signature") == plan["signature"] and
                 value.get("summary_sha256") == summary_sha and
                 value.get("audit_sha256") == audit_sha and
                 value.get("v2_revision_sha256") == revision_sha and
                 set(value.get("registered_case_ids", [])) == set(registered),
                 f"development {name} analysis binding differs")
    _require(v2.get("preference_v2_source_sha256") ==
             _sha(ROOT / "tools/policy_search/preference_v2.py") and
             v2.get("cost_cannot_offset_P0") is True and
             v2.get("clinical_exchange_rate") is None and
             full.get("preference_v2_source_sha256") == v2["preference_v2_source_sha256"] and
             full.get("full_window_source_sha256") ==
             _sha(ROOT / "tools/policy_search/full_window.py") and
             full.get("cost_cannot_offset_P0") is True and
             full.get("clinical_exchange_rate") is None and
             full.get("audit_passed") is True and
             full.get("final_blind_opened") is False,
             "v2/full analysis source or blind seal differs")
    _require(set(v2.get("metrics", {})) == set(policy_ids) and
             set(full.get("metrics", {})) == set(policy_ids) and
             set(full.get("metrics_on_all_policy_common_cases", {})) == set(policy_ids),
             "development analysis policy set differs")
    primary_names = _target_names(v2["metrics"], policy_ids)
    full_names = _target_names(full["metrics_on_all_policy_common_cases"], policy_ids)
    _require(primary_names == full_names, "primary/full target order differs")
    primary_common = _common_support(v2["metrics"], policy_ids, registered)
    full_common = validate_full_common_metrics(
        full["metrics"], full["metrics_on_all_policy_common_cases"],
        policy_ids, registered, full.get("all_policy_common_complete_case_ids", []))
    _require(
             len(full_common) == full.get("all_policy_common_complete_case_count"),
             "full analysis all-policy common support differs")
    prereg_path = verify_preregistration(run)
    _require(sensitivity.get("schema") == "policy_search.audited_crossed_pressure.v1" and
             sensitivity.get("plan_signature") == plan["signature"] and
             sensitivity.get("plan_sha256") == plan_sha and
             sensitivity.get("summary_sha256") == summary_sha and
             sensitivity.get("audit_sha256") == audit_sha and
             sensitivity.get("manifest_sha256") == plan["manifest_sha256"] and
             sensitivity.get("audit_passed") is True and
             sensitivity.get("sensitivity_source_sha256") ==
             _sha(ROOT / "tools/policy_search/sensitivity.py") and
             sensitivity.get("sensitivity_preregistration_sha256") == _sha(prereg_path) and
             set(sensitivity.get("windows", {})) == {"primary_score", "full_observation"},
             "development sensitivity analysis or preregistration binding differs")
    for window in sensitivity["windows"].values():
        _require(window.get("registered_scene_count") == len(registered) and
                 set(window.get("policy_ids", [])) == set(policy_ids) and
                 window.get("same_root_manifest_and_raw_initial_stream_checked") is True and
                 window.get("same_root_event_schedule_checked_excluding_pressure_magnitude") is True and
                 window.get("pressure_points_are_composite_assumptions") is True and
                 window.get("not_hospital_event_probabilities") is True and
                 window.get("pressure_point_indices") == [0, 1, 2, 3, 4],
                 "development same-root sensitivity contract differs")
    account_path = run / "review/RUN_ACCOUNTING.json"
    account = _read(account_path)
    _require(account.get("schema") == "policy_search.v5.run_accounting.v1" and
             account.get("finalizable") is True and not account.get("issues") and
             account.get("all_registered_pairs_terminal") is True and
             account.get("all_batch_receipts_consistent") is True and
             account.get("missing_required_phase_plans") == [] and
             account.get("source_input_integrity", {}).get("final12", {}).get(
                 "artifact_seal_passed") is True and
             all(account.get("budgets", {}).get("within_observed_limits", {}).values()),
             "final review accounting is not complete/valid")
    fresh = collect_accounting(run)
    _require(fresh.get("finalizable") is True and not fresh.get("issues") and
             fresh.get("batches") == account.get("batches") and
             fresh.get("totals") == account.get("totals") and
             fresh.get("source_input_integrity") == account.get("source_input_integrity"),
             "final review accounting is stale against current registered data/source")
    dev_batches = [row for row in account["batches"] if row.get("phase") == "development"]
    _require(len(dev_batches) == 1 and dev_batches[0]["registered_pairs"] == expected_pairs and
             dev_batches[0]["terminal_pairs"] == expected_pairs,
             "review accounting lacks completed development phase")
    rows = summary.get("results") or []
    pairs = [(row.get("scenario_id"), row.get("policy_id")) for row in rows]
    _require(len(rows) == expected_pairs and len(set(pairs)) == expected_pairs and
             set(pairs) == {(sid, pid) for sid in registered for pid in policy_ids},
             "development summary row inventory differs")
    unknown_primary = sorted(f"{row['scenario_id']}/{row['policy_id']}" for row in rows
                             if row.get("primary_score", {}).get("full_horizon") is not True)
    unknown_full = sorted(f"{row['scenario_id']}/{row['policy_id']}" for row in rows
                          if row.get("full_observation", {}).get("full_horizon") is not True)
    _require(unknown_primary == audit.get("unknown_primary_pair_ids") and
             unknown_full == audit.get("unknown_full_pair_ids"),
             "development unknown pair IDs differ from audited summary")
    # Recompute the saved analysis objects from the exact summary bound by AUDIT.
    # This closes the gap left by self-referential hash fields in mutable JSON.
    from tools.policy_search.preference_v2 import _extended_metric
    from tools.policy_search.full_window import _complete_metric
    from tools.policy_search.sensitivity import summarize_pressure_crossing

    for pid in policy_ids:
        _require(_extended_metric(rows, pid) == v2["metrics"][pid],
                 f"v2 metric differs from audited summary: {pid}")
        _require(_complete_metric(rows, pid) == full["metrics"][pid],
                 f"full-window metric differs from audited summary: {pid}")
    common_set = set(full_common)
    common_rows = [row for row in rows if row["scenario_id"] in common_set]
    for pid in policy_ids:
        _require(_complete_metric(common_rows, pid) ==
                 full["metrics_on_all_policy_common_cases"][pid],
                 f"full common-support metric differs from audited summary: {pid}")
    for window in ("primary_score", "full_observation"):
        _require(summarize_pressure_crossing(plan, manifest, summary, window=window) ==
                 sensitivity["windows"][window],
                 f"sensitivity differs from audited summary: {window}")
    return {"run": run, "plan": plan, "manifest": manifest, "audit": audit,
            "score": score, "v2": v2, "full": full, "sensitivity": sensitivity,
            "accounting": account, "fresh_accounting": fresh,
            "summary": summary, "summary_path": summary_path,
            "policy_ids": policy_ids, "registered_ids": registered,
            "target_names": primary_names, "primary_common": primary_common,
            "full_common": full_common, "unknown_primary": unknown_primary,
            "unknown_full": unknown_full,
            "binding_sha256": {"plan": plan_sha, "summary": summary_sha,
                               "audit": audit_sha,
                               "score_recheck": _sha(paths["SCORE_RECHECK.json"]),
                               "preference_v2": _sha(paths["ANALYSIS_PREFERENCE_V2.json"]),
                               "full_window": _sha(paths["ANALYSIS_FULL_WINDOW.json"]),
                               "sensitivity": _sha(paths["SENSITIVITY_BY_CONDITION_PRESSURE.json"]),
                               "run_accounting": _sha(account_path),
                               "predev_draft": _sha(run / REQUIRED[0]),
                               "report_generator": _sha(Path(__file__))}}


def _rebase_links(section: str, draft_path: Path, run: Path) -> str:
    def replace(match: re.Match) -> str:
        label, href = match.group(1), match.group(2)
        if "//" in href or href.startswith("#"):
            return match.group(0)
        path, mark, anchor = href.partition("#")
        target = (draft_path.parent / path).resolve()
        _require(target.is_file(), f"pre-development report link is missing: {href}")
        rebased = os.path.relpath(target, run).replace("\\", "/")
        return f"]({rebased}{mark}{anchor})" if label is None else f"[{label}]({rebased}{mark}{anchor})"
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace, section)


def _static_sections(run: Path) -> str:
    path = run / REQUIRED[0]
    source = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^## ([^\n]+)\n", source))
    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        sections[match.group(1)] = source[match.start():end].strip()
    missing = [heading for heading in STATIC_HEADINGS if heading not in sections]
    _require(not missing, "pre-development static sections missing: " + ", ".join(missing))
    return "\n\n".join(_rebase_links(sections[heading], path, run)
                       for heading in STATIC_HEADINGS)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    def escape(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", "<br>")
    return "\n".join(["| " + " | ".join(map(escape, headers)) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(map(escape, row)) + " |" for row in rows)])


def _status_section(evidence: dict) -> str:
    rows = evidence["summary"]["results"]
    by_policy = defaultdict(list)
    for row in rows:
        by_policy[row["policy_id"]].append(row)
    table = []
    for pid in evidence["policy_ids"]:
        subset = by_policy[pid]
        primary_known = sum(row["primary_score"]["full_horizon"] is True for row in subset)
        full_known = sum(row["full_observation"]["full_horizon"] is True for row in subset)
        table.append([f"`{pid}`", len(subset),
                      sum(row.get("terminal") is True for row in subset),
                      sum(row.get("status") == "completed" for row in subset),
                      f"{primary_known}/{len(subset)}", len(subset) - primary_known,
                      f"{full_known}/{len(subset)}", len(subset) - full_known,
                      ", ".join(f"{key}:{value}" for key, value in sorted(
                          Counter(row.get("status") for row in subset).items()))])
    out = ["## 开发终态、共同支持与未知", "",
           f"开发计划预登记 {len(evidence['registered_ids'])} 个规格 × {len(evidence['policy_ids'])} "
           f"种策略 = {len(rows)} 对。`AUDIT.json` 和 `SCORE_RECHECK.json` 逐对通过；"
           "终态只表示有完整错误收据，不等于物理完成。主窗/全窗共同完整支持分别为 "
           f"{len(evidence['primary_common'])}/{len(evidence['registered_ids'])}、"
           f"{len(evidence['full_common'])}/{len(evidence['registered_ids'])} 个规格。",
           "",
           _markdown_table(["策略", "注册", "终态", "物理完整", "主窗完整", "主窗未知", "全窗完整", "全窗未知", "终态类型"], table),
           "",
           "每个完整分母及各目标可观测数均来自 [开发 v2 主窗分析](development/ANALYSIS_PREFERENCE_V2.json)、"
           "[全窗分析](development/ANALYSIS_FULL_WINDOW.json)。未知不补零。"]
    unknown = sorted(set(evidence["unknown_primary"]) | set(evidence["unknown_full"]))
    if unknown:
        lookup = {(row["scenario_id"], row["policy_id"]): row for row in rows}
        details = []
        for pair in unknown:
            sid, pid = pair.split("/", 1)
            row = lookup[(sid, pid)]
            details.append([f"`{pair}`", row.get("status"),
                            _fmt(row.get("observed_until_minute")),
                            "未知" if pair in evidence["unknown_primary"] else "完整",
                            "未知" if pair in evidence["unknown_full"] else "完整"])
        out.extend(["", "逐对未知（原始失败/前缀仍见 [AUDIT](development/AUDIT.json) 与"
                    "[原结果索引](development/results/)）：", "",
                    _markdown_table(["场景/策略", "终态", "观测截止 min", "主窗", "全窗"], details)])
    return "\n".join(map(str, out))


def _targets_section(evidence: dict) -> str:
    names = evidence["target_names"]
    policies = evidence["policy_ids"]
    out = ["## 开发 19 项原目标：同一共同完整支持", "",
           "下表保留原目标名称与数值差（策略减 secure）。每个窗口在**全部登记策略共同完整**"
           "的场景上求和；`?` 表示该共同支持无可报告总量，绝不是零。"
           "不同目标单位不能相加，费用不能抵消 P0。"
           "各策略自身完整配对、个案原值及目标 mask 见 [主窗 19 项逐例分析]"
           "(development/ANALYSIS_PREFERENCE_V2.json) 和 [全窗 19 项逐例分析]"
           "(development/ANALYSIS_FULL_WINDOW.json)。"]
    for label, metrics, case_ids in (
            ("主窗 60–420 分钟", evidence["v2"]["metrics"], evidence["primary_common"]),
            ("全窗 0–600 分钟", evidence["full"]["metrics_on_all_policy_common_cases"],
             evidence["full_common"])):
        totals = {pid: _sum_on_support(metrics[pid], case_ids) for pid in policies}
        table = [[name, *(_fmt(totals[pid][index]) for pid in policies)]
                 for index, name in enumerate(names)]
        out.extend(["", f"### {label}（共同支持 {len(case_ids)}/{len(evidence['registered_ids'])}）",
                    "", _markdown_table(["目标", *(f"`{pid}`" for pid in policies)], table)])
    return "\n".join(out)


def _sensitivity_section(evidence: dict) -> str:
    sensitivity = evidence["sensitivity"]
    names = evidence["target_names"]
    policies = evidence["policy_ids"]
    out = ["## 同根五压力与六条件敏感性", "",
           "下表每格是同一具名条件、同一压力点相对 secure 的**已观测分量和**，"
           "先列完整配对数/注册数与未知数，再列 P0 失效分钟、电/热/冷 ENS 与费用；"
           "各分量紧邻自身可观测分母 `n/注册数`。压力点是预登记的复合假设配置，"
           "不是单参数因果效应，也不是医院故障概率。p2 为同根锚点；"
           "同根效应变化、19 项值、逐场景 unknown 在 [敏感性原始分析]"
           "(development/SENSITIVITY_BY_CONDITION_PRESSURE.json)，"
           "匹配初态和外生流已由分析及事前登记核验。"]
    for label, key in (("主窗 60–420 分钟", "primary_score"),
                       ("全窗 0–600 分钟", "full_observation")):
        window = sensitivity["windows"][key]
        groups = window["by_condition_and_pressure"]
        conditions = sorted(groups)
        _require(len(conditions) == 6, "development sensitivity lacks six named conditions")
        table = []
        for pid in policies:
            for condition in conditions:
                cells = []
                for point in range(5):
                    per_policy = groups[condition].get(str(point), {})
                    _require(set(per_policy) == set(policies),
                             "sensitivity policy/pressure cell inventory differs")
                    cells.append(format_sensitivity_cell(per_policy[pid], names))
                table.append([f"`{pid}`", f"`{condition}`", *cells])
        out.extend(["", f"### {label}", "",
                    _markdown_table(["策略", "条件", "p0", "p1", "p2 锚点", "p3", "p4"], table)])
    return "\n".join(out)


def _recommendation_section(evidence: dict) -> str:
    tiers = {pid: recommendation_tier(pid, evidence["v2"]["metrics"][pid],
                                      evidence["full"]["metrics"][pid],
                                      evidence["registered_ids"])
             for pid in evidence["policy_ids"]}
    rows = [[f"`{pid}`", tier["label"], tier["reason"]]
            for pid, tier in tiers.items()]
    candidate_ids = [pid for pid, tier in tiers.items()
                     if tier["code"] == "p0_improvement_candidate"]
    conclusion = ("出现需人工审查的 P0 改善研究候选：" + ", ".join(
        f"`{pid}`" for pid in candidate_ids) + "。该等级不自动批准研究默认，更不构成医院部署建议。"
        if candidate_ids else
        "没有策略因本自动分级获得研究默认推荐；已见 P0 恶化、关键未知或无 P0 改善均须分别保留。")
    return "\n".join(["## 推荐、候选、否定与仍未知", "", conclusion, "",
                      "分级采用每个已完成个案的主窗和全窗 P0 原值差：只要有一项正差即标为"
                      "已见 P0 恶化；若有未知则不能以其余改善外推；只有全部登记个案"
                      "两窗完整、无 P0 正差且至少一项 P0 严格改善，才列人工复核候选。"
                      "采用原精确数值，不发明临床容差。Pareto 名单仅说明已登记偏好下的"
                      "非支配关系，绝不单独构成安全或获益判据。", "",
                      _markdown_table(["策略", "证据等级", "判定依据"], rows), "",
                      "低优先级或费用的改善不能抵消 P0 恶化；即使入人工候选，"
                      "还要审查 P1–P3、极端个案、真实参数校准和临床适用性。"])


def _accounting_section(evidence: dict) -> str:
    account = evidence["fresh_accounting"]
    totals = account["totals"]
    budgets = account["budgets"]
    uses = budgets["observed_use"]
    limits = budgets["limits"]
    budget_rows = [[key, _fmt(uses[key]), _fmt(limits[key]),
                    "通过" if budgets["within_observed_limits"][key] else "超限"]
                   for key in limits]
    commands = account["command_receipts"]
    test_rows = []
    full_suite = []
    for item in commands["receipts"]:
        path = evidence["run"] / item["receipt"]
        command = _read(path)
        argv = [str(x) for x in command.get("command", [])]
        if any(token in ("pytest", "pytest.exe") for token in argv):
            test_rows.append([f"[{item.get('name') or path.name}]({item['receipt']})",
                              item["exit_code"], _fmt(item["elapsed_seconds"]),
                              " ".join(argv)])
            if "-q" in argv and argv[-1] == "-q":
                full_suite.append(item)
    tests = (_markdown_table(["实际测试收据", "退出码", "秒", "命令"], test_rows)
             if test_rows else "当前账户命令收据未发现 pytest 运行；不能宣称本轮测试通过。")
    full_note = ("全套 `python -m pytest -q` 最近一次已记录退出码为 "
                 f"{full_suite[-1]['exit_code']}。" if full_suite else
                 "当前账户命令收据未核到无路径过滤的 `python -m pytest -q` 全套测试；"
                 "最终审查必须补真实收据，不能沿用旧轮数字。")
    fit = account["cuda_optimizer_fits"]
    totals_text = (f"登记批次 {totals['registered_batches']}，登记轨迹对 "
                   f"{totals['registered_pairs']}，终态 {totals['terminal_pairs']}，"
                   f"物理完整 {totals['completed_pairs']}，主窗/全窗未知 "
                   f"{totals['unknown_primary_pairs']}/{totals['unknown_full_pairs']}，"
                   f"实际轨迹尝试 {totals['trajectory_attempts']}。新外生规格 "
                   f"{totals['manifest_exogenous_specifications']}，主要策略配置 "
                   f"{totals['registered_major_policy_configurations']}，"
                   f"实际拟合目录 {totals['optimizer_fit_directories']}，"
                   f"其中 CUDA 证据核验通过 {fit['verified_cuda_fits']}。")
    return "\n".join(["## 全体台账、预算、真实测试与审查工件", "",
                      totals_text, "[完整运行账户](review/RUN_ACCOUNTING.json)"
                      "对所有注册批次、源/输入 SHA、模型、最终 12 背景工件封印进行核对；"
                      "源/模型/输入工件真实性仍受现有代理与外部设备资料限制。", "",
                      _markdown_table(["预算口径", "实际记录", "授权上限", "核验"], budget_rows), "",
                      f"墙钟采用日志命令时长保守求和，含重叠和只读命令；"
                      f"当前值 {uses['experimental_wall_hours']!r} 小时。"
                      "未记录的外部时间未知，不能把未用额度当作必须耗尽的目标。", "",
                      full_note, "", tests, "",
                      "[命令与退出码汇总](commands_and_exit_codes.txt) 和"
                      "[原始日志目录](logs/)保留失败收据；非零退出不因后续重试成功而删除。"
                      "本报告写入后，便携审查 ZIP 及其 SHA sidecar 才能由"
                      "[交付程序](../../tools/policy_search/delivery.py)生成；"
                      "报告不预填尚未产生的 ZIP 哈希。"])


def compose_report(evidence: dict) -> str:
    run = evidence["run"]
    tiers = {pid: recommendation_tier(pid, evidence["v2"]["metrics"][pid],
                                      evidence["full"]["metrics"][pid],
                                      evidence["registered_ids"])
             for pid in evidence["policy_ids"]}
    harms = [pid for pid, item in tiers.items() if item["code"] == "observed_p0_harm"]
    candidates = [pid for pid, item in tiers.items()
                  if item["code"] == "p0_improvement_candidate"]
    summary = (f"开发阶段 {len(evidence['registered_ids'])} 个规格、"
               f"{len(evidence['policy_ids'])} 种策略的 {evidence['plan']['registered_pair_count']} "
               "对结果已完成原始审计、复算和同根五压力分析。"
               f"主窗/全窗全部策略共同完整支持为 {len(evidence['primary_common'])}/"
               f"{len(evidence['registered_ids'])} 与 {len(evidence['full_common'])}/"
               f"{len(evidence['registered_ids'])}。"
               f"已见 P0 正差的候选有 {len(harms)} 个；严格 P0 无恶化且有改善、"
               f"仍待人工复核的候选有 {len(candidates)} 个。"
               "本报告以原 P0–P3 判据、逐目标和逐例结果作研究审查，"
               "不赋予费用对 P0 的交换率，也不作临床或部署认证。")
    bindings = "\n".join(f"- `{name}` SHA256 `{digest}`"
                         for name, digest in evidence["binding_sha256"].items())
    sections = ["# 医院 IES v5 完整反馈策略搜索报告", "",
                summary, "",
                "[预开发证据草稿](design/POLICY_REPORT_PREDEV_DRAFT.md)的静态机制段"
                "被按当前文件哈希引入；下列开发分析均绑定冻结计划、原始审计和最终账户。"
                "修正模式的选择复跑明确属于结果生成后的探索性对照，"
                "不具前瞻未见选择资格。", "",
                _static_sections(run), "",
                _status_section(evidence), "",
                _targets_section(evidence), "",
                _sensitivity_section(evidence), "",
                _recommendation_section(evidence), "",
                _accounting_section(evidence), "",
                "## 解释边界与复查入口", "",
                "`model_constraint_infeasible` 不等于已由独立 IIS 证明的物理不可行；"
                "所有未观测后果保持 null。六条件及五压力均是具名、复合研究情景，"
                "同年负荷和理想观测、保护时序、拓扑及设备参数仍缺现场校准。"
                "本报告的证据等级只用于审查现有仿真，不应转述为医院实际 P0 安全率、"
                "并网许可或临床保障。原最终 12 背景仍封存。"
                "完整原始结果可按 [注册计划](development/registered_plan.json)、"
                "[AUDIT](development/AUDIT.json)、[SCORE_RECHECK]"
                "(development/SCORE_RECHECK.json) 及"
                "[运行账户](review/RUN_ACCOUNTING.json)逐 SHA 追溯。", "",
                "本报告生成时的关键来源哈希：", "", bindings, ""]
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true",
                      help="Read-only: show blockers or print complete Markdown to stdout")
    mode.add_argument("--write", action="store_true",
                      help="Exclusively create POLICY_SEARCH_REPORT.md after all gates pass")
    args = parser.parse_args()
    try:
        evidence = load_final_evidence(args.run_dir)
        report = compose_report(evidence)
    except (ReportBlocked, FileNotFoundError, KeyError, ValueError) as error:
        if args.preview:
            print(json.dumps({"ready": False, "blockers": [str(error)]},
                             ensure_ascii=False, indent=2))
            return
        raise SystemExit("final report blocked: " + str(error)) from error
    if args.preview:
        print(report, end="")
        return
    target = Path(args.run_dir).resolve() / "POLICY_SEARCH_REPORT.md"
    with target.open("x", encoding="utf-8") as stream:
        stream.write(report)
    print(json.dumps({"report": str(target), "sha256": _sha(target),
                      "bytes": target.stat().st_size,
                      "registered_development_pairs": evidence["plan"]["registered_pair_count"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
