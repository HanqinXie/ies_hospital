"""Copy selected existing results and verify byte identity; never import model code."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path("E:/hospital_ies_publish_20261028")
STAGE = BASE / "results_stage"
AUDIT = BASE / "results_audit"
ROOTS = {v: Path(f"E:/hospital_ies_{v}_20261027") for v in ("v30", "v31", "v32")}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


SELECT = {v: set() for v in ROOTS}


def add(v: str, *paths: str):
    SELECT[v].update(paths)


def top(v: str, folder: str):
    for p in (ROOTS[v] / folder).iterdir():
        if p.is_file() and p.suffix.lower() in {".json", ".jsonl", ".md", ".csv", ".log", ".txt", ".patch"}:
            SELECT[v].add(p.relative_to(ROOTS[v]).as_posix())


def folder(v: str, rel: str):
    for p in (ROOTS[v] / rel).rglob("*"):
        if p.is_file() and p.suffix.lower() in {".json", ".jsonl", ".md", ".csv", ".log", ".txt", ".patch"}:
            SELECT[v].add(p.relative_to(ROOTS[v]).as_posix())


add("v30", "ACTIVE_TARGET_LOCK.json", "CALENDAR_LABEL_CORRECTIONS_FIX01.json",
    "FROZEN_SOURCE_V30_C02.json", "RUN_PLAN.json", "SCENARIO_PREREGISTRATION.json",
    "SOURCE_DELTA_V29_TO_V30.json", "FIXED_CAMPAIGN_ATTEMPTS.jsonl", "NORMAL_ATTEMPTS.jsonl",
    "DISTURBANCE_ATTEMPTS.jsonl", "REFERENCE_ATTEMPTS.jsonl",
    "release_narrative/ENGINEERING_INTERPRETATION_ZH.md",
    "physical_fix_review/COUNTEREXAMPLE_REVIEW.md", "physical_fix_review/README.md",
    "physical_fix_review/scan02/IMPACT_AUDIT.json", "physical_fix_review/scan02/IMPACT_AUDIT.md",
    "evaluation/EVALUATION_CLOSEOUT.md", "evaluation/final_validation01.log",
    "evaluation/final_01/VALIDATION_SUMMARY.json",
    "release_acceptance/readonly01/COMPLETION_RECEIPT.json",
    "release_acceptance/readonly01/FULL_RESTORE_RECEIPT.json",
    "release_acceptance/execution_index/ACTUAL_ATTEMPTS.csv",
    "release_acceptance/execution_index/ACTUAL_ATTEMPTS.json",
    "release_acceptance/execution_index/FIXED_CORE_BINDINGS.json",
    "release_acceptance/execution_index/SOURCE_DELTA_FIX1.json",
    "release_acceptance/execution_index/PHYSICAL_FIX1_ONLY.patch",
    "release_acceptance/execution_index/README.md",
    "release_acceptance/static_qa/STATIC_QA.json", "release_acceptance/static_qa/STATIC_QA_ZH.md",
    "reference_replay_audit/FINAL_READY.json", "reference_replay_audit/audit_02/PAPER_COMPLETION.json",
    "literature/hirwa/mapping.json", "literature/rosales/mapping.json",
    "runs/r03_grid_R_off/RESOLVED_CASE.json", "runs/r03_normal_R_off/RESOLVED_CASE.json")
top("v30", "comparison_audit_fix1")
folder("v30", "comparison_audit_fix1/final01")
top("v30", "engineering_report_tools")
folder("v30", "engineering_report_tools/actual_final02")
top("v30", "release_acceptance")
for step in ("full_default_after_fix01", "physical_min_off_red03", "physical_min_off_green03",
             "final_zip_build01", "final_zip_build02", "final_zip_build03"):
    folder("v30", "steps/" + step)
for failure in ("d01_b02_L1_heat_pump_disturbed_M", "r01_normal_R_off"):
    for name in ("STATUS.json", "INVOCATION.json", "BINDINGS.json", "SOURCE_SCOPE.json", "stdout.log", "stderr.log"):
        if (ROOTS["v30"] / "runs" / failure / name).is_file():
            add("v30", f"runs/{failure}/{name}")

add("v31", "EXPERIMENT_REGISTRATION.json", "FROZEN_SOURCE_V31_P01.json", "RUN_PLAN.json",
    "FORMAL_ATTEMPTS.jsonl", "SOURCE_DELTA.json", "SOURCE_DIFF.patch",
    "baseline_audit/REUSE_SELECTION.json", "baseline_audit/REUSE_SELECTION.csv",
    "baseline_audit/LOCK_PROVENANCE.json", "baseline_audit/READY.json",
    "baseline_audit/README_ZH.md", "interface_review/INTERFACE_REVIEW.md",
    "interface_review/SOURCE_EVIDENCE.json", "interface_review/final_code_review/CHECKS.json",
    "interface_review/final_code_review/CODE_REVIEW.md",
    "acceptance/FINAL_ACCEPTANCE.json", "acceptance/ZIP_VERIFICATION.json",
    "acceptance/independent_review_COMMAND.json", "acceptance/independent_review.stdout.log",
    "acceptance/independent_review.stderr.log",
    "acceptance/independent_readonly01/RESTORE_RECEIPT.json",
    "acceptance/independent_readonly01/REVIEW_RECEIPT.json",
    "acceptance/static_qa/STATIC_QA.json", "acceptance/static_qa/STATIC_QA_ZH.md",
    "delivery_payload/project/docs/v31/SCOPE_AND_ASSUMPTIONS.md",
    "delivery_payload/project/docs/v31/MIGRATION.md")
top("v31", "evaluation")
folder("v31", "evaluation/final01")
for step in ("targeted_shared_green01", "preparation_red01", "preparation_green02", "freeze_and_four_formal01"):
    folder("v31", "steps/" + step)

add("v32", "EXPERIMENT_REGISTRATION.json", "FROZEN_SOURCE_V32_P01.json",
    "PLANNING_SOURCE_FREEZE.json", "RUN_PLAN.json", "FORMAL_ATTEMPTS.jsonl",
    "SOURCE_DELTA.json", "SOURCE_DIFF.patch", "ENGINEERING_REPORT_ZH.md",
    "FINAL_DELIVERY_STATUS.json", "Hospital_IES_V32_External_Reference_20261027.zip.sha256",
    "package_build/ZIP_VERIFICATION.json", "package_build/FINAL_AUDIT_POINTER.json",
    "package_build/INDEPENDENT_EXTRACT_REVIEW.json", "package_build/SOURCE_AND_SCOPE_FINAL_CHECK.json",
    "package_build/EXTRACT_REVIEW.stdout.log", "package_build/EXTRACT_REVIEW.stderr.log",
    "package_build/LATE_QA_SYNC.json", "package_build/SUPPORT_STAGE_RECEIPT.json",
    "package_review/actual01/STATIC_PACKAGE_REVIEW.json", "package_review/actual01/README_ZH.md",
    "independence_review/FINAL_INDEX.json", "independence_review/QA.json",
    "independence_review/INDEPENDENCE_POSTERIOR_REVIEW_ZH.md",
    "independence_review/actual02/POSTERIOR_CONSISTENCY.json",
    "literature_baseline/FINAL_INDEX.json", "literature_baseline/BASELINE_REUSE_ZH.md",
    "literature_baseline/HIRWA_V32_MAPPING_ZH.md", "literature_baseline/ROSALES_V32_MAPPING_ZH.md",
    "literature_baseline/THEORY_AND_FAIRNESS_REVIEW_ZH.md",
    "literature_baseline/actual02/READONLY_EVIDENCE.json",
    "input_build/INDEPENDENT_SOURCE_REVIEW.json", "input_build/FROZEN_INPUTS_GATE.json",
    "input_build/SOURCE_FIELD_MAP.json", "input_build/README_ZH.md",
    "input_build/formal_isolated_01/BUILD_RECEIPT.json",
    "delivery_payload/project/docs/v32/MIGRATION_AND_ASSUMPTIONS.md")
top("v32", "evaluation")
folder("v32", "evaluation/final02")
top("v32", "final_review")
folder("v32", "planning")
for step in ("targeted_gate01", "two_formal_reference01", "common_initialization01",
             "solve_registered_plans01", "fresh_extract_readonly01", "close_delivery01"):
    folder("v32", "steps/" + step)


def record(root: Path, rel: str):
    p = root / rel
    return {"original_path": p.as_posix(), "original_relative_path": rel,
            "bytes": p.stat().st_size, "sha256": sha(p)}


started = datetime.now(timezone.utc).isoformat()
for v in ROOTS:
    for rel in SELECT[v]:
        p = ROOTS[v] / rel
        if not p.is_file():
            raise FileNotFoundError(p)
        if p.stat().st_size > 5_000_000:
            raise ValueError(f"Unexpected large curated file: {p}")
preflight = {v: {"files": len(SELECT[v]), "bytes": sum((ROOTS[v]/r).stat().st_size for r in SELECT[v])} for v in ROOTS}
print("PREFLIGHT", json.dumps(preflight, ensure_ascii=False))
STAGE.mkdir(parents=True, exist_ok=True)
AUDIT.mkdir(parents=True, exist_ok=True)
files = []
for v in ROOTS:
    for rel in sorted(SELECT[v]):
        item = record(ROOTS[v], rel)
        target = STAGE / v / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha(target) != item["sha256"]:
            raise RuntimeError(f"Refusing conflicting staging target: {target}")
        shutil.copy2(ROOTS[v] / rel, target)
        if sha(target) != item["sha256"] or target.stat().st_size != item["bytes"]:
            raise RuntimeError(f"Copy verification failed: {target}")
        files.append({"version": v, "staged_path": f"{v}/{rel}", **item,
                      "copy_sha256_exact": True})

freezes = {v: read(ROOTS[v]/f"FROZEN_SOURCE_{v.upper()}_{'C02' if v=='v30' else 'P01'}.json") for v in ROOTS}
audit_rel = {"v30": "comparison_audit_fix1/final01/CAMPAIGN_AUDIT.json",
             "v31": "evaluation/final01/AUDIT.json", "v32": "evaluation/final02/AUDIT.json"}
audits = {v: read(ROOTS[v]/audit_rel[v]) for v in ROOTS}
tables = {"v30": "engineering_report_tools/actual_final02/MAIN_RESULT_TABLE.csv",
          "v31": "evaluation/final01/MAIN_RESULT_TABLE.csv", "v32": "evaluation/final02/MAIN_RESULT_TABLE.csv"}
table_rows = {v: list(csv.DictReader((ROOTS[v]/tables[v]).open(encoding="utf-8-sig", newline=""))) for v in ROOTS}
for v, count in {"v30": 32, "v31": 12, "v32": 6}.items():
    if len(table_rows[v]) != count or not audits[v]["final_matrix_complete_and_clean"]:
        raise AssertionError(f"Unexpected final matrix for {v}")

zip_names = {"v30": "Hospital_IES_V30_REVIEW_under100MB.zip",
             "v31": "Hospital_IES_V31_Simple_Preparation_Closeout_20261027.zip",
             "v32": "Hospital_IES_V32_External_Reference_20261027.zip"}
expected_zips = {"v30": (98896789, "48a93fcd391f9cbf903d1e6185fc12916ac91a219c75e0bd2c87bf420b96e54d"),
                 "v31": (24246025, "a36105a82be48b6ec2815928472bd1242111370af0bb3d3e2591133375d848b3"),
                 "v32": (35908886, "f884e2b8495f424303bfd18f49a7c2836c77cfcc4e79e7a7fdf129425b123f01")}
assets = []
for v in ROOTS:
    item = record(ROOTS[v], zip_names[v])
    if (item["bytes"], item["sha256"]) != expected_zips[v]:
        raise AssertionError(f"Release asset mismatch for {v}")
    assets.append({"version": v, **item, "transport": "GitHub Release asset",
                   "copied_to_git_tree": False,
                   "contents_role": "Accepted portable package with native evidence and reproducible source/input bindings"})

failures = []
for name, state, observed, unknown in [
        ("d01_b02_L1_heat_pump_disturbed_M", "failed_unknown", 90.0, 90.0),
        ("r01_normal_R_off", "failed_unknown", 0.0, 180.0)]:
    root = ROOTS["v30"] / "runs" / name
    failures.append({"version": "v30", "original_directory": root.as_posix(), "status": state,
                     "observed_usable_trajectory_minutes": observed, "unknown_tail_minutes": unknown,
                     "included_in_final_main_32": False,
                     "reason": "Powered minimum-off readiness failure" if name.startswith("d01") else "KeyError: strategy_fallback; no usable final trajectory",
                     "original_files": [record(root, p.name) for p in root.iterdir() if p.is_file()],
                     "small_receipts_copied_under": "v30/runs/" + name,
                     "complete_native_evidence_transport": zip_names["v30"]})
write(STAGE / "ORIGINAL_FAILURE_LOCATIONS.json", {
    "schema": "hospital_ies.publish.original_failure_locations.v1", "scientific_failures": failures,
    "other_preserved_audit_or_package_failures": [
        {"version": "v30", "original_paths": ["steps/final_zip_build01", "steps/final_zip_build02"], "role": "Rejected packaging attempts; not model trajectories"},
        {"version": "v31", "original_paths": ["evaluation/PARTIAL01.stderr.log", "evaluation/partial02"], "role": "Pre-final review evidence; final is evaluation/final01"},
        {"version": "v32", "original_paths": ["evaluation/final01", "evaluation/RED03_lifecycle.stderr.log", "evaluation/LIFECYCLE_CORRECTION_V2.md"], "role": "Superseded audit lifecycle failure retained; final is evaluation/final02"}],
    "unknowns_not_imputed": True})

registration30 = read(ROOTS["v30"] / "SCENARIO_PREREGISTRATION.json")
contexts = [{"context": c["id"], "source_start_hour": c["selection"]["start_hour"],
             "source_hours_including_endpoint": c["selection"]["full_source_hours"],
             "hardware": c["configuration"], "end_minute": c["common_observation_end_minute"],
             "preparation_minutes": c["normal_preparation_minutes"],
             "disturbance_window": c["fault_window_minutes"]} for c in registration30["contexts"]]
roles = {"v30": "Current primary C/M comparison on powered-start minimum-off readiness fix1, C02 production freeze",
         "v31": "Simple preparation closeout: two registered permission variants in already observed b05 and b07 contexts",
         "v32": "One offline known-window fixed-capacity literature adaptation on b01; external reference, not online ranking"}
limits = {
    "v30": ["Eight finite deterministic contexts, not annual failure probabilities or certified blind evaluation",
            "Only N02/D02 comprise the final 32; prior producer attempts and R_off are retained separately",
            "Physical bottlenecks and service/fee tradeoffs remain; no universal C dominance",
            "Tiny raw ENS and carrier/priority distinctions preserved; no summed e/h/c ENS or fabricated percentage gains",
            "Original R03/R_off uses an older hour-211 context and is not the b01 hour-1160 reference"],
    "v31": ["Four new trajectories plus eight reused V30 records; not twelve new simulations",
            "b05 and b07 were already observed development contexts, not blinded or annual reliability",
            "Cooling and steam permission variants target different contexts and are not one universally validated controller",
            "Higher steam permission improves heat service with fee and cooling tradeoffs; does not establish full dominance",
            "V30 default 1363 tests are inherited evidence; V31 actual targeted source regression is 60 passed"],
    "v32": ["Six records comprise two newly executed references and four frozen V30 C/M reuses",
            "Offline full-window information differs from C/M current lawful observations",
            "Lower fees accompany larger P3 ENS and lower terminal UPS stock; not equal-service savings or superiority",
            "Not original-author software, official REopt execution, or exact original-paper numerical replication",
            "full_continuous_plan_replayed=false; planned_storage_ports_executed=false; planned_unmet_directly_executed=false",
            "Full initialization solver-call total remains null; core-log zero is not total zero",
            "Final audit is final02; final01 lifecycle issue is retained, not hidden"]}
validation = {
    "v30": {"recorded_original_tests": "1363 passed in full_default_after_fix01; not rerun during publication",
            "recorded_release_acceptance": "release_acceptance/FINAL_RELEASE.json status passed",
            "recorded_independent_restore": "87,668 original native files / 50,105 unique raw payload hashes",
            "recorded_research_attempts": {"actual": 60, "completed": 58, "failed_unknown": 2},
            "separate_public_smoke_executions": 1},
    "v31": {"recorded_actual_targeted_source_tests": 60, "includes_new_preparation_tests": 14,
            "recorded_synthetic_evaluation_tests": 17,
            "recorded_release_acceptance": "acceptance/FINAL_ACCEPTANCE.json status passed",
            "recorded_independent_native_restore": 6961},
    "v32": {"recorded_actual_targeted_source_tests": 60, "recorded_static_reference_checks_passed": 35,
            "recorded_delivery_status": "FINAL_DELIVERY_STATUS.json status complete",
            "recorded_independent_extract_review": "package_build/INDEPENDENT_EXTRACT_REVIEW.json status passed",
            "recorded_independent_native_restore": 15430,
            "full_initialization_solver_call_total": None}}
versions = []
for v in ROOTS:
    freeze = freezes[v]
    versions.append({"version": v, "role": roles[v], "original_root": ROOTS[v].as_posix(),
                     "final_audit_relative_path": audit_rel[v], "final_table_relative_path": tables[v],
                     "final_audit_status": audits[v]["status"], "final_matrix_complete_and_clean": True,
                     "final_audit_summary": audits[v]["summary"], "table_rows": len(table_rows[v]),
                     "scientific_producer": freeze["producer"], "scientific_source_sha256": freeze["source_sha256"],
                     "scientific_source_files": len(freeze["source_files"]), "input_files": len(freeze["input_files"]),
                     "input_set_sha256": freeze["input_sha256"], "physical_patch_id": freeze["physical_patch"],
                     "sample_scope": contexts if v == "v30" else [x for x in contexts if x["context"] in (["b05_L3_ER", "b07_L4_GT_ER"] if v == "v31" else ["b01_L1_grid"])],
                     "recorded_validation": validation[v], "limitations": limits[v],
                     "selected_files": preflight[v]})
catalog = {"schema": "hospital_ies.publish.results_catalog.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
           "publication_task": "2026-10-28 latest model and results GitHub update",
           "curation_only": True, "new_simulations": 0, "new_solver_calls": 0,
           "new_scientific_recomputations": 0, "original_files_modified": False,
           "validation_this_task": "Read final saved receipts and audit status; verify copied bytes, table counts and accepted ZIP size/SHA only. No scientific tests or audits rerun.",
           "versions": versions, "release_assets": assets,
           "copied_artifacts": files, "original_failure_locations": "ORIGINAL_FAILURE_LOCATIONS.json",
           "historical_reference_separation": {
               "old_v30_R03_start_hour": 211,
               "old_v30_R03_cases": ["v30/runs/r03_grid_R_off/RESOLVED_CASE.json", "v30/runs/r03_normal_R_off/RESOLVED_CASE.json"],
               "current_v32_b01_start_hour": 1160,
               "old_R03_is_not_current_b01": True},
           "deduplication_note": "V31/V32 tables intentionally reuse frozen V30 records; do not add row counts as independent sample counts",
           "native_and_pdf_policy": "No full native trajectories, raw scientific archives or source PDFs copied into Git results tree; original accepted ZIPs are release assets"}
write(STAGE / "CATALOG.json", catalog)

readme = """# 医院 IES 最新有效研究结果（2026-10-28整理）

