"""Independent static review: hashes, AST, JSON and saved publication evidence only."""
from pathlib import Path
import ast
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path("E:/hospital_ies_publish_20261028")
REPO = Path("D:/hospital_ies_v22_20261025")
OUT = BASE / "results_docs_review"
STAGE = BASE / "results_stage"


def sha(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8-sig"))


def describe(p):
    p = Path(p)
    return {"path": p.as_posix(), "bytes": p.stat().st_size, "sha256": sha(p)}


source = BASE / "release_stage/verify_repository.py"
tree = ast.parse(source.read_text(encoding="utf-8-sig"))
imports = []
calls = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imports.extend(x.name for x in node.names)
    if isinstance(node, ast.ImportFrom):
        imports.append(node.module)
    if isinstance(node, ast.Call):
        f = node.func
        calls.append((f.id if isinstance(f,ast.Name) else f.attr if isinstance(f,ast.Attribute) else ast.dump(f), node.lineno))
assert set(imports) == {"argparse", "hashlib", "json", "pathlib"}
for name, lineno in calls:
    assert name not in {"write", "write_text", "write_bytes", "mkdir", "unlink", "rmdir", "remove", "rename", "replace", "rmtree", "system", "Popen", "run", "call", "exec", "eval", "fit", "solve", "optimize", "step", "reset"}, (name,lineno)
functions = {n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
sha_open = [n for n in ast.walk(functions["sha"]) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="open"]
assert len(sha_open)==1 and ast.literal_eval(sha_open[0].args[0])=="rb"

# Independent enumeration, without importing or executing the new verifier or model.
freeze = read(BASE/"results_stage/v32/FROZEN_SOURCE_V32_P01.json")
paths = set()
for folder in ("src", "tools", "scripts", "tests"):
    paths.update((REPO/folder).rglob("*.py"))
paths.update((REPO/"configs").rglob("*.json"))
paths.update(REPO.glob("*.py"))
if (REPO/"pytest.ini").is_file(): paths.add(REPO/"pytest.ini")
actual_source = {p.relative_to(REPO).as_posix():sha(p) for p in sorted(paths) if "__pycache__" not in p.parts and not p.is_symlink()}
suffixes = {".csv", ".json", ".idf", ".epw", ".txt"}
inputs = [p for p in (REPO/"inputs").rglob("*") if p.is_file() and p.suffix.lower() in suffixes and "literature" not in p.parts and not p.is_symlink()]
inputs += list(REPO.glob("requirements*.txt"))
actual_inputs = {p.relative_to(REPO).as_posix():sha(p) for p in sorted(inputs)}
assert actual_source == freeze["source_files"] and len(actual_source)==873
assert actual_inputs == freeze["input_files"] and len(actual_inputs)==36
assert not any(x.startswith("release/") for x in actual_source)

cat = read(STAGE/"CATALOG.json")
assert len(cat["copied_artifacts"])==315
for row in cat["copied_artifacts"]:
    p = STAGE/row["staged_path"]
    assert p.stat().st_size == row["bytes"] and sha(p)==row["sha256"]
scope = []
for v in cat["versions"]:
    r=STAGE/v["version"]
    audit=read(r/v["final_audit_relative_path"])
    rows=list(csv.DictReader((r/v["final_table_relative_path"]).open(encoding="utf-8-sig",newline="")))
    assert audit["final_matrix_complete_and_clean"] and len(rows)==v["table_rows"]
    scope.append({"version":v["version"],"scientific_producer":v["scientific_producer"],
                  "rows":len(rows),"source_files":v["scientific_source_files"],
                  "final_audit":v["final_audit_relative_path"],"status":audit["status"]})
    expected = {"v30":(32,0),"v31":(8,4),"v32":(4,2)}[v["version"]]
    new = [x for x in rows if x.get("evidence_role")=="new_v31" or x.get("evidence_role")=="new_v32"]
    if v["version"]!="v30":
        # Evidence-role strings may be descriptive; producer identifies actual new source.
        new=[x for x in rows if x["producer"]==v["scientific_producer"]]
        assert len(new)==expected[1]

reg32=read(STAGE/"v32/EXPERIMENT_REGISTRATION.json")
assert reg32["information_permission"]=="offline_known_window"
assert reg32["fixed_background"]["start_hour"]==1160
assert reg32["fixed_background"]["end_minute"]==180
assert reg32["scope"]["full_continuous_plan_replayed"] is False
assert reg32["scope"]["planned_storage_ports_executed"] is False
assert reg32["scope"]["planned_unmet_directly_executed"] is False
r32=list(csv.DictReader((STAGE/"v32/evaluation/final02/MAIN_RESULT_TABLE.csv").open(encoding="utf-8-sig",newline="")))
for row in r32:
    for priority in ("P0","P1"):
        for carrier in ("e","h","c"):
            assert float(row[f"{priority}_{carrier}_material_minutes"])==0
new_disturbed=next(x for x in r32 if x["id"]=="v32_b01_grid_external")
assert round(float(new_disturbed["actual_fee_USD2001"]),3)==88.116
assert round(float(new_disturbed["P3_e_ENS_kWh"]),3)==200.891
assert round(float(new_disturbed["ups_final_stock_kWh"]),2)==26.33

entry_points = []
for rel in ("tools/model_review_v30/validation.py","tools/model_closeout_v31/validation.py","tools/model_reference_v32/reference.py"):
    p=REPO/rel
    t=ast.parse(p.read_text(encoding="utf-8-sig"))
    assert any(isinstance(n,ast.FunctionDef) and n.name=="main" for n in t.body)
    entry_points.append(describe(p))

links=[]
draft=(BASE/"README_DRAFT.md").read_text(encoding="utf-8-sig")
for link in re.findall(r"\]\(([^)]+)\)",draft):
    if link.startswith("https://"):
        links.append({"link":link,"status":"remote publication target, not queried in static review"})
        continue
    if link.startswith("results/"):
        p=STAGE/link.removeprefix("results/")
    elif link.startswith("release/"):
        p=BASE/"release_stage"/link.removeprefix("release/")
    elif link.startswith("docs/publication/"):
        p=BASE/link.removeprefix("docs/publication/")
    else:
        p=REPO/link
    links.append({"link":link,"resolved_review_path":p.as_posix(),"status":"exists" if p.is_file() else "pending final assembly"})
pending=[x for x in links if x["status"]=="pending final assembly"]
targeted=read(BASE/"steps/v31_v32_targeted01/COMMAND.json")
assert targeted["exit_code"]==0
targeted_log=(BASE/"steps/v31_v32_targeted01/stdout.log").read_text(encoding="utf-8-sig")
assert "61 passed" in targeted_log
default=read(BASE/"steps/default_pytest_after_cleanup02/COMMAND.json")
default_status="finished_exit_"+str(default["exit_code"]) if "exit_code" in default else "running_or_no_final_exit_receipt"

review={"schema":"hospital_ies.publish.independent_static_docs_review.v1",
        "reviewed_utc":datetime.now(timezone.utc).isoformat(),
        "verdict":"no scientific or interface blocker found; final publication assembly metadata links still pending",
        "reviewed_artifacts":[describe(BASE/"README_DRAFT.md"),describe(BASE/"AGENTS_DRAFT.md"),describe(source),describe(STAGE/"CATALOG.json"),describe(STAGE/"README_RESULTS_ZH.md")],
        "AST_modules":sorted(imports),"AST_write_model_solver_or_process_calls":[],
        "independent_current_D_source_hash_matches":873,"independent_current_D_input_hash_matches":36,
        "release_helpers_in_frozen_source_enumeration":False,"result_copies_hash_matches":315,
        "versions":scope,"current_public_entry_files":entry_points,
        "README_links":links,"pending_local_publication_links":pending,
        "current_publishing_tests":{"V31_V32_explicit":{"actual_passed":61,"exit_code":0},"default_cleanup_rerun":default_status},
        "nonblocking_wording_suggestion":"README: replace '在V32的b01一小时外网停电窗口，六条记录' with '在V32的b01 180分钟正常/扰动比较中（停电分支60-120分钟断网），六条记录' to make the episode horizon explicit",
        "not_claimed":"No current full default-test pass; no assembled verify_repository pass; no remote publication URL validation",
        "new_scientific_simulations":0,"new_solver_calls":0,"scientific_recomputations":0,"D_or_old_results_writes":0}
OUT.mkdir(parents=True,exist_ok=True)
(OUT/"REVIEW.json").write_text(json.dumps(review,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
report="""# 独立发布文档静态审查

未发现科学结果、producer、版本角色、信息权限或公开入口的实质错误。README与结果总览均明确V30为32条主比较、V31为8复用+4新增、V32为4复用+2新增；离线参照的低费用同时伴随P3缺供和较低末UPS，没有被写成同服务节费胜出。旧R03 hour211和V32 b01 hour1160分离。

本次独立静态核对重新读取D工作区873个科学源码及36项输入，逐文件SHA与V32 freeze一致；315个结果副本逐SHA符合CATALOG。没有导入或执行模型/求解器。verify_repository.py AST仅引入argparse/hashlib/json/pathlib，文件只读，源枚举与冻结runner一致；release辅助Python不进入873科学源码集合。三条README公开入口均真实存在且有main入口。

以下是最终装配门槛，草稿阶段尚未完成：release/VALIDATION.json、ENVIRONMENT.json、ASSETS.json和docs/publication/CLEANUP_AND_DEPENDENCIES.md等链接需随最终文件落盘后再次核验。release/CURRENT.json也尚未生成，因此本审查不宣称默认仓库核验器已经成功执行。远端Release/标签链接由根任务在发布后核验。

已核实新增V31/V32显式测试原始日志为61 passed、退出0。默认cleanup第二轮命令在本次审查读取时尚无最终exit_code，绝不写成已通过。V30/V31/V32报告中已有的1363/60/17/35等历史验证口径保留其版本来源，未与本次61项混算。

建议澄清一句非阻断文字：将README的“b01一小时外网停电窗口，六条记录”改为“b01的180分钟正常/扰动比较（停电分支60–120分钟断网）”，避免把六条完整episode误读为只有一小时。

完整审查对象SHA、链接解析、当前测试状态和源码/结果核对记录见REVIEW.json。本审查未修改D盘、旧数据、科学源码或结果。
"""
(OUT/"REVIEW_ZH.md").write_text(report,encoding="utf-8")
print(json.dumps({"status":"review_complete","source_hashes":873,"input_hashes":36,"result_hashes":315,
                  "pending_local_links":[x["link"] for x in pending],"default_test_status":default_status,
                  "output":OUT.as_posix()},ensure_ascii=False,indent=2))
