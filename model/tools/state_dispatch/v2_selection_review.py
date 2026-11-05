"""Independent read-only development-v2 selection review; no holdout access."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tools.four_scenarios.figures import _read_json, _read_verified_raw, _sha

BASELINES = ("priority_milp", "secure_milp")
CANDIDATES = ("state_guarded_spread", "state_grid_bounded_spread")
KEY_CASES = {
    "day123_L1": "v9_state_63f0dac89b1ed8d498ea4d1b",
    "day123_L3": "v9_state_e758f8bb6a91b65d9b5e560a",
    "day51_L4b": "v9_state_46d0cc8e61634caaa73d3ff2",
}


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def aggregate(rows):
    result = {"n": len(rows), "P0_failure_count": sum(r["P0_failed"] for r in rows),
              "P0_failure_minutes": sum(r["P0_failure_minutes"] for r in rows)}
    for field in ("actual_energy_fee_USD", "accounting_cost_USD", "GT_protection_trip_count",
                  "no_export_interlock_count", "minimum_output_interlock_count"):
        result[field] = sum(r[field] for r in rows)
    for p in ("P0", "P1", "P2", "P3"):
        for c in "ehc":
            field = f"{p}_{c}_ENS_kWh"
            result[field] = sum(r[field] for r in rows)
    return result


def review(run_dir, out_dir):
    run, out = Path(run_dir).resolve(), Path(out_dir).resolve()
    batch = run / "development_v2"
    paths = {"manifest": run / "new_data/global_manifest.json", "plan": batch / "registered_plan.json",
             "audit": batch / "AUDIT.json"}
    manifest, plan, audit = (_read_json(paths[k]) for k in ("manifest", "plan", "audit"))
    assert plan["phase"] == "development" and audit["passed"] and audit["matched_initial_state_and_stream"]
    assert not plan["final_blind_opened"] and not audit["final_blind_opened"]
    assert plan["manifest_sha256"] == audit["manifest_sha256"] == _sha(paths["manifest"])
    assert audit["plan_sha256"] == _sha(paths["plan"])
    assert audit["registered_pair_count"] == audit["terminal_pair_count"] == plan["registered_pair_count"] == 128
    for name, expected in plan["source_sha256"].items():
        assert _sha(ROOT / name) == expected, "Registered source drift: " + name
    scenes = {s["id"]: s for s in manifest["scenarios"] if s["id"] in set(plan["scenario_ids"])}
    assert len(scenes) == 32
    assert all(not s["is_final_blind"] and s["experiment_role"] == "development" for s in scenes.values())
    ledger = {(r["scenario_id"], r["policy_id"]): r for r in audit["pair_ledger"]}
    rows, events, key_segments, key_decisions, raw_sources, payloads = [], [], [], [], [], {}
    for (sid, pid), entry in sorted(ledger.items()):
        scene = scenes[sid]; latest = entry["attempts"][-1]
        query = {"scenario_id": sid, "policy_id": pid, "analysis_class": scene["analysis_class"],
                 "status": entry["status"], "raw_sha256": latest["raw_sha256"], "raw_path": ""}
        payload, raw = _read_verified_raw(query, scenes, ledger, batch / "results")
        assert payload["scenario"] == scene and entry["binding_passed"] and entry["score_recomputed"]
        payloads[sid, pid] = payload
        score = entry["recomputed_full_observation"]
        assert score["full_horizon"]
        ens = [[0.0]*3 for _ in range(4)]; failed_minutes = 0.0
        intervals = []
        for segment in payload["segments"]:
            dt = segment["end_minute"] - segment["start_minute"]
            for p in range(4):
                for c in range(3):
                    ens[p][c] += segment["unserved_kW"][p][c] * dt / 60
            if segment["classification"]["p0_guarantee_failure"]:
                failed_minutes += dt
                intervals.append([segment["start_minute"], segment["end_minute"]])
        for p in range(4):
            for c in range(3):
                assert abs(ens[p][c] - score["ENS_kWh_by_priority_carrier"][p][c]) < 1e-9
        assert failed_minutes == score["observed_prefix_failure_minutes"]
        counts = Counter(e["kind"] for e in payload["events"])
        row = {"scenario_id": sid, "policy_id": pid, "analysis_class": scene["analysis_class"],
               "source_day_index": scene["source_day_index"], "root_family_id": scene["root_family_id"],
               "design_axis": scene["design_axis"], "design_axis_value": scene["design_axis_value"],
               "P0_failed": bool(score["P0_failure_in_window"]), "P0_failure_minutes": failed_minutes,
               "P0_failure_intervals": intervals, "GT_protection_trip_count": counts["protection_trip"],
               "no_export_interlock_count": counts["no_export_interlock"],
               "minimum_output_interlock_count": counts["minimum_output_interlock"],
               "actual_energy_fee_USD": score["actual_energy_fee_USD"],
               "accounting_cost_USD": score["accounting_cost_USD"], "raw_sha256": latest["raw_sha256"]}
        for i, p in enumerate(("P0", "P1", "P2", "P3")):
            for j, c in enumerate("ehc"):
                row[f"{p}_{c}_ENS_kWh"] = ens[i][j]
        rows.append(row)
        raw_sources.append({"scenario_id": sid, "policy_id": pid, "raw_path": str(raw), "raw_sha256": latest["raw_sha256"]})
        for event in payload["events"]:
            if event["kind"] in ("protection_trip", "no_export_interlock", "minimum_output_interlock"):
                events.append({"scenario_id": sid, "policy_id": pid, "source_day_index": scene["source_day_index"],
                    "analysis_class": scene["analysis_class"], "kind": event["kind"], "time_minute": event["time_minute"],
                    "device": event.get("device"), "stress_ratio": event.get("stress_ratio"), "raw_sha256": latest["raw_sha256"]})
        if sid in KEY_CASES.values():
            for segment in payload["segments"]:
                action = segment["action"]
                s = {"scenario_id": sid, "policy_id": pid, "start_minute": segment["start_minute"],
                     "end_minute": segment["end_minute"], "raw_sha256": latest["raw_sha256"],
                     "GT_total_kW": sum(action["gt_electric_kW"]), "grid_kW": action["grid_import_kW"],
                     "diesel_kW": action["diesel_kW"], "hrsg_steam_lbph": action["hrsg_steam_lbph"],
                     "boiler_steam_lbph": action["boiler_steam_lbph"],
                     "diesel_status_before": segment["pre_state"]["diesel_status"],
                     "diesel_energy_before_kWh": segment["pre_state"]["diesel_energy_remaining_kWh"],
                     "diesel_energy_after_kWh": segment["post_state"]["diesel_energy_remaining_kWh"]}
                s.update({f"GT_{i+1}_kW": v for i,v in enumerate(action["gt_electric_kW"])})
                s.update({f"P0_{c}_unserved_kW": segment["unserved_kW"][0][i] for i,c in enumerate("ehc")})
                key_segments.append(s)
            for diagnostic in payload.get("policy_diagnostics", []):
                key_decisions.append({"scenario_id": sid, "policy_id": pid,
                    "decision_time_min": diagnostic["decision_time_min"], "raw_sha256": latest["raw_sha256"],
                    **{k: diagnostic.get(k) for k in ("mode", "guard_active", "observed_cooling_to_electric_ratio", "route",
                        "selected_weight_USD_per_kWh", "current_planned_arrival_minute", "response_window_active",
                        "arrived_grid_port_kW", "requested_command", "requested_six_targets_kW")}})
    indexed = {(r["scenario_id"], r["policy_id"]): r for r in rows}
    comparisons, new_failures = [], []
    for candidate in CANDIDATES:
        for baseline in BASELINES:
            added, removed, retained = [], [], []
            for sid in scenes:
                c, b = indexed[sid, candidate], indexed[sid, baseline]
                if c["P0_failed"] and not b["P0_failed"]:
                    added.append(sid)
                    new_failures.append({"candidate": candidate, "baseline": baseline, "scenario_id": sid,
                        "source_day_index": c["source_day_index"], "analysis_class": c["analysis_class"],
                        "candidate_failure_minutes": c["P0_failure_minutes"], "candidate_P0_e_ENS_kWh": c["P0_e_ENS_kWh"],
                        "candidate_P0_h_ENS_kWh": c["P0_h_ENS_kWh"], "candidate_P0_c_ENS_kWh": c["P0_c_ENS_kWh"]})
                elif b["P0_failed"] and not c["P0_failed"]:
                    removed.append(sid)
                elif c["P0_failed"] and b["P0_failed"]:
                    retained.append(sid)
            comparisons.append({"candidate": candidate, "baseline": baseline,
                "new_failure_scenarios": added, "removed_failure_scenarios": removed, "retained_failure_scenarios": retained})
    overall = [{"policy_id": p, **aggregate([r for r in rows if r["policy_id"] == p])} for p in (*BASELINES, *CANDIDATES)]
    classes = [{"policy_id": p, "analysis_class": c, **aggregate([r for r in rows if r["policy_id"] == p and r["analysis_class"] == c])}
               for c in ("L1", "L3", "L2", "L4b", "L4a", "N2") for p in (*BASELINES, *CANDIDATES)]
    normal = [r for r in classes if r["analysis_class"] == "N2"]
    priority_cost = next(r["actual_energy_fee_USD"] for r in normal if r["policy_id"] == "priority_milp")
    for row in normal:
        row["actual_fee_delta_vs_priority_USD"] = row["actual_energy_fee_USD"] - priority_cost
        row["actual_fee_relative_delta_vs_priority"] = row["actual_energy_fee_USD"] / priority_cost - 1
    result = {"schema": "state_dispatch.v2.independent_selection_review.v1", "audited_pairs": 128,
              "new_trajectory_attempts": 0, "holdout_outcomes_read": False, "final_blind_opened": False,
              "input_bindings": {k: {"path": str(p), "sha256": _sha(p)} for k,p in paths.items()},
              "registered_policy_specs": plan["policy_specs"], "raw_sources": raw_sources, "scenario_policy_rows": rows,
              "overall": overall, "by_class": classes, "normal_N2": normal, "P0_relative_comparisons": comparisons,
              "new_P0_failures": new_failures, "protection_and_interlock_events": events,
              "scope": "32 already seen-background development specifications; candidate review, not unseen validation"}
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "V2_SELECTION_REVIEW.json", result)
    csv_rows = [{**r, "P0_failure_intervals": json.dumps(r["P0_failure_intervals"])} for r in rows]
    write_csv(out / "V2_POLICY_CASE_METRICS.csv", csv_rows)
    write_csv(out / "V2_PROTECTION_EVENTS.csv", events)
    write_csv(out / "V2_KEY_CASE_SEGMENTS.csv", key_segments)
    write_json(out / "V2_KEY_CASE_DECISIONS.json", key_decisions)
    figure_cases = [
        ("v2_L1_bridge_day051", "v9_state_7ff8dabd398a918c50677b4c", "Online preparation and GRID0 supply bridge", 60, 180),
        ("v2_L2_heat_day363", "v9_state_1aeda70cfe4d8aeb03608a7f", "Heat readiness and the initial response window", 60, 240),
        ("v2_L3_guard_limit_day123", KEY_CASES["day123_L3"], "Cooling guard preparation: a GRID0 counterexample", 75, 180),
        ("v2_L4b_recurrence_day051", KEY_CASES["day51_L4b"], "GT protection, diesel depletion and service recurrence", 60, 345),
        ("v2_L4b_guard_gain_day298", "v9_state_dacaf5e97e8f775ab2315148", "Conditional compound-event response and carrier losses", 120, 240),
    ]
    selection = [{"figure_id": fid, "title": title, **{k: str(paths[k]) for k in ("manifest", "plan", "audit")},
                  "results": str(batch / "results"), "scenario_id": sid,
                  "policy_ids": ["priority_milp", "secure_milp", "state_guarded_spread", "state_grid_bounded_spread"],
                  "view_start": start, "view_end": end} for fid,sid,title,start,end in figure_cases]
    assert all(x["scenario_id"] in scenes for x in selection)
    write_json(out / "V2_FIGURE_SELECTION.json", selection)
    lines = ["# development_v2独立选择复核", "", "2026-10-16。只读32个开发规格×4策略=128条实际raw；当前AUDIT passed128/128、无错误和警告。"
        "重新核验result/raw SHA并按子段复算全部P0–P3分载能ENS及失败分钟，与绑定AUDIT一致。未读取holdout结果、未重跑物理，最终12背景未打开。", "",
        "## 条件候选的实际边界", "", "guarded可保留为条件研究候选，不能宣称整体替代两基线。"
        "其失败条数下降，但失败载能与复发代价不能被条数消去；day123增加了priority本可避免的P0失败。"
        "已有充分负例与正常代价证据，不需要为收口再扩候选或调整留出。", "",
        "| policy | 规格数 | P0失败条数 | P0失败min | P0电ENS kWh | P0热ENS kWh | P0冷ENS kWh | 真实GT保护 | 无外送联锁 | 最小出力联锁 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in overall:
        lines.append(f"| {r['policy_id']} | {r['n']} | {r['P0_failure_count']} | {r['P0_failure_minutes']:g} | {r['P0_e_ENS_kWh']:.6f} | {r['P0_h_ENS_kWh']:.6f} | {r['P0_c_ENS_kWh']:.6f} | {r['GT_protection_trip_count']} | {r['no_export_interlock_count']} | {r['minimum_output_interlock_count']} |")
    lines.extend(["", "各类完整矩阵、P1–P3、费用与逐例事件见JSON和CSV；本表从当前raw生成，不沿用旧secondary_GT_trips混合口径。"
        "开发类别分母为N2=12，L1/L3/L2/L4a/L4b各4；同背景/跨标签不能增加独立日根。", "",
        "## day123 GRID0：guard准备与20分钟执行空窗", "",
        "L1与L3共享同day123 GRID0背景机制，是2条规格而非2个独立背景。t90/t105当前C/E=1.080991544>0.8且GRID健康，"
        "guarded选择source-diversification、权重0.35并关闭spread，沿secure请求；105–120实际GT全0、电网849.992679 kW、锅炉1475.366690 lb/h。"
        "这能保护高冷需求状态下的分源选择，却没有在线电源跨越突然失网。", "",
        "t120观察到GRID=0后才退出guard并请求GT约225×3，原20分钟响应窗令其t140到达。"
        "柴油t135已CONNECTED，但135–140实际柴油仍0，因为有效命令仍未到达；不能把就绪状态当实际供电。"
        "120–140 P0电131.379313 kW和冷62.809418 kW缺供，该20分钟失败区间的ENS贡献为43.793104/20.936473 kWh；"
        "全窗电ENS还含其他时段原账本微小缺供，不能将这两个积分口径混写，精确全窗值见逐例表。"
        "t140实际GT225×3与柴油93.034211 kW开始供能，P0恢复。", "",
        "priority此前GT约471.105 kW在线，L3中虽GT2在t135保护，GT3仍支撑首段；P0无失败。"
        "grid_bounded此前GT168.107456×3在线，原空窗也无P0失败。guarded相对priority新增的2条失败均来自这一已见日根，"
        "相对secure没有新增失败；这不消除新增失败的实质，也不能称guard对GRID0均安全。", "",
        "## day51 L4b：保住首段后失去GT，柴油尾段复发", "",
        "故障/电需求冲击t90开始，冲击t270结束，GRID故障持续到t330；初始响应窗22.5分钟。"
        "guarded在75–90保持GT145.919808×3在线，90–105 P0首段达标；原残余应力代理3台均为1.223742661并于t105保护跳机。"
        "105–112.5实际GT/柴油均0、HRSG/锅炉均0，P0失败7.5分钟，不能把t90生成的GT上调请求当t105前已经执行。", "",
        "t112.5到达命令启动实际柴油约900 kW并供热，P0恢复，但GT保护锁定未解除。"
        "持续柴油支撑后t270余37.532582 kWh，270–285只能执行约150.130326 kW；"
        "t285剩余数值尾量5.5234e-7 kWh、实际柴油0，GRID仍不可用且GT全锁定，于285–330再次P0失败45分钟。"
        "合计52.5分钟，明显长于两基线各22.5分钟；电冷ENS也更大。GRID t330恢复后重新达标。", "",
        "secure也在t285耗尽柴油，但其GT未保护锁定，可在t270/t285换回GT供电，所以没有这段复发。"
        "priority损失1台GT，仍有2台存续与约699 kWh柴油余量，亦无复发。"
        "因此复发链是整队GT保护丢失、持续柴油消耗与故障尾段联合结果，不能只写首段ENS降低。"
        "mode曾回到prepare仅表示观测/调度模式退出应急，不表示GT已修复或柴油足够；报告必须保留这一差别。", "",
        "## 正常N2代价：12个10小时窗口的配对统计", "",
        "| policy | N2 P0失败 | 能源费合计USD | 相对priority能源费变化 | N2 P3电ENS kWh |", "|---|---:|---:|---:|---:|"])
    for r in normal:
        lines.append(f"| {r['policy_id']} | {r['P0_failure_count']} | {r['actual_energy_fee_USD']:.6f} | {r['actual_fee_relative_delta_vs_priority']:+.3%} | {r['P3_e_ENS_kWh']:.6f} |")
    lines.extend(["", "费用采用原模型示例费率，不是实测医院账单；12个窗口并非一年运行费用，不能直接年化。"
        "N2微小电ENS是原求解/服务账本中保留值，均未触发原P0失效。guard长期切到secure路径的正常代价应与条件收益同报。", "",
        "## 建议的声明范围与图选择", "",
        "保留guarded作为当前状态反馈的条件方案：报告热准备/特定复合冲击的已测收益，同时明确高C/E时GT退运后突发GRID0桥接失败、"
        "全部GT保护后柴油尾段复发、以及正常费用上升。不能在线按日根或L标签选预先赢家；以下回顾性图仅解释已测状态，不是切换脚本。"
        "不宣称主导所有指标、未见背景泛化或现场物理认证。", "",
        "已准备V2_FIGURE_SELECTION.json共5图，覆盖L1桥接、L2热准备、day123 L3负例、day51 L4b复发及day298复合条件收益。"
        "四policy各自真实raw已审查，正式图尚未构建，等主任务确认使用该选择。", "",
        "实际命令：`python -m tools.state_dispatch.v2_selection_review --run-dir outputs/v9_state_dispatch_20261016_run01 --out-dir outputs/v9_state_dispatch_20261016_run01/review`。"
        "源绑定、逐例指标/事件、关键案例实际段与决策均随本报告保留。新增物理尝试0。"])
    with (out / "V2_SELECTION_REVIEW.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return {"raws_reverified": len(rows), "new_P0_relative_to_baseline_count": len(new_failures),
            "prepared_figures": len(selection), "new_trajectory_attempts": 0, "holdout_outcomes_read": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(review(args.run_dir, args.out_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
