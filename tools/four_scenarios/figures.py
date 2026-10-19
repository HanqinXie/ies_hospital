"""Audited raw -> traceable four-scenario mechanism figures.

The figure selector is retrospective and descriptive. It never supplies an
online policy with an incident label or treats a truncated tail as zero.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest


CORE_CLASSES = ("L1", "L3", "L2", "L4b")
OPTIONAL_CLASSES = ("L4a",)
PRIORITIES = ("P0", "P1", "P2", "P3")
CARRIERS = ("e", "h", "c")
SEGMENT_FIELDS = (
    "scenario_id", "policy_id", "raw_sha256", "segment_index", "start_minute",
    "end_minute", "observation_status", "GT_1_kW", "GT_2_kW", "GT_3_kW",
    "grid_kW", "diesel_kW", "connected_electric_demand_kW",
    "hrsg_steam_lbph", "boiler_steam_lbph", "absorption_cooling_kW",
    "EC_1_cooling_kW", "EC_2_cooling_kW", "EC_3_cooling_kW",
    *(f"{p}_{c}_unserved_kW" for p in PRIORITIES for c in CARRIERS),
    "P0_guarantee_failure", "P0_temperature_C",
)
EVENT_FIELDS = (
    "scenario_id", "policy_id", "raw_sha256", "event_id", "time_minute",
    "kind", "source_collection", "command_id", "origin", "cause_class", "device",
    "diesel_requested_kW", "diesel_planned_kW", "diesel_executed_kW",
    "request_json", "request_targets_json", "planned_dispatch_json",
    "executed_dispatch_json",
)


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _sha(path: Path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _flag(value):
    return str(value).strip().lower() in ("true", "1", "yes")


def _number(value, default=None):
    if value is None or value == "":
        return default
    answer = float(value)
    if not math.isfinite(answer):
        raise ValueError("Non-finite analysis value")
    return answer


def _load_analysis(path: Path):
    path = Path(path)
    if path.is_dir():
        path = path / "SCENARIO_RESULTS.csv"
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    required = {"analysis_class", "scenario_id", "root_family_id", "policy_id",
                "full_P0_failure_minutes", "full_full_horizon", "raw_path",
                "raw_sha256", "status", "observed_until_minute"}
    if not rows or not required <= set(rows[0]):
        raise ValueError("Analysis CSV missing figure selection fields")
    return rows, path


def _analysis_csv_text(value):
    """Mirror the analysis writer's cell representation for its JSON rows."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _verify_analysis_summary(analysis_rows, analysis_file, manifest_path, plan_path,
                             audit_path, plan):
    summary_path = analysis_file.with_name("ANALYSIS_SUMMARY.json")
    summary = _read_json(summary_path)
    if summary.get("schema") != "four_scenarios.readonly_analysis.v1":
        raise ValueError("Analysis summary schema mismatch")
    for name, path in (("manifest", manifest_path), ("plan", plan_path),
                       ("audit", audit_path)):
        if summary.get(f"{name}_sha256") != _sha(path):
            raise ValueError(f"Analysis summary {name} SHA mismatch")
    if summary.get("final_blind_opened") is not False:
        raise ValueError("Analysis summary must keep final backgrounds sealed")
    if (summary.get("registered_scenario_count") != len(plan["scenario_ids"]) or
            summary.get("registered_pair_count") != plan["registered_pair_count"]):
        raise ValueError("Analysis summary registered denominator mismatch")
    expected = summary.get("scenario_results")
    if not isinstance(expected, list) or len(expected) != len(analysis_rows):
        raise ValueError("Analysis CSV differs from summary rows")
    fields = {key for row in expected for key in row}
    for actual, original in zip(analysis_rows, expected):
        if (set(actual) != fields or
                actual != {key: _analysis_csv_text(original.get(key)) for key in fields}):
            raise ValueError("Analysis CSV differs from summary rows")
    return summary_path