本目录整理已完成、已验收的 V30–V32 可读结果，没有重新运行模型、求解器或科学计算。每个复制文件保留版本内原相对路径；[CATALOG.json](CATALOG.json) 记录原始绝对路径、字节数、SHA-256、科学生产提交、样本范围和限制。完整原生证据经三份原验收 ZIP 作为 GitHub Release assets 提供，未把全部原生轨迹或原 PDF 放进 Git 文件树。

|版本|当前角色|最终科学记录|有效结果位置|
|---|---|---|---|
|V30 fix1 / C02|主 C/M 比较|八个背景×正常/扰动×C/M，共32条N02/D02|[主表](v30/engineering_report_tools/actual_final02/MAIN_RESULT_TABLE.csv)、[最终审计](v30/comparison_audit_fix1/final01/CAMPAIGN_AUDIT.json)、[工程解释](v30/release_narrative/ENGINEERING_INTERPRETATION_ZH.md)|
|V31 P01|两项简单准备措施收尾|b05/b07，共12条：8条冻结V30复用＋4条新增|[主表](v31/evaluation/final01/MAIN_RESULT_TABLE.csv)、[最终审计](v31/evaluation/final01/AUDIT.json)、[工程结论](v31/evaluation/FINAL_ENGINEERING_INTERPRETATION_ZH.md)|
|V32 P01|单个固定容量离线文献适配参照|b01，共6条：4条冻结V30复用＋2条新增|[主表](v32/evaluation/final02/MAIN_RESULT_TABLE.csv)、[最终审计](v32/evaluation/final02/AUDIT.json)、[工程报告](v32/ENGINEERING_REPORT_ZH.md)|

