"""Fixed final finite-clock comparisons; compact JSON only, no physical imports."""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parents[3]
B19 = ROOT / "outputs/v19_model_closeout_20261018_run01"
POLICY = {"E": "endurance_restore_p0_ready_v14r6", "K1": "readiness_handoff_K1_v15", "C19": "C19_critical_reserve_v19r1"}
RULES = [dict(group="contact_long005", left="long_final_r1_005", right="sensitivity_contact025_r1_000",
              path="execution_recovery.local_contact_restore_delay_minutes", before=0., after=.25, end=1065.,
              registration="SENSITIVITY_CONTACT025_R1_PROPOSAL.json"),
         dict(group="OPEN_main003", left="main_final_r1_003", right="sensitivity_open1_r1_000",
              path="execution_recovery.grid_open_transfer_minutes", before=.25, after=1., end=600.,
              registration="SENSITIVITY_OPEN1_R1_PROPOSAL.json"),
         dict(group="OPEN_main067", left="main_final_r1_067", right="sensitivity_open1_r1_001",
              path="execution_recovery.grid_open_transfer_minutes", before=.25, after=1., end=None,
              registration="SENSITIVITY_OPEN1_R1_PROPOSAL.json")]
SERVICE = ("ENS_kWh", "disconnected_ENS_kWh", "connected_unserved_ENS_kWh", "material_minutes", "longest_material_minutes",
           "loss_runs", "first_loss_minute", "first_clean_minute", "confirmed_30min_interval", "recurrence_after_30min_count",
           "recovery_status", "recovery_is_permanent")
ECON = ("actual_energy_fee_USD", "accounting_cost_USD", "DG_shaft_work_kWh", "DG_electric_work_kWh", "DG_loss_work_kWh",
        "DG_rotor_delta_kWh", "DG_fuel_budget_debit_kWh_equivalent", "DG_original_electric_fee_USD", "DG_shaft_equivalent_fee_USD")


def read(path): return json.loads(path.read_text(encoding="utf-8"))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def ref(path): return dict(path=path.relative_to(ROOT).as_posix(), sha256=sha(path), bytes=path.stat().st_size)
def number(value): return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def differences(left, right, prefix=""):
    if isinstance(left, dict) and isinstance(right, dict):
        return [d for key in sorted(set(left) | set(right)) for d in differences(left.get(key), right.get(key), prefix + "." + key if prefix else key)]
    return [] if left == right else [(prefix, left, right)]


def pair_reasons(left, right, path, before, after):
    reasons = []
    if differences(left["config"], right["config"]) != [(path, before, after)]: reasons.append("config_difference_not_exact_registered_clock")
    for key, reason in (("scene", "scene_all_fields_differ"), ("source", "source_mismatch"), ("freeze", "freeze_mismatch"),
                        ("policies", "policy_panel_mismatch"), ("policy_specs", "policy_specs_mismatch"),
                        ("inventory", "input_inventory_unknown_or_mismatch"), ("registered_end", "registered_window_mismatch")):
        if left.get(key) != right.get(key) or (key in ("inventory", "freeze") and left.get(key) is None): reasons.append(reason)
    if not left.get("eligible") or not right.get("eligible"): reasons.append("terminal_or_result_incomplete_or_unknown")
    for policy in left["policies"]:
        for field in ("reset", "applied", "injection"):
            a, z = left["matching"].get(policy, {}).get(field), right["matching"].get(policy, {}).get(field)
            if a is None or z is None or a != z: reasons.append(policy + "_" + field + "_unknown_or_mismatch")
    return reasons


def service_known(value):
    return (all(number(value.get(k)) for k in ("ENS_kWh", "material_minutes", "longest_material_minutes"))
            and isinstance(value.get("loss_runs"), list) and isinstance(value.get("recovery_status"), str)
            and value["recovery_status"] not in ("unknown", "unavailable"))


def require(value, message):
    if not value: raise ValueError(message)