def _load_batch(manifest_path, plan_path, results_path, audit_path, analysis_path):
    manifest_path, plan_path = Path(manifest_path), Path(plan_path)
    results_path, audit_path = Path(results_path), Path(audit_path)
    manifest, plan, audit = map(_read_json, (manifest_path, plan_path, audit_path))
    if not audit.get("passed") or audit.get("matched_initial_state_and_stream") is not True:
        raise ValueError("A passing matched raw audit is required")
    if audit.get("final_blind_opened") is True:
        raise ValueError("Sealed final backgrounds must remain unopened")
    if plan.get("manifest_sha256") != _sha(manifest_path) or audit.get("manifest_sha256") != _sha(manifest_path):
        raise ValueError("Manifest SHA mismatch")
    if audit.get("plan_sha256") != _sha(plan_path):
        raise ValueError("Plan SHA mismatch")
    if audit.get("registered_pair_count") != plan.get("registered_pair_count"):
        raise ValueError("Registered denominator mismatch")
    if audit.get("terminal_pair_count") != plan.get("registered_pair_count"):
        raise ValueError("Nonterminal registered pair")
    scenes = {s["id"]: s for s in manifest["scenarios"]}
    ledger = {(r["scenario_id"], r["policy_id"]): r for r in audit["pair_ledger"]}
    policy_ids = {p["policy_id"] for p in plan["policy_specs"]}
    analysis_rows, analysis_file = _load_analysis(analysis_path)
    summary_file = _verify_analysis_summary(analysis_rows, analysis_file,
                                            manifest_path, plan_path, audit_path, plan)
    for row in analysis_rows:
        sid, policy = row["scenario_id"], row["policy_id"]
        if sid not in scenes or sid not in plan["scenario_ids"] or policy not in policy_ids:
            raise ValueError("Analysis row outside registered plan")
        if scenes[sid].get("is_final_blind") or row["analysis_class"] != scenes[sid]["analysis_class"]:
            raise ValueError("Analysis class or sealed source mismatch")
    return (scenes, ledger, analysis_rows, results_path, audit_path, plan_path,
            manifest_path, analysis_file, summary_file)


def _select(rows):
    """Choose within-class mechanisms, never an across-policy winner."""
    chosen = []
    by_class = {name: [r for r in rows if r["analysis_class"] == name and
                       r["policy_id"] == "secure_milp"]
                for name in CORE_CLASSES + OPTIONAL_CLASSES}
    for name in CORE_CLASSES + OPTIONAL_CLASSES:
        group = by_class[name]
        if not group:
            if name in CORE_CLASSES:
                raise ValueError(f"No audited secure case for {name}")
            continue
        complete = [r for r in group if _flag(r["full_full_horizon"])]
        unknown = [r for r in group if not _flag(r["full_full_horizon"])]
        if complete:
            severe = max(complete, key=lambda r: (
                _number(r["full_P0_failure_minutes"], -1.0),
                _number(r.get("protection_event_count"), 0.0),
                -len(r["scenario_id"]), r["scenario_id"]))
            minutes = _number(severe["full_P0_failure_minutes"], 0.0)
            role = "failure_case" if minutes > 0 else "no_failure_case"
            reason = ("within-class maximum observed P0 failure minutes" if minutes > 0
                      else "complete no-P0-failure mechanism example")
            if _number(severe.get("protection_event_count"), 0.0) > 0:
                reason += " with observed protection"
            chosen.append((severe, role, reason))
            clear = sorted((r for r in complete if _number(r["full_P0_failure_minutes"], 0.0) == 0
                            and r["scenario_id"] != severe["scenario_id"]),
                           key=lambda r: (-_number(r.get("protection_event_count"), 0.0),
                                          r["scenario_id"]))
            if minutes > 0 and clear:
                reason = "same-class complete no-P0-failure contrast"
                if _number(clear[0].get("protection_event_count"), 0.0) > 0:
                    reason += " with observed protection"
                chosen.append((clear[0], "no_failure_case", reason))
        if unknown:
            row = min(unknown, key=lambda r: r["scenario_id"])
            chosen.append((row, "unknown_truncated", "audited truncated case; tail remains unknown"))
    return chosen


