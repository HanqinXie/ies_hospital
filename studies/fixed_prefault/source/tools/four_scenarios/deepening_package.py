"""Small, write-once v8 evidence bundle with exact selected-raw verification.

Full registered results remain external and are SHA-indexed.  Verification of
selected saved raw does not rerun an incident or establish clinical validity.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hospital_ies.dataset_manifest import digest
from tools.four_scenarios.package import (
    PHYSICAL_INPUTS,
    _batch_evidence,
    _pair_record,
    _source_entries,
    _verify_raw_index_against_audit,
    _verify_registered_sources,
    compare_scored_windows,
)


SCHEMA = "four_scenarios.deepening_portable.v1"
MANIFEST_NAME = "PACKAGE_MANIFEST.json"
INDEX_NAME = "EXTERNAL_RAW_SHA256_INDEX.json"
POLICIES = ("secure_milp", "priority_milp")
REQUIRED_CLASSES = ("N2", "N4", "L1", "L2", "L3", "L4a", "L4b")
FOUR_CELLS = ("N4", "L3", "L4a", "L4b")
ANALYSIS_CANDIDATES = ("analysis/TRAJECTORY_RESULTS.csv",
                       "analysis/SCENARIO_RESULTS.csv")
SHA_RE = re.compile(r"[0-9a-f]{64}")


def _read(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _name(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Unsafe archive member name")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise ValueError("Unsafe archive member name")
    return value


def _within(path: Path, root: Path) -> Path:
    target, base = Path(path).resolve(), Path(root).resolve()
    try:
        target.relative_to(base)
    except ValueError as error:
        raise ValueError("Path outside approved root: " + str(path)) from error
    return target


def _add(entries: dict[str, Path | bytes], name: str, source: Path | bytes) -> None:
    name = _name(name)
    if name in entries:
        if entries[name] != source:
            raise ValueError("Archive member collision: " + name)
        return
    if isinstance(source, Path) and (source.is_symlink() or not source.is_file()):
        raise ValueError("Unusable archive source: " + str(source))
    entries[name] = source


def _metadata(name: str, source: Path | bytes) -> dict:
    if isinstance(source, bytes):
        data = source
        return {"archive_path": name, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "crc32": zlib.crc32(data) & 0xffffffff}
    return {"archive_path": name, "bytes": source.stat().st_size,
            "sha256": _sha(source), "crc32": _crc(source)}


def _crc(path: Path) -> int:
    value = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value = zlib.crc32(block, value)
    return value & 0xffffffff


def create_archive(archive_path: str | Path, entries: dict[str, Path | bytes],
                   extra: dict) -> dict:
    """Create a new ZIP and SHA sidecar; never overwrite evidence."""
    archive = Path(archive_path).resolve()
    sidecar = archive.with_name(archive.name + ".sha256")
    if archive.exists() or sidecar.exists():
        raise FileExistsError(archive)
    if MANIFEST_NAME in entries:
        raise ValueError("Manifest member name reserved")
    checked = {_name(name): source for name, source in entries.items()}
    rows = [_metadata(name, checked[name]) for name in sorted(checked)]
    manifest = {"schema": SCHEMA, "entries": rows,
                "manifest_self_excluded_from_entries": True, **extra}
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as stream:
        for name in sorted(checked):
            source = checked[name]
            if isinstance(source, Path):
                stream.write(source, arcname=name)
            else:
                stream.writestr(name, source)
        stream.writestr(MANIFEST_NAME, _bytes(manifest))
    with zipfile.ZipFile(archive) as stream:
        if (stream.testzip() is not None or len(stream.namelist()) != len(set(stream.namelist())) or
                len(stream.namelist()) != len(rows) + 1):
            raise ValueError("ZIP CRC or member denominator differs")
        for row in rows:
            if hashlib.sha256(stream.read(row["archive_path"])).hexdigest() != row["sha256"]:
                raise ValueError("ZIP member SHA differs: " + row["archive_path"])
    sidecar.write_text(f"{_sha(archive)}  {archive.name}\n", encoding="ascii")
    return {"archive_path": str(archive), "archive_sha256": _sha(archive),
            "archive_bytes": archive.stat().st_size, "member_count": len(rows) + 1}


def verify_integrity(extracted_root: str | Path) -> dict:
    root = Path(extracted_root).resolve()
    manifest = _read(root / MANIFEST_NAME)
    if manifest.get("schema") != SCHEMA:
        raise ValueError("Unsupported v8 bundle schema")
    expected = set()
    for row in manifest["entries"]:
        name = _name(row["archive_path"])
        if name in expected:
            raise ValueError("Duplicate manifest member")
        expected.add(name)
        path = _within(root / name, root)
        if (not path.is_file() or path.stat().st_size != row["bytes"] or
                _sha(path) != row["sha256"] or _crc(path) != row["crc32"]):
            raise ValueError("Extracted member differs: " + name)
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
              and "__pycache__" not in path.parts and ".pytest_cache" not in path.parts}
    if actual != expected | {MANIFEST_NAME}:
        raise ValueError("Extracted file set differs from manifest")
    return manifest


def _analysis_map(rows: list[dict]) -> dict[tuple[str, str], dict]:
    found = {}
    for row in rows:
        key = (row["scenario_id"], row["policy_id"])
        if key in found:
            raise ValueError("Duplicate analysis pair")
        found[key] = row
    return found


def validate_selection(selection: dict, manifest: dict, plan: dict,
                       analysis_rows: list[dict]) -> dict:
    """Require paired class coverage, a matched four-cell group and both outcomes."""
    rows = selection.get("selected_pairs")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("Selection needs 1 to 32 small raw examples")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    allowed = {(sid, spec["policy_id"]) for sid in plan["scenario_ids"]
               for spec in plan["policy_specs"]}
    analysis = _analysis_map(analysis_rows)
    keys = []
    for item in rows:
        key = (item.get("scenario_id"), item.get("policy_id"))
        digest_value = item.get("expected_raw_sha256")
        if not isinstance(digest_value, str) or not SHA_RE.fullmatch(digest_value):
            raise ValueError("Selection expected_raw_sha256 must be exact SHA-256")
        if key not in allowed or key not in analysis or key[0] not in scenes:
            raise ValueError("Selected pair absent from registered analysis")
        if scenes[key[0]].get("is_final_blind") is not False:
            raise ValueError("Selected final or unclassified scene")
        if str(analysis[key].get("full_full_horizon", "")).lower() not in ("true", "1"):
            raise ValueError("Selected raw must have complete full horizon")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate selected pair")
    chosen = set(keys)
    for category in REQUIRED_CLASSES:
        sids = {scene["id"] for scene in scenes.values()
                if scene.get("analysis_class") == category}
        if not any({(sid, policy) for policy in POLICIES} <= chosen for sid in sids):
            raise ValueError("Each class needs both policies on one scenario: " + category)
    group_id = None
    for group in manifest.get("matched_four_cell_groups", []):
        ids = group.get("scene_ids", {})
        if all(category in ids for category in FOUR_CELLS) and all(
                (ids[category], policy) in chosen
                for category in FOUR_CELLS for policy in POLICIES):
            group_id = group.get("design_cell_id")
            break
    if group_id is None:
        raise ValueError("Selection lacks a paired four-cell group")
    failed = sum(int(float(analysis[key].get("full_P0_failure", 0))) == 1 for key in keys)
    if not 0 < failed < len(keys):
        raise ValueError("Selection requires failed and nonfailed P0 examples")
    return {"selected_raw_count": len(keys), "four_cell_group_id": group_id,
            "selected_class_count": {category: sum(scenes[sid]["analysis_class"] == category
                                                   for sid, _ in keys)
                                     for category in REQUIRED_CLASSES},
            "p0_failed_count": failed, "p0_nonfailed_count": len(keys) - failed}


def index_raw_attempts(run_dir: str | Path, ledger: dict) -> list[dict]:
    """Index each audited attempt, including retries, and reject unindexed raw."""
    run = Path(run_dir).resolve()
    result_root = run / "main/results"
    rows, known_paths = [], set()
    for (sid, pid), pair in sorted(ledger.items()):
        attempts = pair.get("attempts") or []
        if not attempts:
            raise ValueError("Audit has no raw attempts")
        used = set()
        for attempt in attempts:
            attempt_name = attempt["attempt"]
            if (not re.fullmatch(r"attempt_[0-9]+", attempt_name) or
                    attempt_name in used):
                raise ValueError("Invalid or repeated audited attempt")
            used.add(attempt_name)
            folder = result_root / "pairs" / digest([sid, pid]) / attempt_name
            raw, result_path = folder / "trajectory.json.gz", folder / "result.json"
            if not raw.is_file() or not result_path.is_file():
                raise FileNotFoundError("Audited raw or result absent: " + str(folder))
            if (_sha(raw) != attempt["raw_sha256"] or
                    _sha(result_path) != attempt["result_sha256"]):
                raise ValueError("Audited raw SHA or result SHA differs")
            result = _read(result_path)
            if (result.get("trajectory_path") != raw.name or
                    result.get("trajectory_sha256") != attempt["raw_sha256"] or
                    result.get("scenario_id") != sid or result.get("policy_id") != pid):
                raise ValueError("Audited raw/result identity differs")
            relative = _within(raw, run).relative_to(run).as_posix()
            if relative in known_paths:
                raise ValueError("Duplicate indexed raw path")
            known_paths.add(relative)
            rows.append({"scenario_id": sid, "policy_id": pid,
                         "raw_relative_to_original_run": relative,
                         "raw_bytes": raw.stat().st_size,
                         "raw_sha256": attempt["raw_sha256"],
                         "result_relative_to_original_run": _within(result_path, run).relative_to(run).as_posix(),
                         "result_sha256": attempt["result_sha256"],
                         "latest_audited": attempt["result_sha256"] == pair["latest_result_sha256"],
                         "included_in_package": False})
    actual = {_within(path, run).relative_to(run).as_posix()
              for path in result_root.rglob("trajectory.json.gz")}
    if actual != known_paths:
        raise ValueError("Unindexed actual raw or missing audited raw")
    rows.sort(key=lambda row: row["raw_relative_to_original_run"])
    _verify_raw_index_against_audit(rows, ledger)
    return rows


def compare_analysis_subset(payload: dict, analysis_row: dict) -> dict:
    """Independently integrate selected P0/P3 raw and count real GT trips."""
    summary = payload["summary"]
    observed, requested = (float(summary[key]) for key in
                           ("observed_until_minute", "requested_end_minute"))
    if observed + 1e-6 < requested:
        raise ValueError("Selected analysis raw has unknown tail")
    ens = [[0.0] * 3 for _ in range(4)]
    failure_minutes = 0.0
    cursor = 0.0
    for segment in payload["segments"]:
        start, end = float(segment["start_minute"]), float(segment["end_minute"])
        if abs(start - cursor) > 1e-6 or end <= start or end > requested + 1e-6:
            raise ValueError("Raw subsegments are not contiguous")
        cursor = end
        loss = segment["unserved_kW"]
        if len(loss) != 4 or any(len(row) != 3 for row in loss):
            raise ValueError("Raw service carrier shape differs")
        for priority in range(4):
            for carrier in range(3):
                ens[priority][carrier] += float(loss[priority][carrier]) * (end - start) / 60
        if segment["classification"]["p0_guarantee_failure"]:
            failure_minutes += end - start
    if abs(cursor - requested) > 1e-6:
        raise ValueError("Raw subsegments miss requested endpoint")
    values = {"full_P0_failure": int(failure_minutes > 0),
              "full_P0_failure_minutes": failure_minutes,
              "GT_protection_trip_count_observed": sum(
                  event.get("kind") == "protection_trip" and
                  str(event.get("device", "")).startswith("GT_") and
                  0 <= float(event["time_minute"]) < requested
                  for event in payload.get("events", []))}
    for p, label in ((0, "P0"), (3, "P3")):
        for carrier, letter in enumerate("ehc"):
            values[f"full_{label}_{letter}_ENS_kWh"] = ens[p][carrier]
    for key, value in values.items():
        if key in analysis_row and analysis_row[key] not in (None, ""):
            expected = float(analysis_row[key])
            if not math.isfinite(expected) or not math.isclose(
                    float(value), expected, rel_tol=1e-9, abs_tol=1e-7):
                raise ValueError("Saved raw differs from analysis subset: " + key)
    return {"analysis_subset_match": True, "checked_fields": values}


def _analysis_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _analysis_path(root: Path, run: Path, selection: dict, *,
                   allow_external: bool = False) -> Path:
    declared = selection.get("analysis_file")
    if declared is not None:
        name = _name(declared)
        source = root / name if name.startswith("outputs/") else run / name
    else:
        source = next((run / name for name in ANALYSIS_CANDIDATES
                       if (run / name).is_file()), run / ANALYSIS_CANDIDATES[0])
    source = _within(source, root if allow_external else run)
    if (not source.is_file() or source.suffix.lower() != ".csv" or
            not (source.name.endswith("TRAJECTORY_RESULTS.csv") or
                 source.name == "SCENARIO_RESULTS.csv")):
        raise FileNotFoundError("Registered trajectory analysis absent: " + str(source))
    return source


def choose_reproduction_pairs(manifest: dict, plan: dict,
                              analysis_rows: list[dict]) -> list[dict]:
    """Deterministically cover one matched four-cell group and all seven classes."""
    analysis = _analysis_map(analysis_rows)
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    registered = set(plan["scenario_ids"])

    def interest(sid: str):
        rows = [analysis[(sid, pid)] for pid in POLICIES]
        failures = [int(float(row["full_P0_failure"])) for row in rows]
        trips = [float(row.get("GT_protection_trip_count_observed") or 0)
                 for row in rows]
        p3 = [sum(float(row.get(f"full_P3_{carrier}_ENS_kWh") or 0)
                  for carrier in "ehc") for row in rows]
        return (int(0 in failures and 1 in failures), int(1 in failures),
                int(any(trips)), int(abs(p3[0] - p3[1]) > 1e-7),
                sum(failures), sum(trips))

    eligible = {sid for sid in registered if sid in scenes and all(
        (sid, pid) in analysis and
        str(analysis[(sid, pid)].get("full_full_horizon", "")).lower() in ("true", "1") and
        str(analysis[(sid, pid)].get("full_P0_failure", "")) in ("0", "1", "0.0", "1.0")
        for pid in POLICIES)}
    group_options = []
    for group in manifest.get("matched_four_cell_groups", []):
        cells = group.get("scene_ids", {})
        if all(name in cells and cells[name] in eligible for name in FOUR_CELLS):
            sids = [cells[name] for name in FOUR_CELLS]
            score = tuple(sum(interest(sid)[i] for sid in sids) for i in range(6))
            group_options.append((score, str(group.get("design_cell_id", "")), sids))
    if not group_options:
        raise ValueError("No complete audited four-cell group for reproduction")
    group_options.sort(key=lambda value: (value[0], value[1]), reverse=True)
    selected_sids = list(group_options[0][2])
    for category in REQUIRED_CLASSES:
        if category in FOUR_CELLS:
            continue
        choices = [sid for sid in eligible if scenes[sid].get("analysis_class") == category]
        if not choices:
            raise ValueError("No complete paired raw for class: " + category)
        choices.sort(key=lambda sid: (interest(sid), sid), reverse=True)
        selected_sids.append(choices[0])

    def selected_failure_values():
        return {int(float(analysis[(sid, pid)]["full_P0_failure"]))
                for sid in selected_sids for pid in POLICIES}

    if selected_failure_values() != {0, 1}:
        missing = ({0, 1} - selected_failure_values()).pop()
        choices = [sid for sid in eligible if sid not in selected_sids and any(
            int(float(analysis[(sid, pid)]["full_P0_failure"])) == missing
            for pid in POLICIES)]
        if not choices:
            raise ValueError("No complete paired raw for failed/nonfailed P0 example")
        choices.sort(key=lambda sid: (interest(sid), sid), reverse=True)
        selected_sids.append(choices[0])
    if not any(interest(sid)[2] for sid in selected_sids):
        choices = [sid for sid in eligible if sid not in selected_sids and interest(sid)[2]]
        if choices:
            choices.sort(key=lambda sid: (interest(sid), sid), reverse=True)
            selected_sids.append(choices[0])
    if len(selected_sids) * len(POLICIES) > 32:
        raise ValueError("Small raw selection exceeded 32 examples")
    return [{"scenario_id": sid, "policy_id": pid}
            for sid in selected_sids for pid in POLICIES]


def write_repro_selection(run_dir: str | Path, output_path: str | Path, *,
                          workspace_root: str | Path = ROOT) -> dict:
    """Create exact, write-once SHA selection after full audited analysis."""
    root = Path(workspace_root).resolve()
    run = _within(Path(run_dir), root)
    output = _within(Path(output_path), run)
    if output.exists():
        raise FileExistsError(output)
    manifest_path = run / "new_data/global_manifest.json"
    plan_path = run / "main/registered_plan.json"
    audit_path = run / "main/AUDIT.json"
    manifest, plan, audit, ledger = _batch_evidence(
        manifest_path, plan_path, audit_path, required_policies=POLICIES)
    analysis_path = _analysis_path(root, run, {})
    analysis_rows = _analysis_rows(analysis_path)
    if set(_analysis_map(analysis_rows)) != set(ledger):
        raise ValueError("Selection analysis denominator differs from audit")
    pairs = choose_reproduction_pairs(manifest, plan, analysis_rows)
    indexed = index_raw_attempts(run, ledger)
    latest = {(row["scenario_id"], row["policy_id"]): row for row in indexed
              if row["latest_audited"]}
    selected = [{**item, "expected_raw_sha256":
                 latest[(item["scenario_id"], item["policy_id"])]["raw_sha256"]}
                for item in pairs]
    document = {"schema": SCHEMA + ".selection", "decision_rule":
                "deterministic_complete_four_cell_all_classes_p0_outcomes_gt_if_available",
                "manifest_sha256": _sha(manifest_path),
                "plan_sha256": _sha(plan_path), "audit_sha256": _sha(audit_path),
                "analysis_file": analysis_path.relative_to(run).as_posix(),
                "analysis_sha256": _sha(analysis_path),
                "selected_pairs": selected}
    roles = validate_selection(document, manifest, plan, analysis_rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(_bytes(document))
    return {"selection_path": str(output), "selection_sha256": _sha(output),
            **roles}


def write_supplement_selection(supplement_dir: str | Path,
                               analysis_path: str | Path,
                               output_path: str | Path, *,
                               workspace_root: str | Path = ROOT) -> dict:
    """Select a few complete paired supplement raw with exact independent SHA."""
    root = Path(workspace_root).resolve()
    run = _within(Path(supplement_dir), root)
    analysis_path = _within(Path(analysis_path), root)
    output = _within(Path(output_path), run)
    if output.exists():
        raise FileExistsError(output)
    manifest_path = run / "new_data/global_manifest.json"
    plan_path = run / "main/registered_plan.json"
    audit_path = run / "main/AUDIT.json"
    manifest, plan, audit, ledger = _batch_evidence(
        manifest_path, plan_path, audit_path, required_policies=POLICIES)
    rows = _analysis_rows(analysis_path)
    analysis = _analysis_map(rows)
    if set(analysis) != set(ledger):
        raise ValueError("Supplement selection analysis denominator differs")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    eligible = [sid for sid in plan["scenario_ids"] if all(
        str(analysis[(sid, pid)].get("full_full_horizon", "")).lower() in ("true", "1")
        for pid in POLICIES)]
    if not eligible:
        raise ValueError("Supplement has no complete paired raw for reproduction")

    def interest(sid: str):
        pair_rows = [analysis[(sid, pid)] for pid in POLICIES]
        failures = [int(float(row.get("full_P0_failure") or 0)) for row in pair_rows]
        trips = [float(row.get("GT_protection_trip_count_observed") or 0)
                 for row in pair_rows]
        return (int(0 in failures and 1 in failures), int(1 in failures),
                int(any(trips)), sum(failures), sum(trips))

    selected_sids = []
    for category in sorted({scenes[sid]["analysis_class"] for sid in eligible}):
        candidates = sorted(sid for sid in eligible
                            if scenes[sid]["analysis_class"] == category)
        candidates.sort(key=interest, reverse=True)
        selected_sids.append(candidates[0])
    if not any(interest(sid)[1] for sid in selected_sids):
        candidates = sorted(sid for sid in eligible if sid not in selected_sids
                            and interest(sid)[1])
        if candidates:
            candidates.sort(key=interest, reverse=True)
            selected_sids.append(candidates[0])
    if not any(interest(sid)[2] for sid in selected_sids):
        candidates = sorted(sid for sid in eligible if sid not in selected_sids
                            and interest(sid)[2])
        if candidates:
            candidates.sort(key=interest, reverse=True)
            selected_sids.append(candidates[0])
    if len(selected_sids) > 8:
        raise ValueError("Supplement selected more than 16 small raw")
    indexed = index_raw_attempts(run, ledger)
    latest = {(row["scenario_id"], row["policy_id"]): row for row in indexed
              if row["latest_audited"]}
    selected = [{"scenario_id": sid, "policy_id": pid,
                 "expected_raw_sha256": latest[(sid, pid)]["raw_sha256"]}
                for sid in selected_sids for pid in POLICIES]
    document = {"schema": SCHEMA + ".supplement_selection",
                "decision_rule": "one_complete_pair_per_class_plus_failure_and_gt_if_available",
                "manifest_sha256": _sha(manifest_path),
                "plan_sha256": _sha(plan_path), "audit_sha256": _sha(audit_path),
                "analysis_file": analysis_path.relative_to(root).as_posix(),
                "analysis_sha256": _sha(analysis_path),
                "selected_pairs": selected}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(_bytes(document))
    return {"selection_path": str(output), "selection_sha256": _sha(output),
            "selected_raw_count": len(selected),
            "selected_class_count": len({scenes[sid]["analysis_class"]
                                         for sid in selected_sids})}


def _readme(supplements: list[dict] | None = None) -> bytes:
    contents = ("# Hospital IES v8 selected-raw reproduction\n\n"
            "This is a small evidence bundle, not the complete 1056-trajectory run. "
            "Every registered raw attempt is listed in EXTERNAL_RAW_SHA256_INDEX.json "
            "or its independent supplement index; "
            "only examples selected by evidence/REPRO_SELECTION.json are embedded. "
            "The full design proposal, manifest, registered plan, audit, analysis tables, "
            "report, figure source CSV, runtime inputs, execution contract, requirements "
            "and Python source are copied with byte hashes. Registered literature PDF "
            "provenance may be indexed externally where it is not a runtime dependency.\n\n"
            "Install requirements.txt in a compatible Python environment, with the "
            "recorded gurobipy version in the manifest if needed for scorer imports. "
            "From the extracted directory run:\n\n"
            "    python -B -m tools.four_scenarios.deepening_package verify --root .\n\n"
            "The verifier checks every bundled byte, exact selected/raw audit identities, "
            "the original two 19-target score windows and a direct selected-raw P0/P3 "
            "service/protection subset. It does not run the physical simulator, audit "
            "external raw bytes anew, validate hospital equipment or establish clinical "
            "safety. Some v7 mechanism figures may reference v7 raw listed only by SHA.\n")
    if supplements:
        contents += ("\n## Supplement path map\n\n"
                     "The main report is stored at `run/FOUR_SCENARIOS_DEEPENING_REPORT.md`. "
                     "Its workspace-relative supplement references must be mapped to "
                     "the selected evidence in this extracted archive as follows. "
                     "Portable paths are relative to that report; workspace paths "
                     "starting with `../` are report-relative, and those starting with "
                     "`outputs/` are workspace-root-relative. They are text, not "
                     "cross-environment Markdown links.\n\n")
        for record in supplements:
            sid = record["supplement_id"]
            original = "../" + sid
            portable = "../supplements/" + sid
            source_run = record["source_run_relative_to_workspace"].rstrip("/") + "/"
            source_analysis = record["source_analysis_relative_to_workspace"]
            in_supplement = source_analysis.startswith(source_run)
            analysis_rel = source_analysis.removeprefix(source_run)
            archive_analysis = record["final_analysis_file"]
            archive_prefix = "supplements/" + sid + "/run/"
            archive_analysis_rel = archive_analysis.removeprefix(archive_prefix)
            if archive_analysis_rel == archive_analysis:
                raise ValueError("Supplement analysis path cannot be mapped")
            if in_supplement:
                source_analysis_display = original + "/" + analysis_rel
                source_analysis_dir = original + "/" + PurePosixPath(analysis_rel).parent.as_posix()
            else:
                source_analysis_display = source_analysis
                source_analysis_dir = PurePosixPath(source_analysis).parent.as_posix()
            archive_analysis_dir = PurePosixPath(archive_analysis_rel).parent.as_posix()
            contents += (f"### {sid}\n\n"
                         f"- Manifest: `{original}/new_data/global_manifest.json` → "
                         f"`{portable}/evidence/global_manifest.json`\n"
                         f"- Audit: `{original}/main/AUDIT.json` → "
                         f"`{portable}/evidence/AUDIT.json`\n"
                         f"- Final analysis: `{source_analysis_display}` → "
                         f"`{portable}/run/{archive_analysis_rel}`\n")
            if in_supplement:
                contents += (f"- Other saved analysis tables: `{source_analysis_dir}/` "
                             f"→ `{portable}/run/{archive_analysis_dir}/`\n")
            contents += "\n"
    return contents.encode("utf-8")


def prepare_supplement(supplement_dir: str | Path, *,
                       workspace_root: str | Path = ROOT) -> tuple[dict[str, Path | bytes], dict]:
    """Validate one independent registered supplement and stage selected raw.

    Its manifest/plan/audit and raw denominator are separate from the main v8
    batch; no supplementary scene is inserted into the frozen main manifest.
    """
    root = Path(workspace_root).resolve()
    run = _within(Path(supplement_dir), root)
    supplement_id = run.name
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", supplement_id):
        raise ValueError("Unsafe supplement id")
    base = "supplements/" + supplement_id
    manifest_path = run / "new_data/global_manifest.json"
    plan_path = run / "main/registered_plan.json"
    audit_path = run / "main/AUDIT.json"
    selection_path = run / "review/REPRO_SELECTION.json"
    ledger_path = run / "RUN_LEDGER.jsonl"
    manifest, plan, audit, ledger = _batch_evidence(
        manifest_path, plan_path, audit_path, required_policies=POLICIES)
    if not str(manifest.get("manifest_version", "")).startswith("four_scenarios.v8."):
        raise ValueError("Supplement must have an independent v8 manifest")
    _verify_registered_sources(plan, root)
    if not ledger_path.is_file():
        raise FileNotFoundError("Supplement run ledger absent: " + str(ledger_path))
    selection = _read(selection_path)
    analysis_path = _analysis_path(root, run, selection, allow_external=True)
    analysis_rows = _analysis_rows(analysis_path)
    analysis = _analysis_map(analysis_rows)
    if set(analysis) != set(ledger):
        raise ValueError("Supplement analysis denominator differs from audit")
    if (selection.get("manifest_sha256") != _sha(manifest_path) or
            selection.get("audit_sha256") != _sha(audit_path) or
            selection.get("analysis_sha256") != _sha(analysis_path)):
        raise ValueError("Supplement selection SHA binding differs")
    requested = selection.get("selected_pairs")
    if not isinstance(requested, list) or not 2 <= len(requested) <= 16:
        raise ValueError("Supplement needs 2 to 16 selected raw")
    selected_keys = set()
    for item in requested:
        sid, pid, sha = (item.get("scenario_id"), item.get("policy_id"),
                         item.get("expected_raw_sha256"))
        key = (sid, pid)
        if (key not in ledger or key not in analysis or key in selected_keys or
                pid not in POLICIES or not isinstance(sha, str) or
                not SHA_RE.fullmatch(sha) or
                str(analysis[key].get("full_full_horizon", "")).lower() not in ("true", "1")):
            raise ValueError("Invalid or incomplete selected supplement pair")
        selected_keys.add(key)
    if not any({(sid, pid) for pid in POLICIES} <= selected_keys
               for sid in plan["scenario_ids"]):
        raise ValueError("Supplement selection needs a same-scene policy pair")
    external = index_raw_attempts(run, ledger)
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    latest = {(row["scenario_id"], row["policy_id"]): row
              for row in external if row["latest_audited"]}
    entries: dict[str, Path | bytes] = {}
    for name in plan["source_sha256"]:
        _add(entries, _name(name), _within(root / name, root))
    for row in manifest.get("files", []):
        name = _name(row["path"])
        source = _within(root / name, root)
        if (not source.is_file() or source.stat().st_size != row["bytes"] or
                _sha(source) != row["sha256"]):
            raise ValueError("Supplement registered input SHA differs: " + name)
        if not name.startswith("inputs/literature/"):
            _add(entries, name, source)
    for name, expected in manifest.get("four_scenario_source_sha256", {}).items():
        source = _within(root / _name(name), root)
        if not source.is_file() or _sha(source) != expected:
            raise ValueError("Supplement design source SHA differs: " + name)
        _add(entries, name, source)
    for name, source in (("evidence/global_manifest.json", manifest_path),
                         ("evidence/registered_plan.json", plan_path),
                         ("evidence/AUDIT.json", audit_path),
                         ("evidence/REPRO_SELECTION.json", selection_path),
                         ("run/RUN_LEDGER.jsonl", ledger_path)):
        _add(entries, base + "/" + name, source)
    try:
        analysis_relative = analysis_path.relative_to(run).as_posix()
    except ValueError:
        analysis_relative = "analysis/" + analysis_path.name
    packaged_analysis_path = base + "/run/" + analysis_relative
    _add(entries, packaged_analysis_path, analysis_path.read_bytes())
    folders = [folder for folder in sorted(run.iterdir()) if folder.is_dir() and
               (folder.name == "analysis" or folder.name.startswith("analysis_") or
                folder.name in ("design", "figures", "review", "logs"))]
    for folder in folders:
        if folder.is_dir():
            for source in sorted(folder.rglob("*")):
                if source.is_symlink():
                    raise ValueError("Symlinked supplement artifact prohibited")
                if source.is_file():
                    relative = _within(source, run).relative_to(run).as_posix()
                    _add(entries, base + "/run/" + relative, source.read_bytes())
    for source in sorted(run.glob("*.md")):
        _add(entries, base + "/run/" + source.name, source)
    examples = []
    for item in requested:
        sid, pid = item["scenario_id"], item["policy_id"]
        result, payload, result_path, raw_path = _pair_record(
            run / "main/results", sid, pid, scenes[sid], ledger[(sid, pid)], load_raw=True)
        index_row = latest[(sid, pid)]
        if (_sha(raw_path) != item["expected_raw_sha256"] or
                result.get("plan_signature") != plan["signature"] or
                _sha(result_path) != index_row["result_sha256"] or
                _sha(raw_path) != index_row["raw_sha256"]):
            raise ValueError("Supplement selected raw SHA or signature differs")
        compare_analysis_subset(payload, analysis[(sid, pid)])
        index_row["included_in_package"] = True
        raw_arc = (base + f"/examples/{sid}/{pid}/{result_path.parent.name}/"
                   "trajectory.json.gz")
        result_arc = raw_arc[:-len("trajectory.json.gz")] + "result.json"
        _add(entries, raw_arc, raw_path)
        _add(entries, result_arc, result_path)
        examples.append({"scenario_id": sid, "policy_id": pid,
                         "analysis_class": scenes[sid]["analysis_class"],
                         "root_family_id": scenes[sid]["root_family_id"],
                         "status": result["status"], "raw_path": raw_arc,
                         "raw_sha256": _sha(raw_path), "result_path": result_arc,
                         "result_sha256": _sha(result_path)})
    _add(entries, base + "/" + INDEX_NAME,
         _bytes({"schema": SCHEMA + ".supplement_raw_index",
                 "supplement_id": supplement_id,
                 "run_relative_to_workspace": run.relative_to(root).as_posix(),
                 "plan_sha256": _sha(plan_path), "audit_sha256": _sha(audit_path),
                 "registered_pair_count": len(ledger),
                 "raw_file_count": len(external), "rows": external}))
    receipt = {"supplement_id": supplement_id, "registered_pair_count": len(ledger),
               "external_raw_file_count": len(external),
               "selected_raw_count": len(examples), "examples": examples,
               "manifest_sha256": _sha(manifest_path), "plan_sha256": _sha(plan_path),
               "audit_sha256": _sha(audit_path), "analysis_sha256": _sha(analysis_path),
               "analysis_file": packaged_analysis_path,
               "final_analysis_file": packaged_analysis_path,
               "source_analysis_relative_to_workspace":
                   analysis_path.relative_to(root).as_posix(),
               "selection_sha256": _sha(selection_path),
               "source_v8_manifest_sha256":
                   manifest.get("source_v8_manifest_sha256"),
               "source_run_relative_to_workspace": run.relative_to(root).as_posix()}
    return entries, receipt


def verify_supplement(extracted_root: str | Path, receipt: dict,
                      *, env=None) -> dict:
    """Check an extracted supplement; env enables original scorer recomputation."""
    root = Path(extracted_root).resolve()
    supplement_id = receipt["supplement_id"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", supplement_id):
        raise ValueError("Unsafe supplement id")
    base = "supplements/" + supplement_id
    paths = {"manifest_sha256": base + "/evidence/global_manifest.json",
             "plan_sha256": base + "/evidence/registered_plan.json",
             "audit_sha256": base + "/evidence/AUDIT.json",
             "analysis_sha256": _name(receipt["final_analysis_file"]),
             "selection_sha256": base + "/evidence/REPRO_SELECTION.json"}
    for key, name in paths.items():
        if _sha(_within(root / name, root)) != receipt[key]:
            raise ValueError("Supplement evidence SHA differs: " + key)
    if receipt.get("analysis_file") != receipt["final_analysis_file"]:
        raise ValueError("Supplement final analysis identity differs")
    manifest_path, plan_path, audit_path = (root / paths[key] for key in
                                            ("manifest_sha256", "plan_sha256", "audit_sha256"))
    manifest, plan, audit, ledger = _batch_evidence(
        manifest_path, plan_path, audit_path, required_policies=POLICIES)
    if manifest.get("source_v8_manifest_sha256") != receipt.get(
            "source_v8_manifest_sha256"):
        raise ValueError("Supplement main-manifest source binding differs")
    _verify_registered_sources(plan, root)
    for row in manifest.get("files", []):
        name = _name(row["path"])
        path = root / name
        if name.startswith("inputs/literature/"):
            if path.exists():
                if path.stat().st_size != row["bytes"] or _sha(path) != row["sha256"]:
                    raise ValueError("Supplement literature SHA differs: " + name)
        elif path.stat().st_size != row["bytes"] or _sha(path) != row["sha256"]:
            raise ValueError("Supplement input SHA differs: " + name)
    for name, expected in manifest.get("four_scenario_source_sha256", {}).items():
        if _sha(_within(root / _name(name), root)) != expected:
            raise ValueError("Supplement design source SHA differs: " + name)
    rows = _analysis_rows(root / paths["analysis_sha256"])
    analysis = _analysis_map(rows)
    if set(analysis) != set(ledger) or len(ledger) != receipt["registered_pair_count"]:
        raise ValueError("Supplement analysis/audit denominator differs")
    selection = _read(root / paths["selection_sha256"])
    if (selection.get("manifest_sha256") != receipt["manifest_sha256"] or
            selection.get("audit_sha256") != receipt["audit_sha256"] or
            selection.get("analysis_sha256") != receipt["analysis_sha256"]):
        raise ValueError("Supplement selection SHA binding differs")
    selected_keys = {(item["scenario_id"], item["policy_id"])
                     for item in selection["selected_pairs"]}
    examples = receipt["examples"]
    example_keys = {(item["scenario_id"], item["policy_id"]) for item in examples}
    if (selected_keys != example_keys or len(examples) != len(example_keys) or
            len(examples) != receipt["selected_raw_count"] or
            not any({(sid, pid) for pid in POLICIES} <= example_keys
                    for sid in plan["scenario_ids"])):
        raise ValueError("Supplement selected raw roles differ")
    expected_sha = {(item["scenario_id"], item["policy_id"]):
                    item["expected_raw_sha256"] for item in selection["selected_pairs"]}
    index = _read(root / base / INDEX_NAME)
    if (index.get("schema") != SCHEMA + ".supplement_raw_index" or
            index.get("supplement_id") != supplement_id or
            index.get("plan_sha256") != receipt["plan_sha256"] or
            index.get("audit_sha256") != receipt["audit_sha256"] or
            index.get("registered_pair_count") != len(ledger) or
            index.get("raw_file_count") != receipt["external_raw_file_count"] or
            len(index.get("rows", [])) != receipt["external_raw_file_count"]):
        raise ValueError("Supplement external raw index header differs")
    _verify_raw_index_against_audit(index["rows"], ledger)
    indexed_keys = {(row["scenario_id"], row["policy_id"])
                    for row in index["rows"] if row["included_in_package"]}
    if (indexed_keys != example_keys or
            sum(row["latest_audited"] for row in index["rows"]) != len(ledger)):
        raise ValueError("Supplement external raw index selected set differs")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    checked = []
    for item in examples:
        sid, pid = item["scenario_id"], item["policy_id"]
        raw_path = _within(root / _name(item["raw_path"]), root)
        result_path = _within(root / _name(item["result_path"]), root)
        if (_sha(raw_path) != item["raw_sha256"] or
                _sha(result_path) != item["result_sha256"] or
                expected_sha[(sid, pid)] != item["raw_sha256"] or
                ledger[(sid, pid)]["latest_result_sha256"] != item["result_sha256"] or
                item["analysis_class"] != scenes[sid]["analysis_class"] or
                item["root_family_id"] != scenes[sid]["root_family_id"] or
                scenes[sid].get("is_final_blind") is not False):
            raise ValueError("Supplement selected raw audit identity differs")
        result = _read(result_path)
        with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream, parse_constant=lambda value:
                                (_ for _ in ()).throw(ValueError(value)))
        if (result.get("scenario_id") != sid or result.get("policy_id") != pid or
                result.get("plan_signature") != plan["signature"] or
                result.get("trajectory_sha256") != item["raw_sha256"] or
                result.get("status") != item["status"] or
                payload.get("scenario") != scenes[sid] or
                payload.get("identity", {}).get("scenario_id") != sid or
                payload.get("identity", {}).get("policy_id") != pid or
                payload.get("summary", {}).get("status") != item["status"]):
            raise ValueError("Supplement selected raw/result identity differs")
        subset = compare_analysis_subset(payload, analysis[(sid, pid)])
        if env is not None:
            from tools.control_improvement.branches import window_targets
            primary = window_targets(payload, 60.0, 360.0, env)
            full = window_targets(payload, 0.0, 600.0, env)
            compare_scored_windows(result, primary, full)
        checked.append({"scenario_id": sid, "policy_id": pid,
                        "raw_sha256": item["raw_sha256"],
                        "analysis_subset_match": subset["analysis_subset_match"],
                        "score_rechecked": env is not None})
    return {"supplement_id": supplement_id,
            "registered_pair_count": len(ledger),
            "external_raw_indexed": len(index["rows"]),
            "selected_raw_rechecked": len(checked), "selected_rows": checked}


def write_command_ledger(run_dirs: dict[str, str | Path], output_path: str | Path,
                         *, extra_records: list[dict] | None = None) -> dict:
    """Preserve every recorded attempt, including nonzero exits, in a short review."""
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError(output)
    lines = ["# V8 command ledger", "",
             "Run IDs refer to the corresponding full `RUN_LEDGER.jsonl` argv and "
             "SHA-bound stdout/stderr logs. A completed command is not a scientific pass.", ""]
    count, failed, unfinished = 0, 0, 0
    for scope, location in run_dirs.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", scope):
            raise ValueError("Unsafe command-ledger scope")
        run = Path(location).resolve()
        ledger_path = run / "RUN_LEDGER.jsonl"
        records = [json.loads(line, parse_constant=lambda value:
                   (_ for _ in ()).throw(ValueError(value)))
                   for line in ledger_path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        starts, terminals = {}, {}
        for record in records:
            run_id = record["run_id"]
            if record["event"] == "started":
                if run_id in starts:
                    raise ValueError("Duplicate command start: " + run_id)
                starts[run_id] = record
            elif record["event"] in ("finished", "launcher_exception"):
                if run_id in terminals:
                    raise ValueError("Duplicate command terminal: " + run_id)
                terminals[run_id] = record
        if not starts or not set(terminals) <= set(starts):
            raise ValueError("Command ledger has missing starts")
        lines.extend([f"## {scope}", "",
                      f"Source: `{ledger_path}`; SHA-256 `{_sha(ledger_path)}`.", "",
                      "| Stage | Run ID | Exit | Evidence |",
                      "| --- | --- | --- | --- |"])
        for run_id, start in starts.items():
            terminal = terminals.get(run_id)
            stage = start.get("name", "unspecified")
            if terminal is None:
                code, evidence = "unfinished", "No terminal ledger record"
                unfinished += 1
            elif terminal["event"] == "launcher_exception":
                code = "launcher exception"
                evidence = str(terminal.get("error", ""))[:180]
                failed += 1
            else:
                value = terminal.get("exit_code")
                if not isinstance(value, int):
                    raise ValueError("Command terminal exit code absent")
                code = f"exit {value}"
                if value:
                    failed += 1
                stdout = Path(start["stdout"])
                stderr = Path(start["stderr"])
                if (_sha(stdout) != terminal["stdout_sha256"] or
                        _sha(stderr) != terminal["stderr_sha256"]):
                    raise ValueError("Recorded command log SHA differs: " + run_id)
                evidence = ("logs/" + stdout.name + " SHA " +
                            terminal["stdout_sha256"][:12] + ", logs/" + stderr.name +
                            " SHA " + terminal["stderr_sha256"][:12])
                if value:
                    error_lines = [line.strip() for line in
                                   stderr.read_text(encoding="utf-8", errors="replace").splitlines()
                                   if line.strip()]
                    if error_lines:
                        evidence += "; " + error_lines[-1][:180]
            evidence = evidence.replace("|", "\\|").replace("`", "'")
            lines.append(f"| {stage} | `{run_id}` | {code} | {evidence} |")
            count += 1
        lines.append("")
    if extra_records:
        lines.extend(["## Direct commands", "",
                      "Commands executed outside the JSONL runner retain their reported "
                      "exit code and output artifact evidence.", "",
                      "| Scope | Stage | Exit | Command and evidence |",
                      "| --- | --- | --- | --- |"])
        for item in extra_records:
            value = item.get("exit_code")
            if not isinstance(value, int) or not item.get("command"):
                raise ValueError("Direct command needs exact argv/text and exit code")
            if value:
                failed += 1
            evidence = (str(item["command"]) + "; " + str(item.get("evidence", "")))
            evidence = evidence.replace("|", "\\|").replace("`", "'")
            lines.append(f"| {item['scope']} | {item['stage']} | exit {value} | {evidence} |")
            count += 1
        lines.append("")
    lines.extend([f"Recorded commands: {count}; nonzero or launcher failures: {failed}; "
                  f"without terminal record: {unfinished}.", ""])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines))
    return {"path": str(output), "sha256": _sha(output),
            "recorded_command_count": count, "nonzero_or_launcher_failures": failed,
            "unterminated_command_count": unfinished}


def build_bundle(run_dir: str | Path, archive_path: str | Path,
                 selection_path: str | Path, report_path: str | Path,
                 *, workspace_root: str | Path = ROOT,
                 supplements: list[str | Path] | None = None) -> dict:
    """Build the v8 archive after its full audited run and report are complete."""
    root = Path(workspace_root).resolve()
    run = _within(Path(run_dir), root)
    archive = Path(archive_path).resolve()
    if not run.name.startswith("v8_four_scenarios_deepening_"):
        raise ValueError("Bundle requires a versioned v8 deepening run")
    if archive.parent != (run / "delivery").resolve():
        raise ValueError("V8 archive must be written in run/delivery")
    selection_path = _within(Path(selection_path), run)
    report_path = _within(Path(report_path), run)
    if not report_path.is_file() or report_path.suffix.lower() != ".md":
        raise FileNotFoundError("Final Markdown report absent")
    if not (run / "review/COMMAND_LEDGER.md").is_file():
        raise FileNotFoundError("Final command ledger absent")
    manifest_path = run / "new_data/global_manifest.json"
    plan_path = run / "main/registered_plan.json"
    audit_path = run / "main/AUDIT.json"
    manifest, plan, audit, ledger = _batch_evidence(
        manifest_path, plan_path, audit_path, required_policies=POLICIES)
    if not str(manifest.get("manifest_version", "")).startswith("four_scenarios.v8."):
        raise ValueError("Expected registered v8 source manifest")
    _verify_registered_sources(plan, root)
    proposal = run / "design/SOURCE_PROPOSAL.json"
    if (not proposal.is_file() or
            _sha(proposal) != manifest.get("four_scenario_proposal_file_sha256")):
        raise ValueError("V8 source proposal SHA differs")
    selection = _read(selection_path)
    analysis_path = _analysis_path(root, run, selection)
    if not analysis_path.is_file():
        raise FileNotFoundError("Full v8 analysis absent")
    analysis_rows = _analysis_rows(analysis_path)
    analysis = _analysis_map(analysis_rows)
    if set(analysis) != set(ledger):
        raise ValueError("Full analysis denominator differs from audited plan")
    if (selection.get("manifest_sha256") != _sha(manifest_path) or
            selection.get("audit_sha256") != _sha(audit_path) or
            selection.get("analysis_sha256") != _sha(analysis_path)):
        raise ValueError("Selected raw evidence SHA binding differs")
    roles = validate_selection(selection, manifest, plan, analysis_rows)
    external = index_raw_attempts(run, ledger)
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    entries: dict[str, Path | bytes] = {}
    for name, source in _source_entries(root).items():
        _add(entries, name, source)
    external_inputs = []
    for row in manifest.get("files", []):
        name = _name(row["path"])
        source = _within(root / name, root)
        if (not source.is_file() or source.stat().st_size != row["bytes"] or
                _sha(source) != row["sha256"]):
            raise ValueError("Registered source/input SHA differs: " + name)
        if name.startswith("inputs/literature/"):
            external_inputs.append({"path": name, "bytes": row["bytes"],
                                    "sha256": row["sha256"], "packaged": False,
                                    "reason": "literature_not_runtime_score_dependency"})
        else:
            _add(entries, name, source)
    for name in PHYSICAL_INPUTS:
        _add(entries, name, _within(root / name, root))
    if _read(root / "configs/p1b_execution_research.json") != manifest["execution_contract"]:
        raise ValueError("Packaged execution contract differs from manifest")
    for name, expected in manifest.get("four_scenario_source_sha256", {}).items():
        source = _within(root / _name(name), root)
        if not source.is_file() or _sha(source) != expected:
            raise ValueError("Registered v8 design source SHA differs: " + name)
        _add(entries, name, source)
    for name in ("requirements.txt", "requirements-training.txt"):
        source = root / name
        if name == "requirements.txt" or source.is_file():
            _add(entries, name, _within(source, root))
    for name, source in (("evidence/v8/global_manifest.json", manifest_path),
                         ("evidence/v8/registered_plan.json", plan_path),
                         ("evidence/v8/AUDIT.json", audit_path),
                         ("evidence/REPRO_SELECTION.json", selection_path)):
        _add(entries, name, source)
    # Preserve report-relative links by retaining the run/analysis and run/figures layout.
    for name in ("analysis", "figures", "design"):
        if not (run / name).is_dir():
            raise FileNotFoundError(run / name)
    folders = [folder for folder in sorted(run.iterdir()) if folder.is_dir() and
               (folder.name == "analysis" or folder.name.startswith("analysis_") or
                folder.name in ("figures", "design", "review", "logs"))]
    for folder in folders:
        for source in sorted(folder.rglob("*")):
            if source.is_symlink():
                raise ValueError("Symlinked run artifact prohibited")
            if source.is_file():
                relative = _within(source, run).relative_to(run).as_posix()
                _add(entries, "run/" + relative, source.read_bytes())
    if not any(name.startswith("run/figures/") and name.endswith(".csv")
               for name in entries):
        raise ValueError("No numeric figure source CSV")
    if not (run / "analysis/ANALYSIS_SUMMARY.json").is_file():
        raise FileNotFoundError("Analysis summary absent")
    report_relative = _within(report_path, run).relative_to(run).as_posix()
    _add(entries, "run/" + report_relative, report_path)
    ledger_path = run / "RUN_LEDGER.jsonl"
    _add(entries, "run/RUN_LEDGER.jsonl", _within(ledger_path, run).read_bytes())
    selected = []
    indexed = {(row["scenario_id"], row["policy_id"]): row for row in external
               if row["latest_audited"]}
    for requested in selection["selected_pairs"]:
        sid, pid = requested["scenario_id"], requested["policy_id"]
        scene = scenes[sid]
        result, payload, result_path, raw_path = _pair_record(
            run / "main/results", sid, pid, scene, ledger[(sid, pid)], load_raw=True)
        if (_sha(raw_path) != requested["expected_raw_sha256"] or
                result.get("plan_signature") != plan["signature"]):
            raise ValueError("Selected raw SHA or plan signature differs")
        compare_analysis_subset(payload, analysis[(sid, pid)])
        index_row = indexed[(sid, pid)]
        if (index_row["raw_sha256"] != _sha(raw_path) or
                index_row["result_sha256"] != _sha(result_path)):
            raise ValueError("Selected raw differs from complete external index")
        index_row["included_in_package"] = True
        attempt = result_path.parent.name
        base = f"examples/v8/{sid}/{pid}/{attempt}"
        raw_arc, result_arc = base + "/trajectory.json.gz", base + "/result.json"
        _add(entries, raw_arc, raw_path)
        _add(entries, result_arc, result_path)
        selected.append({"scenario_id": sid, "policy_id": pid,
                         "analysis_class": scene["analysis_class"],
                         "root_family_id": scene["root_family_id"],
                         "status": result["status"], "raw_path": raw_arc,
                         "raw_sha256": _sha(raw_path), "result_path": result_arc,
                         "result_sha256": _sha(result_path),
                         "source_raw_relative_path": index_row["raw_relative_to_original_run"]})
    _add(entries, INDEX_NAME, _bytes({"schema": SCHEMA + ".external_raw_index",
                                     "run_relative_to_workspace": run.relative_to(root).as_posix(),
                                     "plan_sha256": _sha(plan_path),
                                     "audit_sha256": _sha(audit_path),
                                     "registered_pair_count": len(ledger),
                                     "raw_file_count": len(external),
                                     "unselected_raw_are_external": True,
                                     "rows": external}))
    supplement_records = []
    seen_supplements = set()
    for path in supplements or []:
        supplement = _within(Path(path), root)
        if supplement == run or supplement.name in seen_supplements:
            raise ValueError("Duplicate or main-run supplement")
        seen_supplements.add(supplement.name)
        staged, record = prepare_supplement(supplement, workspace_root=root)
        if (record["source_v8_manifest_sha256"] is not None and
                record["source_v8_manifest_sha256"] != _sha(manifest_path)):
            raise ValueError("Supplement belongs to another v8 main manifest")
        for name, source in staged.items():
            _add(entries, name, source)
        supplement_records.append(record)
    _add(entries, "README_REPRODUCTION.md", _readme(supplement_records))
    receipt = create_archive(archive, entries, {
        "kind": "v8_selected_raw", "registered_pair_count": len(ledger),
        "external_raw_file_count": len(external), "selected_raw_count": len(selected),
        "examples": selected, "roles": roles, "full_raw_archive": False,
        "manifest_sha256": _sha(manifest_path), "plan_sha256": _sha(plan_path),
        "audit_sha256": _sha(audit_path), "analysis_sha256": _sha(analysis_path),
        "analysis_file": "run/analysis/" + analysis_path.name,
        "selection_sha256": _sha(selection_path), "report_path": "run/" + report_relative,
        "runtime_inputs": {name: _sha(root / name) for name in PHYSICAL_INPUTS},
        "external_registered_inputs": external_inputs,
        "supplements": supplement_records,
        "claim_scope": "Selected saved raw score and analysis subset; no physical rerun",
    })
    return {**receipt, **roles, "external_raw_indexed": len(external)}


def verify_bundle(extracted_root: str | Path) -> dict:
    """Verify from the extracted source tree and rescore only selected raw."""
    root = Path(extracted_root).resolve()
    if ROOT.resolve() != root:
        raise ValueError("Run v8 verify using package source from extracted root")
    package = verify_integrity(root)
    if package.get("kind") != "v8_selected_raw" or package.get("full_raw_archive") is not False:
        raise ValueError("Unexpected v8 package kind")
    manifest_path = root / "evidence/v8/global_manifest.json"
    plan_path = root / "evidence/v8/registered_plan.json"
    audit_path = root / "evidence/v8/AUDIT.json"
    analysis_path = _within(root / _name(package["analysis_file"]), root)
    selection_path = root / "evidence/REPRO_SELECTION.json"
    for path, key in ((manifest_path, "manifest_sha256"), (plan_path, "plan_sha256"),
                      (audit_path, "audit_sha256"), (analysis_path, "analysis_sha256"),
                      (selection_path, "selection_sha256")):
        if _sha(path) != package[key]:
            raise ValueError("Bundled registered evidence SHA differs: " + key)
    manifest, plan, audit, ledger = _batch_evidence(
        manifest_path, plan_path, audit_path, required_policies=POLICIES)
    _verify_registered_sources(plan, root)
    for name, expected in manifest.get("four_scenario_source_sha256", {}).items():
        path = _within(root / _name(name), root)
        if _sha(path) != expected:
            raise ValueError("Registered v8 design source differs")
    excluded = {item["path"]: item for item in package.get("external_registered_inputs", [])}
    for row in manifest.get("files", []):
        name = _name(row["path"])
        if name in excluded:
            if (not name.startswith("inputs/literature/") or
                    excluded[name]["sha256"] != row["sha256"] or
                    excluded[name]["bytes"] != row["bytes"] or
                    excluded[name]["packaged"] is not False or
                    (root / name).exists()):
                raise ValueError("External input provenance differs: " + name)
        else:
            path = _within(root / name, root)
            if path.stat().st_size != row["bytes"] or _sha(path) != row["sha256"]:
                raise ValueError("Bundled source/input differs: " + name)
    for name, expected in package["runtime_inputs"].items():
        if name not in PHYSICAL_INPUTS or _sha(root / name) != expected:
            raise ValueError("Bundled runtime input differs: " + name)
    listed = {row["archive_path"] for row in package["entries"]}
    if (not any(path.startswith("run/figures/") and path.endswith(".csv") for path in listed) or
            not all(name in listed for name in (package["report_path"],
              package["analysis_file"], "run/analysis/ANALYSIS_SUMMARY.json",
              "run/RUN_LEDGER.jsonl", "run/review/COMMAND_LEDGER.md",
              "README_REPRODUCTION.md", "requirements.txt"))):
        raise ValueError("Package lacks report, numeric figures or analysis")
    rows = _analysis_rows(analysis_path)
    analysis = _analysis_map(rows)
    if set(analysis) != set(ledger):
        raise ValueError("Bundled analysis denominator differs")
    selection = _read(selection_path)
    if (selection.get("manifest_sha256") != package["manifest_sha256"] or
            selection.get("audit_sha256") != package["audit_sha256"] or
            selection.get("analysis_sha256") != package["analysis_sha256"] or
            validate_selection(selection, manifest, plan, rows) != package["roles"]):
        raise ValueError("Bundled selection coverage/binding differs")
    index = _read(root / INDEX_NAME)
    if (index.get("schema") != SCHEMA + ".external_raw_index" or
            index.get("plan_sha256") != package["plan_sha256"] or
            index.get("audit_sha256") != package["audit_sha256"] or
            index.get("registered_pair_count") != len(ledger) or
            index.get("raw_file_count") != package["external_raw_file_count"] or
            len(index.get("rows", [])) != index["raw_file_count"]):
        raise ValueError("External raw index header differs")
    _verify_raw_index_against_audit(index["rows"], ledger)
    selected_keys = {(item["scenario_id"], item["policy_id"]) for item in package["examples"]}
    marked_keys = {(row["scenario_id"], row["policy_id"]) for row in index["rows"]
                   if row["included_in_package"]}
    if (selected_keys != marked_keys or len(selected_keys) != package["selected_raw_count"] or
            sum(row["latest_audited"] for row in index["rows"]) != len(ledger)):
        raise ValueError("Selected/external raw denominator differs")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    from hospital_ies.control_env import HospitalIESEnv
    from tools.control_improvement.branches import window_targets

    env = HospitalIESEnv(root, execution_contract=manifest["execution_contract"])
    checked = []
    for item in package["examples"]:
        sid, pid = item["scenario_id"], item["policy_id"]
        source = scenes[sid]
        raw_path = _within(root / _name(item["raw_path"]), root)
        result_path = _within(root / _name(item["result_path"]), root)
        if (_sha(raw_path) != item["raw_sha256"] or
                _sha(result_path) != item["result_sha256"] or
                ledger[(sid, pid)]["latest_result_sha256"] != item["result_sha256"] or
                item["analysis_class"] != source["analysis_class"] or
                item["root_family_id"] != source["root_family_id"] or
                source.get("is_final_blind") is not False):
            raise ValueError("Selected raw audit identity differs")
        result = _read(result_path)
        with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream, parse_constant=lambda value:
                                (_ for _ in ()).throw(ValueError(value)))
        if (result.get("scenario_id") != sid or result.get("policy_id") != pid or
                result.get("plan_signature") != plan["signature"] or
                result.get("trajectory_sha256") != item["raw_sha256"] or
                result.get("status") != item["status"] or
                payload.get("scenario") != source or
                payload.get("identity", {}).get("scenario_id") != sid or
                payload.get("identity", {}).get("policy_id") != pid or
                payload.get("summary", {}).get("status") != item["status"]):
            raise ValueError("Selected result/raw identity differs")
        primary = window_targets(payload, 60.0, 360.0, env)
        full = window_targets(payload, 0.0, 600.0, env)
        scored = compare_scored_windows(result, primary, full)
        subset = compare_analysis_subset(payload, analysis[(sid, pid)])
        checked.append({"scenario_id": sid, "policy_id": pid,
                        "raw_sha256": item["raw_sha256"],
                        "score_match": scored["score_match"],
                        "analysis_subset_match": subset["analysis_subset_match"],
                        "unknown_windows": scored["unknown_windows"]})
    supplement_ids = [item["supplement_id"] for item in package.get("supplements", [])]
    if len(supplement_ids) != len(set(supplement_ids)):
        raise ValueError("Duplicate bundled supplement id")
    supplements = [verify_supplement(root, item, env=env)
                   for item in package.get("supplements", [])]
    if any(item.get("source_v8_manifest_sha256") not in (None, package["manifest_sha256"])
           for item in package.get("supplements", [])):
        raise ValueError("Bundled supplement source-v8 manifest differs")
    return {"integrity_passed": True, "registered_pair_count": len(ledger),
            "external_raw_indexed": len(index["rows"]),
            "selected_raw_rechecked": len(checked),
            "targets_per_window": 19, "selected_rows": checked,
            "supplements": supplements,
            "full_physical_simulator_rerun": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="create a new v8 selected-raw archive")
    build.add_argument("--run", type=Path, required=True)
    build.add_argument("--archive", type=Path, required=True)
    build.add_argument("--selection", type=Path, required=True)
    build.add_argument("--report", type=Path, required=True)
    build.add_argument("--supplement", type=Path, action="append", default=[],
                       help="independent audited supplement; repeat for each run")
    select = sub.add_parser("select-main", help="write exact audited main raw selection")
    select.add_argument("--run", type=Path, required=True)
    select.add_argument("--output", type=Path, required=True)
    supplement_select = sub.add_parser("select-supplement",
                                       help="write independent supplement raw selection")
    supplement_select.add_argument("--run", type=Path, required=True)
    supplement_select.add_argument("--analysis", type=Path, required=True)
    supplement_select.add_argument("--output", type=Path, required=True)
    ledger = sub.add_parser("command-ledger", help="write all recorded attempt outcomes")
    ledger.add_argument("--main", type=Path, required=True)
    ledger.add_argument("--clockmatched", type=Path, required=True)
    ledger.add_argument("--fault-cross", type=Path, required=True)
    ledger.add_argument("--output", type=Path, required=True)
    ledger.add_argument("--extra-json", type=Path,
                        help="optional direct-command records with actual exit codes")
    verify = sub.add_parser("verify", help="verify from inside the extracted root")
    verify.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    if args.command == "build":
        receipt = build_bundle(args.run, args.archive, args.selection, args.report,
                               supplements=args.supplement)
    elif args.command == "select-main":
        receipt = write_repro_selection(args.run, args.output)
    elif args.command == "select-supplement":
        receipt = write_supplement_selection(args.run, args.analysis, args.output)
    elif args.command == "command-ledger":
        extras = _read(args.extra_json) if args.extra_json else []
        receipt = write_command_ledger(
            {"main": args.main, "clockmatched": args.clockmatched,
             "fault_cross": args.fault_cross}, args.output, extra_records=extras)
    else:
        receipt = verify_bundle(args.root)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