def load_batch(name, cp, frozen, inputs):
    directory = B19 / "experiments" / name
    plan_path, manifest_path, terminal_path = [directory / x for x in ("plan.json", "manifest.json", "BATCH_RESULT.json")]
    plan, manifest, terminal = read(plan_path), read(manifest_path), read(terminal_path)
    inputs.extend([ref(plan_path), ref(manifest_path), ref(terminal_path)])
    require(len(manifest["scenarios"]) == 1, name + ": one fixed scene required")
    require(manifest["source_sha256"] == frozen["source_sha256"] and len(manifest["source_sha256"]) == 345, name + ": frozen 345 source mismatch")
    require(manifest.get("freeze_signature") == frozen["signature"], name + ": freeze signature mismatch")
    require([p["policy_id"] for p in plan["policy_specs"]] == list(POLICY.values()), name + ": fixed E/K1/C19 policy panel mismatch")
    require(terminal.get("registered_pairs") == terminal.get("complete_full") == terminal.get("actual_attempts") == 3
            and terminal.get("unknown_full") == 0 and terminal.get("analysis_failure_or_unavailable_count") == 0
            and terminal.get("status_counts") == {"completed": 3}, name + ": terminal not exactly three complete outcomes")
    scene = manifest["scenarios"][0]
    # The helper validates each side against its resolved registration. The
    # frozen baseline and the sole preregistered clock deviation are checked
    # independently below; this is not a claim that variant config equals freeze.
    own_contract = copy.deepcopy(frozen); own_contract["model_config"] = manifest["model_config"]
    cp_inputs = []
    candidates = [r for r in cp.collect_batch(directory, own_contract, {scene["id"]: scene["id"]}, cp_inputs)
                  if r["window"] == "registered_full"]
    inputs.extend(ref(Path(x["path"])) for x in cp_inputs)
    require(len(candidates) == 3 and all(r["eligible"] for r in candidates),
            name + ": strict compact helper refused: " + json.dumps([r["reasons"] for r in candidates if not r["eligible"]]))
    evidence = terminal["matching_evidence"][scene["id"]]
    require(evidence.get("injection_state_coverage_known") is True, name + ": injection state unknown")
    matching = {}
    for index, alias in enumerate(POLICY):
        own = {}
        for key, field in (("reset", "initial_state_sha256"), ("applied", "applied_exogenous_definition_sha256"), ("injection", "injection_state_sha256")):
            values = evidence.get(field)
            require(isinstance(values, list) and len(values) == 3 and all(cp.is_sha(x) for x in values), name + ": " + field + " unknown")
            own[key] = values[index]
        matching[alias] = own
    saved = {}
    for alias, policy in POLICY.items():
        candidate = next(r for r in candidates if r["policy_id"] == policy)
        result_path = directory / "pairs" / (scene["id"] + "__" + policy) / "RESULT.json"
        result = read(result_path); metrics = candidate["saved_metrics"]
        for priority in ("P0", "P1"):
            for carrier in ("e", "h", "c"):
                value = metrics["carrier_service"][priority][carrier]
                require(service_known(value) and all(k in value for k in SERVICE), name + ": missing/unknown critical service metric")
        economics = metrics["economics"]
        require(all(number(economics.get(k)) for k in ECON), name + ": missing/unknown actual fee or DG receipt")
        audit = result["analysis"]["v19"]["episode_audit"]
        require(abs(economics["DG_shaft_work_kWh"] - audit["DG_totals"]["mechanical_work_kWh"]) <= audit["work_audit_tolerance_kWh"], name + ": actual DG shaft audit mismatch")
        saved[alias] = dict(metrics=metrics, audit_DG_totals=audit["DG_totals"], result=ref(result_path))
    return dict(batch=name, scene=scene, source=manifest["source_sha256"], source_binding_digest=manifest["simulator_hash"],
        freeze=manifest["freeze_signature"], config=manifest["model_config"], policies=list(POLICY), policy_specs=plan["policy_specs"],
        inventory=cp.inventory_signature(manifest), registered_end=float(scene["duration_steps"]) * float(scene["step_minutes"]),
        matching=matching, terminal_matching_evidence=evidence, eligible=True, saved=saved,
        plan_sha256=sha(plan_path), manifest_sha256=sha(manifest_path), terminal_sha256=sha(terminal_path))


