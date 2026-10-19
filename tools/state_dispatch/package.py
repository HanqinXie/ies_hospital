"""Small multi-batch v9 evidence bundle; verify saved raw, never rerun physics.

Archive creation, byte integrity, source inventory and the 19-target scorer
are reused from the established four-scenario packaging implementation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.four_scenarios.package import (
    PHYSICAL_INPUTS, _add, _archive_name, _bytes, _label, _pair_record, _read,
    _sha, _source_entries, _verify_raw_index_against_audit,
    _verify_registered_sources, _within, compare_scored_windows,
    create_archive, verify_integrity,
)
from tools.four_scenarios.deepening_package import compare_analysis_subset

KIND = "v9_state_dispatch_selected_raw"
INDEX_NAME = "EXTERNAL_RAW_SHA256_INDEX.json"
REQUIRED_CLASSES = ("L1", "L2", "L3", "L4b")
BASELINES = ("priority_milp", "secure_milp")
WEIGHT_SUFFIXES = {".pt", ".pth", ".onnx", ".h5", ".ckpt"}


def _resolve(value, root, base):
    path = Path(value)
    if not path.is_absolute():
        path = root / path if (root / path).exists() else base / path
    return _within(path, root)


def _analysis_map(path, expected):
    if path is None:
        return {}, None
    candidates = ([path] if path.is_file() else
                  sorted(p for p in path.glob("*.csv") if
                         "TRAJECTORY_RESULTS" in p.name or "SCENARIO_RESULTS" in p.name))
    found = []
    for candidate in candidates:
        with candidate.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        mapping = {(r.get("scenario_id"), r.get("policy_id")): r for r in rows}
        if len(mapping) == len(rows) and set(mapping) == expected:
            found.append((mapping, candidate))
    if len(found) != 1:
        raise ValueError("Analysis requires one exact registered pair denominator")
    return found[0]


def _check_evidence(manifest, plan, audit, paths):
    ids = plan.get("scenario_ids", [])
    policies = [item["policy_id"] for item in plan.get("policy_specs", [])]
    expected = {(sid, pid) for sid in ids for pid in policies}
    if (not ids or not policies or len(ids) != len(set(ids)) or
            len(policies) != len(set(policies)) or
            plan.get("registered_pair_count") != len(expected) or
            audit.get("registered_pair_count") != len(expected) or
            plan.get("manifest_sha256") != _sha(paths["manifest"]) or
            audit.get("manifest_sha256") != _sha(paths["manifest"]) or
            audit.get("plan_sha256") != _sha(paths["plan"])):
        raise ValueError("Batch registration/audit binding or denominator differs")
    scenes = {s["id"]: s for s in manifest.get("scenarios", [])}
    if (len(scenes) != len(manifest.get("scenarios", [])) or not set(ids) <= set(scenes) or
            manifest.get("policy_search_final_12_backgrounds_unopened") is False or
            plan.get("final_blind_opened") is True or audit.get("final_blind_opened") is True or
            any(scenes[sid].get("is_final_blind") is not False or
                scenes[sid].get("experiment_role") != plan.get("phase") for sid in ids)):
        raise ValueError("Batch contains a sealed final, absent or phase-mismatched scene")
    ledger = {(r.get("scenario_id"), r.get("policy_id")): r for r in audit.get("pair_ledger", [])}
    if len(ledger) != len(audit.get("pair_ledger", [])) or set(ledger) != expected:
        raise ValueError("Audit pair ledger omits or duplicates registered denominator")
    return ledger


def _failure(pair):
    score = pair.get("recomputed_full_observation") or {}
    targets = score.get("targets") or []
    value = targets[0] if targets else None
    if value == 1:
        return 1
    if value == 0 and score.get("full_horizon") is True:
        return 0
    return None


def _denominators(ledger):
    values = list(ledger.values())
    return dict(
        registered_pairs=len(values), attempts_started=sum(len(p.get("attempts", [])) for p in values),
        terminal_pairs=sum(p.get("terminal") is True for p in values),
        not_started_pairs=sum(not p.get("attempts") for p in values),
        known_failure_pairs=sum(_failure(p) == 1 for p in values),
        known_no_failure_pairs=sum(_failure(p) == 0 for p in values),
        unknown_pairs=sum(_failure(p) is None for p in values),
        status_counts=dict(Counter(str(p.get("status") or "not_started") for p in values)))


def load_batches(batches_path, *, workspace_root=ROOT):
    """Read all registered pairs, including failed or not-yet-terminal audits."""
    root = Path(workspace_root).resolve()
    definition = _within(Path(batches_path), root)
    rows = _read(definition)
    if not isinstance(rows, list) or not rows:
        raise ValueError("batches.json must be a nonempty list")
    names, result_paths, batches = set(), set(), []
    for row in rows:
        name = _label(row["name"])
        if name in names:
            raise ValueError("Duplicate batch name")
        names.add(name)
        paths = {key: _resolve(row[key], root, definition.parent)
                 for key in ("manifest", "plan", "results", "audit")}
        if paths["results"] in result_paths:
            raise ValueError("Duplicate batch results directory")
        result_paths.add(paths["results"])
        if not paths["results"].is_dir():
            raise FileNotFoundError(paths["results"])
        manifest, plan, audit = (_read(paths[key]) for key in ("manifest", "plan", "audit"))
        ledger = _check_evidence(manifest, plan, audit, paths)
        analysis_path = (_resolve(row["analysis"], root, definition.parent)
                         if row.get("analysis") else None)
        analysis, final_analysis = _analysis_map(analysis_path, set(ledger))
        batches.append(dict(name=name, paths=paths, manifest=manifest, plan=plan, audit=audit,
                            ledger=ledger, analysis=analysis, analysis_path=final_analysis,
                            analysis_directory=analysis_path if analysis_path and analysis_path.is_dir() else None,
                            denominators=_denominators(ledger)))
    return batches


def index_batch_attempts(batch, workspace_root=ROOT):
    """Index audited retry/raw/result/error bytes, preserving no-raw attempts."""
    root, results = Path(workspace_root).resolve(), batch["paths"]["results"]
    rows, raw_paths, known_folders = [], set(), set()
    for (sid, pid), pair in sorted(batch["ledger"].items()):
        used = set()
        for attempt in pair.get("attempts", []):
            number = attempt["attempt"]
            if not re.fullmatch(r"attempt_[0-9]+", number) or number in used:
                raise ValueError("Invalid or duplicate audited attempt")
            used.add(number)
            folder = results / "pairs" / digest([sid, pid]) / number
            known_folders.add(folder.resolve())
            raw, result, error = (folder / name for name in ("trajectory.json.gz", "result.json", "error.json"))
            record = dict(batch=batch["name"], scenario_id=sid, policy_id=pid, attempt=number,
                          status=attempt.get("status"), terminal=attempt.get("terminal"),
                          raw_sha256=attempt.get("raw_sha256"), result_sha256=attempt.get("result_sha256"),
                          error_sha256=attempt.get("error_sha256"), raw_bytes=None,
                          latest_audited=attempt.get("result_sha256") == pair.get("latest_result_sha256"),
                          included_in_package=False, raw_relative_to_original_workspace=None,
                          raw_relative_to_original_run=None,
                          packaged_result_path=None, packaged_error_path=None)
            for kind, path in (("raw", raw), ("result", result), ("error", error)):
                expected = record[kind + "_sha256"]
                if expected is None:
                    if path.exists():
                        raise ValueError("Unindexed or unbound actual " + kind + " bytes")
                elif not path.is_file() or _sha(path) != expected:
                    raise ValueError("Audited " + kind + " SHA differs: " + str(path))
            if raw.is_file():
                relative = _within(raw, root).relative_to(root).as_posix()
                raw_paths.add(raw.resolve())
                record.update(raw_bytes=raw.stat().st_size,
                              raw_relative_to_original_workspace=relative,
                              raw_relative_to_original_run=relative)
            if result.is_file():
                value = _read(result)
                if (value.get("scenario_id") != sid or value.get("policy_id") != pid or
                        value.get("plan_signature") != batch["plan"]["signature"]):
                    raise ValueError("Audited result/plan identity differs")
                record["packaged_result_path"] = f"batches/{batch['name']}/attempts/{digest([sid,pid])}/{number}/result.json"
            if error.is_file():
                record["packaged_error_path"] = f"batches/{batch['name']}/attempts/{digest([sid,pid])}/{number}/error.json"
            rows.append(record)
    actual_raw = {p.resolve() for p in results.rglob("trajectory.json.gz")}
    actual_folders = {p.resolve() for p in results.glob("pairs/*/attempt_*") if p.is_dir()}
    if actual_raw != raw_paths or actual_folders != known_folders:
        raise ValueError("Unindexed actual raw or attempt directory")
    raw_ledger = {}
    for key, pair in batch["ledger"].items():
        attempts = [a for a in pair.get("attempts", []) if a.get("raw_sha256") and a.get("result_sha256")]
        if attempts:
            raw_ledger[key] = {**pair, "attempts": attempts}
    _verify_raw_index_against_audit([r for r in rows if r["raw_sha256"] and r["result_sha256"]], raw_ledger)
    return rows


def _latest_attempt(pair):
    return next((a for a in reversed(pair.get("attempts", []))
                 if a.get("result_sha256") == pair.get("latest_result_sha256")), None)


def validate_selection(selection, batches):
    rows = selection.get("selected_pairs")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("Selection needs 1 to 32 small complete raw examples")
    by_name = {b["name"]: b for b in batches}
    chosen = set()
    for row in rows:
        key = (row.get("batch"), row.get("scenario_id"), row.get("policy_id"))
        batch = by_name.get(key[0])
        if batch is None or key[1:] not in batch["ledger"] or key in chosen:
            raise ValueError("Selected pair absent or duplicate")
        pair = batch["ledger"][key[1:]]
        latest = _latest_attempt(pair)
        if (latest is None or pair.get("terminal") is not True or
                pair.get("binding_passed") is not True or pair.get("score_recomputed") is not True or
                pair.get("recomputed_full_observation", {}).get("full_horizon") is not True):
            raise ValueError("Selected raw must be a complete audited matched result")
        expected = row.get("expected_raw_sha256")
        if (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or
                expected != latest.get("raw_sha256")):
            raise ValueError("Selected raw expected SHA differs from audit")
        chosen.add(key)
    required = set(selection.get("required_policies") or [
        spec["policy_id"] for b in batches for spec in b["plan"]["policy_specs"]])
    if not set(BASELINES) <= required:
        raise ValueError("Both original baselines are required")
    groups = {(name, sid) for name, sid, _ in chosen}
    classes = {}
    for name, sid in groups:
        batch = by_name[name]
        policies = {p["policy_id"] for p in batch["plan"]["policy_specs"]}
        if {(name, sid, p) for p in policies} - chosen:
            raise ValueError("Selected scene lacks complete matched policy raw")
        scene = next(s for s in batch["manifest"]["scenarios"] if s["id"] == sid)
        if required <= policies:
            classes[scene["analysis_class"]] = True
    if not set(REQUIRED_CLASSES) <= set(classes):
        raise ValueError("Selected matched raw lacks required four classes or frozen policy")
    failures = [_failure(by_name[name]["ledger"][(sid, pid)]) for name, sid, pid in chosen]
    if selection.get("require_failure_and_nonfailure", True) and not (1 in failures and 0 in failures):
        raise ValueError("Selection needs observed failure and nonfailure examples")
    return dict(selected_raw_count=len(chosen), required_policies=sorted(required),
                matched_scene_count=len(groups), p0_failed_count=failures.count(1),
                p0_nonfailed_count=failures.count(0), required_classes=list(REQUIRED_CLASSES))


def rescore_selected(payload, result, analysis_row, env):
    """Use the unchanged 19-target score and established independent raw subset."""
    from tools.control_improvement.branches import window_targets
    primary = window_targets(payload, 60., 360., env)
    full = window_targets(payload, 0., 600., env)
    score = compare_scored_windows(result, primary, full)
    if analysis_row is None:
        return {**score, "analysis_subset_match": None, "analysis_check": "not_provided"}
    adapted = dict(analysis_row)
    if "gt_protection_trip_count" in adapted:
        adapted["GT_protection_trip_count_observed"] = adapted["gt_protection_trip_count"]
    subset = compare_analysis_subset(payload, adapted)
    return {**score, **subset}


def _stage_artifacts(entries, directory, prefix):
    for source in sorted(directory.rglob("*")):
        if source.is_symlink():
            raise ValueError("Symlinked artifact prohibited")
        if (source.is_file() and "__pycache__" not in source.parts and
                source.suffix not in WEIGHT_SUFFIXES and source.name != "trajectory.json.gz"):
            _add(entries, prefix + source.relative_to(directory).as_posix(), source.read_bytes())


def _stage_sources(entries, batches, root):
    external = {}
    for name, source in _source_entries(root).items():
        _add(entries, name, source)
    for batch in batches:
        _verify_registered_sources(batch["plan"], root)
        for item in batch["manifest"].get("files", []):
            name = _archive_name(item["path"])
            path = _within(root / name, root)
            if (not path.is_file() or path.stat().st_size != item["bytes"] or _sha(path) != item["sha256"]):
                raise ValueError("Registered runtime/source input SHA differs: " + name)
            if name.startswith("inputs/literature/"):
                external[name] = {**item, "packaged": False, "reason": "literature_not_runtime_score_input"}
            else:
                _add(entries, name, path)
        for name, expected in batch["manifest"].get("source_sha256", {}).items():
            name = _archive_name(name)
            path = _within(root / name, root)
            if not path.is_file() or _sha(path) != expected:
                raise ValueError("Registered design source SHA differs: " + name)
            if path.suffix in WEIGHT_SUFFIXES or path.name == "trajectory.json.gz":
                external[name] = dict(path=name, sha256=expected, bytes=path.stat().st_size,
                                      packaged=False, reason="historical_raw_or_weight_not_score_input")
            else:
                _add(entries, name, path)
    for name in PHYSICAL_INPUTS:
        _add(entries, name, _within(root / name, root))
    for source in sorted(root.glob("requirements*.txt")):
        _add(entries, source.name, source)
    if "requirements.txt" not in entries:
        raise FileNotFoundError(root / "requirements.txt")
    return sorted(external.values(), key=lambda r: r["path"])


def _pair_index(batches):
    return [dict(batch=b["name"], scenario_id=sid, policy_id=pid,
                 status=pair.get("status"), terminal=pair.get("terminal"),
                 latest_result_sha256=pair.get("latest_result_sha256"),
                 attempt_count=len(pair.get("attempts", [])), known_p0_failure=_failure(pair))
            for b in batches for (sid, pid), pair in sorted(b["ledger"].items())]


def build_bundle(run_dir, archive_path, batches_path, selection_path, report_path, *, workspace_root=ROOT):
    root = Path(workspace_root).resolve()
    run = _within(Path(run_dir), root)
    report = _within(Path(report_path), run)
    batches = load_batches(batches_path, workspace_root=root)
    selection = _read(_within(Path(selection_path), root))
    roles = validate_selection(selection, batches)
    entries = {}
    external_inputs = _stage_sources(entries, batches, root)
    for folder in sorted(run.iterdir()):
        if folder.is_dir() and (folder.name in ("design", "review", "figures", "logs", "analysis")
                                or folder.name.startswith("analysis_")):
            _stage_artifacts(entries, folder, "run/" + folder.name + "/")
    _add(entries, "run/" + report.relative_to(run).as_posix(), report.read_bytes())
    if (run / "RUN_LEDGER.jsonl").is_file():
        _add(entries, "run/RUN_LEDGER.jsonl", (run / "RUN_LEDGER.jsonl").read_bytes())
    _add(entries, "evidence/BATCHES_REGISTRATION.json", Path(batches_path).resolve())
    _add(entries, "evidence/REPRO_SELECTION.json", Path(selection_path).resolve())
    indexed, batch_records = [], []
    for batch in batches:
        prefix = f"batches/{batch['name']}/"
        paths = batch["paths"]
        record = dict(name=batch["name"], phase=batch["plan"]["phase"],
                      audit_passed=batch["audit"].get("passed") is True,
                      denominators=batch["denominators"], evidence={})
        for key in ("manifest", "plan", "audit"):
            name = prefix + "evidence/" + {"manifest": "global_manifest.json", "plan": "registered_plan.json", "audit": "AUDIT.json"}[key]
            _add(entries, name, paths[key])
            record["evidence"][key] = dict(path=name, sha256=_sha(paths[key]))
        if batch["plan"].get("preference_sha256"):
            preference = _resolve(batch["plan"]["preference_path"], root, paths["plan"].parent)
            if _sha(preference) != batch["plan"]["preference_sha256"]:
                raise ValueError("Registered research preference SHA differs")
            name = prefix + "evidence/RESEARCH_PREFERENCE.json"
            _add(entries, name, preference)
            record.update(preference_file=name, preference_sha256=_sha(preference))
        record["analysis_file"] = None
        if batch["analysis_path"]:
            name = prefix + "analysis/" + batch["analysis_path"].name
            if batch["analysis_directory"]:
                _stage_artifacts(entries, batch["analysis_directory"], prefix + "analysis/")
            else:
                _add(entries, name, batch["analysis_path"])
            record.update(analysis_file=name, analysis_sha256=_sha(batch["analysis_path"]))
        rows = index_batch_attempts(batch, root)
        for row in rows:
            folder = paths["results"] / "pairs" / digest([row["scenario_id"], row["policy_id"]]) / row["attempt"]
            for kind in ("result", "error"):
                if row["packaged_" + kind + "_path"]:
                    _add(entries, row["packaged_" + kind + "_path"], folder / (kind + ".json"))
        indexed.extend(rows)
        batch_records.append(record)
    by_name = {b["name"]: b for b in batches}
    selected = []
    for requested in selection["selected_pairs"]:
        name, sid, pid = (requested[k] for k in ("batch", "scenario_id", "policy_id"))
        batch = by_name[name]
        scene = next(s for s in batch["manifest"]["scenarios"] if s["id"] == sid)
        result, payload, result_path, raw_path = _pair_record(batch["paths"]["results"], sid, pid,
            scene, batch["ledger"][(sid, pid)], load_raw=True)
        if _sha(raw_path) != requested["expected_raw_sha256"]:
            raise ValueError("Selected actual raw SHA differs")
        analysis = batch["analysis"].get((sid, pid))
        if analysis is not None:
            adapted = dict(analysis)
            if "gt_protection_trip_count" in adapted:
                adapted["GT_protection_trip_count_observed"] = adapted["gt_protection_trip_count"]
            compare_analysis_subset(payload, adapted)
        row = next(r for r in indexed if r["batch"] == name and r["scenario_id"] == sid
                   and r["policy_id"] == pid and r["latest_audited"])
        row["included_in_package"] = True
        raw_arc = f"examples/{name}/{digest([sid,pid])}/{result_path.parent.name}/trajectory.json.gz"
        _add(entries, raw_arc, raw_path)
        selected.append(dict(batch=name, scenario_id=sid, policy_id=pid, analysis_class=scene["analysis_class"],
            raw_path=raw_arc, raw_sha256=_sha(raw_path), result_path=row["packaged_result_path"],
            result_sha256=_sha(result_path), status=result["status"]))
    pairs = _pair_index(batches)
    _add(entries, INDEX_NAME, _bytes(dict(kind=KIND + ".attempt_index", registered_pair_count=len(pairs),
        raw_file_count=sum(r["raw_sha256"] is not None for r in indexed), attempt_count=len(indexed),
        pairs=pairs, attempts=indexed, unselected_raw_are_external=True)))
    replay_example = next((item for item in selected
        if by_name[item["batch"]]["plan"]["phase"] == "confirmation"), selected[0])
    replay_command = ("python -m tools.state_dispatch.portable_replay run --root . --batch " +
        replay_example["batch"] + " --scene " + replay_example["scenario_id"] +
        " --output D:\\hospital_v9_portable_replay_20261016_run01")
    _add(entries, "README_REPRODUCTION.md", (
        "# v9 state dispatch selected-raw evidence\n\n"
        "Run from this extracted root: `python -m tools.state_dispatch.package verify --root .`.\n\n"
        "Without Gurobi, install `requirements-saved-recheck.txt` (numpy/pandas only), then run "
        "`python -m tools.state_dispatch.saved_recheck --root . --output ../saved_recheck`. "
        "This independent integration path uses saved classifications and is not the original optimizer scorer. "
        "The original full 19-target verify and physical simulation require complete requirements plus real Gurobi.\n\n"
        "For that full path, install the complete dependencies with "
        "`python -m pip install -r requirements-training.txt gurobipy` and supply a genuine Gurobi license.\n\n"
        "Verification checks every package byte and recomputes both original windows (60-420 and 0-600 minutes), "
        "19 targets per window, plus available key analysis fields from selected saved raw. It does not reset, step, "
        "optimize, or physically rerun an incident. Runtime inputs and complete Python dependencies are included. "
        "Literature may remain external with SHA; historical raw and old neural weights are not copied.\n\n"
        "Each batch retains its own manifest, plan, audit, registered pair denominator and retry/error index. "
        "A failed audit remains failed; unobserved outcomes stay unknown. Shared roots or repeated physical points "
        "are not independent background evidence. Unselected raw remains at the indexed original paths.\n"
        "\n## Optional physical replay with genuine solver/license\n\n"
        "Original plans retain absolute original workspace paths as provenance only. Do not run an original plan "
        "and call that a package-local rerun. From this extracted root, first list portable scenes:\n\n"
        "`python -m tools.state_dispatch.portable_replay list --root .`\n\n"
        "This package's included example has a copyable physical replay command; its output directory must be new/empty "
        "and outside the package (change only that output path if it already exists):\n\n"
        f"`{replay_command}`\n\n"
        "This selects the first included scene by default (or repeat `--scene <listed_id>` for up to four), "
        "keeps its frozen manifest scenario, all original plan policy_specs/phase, and byte-bound preference, "
        "rebinds the current runtime/input inventory, re-registers a new plan into the new empty directory, "
        "then performs real simulation and a new raw audit. `prepare` in place of `run` only registers and "
        "starts zero episodes. Parent v7/v8 design/proposal files are provenance; the frozen scenario manifest "
        "is sufficient for execution and no parent sampling/design generator is called. Missing solver/license, "
        "required model weights or runtime inputs are reported; no dependency stubs or substitute policies are used. "
        "New results are separate experimental evidence and may differ with the recorded local runtime.\n"
    ).encode("utf-8"))
    summary = dict(kind=KIND, batches=batch_records, registered_pair_count=len(pairs),
        attempt_count=len(indexed), raw_file_count=sum(r["raw_sha256"] is not None for r in indexed),
        selected_raw_count=len(selected), examples=selected, roles=roles, full_raw_archive=False,
        external_registered_inputs=external_inputs,
        report_path="run/" + report.relative_to(run).as_posix(),
        selection_sha256=_sha(Path(selection_path)),
        batches_registration_sha256=_sha(Path(batches_path)),
        runtime_inputs={name: _sha(root / name) for name in PHYSICAL_INPUTS},
        claim_scope="Selected saved raw two-window score and available analysis checks; no physical rerun")
    return {**create_archive(archive_path, entries, summary), **roles,
            "registered_pair_count": len(pairs), "attempt_count": len(indexed),
            "raw_file_count": summary["raw_file_count"]}


def _verify_bundled_index(index, batches, root):
    if index.get("pairs") != _pair_index(batches):
        raise ValueError("Bundled pair index differs from full audited denominator")
    expected = {(b["name"], sid, pid, a["attempt"]): (a, pair)
                for b in batches for (sid, pid), pair in b["ledger"].items()
                for a in pair.get("attempts", [])}
    actual = set()
    for row in index.get("attempts", []):
        key = tuple(row[k] for k in ("batch", "scenario_id", "policy_id", "attempt"))
        if key not in expected or key in actual:
            raise ValueError("Bundled attempt index missing, duplicate or unregistered")
        actual.add(key)
        attempt, pair = expected[key]
        if any(row.get(kind + "_sha256") != attempt.get(kind + "_sha256")
               for kind in ("raw", "result", "error")):
            raise ValueError("Bundled attempt SHA differs from audit")
        for kind in ("result", "error"):
            name = row.get("packaged_" + kind + "_path")
            if (name is None) != (attempt.get(kind + "_sha256") is None):
                raise ValueError("Bundled no-raw/error attempt evidence missing")
            if name and _sha(_within(root / _archive_name(name), root)) != row[kind + "_sha256"]:
                raise ValueError("Bundled attempt receipt SHA differs")
    if actual != set(expected):
        raise ValueError("Bundled attempt index drops audited retry")
    if (index.get("registered_pair_count") != len(index["pairs"]) or
            index.get("attempt_count") != len(actual) or
            index.get("raw_file_count") != sum(r.get("raw_sha256") is not None for r in index["attempts"])):
        raise ValueError("Bundled index denominator differs")


def verify_bundle(extracted_root):
    root = Path(extracted_root).resolve()
    if ROOT.resolve() != root:
        raise ValueError("Run verify using package source from the extracted root")
    package = verify_integrity(root)
    if package.get("kind") != KIND or package.get("full_raw_archive") is not False:
        raise ValueError("Unexpected v9 package kind")
    batches = []
    for record in package["batches"]:
        paths = {key: _within(root / _archive_name(row["path"]), root)
                 for key, row in record["evidence"].items()}
        for key, path in paths.items():
            if _sha(path) != record["evidence"][key]["sha256"]:
                raise ValueError("Bundled batch evidence SHA differs")
        manifest, plan, audit = (_read(paths[key]) for key in ("manifest", "plan", "audit"))
        ledger = _check_evidence(manifest, plan, audit, paths)
        _verify_registered_sources(plan, root)
        if record["denominators"] != _denominators(ledger) or record["audit_passed"] != (audit.get("passed") is True):
            raise ValueError("Bundled audit outcome or denominator differs")
        if record.get("preference_file"):
            preference = _within(root / _archive_name(record["preference_file"]), root)
            if (_sha(preference) != record["preference_sha256"] or
                    record["preference_sha256"] != plan.get("preference_sha256")):
                raise ValueError("Bundled research preference SHA differs")
        analysis_file = record.get("analysis_file")
        analysis_path = _within(root / _archive_name(analysis_file), root) if analysis_file else None
        if analysis_path and _sha(analysis_path) != record["analysis_sha256"]:
            raise ValueError("Bundled analysis SHA differs")
        analysis, _ = _analysis_map(analysis_path, set(ledger))
        batches.append(dict(name=record["name"], manifest=manifest, plan=plan, audit=audit,
                            ledger=ledger, analysis=analysis, paths=paths))
    excluded = {item["path"]: item for item in package["external_registered_inputs"]}
    for batch in batches:
        for row in batch["manifest"].get("files", []):
            name = _archive_name(row["path"])
            if name in excluded:
                item = excluded[name]
                if not name.startswith("inputs/literature/") or item["sha256"] != row["sha256"] or (root / name).exists():
                    raise ValueError("External literature provenance differs")
            else:
                path = _within(root / name, root)
                if path.stat().st_size != row["bytes"] or _sha(path) != row["sha256"]:
                    raise ValueError("Bundled runtime/source input differs")
        for name, expected in batch["manifest"].get("source_sha256", {}).items():
            if name not in excluded and _sha(_within(root / _archive_name(name), root)) != expected:
                raise ValueError("Bundled registered design source differs")
    for name, expected in package["runtime_inputs"].items():
        if name not in PHYSICAL_INPUTS or _sha(root / name) != expected:
            raise ValueError("Bundled required runtime input differs")
    if (_sha(root / "evidence/REPRO_SELECTION.json") != package["selection_sha256"] or
            _sha(root / "evidence/BATCHES_REGISTRATION.json") != package["batches_registration_sha256"]):
        raise ValueError("Bundled batch/selection registration SHA differs")
    selection = _read(root / "evidence/REPRO_SELECTION.json")
    if validate_selection(selection, batches) != package["roles"]:
        raise ValueError("Bundled matched selection roles differ")
    index = _read(root / INDEX_NAME)
    _verify_bundled_index(index, batches, root)
    selected_keys = {(r["batch"], r["scenario_id"], r["policy_id"]) for r in package["examples"]}
    marked = {(r["batch"], r["scenario_id"], r["policy_id"]) for r in index["attempts"] if r["included_in_package"]}
    if selected_keys != marked or len(selected_keys) != package["selected_raw_count"]:
        raise ValueError("Bundled selected/external raw index differs")
    from hospital_ies.control_env import HospitalIESEnv
    by_name, environments = {b["name"]: b for b in batches}, {}
    checked = []
    for item in package["examples"]:
        batch, sid, pid = by_name[item["batch"]], item["scenario_id"], item["policy_id"]
        pair = batch["ledger"][(sid, pid)]
        raw = _within(root / _archive_name(item["raw_path"]), root)
        result_path = _within(root / _archive_name(item["result_path"]), root)
        if (_sha(raw) != item["raw_sha256"] or _sha(result_path) != item["result_sha256"] or
                pair["latest_result_sha256"] != item["result_sha256"]):
            raise ValueError("Bundled selected raw/result SHA differs")
        result = _read(result_path)
        with gzip.open(raw, "rt", encoding="utf-8") as stream:
            payload = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        scene = next(s for s in batch["manifest"]["scenarios"] if s["id"] == sid)
        if (payload.get("scenario") != scene or payload.get("identity", {}).get("scenario_id") != sid or
                payload.get("identity", {}).get("policy_id") != pid or result.get("scenario_id") != sid or
                result.get("policy_id") != pid or result.get("plan_signature") != batch["plan"]["signature"] or
                result.get("trajectory_sha256") != item["raw_sha256"] or result.get("status") != item["status"] or
                payload.get("summary", {}).get("status") != item["status"]):
            raise ValueError("Bundled selected raw identity differs")
        contract = batch["manifest"]["execution_contract"]
        key = digest(contract)
        if key not in environments:
            environments[key] = HospitalIESEnv(root, execution_contract=contract)
        check = rescore_selected(payload, result, batch["analysis"].get((sid, pid)), environments[key])
        checked.append(dict(batch=batch["name"], scenario_id=sid, policy_id=pid, **check))
    if sum(len(b["ledger"]) for b in batches) != package["registered_pair_count"]:
        raise ValueError("Bundled multi-batch denominator differs")
    return dict(integrity_passed=True, registered_pair_count=package["registered_pair_count"],
                batch_denominators={b["name"]: _denominators(b["ledger"]) for b in batches},
                raw_file_count=index["raw_file_count"], attempt_count=index["attempt_count"],
                selected_raw_rechecked=len(checked), targets_per_window=19, selected_rows=checked,
                audit_passed_by_batch={b["name"]: b["audit"].get("passed") is True for b in batches},
                full_physical_simulator_rerun=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    for name in ("run", "archive", "batches", "selection", "report"):
        build.add_argument("--" + name, type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    receipt = (build_bundle(args.run, args.archive, args.batches, args.selection, args.report)
               if args.command == "build" else verify_bundle(args.root))
    print(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
