"""Bounded standard-library readback; never launches/stops a scientific child."""
import csv
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
RUN = ROOT / "outputs/v20_load_accounting_20261019_run01"
PROFILE = RUN / "provenance/normal_resource_profile"
RESOURCE = PROFILE / "supervision_canonical02"
GIB = 1024**3


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def memory_snapshot():
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (k, ctypes.c_ulonglong) for k in ("total_phys", "avail_phys", "total_page",
            "avail_page", "total_virtual", "avail_virtual", "avail_extended")]
    class Performance(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong)] + [(k, ctypes.c_size_t) for k in
            ("CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal", "PhysicalAvailable",
             "SystemCache", "KernelTotal", "KernelPaged", "KernelNonpaged", "PageSize")] + [
            (k, ctypes.c_ulong) for k in ("HandleCount", "ProcessCount", "ThreadCount")]
    m = Memory(); m.length = ctypes.sizeof(m)
    p = Performance(); p.cb = ctypes.sizeof(p)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        raise OSError("GlobalMemoryStatusEx")
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(p), p.cb):
        raise OSError("GetPerformanceInfo")
    return dict(utc=datetime.now(timezone.utc).isoformat(),
        available_physical_bytes=int(m.avail_phys),
        committed_bytes=int(p.CommitTotal)*int(p.PageSize),
        commit_limit_bytes=int(p.CommitLimit)*int(p.PageSize),
        commit_headroom_bytes=(int(p.CommitLimit)-int(p.CommitTotal))*int(p.PageSize),
        basis="Read-only GlobalMemoryStatusEx / GetPerformanceInfo; volatile pre-action observation")