def write_csv(path, rows):
    keys = sorted({k for r in rows for k in r})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys); writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in r.items()} for r in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--name", default="final_finite_clocks01")
    args = parser.parse_args()
    require(Path(args.name).name == args.name and args.name not in (".", ".."), "invalid output name")
    output = B19 / "review/finite_clocks" / args.name; output.mkdir(parents=True, exist_ok=False)
    inputs, proof, service_rows, economics_rows = [], [], [], []
    try:
        helper = B19 / "scripts/summarize_compatibility.py"
        spec = importlib.util.spec_from_file_location("inherited_compact_comparison", helper)
        cp = importlib.util.module_from_spec(spec); spec.loader.exec_module(cp)
        frozen_path = B19 / "MODEL_FREEZE_CLOSEOUT01.json"; frozen = read(frozen_path)
        inputs.extend([ref(helper), ref(frozen_path)])
        require(frozen["signature"] == cp.digest({k:v for k,v in frozen.items() if k != "signature"}), "freeze self-signature invalid")
        for rule in RULES:
            left = load_batch(rule["left"], cp, frozen, inputs); right = load_batch(rule["right"], cp, frozen, inputs)
            require(left["config"] == frozen["model_config"], rule["group"] + ": baseline config differs from actual freeze")
            reasons = pair_reasons(left, right, rule["path"], rule["before"], rule["after"])
            require(not reasons, rule["group"] + ": " + ",".join(reasons))
            require(rule["end"] is None or left["registered_end"] == rule["end"], "unexpected requested registered window")
            prereg_path = B19 / "design" / rule["registration"]; prereg = read(prereg_path)["sensitivity_registration"]
            inputs.append(ref(prereg_path))
            require(prereg["signature"] == cp.digest({k:v for k,v in prereg.items() if k != "signature"})
                and prereg["source_freeze_signature"] == frozen["signature"] and prereg["source_sha256"] == frozen["source_sha256"]
                and prereg["model_config"] == right["config"] and prereg["policy_specs"] == right["policy_specs"], "sensitivity preregistration mismatch")
            group_proof = dict(rule=rule, matched=True, window_minutes=[0., left["registered_end"]],
                scene_all_fields_equal=True, source_345_equal=True, freeze_equal=True, policy_specs_equal=True,
                config_differences=differences(left["config"], right["config"]), input_inventory_equal=left["inventory"] == right["inventory"],
                source_binding_digest=left["source_binding_digest"], freeze_signature=left["freeze"],
                baseline={k:v for k,v in left.items() if k not in ("source", "saved")},
                variant={k:v for k,v in right.items() if k not in ("source", "saved")}, preregistration=ref(prereg_path))
            proof.append(group_proof)
            for alias in POLICY:
                identity = dict(group=rule["group"], policy=alias, policy_id=POLICY[alias], baseline_batch=rule["left"], variant_batch=rule["right"],
                    scenario_id=left["scene"]["id"], window="registered_full", window_start_minute=0., window_end_minute=left["registered_end"],
                    clock_path=rule["path"], baseline_clock_minutes=rule["before"], variant_clock_minutes=rule["after"], matched=True,
                    source_binding_digest=left["source_binding_digest"], freeze_signature=left["freeze"])
                for priority in ("P0", "P1"):
                    for carrier in ("e", "h", "c"):
                        a = left["saved"][alias]["metrics"]["carrier_service"][priority][carrier]
                        z = right["saved"][alias]["metrics"]["carrier_service"][priority][carrier]
                        row = dict(identity, priority=priority, carrier=carrier)
                        row.update({"baseline_" + k: a[k] for k in SERVICE}); row.update({"variant_" + k: z[k] for k in SERVICE})
                        row.update({"delta_" + k: z[k] - a[k] if number(a[k]) and number(z[k]) else None
                                    for k in SERVICE if k not in ("loss_runs", "confirmed_30min_interval", "recovery_status", "recovery_is_permanent")})
                        service_rows.append(row)
                a, z = left["saved"][alias]["metrics"]["economics"], right["saved"][alias]["metrics"]["economics"]
                row = dict(identity)
                for key in ECON: row.update({"baseline_" + key: a[key], "variant_" + key: z[key], "delta_" + key: z[key] - a[key]})
                economics_rows.append(row)
        require(sha(helper) == next(x["sha256"] for x in inputs if x["path"] == helper.relative_to(ROOT).as_posix()), "inherited helper changed during collection")
        write_csv(output / "CRITICAL_SERVICE.csv", service_rows); write_csv(output / "COST_AND_DG.csv", economics_rows)
        report = dict(schema="v19.final_finite_clock_comparison.v1", status="complete_strictly_matched", generated_utc=datetime.now(timezone.utc).isoformat(),
            collector=ref(Path(__file__).resolve()), comparisons=proof, critical_service=service_rows, cost_and_DG=economics_rows,
            delta_direction="variant clock minus baseline clock; no different carriers are added",
            actual_DG_shaft_basis="Saved economics.DG_shaft_work_kWh cross-checked to complete episode audit DG_totals.mechanical_work_kWh using its unchanged audit tolerance",
            actual_fee_boundary="Saved original actual_energy_fee_USD; accounting_cost and illustrative DG shaft-equivalent fee are separate columns",
            injection_semantics="Per-policy injection hashes equal across each clock pair; policy-dependent differences within E/K1/C19 are retained and permitted",
            scope="Three fixed seen-case sensitivities; no strategy search, universal delay robustness or mechanical continuity proof; no development C18 positive used",
            proof_files=sorted({x["path"]: x for x in inputs}.values(), key=lambda x:x["path"]),
            physical_episodes_started=0, raw_files_opened=0, annual_files_opened=0)
        (output / "FINAL_CLOCK_COMPARISON.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# 最终有限时钟对比", "", "三组固定对比均通过同场景全字段、345 源文件绑定、freeze、唯一登记时钟差、策略面板、输入清单及逐策略 reset/applied-exog/injection 匹配。delta 均为变体减基准，未跨载能相加。", "", "| 组 | 策略 | P0 e/c 实质分钟 Δ | P1 e/c 实质分钟 Δ | 实际能源费 Δ USD | 实际 DG 轴功 Δ kWh |", "|---|---|---:|---:|---:|---:|"]
        for row in economics_rows:
            own = [r for r in service_rows if (r["group"],r["policy"]) == (row["group"],row["policy"])]
            def change(p,c): return next(r["delta_material_minutes"] for r in own if (r["priority"],r["carrier"]) == (p,c))
            lines.append(f"| {row['group']} | {row['policy']} | {change('P0','e'):.9g} / {change('P0','c'):.9g} | {change('P1','e'):.9g} / {change('P1','c'):.9g} | {row['delta_actual_energy_fee_USD']:.9g} | {row['delta_DG_shaft_work_kWh']:.9g} |")
        lines += ["", "完整逐 P0/P1、电/热/冷 ENS、实质分钟、最长段、全部 loss_runs 及恢复状态见 CRITICAL_SERVICE.csv 与 JSON；费用和实际 DG 轴功及原审计工作量见 COST_AND_DG.csv。正延迟带来的坏结果原样保留，不用开发期 C18 结果替代。", "", "此表仅支持所登记场景和完整窗口的观察，不推广所有策略或机械动态。未读 raw/annual，未重放，未改生产或阈值。"]
        (output / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        receipt = dict(status=report["status"], comparisons=3, policy_comparisons=9, critical_carrier_rows=len(service_rows), economics_rows=len(economics_rows),
            output_files=[ref(x) for x in sorted(output.iterdir())], physical_episodes_started=0, raw_files_opened=0, annual_files_opened=0)
        (output / "RECEIPT.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(dict(status=receipt["status"], output=output.relative_to(ROOT).as_posix(), report=ref(output / "FINAL_CLOCK_COMPARISON.json"), critical_rows=len(service_rows), policy_comparisons=9)))
        return 0
    except Exception as error:
        (output / "FAILED_ATTEMPT.json").write_text(json.dumps(dict(status="failed_or_unknown_no_retry", error=dict(type=type(error).__name__, message=str(error)), traceback=traceback.format_exc(), proof_files=inputs), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(status="failed_or_unknown_no_retry", error=str(error), output=output.relative_to(ROOT).as_posix())))
        return 1


if __name__ == "__main__": raise SystemExit(main())