def _read_verified_raw(row, scenes, ledger, results_path):
    sid, policy = row["scenario_id"], row["policy_id"]
    entry = ledger.get((sid, policy))
    if not entry or not entry.get("terminal") or entry.get("status") != row["status"]:
        raise ValueError("Missing or inconsistent audited pair")
    attempts = entry.get("attempts") or []
    if not attempts:
        raise ValueError("Audited pair has no retained attempt")
    pair = results_path / "pairs" / digest([sid, policy])
    result_path = pair / attempts[-1]["attempt"] / "result.json"
    if not result_path.is_file() or _sha(result_path) != entry.get("latest_result_sha256"):
        raise ValueError("Result SHA mismatch after audit")
    result = _read_json(result_path)
    raw_path = result_path.parent / result["trajectory_path"]
    declared = result.get("trajectory_sha256")
    if (_sha(raw_path) != declared or declared != row["raw_sha256"] or
            declared != attempts[-1].get("raw_sha256")):
        raise ValueError("raw SHA mismatch after audit")
    listed = Path(row["raw_path"])
    if listed.is_absolute() and listed.resolve() != raw_path.resolve():
        raise ValueError("Analysis raw path disagrees with audited pair")
    if result.get("scenario_id") != sid or result.get("policy_id") != policy:
        raise ValueError("Result identity mismatch")
    with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    identity = payload.get("identity", {})
    if identity.get("scenario_id", sid) != sid or identity.get("policy_id", policy) != policy:
        raise ValueError("Raw identity mismatch")
    if payload["summary"]["status"] != row["status"]:
        raise ValueError("Raw status mismatch")
    if scenes[sid]["analysis_class"] != row["analysis_class"]:
        raise ValueError("Raw class mismatch")
    return payload, raw_path


def _segment_rows(payload, scenario_id, policy_id, raw_sha):
    summary = payload["summary"]
    observed = float(summary["observed_until_minute"])
    requested = float(summary["requested_end_minute"])
    rows = []
    for index, segment in enumerate(payload["segments"]):
        start, end = float(segment["start_minute"]), float(segment["end_minute"])
        if not (0 <= start < end <= observed + 1e-6):
            raise ValueError("Segment exceeds observed horizon")
        action, unserved = segment["action"], segment["unserved_kW"]
        connected = segment.get("connected_loads_kW", segment.get("loads_kW"))
        if connected is None or len(unserved) != 4:
            raise ValueError("Missing original service or connected load")
        gts, ecs = action["gt_electric_kW"], action["electric_chiller_cooling_kW"]
        if len(gts) != 3 or len(ecs) != 3:
            raise ValueError("Expected three original GT and EC ports")
        row = dict.fromkeys(SEGMENT_FIELDS, None)
        row.update(scenario_id=scenario_id, policy_id=policy_id, raw_sha256=raw_sha,
                   segment_index=index, start_minute=start, end_minute=end,
                   observation_status="observed", GT_1_kW=gts[0], GT_2_kW=gts[1],
                   GT_3_kW=gts[2], grid_kW=action["grid_import_kW"],
                   diesel_kW=action["diesel_kW"],
                   connected_electric_demand_kW=sum(float(g[0]) for g in connected),
                   hrsg_steam_lbph=action["hrsg_steam_lbph"],
                   boiler_steam_lbph=action["boiler_steam_lbph"],
                   absorption_cooling_kW=action["absorption_cooling_kW"],
                   EC_1_cooling_kW=ecs[0], EC_2_cooling_kW=ecs[1], EC_3_cooling_kW=ecs[2],
                   P0_guarantee_failure=int(bool(segment["classification"]["p0_guarantee_failure"])),
                   P0_temperature_C=segment.get("temperature", {}).get("P0"))
        for i, priority in enumerate(PRIORITIES):
            for k, carrier in enumerate(CARRIERS):
                row[f"{priority}_{carrier}_unserved_kW"] = unserved[i][k]
        rows.append(row)
    if (rows and abs(rows[-1]["end_minute"] - observed) > 1e-5) or (not rows and observed > 1e-5):
        raise ValueError("Observed segment coverage does not reach recorded horizon")
    if observed < requested - 1e-6:
        tail = dict.fromkeys(SEGMENT_FIELDS, None)
        tail.update(scenario_id=scenario_id, policy_id=policy_id, raw_sha256=raw_sha,
                    start_minute=observed, end_minute=requested,
                    observation_status="unknown_tail")
        rows.append(tail)
    return rows


