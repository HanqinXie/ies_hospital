"""Version-bound v9 raw mechanism panels and discrete measured boundary points.

Python/matplotlib only. Selection is retrospective; nothing here controls an
environment. Mechanism inputs must be bound to a passing matched raw audit.
Boundary CSV hashes and declared raw hashes remain distinct evidence layers.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import re
import textwrap

from tools.four_scenarios.figures import (
    EVENT_FIELDS, SEGMENT_FIELDS, _event_rows, _read_json, _read_verified_raw,
    _segment_rows, _sha,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "state_dispatch.v9.audited_raw_figures.v1"
CARRIERS = ("e", "h", "c")
COLORS = ("#315d86", "#b86a3b", "#60856e", "#95719c")
CURVE_FIELDS = (
    "figure_id", "analysis_class", "root_family_id", *SEGMENT_FIELDS,
    "GT_total_kW", *(f"total_{c}_demand_kW" for c in CARRIERS),
    *(f"P0_{c}_demand_kW" for c in CARRIERS), *(f"P3_{c}_demand_kW" for c in CARRIERS),
    *(f"connected_{c}_demand_kW" for c in CARRIERS),
    *(f"stress_ratio_GT_{i}" for i in (1, 2, 3)),
    *(f"overload_minutes_GT_{i}" for i in (1, 2, 3)),
    "mode", "selected_weight_USD_per_kWh", "known_response_wait_minutes",
    "command_id", "cost_USD_step",
)
EVENT_OUTPUT_FIELDS = ("figure_id", "analysis_class", "root_family_id", *EVENT_FIELDS,
                       "generated_minute", "arrival_minute", "superseded_by", "mode", "details_json")
BOUNDARY_FIELDS = ("figure_id", "scenario_id", "policy_id", "raw_sha256", "source_row",
                   "x", "y", "complete", "plotted", "source_row_json")


def _path(value, base):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _number(value, name):
    try:
        result = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid numeric " + name) from error
    if not math.isfinite(result):
        raise ValueError("Nonfinite " + name)
    return result


def _policies(case):
    policies = case.get("policy_ids")
    if (not isinstance(policies, list) or not 1 <= len(policies) <= 4 or
            any(not isinstance(p, str) or not p for p in policies) or len(set(policies)) != len(policies)):
        raise ValueError("Figure needs one to four distinct policy_ids")
    return policies


def _write_csv(path, fields, rows):
    with path.open("x", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _diagnostics(payload):
    records = payload.get("policy_diagnostics") or [d.get("policy_diagnostic", {})
                                                   for d in payload.get("decisions", [])]
    return sorted((d for d in records if isinstance(d, dict) and "decision_time_min" in d),
                  key=lambda d: float(d["decision_time_min"]))


def _prepare_mechanism(case, base):
    policies = _policies(case)
    paths = {key: _path(case[key], base) for key in ("manifest", "plan", "results", "audit")}
    manifest, plan, audit = (_read_json(paths[key]) for key in ("manifest", "plan", "audit"))
    if (not audit.get("passed") or audit.get("matched_initial_state_and_stream") is not True or
            plan.get("final_blind_opened") is True or audit.get("final_blind_opened") is True):
        raise ValueError("A passing matched raw audit with sealed final backgrounds is required")
    if (plan.get("manifest_sha256") != _sha(paths["manifest"]) or
            audit.get("manifest_sha256") != _sha(paths["manifest"]) or
            audit.get("plan_sha256") != _sha(paths["plan"])):
        raise ValueError("Manifest/plan/audit SHA binding differs")
    if (audit.get("registered_pair_count") != plan.get("registered_pair_count") or
            audit.get("terminal_pair_count") != plan.get("registered_pair_count")):
        raise ValueError("Audited registered denominator is incomplete")
    scenes = {s["id"]: s for s in manifest["scenarios"]}
    sid = case["scenario_id"]
    if sid not in scenes or sid not in plan["scenario_ids"] or scenes[sid].get("is_final_blind"):
        raise ValueError("Selected scene absent or sealed")
    if not set(policies) <= {p["policy_id"] for p in plan["policy_specs"]}:
        raise ValueError("Selected policy outside registered plan")
    start, end = _number(case.get("view_start", 0), "view_start"), _number(case.get("view_end", 600), "view_end")
    if not 0 <= start < end:
        raise ValueError("Invalid viewed observation interval")
    ledger = {(r["scenario_id"], r["policy_id"]): r for r in audit["pair_ledger"]}
    rows, events, sources, identities = [], [], [], []
    scene = scenes[sid]
    extra = {"figure_id": case["figure_id"], "analysis_class": scene["analysis_class"],
             "root_family_id": scene["root_family_id"]}
    for pid in policies:
        entry = ledger.get((sid, pid))
        if not entry or not entry.get("terminal") or not entry.get("attempts"):
            raise ValueError("Missing audited policy trajectory")
        latest = entry["attempts"][-1]
        analysis_row = {"scenario_id": sid, "policy_id": pid, "analysis_class": scene["analysis_class"],
                        "status": entry["status"], "raw_sha256": latest["raw_sha256"], "raw_path": ""}
        payload, raw = _read_verified_raw(analysis_row, scenes, ledger, paths["results"])
        summary = payload["summary"]
        observed, requested = float(summary["observed_until_minute"]), float(summary["requested_end_minute"])
        if entry["status"] != "completed" or observed < requested - 1e-6 or end > observed + 1e-6:
            raise ValueError("Figure requires a complete observed trajectory; unknown tails are not zero")
        identity = payload.get("identity", {})
        if identity.get("is_final_blind") is True or identity.get("group_id", scene["root_family_id"]) != scene["root_family_id"]:
            raise ValueError("Raw source is sealed or has a different root")
        identities.append(identity)
        segments = _segment_rows(payload, sid, pid, latest["raw_sha256"])
        cursor = 0.0
        diagnostics = _diagnostics(payload)
        for row, segment in zip(segments, payload["segments"]):
            if row["observation_status"] != "observed" or abs(row["start_minute"] - cursor) > 1e-6:
                raise ValueError("Original observed segments must be contiguous")
            cursor = row["end_minute"]
            loads, connected = segment.get("loads_kW"), segment.get("connected_loads_kW")
            if loads is None or connected is None:
                raise ValueError("Original demand and connected demand are separately required")
            if len(loads) != 4 or len(connected) != 4:
                raise ValueError("Expected original four-priority demand matrix")
            known = [d for d in diagnostics if float(d["decision_time_min"]) <= row["start_minute"] + 1e-9]
            current = known[-1] if known else {}
            mode = current.get("mode", "baseline" if pid in ("priority_milp", "secure_milp") else "unrecorded")
            enriched = {**extra, **row, "GT_total_kW": sum(row[f"GT_{i}_kW"] for i in (1, 2, 3)),
                        "mode": mode, "selected_weight_USD_per_kWh": current.get("selected_weight_USD_per_kWh"),
                        "known_response_wait_minutes": current.get("current_known_response_wait_minutes"),
                        "command_id": segment.get("command_id"), "cost_USD_step": segment.get("cost_USD_step")}
            for k, carrier in enumerate(CARRIERS):
                enriched[f"total_{carrier}_demand_kW"] = sum(float(g[k]) for g in loads)
                enriched[f"connected_{carrier}_demand_kW"] = sum(float(g[k]) for g in connected)
                enriched[f"P0_{carrier}_demand_kW"] = loads[0][k]
                enriched[f"P3_{carrier}_demand_kW"] = loads[3][k]
            for i in (1, 2, 3):
                enriched[f"stress_ratio_GT_{i}"] = segment.get("protection_stress", {}).get(f"GT_{i}")
                clocks = segment.get("post_state", {}).get("overload_minutes")
                enriched[f"overload_minutes_GT_{i}"] = clocks[i-1] if clocks is not None else None
            rows.append(enriched)
        if abs(cursor - observed) > 1e-6 or len(segments) != len(payload["segments"]):
            raise ValueError("Segment coverage differs from observed horizon")
        original = {e.get("event_id"): e for e in payload.get("events", [])}
        event_rows = _event_rows(payload, sid, pid, latest["raw_sha256"])
        for event in event_rows:
            source = original.get(event["event_id"], {}) if event["source_collection"] == "events" else {}
            cmd = source.get("command", {})
            events.append({**extra, **event, "generated_minute": source.get("generated_minute", cmd.get("generated_minute")),
                           "arrival_minute": source.get("arrival_minute", cmd.get("arrival_minute")),
                           "superseded_by": source.get("by"), "mode": None,
                           "details_json": json.dumps(source, ensure_ascii=False, sort_keys=True)})
        previous_mode = None
        for index, diagnostic in enumerate(diagnostics):
            mode = diagnostic.get("mode")
            if mode is None or mode == previous_mode:
                continue
            event = dict.fromkeys(EVENT_OUTPUT_FIELDS, None)
            event.update(extra, scenario_id=sid, policy_id=pid, raw_sha256=latest["raw_sha256"],
                         event_id=f"mode:{index}", time_minute=diagnostic["decision_time_min"], kind="mode_changed",
                         source_collection="policy_diagnostics", origin="policy", mode=mode,
                         details_json=json.dumps(diagnostic, ensure_ascii=False, sort_keys=True))
            events.append(event)
            previous_mode = mode
        sources.append({"scenario_id": sid, "policy_id": pid, "raw_path": str(raw),
                        "raw_sha256": latest["raw_sha256"], "segment_count": len(segments),
                        "observed_until_minute": observed, "requested_end_minute": requested})
    for key in ("base_state_id", "exogenous_stream_id"):
        if len({x[key] for x in identities if x.get(key) is not None}) > 1:
            raise ValueError("Selected policies have unmatched initial state or exogenous stream")
    return {"case": {**case, "view_start": start, "view_end": end}, "rows": rows, "events": events,
            "raw_sources": sources, "bindings": {f"{k}_sha256": _sha(paths[k]) for k in ("manifest", "plan", "audit")}}


def _boolean(value):
    if value is True or str(value).lower() in ("true", "1", "1.0"):
        return True
    if value is False or str(value).lower() in ("false", "0", "0.0"):
        return False
    raise ValueError("Boundary complete field must be explicit true/false")


def _prepare_boundary(case, base):
    policies = _policies(case)
    source = _path(case["csv"], base)
    with source.open(newline="", encoding="utf-8-sig") as stream:
        input_rows = list(csv.DictReader(stream))
    required = {"scenario_id", "policy_id", "raw_sha256", case["x_field"], case["y_field"], case["complete_field"]}
    if not input_rows or not required <= set(input_rows[0]):
        raise ValueError("Boundary CSV requires measured coordinates, completeness and source identities")
    rows, keys = [], set()
    for index, row in enumerate(input_rows):
        if row["policy_id"] not in policies:
            continue
        if row.get("is_final_blind") not in (None, "") and _boolean(row["is_final_blind"]):
            raise ValueError("Boundary CSV contains a sealed final background")
        key = row["scenario_id"], row["policy_id"]
        if key in keys:
            raise ValueError("Duplicate measured scenario/policy point")
        keys.add(key)
        complete = _boolean(row[case["complete_field"]])
        x = _number(row[case["x_field"]], "boundary coordinate")
        y = _number(row[case["y_field"]], "complete boundary result") if complete else None
        raw_sha = row["raw_sha256"]
        if (raw_sha and not re.fullmatch(r"[0-9a-f]{64}", raw_sha)) or (complete and not raw_sha):
            raise ValueError("Measured point lacks declared raw SHA256")
        rows.append({"figure_id": case["figure_id"], "scenario_id": key[0], "policy_id": key[1],
                     "raw_sha256": raw_sha, "source_row": index, "x": x, "y": y,
                     "complete": complete, "plotted": complete,
                     "source_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True)})
    if not rows or not any(row["plotted"] for row in rows):
        raise ValueError("Boundary selection has no complete measured point")
    return {"case": case, "rows": rows, "events": [], "raw_sources": [],
            "bindings": {"source_csv_path": str(source), "source_csv_sha256": _sha(source)},
            "interpolation": False, "n_measured_plotted_points": sum(row["plotted"] for row in rows),
            "boundary_raw_hash_semantics": "CSV-declared raw hashes; raw files not reverified by boundary plotting"}


def _plot_setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["Arial", "Microsoft YaHei", "DejaVu Sans"],
                         "font.size": 8, "svg.fonttype": "none", "pdf.fonttype": 42,
                         "legend.frameon": False, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.linewidth": .7})
    return plt


def _step(ax, rows, field, *, label, color, linestyle="-"):
    x = [float(row["start_minute"]) for row in rows] + [float(rows[-1]["end_minute"])]
    y = [float(row[field]) if row.get(field) is not None else float("nan") for row in rows]
    ax.step(x, y + [y[-1]], where="post", color=color, linestyle=linestyle, linewidth=1.3, label=label)


def _draw_mechanism(prepared, png, svg, dpi):
    plt = _plot_setup()
    case = prepared["case"]
    policies = case["policy_ids"]
    figure, axes = plt.subplots(8, len(policies), figsize=(max(7.2, len(policies)*5.2), 15.5),
                                sharex=True, sharey="row", squeeze=False, constrained_layout=True)
    carrier_names = ("Electric", "Heat", "Cooling")
    ylabel = ("Current demand (kW)", "Actual source ports (kW)", "Actual GT output (kW)",
              "Actual steam (lb/h)", "P0 unserved (kW)", "P3 unserved (kW)",
              "Residual stress / nameplate", "Mode / observed events")
    mode_codes = {"prepare": 0, "emergency": 1, "recovery": 2, "baseline": 3, "unrecorded": 4}
    for column, policy in enumerate(policies):
        rows = [r for r in prepared["rows"] if r["policy_id"] == policy]
        for k, (carrier, label) in enumerate(zip(CARRIERS, carrier_names)):
            _step(axes[0, column], rows, f"total_{carrier}_demand_kW", label=label, color=COLORS[k])
            _step(axes[4, column], rows, f"P0_{carrier}_unserved_kW", label=label, color=COLORS[k])
            _step(axes[5, column], rows, f"P3_{carrier}_unserved_kW", label=label, color=COLORS[k])
        for field, label, color in (("GT_total_kW", "GT total", COLORS[0]),
                                    ("grid_kW", "Grid", "#777777"), ("diesel_kW", "Diesel", COLORS[3])):
            _step(axes[1, column], rows, field, label=label, color=color)
        for i, color in zip((1, 2, 3), COLORS):
            _step(axes[2, column], rows, f"GT_{i}_kW", label=f"GT{i}", color=color)
            _step(axes[6, column], rows, f"stress_ratio_GT_{i}", label=f"GT{i}", color=color)
        _step(axes[3, column], rows, "hrsg_steam_lbph", label="HRSG", color=COLORS[0])
        _step(axes[3, column], rows, "boiler_steam_lbph", label="Boiler", color=COLORS[1])
        mode_rows = [{**r, "mode_code": mode_codes.get(r["mode"], 4)} for r in rows]
        _step(axes[7, column], mode_rows, "mode_code", label="Selected mode", color=COLORS[0])
        lane = axes[7, column]
        for kind, y, marker, color in (("command_arrived", 5, "v", COLORS[0]),
                                        ("protection_trip", 6, "X", "#a93546"),
                                        ("no_export_interlock", 7, "D", COLORS[1]),
                                        ("minimum_output_interlock", 7, "D", COLORS[1])):
            selected = [e for e in prepared["events"] if e["policy_id"] == policy and e["kind"] == kind
                        and case["view_start"] <= float(e["time_minute"]) <= case["view_end"]]
            if selected:
                lane.scatter([float(e["time_minute"]) for e in selected], [y]*len(selected),
                             marker=marker, color=color, s=23, zorder=3)
        lane.set_yticks(range(8), ("Prepare", "Emergency", "Recovery", "Baseline", "Unrecorded",
                                  "Cmd arrived", "GT protection", "Interlock"), fontsize=7)
        lane.set_ylim(-.4, 7.5)
        axes[0, column].set_title(textwrap.fill(policy, width=30), fontsize=10, fontweight="bold")
        for row_index in range(8):
            ax = axes[row_index, column]
            ax.set_xlim(case["view_start"], case["view_end"])
            ax.grid(axis="y", color="#e5e9ee", linewidth=.5)
            if column == 0:
                ax.set_ylabel(ylabel[row_index])
            if row_index < 7:
                ax.legend(loc="upper right", ncol=3, fontsize=6.5)
        lane.set_xlabel("Minutes since episode start")
    # Fix the lower bound only after all shared-axis policy traces exist;
    # doing so on the first column would freeze and clip later policy data.
    for row_index in range(7):
        axes[row_index, 0].set_ylim(bottom=0)
    figure.suptitle(case["title"] + "\nSaved raw interval means; current-state modes and actual arrival/protection events",
                   fontsize=12)
    figure.savefig(png, dpi=dpi)
    figure.savefig(svg)
    plt.close(figure)


def _draw_boundary(prepared, png, svg, dpi):
    plt = _plot_setup()
    case = prepared["case"]
    figure, ax = plt.subplots(figsize=(7.2, 4.5), constrained_layout=True)
    for policy, color in zip(case["policy_ids"], COLORS):
        rows = [r for r in prepared["rows"] if r["policy_id"] == policy and r["plotted"]]
        if rows:
            artist = ax.scatter([r["x"] for r in rows], [r["y"] for r in rows], label=policy,
                                color=color, edgecolor="white", linewidth=.5, s=48)
            artist.set_gid("scatter_" + policy)
    unknown = sum(not r["plotted"] for r in prepared["rows"])
    ax.set(title=case["title"], xlabel=case.get("x_label", case["x_field"]),
           ylabel=case.get("y_label", case["y_field"]))
    ax.text(.99, .99, f"Measured points only; no interpolation\n{unknown} unknown result(s) not plotted",
            ha="right", va="top", transform=ax.transAxes, fontsize=7, color="#555555")
    ax.grid(color="#e5e9ee", linewidth=.5)
    ax.legend(loc="best", fontsize=7)
    figure.savefig(png, dpi=dpi)
    figure.savefig(svg)
    plt.close(figure)


def build_figures(selection, out_dir, *, base_dir=ROOT, dpi=220):
    """Validate every source first; write a new traceable figure set once.

    Mechanism selection keys: figure_id,title,manifest,plan,results,audit,
    scenario_id,policy_ids,view_start,view_end. Boundary keys: kind=boundary,
    figure_id,title,csv,x_field,y_field,complete_field,policy_ids; axis labels
    are optional. All relative input paths resolve against base_dir.
    """
    if not isinstance(selection, list) or not selection:
        raise ValueError("Selection must contain figure definitions")
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 60 <= dpi <= 600:
        raise ValueError("PNG dpi must be an integer in [60,600]")
    base, out = Path(base_dir).resolve(), Path(out_dir).resolve()
    names, prepared = set(), []
    for case in selection:
        if not isinstance(case, dict):
            raise ValueError("Each figure selection must be an object")
        name = case.get("figure_id")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in names:
            raise ValueError("Unsafe or duplicate figure_id")
        if not isinstance(case.get("title"), str) or not case["title"].strip():
            raise ValueError("A figure title is required")
        names.add(name)
        kind = case.get("kind", "mechanism")
        if kind not in ("mechanism", "boundary"):
            raise ValueError("Unknown figure kind")
        prepared.append(_prepare_boundary(case, base) if kind == "boundary" else _prepare_mechanism(case, base))
    for name in names:
        for suffix in (".csv", "_events.csv", ".png", ".svg"):
            if (out / (name + suffix)).exists():
                raise FileExistsError("Figure outputs already exist")
    if (out / "FIGURE_INDEX.json").exists() or (out / "RAW_SHA256_INDEX.json").exists():
        raise FileExistsError("Figure index already exists")
    out.mkdir(parents=True, exist_ok=True)
    index = {"schema": SCHEMA, "figures": [], "new_trajectory_attempts": 0,
             "final_blind_opened": False, "figure_source_sha256": _sha(Path(__file__))}
    raw_index = {"schema": SCHEMA + ".raw_index", "raw_sources": [], "boundary_csv_sources": []}
    for item in prepared:
        case = item["case"]
        name = case["figure_id"]
        csv_path, events_path, png, svg = (out / (name + suffix) for suffix in (".csv", "_events.csv", ".png", ".svg"))
        boundary = case.get("kind") == "boundary"
        _write_csv(csv_path, BOUNDARY_FIELDS if boundary else CURVE_FIELDS, item["rows"])
        _write_csv(events_path, EVENT_OUTPUT_FIELDS, item["events"])
        (_draw_boundary if boundary else _draw_mechanism)(item, png, svg, dpi)
        result = {**case, **item["bindings"], "raw_sources": item["raw_sources"],
                  "curves_csv": str(csv_path), "events_csv": str(events_path), "png": str(png), "svg": str(svg),
                  "curves_csv_sha256": _sha(csv_path), "events_csv_sha256": _sha(events_path),
                  "png_sha256": _sha(png), "svg_sha256": _sha(svg)}
        if boundary:
            result.update({k: item[k] for k in ("interpolation", "n_measured_plotted_points", "boundary_raw_hash_semantics")})
            raw_index["boundary_csv_sources"].append({"figure_id": name, **item["bindings"],
                "declared_row_raw_sha256": sorted({row["raw_sha256"] for row in item["rows"] if row["raw_sha256"]}),
                "raw_files_reverified": False})
        else:
            raw_index["raw_sources"].extend({"figure_id": name, **source} for source in item["raw_sources"])
        index["figures"].append(result)
    _write_json(out / "RAW_SHA256_INDEX.json", raw_index)
    _write_json(out / "FIGURE_INDEX.json", index)
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--base-dir", type=Path, default=ROOT)
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args(argv)
    result = build_figures(_read_json(args.selection), args.out_dir, base_dir=args.base_dir, dpi=args.dpi)
    print(json.dumps({"figures": len(result["figures"]), "index": str(args.out_dir / "FIGURE_INDEX.json"),
                      "new_trajectory_attempts": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