def main():
    evidence = {}
    def bind(path):
        path = Path(path).resolve()
        value = dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path))
        assert str(path) not in evidence or evidence[str(path)] == value
        evidence[str(path)] = value
        return value["sha256"]
    freeze_path = RUN / "MODEL_FREEZE_ACCOUNTING01.json"
    bind(freeze_path); freeze = read(freeze_path)
    assert len(freeze["source_sha256"]) == 366
    assert len(freeze["source_bound_test_gate"]["test_files_sha256"]) == 241
    assert len(freeze["runtime_inputs"]) == 28
    # Streaming hashes are bounded in memory. No scientific modules are imported.
    for name, expected in freeze["source_sha256"].items():
        assert sha(ROOT / name) == expected, name
    for name, expected in freeze["source_bound_test_gate"]["test_files_sha256"].items():
        assert sha(ROOT / name) == expected, name
    assert all((ROOT / r["path"]).stat().st_size == r["bytes"] for r in freeze["runtime_inputs"])
    profile_path = PROFILE / "V19_R2_PROFILE.json"
    bind(profile_path); profile = read(profile_path)
    assert profile["source_model_id"] == "M19_model_closeout"
    assert profile["is_M20_measurement"] is False and profile["growth_factor"] == 1.2
    assert profile["host_reserve_bytes"] == 6*GIB
    assert profile["signature"] == canonical({k:v for k,v in profile.items() if k != "signature"})
    summary_path = PROFILE / "V19_R2_SOURCE_SUMMARY.json"
    assert bind(summary_path) == profile["source_summary_sha256"]
    old_run = Path(profile["source_summary_path"]).parents[2]
    sample_path = RESOURCE / "SAMPLES.jsonl"
    bind(sample_path)
    samples = [json.loads(line) for line in sample_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    rows = []
    metadata_keys = {"evaluation_role", "background_status", "original_final_release_authorized",
        "historical_unseen_claim", "v20_seen_regression", "v20_source_scene_id"}
    for path in sorted(RESOURCE.glob("v20_normal_*.result.json")):
        bind(path); result = read(path); q = result["qualification"]
        assert result["passed"] is True and result["exit_code"] == 0 and q["qualified"] is True
        d = RUN / "experiments" / result["batch"]
        for name in ("plan.json", "manifest.json", "BATCH_RESULT.json"):
            bind(d / name)
        plan, manifest = read(d/"plan.json"), read(d/"manifest.json")
        spec = plan["policy_specs"][0]
        old = next(v for v in profile["profiles"] if v["alias"] == spec["display_alias"]
            and v["scenario_id"] == q["scenario_id"] and v["duration_minutes"] == q["window_minutes"][1])
        old_manifest_path = old_run / "experiments" / old["original_batch"] / "manifest.json"
        old_plan_path = old_manifest_path.parent / "plan.json"
        assert bind(old_manifest_path) == old["original_manifest_sha256"]
        bind(old_plan_path)
        old_manifest, old_plan = read(old_manifest_path), read(old_plan_path)
        a, b = old_manifest["scenarios"][0], manifest["scenarios"][0]
        assert {k:v for k,v in a.items() if k not in metadata_keys} == {k:v for k,v in b.items() if k not in metadata_keys}
        assert old_plan["model_config"] == plan["model_config"] == freeze["model_config"]
        assert old_plan["policy_specs"][0]["config"] == spec["config"]
        assert spec["policy_id"] == q["policy_id"]
        scientific = d / "pairs" / (q["scenario_id"]+"__"+q["policy_id"]) / "RESULT.json"
        assert bind(scientific) == q["result_sha256"] and sha(d/"BATCH_RESULT.json") == q["terminal_sha256"]
        own = [v for v in samples if v["batch"] == result["batch"]]
        assert len(own) == result["resource_samples"] and result["unknown_process_samples"] == 0
        assert all(v["process_sample_status"] == "measured" and type(v["pid"]) is int and v["pid"] > 0 for v in own)
        assert len({v["pid"] for v in own}) == 1
        mapping = (("sampled_private_peak_bytes", "private_bytes", "sampled_private_peak_bytes", "private"),
            ("sampled_working_set_peak_bytes", "working_set_bytes", "sampled_working_set_peak_bytes", "sampled_RSS"),
            ("observed_OS_lifetime_peak_working_set_bytes", "OS_lifetime_peak_working_set_bytes", "OS_lifetime_peak_working_set_bytes", "OS_lifetime_RSS"))
        row = dict(batch=result["batch"], alias=old["alias"], source_day_index=old["source_day_index"],
            duration_hours=old["duration_minutes"]/60, scenario_id=q["scenario_id"], policy_id=q["policy_id"],
            samples=len(own), unknown_process_samples=0, scientific_pid=own[0]["pid"],
            old_original_batch=old["original_batch"], old_manifest_sha256=old["original_manifest_sha256"],
            resource_receipt_sha256=sha(path), new_result_sha256=q["result_sha256"], terminal_sha256=q["terminal_sha256"])
        for new_key, sample_key, old_key, label in mapping:
            assert result[new_key] == max(v[sample_key] for v in own)
            row["M19_"+label+"_bytes"] = old[old_key]
            row["M20_"+label+"_bytes"] = result[new_key]
            row[label+"_M20_over_M19"] = result[new_key]/old[old_key]
        rows.append(row)
    assert len(rows) == 10 and sum(r["samples"] for r in rows) == len(samples) == 632
    assert {r["batch"] for r in rows} == {f"v20_normal_{i:03d}" for i in (0,1,2,3,4,5,6,7,10,11)}
    unknown_dir = PROFILE / "supervision_canonical01"
    unknown_samples = unknown_dir / "SAMPLES.jsonl"
    interrupted_path = unknown_dir / "v20_normal_009.interrupted.json"
    bind(unknown_samples); bind(interrupted_path)
    interrupted = read(interrupted_path)
    assert unknown_samples.stat().st_size == 0
    assert interrupted["batch"] == "v20_normal_009" and interrupted["exit_code"] == 0
    assert interrupted["owned_child_drained"] is True and interrupted["error_type"] == "TypeError"
    assert not (RESOURCE / "v20_normal_009.result.json").exists()
    assert not (RESOURCE / "v20_normal_008.result.json").exists()
    historic = next(r for r in profile["profiles"] if r["alias"] == "C19" and r["duration_minutes"] == 4320.)
    thresholds = []
    for name, factor, reserve in (("original",1.2,6), ("prospective_single008",1.1,2), ("optional_if_actual_capacity",1.1,3)):
        phys = math.ceil(factor*historic["OS_lifetime_peak_working_set_bytes"])+reserve*GIB
        commit = math.ceil(factor*historic["sampled_private_peak_bytes"])+reserve*GIB
        thresholds.append(dict(name=name, factor=factor, reserve_GiB=reserve, required_physical_bytes=phys,
            required_commit_bytes=commit, required_physical_GiB=phys/GIB, required_commit_GiB=commit/GIB))
    snapshot = memory_snapshot()
    for value in evidence.values():
        assert Path(value["path"]).stat().st_size == value["bytes"] and sha(value["path"]) == value["sha256"]
    decision = dict(status="conditional_design_support_not_launch_authorization_or_memory_guarantee",
        scope="One still-unstarted registered v20_normal_008 only; no scientific changes",
        original_guard_profile_preserved=True,
        launch_conditions=[
            "Strict 405 completed proof and exact008 no-start evidence saved before continuation",
            "Old monitor and S3 actually exit/drain; actual codes and unknowns retained",
            "No action on current Code/Codex ancestors and no system/pagefile changes",
            "Only stdlib+psutil resident controller; numerical qualification subprocesses exit/drain before launch and after scientific child",
            "Measure both global physical and commit counters after drains; do not add predicted private reclaim to a stale snapshot",
            "Two or three stable post-drain measurements meet prospectively registered thresholds; no other M20/Gurobi child",
            "One attempt; same frozen source/tests/runtime input bytes, registered scene/policy/config/Gurobi environment",
            "Sample owned PID and global counters every2s; preregister resource-danger handling, no automatic kill/retry or scientific parameter adjustment",
            "New continuation receipts distinguish ten canonical02 results, one new008 controller result, and009 unknown; never relabel old S3/normal03 as passed"])
    result = dict(schema="hospital_ies.v20.normal008.resource_feasibility_review.v1",
        utc=datetime.now(timezone.utc).isoformat(), source_freeze_sha256=sha(freeze_path),
        source_files_rehashed=366, test_files_rehashed=241, runtime_inventory_rows_stat_checked=28,
        runtime_annual_content_rehashed_or_parsed=False, old_source_summary_sha256=profile["source_summary_sha256"],
        resource_samples_sha256=sha(sample_path), comparable_measured_pairs=10, measured_samples=632,
        unknown_samples_in_ten=0, ratios=rows,
        normal009=dict(resource_peaks=None, ratio=None, reason="Actual science completed but old sampling ledger empty",
            old_empty_samples_sha256=sha(unknown_samples), interrupted_receipt_sha256=sha(interrupted_path)),
        normal008=dict(resource_peak=None, ratio=None, status="not_started_at_review"),
        historic_same72_C19=historic, thresholds=thresholds, pre_action_volatile_snapshot=snapshot,
        decision=decision, evidence_files=list(evidence.values()),
        raw_opened=0, annual_values_parsed=0, scientific_children_started=0, processes_stopped=0,
        imports="standard library only; no scientific module or persistent heavy process",
        review_script_sha256=sha(Path(__file__)))
    with (HERE/"RESOURCE_FEASIBILITY_REVIEW.json").open("x",encoding="utf-8") as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    with (HERE/"MATCHED_PEAK_RATIOS.csv").open("x",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    lines=["# normal008 资源可行性独立审查", "",
        "结论：有条件支持单条008的轻控制器方案；本审查不启动任务，也不证明新的内存安全上界。", "",
        "10组M20/M19同日、同策略、同持续时间匹配；RESULT和SAMPLES峰值逐项一致，632个样本均为measured。"
        "科学RESULT/BATCH及旧manifest的SHA、相同物理配置/策略偏好均已核。009旧采样0字节，保持unknown；008尚未运行。", "",
        "| 批次 | 策略 | 日 | 小时 | 样本 | private新/旧 | OS生命周期RSS新/旧 | 采样RSS新/旧 |",
        "|---|---|---|---|---|---|---|---|"]
    lines += [f'| {r["batch"]} | {r["alias"]} | {r["source_day_index"]} | {r["duration_hours"]:g} | {r["samples"]} | {r["private_M20_over_M19"]:.6f} | {r["OS_lifetime_RSS_M20_over_M19"]:.6f} | {r["sampled_RSS_M20_over_M19"]:.6f} |' for r in rows]
    lines += ["", "旧同72h C19 private峰24.677795 GiB、OS峰22.680138 GiB。", "",
        "| 资源方案 | required physical GiB | required commit GiB |",
        "|---|---|---|"]
    lines += [f'| {r["name"]} ({r["factor"]}×+{r["reserve_GiB"]} GiB) | {r["required_physical_GiB"]:.9f} | {r["required_commit_GiB"]:.9f} |' for r in thresholds]
    lines += ["", "预计post-drain commit若为29.75 GiB，则比1.1×+2门槛多0.604425 GiB，"
        "相对旧实际private峰多5.072205 GiB；这只是条件算术，不是已发生的回收或可用量。"
        "实际旧监控器退出后必须重新量物理/commit两项，不能把private释放量全当物理可用量增加。"
        "本报告JSON内的host snapshot是停止任何进程前的瞬时观察，不能直接充当新launch许可。", "",
        "支持依据：10组private比值0.887500–1.016316，三个C19为0.887500–0.908864。"
        "不可证：未跑72h C19的native瞬时分配、采样末尾遗漏及系统commit漂移。"
        "若实际容量达到1.1×+3门槛可优先多留1 GiB；不满足已登记门槛则继续不启动。", "",
        "运行期间仅标准库/psutil监控；资格核验用短命独立进程，必须wait/drain后再放行科学子进程。"
        "记录真实lease/STOP/退出码，不能自动kill、自动重试、修改Gurobi或科学判据。"
        "旧normal03/S3真实停态必须保留，后续新续行不能冒充旧链passed；资源分母应为10条canonical02＋008新控制器＋009未知。", "",
        "不会关闭当前Code/Codex或其祖先，不修改系统设置、pagefile、原profile、冻结或任何科学文件。"
        "源366及测试241按冻结逐文件重算SHA；运行输入28仅核登记/长度，本审查未解析年度值，"
        "正式启动仍依赖root的405严格前置证明和短命核验。", "",
        "证据见RESOURCE_FEASIBILITY_REVIEW.json和MATCHED_PEAK_RATIOS.csv。"
        "本脚本仅标准库、运行后立即结束；没有科学子进程/进程停止/物理仿真。", ""]
    with (HERE/"RESOURCE_FEASIBILITY_REVIEW.md").open("x",encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    print(json.dumps(dict(status="review_saved",comparable_pairs=10,samples=632,
        json_sha256=sha(HERE/"RESOURCE_FEASIBILITY_REVIEW.json"),
        csv_sha256=sha(HERE/"MATCHED_PEAK_RATIOS.csv"),
        md_sha256=sha(HERE/"RESOURCE_FEASIBILITY_REVIEW.md")),ensure_ascii=True))


if __name__ == "__main__":
    main()