def _event_rows(payload, scenario_id, policy_id, raw_sha):
    observed = float(payload["summary"]["observed_until_minute"])
    ordered = []
    for index, decision in enumerate(payload.get("decisions", [])):
        request = decision.get("requested_command")
        if request is None:
            continue
        if not isinstance(request, dict):
            raise ValueError("Recorded policy request must be an object")
        time = float(decision["decision_time_min"])
        if time > observed + 1e-6:
            continue
        targets = request.get("targets") or {}
        row = dict.fromkeys(EVENT_FIELDS, None)
        row.update(scenario_id=scenario_id, policy_id=policy_id, raw_sha256=raw_sha,
                   event_id=f"decision_request:{index}", time_minute=time,
                   kind="policy_request", source_collection="decisions",
                   origin="policy", cause_class="requested_command",
                   diesel_requested_kW=targets.get("diesel"),
                   request_json=json.dumps(request, ensure_ascii=False, sort_keys=True),
                   request_targets_json=json.dumps(targets, ensure_ascii=False,
                                                   sort_keys=True),
                   planned_dispatch_json="{}", executed_dispatch_json="{}")
        ordered.append((time, 0, index, row))
    for index, event in enumerate(payload.get("events", [])):
        time = float(event["time_minute"])
        if time > observed + 1e-6:
            continue
        cmd = event.get("command") or {}
        request = cmd.get("request") or {}
        targets = request.get("targets") or {}
        planned = cmd.get("dispatch") or event.get("requested_dispatch") or {}
        executed = event.get("executed_dispatch") or {}
        row = dict.fromkeys(EVENT_FIELDS, None)
        row.update(scenario_id=scenario_id, policy_id=policy_id, raw_sha256=raw_sha,
                   event_id=event.get("event_id"), time_minute=time, kind=event["kind"],
                   source_collection="events",
                   command_id=event.get("command_id", cmd.get("id")),
                   origin=event.get("origin"), cause_class=event.get("cause_class"),
                   device=event.get("device"),
                   diesel_requested_kW=targets.get("diesel"),
                   diesel_planned_kW=planned.get("diesel_kW"),
                   diesel_executed_kW=executed.get("diesel_kW"),
                   request_json=json.dumps(request, ensure_ascii=False, sort_keys=True),
                   request_targets_json=json.dumps(targets, ensure_ascii=False, sort_keys=True),
                   planned_dispatch_json=json.dumps(planned, ensure_ascii=False, sort_keys=True),
                   executed_dispatch_json=json.dumps(executed, ensure_ascii=False, sort_keys=True))
        ordered.append((time, 1, index, row))
    ordered.sort(key=lambda item: item[:3])
    return [item[3] for item in ordered]


def _write_csv(path, fields, rows):
    with Path(path).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _steps(rows, field):
    observed = [r for r in rows if r["observation_status"] == "observed"]
    if not observed:
        return [], []
    x = [float(r["start_minute"]) for r in observed] + [float(observed[-1]["end_minute"])]
    y = [float(r[field]) for r in observed]
    return x, y + [y[-1]]