V31/V32 复用了 V30 记录，三版表格行数不能相加成独立实验样本数。各背景均为有限确定性窗口，不代表全年可靠率或临床资格。没有增加 MPC、未来在线故障脚本、硬件容量或执行快通道。

## V30：模型/评价可冻结，存在真实服务与费用取舍

同一 fix1 核心下最终32条N02/D02均完成180 min，最终审计记录的未知尾段、求解错误、策略回退、评价失败均为空。b01 为源小时1160、H1：C/M扰动实际费用分别为112.56322728559338和120.00232510658562 USD2001，P3电ENS分别为130.8172606290107和76.79572704443306 kWh_e。较低费用与较多一般区域电缺供同时出现，不能宣称全面优胜。

b02供热循环泵、b03共同冷却塔、b04冷水循环泵出现双方共同受限的服务中断。b07中锅炉早已就绪，C/M的关键热交付差异来自已到达许可与实际供汽差异，不能归因为M尚未预热。P0–P3电、热、冷ENS分别保留；微小ENS不改成零，实质中断为零也不代表原始ENS严格为零。

原先 b07 提案中的 GT_2 是待选择占位符；[ACTIVE_TARGET_LOCK.json](v30/ACTIVE_TARGET_LOCK.json) 记录按预登记规则在扰动前锁定的真实共同目标为 GT_3。不得据旧提案误写本轮故障对象。

