"""Read-only consequences and exact-prefix checks for the seven frozen gates."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.four_scenarios.figures import _read_verified_raw, _sha
from tools.state_dispatch.diagnostics import compare_prefix, timing_rows, TIMING_FIELDS

VARIANTS = ("day298_delay0", "day298_delay15", "day298_delay30", "day341_shadow",
            "day341_delay0", "day341_no_propagation", "day341_gate_t0")
POLICIES = ("priority_milp", "secure_milp")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def first_differences(left, right, end):
    physical = []
    keys = ("action", "loads_kW", "connected_loads_kW", "disconnected_loads_kW", "unserved_kW",
            "protection_stress", "protection_debug", "pre_state", "post_state")
    ls = [s for s in left["segments"] if s["end_minute"] <= end]
    rs = [s for s in right["segments"] if s["end_minute"] <= end]
    for a, b in zip(ls, rs):
        different = [key for key in keys if a.get(key) != b.get(key)]
        if different:
            physical.append({"start_minute": a["start_minute"], "end_minute": a["end_minute"],
                             "different_segment_fields": different})
            break
    observations = []
    for a, b in zip([d for d in left["decisions"] if d["decision_time_min"] < end],
                    [d for d in right["decisions"] if d["decision_time_min"] < end]):
        fields = sorted(k for k in set(a["features"]) | set(b["features"])
                        if a["features"].get(k) != b["features"].get(k))
        queue_differs = a.get("known_command_queue") != b.get("known_command_queue")
        if fields or queue_differs:
            observations.append({"decision_time_min": a["decision_time_min"], "different_features": fields,
                                 "known_command_queue_differs": queue_differs})
            break
    return {"first_different_physical_segment": physical, "first_different_legal_observation": observations}


def analyze(run_dir, out_dir):
    run, out = Path(run_dir).resolve(), Path(out_dir).resolve()
    paths = {"manifest": run / "new_data/global_manifest.json", "plan": run / "main/registered_plan.json",
             "audit": run / "main/AUDIT.json"}
    manifest, plan, audit = (read(paths[k]) for k in ("manifest", "plan", "audit"))
    assert audit["passed"] is True and audit["matched_initial_state_and_stream"] is True
    assert audit["registered_pair_count"] == audit["terminal_pair_count"] == plan["registered_pair_count"] == 14
    assert plan["manifest_sha256"] == audit["manifest_sha256"] == _sha(paths["manifest"])
    assert audit["plan_sha256"] == _sha(paths["plan"])
    assert not plan["final_blind_opened"] and not audit["final_blind_opened"]
    scenes = {s["id"]: s for s in manifest["scenarios"]}
    assert {s["diagnostic_variant"] for s in scenes.values()} == set(VARIANTS)
    ledger = {(r["scenario_id"], r["policy_id"]): r for r in audit["pair_ledger"]}
    payloads, summaries, clocks, sources = {}, [], [], []
    for (sid, pid), entry in ledger.items():
        scene = scenes[sid]
        assert scene["is_final_blind"] is False and entry["score_recomputed"] is True
        latest = entry["attempts"][-1]
        row = {"scenario_id": sid, "policy_id": pid, "analysis_class": scene["analysis_class"],
               "status": entry["status"], "raw_sha256": latest["raw_sha256"], "raw_path": ""}
        payload, path = _read_verified_raw(row, scenes, ledger, run / "main/results")
        assert payload["scenario"] == scene
        variant = scene["diagnostic_variant"]
        payloads[variant, pid] = payload
        sources.append({"variant": variant, "policy_id": pid, "raw_path": str(path), "raw_sha256": latest["raw_sha256"]})
        ens = [[0.0]*3 for _ in range(4)]
        failures = []
        for segment in payload["segments"]:
            duration = segment["end_minute"] - segment["start_minute"]
            for p in range(4):
                for c in range(3):
                    ens[p][c] += segment["unserved_kW"][p][c] * duration / 60
            if segment["classification"]["p0_guarantee_failure"]:
                failures.append([segment["start_minute"], segment["end_minute"]])
        score = entry["recomputed_full_observation"]
        for p in range(4):
            for c in range(3):
                assert abs(ens[p][c] - score["ENS_kWh_by_priority_carrier"][p][c]) <= 1e-10
        trips = [{k: event.get(k) for k in ("kind", "time_minute", "device", "stress_ratio", "power_surplus_kW")}
                 for event in payload["events"] if event["kind"] in
                 ("protection_trip", "no_export_interlock", "minimum_output_interlock")]
        generated = [{"command_id": e["command"]["id"], "generated_minute": e["command"]["generated_minute"],
                      "arrival_minute": e["command"]["arrival_minute"]} for e in payload["events"]
                     if e["kind"] == "command_generated"]
        noninstant = [c for c in generated if c["arrival_minute"] > c["generated_minute"]]
        summaries.append({"variant": variant, "scenario_id": sid, "policy_id": pid,
            "source_day_index": scene["source_day_index"], "status": entry["status"],
            "response_delay_minutes": scene["response_delay_minutes"], "stage": scene["stage"],
            "shock_start_minute": scene["shock_start_minute"], "enable_propagation": scene["enable_propagation"],
            "exogenous_events": scene["exogenous_events"], "assumption_changes": scene["diagnostic_assumption_changes"],
            "P0_failed": bool(score["P0_failure_in_window"]), "P0_failure_intervals_minute": failures,
            "P0_failure_minutes": sum(b-a for a,b in failures), "ENS_kWh_by_priority_carrier": ens,
            "GT_protection_trip_count": sum(e["kind"] == "protection_trip" for e in trips),
            "no_export_interlock_count": sum(e["kind"] == "no_export_interlock" for e in trips),
            "minimum_output_interlock_count": sum(e["kind"] == "minimum_output_interlock" for e in trips),
            "protection_and_interlock_events": trips, "generated_commands_with_wait": noninstant,
            "actual_energy_fee_USD": score["actual_energy_fee_USD"],
            "accounting_cost_USD": score["accounting_cost_USD"],
            "primary_score_from_current_audit": entry["recomputed_primary_score"],
            "full_score_from_current_audit": score, "raw_sha256": latest["raw_sha256"]})
        clocks.extend({"variant": variant, **r} for r in timing_rows(payload, latest["raw_sha256"]))
    assert len(summaries) == 14
    prefixes = []
    for variant in VARIANTS:
        base = "day298_delay30" if variant.startswith("day298") else "day341_shadow"
        for pid in POLICIES:
            scene = next(s for s in scenes.values() if s["diagnostic_variant"] == variant)
            left, right = payloads[base, pid], payloads[variant, pid]
            end = scene["diagnostic_common_prefix_end_minute"]
            check = compare_prefix(left, right, end)
            result = {"variant": variant, "reference_variant": base, "policy_id": pid,
                      "registered_common_prefix_check": check}
            if variant == "day341_gate_t0":
                result["exploratory_to_120_not_asserted_common_prefix"] = {
                    **compare_prefix(left, right, 120), **first_differences(left, right, 120)}
            else:
                assert check["physical_prefix_equal"] and check["observation_prefix_equal"]
            prefixes.append(result)
    summaries.sort(key=lambda r: (VARIANTS.index(r["variant"]), POLICIES.index(r["policy_id"])))
    result = {"schema": "state_dispatch.v9.readonly_diagnostic_consequences.v1", "new_trajectory_attempts": 0,
              "registered_pairs": 14, "audited_terminal_pairs": 14, "audit_passed": True,
              "input_bindings": {k: {"path": str(p), "sha256": _sha(p)} for k,p in paths.items()},
              "raw_sources": sources, "variant_policy_results": summaries, "prefix_checks": prefixes,
              "ENS_reintegration": "all 14 full-window raw matrices agree with current audited matrices within 1e-10 kWh serialization precision",
              "final_blind_opened": False, "claim_scope": "two seen backgrounds; execution assumption diagnostics, not policy improvement"}
    out.mkdir(parents=True, exist_ok=True)
    target = out / "DIAGNOSTIC_RESULTS.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    with (out / "DIAGNOSTIC_CLOCKS.csv").open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=("variant", *TIMING_FIELDS))
        writer.writeheader(); writer.writerows(clocks)
    lines = ["# 7个门控规格、14条实际轨迹的只读后果分析", "",
        "2026-10-16。输入为本轮diagnostics已完成并通过AUDIT的14条实际raw，不重跑物理。"
        "两个已见背景，不增加独立日根，不代表现场风险频率。每个窗口/门控变体是独立执行假设诊断，不算新策略收益。", "",
        "## 原始来源与口径", "",
        "manifest/plan/audit及全部14 raw SHA见DIAGNOSTIC_RESULTS.json；每条raw在读取时重验result与raw SHA。"
        "全窗0–600分钟P0–P3电热冷ENS重新按子段积分，全部与当前AUDIT一致；"
        "原19项两个窗口（60–420、0–600）的值直接保留当前已绑定AUDIT结果，不复制旧v8摘要。"
        "真实GT protection_trip、no_export_interlock与minimum_output_interlock分别计数。"
        "原19项中的旧secondary_GT_trips如含联锁，只保留旧口径，不作本表真实保护数。", "",
        "## 各变体、各策略的0–600分钟结果", "",
        "| variant | policy | P0失败min | P0电ENS | P0热ENS | P0冷ENS | P3电ENS | P3热ENS | P3冷ENS | GT保护 | 无外送联锁 | 最小出力联锁 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in summaries:
        ens = row["ENS_kWh_by_priority_carrier"]
        lines.append(f"| {row['variant']} | {row['policy_id']} | {row['P0_failure_minutes']:g} | " +
            " | ".join(f"{v:.9f}" for v in (*ens[0], *ens[3])) +
            f" | {row['GT_protection_trip_count']} | {row['no_export_interlock_count']} | {row['minimum_output_interlock_count']} |")
    lines.extend(["", "电/热/冷ENS单位分别为对应载能kWh；没有跨载能相加。微小非零ENS不自动构成原P0失败。P1/P2完整矩阵、费用、两窗19项和事件时钟保留在JSON/CSV。", "",
        "## day298：0、15、30分钟初始响应窗口", "",
        "priority在0/15/30分钟窗口下分别为0/0/15分钟P0失败，真实GT保护分别0/2/2次。"
        "15与30分钟均在t165先提交GT2/GT3保护；15分钟窗口的请求同刻到达，随后在已跳机状态重新请求并执行，"
        "没有165–180的P0失供区间。30分钟窗口仍需等t180，165–180留下44.053436403 kWh热ENS与6.425972845 kWh单段电ENS。"
        "因此15分钟不能解释为成功避免跳机；它缩短了余热丢失之后的供能空窗。0分钟诊断取消初始响应窗后，两策略均无GT保护与P0失败。", "",
        "secure在三个窗口均无P0失败，但15/30分钟仍各有GT3在t165保护跳机。"
        "其15→30分钟也伴随P1热、P3电冷代价变化，不能只报告P0分类未变。"
        "所有原保护阈值/容量/负荷/服务判据保持；0/15/30不是逐命令恒定通信延迟。", "",
        "## day341：空shock的影子时钟、关闭传播与gate_t0", "",
        "所有day341变体的faults/shock/exogenous_events仍为空，没有真实外生事故。背景逐小时负荷和环境容量继续变化。"
        "priority的shadow原设置在t120先no_export隔离GT3，t135再保护GT1。"
        "no_propagation保留t120相同无外送联锁，但不发生t135保护；这说明最初隔离不由传播门控控制，第二次停机由残余应力保持生成。"
        "两者P3电ENS为75.480427151与75.475486677 kWh，差仅0.004940474 kWh，主导的首段服务代价来自先前整台GT隔离，不能把全部约75.48归给第二次保护。", "",
        "同t120起点仅将响应窗置0时，priority没有联锁/保护，P3电ENS降至0.081900692 kWh；"
        "secure也无联锁/保护。该差异支持新正常命令能否在背景需求下降时及时生效的机制贡献，"
        "不证明真实医院具有零响应时延，也不是新控制器收益。正延迟、无外送、持有源与零延迟代数隔离仍是既有未校准合同。", "",
        "gate_t0把影子窗口移到t0，不能等同为删除所有门控或t120的delay0。"
        "priority无联锁/保护；secure却在t15出现GT1 minimum_output_interlock。"
        "其终态服务积分可与delay0近似/相同，也不能消去不同的设备事件历史。"
        "此变体从t0起已经改变响应与传播门控，不能当同前缀t120的单因素因果对照。", "",
        "## 同前缀核对", "",
        "除gate_t0外，各变体与同policy参考（day298_delay30/day341_shadow）在登记的t150/t120之前，"
        "物理子段与合法观测逐值exact匹配。比较排除了情景身份、未来标签及墙钟字段，未放宽数值容差。"
        "gate_t0登记共同前缀为0，检查结果明确vacuous；额外探索性比较到t120的物理与合法观测均不匹配。"
        "最早差异子段/观测字段在JSON中单列，因此本报告不把其描述为同前缀。", "",
        "## 结论边界与产物", "",
        "这14条证据解释了当前合同内的响应空窗、无外送隔离和残余保护的不同贡献；没有证明现场医院无因跳闸，"
        "没有把保护代理解释为OEM实测过载，也没有据有限开关诊断宣称连续安全区间、通用最优策略或物理已校准。"
        "no_propagation通过原支持stage2/event_class=L2适配关闭传播，不能作为发布用无保护L4策略。", "",
        "实际命令：`python -m tools.state_dispatch.diagnostic_results --run-dir outputs/v9_state_dispatch_20261016_run01/diagnostics --out-dir outputs/v9_state_dispatch_20261016_run01/review`。"
        "输出DIAGNOSTIC_RESULTS.json、DIAGNOSTIC_CLOCKS.csv与本文件；新增真实轨迹尝试0，最终12未打开。"])
    with (out / "DIAGNOSTIC_RESULTS.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return {"raws_reverified": len(sources), "prefix_checks": len(prefixes), "clock_rows": len(clocks),
            "results_path": str(target), "new_trajectory_attempts": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir, args.out_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
