"""Audited, paired mechanism figures from saved four-scenario trajectories.

Selection and drawing are retrospective.  No scenario identity or future event
is passed to an online controller.  Input runs are read only; output is explicit.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import re

from tools.four_scenarios.figures import (
    EVENT_FIELDS,
    SEGMENT_FIELDS,
    _event_rows,
    _read_json,
    _read_verified_raw,
    _segment_rows,
    _sha,
)


POLICIES = ("secure_milp", "priority_milp")
PLOT_FIELDS = {
    "electric_bridge": (
        ("grid_kW", "Executed grid import (kW)"),
        ("GT_total_kW", "Executed GT electric output (kW)"),
        ("diesel_kW", "Executed diesel output (kW)"),
        ("P0_e_unserved_kW", "P0 electric shortage (kW)"),
        ("P3_e_unserved_kW", "P3 electric shortage (kW)"),
    ),
    "heat_response": (
        ("hrsg_steam_lbph", "Executed HRSG steam (lb/h)"),
        ("boiler_steam_lbph", "Executed boiler steam (lb/h)"),
        ("P0_h_unserved_kW", "P0 heat shortage (kW)"),
        ("P3_h_unserved_kW", "P3 heat shortage (kW)"),
    ),
    "gt_trip": (
        ("GT_2_kW", "Executed GT_2 output (kW)"),
        ("P0_e_unserved_kW", "P0 electric shortage (kW)"),
        ("P3_e_unserved_kW", "P3 electric shortage (kW)"),
    ),
    "compound": (
        ("GT_total_kW", "Executed GT electric output (kW)"),
        ("P3_e_unserved_kW", "P3 electric shortage (kW)"),
        ("P3_c_unserved_kW", "P3 cooling shortage (kW)"),
    ),
}
SEGMENT_OUTPUT_FIELDS = ("figure_id", "analysis_class", "root_family_id",
                         "GT_total_kW", *SEGMENT_FIELDS)
EVENT_OUTPUT_FIELDS = ("figure_id", "analysis_class", "root_family_id", *EVENT_FIELDS)
COVERAGE_CLASSES = ("L2", "L4a", "L4b")
COVERAGE_MODES = ("e", "h", "c", "mixed")
COVERAGE_LEVELS = ("ordinary", "elevated", "boundary_candidate")


def _optional_number(value):
    if value is None or value == "":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite coverage evidence")
    return number


def _optional_bool(value):
    if value is None or value == "":
        return None
    if value is True or str(value).lower() in ("true", "1", "1.0"):
        return True
    if value is False or str(value).lower() in ("false", "0", "0.0"):
        return False
    raise ValueError("Invalid coverage Boolean")


def coverage_tables(manifest: dict, analysis_rows: list[dict]) -> tuple[list, list, list]:
    """Pure discrete evidence tables, separately for L2, L4a and L4b.

    A design cell has its own observed denominator (normally three roots).
    Missing cells and incomplete tails remain unknown; carriers are not added.
    """
    analysis = {}
    for row in analysis_rows:
        key = (row["scenario_id"], row["policy_id"])
        if key in analysis:
            raise ValueError("Duplicate coverage analysis pair")
        analysis[key] = row
    detail = []
    for scene in manifest["scenarios"]:
        if scene.get("design_axis") != "main" or scene.get("analysis_class") not in COVERAGE_CLASSES:
            continue
        quarter = int(scene["season_quarter"])
        mode = scene.get("shock_mode")
        level_field = str(scene.get("design_level", ""))
        if (quarter not in (1, 2, 3, 4) or mode not in COVERAGE_MODES or
                level_field not in {mode + "|" + level for level in COVERAGE_LEVELS} or
                scene.get("is_final_blind") is not False):
            raise ValueError("Main shock scene outside registered coverage grid")
        level = level_field.split("|", 1)[1]
        shock = scene.get("shock", {})
        for policy in POLICIES:
            key = (scene["id"], policy)
            if key not in analysis:
                raise ValueError("Missing paired coverage analysis row")
            row = analysis[key]
            complete = _optional_bool(row.get("full_full_horizon"))
            failed = _optional_bool(row.get("full_P0_failure"))
            if complete is None or (complete and failed is None):
                raise ValueError("Incomplete coverage full-horizon status")
            ens = {carrier: _optional_number(row.get(f"full_P0_{carrier}_ENS_kWh"))
                   for carrier in "ehc"}
            if complete and any(value is None for value in ens.values()):
                raise ValueError("Complete coverage row lacks carrier ENS")
            trips = _optional_number(row.get("GT_protection_trip_count_observed"))
            if trips is None or trips < 0 or int(trips) != trips:
                raise ValueError("Coverage GT trip count absent or invalid")
            sha = row.get("raw_sha256")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise ValueError("Coverage row lacks exact raw SHA")
            detail.append({"scenario_id": scene["id"], "root_family_id":
                           scene["root_family_id"],
                           "source_day_index": scene.get("source_day_index"),
                           "analysis_class": scene["analysis_class"],
                           "season_quarter": quarter, "shock_mode": mode,
                           "intensity_level": level, "policy_id": policy,
                           "shock_e": _optional_number(shock.get("e")),
                           "shock_h": _optional_number(shock.get("h")),
                           "shock_c": _optional_number(shock.get("c")),
                           "full_complete": complete, "p0_failed_observed": failed,
                           "P0_e_ENS_kWh": ens["e"] if complete else None,
                           "P0_h_ENS_kWh": ens["h"] if complete else None,
                           "P0_c_ENS_kWh": ens["c"] if complete else None,
                           "GT_protection_trip_count": int(trips),
                           "raw_sha256": sha})
    grouped = {}
    for row in detail:
        key = tuple(row[name] for name in ("analysis_class", "season_quarter",
                                         "shock_mode", "intensity_level", "policy_id"))
        grouped.setdefault(key, []).append(row)
    cells = []
    for category in COVERAGE_CLASSES:
        for quarter in (1, 2, 3, 4):
            for mode in COVERAGE_MODES:
                for level in COVERAGE_LEVELS:
                    for policy in POLICIES:
                        members = grouped.get((category, quarter, mode, level, policy), [])
                        complete = [row for row in members if row["full_complete"]]
                        cell = {"analysis_class": category,
                                "season_quarter": quarter, "shock_mode": mode,
                                "intensity_level": level, "policy_id": policy,
                                "n_registered_trajectories": len(members),
                                "n_unique_roots": len({row["root_family_id"] for row in members}),
                                "n_full_complete": len(complete),
                                "n_unknown_tail": len(members) - len(complete),
                                "p0_observed_failed_count": sum(
                                    row["p0_failed_observed"] is True for row in members),
                                "p0_complete_no_failure_count": sum(
                                    row["p0_failed_observed"] is False for row in complete),
                                "gt_trip_trajectory_count": sum(
                                    row["GT_protection_trip_count"] > 0 for row in members),
                                "gt_trip_event_count": sum(
                                    row["GT_protection_trip_count"] for row in members),
                                "p0_max_single_carrier_ENS_kWh": max(
                                    (row[f"P0_{carrier}_ENS_kWh"] for row in complete
                                     for carrier in "ehc"), default=None)}
                        for carrier in "ehc":
                            values = [row[f"P0_{carrier}_ENS_kWh"] for row in complete]
                            cell[f"P0_{carrier}_ENS_min_kWh"] = min(values, default=None)
                            cell[f"P0_{carrier}_ENS_max_kWh"] = max(values, default=None)
                        cells.append(cell)
    matched = {}
    for row in detail:
        key = tuple(row[name] for name in ("root_family_id", "analysis_class",
                                         "season_quarter", "shock_mode", "policy_id"))
        levels = matched.setdefault(key, {})
        if row["intensity_level"] in levels:
            raise ValueError("Duplicate root/class/policy shock intensity")
        levels[row["intensity_level"]] = row
    reversals = []
    for key, levels in sorted(matched.items()):
        root_id, category, quarter, mode, policy = key
        all_levels = all(level in levels for level in COVERAGE_LEVELS)
        complete = all_levels and all(levels[level]["full_complete"]
                                       for level in COVERAGE_LEVELS)
        metrics = {"P0_failure": "p0_failed_observed",
                   "P0_e_ENS_kWh": "P0_e_ENS_kWh",
                   "P0_h_ENS_kWh": "P0_h_ENS_kWh",
                   "P0_c_ENS_kWh": "P0_c_ENS_kWh",
                   "GT_protection_trip_count": "GT_protection_trip_count"}
        for metric, field in metrics.items():
            values = ([float(levels[level][field]) for level in COVERAGE_LEVELS]
                      if complete else None)
            reversal = (values[1] < min(values[0], values[2]) - 1e-9 or
                        values[1] > max(values[0], values[2]) + 1e-9
                        if values is not None else None)
            reversals.append({"root_family_id": root_id, "analysis_class": category,
                              "season_quarter": quarter, "shock_mode": mode,
                              "policy_id": policy, "metric": metric,
                              "ordinary": values[0] if values is not None else None,
                              "elevated": values[1] if values is not None else None,
                              "boundary_candidate": values[2] if values is not None else None,
                              "complete_three_levels": bool(complete),
                              "nonmonotonic": reversal,
                              "exclusion_reason": None if complete else
                                  ("unknown_tail" if all_levels else "missing_level")})
    return detail, cells, reversals


def _draw_coverage_class(category: str, cells: list[dict], reversals: list[dict],
                         png: Path, svg: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                         "svg.fonttype": "none", "legend.frameon": False,
                         "axes.spines.top": False, "axes.spines.right": False})
    lookup = {(row["season_quarter"], row["shock_mode"],
               row["intensity_level"], row["policy_id"]): row
              for row in cells if row["analysis_class"] == category}
    reversed_groups = {(row["season_quarter"], row["shock_mode"])
                       for row in reversals if row["analysis_class"] == category and
                       row["nonmonotonic"] is True}
    fig, axes = plt.subplots(4, 4, figsize=(17.0, 12.0))
    mode_title = {"e": "Electric", "h": "Heat", "c": "Cooling", "mixed": "Mixed"}
    for q in (1, 2, 3, 4):
        for mode_index, mode in enumerate(COVERAGE_MODES):
            ax = axes[q - 1, mode_index]
            for x, level in enumerate(COVERAGE_LEVELS):
                for y, policy in enumerate(POLICIES):
                    cell = lookup[(q, mode, level, policy)]
                    n = cell["n_registered_trajectories"]
                    failed = cell["p0_observed_failed_count"]
                    unknown = cell["n_unknown_tail"]
                    color = ("#eef0f3" if n == 0 else
                             "#f7dad7" if failed else
                             "#fff0d3" if unknown else "#e1f1ed")
                    ax.add_patch(Rectangle((x - .47, y - .43), .94, .86,
                                           facecolor=color, edgecolor="white",
                                           linewidth=1.3))
                    if n == 0:
                        label = "not sampled"
                    else:
                        maximum = cell["p0_max_single_carrier_ENS_kWh"]
                        ens = ("?" if maximum is None else
                               f"{maximum:.2g}" if maximum else "0")
                        label = (f"F {failed}/{n}  U {unknown}\n"
                                 f"M {ens}  GT {cell['gt_trip_trajectory_count']}")
                    ax.text(x, y, label, ha="center", va="center", fontsize=6.4,
                            color="#28343d", linespacing=1.25)
            ax.set_xlim(-.5, 2.5)
            ax.set_ylim(1.5, -.5)
            ax.set_xticks(range(3), ("Ordinary", "Elevated", "Boundary"), fontsize=7)
            ax.set_yticks((0, 1), ("Secure", "Priority"), fontsize=7)
            ax.tick_params(length=0)
            for spine in ax.spines.values():
                spine.set_visible(False)
            star = "  *" if (q, mode) in reversed_groups else ""
            ax.set_title(f"Q{q} | {mode_title[mode]}{star}", fontsize=9,
                         weight="semibold", pad=6)
    fig.suptitle(f"{category} | observed model coverage by season, shock and design intensity",
                 fontsize=14, weight="semibold", y=.995)
    fig.text(.02, .012,
             "Each rectangle is one sampled design point for one policy and one class. "
             "F = observed P0 failures / registered trajectories; U = unknown tails.\n"
             "M = largest single-carrier P0 ENS among complete trajectories "
             "(kWh; carriers are not added); GT = trajectories with a real GT protection trip.\n"
             "* = at least one complete matched root/policy three-level reversal. "
             "Colors mark failed, unknown, no observed failure or unsampled; "
             "no interpolation or probability.",
             ha="left", va="bottom", fontsize=7, wrap=True)
    fig.tight_layout(rect=(0, .065, 1, .97), h_pad=1.15, w_pad=.8)
    fig.savefig(png, dpi=200)
    fig.savefig(svg)
    plt.close(fig)


def build_coverage_figures(run_dir: str | Path, out_dir: str | Path,
                           *, analysis_path: str | Path | None = None) -> dict:
    """Draw three class-separated coverage matrices from audited full analysis."""
    run, out = Path(run_dir).resolve(), Path(out_dir).resolve()
    manifest_path = run / "new_data/global_manifest.json"
    plan_path = run / "main/registered_plan.json"
    audit_path = run / "main/AUDIT.json"
    analysis_path = (Path(analysis_path).resolve() if analysis_path is not None
                     else run / "analysis/TRAJECTORY_RESULTS.csv")
    summary_path = run / "analysis/ANALYSIS_SUMMARY.json"
    manifest, plan, audit, summary = (_read_json(path) for path in
                                      (manifest_path, plan_path, audit_path, summary_path))
    expected = {(sid, policy["policy_id"]) for sid in plan["scenario_ids"]
                for policy in plan["policy_specs"]}
    if (analysis_path.parent != (run / "analysis").resolve() or
            plan.get("manifest_sha256") != _sha(manifest_path) or
            audit.get("manifest_sha256") != _sha(manifest_path) or
            audit.get("plan_sha256") != _sha(plan_path) or
            summary.get("manifest_sha256") != _sha(manifest_path) or
            summary.get("plan_sha256") != _sha(plan_path) or
            summary.get("audit_sha256") != _sha(audit_path) or
            audit.get("passed") is not True or
            audit.get("matched_initial_state_and_stream") is not True or
            audit.get("final_blind_opened") is True or
            plan.get("final_blind_opened") is True or
            any(audit.get(key) != len(expected) for key in
                ("registered_pair_count", "terminal_pair_count", "recomputed_pair_count"))):
        raise ValueError("Complete registered main analysis and raw audit required")
    ledger = {(item["scenario_id"], item["policy_id"]): item
              for item in audit["pair_ledger"]}
    if len(ledger) != len(audit["pair_ledger"]) or set(ledger) != expected:
        raise ValueError("Coverage audit pair denominator differs")
    with analysis_path.open(newline="", encoding="utf-8-sig") as stream:
        analysis_rows = list(csv.DictReader(stream))
    analysis = {(row["scenario_id"], row["policy_id"]): row for row in analysis_rows}
    if len(analysis) != len(analysis_rows) or set(analysis) != expected:
        raise ValueError("Coverage analysis pair denominator differs")
    for key, row in analysis.items():
        entry = ledger[key]
        latest = [attempt for attempt in entry["attempts"]
                  if attempt.get("result_sha256") == entry["latest_result_sha256"]]
        if len(latest) != 1 or row.get("raw_sha256") != latest[0]["raw_sha256"]:
            raise ValueError("Coverage analysis raw SHA differs from latest audit")
    detail, cells, reversals = coverage_tables(manifest, analysis_rows)
    if not detail or out.exists():
        raise ValueError("Coverage requires observed main-axis rows and new output directory")
    out.mkdir(parents=True)
    _write_csv(out / "COVERAGE_PAIRS.csv", tuple(detail[0]), detail)
    _write_csv(out / "COVERAGE_CELLS.csv", tuple(cells[0]), cells)
    _write_csv(out / "MATCHED_NONMONOTONIC.csv", tuple(reversals[0]), reversals)
    figures = []
    for category in COVERAGE_CLASSES:
        png, svg = out / f"coverage_{category}.png", out / f"coverage_{category}.svg"
        _draw_coverage_class(category, cells, reversals, png, svg)
        figures.append({"analysis_class": category, "png_path": str(png),
                        "png_sha256": _sha(png), "svg_path": str(svg),
                        "svg_sha256": _sha(svg)})
    receipt = {"schema": "four_scenarios.deepening_coverage_figures.v1",
               "manifest_sha256": _sha(manifest_path), "plan_sha256": _sha(plan_path),
               "audit_sha256": _sha(audit_path), "analysis_sha256": _sha(analysis_path),
               "analysis_summary_sha256": _sha(summary_path),
               "coverage_pair_count": len(detail), "coverage_cell_count": len(cells),
               "matched_reversal_count": sum(row["nonmonotonic"] is True
                                             for row in reversals),
               "source_csv": {name: _sha(out / name) for name in
                              ("COVERAGE_PAIRS.csv", "COVERAGE_CELLS.csv",
                               "MATCHED_NONMONOTONIC.csv")},
               "figures": figures,
               "interpretation": "Discrete model observations; no interpolation or incidence probability"}
    with (out / "COVERAGE_FIGURE_INDEX.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return receipt


def v7_mechanism_selection() -> list[dict]:
    """Known v7 mechanism anchors; other runs can supply a selection JSON."""
    return [
        {"figure_id": "day051_l1_electric_bridge", "plot_kind": "electric_bridge",
         "time_block_id": "four_window_day_51", "analysis_classes": ["L1"],
         "view_start": 60, "view_end": 180},
        {"figure_id": "day363_l2_heat_response", "plot_kind": "heat_response",
         "time_block_id": "four_window_day_363", "analysis_classes": ["L2"],
         "view_start": 45, "view_end": 180},
        {"figure_id": "day051_l3_gt_trip", "plot_kind": "gt_trip",
         "time_block_id": "four_window_day_51", "analysis_classes": ["L3"],
         "view_start": 60, "view_end": 180,
         "required_event": {"policy_id": "priority_milp", "kind": "protection_trip",
                            "device": "GT_2"}},
        {"figure_id": "day187_l4a_l4b_compound", "plot_kind": "compound",
         "time_block_id": "four_window_day_187", "analysis_classes": ["L4a", "L4b"],
         "view_start": 45, "view_end": 360},
    ]


def _validate_selection(selection: list[dict], scenes: dict, plan: dict) -> list[dict]:
    if not isinstance(selection, list) or not selection:
        raise ValueError("Selection must contain at least one figure")
    registered = set(plan["scenario_ids"])
    known_policies = {x["policy_id"] for x in plan["policy_specs"]}
    if not set(POLICIES) <= known_policies:
        raise ValueError("Both registered baseline policies are required")
    validated, names = [], set()
    for case in selection:
        name = case.get("figure_id")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Unsafe or missing figure_id")
        if name in names:
            raise ValueError("Duplicate figure_id")
        names.add(name)
        kind = case.get("plot_kind")
        if kind not in PLOT_FIELDS:
            raise ValueError("Unknown plot_kind")
        ids = case.get("scenario_ids")
        if ids is None:
            block, classes = case.get("time_block_id"), case.get("analysis_classes")
            if not isinstance(block, str) or not isinstance(classes, list) or not classes:
                raise ValueError("Selection needs scenario_ids or block and classes")
            if len(classes) != len(set(classes)):
                raise ValueError("Repeated analysis class")
            ids = []
            for analysis_class in classes:
                found = [sid for sid, scene in scenes.items()
                         if scene.get("time_block_id") == block and
                         scene.get("analysis_class") == analysis_class]
                if len(found) != 1:
                    raise ValueError("Expected one registered scene for block/class")
                ids.append(found[0])
        if not isinstance(ids, list) or not 1 <= len(ids) <= 4 or len(ids) != len(set(ids)):
            raise ValueError("Selection needs one to four distinct scenarios")
        if any(sid not in scenes or sid not in registered for sid in ids):
            raise ValueError("Selected scene is absent from registered manifest")
        selected = [scenes[sid] for sid in ids]
        if any(scene.get("is_final_blind") for scene in selected):
            raise ValueError("Sealed final scene cannot be plotted")
        roots = {scene["root_family_id"] for scene in selected}
        if len(roots) != 1:
            raise ValueError("Selected scenarios do not share a root")
        start, end = float(case.get("view_start", 0)), float(case.get("view_end", 600))
        if not 0 <= start < end:
            raise ValueError("Invalid plotted observation window")
        required = case.get("required_event")
        if required is not None and (not isinstance(required, dict) or
                                     required.get("policy_id") not in POLICIES or
                                     not required.get("kind")):
            raise ValueError("Invalid required_event")
        validated.append({"figure_id": name, "plot_kind": kind,
                          "scenario_ids": ids, "root_family_id": next(iter(roots)),
                          "view_start": start, "view_end": end,
                          "required_event": required})
    return validated


def _read_sources(case: dict, scenes: dict, ledger: dict, results: Path) -> tuple[list, list, list]:
    segment_rows, event_rows, sources = [], [], []
    base_ids = set()
    for sid in case["scenario_ids"]:
        streams = set()
        for policy in POLICIES:
            entry = ledger.get((sid, policy))
            if not entry or not entry.get("terminal") or not entry.get("attempts"):
                raise ValueError("Missing audited paired policy trajectory")
            latest = entry["attempts"][-1]
            row = {"scenario_id": sid, "policy_id": policy,
                   "analysis_class": scenes[sid]["analysis_class"],
                   "status": entry["status"], "raw_sha256": latest["raw_sha256"],
                   "raw_path": latest.get("raw_path", "")}
            payload, raw_path = _read_verified_raw(row, scenes, ledger, results)
            summary = payload["summary"]
            observed = float(summary["observed_until_minute"])
            requested = float(summary["requested_end_minute"])
            if (entry["status"] != "completed" or observed + 1e-6 < requested or
                    case["view_end"] > observed + 1e-6):
                raise ValueError("Figure requires a complete observed trajectory")
            identity = payload.get("identity", {})
            if identity.get("group_id") not in (None, case["root_family_id"]):
                raise ValueError("Raw root identity differs from manifest")
            if identity.get("base_state_id") is not None:
                base_ids.add(identity["base_state_id"])
            if identity.get("exogenous_stream_id") is not None:
                streams.add(identity["exogenous_stream_id"])
            rows = _segment_rows(payload, sid, policy, latest["raw_sha256"])
            if any(x["observation_status"] != "observed" for x in rows):
                raise ValueError("Figure requires a complete observed trajectory")
            cursor = 0.0
            for segment in rows:
                if abs(float(segment["start_minute"]) - cursor) > 1e-6:
                    raise ValueError("Raw segments are not contiguous")
                cursor = float(segment["end_minute"])
            if abs(cursor - observed) > 1e-6:
                raise ValueError("Raw segment endpoint differs from summary")
            for segment in rows:
                segment_rows.append({"figure_id": case["figure_id"],
                                     "analysis_class": scenes[sid]["analysis_class"],
                                     "root_family_id": case["root_family_id"],
                                     "GT_total_kW": sum(float(segment[f"GT_{j}_kW"])
                                                        for j in (1, 2, 3)), **segment})
            events = _event_rows(payload, sid, policy, latest["raw_sha256"])
            for event in events:
                event_rows.append({"figure_id": case["figure_id"],
                                   "analysis_class": scenes[sid]["analysis_class"],
                                   "root_family_id": case["root_family_id"], **event})
            sources.append({"scenario_id": sid, "analysis_class": scenes[sid]["analysis_class"],
                            "policy_id": policy, "raw_path": str(raw_path),
                            "raw_sha256": latest["raw_sha256"],
                            "segment_count": len(rows), "event_count": len(events)})
        if len(streams) > 1:
            raise ValueError("Paired policies have different exogenous streams")
    if len(base_ids) > 1:
        raise ValueError("Selected scenarios have different initial states")
    required = case["required_event"]
    if required and not any(all(str(event.get(k)) == str(value)
                                for k, value in required.items()) for event in event_rows):
        raise ValueError("Required raw event is absent")
    return segment_rows, event_rows, sources


def _write_csv(path: Path, fields: tuple, rows: list[dict]) -> None:
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _draw(case: dict, rows: list[dict], events: list[dict], png: Path, svg: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                         "svg.fonttype": "none", "legend.frameon": False,
                         "axes.spines.top": False, "axes.spines.right": False})
    metrics = PLOT_FIELDS[case["plot_kind"]]
    figure, axes = plt.subplots(len(metrics) + 1, 1, figsize=(10.2, 2.1 * (len(metrics) + 1)),
                                sharex=True, constrained_layout=True)
    ids = case["scenario_ids"]
    grouped = {(sid, policy): [r for r in rows if r["scenario_id"] == sid and
                               r["policy_id"] == policy] for sid in ids for policy in POLICIES}
    colors = {"secure_milp": "#315d86", "priority_milp": "#be6b39"}
    scene_colors = {sid: color for sid, color in zip(ids,
                     ("#315d86", "#be6b39", "#63846f", "#9a6f9c"))}
    for ax, (field, ylabel) in zip(axes[:-1], metrics):
        for sid in ids:
            for policy in POLICIES:
                data = grouped[(sid, policy)]
                x = [float(r["start_minute"]) for r in data] + [float(data[-1]["end_minute"])]
                y = [float(r[field]) for r in data]
                label = (f"{data[0]['analysis_class']} {policy.replace('_milp', '')}"
                         if len(ids) > 1 else policy.replace("_milp", ""))
                ax.step(x, y + [y[-1]], where="post", label=label,
                        color=scene_colors[sid] if len(ids) > 1 else colors[policy],
                        linestyle="-" if policy == "secure_milp" else "--",
                        linewidth=1.5)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", color="#e5e9ee", linewidth=.6)
        ax.legend(loc="upper right", ncol=4 if len(ids) > 1 else 2, fontsize=7)
    lane = axes[-1]
    labels = [(sid, policy) for sid in ids for policy in POLICIES]
    for event in events:
        kind = event["kind"]
        if kind not in {"exogenous_fault_start", "exogenous_fault_end",
                        "shock_changed", "protection_trip"}:
            continue
        time = float(event["time_minute"])
        if not case["view_start"] <= time <= case["view_end"]:
            continue
        key = (event["scenario_id"], event["policy_id"])
        y = labels.index(key)
        marker, color = ({"protection_trip": ("X", "#b83b44"),
                          "exogenous_fault_start": ("^", "#7c629b"),
                          "exogenous_fault_end": ("v", "#8b8990"),
                          "shock_changed": ("D", "#397d79")})[kind]
        lane.scatter(time, y, marker=marker, color=color, s=43, zorder=3)
        if kind == "protection_trip":
            lane.annotate(f"{event['device']} trip", (time, y), xytext=(4, 5),
                          textcoords="offset points", color=color, fontsize=7)
    lane.set_yticks(range(len(labels)),
                    [f"{next(r['analysis_class'] for r in rows if r['scenario_id'] == sid)} "
                     f"{policy.replace('_milp', '')}" for sid, policy in labels])
    lane.set_ylim(-.6, len(labels) - .4)
    lane.set_ylabel("Observed events")
    lane.set_xlabel("Minutes since episode start; source CSV retains full observed window")
    lane.grid(axis="x", color="#e5e9ee", linewidth=.6)
    for ax in axes:
        ax.set_xlim(case["view_start"], case["view_end"])
    figure.suptitle(f"{case['figure_id']} | saved raw, matched initial root\n"
                    "Solid: secure; dashed: priority. Events and service are observed, not forecasts.")
    figure.savefig(png, dpi=180)
    figure.savefig(svg)
    plt.close(figure)


def build_figures(run_dir: str | Path, out_dir: str | Path,
                  selection: list[dict]) -> dict:
    """Verify selected paired raw, then write figures and full numeric source CSV."""
    run, out = Path(run_dir).resolve(), Path(out_dir).resolve()
    manifest_path = run / "new_data/global_manifest.json"
    plan_path = run / "main/registered_plan.json"
    audit_path = run / "main/AUDIT.json"
    manifest, plan, audit = (_read_json(p) for p in
                             (manifest_path, plan_path, audit_path))
    if (plan.get("manifest_sha256") != _sha(manifest_path) or
            audit.get("manifest_sha256") != _sha(manifest_path) or
            audit.get("plan_sha256") != _sha(plan_path)):
        raise ValueError("Manifest/plan/audit SHA binding differs")
    if (not audit.get("passed") or audit.get("matched_initial_state_and_stream") is not True or
            plan.get("final_blind_opened") is True or audit.get("final_blind_opened") is True):
        raise ValueError("A passing matched audit and sealed final declaration are required")
    if (audit.get("registered_pair_count") != plan.get("registered_pair_count") or
            audit.get("terminal_pair_count") != plan.get("registered_pair_count")):
        raise ValueError("Registered pair denominator is incomplete")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    cases = _validate_selection(selection, scenes, plan)
    ledger = {(entry["scenario_id"], entry["policy_id"]): entry
              for entry in audit["pair_ledger"]}
    prepared = []
    for case in cases:
        rows, events, sources = _read_sources(case, scenes, ledger, run / "main/results")
        prepared.append((case, rows, events, sources))
    out.mkdir(parents=True, exist_ok=True)
    result = {"schema": "four_scenarios.deepening_figures.v1",
              "input_run": str(run), "manifest_sha256": _sha(manifest_path),
              "plan_sha256": _sha(plan_path), "audit_sha256": _sha(audit_path),
              "figures": []}
    for case, rows, events, sources in prepared:
        stem = case["figure_id"]
        csv_path, events_path = out / f"{stem}.csv", out / f"{stem}_events.csv"
        png, svg = out / f"{stem}.png", out / f"{stem}.svg"
        for path in (csv_path, events_path, png, svg):
            if path.exists():
                raise FileExistsError(f"Figure output already exists: {path}")
        _write_csv(csv_path, SEGMENT_OUTPUT_FIELDS, rows)
        _write_csv(events_path, EVENT_OUTPUT_FIELDS, events)
        _draw(case, rows, events, png, svg)
        result["figures"].append({**case, "csv_path": str(csv_path),
                                  "csv_sha256": _sha(csv_path),
                                  "events_csv_path": str(events_path),
                                  "events_csv_sha256": _sha(events_path),
                                  "png_path": str(png), "png_sha256": _sha(png),
                                  "svg_path": str(svg), "svg_sha256": _sha(svg),
                                  "raw_sources": sources})
    index_path = out / "FIGURE_INDEX.json"
    with index_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Existing audited run directory")
    parser.add_argument("--out-dir", required=True, help="New versioned output directory")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--selection", help="JSON list of figure selections for any run")
    source.add_argument("--preset-v7", action="store_true", help="Four known v7 mechanisms")
    source.add_argument("--coverage-v8", action="store_true",
                        help="three class-separated audited shock coverage figures")
    args = parser.parse_args(argv)
    if args.coverage_v8:
        result = build_coverage_figures(args.run, args.out_dir)
        print(json.dumps({"figure_count": len(result["figures"]),
                          "index": str(Path(args.out_dir).resolve() /
                                       "COVERAGE_FIGURE_INDEX.json")}, ensure_ascii=False))
        return 0
    selection = (_read_json(Path(args.selection)) if args.selection
                 else v7_mechanism_selection())
    result = build_figures(args.run, args.out_dir, selection)
    print(json.dumps({"figure_count": len(result["figures"]),
                      "index": str(Path(args.out_dir).resolve() / "FIGURE_INDEX.json")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