## V31：简单许可措施有效，收益有明确适用背景

b05 的 M_cooling_permission 只打开健康制冷许可：正常/扰动 P3 冷 ENS 从原 M 的8.788847650063195/10.593407344124453 kWh_c降为0/0。正常工况本已有缺供，故不能把扰动轨迹全部损失都归于ER冲击。该措施相对M全程费用略降，扰动减自身正常的费用增量却略高，完整C仍有更低费用。

b07 的 M_steam_permission 提高已到达的健康锅炉供汽许可，未加速队列或新增预热。扰动P0/P1热ENS从原M的12.99693845167845/8.790220719501372 kWh_h降为0/0，P3冷ENS仍为29.902399823603297 kWh_c，费用从175.5826376577955增至177.757136529103 USD2001。完整C费用和供冷更好，但仍有P1热中断。两项措施分别针对已见背景，不能混称为一个已普遍验证的新控制器；登记范围内模型开发可以结束。

## V32：严格静态可行与动态实际执行存在差距

最终有效审计为 evaluation/final02。固定 b01_L1_grid、源小时1160、H1、180 min，外部参照使用 offline_known_window 信息，和 C/M 在线合法观测条件不同。Hirwa提供数学结构，Rosales提供设计/服务/经济/韧性边界核对；本轮只有一个 R32_fixed_capacity_offline 数值参照，并非两套原作者控制器、官方 REopt 运行或原论文数值精确复现。

