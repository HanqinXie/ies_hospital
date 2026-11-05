"""三并发年度启动入口：依次核验、验证、准入，再启动原独立监督器。
仅显式执行本文件才启动任务；--dry-run 只显示计划，不写文件。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parent
KIND = "normal_year" if (PACKAGE / "transfer_checks.py").is_file() else "busy_year"
# 繁忙年可以在此填写正常年 M_ready 的完整原始 producer 输出目录。
# 留空时，双击运行会询问；也可传 --normal-reference。
NORMAL_REFERENCE = ""


def three_limits():
    limits = json.loads((PACKAGE / "limits.example.json").read_text(encoding="utf-8-sig"))
    limits.update(max_producers=3, max_accounts=1, max_total_processes=4,
                  threads_per_producer=1)
    return limits


def build_plan(work, frozen=None, reference=None):
    work = Path(work).resolve()
    limits = str(work / "limits.json")
    def cmd(script, *args):
        return [sys.executable, "-B", str(PACKAGE / script), *map(str, args)]
    if KIND == "normal_year":
        return [
            ("verify", cmd("transfer_checks.py", "verify")),
            ("source", cmd("verify_source_copy.py")),
            ("payload", cmd("build_payload.py")),
            ("fixtures", cmd("build_legacy_fixtures.py")),
            ("launcher_probe", cmd("scripts/verify_launcher.py", "--work-root", work)),
            ("preflight", cmd("transfer_checks.py", "preflight", "--work-root", work)),
            ("suite", cmd("scripts/server_validate.py", "suite", "--work-root", work,
                         "--timeout-seconds", 43200)),
            ("license", cmd("scripts/server_validate.py", "license", "--work-root", work,
                           "--workers", 3, "--timeout-seconds", 1800)),
            ("short", cmd("scripts/server_validate.py", "short", "--work-root", work,
                         "--timeout-seconds", 43200)),
            ("admit", cmd("transfer_checks.py", "admit", "--work-root", work,
                         "--limits-file", limits, "--valid-hours", 168)),
            ("launch", cmd("launch_server.py", "--work-root", work)),
        ]
    return [
        ("verify", cmd("runtime_checks.py", "verify")),
        ("freeze", cmd("freeze_calendar.py", "--reference-output", reference,
                       "--output", frozen)),
        ("preflight", cmd("runtime_checks.py", "preflight", "--frozen-root", frozen,
                          "--work-root", work)),
        ("launcher_probe", cmd("scripts/verify_launcher.py", "--work-root", work)),
        ("validate", cmd("runtime_checks.py", "validate", "--frozen-root", frozen,
                         "--work-root", work, "--limits-file", limits)),
        ("admission", cmd("runtime_checks.py", "check-admission", "--frozen-root", frozen,
                          "--work-root", work)),
        ("launch", cmd("launch_server.py", "--frozen-root", frozen, "--work-root", work)),
    ]


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def validate_locations(work, frozen, reference):
    if sys.platform != "win32":
        raise ValueError("正式运行须在已授权的 Windows 服务器执行。")
    if work.drive.upper() != "E:" or not Path("E:/").is_dir():
        raise ValueError("工作目录必须位于实际 E 盘；不自动映射盘符。")
    if work == PACKAGE or work in PACKAGE.parents or PACKAGE in work.parents:
        raise ValueError("工作目录必须在交接包外，且不能包含交接包。")
    if KIND == "busy_year":
        if not reference.is_dir():
            raise ValueError("normal reference 不存在，请选择完整正常 M_ready 原始输出目录。")
        if frozen.exists():
            raise FileExistsError("冻结日历目录已存在；保留旧目录，请使用新路径。")
        for other in (PACKAGE, work, reference):
            if frozen == other or frozen in other.parents or other in frozen.parents:
                raise ValueError("冻结目录必须独立于交接包、工作目录和正常参考目录。")
        if frozen.drive.upper() != "E:":
            raise ValueError("新冻结日历必须位于实际 E 盘。")


def execute(work, frozen=None, reference=None):
    work = Path(work).resolve()
    if KIND == "busy_year" and reference is None:
        raise ValueError("busy 年必须指定正常 M_ready reference。")
    if work.exists():
        raise FileExistsError("工作目录已存在；不会覆盖、重复启动或自动从初态重跑：" + str(work))
    frozen = Path(frozen).resolve() if frozen is not None else None
    reference = Path(reference).resolve() if reference is not None else None
    validate_locations(work, frozen, reference)
    plan = build_plan(work, frozen, reference)
    work.mkdir(parents=True, exist_ok=False)
    logs = work / "oneclick"
    logs.mkdir()
    temp = work / "temp"
    temp.mkdir()
    write_json(work / "limits.json", three_limits())
    write_json(logs / "PLAN.json", dict(
        kind=KIND, work_root=str(work), frozen_root=str(frozen) if frozen else None,
        normal_reference=str(reference) if reference else None,
        entry_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        commands=[dict(stage=name, argv=command) for name, command in plan],
        annual_completion_claimed=False))
    env = os.environ.copy()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        env[name] = "1"
    env.update(TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8")
    # Do not set PYTHONUTF8: it is incompatible with some host Python startups.
    current = None
    try:
        for number, (stage, command) in enumerate(plan, 1):
            current = stage
            log_path = logs / (str(number).zfill(2) + "_" + stage + ".log")
            print(f"[{number}/{len(plan)}] {stage}  日志：{log_path}", flush=True)
            event = dict(stage=stage, command=command,
                         started_utc=datetime.now(timezone.utc).isoformat())
            with log_path.open("x", encoding="utf-8") as output:
                result = subprocess.run(command, cwd=str(PACKAGE), env=env,
                                        stdout=output, stderr=subprocess.STDOUT, shell=False)
            event.update(exit_code=result.returncode,
                         finished_utc=datetime.now(timezone.utc).isoformat())
            with (logs / "COMMANDS.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            if result.returncode:
                write_json(logs / "RESULT.json", dict(
                    status="failed", stage=stage, exit_code=result.returncode,
                    annual_completion_claimed=False))
                print("此步骤失败，后续步骤未执行。请查看日志：" + str(log_path), flush=True)
                return result.returncode
    except BaseException as error:
        write_json(logs / "RESULT.json", dict(
            status="interrupted" if isinstance(error, KeyboardInterrupt) else "error",
            stage=current, exit_code=None, error_type=type(error).__name__,
            error=str(error), annual_completion_claimed=False))
        raise
    write_json(logs / "RESULT.json", dict(
        status="launch_command_succeeded", exit_code=0, annual_completion_claimed=False))
    print("原独立监督启动命令已成功返回；这不代表年度计算已经完成。", flush=True)
    print("运行状态：" + subprocess.list2cmdline([
        sys.executable, "-B", str(PACKAGE / "server_runner.py"),
        "status", "--work-root", str(work)]), flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, help="包外 E 盘新工作目录；默认自动生成")
    parser.add_argument("--normal-reference", type=Path, help="busy 所需完整正常 M_ready 原始输出目录")
    parser.add_argument("--frozen-root", type=Path, help="busy 新冻结日历目录；默认在工作目录同级")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不运行、不写文件")
    parser.add_argument("--no-pause", action="store_true", help="命令行运行结束后不等待回车")
    args = parser.parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    work = (args.work_root or Path("E:/hospital_ies_" + KIND + "_" + stamp)).resolve()
    frozen = (args.frozen_root or work.with_name(work.name + "_calendar")).resolve()
    reference = args.normal_reference or (Path(NORMAL_REFERENCE) if NORMAL_REFERENCE.strip() else None)
    if KIND == "busy_year" and reference is None:
        if args.dry_run:
            reference = Path("NORMAL_M_READY_OUTPUT")
        else:
            raw = input("请输入正常年 M_ready 完整原始输出目录（不能用汇总表或旧35天结果）：").strip().strip('"')
            if not raw:
                raise ValueError("未指定正常年 M_ready reference；没有启动。")
            reference = Path(raw)
    print("模式：" + KIND + "；三生产并发、一核账；每条求解器/BLAS单线程。", flush=True)
    print("工作目录：" + str(work), flush=True)
    if args.dry_run:
        for stage, command in build_plan(work, frozen, reference):
            print(stage + ": " + subprocess.list2cmdline(command))
        return 0
    return execute(work, frozen, reference)


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except KeyboardInterrupt:
        print("\n启动流程被中断。请核查所属进程与原停止机制的关闭收据。", file=sys.stderr)
        code = 130
    except Exception as error:
        print(f"启动停止：{type(error).__name__}: {error}", file=sys.stderr)
    finally:
        # Double-click keeps errors visible. Command-line calls never pause.
        if len(sys.argv) == 1 and sys.stdin.isatty():
            try:
                input("\n按回车关闭窗口……")
            except (EOFError, KeyboardInterrupt):
                pass
    raise SystemExit(code)

