"""Write-once, relocatable selected-raw evidence for the four-scenario study.

Build only after the registered batch, raw audit, analysis, figures and report
exist.  Verification runs from an extracted copy and reuses the original
two-window 19-target scorer on saved raw; it never runs the simulator.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile
import zlib


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

SCHEMA = "four_scenarios.portable.v1"
REQUIRED_CLASSES = ("L1", "L2", "L3", "L4b")
NORMAL_CLASSES = ("N2", "N4")
TARGET_COUNT = 19
PHYSICAL_INPUTS = (
    "configs/plant_configs.json",
    "configs/resilience_assumptions.json",
    "configs/p1b_execution_research.json",
    "inputs/energyplus/Hospital_SanDiego.idf",
    "inputs/loads/critical_zones_8760.csv",
    "inputs/loads/electricity_boundary_8760.csv",
    "inputs/loads/hospital_ies_demand_8760.csv",
    "inputs/loads/thermal_boundary_8760.csv",
)
ANALYSIS_FILES = (
    "SCENARIO_RESULTS.csv", "CLASS_SUMMARY.csv", "ROOT_SUMMARY.csv",
    "MATCHED_COMPARISONS.csv", "RECOVERY_EVENTS.csv",
    "UNKNOWN_AND_FAILURES.csv", "ANALYSIS_SUMMARY.json",
)
MANIFEST_NAME = "PACKAGE_MANIFEST.json"
INDEX_NAME = "EXTERNAL_RAW_SHA256_INDEX.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _crc(path: Path) -> int:
    crc = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            crc = zlib.crc32(block, crc)
    return crc & 0xffffffff


def _bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("Path outside approved root: " + str(path)) from error
    return resolved


def _archive_name(value: str) -> str:
    path = PurePosixPath(value)
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value or
            path.is_absolute() or ".." in path.parts or path.as_posix() != value):
        raise ValueError("Unsafe archive member: " + repr(value))
    return value


def _member(root: Path, listed: set[str], name: str) -> Path:
    checked = _archive_name(name)
    if checked not in listed:
        raise ValueError("Selected evidence path is unlisted: " + checked)
    return _within(root / checked, root)


def _label(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError("Unsafe example label: " + repr(value))
    return value


def _add(entries: dict[str, Path | bytes], name: str, source: Path | bytes) -> None:
    name = _archive_name(name)
    if name in entries:
        if entries[name] != source:
            raise ValueError("Archive member collision: " + name)
        return
    if isinstance(source, Path) and not source.is_file():
        raise FileNotFoundError(source)
    entries[name] = source


def _metadata(name: str, source: Path | bytes) -> dict:
    if isinstance(source, Path):
        return {"archive_path": name, "bytes": source.stat().st_size,
                "sha256": _sha(source), "crc32": _crc(source)}
    return {"archive_path": name, "bytes": len(source),
            "sha256": hashlib.sha256(source).hexdigest(),
            "crc32": zlib.crc32(source) & 0xffffffff}


def create_archive(archive_path: str | Path, entries: dict[str, Path | bytes],
                   extra: dict) -> dict:
    """Create an immutable ZIP with a hashed member list and sidecar digest."""
    archive = Path(archive_path).resolve()
    sidecar = archive.with_name(archive.name + ".sha256")
    if archive.exists() or sidecar.exists():
        raise FileExistsError(archive)
    if MANIFEST_NAME in entries:
        raise ValueError("Bundle manifest name collides with evidence")
    checked = {_archive_name(name): source for name, source in entries.items()}
    if len(checked) != len(entries):
        raise ValueError("Duplicate archive member")
    rows = [_metadata(name, checked[name]) for name in sorted(checked)]
    manifest = {"schema": SCHEMA, "entries": rows,
                "manifest_self_excluded_from_entries": True, **extra}
    manifest_bytes = _bytes(manifest)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as stream:
        for name in sorted(checked):
            source = checked[name]
            if isinstance(source, Path):
                stream.write(source, arcname=name)
            else:
                stream.writestr(name, source)
        stream.writestr(MANIFEST_NAME, manifest_bytes)
    with zipfile.ZipFile(archive) as stream:
        if stream.testzip() is not None or len(stream.namelist()) != len(set(stream.namelist())):
            raise ValueError("Created ZIP CRC or duplicate member check failed")
        for row in rows:
            if hashlib.sha256(stream.read(row["archive_path"])).hexdigest() != row["sha256"]:
                raise ValueError("Created ZIP SHA differs: " + row["archive_path"])
    sidecar.write_text(f"{_sha(archive)}  {archive.name}\n", encoding="ascii")
    return {"archive_path": str(archive), "archive_sha256": _sha(archive),
            "archive_bytes": archive.stat().st_size,
            "member_count": len(rows) + 1}


def verify_integrity(extracted_root: str | Path) -> dict:
    """Check every extracted byte and reject unlisted evidence files."""
    root = Path(extracted_root).resolve()
    manifest = _read(root / MANIFEST_NAME)
    if manifest.get("schema") != SCHEMA:
        raise ValueError("Unsupported four-scenario bundle schema")
    expected = set()
    for row in manifest["entries"]:
        name = _archive_name(row["archive_path"])
        if name in expected:
            raise ValueError("Duplicate manifest member")
        expected.add(name)
        path = _within(root / name, root)
        if (not path.is_file() or path.stat().st_size != row["bytes"] or
                _sha(path) != row["sha256"] or _crc(path) != row["crc32"]):
            raise ValueError("Extracted member differs: " + name)
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*")
              if path.is_file() and "__pycache__" not in path.parts and
              ".pytest_cache" not in path.parts}
    if actual != expected | {MANIFEST_NAME}:
        raise ValueError("Extracted file set differs from manifest")
    return manifest


def classify_selected_examples(examples: list[dict]) -> dict:
    """Keep current scene evidence separate from the historical unknown."""
    if not examples or len(examples) > 16:
        raise ValueError("Selected raw sample must contain 1 to 16 attempts")
    if any(item.get("is_final_blind") is not False for item in examples):
        raise ValueError("Sealed or unknown blind identity is prohibited")
    current = [item for item in examples if item.get("origin") == "v7"]
    historic = [item for item in examples if item.get("origin") == "v6_historical_unknown"]
    if len(current) + len(historic) != len(examples):
        raise ValueError("Unknown selected evidence origin")
    counts = {category: sum(item.get("analysis_class") == category for item in current)
              for category in REQUIRED_CLASSES}
    missing = [category for category, count in counts.items() if not count]
    if missing:
        raise ValueError("Missing new four-class raw: " + ",".join(missing))
    normal = sum(item.get("analysis_class") in NORMAL_CLASSES and
                 item.get("no_external_disturbance") is True for item in current)
    if not normal:
        raise ValueError("Missing new normal no-external-disturbance raw")
    new_unknown = sum(item.get("full_horizon") is False for item in current)
    if any(item.get("full_horizon") is not False for item in historic):
        raise ValueError("Historical v6 unknown example is not truncated")
    if len(historic) > 1:
        raise ValueError("At most one historical v6 unknown example is allowed")
    if not new_unknown and not historic:
        raise ValueError("Missing unknown raw (new or explicitly historical v6)")
    return {"new_class_examples": counts,
            "new_no_disturbance_count": normal,
            "new_unknown_count": new_unknown,
            "historical_unknown_count": len(historic),
            "new_raw_count": len(current),
            "selected_raw_count": len(examples)}


def compare_scored_windows(result: dict, primary: dict, full: dict) -> dict:
    """Compare both original 19-target scores exactly, including null masks."""
    from tools.policy_search.score_recheck import score_differences

    unknown = []
    for name, score in (("primary_score", primary), ("full_observation", full)):
        targets = score.get("targets")
        mask = score.get("target_mask")
        if (not isinstance(targets, list) or len(targets) != TARGET_COUNT or
                mask != [value is not None for value in targets] or
                type(score.get("full_horizon")) is not bool):
            raise ValueError(name + " 19-target schema or mask differs")
        if score["full_horizon"]:
            if any(value is None for value in targets):
                raise ValueError(name + " complete window has unknown target")
        else:
            if any(value is not None for value in targets[1:]):
                raise ValueError(name + " unknown tail was imputed")
            unknown.append(name)
        errors = score_differences(result.get(name), score, name)
        if errors:
            raise ValueError("Stored/recomputed " + name + " differs: " + "; ".join(errors[:3]))
    return {"score_match": True, "targets_per_window": TARGET_COUNT,
            "unknown_windows": unknown}


def _source_entries(root: Path) -> dict[str, Path]:
    found = {}
    for folder in (root / "src/hospital_ies", root / "tools"):
        if not folder.is_dir():
            raise FileNotFoundError(folder)
        for path in sorted(folder.rglob("*.py")):
            relative = _within(path, root).relative_to(root).as_posix()
            found[relative] = path
    for path in sorted(root.glob("*.py")):
        relative = _within(path, root).relative_to(root).as_posix()
        found[relative] = path
    return found


def _verify_registered_sources(plan: dict, root: Path) -> None:
    registered = plan.get("source_sha256")
    if not isinstance(registered, dict) or not registered:
        raise ValueError("Registered source SHA inventory absent")
    for name, digest in registered.items():
        path = _within(root / _archive_name(name), root)
        if not path.is_file() or _sha(path) != digest:
            raise ValueError("Registered source SHA differs: " + name)


def _verify_four_source(manifest: dict, root: Path,
                        external_proposals: list[dict] | None = None) -> list[dict]:
    registered = manifest.get("four_scenario_source_sha256")
    design = "tools/four_scenarios/data_design.py"
    if not isinstance(registered, dict) or design not in registered:
        raise ValueError("Four-scenario source SHA inventory absent")
    external = []
    for name, digest in registered.items():
        name = _archive_name(name)
        if (name.startswith("outputs/v5_policy_search_") or
                name.startswith("outputs/v6_policy_redesign_")):
            external.append({"path": name, "sha256": digest,
                             "packaged": False,
                             "reason": "prior_source_reservation_provenance"})
            if external_proposals is not None:
                if (root / name).exists():
                    raise ValueError("External prior source proposal unexpectedly packaged")
                continue
        path = _within(root / name, root)
        if not path.is_file() or _sha(path) != digest:
            raise ValueError("Four-scenario source SHA differs: " + name)
    if external_proposals is not None and external != external_proposals:
        raise ValueError("External prior source proposal SHA inventory differs")
    return external


def _run_file(path: Path, run: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Symlinked run artifact prohibited: " + str(path))
    return _within(path, run)


def _verify_raw_index_against_audit(rows: list[dict], ledger: dict) -> None:
    """Every external raw must be one exact audited attempt, including retries."""
    from hospital_ies.dataset_manifest import digest

    expected = {}
    for (sid, pid), pair in ledger.items():
        attempts = pair.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ValueError("Raw audit attempt inventory absent: " + sid + "/" + pid)
        for attempt in attempts:
            name = _label(attempt["attempt"])
            key = (sid, pid, name)
            if (key in expected or not attempt.get("raw_sha256") or
                    not attempt.get("result_sha256")):
                raise ValueError("Raw audit attempt SHA or identity invalid")
            expected[key] = attempt
    actual = set()
    for row in rows:
        sid, pid = row.get("scenario_id"), row.get("policy_id")
        relative = _archive_name(row["raw_relative_to_original_run"])
        path = PurePosixPath(relative)
        if (not isinstance(sid, str) or not isinstance(pid, str) or
                path.name != "trajectory.json.gz" or
                len(path.parts) < 6 or path.parts[-3] != digest([sid, pid])):
            raise ValueError("Raw index path/identity differs from audited pair")
        key = (sid, pid, path.parent.name)
        audited = expected.get(key)
        if (audited is None or key in actual or
                row.get("raw_sha256") != audited["raw_sha256"] or
                row.get("result_sha256") != audited["result_sha256"]):
            raise ValueError("Raw index differs from audit attempt: " + relative)
        if row.get("latest_audited") is True and (
                row["result_sha256"] != ledger[(sid, pid)]["latest_result_sha256"]):
            raise ValueError("Latest raw index differs from audited result")
        actual.add(key)
    if actual != set(expected):
        raise ValueError("Raw index lacks audited attempt")


def _batch_evidence(manifest_path: Path, plan_path: Path, audit_path: Path,
                    *, required_policies: tuple[str, ...] = ()) -> tuple[dict, dict, dict, dict]:
    manifest, plan, audit = map(_read, (manifest_path, plan_path, audit_path))
    ids = plan.get("scenario_ids", [])
    policies = [row["policy_id"] for row in plan.get("policy_specs", [])]
    expected = {(sid, pid) for sid in ids for pid in policies}
    if (not ids or not policies or len(ids) != len(set(ids)) or
            len(policies) != len(set(policies)) or
            not set(required_policies) <= set(policies) or
            audit.get("passed") is not True or
            audit.get("matched_initial_state_and_stream") is not True or
            audit.get("final_blind_opened") is True or
            audit.get("plan_sha256") != _sha(plan_path) or
            audit.get("manifest_sha256") != _sha(manifest_path) or
            plan.get("manifest_sha256") != _sha(manifest_path) or
            plan.get("registered_pair_count") != len(expected) or
            any(audit.get(key) != len(expected) for key in
                ("registered_pair_count", "terminal_pair_count", "recomputed_pair_count"))):
        raise ValueError("Passed registered batch, raw audit and bindings required")
    ledger = {}
    for row in audit.get("pair_ledger", []):
        key = (row.get("scenario_id"), row.get("policy_id"))
        if key in ledger:
            raise ValueError("Duplicate audited pair")
        ledger[key] = row
    if set(ledger) != expected:
        raise ValueError("Audited pair denominator differs from plan")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    if not set(ids) <= set(scenes):
        raise ValueError("Registered scene missing from source manifest")
    if any(scenes[sid].get("is_final_blind") is not False for sid in ids):
        raise ValueError("Sealed final scene appears in registered plan")
    if any(scenes[sid].get("experiment_role") != plan.get("phase") for sid in ids):
        raise ValueError("Registered scenario phase differs from plan")
    return manifest, plan, audit, ledger


def _latest_result(results_dir: Path, sid: str, pid: str) -> Path:
    from hospital_ies.dataset_manifest import digest

    pair = results_dir / "pairs" / digest([sid, pid])
    attempts = sorted(path for path in pair.glob("attempt_*")
                      if path.is_dir() and path.name[8:].isdigit())
    if not attempts:
        raise FileNotFoundError("Missing raw attempt: " + sid + "/" + pid)
    result = attempts[-1] / "result.json"
    if not result.is_file():
        raise FileNotFoundError(result)
    return result


def _pair_record(results_dir: Path, sid: str, pid: str, scene: dict,
                 ledger: dict, *, load_raw=False) -> tuple[dict, dict | None, Path, Path]:
    result_path = _latest_result(results_dir, sid, pid)
    result = _read(result_path)
    raw_name = result.get("trajectory_path")
    if raw_name != "trajectory.json.gz":
        raise ValueError("Unexpected selected raw filename")
    raw_path = result_path.parent / raw_name
    if (result.get("scenario_id") != sid or result.get("policy_id") != pid or
            result.get("terminal") is not True or
            result.get("status") != ledger.get("status") or
            _sha(result_path) != ledger.get("latest_result_sha256") or
            not raw_path.is_file() or _sha(raw_path) != result.get("trajectory_sha256") or
            result.get("binding", {}).get("passed") is not True):
        raise ValueError("Result/raw SHA, identity or audit ledger differs: " + sid + "/" + pid)
    payload = None
    if load_raw:
        with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if (payload.get("scenario") != scene or
                payload.get("identity", {}).get("scenario_id") != sid or
                payload.get("identity", {}).get("policy_id") != pid or
                payload.get("summary", {}).get("status") != result["status"]):
            raise ValueError("Selected raw scene, policy or status differs")
    for stored, audited in (("primary_score", "recomputed_primary_score"),
                            ("full_observation", "recomputed_full_observation")):
        if audited in ledger and result.get(stored) != ledger[audited]:
            raise ValueError("Audited/stored score differs: " + sid + "/" + pid)
    return result, payload, result_path, raw_path


def _selected_pair_spec(item) -> tuple[str, str]:
    if isinstance(item, dict):
        return _label(item["scenario_id"]), _label(item["policy_id"])
    if isinstance(item, (list, tuple)) and len(item) == 2:
        return _label(item[0]), _label(item[1])
    raise ValueError("Selection must contain scenario_id and policy_id")


def _example(origin: str, scene: dict, sid: str, pid: str, result: dict,
             result_path: Path, raw_path: Path, *, source_run: Path,
             manifest_arc: str, plan_arc: str, audit_arc: str,
             audit_sha: str) -> dict:
    category = scene.get("analysis_class") if origin == "v7" else "GRID0_historical_unknown"
    base = (f"examples/{origin}/{_label(category)}/{_label(sid)}/{_label(pid)}")
    return {"origin": origin, "evidence_role": (
                "new_four_scenario" if origin == "v7" else
                "historical_v6_known_mechanism_unknown"),
            "analysis_class": category,
            "scenario_id": sid, "policy_id": pid,
            "status": result["status"],
            "full_horizon": result["full_observation"]["full_horizon"],
            "no_external_disturbance": (origin == "v7" and
                                        category in NORMAL_CLASSES and
                                        not scene.get("exogenous_events")),
            "is_final_blind": False,
            "raw_path": base + "/trajectory.json.gz",
            "result_path": base + "/result.json",
            "raw_sha256": _sha(raw_path), "result_sha256": _sha(result_path),
            "source_run_id": source_run.name,
            "source_raw_relative_path": _within(raw_path, source_run).relative_to(
                source_run).as_posix(),
            "data_manifest_path": manifest_arc, "plan_path": plan_arc,
            "audit_path": audit_arc, "audit_sha256": audit_sha,
            "source_phase": scene.get("experiment_role")}


def _add_example(entries: dict[str, Path | bytes], item: dict,
                 raw_path: Path, result_path: Path) -> None:
    _add(entries, item["raw_path"], raw_path)
    _add(entries, item["result_path"], result_path)
    error = result_path.parent / "error.json"
    if error.is_file():
        _add(entries, str(PurePosixPath(item["result_path"]).parent / "error.json"), error)


def _historical_unknown(spec: dict, root: Path,
                        entries: dict[str, Path | bytes]) -> dict:
    old_run = _within(Path(spec["run_dir"]), root)
    if not old_run.name.startswith("v6_policy_redesign_"):
        raise ValueError("Historical unknown must come from the frozen v6 run")
    batch = _within(Path(spec["batch_dir"]), old_run)
    audit_path = _within(Path(spec["audit_path"]), old_run)
    plan_path = batch / "registered_plan.json"
    manifest_path = _within(Path(_read(plan_path)["manifest_path"]), root)
    manifest, plan, audit, ledger = _batch_evidence(manifest_path, plan_path, audit_path)
    if not str(manifest.get("manifest_version", "")).startswith(
            ("policy_search.v5.", "policy_redesign.v6.")):
        raise ValueError("Historical v6 run has unexpected source manifest")
    sid, pid = _selected_pair_spec(spec)
    if (sid, pid) not in ledger:
        raise ValueError("Historical unknown absent from passed v6 audit")
    scene = next(row for row in manifest["scenarios"] if row["id"] == sid)
    if scene.get("experiment_role") != plan.get("phase"):
        raise ValueError("Historical unknown phase differs")
    result, payload, result_path, raw_path = _pair_record(
        batch / "results", sid, pid, scene, ledger[(sid, pid)], load_raw=True)
    if (result.get("plan_signature") != plan.get("signature") or
            result["full_observation"].get("full_horizon") is not False or
            ledger[(sid, pid)].get("full_observation_horizon") is not False):
        raise ValueError("Historical v6 example is not an audited unknown")
    manifest_arc = "evidence/v6_unknown/global_manifest.json"
    plan_arc = "evidence/v6_unknown/registered_plan.json"
    audit_arc = "evidence/v6_unknown/AUDIT.json"
    for arc, source in ((manifest_arc, manifest_path), (plan_arc, plan_path),
                        (audit_arc, audit_path)):
        _add(entries, arc, source)
    example = _example("v6_historical_unknown", scene, sid, pid, result,
                       result_path, raw_path, source_run=old_run,
                       manifest_arc=manifest_arc, plan_arc=plan_arc,
                       audit_arc=audit_arc, audit_sha=_sha(audit_path))
    _add_example(entries, example, raw_path, result_path)
    return example


def _runtime_receipt(manifest: dict) -> dict:
    import gurobipy as gp

    return {"manifest_runtime": manifest.get("runtime", {}),
            "recorded_gurobipy_version": manifest.get("runtime", {}).get(
                "packages", {}).get("gurobipy"),
            "build_gurobipy_version": metadata.version("gurobipy"),
            "build_gurobi_api_version": list(gp.gurobi.version()),
            "score_recheck_imports_gurobipy_but_does_not_solve": True,
            "full_year_simulation_not_in_bundle": True}


def _readme(runtime: dict) -> bytes:
    gurobi = runtime["build_gurobipy_version"]
    return ("# Four-scenario selected-raw reproduction\n\n"
            "This archive contains the registered v7 manifest/plan/audit, every "
            "analysis table, figure source CSV, complete Python source and "
            "runtime inputs and contract, plus selected audited raw. Literature "
            "PDFs in the registered source manifest are SHA-indexed outside "
            "this small bundle because they are not runtime score dependencies. "
            "Prior v5/v6 source proposals are likewise externally SHA-indexed "
            "in PACKAGE_MANIFEST.json; the registered v7 design Python is included. "
            "The remaining "
            "raw stays external with SHA-256 paths in EXTERNAL_RAW_SHA256_INDEX.json.\n\n"
            "The recorded full runtime and build-time Gurobi version are in "
            "evidence/RECORDED_RUNTIME.json. The project requirements files "
            "are copied unchanged. For the stored-raw score check, use a "
            "compatible Python 3.10 environment and install:\n\n"
            "    python -m pip install -r requirements.txt\n"
            f"    python -m pip install gurobipy=={gurobi}\n\n"
            "The original scoring import graph requires gurobipy, although "
            "this verification does not solve an optimization model. It does "
            "not establish that a Gurobi license is available for an actual "
            "physical rerun. requirements-training.txt is included when present "
            "but is not needed for this raw score check.\n\n"
            "From the extracted directory, run:\n\n"
            "    python -B -m tools.four_scenarios.package verify --root .\n\n"
            "Verification checks extracted bytes and recomputes both original "
            "19-target windows from each included raw. It cannot rerun the "
            "full-year physical experiment and does not validate real hospital "
            "clinical performance. Historical "
            "v6 unknown raw, if included, is marked separately and never counts "
            "as a new v7 four-class example.\n").encode("utf-8")


def build_bundle(run_dir: str | Path, archive_path: str | Path,
                 selected_pairs: list[dict | tuple[str, str]], *,
                 historical_unknown: dict | None = None,
                 workspace_root: str | Path = ROOT) -> dict:
    """Package the complete registered v7 table and a small audited raw sample."""
    root = Path(workspace_root).resolve()
    run = _within(Path(run_dir), root)
    if "v7_four_scenarios" not in run.name:
        raise ValueError("Four-scenario bundle requires the new v7 run directory")
    manifest_path = run / "new_data/global_manifest.json"
    plan_path = run / "main/registered_plan.json"
    audit_path = run / "main/AUDIT.json"
    manifest, plan, audit, ledger = _batch_evidence(
        manifest_path, plan_path, audit_path,
        required_policies=("secure_milp", "priority_milp"))
    _verify_registered_sources(plan, root)
    external_proposals = _verify_four_source(manifest, root)
    if not str(manifest.get("manifest_version", "")).startswith("four_scenarios.v7."):
        raise ValueError("Expected registered v7 four-scenario manifest")
    scenes = {scene["id"]: scene for scene in manifest["scenarios"]}
    results_dir = run / "main/results"
    entries: dict[str, Path | bytes] = {}
    for name, path in _source_entries(root).items():
        _add(entries, name, path)
    external_literature = []
    for row in manifest.get("files", []):
        name = _archive_name(row["path"])
        path = _within(root / name, root)
        if path.stat().st_size != row["bytes"] or _sha(path) != row["sha256"]:
            raise ValueError("Registered source/input SHA differs: " + name)
        if name.startswith("inputs/literature/"):
            external_literature.append({"path": name, "bytes": row["bytes"],
                                        "sha256": row["sha256"],
                                        "packaged": False,
                                        "reason": "literature_provenance_not_runtime_score_dependency"})
            continue
        _add(entries, name, path)
    for name in PHYSICAL_INPUTS:
        path = _within(root / name, root)
        _add(entries, name, path)
    for name in ("requirements.txt", "requirements-training.txt"):
        path = root / name
        if name == "requirements.txt" or path.is_file():
            _add(entries, name, _within(path, root))
    if _read(root / "configs/p1b_execution_research.json") != manifest.get("execution_contract"):
        raise ValueError("Registered execution contract differs from copied JSON")
    for arc, source in (("evidence/v7/global_manifest.json", manifest_path),
                        ("evidence/v7/registered_plan.json", plan_path),
                        ("evidence/v7/AUDIT.json", audit_path)):
        _add(entries, arc, source)
    runtime = _runtime_receipt(manifest)
    _add(entries, "evidence/RECORDED_RUNTIME.json", _bytes(runtime))
    for name in ANALYSIS_FILES:
        _add(entries, "analysis/" + name, _run_file(run / "analysis" / name, run))
    figure_root = (run / "figures/final") if (run / "figures/final").is_dir() else (run / "figures")
    figure_csvs = sorted(figure_root.rglob("*.csv"))
    if not figure_csvs:
        raise ValueError("Figure source CSV absent")
    for path in sorted(figure_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinked figure artifact prohibited: " + str(path))
        if path.is_file() and (path.suffix.lower() in (".csv", ".png", ".svg") or
                               path.name == "FIGURE_QA.json"):
            _add(entries, "figures/" + path.relative_to(run / "figures").as_posix(),
                 _run_file(path, run))
    _add(entries, "FOUR_SCENARIOS_REPORT.md",
         _run_file(run / "FOUR_SCENARIOS_REPORT.md", run))
    for folder in (run / "design", run / "review"):
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Symlinked run artifact prohibited: " + str(path))
                if path.is_file():
                    _add(entries, "run/" + path.relative_to(run).as_posix(),
                         _run_file(path, run))
    _add(entries, "README_REPRODUCTION.md", _readme(runtime))

    # Index every registered raw, not only the small selected sample. No raw is
    # omitted from the denominator; unselected raw remains external by SHA.
    external = []
    for sid in plan["scenario_ids"]:
        for pid in (row["policy_id"] for row in plan["policy_specs"]):
            result, _, result_path, raw_path = _pair_record(
                results_dir, sid, pid, scenes[sid], ledger[(sid, pid)])
            external.append({"origin": "v7", "scenario_id": sid, "policy_id": pid,
                             "analysis_class": scenes[sid]["analysis_class"],
                             "root_family_id": scenes[sid]["root_family_id"],
                             "status": result["status"],
                             "raw_relative_to_original_run": _within(
                                 raw_path, run).relative_to(run).as_posix(),
                             "raw_bytes": raw_path.stat().st_size,
                             "raw_sha256": _sha(raw_path),
                             "result_relative_to_original_run": _within(
                                 result_path, run).relative_to(run).as_posix(),
                             "result_sha256": _sha(result_path),
                             "latest_audited": True,
                             "included_in_package": False})
    latest_paths = {row["raw_relative_to_original_run"] for row in external}
    for raw_path in sorted(results_dir.rglob("trajectory.json.gz")):
        relative = _within(raw_path, run).relative_to(run).as_posix()
        if relative in latest_paths:
            continue
        result_path = raw_path.parent / "result.json"
        result = _read(result_path) if result_path.is_file() else {}
        digest = _sha(raw_path)
        if result.get("trajectory_sha256") not in (None, digest):
            raise ValueError("Unselected attempt raw/result SHA differs: " + relative)
        sid, pid = result.get("scenario_id"), result.get("policy_id")
        scene = scenes.get(sid, {})
        external.append({"origin": "v7", "scenario_id": sid, "policy_id": pid,
                         "analysis_class": scene.get("analysis_class"),
                         "root_family_id": scene.get("root_family_id"),
                         "status": result.get("status", "missing_result"),
                         "raw_relative_to_original_run": relative,
                         "raw_bytes": raw_path.stat().st_size,
                         "raw_sha256": digest,
                         "result_relative_to_original_run": (
                             _within(result_path, run).relative_to(run).as_posix()
                             if result_path.is_file() else None),
                         "result_sha256": _sha(result_path) if result_path.is_file() else None,
                         "latest_audited": False,
                         "included_in_package": False})
    external.sort(key=lambda row: row["raw_relative_to_original_run"])
    _verify_raw_index_against_audit(external, ledger)
    selected = []
    keys = [_selected_pair_spec(item) for item in selected_pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate selected raw pair")
    for sid, pid in keys:
        if (sid, pid) not in ledger:
            raise ValueError("Selected raw absent from passed v7 audit")
        scene = scenes[sid]
        result, payload, result_path, raw_path = _pair_record(
            results_dir, sid, pid, scene, ledger[(sid, pid)], load_raw=True)
        if result.get("plan_signature") != plan.get("signature"):
            raise ValueError("Selected result differs from plan signature")
        example = _example("v7", scene, sid, pid, result, result_path, raw_path,
                           source_run=run,
                           manifest_arc="evidence/v7/global_manifest.json",
                           plan_arc="evidence/v7/registered_plan.json",
                           audit_arc="evidence/v7/AUDIT.json",
                           audit_sha=_sha(audit_path))
        _add_example(entries, example, raw_path, result_path)
        selected.append(example)
        matches = [row for row in external if
                   row["scenario_id"] == sid and row["policy_id"] == pid and
                   row["latest_audited"] is True and
                   row["raw_sha256"] == example["raw_sha256"] and
                   row["raw_relative_to_original_run"] ==
                   example["source_raw_relative_path"]]
        if len(matches) != 1:
            raise ValueError("Selected raw has no unique latest audited external index row")
        matches[0]["included_in_package"] = True
    if historical_unknown is not None:
        selected.append(_historical_unknown(historical_unknown, root, entries))
    roles = classify_selected_examples(selected)
    _add(entries, INDEX_NAME, _bytes({
        "schema": SCHEMA + ".external_raw_index",
        "run_relative_to_workspace": run.relative_to(root).as_posix(),
        "plan_sha256": _sha(plan_path), "audit_sha256": _sha(audit_path),
        "registered_pair_count": len(ledger),
        "raw_file_count": len(external),
        "unselected_raw_are_external": True,
        "rows": external,
    }))
    summary = create_archive(archive_path, entries, {
        "kind": "four_scenarios_selected_raw", "examples": selected,
        "roles": roles, "registered_pair_count": len(ledger),
        "external_raw_file_count": len(external),
        "manifest_sha256": _sha(manifest_path),
        "plan_sha256": _sha(plan_path), "audit_sha256": _sha(audit_path),
        "runtime_inputs": {name: _sha(root / name) for name in PHYSICAL_INPUTS},
        "runtime_receipt_sha256": hashlib.sha256(_bytes(runtime)).hexdigest(),
        "external_literature_provenance": external_literature,
        "external_design_proposals": external_proposals,
        "full_raw_archive": False,
        "claim_scope": "Selected saved raw two-window score recomputation; no physical simulator rerun",
    })
    return {**summary, **roles, "external_raw_indexed": len(external)}


def verify_bundle(extracted_root: str | Path) -> dict:
    """Verify from the extracted source tree and recompute two 19-target scores."""
    root = Path(extracted_root).resolve()
    if ROOT.resolve() != root:
        raise ValueError("Run verify using tools.four_scenarios.package from the extracted root")
    manifest = verify_integrity(root)
    if manifest.get("kind") != "four_scenarios_selected_raw":
        raise ValueError("Not a four-scenario selected-raw bundle")
    for name, digest in manifest["runtime_inputs"].items():
        if name not in PHYSICAL_INPUTS or _sha(root / name) != digest:
            raise ValueError("Bundled physical input differs: " + name)
    roles = classify_selected_examples(manifest["examples"])
    if roles != manifest.get("roles"):
        raise ValueError("Selected raw role counts differ")
    v7_manifest = root / "evidence/v7/global_manifest.json"
    v7_plan = root / "evidence/v7/registered_plan.json"
    v7_audit = root / "evidence/v7/AUDIT.json"
    if any((_sha(path) != manifest[key]) for path, key in
           ((v7_manifest, "manifest_sha256"), (v7_plan, "plan_sha256"),
            (v7_audit, "audit_sha256"))):
        raise ValueError("Bundled registered v7 evidence SHA differs")
    source_manifest, plan, audit, ledger = _batch_evidence(
        v7_manifest, v7_plan, v7_audit,
        required_policies=("secure_milp", "priority_milp"))
    _verify_registered_sources(plan, root)
    _verify_four_source(source_manifest, root,
                        external_proposals=manifest.get("external_design_proposals", []))
    runtime_path = root / "evidence/RECORDED_RUNTIME.json"
    runtime = _read(runtime_path)
    if (runtime.get("manifest_runtime") != source_manifest.get("runtime", {}) or
            _sha(runtime_path) != manifest.get("runtime_receipt_sha256") or
            runtime.get("score_recheck_imports_gurobipy_but_does_not_solve") is not True):
        raise ValueError("Recorded runtime/dependency receipt differs")
    excluded = {row["path"]: row for row in
                manifest.get("external_literature_provenance", [])}
    for row in source_manifest.get("files", []):
        name = _archive_name(row["path"])
        if name.startswith("inputs/literature/"):
            external = excluded.get(name)
            if (external is None or external.get("sha256") != row["sha256"] or
                    external.get("bytes") != row["bytes"] or
                    external.get("packaged") is not False or
                    (root / name).exists()):
                raise ValueError("External literature SHA provenance differs: " + name)
            continue
        path = _within(root / name, root)
        if (not path.is_file() or path.stat().st_size != row["bytes"] or
                _sha(path) != row["sha256"]):
            raise ValueError("Bundled registered source/input differs: " + name)
    if set(excluded) != {row["path"] for row in source_manifest.get("files", [])
                         if row["path"].startswith("inputs/literature/")}:
        raise ValueError("External literature provenance denominator differs")
    listed = {row["archive_path"] for row in manifest["entries"]}
    if (not all("analysis/" + name in listed for name in ANALYSIS_FILES) or
            "FOUR_SCENARIOS_REPORT.md" not in listed or
            "requirements.txt" not in listed or
            "README_REPRODUCTION.md" not in listed or
            not any(name.startswith("figures/") and name.endswith(".csv")
                    for name in listed)):
        raise ValueError("Bundle lacks full analysis tables, report or figure source CSV")
    index = _read(root / INDEX_NAME)
    if (index.get("schema") != SCHEMA + ".external_raw_index" or
            index.get("registered_pair_count") != manifest["registered_pair_count"] or
            index.get("raw_file_count") != manifest["external_raw_file_count"] or
            len(index.get("rows", [])) != index["raw_file_count"] or
            index.get("plan_sha256") != manifest["plan_sha256"] or
            index.get("audit_sha256") != manifest["audit_sha256"]):
        raise ValueError("External raw SHA index differs from registered denominator")
    expected_keys = {(sid, spec["policy_id"]) for sid in plan["scenario_ids"]
                     for spec in plan["policy_specs"]}
    latest_rows = [row for row in index["rows"] if row["latest_audited"] is True]
    indexed_keys = {(row["scenario_id"], row["policy_id"]) for row in latest_rows}
    raw_paths = [row["raw_relative_to_original_run"] for row in index["rows"]]
    if (expected_keys != indexed_keys or len(latest_rows) != len(indexed_keys) or
            len(raw_paths) != len(set(raw_paths)) or
            any(_archive_name(path) != path for path in raw_paths)):
        raise ValueError("External raw index lacks or duplicates registered pair")
    _verify_raw_index_against_audit(index["rows"], ledger)
    indexed = {(row["scenario_id"], row["policy_id"]): row
               for row in latest_rows}
    for key, row in indexed.items():
        audited = ledger[key]
        if row["result_sha256"] != audited["latest_result_sha256"]:
            raise ValueError("External index result SHA differs from raw audit")
        attempts = audited.get("attempts") or []
        if attempts and row["raw_sha256"] != attempts[-1].get("raw_sha256"):
            raise ValueError("External index raw SHA differs from raw audit")
    selected_v7 = {(item["scenario_id"], item["policy_id"]): item
                   for item in manifest["examples"] if item["origin"] == "v7"}
    if (sum(row["included_in_package"] is True for row in index["rows"]) != len(selected_v7) or
            any(not indexed[key]["included_in_package"] or
                indexed[key]["raw_sha256"] != item["raw_sha256"] or
                indexed[key]["result_sha256"] != item["result_sha256"]
                for key, item in selected_v7.items())):
        raise ValueError("Selected v7 raw differs from full external SHA index")
    scenes = {scene["id"]: scene for scene in source_manifest["scenarios"]}
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from hospital_ies.control_env import HospitalIESEnv
    from tools.control_improvement.branches import window_targets

    envs = {}
    checked = []
    for item in manifest["examples"]:
        paths = {name: _member(root, listed, item[name]) for name in
                 ("raw_path", "result_path", "audit_path",
                  "data_manifest_path", "plan_path")}
        if (item.get("is_final_blind") is not False or
                _sha(paths["raw_path"]) != item["raw_sha256"] or
                _sha(paths["result_path"]) != item["result_sha256"] or
                _sha(paths["audit_path"]) != item["audit_sha256"]):
            raise ValueError("Selected raw/result/audit SHA differs")
        source_data = _read(paths["data_manifest_path"])
        source_plan = _read(paths["plan_path"])
        source_audit = _read(paths["audit_path"])
        if (source_plan.get("manifest_sha256") != _sha(paths["data_manifest_path"]) or
                source_audit.get("plan_sha256") != _sha(paths["plan_path"]) or
                source_audit.get("manifest_sha256") != _sha(paths["data_manifest_path"]) or
                source_audit.get("passed") is not True):
            raise ValueError("Selected manifest/plan/audit binding differs")
        sid, pid = item["scenario_id"], item["policy_id"]
        scene = next((row for row in source_data["scenarios"] if row["id"] == sid), None)
        if scene is None or scene.get("is_final_blind") is not False:
            raise ValueError("Selected scene absent or sealed")
        pair_ledger = next((row for row in source_audit["pair_ledger"] if
                            row["scenario_id"] == sid and row["policy_id"] == pid), None)
        if (pair_ledger is None or pair_ledger.get("latest_result_sha256") !=
                item["result_sha256"]):
            raise ValueError("Selected result differs from audited pair")
        result = _read(paths["result_path"])
        with gzip.open(paths["raw_path"], "rt", encoding="utf-8") as stream:
            payload = json.load(stream,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if (result.get("scenario_id") != sid or result.get("policy_id") != pid or
                result.get("status") != item["status"] or
                result.get("plan_signature") != source_plan.get("signature") or
                result.get("trajectory_sha256") != item["raw_sha256"] or
                payload.get("scenario") != scene or
                payload.get("summary", {}).get("status") != item["status"] or
                payload.get("identity", {}).get("scenario_id") != sid or
                payload.get("identity", {}).get("policy_id") != pid):
            raise ValueError("Selected payload/result identity differs")
        if item["origin"] == "v7":
            if (scene != scenes[sid] or item["analysis_class"] != scene["analysis_class"] or
                    (sid, pid) not in ledger or
                    item["full_horizon"] is not result["full_observation"]["full_horizon"] or
                    item["no_external_disturbance"] is not (
                        scene["analysis_class"] in NORMAL_CLASSES and
                        not scene.get("exogenous_events"))):
                raise ValueError("Selected v7 class or original ledger differs")
        elif (item["origin"] != "v6_historical_unknown" or
              not str(item.get("source_run_id", "")).startswith("v6_policy_redesign_") or
              not str(source_data.get("manifest_version", "")).startswith(
                  ("policy_search.v5.", "policy_redesign.v6.")) or
              pair_ledger.get("full_observation_horizon") is not False):
            raise ValueError("Historical unknown role differs")
        contract = source_data["execution_contract"]
        contract_key = json.dumps(contract, sort_keys=True, ensure_ascii=True)
        if contract_key not in envs:
            envs[contract_key] = HospitalIESEnv(root, execution_contract=contract)
        env = envs[contract_key]
        primary = window_targets(payload, 60.0, 360.0, env)
        full = window_targets(payload, 0.0, 600.0, env)
        receipt = compare_scored_windows(result, primary, full)
        if receipt["unknown_windows"] and item["full_horizon"] is True and "full_observation" in receipt["unknown_windows"]:
            raise ValueError("Selected full-horizon role conflicts with rescore")
        checked.append({"origin": item["origin"], "analysis_class": item["analysis_class"],
                        "scenario_id": sid, "policy_id": pid,
                        "primary_full_horizon": primary["full_horizon"],
                        "full_observation_horizon": full["full_horizon"],
                        **receipt})
    return {"integrity_passed": True, "selected_raw_rechecked": len(checked),
            "targets_per_window": TARGET_COUNT,
            "registered_external_raw_indexed": len(index["rows"]),
            "recorded_gurobipy_version": runtime.get("recorded_gurobipy_version"),
            "verifier_gurobipy_version": metadata.version("gurobipy"),
            "gurobipy_version_matches_recorded": (
                metadata.version("gurobipy") == runtime.get("recorded_gurobipy_version")),
            "new_class_examples": roles["new_class_examples"],
            "historical_unknown_count": roles["historical_unknown_count"],
            "rows": checked}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="write a new four-scenario ZIP")
    build.add_argument("--run", type=Path, required=True)
    build.add_argument("--archive", type=Path, required=True)
    build.add_argument("--selection", type=Path, required=True,
                       help="JSON with selected_pairs and optional historical_unknown")
    verify = sub.add_parser("verify", help="verify from within extracted root")
    verify.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    if args.command == "build":
        selection = _read(args.selection)
        receipt = build_bundle(args.run, args.archive, selection["selected_pairs"],
                               historical_unknown=selection.get("historical_unknown"))
    else:
        receipt = verify_bundle(args.root)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