六记录的P0/P1均无冻结判据下的实质中断，但微小电ENS照常保留。外部参照扰动费用88.1156142 USD2001，低于C/M；同时P3电/热/冷ENS分别为200.890510/21.8760935/15.4087008，各自kWh，末UPS约26.3334386 kWh_e，C/M约40 kWh_e。参照正常分支还存在15 min P3电实质中断。因此不能将低费用称为同服务、同末库存的节费优胜，亦不能以离线未来信息参照证明在线算法排名。

两支严格静态规划均为optimal且独立算术可行，静态各级服务ENS为零；经共同队列、启动、禁止倒送联锁、辅助耗电与实际分配后，实际服务存在缺口。固定45 min提前准备转换不是通信延迟或必然就绪保证。完整连续计划、储能功率端口和规划缺供均未逐值直接回放。完整初始化求解器调用数保持null，没有以core日志中的0冒充总调用0。

旧V30 R03/R_off的hour211是历史参考背景，和V32的b01 hour1160分开保存；禁止把旧R03结果改标签冒充本轮b01。

## 验证口径、失败证据与归档

本次整理只新增文件清单和逐字节校验，不重跑以下已有验证。V30收据记录默认1363项测试通过；V31记录60项真实相关源测试和17项synthetic评价工具测试；V32记录60项真实相关源测试、35项最终静态审查。V30的1363不能复制成V31/V32本轮重跑数。

