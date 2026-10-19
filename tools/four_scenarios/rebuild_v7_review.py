"""Rebuild two v7 delivery corrections from intact numeric CSV and audit receipts.

This does not modify v7. It writes a new UTF-8 review into the v8 run folder.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V7 = ROOT / "outputs" / "v7_four_scenarios_20261016_run01"


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: str) -> str:
    if value == "" or value is None:
        return "未知"
    return f"{float(value):+.6g}"


def rebuild(output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    contrast_path = V7 / "review" / "ADDITIONAL_MATCHED_CONTRASTS.csv"
    flagged_path = V7 / "analysis" / "UNKNOWN_AND_FAILURES.csv"
    audit_path = V7 / "main" / "AUDIT.json"
    contrasts, flagged = _csv(contrast_path), _csv(flagged_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if (len(contrasts) != 48 or Counter(row["comparison_id"] for row in contrasts)
            != {"L4b_minus_L4a": 24, "L2_minus_N2": 24}):
        raise ValueError("Original matched contrast denominator changed")
    if (len(flagged) != 27 or any(row["status"] != "completed" for row in flagged)
            or sum(row["full_P0_failure"] == "1" for row in flagged) != 7):
        raise ValueError("Original v7 failure/protection accounting changed")
    if (audit.get("passed") is not True or audit.get("registered_pair_count") != 168
            or audit.get("terminal_pair_count") != 168):
        raise ValueError("Original v7 raw audit receipt changed")

    correction = [
        "# v7 基线交付勘误（新轮只读重分析）",
        "",
        "旧 `FOUR_SCENARIOS_REPORT.md` 称 `UNKNOWN_AND_FAILURES.csv` ‘无记录’不准确。",
        "该表实际有 **27 条**，全部为 `completed`；其中 **7 条**发生观察到的 P0 服务失败，",
        "其余记录是保护或联锁相关标记（可能与前 7 条交叠）。准确结论是：",
        "**v7 的 168 条轨迹没有数值截断或未知尾部，但有真实服务失效与保护记录。**",
        "不得删掉这些失败，也不能把它们并入旧 v6 的 20 条截断未知。",
        "",
        "旧 `review/ADDITIONAL_MATCHED_CONTRASTS.md` 有实际问号替换的中文；",
        "下述新文件完全从仍完整的数值 CSV 与审计收据重建，不能把终端编码修正当作恢复原文字。",
        "L4b−L4a 表示在同根已设计冲击上**添加整段故障过程**；",
        "旧 L4b 与 L3 的故障绝对时钟有 8/12 根不一致，不能据其直接计算纯故障×冲击交互。",
        "本轮新四格另行匹配时钟、传播和初态。",
        "",
        f"数值来源 SHA-256：`{_sha(contrast_path)}`；失败/保护表 SHA-256：`{_sha(flagged_path)}`；",
        f"原审计 SHA-256：`{_sha(audit_path)}`。三项旧文件均只读。",
        "",
    ]
    contrast_lines = [
        "# v7 补充同根对照（由原始数值 CSV 重建）",
        "",
        "本表一行是一个旧日根、一个策略、一个已登记差分；同根多行不是独立医院背景。",
        "差分定义为左侧情景减右侧情景。L4b−L4a 是添加完整设备故障过程；",
        "L2−N2 是无传播条件下增加纯需求冲击。所有差值保留原载能和单位。",
        "表中‘未知’保持缺失，不能按零解释。",
        "",
        f"来源：旧 `review/ADDITIONAL_MATCHED_CONTRASTS.csv`，SHA-256 `{_sha(contrast_path)}`，48 行；",
        "左/右 raw 的 SHA-256 见原 CSV。",
        "",
        "| 对照 | 日根 | 策略 | 故障设备 | ΔP0失败(min) | ΔP0电/热/冷 ENS(kWh) | ΔP3电/冷 ENS(kWh) | Δ真实GT跳闸 |",
        "|---|---:|---|---|---:|---|---|---:|",
    ]
    for row in sorted(contrasts, key=lambda item: (
            item["comparison_id"], int(item["source_day_index"]), item["policy_id"])):
        p0 = "/".join(_number(row[f"delta_full_P0_{carrier}_ENS_kWh"])
                      for carrier in "ehc")
        p3 = "/".join(_number(row[f"delta_full_P3_{carrier}_ENS_kWh"])
                      for carrier in "ec")
        contrast_lines.append(
            f"| {row['comparison_id']} | {row['source_day_index']} | {row['policy_id']} | "
            f"{row['fault_device'] or '无'} | {_number(row['delta_full_P0_failure_minutes'])} | "
            f"{p0} | {p3} | {_number(row['delta_GT_protection_trip_count_observed'])} |"
        )
    correction_path = output_dir / "BASELINE_CORRECTIONS.md"
    rebuilt_path = output_dir / "ADDITIONAL_MATCHED_CONTRASTS_REBUILT.md"
    for path, lines in ((correction_path, correction), (rebuilt_path, contrast_lines)):
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
    return {
        "v7_completed_pairs": audit["terminal_pair_count"],
        "v7_flagged_rows": len(flagged),
        "v7_p0_failure_rows": 7,
        "contrast_rows": len(contrasts),
        "correction_path": str(correction_path),
        "rebuilt_contrast_path": str(rebuilt_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.out_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