def _draw(csv_path, events_csv_path, png_path, svg_path, *, analysis_class, scenario_id,
          raw_sha, role):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                         "svg.fonttype": "none", "axes.spines.top": False,
                         "axes.spines.right": False, "legend.frameon": False})
    with Path(csv_path).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    with Path(events_csv_path).open(newline="", encoding="utf-8") as stream:
        events = list(csv.DictReader(stream))
    observed = [r for r in rows if r["observation_status"] == "observed"]
    seen_end = float(observed[-1]["end_minute"]) if observed else float(rows[0]["start_minute"])
    horizon = float(rows[-1]["end_minute"])
    fig = plt.figure(figsize=(12, 11.4), constrained_layout=True)
    grid = fig.add_gridspec(4, 3, height_ratios=[1.25, 1.0, 1.15, .9])
    ax_e = fig.add_subplot(grid[0, :])
    ax_h = fig.add_subplot(grid[1, 0])
    ax_c = fig.add_subplot(grid[1, 1:])
    shortage = [fig.add_subplot(grid[2, i]) for i in range(3)]
    ax_event = fig.add_subplot(grid[3, :])
    axes = [ax_e, ax_h, ax_c, *shortage, ax_event]
    source_fields = [("GT_1_kW", "GT1", "#42678b"),
                     ("GT_2_kW", "GT2", "#6992ad"),
                     ("GT_3_kW", "GT3", "#9db5c3"),
                     ("grid_kW", "Grid", "#6da887"),
                     ("diesel_kW", "Diesel", "#bf855d")]
    cumulative = None
    for field, label, color in source_fields:
        x, y = _steps(rows, field)
        if cumulative is None:
            cumulative = [0.0] * len(y)
        upper = [a + b for a, b in zip(cumulative, y)]
        ax_e.fill_between(x, cumulative, upper, step="post", label=label,
                          color=color, alpha=.78)
        cumulative = upper
    ax_e.step(x, cumulative, where="post", label="Total executed supply",
              color="#263849", linewidth=1.25)
    x, y = _steps(rows, "connected_electric_demand_kW")
    ax_e.step(x, y, where="post", label="Connected electric demand", color="#292c31",
              linestyle="--", linewidth=1.35)
    ax_e.set(ylabel="Electric power (kW)", title="Executed electric sources and connected demand")
    ax_e.margins(y=.18)
    ax_e.legend(ncol=4, fontsize=7, loc="upper right")
    for field, label, color in (("hrsg_steam_lbph", "HRSG", "#9874a1"),
                                ("boiler_steam_lbph", "Boiler", "#c295aa")):
        x, y = _steps(rows, field)
        ax_h.step(x, y, where="post", label=label, color=color)
    ax_h.set(ylabel="Steam flow (lb/h)", title="Steam production")
    ax_h.legend(fontsize=7)
    for field, label, color in (("absorption_cooling_kW", "Absorption", "#607da8"),
                                ("EC_1_cooling_kW", "EC1", "#70adc1"),
                                ("EC_2_cooling_kW", "EC2", "#98c9cf"),
                                ("EC_3_cooling_kW", "EC3", "#bfd8d8")):
        x, y = _steps(rows, field)
        ax_c.step(x, y, where="post", label=label, color=color)
    ax_c.set(ylabel="Cooling output (kW)", title="Executed cooling converters")
    ax_c.legend(ncol=4, fontsize=7)
    priority_colors = ("#b14d4a", "#d39952", "#7394b6", "#8f9da5")
    for ax, carrier, title in zip(shortage, CARRIERS,
                                  ("Electric shortage", "Heat shortage", "Cold shortage")):
        for priority, color in zip(PRIORITIES, priority_colors):
            x, y = _steps(rows, f"{priority}_{carrier}_unserved_kW")
            ax.step(x, y, where="post", label=priority, color=color, linewidth=1.2)
        ax.set(ylabel="Unserved (kW)", title=title)
        highest = max(float(r[f"{priority}_{carrier}_unserved_kW"])
                      for r in observed for priority in PRIORITIES) if observed else 0.0
        ax.set_ylim(0, highest * 1.1 if highest > 0 else 1)
    shortage[0].legend(ncol=4, fontsize=7)
    lanes = {"policy_request": 0, "command_generated": 1,
             "command_arrived": 2, "command_executed": 3}
    for event in events:
        kind = event["kind"]
        if kind in lanes:
            lane = lanes[kind]
            color = "#a47751" if kind == "policy_request" else "#5c789a"
        elif kind.startswith("exogenous_") or kind == "shock_changed":
            lane, color = 4, "#806fab"
        elif kind in ("protection_trip", "no_export_interlock", "minimum_output_interlock"):
            lane, color = 5, "#b4514c"
        else:
            continue
        ax_event.scatter(float(event["time_minute"]), lane, color=color, s=18,
                         marker="|", linewidths=1.3)
    for r in observed:
        if r["P0_guarantee_failure"] == "1":
            ax_event.hlines(6, float(r["start_minute"]), float(r["end_minute"]),
                            color="#c25450", linewidth=6, alpha=.58)
    ax_event.set(yticks=range(7), yticklabels=["Request", "Generated", "Arrive",
                                             "Execute", "Exogenous", "Protection",
                                             "P0 failed"],
                 xlabel="Time since episode start (min)",
                 title="Observed request, command and event timeline; red block = P0 service failure")
    ax_event.set_ylim(-.6, 6.65)
    for ax in axes:
        ax.set_xlim(0, horizon)
        ax.grid(axis="y", color="#e8ecef", linewidth=.5)
        if seen_end < horizon - 1e-6:
            ax.axvspan(seen_end, horizon, facecolor="#e4e4e4", edgecolor="#888",
                       hatch="///", linewidth=0, alpha=.7)
            ax.axvline(seen_end, color="#777", linestyle=":", linewidth=1)
    if seen_end < horizon - 1e-6:
        ax_e.text((seen_end + horizon) / 2, .5, "Unknown tail",
                  transform=ax_e.get_xaxis_transform(), rotation=90,
                  ha="center", va="center", color="#444", fontsize=8,
                  bbox={"facecolor": "white", "edgecolor": "none", "alpha": .8})
    fig.suptitle(f"{analysis_class} | {scenario_id} | {role}\n"
                 f"Observed raw SHA-256 {raw_sha[:16]}...; designed scenario, not a clinical rate",
                 fontsize=11)
    fig.savefig(png_path, dpi=180)
    fig.savefig(svg_path)
    plt.close(fig)