[原始失败位置索引](ORIGINAL_FAILURE_LOCATIONS.json) 保留V30的 d01_b02_L1_heat_pump_disturbed_M（已知90 min，后90 min未知）和 r01_normal_R_off（KeyError: strategy_fallback，无可用最终轨迹，180 min未知）。原始文件不改动，小收据复制在v30/runs，完整失败证据由原ZIP携带。审计RED日志、V32 final01生命周期问题和最终final02修正、拒绝的打包尝试也保留；失败/未知均未补零或混入最终比较。

原始输入36文件集合SHA-256在三版均为 f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15。科学生产提交分别为V30 3252727135645037cedc57ebc188a48d2aa34ccb、V31 f009bdb66d5b9d53f1ff2331f2dc58404c2d056b、V32 575cb02b2ac3a1bbdce99a6f283891dc05b3d51a。后续文档提交不是上述轨迹的生产提交。

|Release asset|字节数|SHA-256|
|---|---:|---|
|Hospital_IES_V30_REVIEW_under100MB.zip|98896789|48a93fcd391f9cbf903d1e6185fc12916ac91a219c75e0bd2c87bf420b96e54d|
|Hospital_IES_V31_Simple_Preparation_Closeout_20261027.zip|24246025|a36105a82be48b6ec2815928472bd1242111370af0bb3d3e2591133375d848b3|
|Hospital_IES_V32_External_Reference_20261027.zip|35908886|f884e2b8495f424303bfd18f49a7c2836c77cfcc4e79e7a7fdf129425b123f01|

V30/V31最终归档验收均为passed；V32最终状态为complete，最终ZIP独立解压只读复核为passed。以上状态来自已存原始收据；本次当前校验重新读取三ZIP并比对大小/SHA，未把旧验收说成本次重新解压或科学重算。下载后的完整复核入口和依赖以各ZIP内README为准。
"""
(STAGE / "README_RESULTS_ZH.md").write_text(readme, encoding="utf-8")

generated = [record(STAGE, p) for p in ("CATALOG.json", "ORIGINAL_FAILURE_LOCATIONS.json", "README_RESULTS_ZH.md")]
result = {"status": "passed", "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
          "curation_script": record(AUDIT, "curate_results.py"), "source_roots": [p.as_posix() for p in ROOTS.values()],
          "selected_original_files": len(files), "selected_original_bytes": sum(x["bytes"] for x in files),
          "all_selected_copy_sha256_exact": True, "per_version": preflight,
          "generated_files": generated, "release_assets_current_sha_size_verified": assets,
          "new_simulations": 0, "new_solver_calls": 0, "new_scientific_recomputations": 0,
          "D_files_modified": 0, "original_E_files_modified": 0, "git_or_api_operations": 0,
          "exploration_note": "One read-only report-display command exited 1 due Windows GBK stdout UnicodeEncodeError; rerun with UTF-8 stdout succeeded. No scientific command failed or was rerun in this curation task."}
write(AUDIT / "CURATION_RECEIPT.json", result)
print("CURATION_COMPLETE", json.dumps({"files": len(files), "bytes": result["selected_original_bytes"], "per_version": preflight}, ensure_ascii=False))