def build_figures(manifest, plan, results, audit, analysis, out_dir):
    """Create deterministic source CSVs and PNG/SVG figures from audited raw."""
    (scenes, ledger, rows, results_path, audit_path, plan_path,
     manifest_path, analysis_file, summary_file) = _load_batch(
         manifest, plan, results, audit, analysis)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    selected = _select(rows)
    if not selected:
        raise ValueError("No four-scenario representative rows")
    figures = []
    for row, role, reason in selected:
        payload, raw_path = _read_verified_raw(row, scenes, ledger, results_path)
        sha = _sha(raw_path)
        sid, policy = row["scenario_id"], row["policy_id"]
        segments = _segment_rows(payload, sid, policy, sha)
        events = _event_rows(payload, sid, policy, sha)
        stem = f"{row['analysis_class']}_{role}_{sid}_{policy}"
        csv_path = out_dir / f"{stem}.csv"
        events_path = out_dir / f"{stem}.events.csv"
        png_path = out_dir / f"{stem}.png"
        svg_path = out_dir / f"{stem}.svg"
        _write_csv(csv_path, SEGMENT_FIELDS, segments)
        _write_csv(events_path, EVENT_FIELDS, events)
        _draw(csv_path, events_path, png_path, svg_path,
              analysis_class=row["analysis_class"], scenario_id=sid,
              raw_sha=sha, role=role)
        figures.append(dict(analysis_class=row["analysis_class"], role=role,
                            selection_reason=reason, scenario_id=sid, policy_id=policy,
                            root_family_id=row["root_family_id"], status=row["status"],
                            observed_until_minute=row["observed_until_minute"],
                            full_observation_complete=_flag(row["full_full_horizon"]),
                            raw_path=str(raw_path), raw_sha256=sha,
                            csv_path=str(csv_path), events_csv_path=str(events_path),
                            png_path=str(png_path), svg_path=str(svg_path),
                            audit_sha256=_sha(audit_path), plan_sha256=_sha(plan_path),
                            manifest_sha256=_sha(manifest_path),
                            analysis_csv_sha256=_sha(analysis_file),
                            analysis_summary_sha256=_sha(summary_file)))
    _write_csv(out_dir / "FIGURE_INDEX.csv", tuple(figures[0]), figures)
    return {"figures": figures, "index_path": str(out_dir / "FIGURE_INDEX.csv")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "plan", "results", "audit", "analysis", "out-dir"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    result = build_figures(args.manifest, args.plan, args.results, args.audit,
                           args.analysis, args.out_dir)
    print(json.dumps({"figure_count": len(result["figures"]),
                      "index_path": result["index_path"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
