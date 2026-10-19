"""Two-level v6 evidence delivery and relocation-safe selected-raw review.

The global archive carries every derived run artifact but indexes raw trajectories
outside it.  The separate reproduction archive carries a small, explicitly
identified set of real raw attempts, their registered results, the complete
Python source closure, and the seven physical inputs needed to rescore them.
Neither archive changes or reruns the hospital simulator.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile
import zlib

from tools.policy_redesign.closed_loop import verify_batch
from tools.policy_redesign.select_develop import verify_saved_audit


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "policy_redesign.portable.v1"
PHYSICAL_INPUTS = (
    "configs/plant_configs.json",
    "configs/resilience_assumptions.json",
    "inputs/energyplus/Hospital_SanDiego.idf",
    "inputs/loads/critical_zones_8760.csv",
    "inputs/loads/electricity_boundary_8760.csv",
    "inputs/loads/hospital_ies_demand_8760.csv",
    "inputs/loads/thermal_boundary_8760.csv",
)
# These are deliberately historical, already-seen mechanism examples.  They
# never count as new v6 selection/development evidence.
V5_EXAMPLES = (
    ("development/registered_plan.json", "strict_guard_microdifference",
     "control_case_b0ffe845e9981ad93704", "secure_milp"),
    ("development/registered_plan.json", "strict_guard_microdifference",
     "control_case_b0ffe845e9981ad93704", "strict_guard"),
    ("development/registered_plan.json", "nn_p0_harm",
     "control_case_9c8ea8b1c22e1517a144", "secure_milp"),
    ("development/registered_plan.json", "nn_p0_harm",
     "control_case_9c8ea8b1c22e1517a144", "nn_ungated"),
    ("development/registered_plan.json", "diesel_and_recovery",
     "control_case_75dc748d38ed2208bff2", "secure_milp"),
    ("development/registered_plan.json", "diesel_and_recovery",
     "control_case_75dc748d38ed2208bff2", "nn_ungated"),
    ("development/registered_plan.json", "normal",
     "control_case_b82b06fd2adca81f29d2", "secure_milp"),
    ("development/registered_plan.json", "normal",
     "control_case_b82b06fd2adca81f29d2", "nn_ungated"),
    ("selection/registered_plan.json", "unknown_grid0",
     "control_case_ddc9a27df0f33f9f4612", "secure_milp"),
    ("selection/registered_plan.json", "unknown_grid0",
     "control_case_ddc9a27df0f33f9f4612", "nn_gru_aggregate_r1"),
)


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
    value = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value = zlib.crc32(block, value)
    return value & 0xffffffff


def _within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"Path outside approved root: {path}") from error
    return resolved


def _archive_name(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute() or
            ".." in path.parts or path.as_posix() != name):
        raise ValueError(f"Unsafe archive member: {name}")
    return name


def _label(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"Unsafe example label: {value!r}")
    return value


def _add(entries: dict[str, Path | bytes], name: str, source: Path | bytes) -> None:
    name = _archive_name(name)
    if name in entries:
        if entries[name] != source:
            raise ValueError(f"Archive member collision: {name}")
        return
    if isinstance(source, Path) and not source.is_file():
        raise FileNotFoundError(source)
    entries[name] = source


def _entry_metadata(name: str, source: Path | bytes) -> dict:
    if isinstance(source, Path):
        return {"archive_path": name, "bytes": source.stat().st_size,
                "sha256": _sha(source), "crc32": _crc(source)}
    return {"archive_path": name, "bytes": len(source),
            "sha256": hashlib.sha256(source).hexdigest(),
            "crc32": zlib.crc32(source) & 0xffffffff}


def _make_archive(archive: Path, entries: dict[str, Path | bytes],
                  manifest_name: str, manifest_extra: dict) -> dict:
    archive = archive.resolve()
    if archive.exists() or archive.with_name(archive.name + ".sha256").exists():
        raise FileExistsError(archive)
    _archive_name(manifest_name)
    if manifest_name in entries:
        raise ValueError("Archive manifest collides with evidence file")
    rows = [_entry_metadata(name, entries[name]) for name in sorted(entries)]
    manifest = {"schema": SCHEMA + ".archive", "entries": rows,
                "manifest_self_excluded_from_entries": True, **manifest_extra}
    data = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as zf:
        for name in sorted(entries):
            source = entries[name]
            if isinstance(source, Path):
                zf.write(source, arcname=name)
            else:
                zf.writestr(name, source)
        zf.writestr(manifest_name, data)
    with zipfile.ZipFile(archive) as zf:
        if zf.testzip() is not None:
            raise ValueError("ZIP CRC verification failed")
        if len(zf.namelist()) != len(set(zf.namelist())):
            raise ValueError("Duplicate ZIP member")
        for row in rows:
            if hashlib.sha256(zf.read(row["archive_path"])).hexdigest() != row["sha256"]:
                raise ValueError(f"ZIP SHA verification failed: {row['archive_path']}")
    digest = _sha(archive)
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii")
    return {"archive_path": str(archive), "archive_sha256": digest,
            "archive_bytes": archive.stat().st_size,
            "member_count": len(rows) + 1, "manifest_name": manifest_name}


def _source_entries(workspace_root: Path, *, prefix: str = "") -> dict[str, Path]:
    root = workspace_root.resolve()
    found = {}
    for folder in (root / "src/hospital_ies", root / "tools"):
        if not folder.is_dir():
            raise FileNotFoundError(folder)
        for path in sorted(folder.rglob("*.py")):
            relative = _within(path, root).relative_to(root).as_posix()
            found[prefix + relative] = path
    # Several historical tool modules import these top-level helpers.  They
    # are tiny, so retain the full Python import closure even though the
    # selected-raw scorer itself does not call those helpers.
    for path in sorted(root.glob("*.py")):
        relative = _within(path, root).relative_to(root).as_posix()
        found[prefix + relative] = path
    return found


def _result_example(*, origin: str, role: str, category: str,
                    scenario_id: str, policy_id: str, result_path: Path,
                    plan_path: Path, manifest_path: Path, is_final_blind: bool) -> dict:
    result = _read(result_path)
    plan = _read(plan_path)
    if (result.get("scenario_id") != scenario_id or result.get("policy_id") != policy_id or
            result.get("plan_signature") != plan.get("signature")):
        raise ValueError(f"Example result identity/plan differs: {result_path}")
    raw_name = result.get("trajectory_path")
    if raw_name != "trajectory.json.gz":
        raise ValueError(f"Registered example lacks standard raw file: {result_path}")
    raw_path = result_path.parent / raw_name
    if not raw_path.is_file():
        raise FileNotFoundError(raw_path)
    digest = _sha(raw_path)
    declared = result.get("trajectory_sha256")
    if declared is not None and declared != digest:
        raise ValueError(f"Raw SHA differs from result: {raw_path}")
    if is_final_blind:
        raise ValueError(f"Sealed final case cannot be included: {scenario_id}")
    return {"origin_run": origin, "evidence_role": role, "category": category,
            "scenario_id": scenario_id, "policy_id": policy_id,
            "status": result.get("status"), "is_final_blind": False,
            "raw_path": raw_path, "result_path": result_path,
            "plan_path": plan_path, "manifest_path": manifest_path,
            "raw_sha256": digest, "result_sha256": _sha(result_path)}


def select_v5_examples(v5_run: str | Path) -> list[dict]:
    """Return ten fixed, checked historical examples with no v6 novelty claim."""
    run = Path(v5_run).resolve()
    ledger = _read(run / "review/POLICY_PAIR_LEDGER.json")
    manifest_path = run / "new_data/global_manifest.json"
    scenarios = {row["id"]: row for row in _read(manifest_path)["scenarios"]}
    batch_by_path = {b["plan_path"]: b for b in ledger["batches"]}
    examples = []
    for plan_relative, category, sid, pid in V5_EXAMPLES:
        batch = batch_by_path[plan_relative]
        pair = next((p for p in batch["pairs"] if
                     p["scenario_id"] == sid and p["policy_id"] == pid), None)
        if pair is None or not pair["attempts"]:
            raise ValueError(f"Missing fixed v5 raw: {sid}/{pid}")
        attempt = pair["attempts"][-1]
        result_path = _within(run / attempt["result_path"], run)
        example = _result_example(
            origin="v5", role="historical_seen_mechanism", category=category,
            scenario_id=sid, policy_id=pid, result_path=result_path,
            plan_path=_within(run / plan_relative, run),
            manifest_path=manifest_path,
            is_final_blind=bool(scenarios[sid]["is_final_blind"]),
        )
        if (example["status"] != pair["status"] or
                _within(run / attempt["raw_trajectory_path"], run) != example["raw_path"]):
            raise ValueError(f"v5 ledger/raw identity differs: {sid}/{pid}")
        examples.append(example)
    return examples


def select_v5_core_examples(v5_run: str | Path) -> list[dict]:
    """Keep four already-seen mechanism pairs when two new v6 pairs are included."""
    categories = {"strict_guard_microdifference", "nn_p0_harm",
                  "diesel_and_recovery", "normal"}
    examples = [item for item in select_v5_examples(v5_run)
                if item["category"] in categories]
    if (len(examples) != 8 or {item["category"] for item in examples} != categories or
            any(sum(item["category"] == category for item in examples) != 2
                for category in categories) or
            any(sum(item["category"] == category and
                    item["policy_id"] == "secure_milp" for item in examples) != 1
                for category in categories)):
        raise ValueError("Eight historical raw must retain four matched v5 mechanism pairs")
    return examples


def select_v6_matched_pair(v6_run: str | Path, batch_dir: str | Path,
                           scenario_id: str, candidate_policy_id: str,
                           *, category: str = "v6_new_matched_case",
                           audit_path: str | Path | None = None) -> list[dict]:
    """Select an audited new selection/development secure/candidate raw pair."""
    run = Path(v6_run).resolve()
    batch = _within(Path(batch_dir), run)
    plan_path = batch / "registered_plan.json"
    plan, manifest = verify_batch(plan_path)
    phase = plan.get("phase")
    if phase not in ("selection", "development"):
        raise ValueError("New v6 validation pair requires selection or development phase")
    if _within(Path(plan["manifest_path"]), run) != (run / "new_data/global_manifest.json").resolve():
        raise ValueError("New v6 validation pair must use frozen v6 new-data manifest")
    if scenario_id not in plan.get("scenario_ids", []):
        raise ValueError("Scenario absent from registered v6 plan")
    declared = {row["policy_id"] for row in plan.get("policy_specs", [])}
    if not {"secure_milp", candidate_policy_id} <= declared or candidate_policy_id == "secure_milp":
        raise ValueError("Matched secure/candidate policies absent from plan")
    manifest_path = _within(Path(plan["manifest_path"]), run)
    scene = next((row for row in manifest["scenarios"]
                  if row["id"] == scenario_id), None)
    if (scene is None or scene.get("experiment_role") != phase or
            scene.get("is_final_blind") is not False):
        raise ValueError("v6 scenario role/manifest differs")
    checked_audit_path = _within(Path(audit_path) if audit_path is not None
                                 else batch / "AUDIT_V6.json", run)
    verify_saved_audit(plan_path, checked_audit_path, phase)
    audit = _read(checked_audit_path)
    ledger = {(row.get("scenario_id"), row.get("policy_id")): row
              for row in audit["pair_ledger"]}
    results = {}
    for pid in ("secure_milp", candidate_policy_id):
        pair = ledger.get((scenario_id, pid))
        if (pair is None or pair.get("terminal") is not True or
                pair.get("score_recomputed") is not True or
                pair.get("binding_passed") is not True or not pair.get("attempts")):
            raise ValueError("Matched v6 pair lacks terminal audited raw")
        latest = pair["attempts"][-1]
        result_path = _within(Path(latest["result_path"]), batch)
        raw_path = _within(Path(latest["raw_path"]), batch)
        result = _read(result_path)
        if (latest.get("terminal") is not True or
                result.get("terminal") is not True or
                _sha(result_path) != latest.get("result_sha256") or
                _sha(raw_path) != latest.get("raw_sha256") or
                result.get("trajectory_sha256") != latest["raw_sha256"] or
                result_path.parent / result.get("trajectory_path", "") != raw_path):
            raise ValueError("Matched v6 result/raw SHA or terminal receipt differs")
        results[pid] = result_path
    examples = [_result_example(
        origin="v6", role="new_v6_matched_validation", category=category,
        scenario_id=scenario_id, policy_id=pid,
        result_path=results[pid], plan_path=plan_path,
        manifest_path=manifest_path, is_final_blind=bool(scene.get("is_final_blind")),
    ) for pid in ("secure_milp", candidate_policy_id)]
    for item in examples:
        item.update(source_phase=phase, audit_path=checked_audit_path,
                    audit_sha256=_sha(checked_audit_path))
    return examples


def select_v6_train_unknown_pair(v6_run: str | Path, batch_dir: str | Path,
                                 scenario_id: str, candidate_policy_id: str,
                                 *, category: str = "current_v6_train_unknown",
                                 audit_path: str | Path | None = None) -> list[dict]:
    """Select a terminal but uncompleted broad-train pair as *unknown* evidence."""
    run = Path(v6_run).resolve()
    batch = _within(Path(batch_dir), run)
    if batch != (run / "training/expanded_30roots_p01234").resolve():
        raise ValueError("Current unknown must come from the registered broad train batch")
    plan_path = batch / "registered_plan.json"
    plan, manifest = verify_batch(plan_path)
    if (plan.get("phase") != "train" or len(plan.get("scenario_ids", [])) != 150 or
            len(plan.get("policy_specs", [])) != 5 or
            scenario_id not in plan["scenario_ids"] or
            candidate_policy_id == "secure_milp" or
            not {"secure_milp", candidate_policy_id} <= {
                row["policy_id"] for row in plan["policy_specs"]}):
        raise ValueError("Current unknown requires the registered 150-by-five train panel")
    scene = next((row for row in manifest["scenarios"]
                  if row["id"] == scenario_id), None)
    if (scene is None or scene.get("experiment_role") != "train" or
            scene.get("is_final_blind") is not False):
        raise ValueError("Current unknown scene role differs or final blind is sealed")
    checked_audit_path = _within(Path(audit_path) if audit_path is not None
                                 else batch / "AUDIT_rechecked_v2.json", run)
    verify_saved_audit(plan_path, checked_audit_path, "train")
    audit = _read(checked_audit_path)
    if (audit.get("passed") is not True or audit.get("registered_pair_count") != 750 or
            audit.get("recomputed_pair_count") != 750):
        raise ValueError("Current unknown requires the passing full broad raw audit")
    ledger = {(row.get("scenario_id"), row.get("policy_id")): row
              for row in audit["pair_ledger"]}
    results = {}
    for pid in ("secure_milp", candidate_policy_id):
        pair = ledger.get((scenario_id, pid))
        if (pair is None or pair.get("terminal") is not True or
                pair.get("binding_passed") is not True or
                pair.get("score_recomputed") is not True or
                pair.get("status") != "model_constraint_infeasible" or
                pair.get("primary_full_horizon") is not False or
                pair.get("full_observation_horizon") is not False or
                not pair.get("attempts")):
            raise ValueError("Current train pair is not an audited unknown horizon")
        latest = pair["attempts"][-1]
        result_path = _within(Path(latest["result_path"]), batch)
        raw_path = _within(Path(latest["raw_path"]), batch)
        result = _read(result_path)
        if (latest.get("terminal") is not True or
                result.get("terminal") is not True or
                result.get("status") != "model_constraint_infeasible" or
                _sha(result_path) != latest.get("result_sha256") or
                _sha(raw_path) != latest.get("raw_sha256") or
                result.get("trajectory_sha256") != latest["raw_sha256"] or
                result_path.parent / result.get("trajectory_path", "") != raw_path):
            raise ValueError("Current unknown result/raw SHA or terminal receipt differs")
        results[pid] = result_path
    manifest_path = Path(plan["manifest_path"]).resolve()
    examples = [_result_example(
        origin="v6", role="new_v6_train_unknown", category=category,
        scenario_id=scenario_id, policy_id=pid,
        result_path=results[pid], plan_path=plan_path,
        manifest_path=manifest_path, is_final_blind=False,
    ) for pid in ("secure_milp", candidate_policy_id)]
    for item in examples:
        item.update(source_phase="train", audit_path=checked_audit_path,
                    audit_sha256=_sha(checked_audit_path))
    return examples


def build_global_review_bundle(run_dir: str | Path, archive_path: str | Path,
                               *, workspace_root: str | Path = ROOT) -> dict:
    """Package all derived v6 artifacts; raw stays external but SHA indexed."""
    run, root, archive = Path(run_dir).resolve(), Path(workspace_root).resolve(), Path(archive_path).resolve()
    report = run / "POLICY_REDESIGN_REPORT.md"
    if not report.is_file():
        raise FileNotFoundError(report)
    entries: dict[str, Path | bytes] = {}
    raw_rows = []
    for path in sorted(run.rglob("*")):
        if not path.is_file() or path.resolve() == archive:
            continue
        name = _within(path, run).relative_to(run).as_posix()
        if name.endswith("trajectory.json.gz"):
            result_path = path.parent / "result.json"
            result = _read(result_path) if result_path.is_file() else {}
            digest = _sha(path)
            if result.get("trajectory_sha256") not in (None, digest):
                raise ValueError(f"Raw/result SHA differs: {path}")
            raw_rows.append({"path": name, "bytes": path.stat().st_size,
                             "sha256": digest, "result_declared_sha256": result.get("trajectory_sha256"),
                             "status": result.get("status"),
                             "result_path": result_path.relative_to(run).as_posix()
                             if result_path.is_file() else None})
            continue
        if path.suffix.lower() in (".zip", ".sha256"):
            continue
        _add(entries, name, path)
    raw_index = {"schema": SCHEMA + ".external_raw_index", "files": raw_rows,
                 "raw_inside_global_archive": False,
                 "unknown_not_imputed": True}
    _add(entries, "review/RAW_SHA256_INDEX.json",
         (json.dumps(raw_index, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    for name, path in _source_entries(root, prefix="source/").items():
        _add(entries, name, path)
    summary = _make_archive(archive, entries, "review/GLOBAL_ZIP_MANIFEST.json",
                            {"kind": "global_review", "raw_excluded": True,
                             "all_nonraw_run_files_included": True,
                             "source_python_included": True})
    return {**summary, "raw_count": len(raw_rows),
            "raw_total_bytes_external": sum(row["bytes"] for row in raw_rows)}


_PORTABLE_TEST = b'''from pathlib import Path
from tools.policy_redesign.portable import verify_all

def test_selected_raw_scores_and_cpu_model():
    report = verify_all(Path(__file__).resolve().parents[1])
    assert report["manifest_valid"]
    assert report["rechecked_raw"] == report["selected_raw"]
    assert report["model_cpu_inference"]
'''


def _model_spec(model_dir: Path, v5_run: Path, workspace_root: Path) -> dict:
    directory = _within(model_dir, workspace_root)
    metadata = _read(directory / "metadata.json")
    if (directory / "best.pt").is_file():
        kind, checkpoint, role = "v6_best", "best.pt", "new_v6_best_checkpoint"
        artifacts = metadata.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("v6 best.pt/last.pt artifact binding absent")
        for name in ("best.pt", "last.pt", "fit_report.json"):
            file = directory / name
            if not file.is_file() or artifacts.get(name) != _sha(file):
                raise ValueError("v6 model artifact SHA differs: " + name)
        report = _read(directory / "fit_report.json")
        if (metadata.get("deployed_checkpoint") != "best.pt" or
                metadata.get("best_epoch") != report.get("best_epoch") or
                metadata.get("last_epoch") != report.get("last_epoch") or
                report.get("actual_train_device") != "cuda:0" or
                report.get("actual_validation_device") != "cuda:0" or
                report.get("peak_cuda_memory_allocated_bytes", 0) <= 0 or
                report.get("optimizer_updates", 0) <= 0):
            raise ValueError("v6 best/last epochs or actual CUDA fit metadata differs")
        from tools.policy_redesign.neural import model_artifact_binding
        model_artifact_binding(directory)
    elif directory == (v5_run / "models/gru_agg1_blend_overload_seed5102").resolve():
        kind, checkpoint, role = "v5_gru", "model.pt", "historical_reload_example_only"
    else:
        raise ValueError("Model must provide v6 best.pt or explicit v5 GRU fallback")
    if not (directory / checkpoint).is_file():
        raise FileNotFoundError(directory / checkpoint)
    return {"kind": kind, "role": role, "relative_dir": "models/selected",
            "checkpoint": checkpoint, "metadata_sha256": _sha(directory / "metadata.json"),
            "checkpoint_sha256": _sha(directory / checkpoint),
            "selected_epoch": metadata.get("best_epoch") if kind == "v6_best" else None,
            "last_checkpoint_sha256": (_sha(directory / "last.pt")
                                       if kind == "v6_best" else None),
            "fit_report_sha256": (_sha(directory / "fit_report.json")
                                  if kind == "v6_best" else None)}


def _checked_v6_examples(v6_examples: list[dict], *, v6_run: str | Path | None = None
                         ) -> list[dict]:
    """Rebind each complete v6 pair to its passing phase-specific raw audit."""
    rows = list(v6_examples)
    if not rows:
        return []
    roles = {"new_v6_train_unknown", "new_v6_matched_validation"}
    if len(rows) not in (2, 4) or any(row.get("evidence_role") not in roles for row in rows):
        raise ValueError("v6 reproduction requires one or two complete matched pairs")
    groups = {role: [row for row in rows if row["evidence_role"] == role]
              for role in roles}
    if ((len(rows) == 2 and len(groups["new_v6_matched_validation"]) != 2) or
            (len(rows) == 4 and any(len(group) != 2 for group in groups.values()))):
        raise ValueError("Two-pair reproduction requires train unknown and new validation roles")
    checked_rows = []
    common_run = Path(v6_run).resolve() if v6_run is not None else None
    for role in ("new_v6_train_unknown", "new_v6_matched_validation"):
        group = groups[role]
        if not group:
            continue
        if (len({item.get("scenario_id") for item in group}) != 1 or
                len({Path(item["plan_path"]).resolve() for item in group}) != 1 or
                len({item.get("category") for item in group}) != 1 or
                len({Path(item["audit_path"]).resolve() for item in group}) != 1 or
                len({item.get("policy_id") for item in group}) != 2 or
                "secure_milp" not in {item.get("policy_id") for item in group} or
                any(item.get("origin_run") != "v6" for item in group)):
            raise ValueError("v6 example pair identities, audit or source role differ")
        candidate = next(item["policy_id"] for item in group
                         if item["policy_id"] != "secure_milp")
        batch = Path(group[0]["plan_path"]).resolve().parent
        phase = _read(batch / "registered_plan.json").get("phase")
        if (role == "new_v6_train_unknown" and phase != "train") or (
                role == "new_v6_matched_validation" and phase not in
                ("selection", "development")):
            raise ValueError("v6 train unknown or validation phase differs")
        run = batch.parents[1] if phase == "train" else batch.parent
        if common_run is None:
            common_run = run
        elif run != common_run:
            raise ValueError("v6 example pairs come from different run roots")
        if any(item.get("source_phase") != phase for item in group):
            raise ValueError("v6 example source phase differs from registered plan")
        select = (select_v6_train_unknown_pair if phase == "train" else
                  select_v6_matched_pair)
        checked = select(common_run, batch, group[0]["scenario_id"], candidate,
                         category=group[0]["category"], audit_path=group[0]["audit_path"])
        by_policy = {item["policy_id"]: item for item in checked}
        for item in group:
            registered = by_policy[item["policy_id"]]
            for key in ("raw_path", "raw_sha256", "result_path", "result_sha256",
                        "plan_path", "manifest_path", "status", "evidence_role",
                        "source_phase", "audit_path", "audit_sha256"):
                if item.get(key) != registered[key]:
                    raise ValueError("v6 matched audited raw example changed: " + key)
        checked_rows.extend(group)
    if (len(rows) == 4 and
            groups["new_v6_train_unknown"][0]["scenario_id"] ==
            groups["new_v6_matched_validation"][0]["scenario_id"]):
        raise ValueError("Train unknown and independent validation scenarios overlap")
    return checked_rows


def build_reproduction_bundle(archive_path: str | Path, *, examples: list[dict] | None = None,
                              v6_examples: list[dict] = (),
                              workspace_root: str | Path = ROOT,
                              v5_run: str | Path | None = None,
                              model_dir: str | Path | None = None,
                              v6_run: str | Path | None = None) -> dict:
    """Bundle selected actual trajectories and relocated score/model dependencies."""
    root = Path(workspace_root).resolve()
    old = (Path(v5_run).resolve() if v5_run is not None else
           root / "outputs/v5_policy_search_20261015_run01")
    new = _checked_v6_examples(list(v6_examples), v6_run=v6_run)
    historical = list((select_v5_core_examples(old) if len(new) == 4 else
                       select_v5_examples(old)) if examples is None else examples)
    if len(new) == 4:
        fixed = select_v5_core_examples(old)
        fixed_by_identity = {(item["category"], item["policy_id"]): item
                             for item in fixed}
        if (len(historical) != 8 or
                {(item.get("category"), item.get("policy_id")) for item in historical}
                != set(fixed_by_identity)):
            raise ValueError("Two v6 pairs require eight specified v5 historical raw")
        for item in historical:
            original = fixed_by_identity[item["category"], item["policy_id"]]
            for key in ("raw_path", "result_path", "plan_path", "manifest_path"):
                if Path(item[key]).resolve() != Path(original[key]).resolve():
                    raise ValueError("Specified historical v5 raw path differs: " + key)
            for key in ("raw_sha256", "result_sha256", "status",
                        "evidence_role", "is_final_blind"):
                if item.get(key) != original[key]:
                    raise ValueError("Specified historical v5 raw binding differs: " + key)
    selected = historical + new
    if not selected or len(selected) > 12:
        raise ValueError("Reproduction sample must contain 1 to 12 actual raw attempts")
    if len(new) == 4 and model_dir is None:
        raise ValueError("Two-pair v6 reproduction requires explicit formal v6 best model")
    entries: dict[str, Path | bytes] = {}
    for name, path in _source_entries(root).items():
        _add(entries, name, path)
    for name in PHYSICAL_INPUTS:
        _add(entries, name, _within(root / name, root))
    for name in ("pytest.ini", "requirements.txt", "requirements-training.txt",
                 "docs/v6/REPRODUCTION_README.md"):
        _add(entries, name, _within(root / name, root))
    _add(entries, "tests/test_portable_reproduction.py", _PORTABLE_TEST)
    data_manifests = {}
    plans = {}
    index = []
    for item in selected:
        if item.get("is_final_blind") is not False:
            raise ValueError("Sealed/unknown blind example prohibited")
        origin = item["origin_run"]
        role = ("historical_seen_mechanism" if origin == "v5" else
                item.get("evidence_role") if origin == "v6" else None)
        if origin == "v6" and role not in (
                "new_v6_train_unknown", "new_v6_matched_validation"):
            raise ValueError("v6 example evidence role differs")
        if item.get("evidence_role") != role:
            raise ValueError("Example origin and evidence role differ")
        category, sid, pid = map(_label, (item["category"], item["scenario_id"],
                                         item["policy_id"]))
        base = f"examples/{origin}/{category}/{sid}/{pid}"
        raw_path, result_path = Path(item["raw_path"]), Path(item["result_path"])
        result = _read(result_path)
        if (result.get("scenario_id") != sid or result.get("policy_id") != pid or
                _sha(raw_path) != item["raw_sha256"] or
                result.get("trajectory_sha256") not in (None, item["raw_sha256"])):
            raise ValueError("Selected raw/result identity or hash changed")
        if result.get("status") != item["status"]:
            raise ValueError("Selected raw status changed")
        manifest_path, plan_path = Path(item["manifest_path"]), Path(item["plan_path"])
        manifest_hash, plan_hash = _sha(manifest_path), _sha(plan_path)
        manifest_arc = f"evidence/manifests/{origin}_{manifest_hash[:16]}.json"
        plan_arc = f"evidence/plans/{origin}_{plan_hash[:16]}.json"
        data_manifests[manifest_arc] = manifest_path
        plans[plan_arc] = plan_path
        _add(entries, base + "/trajectory.json.gz", raw_path)
        _add(entries, base + "/result.json", result_path)
        error_path = result_path.parent / "error.json"
        if error_path.is_file():
            _add(entries, base + "/error.json", error_path)
        index.append({"origin_run": origin, "evidence_role": role, "category": category,
                      "scenario_id": sid, "policy_id": pid, "status": item["status"],
                      "raw_path": base + "/trajectory.json.gz",
                      "result_path": base + "/result.json",
                      "plan_path": plan_arc, "data_manifest_path": manifest_arc,
                      "raw_sha256": item["raw_sha256"],
                      "result_sha256": _sha(result_path),
                      "source_phase": item.get("source_phase"),
                      "external_raw_audit_sha256": item.get("audit_sha256"),
                      "is_final_blind": False})
    for name, path in {**data_manifests, **plans}.items():
        _add(entries, name, path)
    chosen_model = (Path(model_dir).resolve() if model_dir is not None else
                    old / "models/gru_agg1_blend_overload_seed5102")
    model = _model_spec(chosen_model, old, root)
    if len(new) == 4 and model["kind"] != "v6_best":
        raise ValueError("Two-pair v6 reproduction requires formal v6 best model")
    for path in sorted(chosen_model.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            _add(entries, "models/selected/" + path.relative_to(chosen_model).as_posix(), path)
    runtime = _read(old / "new_data/global_manifest.json").get("runtime", {})
    _add(entries, "evidence/RECORDED_RUNTIME.json",
         (json.dumps(runtime, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    extra = {"kind": "selected_raw_reproduction", "examples": index,
             "historical_v5_count": sum(x["origin_run"] == "v5" for x in index),
             "new_v6_count": sum(x["origin_run"] == "v6" for x in index),
             "new_v6_train_unknown_count": sum(
                 x["evidence_role"] == "new_v6_train_unknown" for x in index),
             "new_v6_matched_validation_count": sum(
                 x["evidence_role"] == "new_v6_matched_validation" for x in index),
             "v5_examples_do_not_count_as_new_validation": True,
             "runtime_inputs": {name: _sha(root / name) for name in PHYSICAL_INPUTS},
             "model": model, "full_raw_archive": False,
             "claim_scope": "Selected stored raw score recomputation and CPU model inference; not full physical rerun"}
    summary = _make_archive(Path(archive_path), entries, "REPRO_MANIFEST.json", extra)
    return {**summary, "selected_raw": len(index),
            "historical_v5_raw": extra["historical_v5_count"],
            "new_v6_raw": extra["new_v6_count"], "model_kind": model["kind"]}


def verify_manifest(extracted_root: str | Path) -> dict:
    root = Path(extracted_root).resolve()
    manifest = _read(root / "REPRO_MANIFEST.json")
    if manifest.get("schema") != SCHEMA + ".archive" or manifest.get("kind") != "selected_raw_reproduction":
        raise ValueError("Unsupported reproduction manifest")
    expected = set()
    for row in manifest["entries"]:
        name = _archive_name(row["archive_path"])
        if name in expected:
            raise ValueError("Duplicate manifest member")
        expected.add(name)
        path = _within(root / name, root)
        if (not path.is_file() or path.stat().st_size != row["bytes"] or
                _sha(path) != row["sha256"] or _crc(path) != row["crc32"]):
            raise ValueError(f"Extracted member differs: {name}")
    # Python and pytest may create bytecode/cache after extraction, including
    # before this function is entered via ``python -m``.
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts and
              ".pytest_cache" not in p.parts}
    if actual != expected | {"REPRO_MANIFEST.json"}:
        raise ValueError("Extracted file set differs from manifest")
    for name, digest in manifest["runtime_inputs"].items():
        if name not in PHYSICAL_INPUTS or _sha(root / name) != digest:
            raise ValueError(f"Runtime input differs: {name}")
    examples = manifest["examples"]
    if (len(examples) > 12 or
            manifest["historical_v5_count"] != sum(
                x["evidence_role"] == "historical_seen_mechanism" for x in examples) or
            manifest["new_v6_count"] != sum(x["origin_run"] == "v6" for x in examples) or
            manifest["new_v6_train_unknown_count"] != sum(
                x["evidence_role"] == "new_v6_train_unknown" for x in examples) or
            manifest["new_v6_matched_validation_count"] != sum(
                x["evidence_role"] == "new_v6_matched_validation" for x in examples)):
        raise ValueError("Portable raw sample roles or denominators differ")
    if manifest["new_v6_count"] == 4 and (
            manifest["historical_v5_count"] != 8 or
            manifest["new_v6_train_unknown_count"] != 2 or
            manifest["new_v6_matched_validation_count"] != 2):
        raise ValueError("Twelve-raw package lacks 8 historical plus 2 unknown plus 2 validation")
    return manifest


def recheck_selected_raw(extracted_root: str | Path, manifest: dict | None = None) -> dict:
    """Use the original 19-target scorer on stored raw, preserving unknowns."""
    root = Path(extracted_root).resolve()
    manifest = verify_manifest(root) if manifest is None else manifest
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from hospital_ies.control_env import HospitalIESEnv
    from tools.control_improvement.branches import window_targets
    from tools.policy_search.score_recheck import score_differences

    envs = {}
    rows = []
    for item in manifest["examples"]:
        raw_path = _within(root / item["raw_path"], root)
        result_path = _within(root / item["result_path"], root)
        result = _read(result_path)
        plan = _read(_within(root / item["plan_path"], root))
        data_manifest = _read(_within(root / item["data_manifest_path"], root))
        if (result.get("scenario_id") != item["scenario_id"] or
                result.get("policy_id") != item["policy_id"] or
                result.get("status") != item["status"] or
                result.get("plan_signature") != plan.get("signature") or
                _sha(raw_path) != item["raw_sha256"] or
                _sha(result_path) != item["result_sha256"]):
            raise ValueError("Selected raw/result/plan identity differs")
        if _sha(root / item["data_manifest_path"]) != plan.get("manifest_sha256"):
            raise ValueError("Selected plan/data manifest binding differs")
        scene = next((s for s in data_manifest["scenarios"]
                      if s["id"] == item["scenario_id"]), None)
        if scene is None or scene.get("is_final_blind") or item["is_final_blind"]:
            raise ValueError("Selected scene absent or sealed")
        if item["origin_run"] == "v6":
            phase = plan.get("phase")
            if (item.get("source_phase") != phase or
                    scene.get("experiment_role") != phase or
                    item.get("external_raw_audit_sha256") is None):
                raise ValueError("Selected v6 raw phase or external audit binding differs")
            if item["evidence_role"] == "new_v6_train_unknown":
                if phase != "train" or item["status"] != "model_constraint_infeasible":
                    raise ValueError("Train unknown raw mislabeled as validation")
            elif (item["evidence_role"] != "new_v6_matched_validation" or
                  phase not in ("selection", "development")):
                raise ValueError("v6 validation role or phase differs")
        with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
            payload = json.load(stream,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if (payload.get("scenario", {}).get("id") != item["scenario_id"] or
                payload.get("summary", {}).get("status") != item["status"]):
            raise ValueError("Raw payload identity/status differs")
        contract = data_manifest["execution_contract"]
        contract_key = json.dumps(contract, sort_keys=True, ensure_ascii=True)
        if contract_key not in envs:
            envs[contract_key] = HospitalIESEnv(root, execution_contract=contract)
        env = envs[contract_key]
        primary = window_targets(payload, 60.0, 360.0, env)
        full = window_targets(payload, 0.0, 600.0, env)
        errors = (score_differences(result.get("primary_score"), primary, "primary_score") +
                  score_differences(result.get("full_observation"), full, "full_observation"))
        if errors:
            raise ValueError(f"Selected raw score differs {item['scenario_id']}/"
                             f"{item['policy_id']}: {errors[:3]}")
        for window_name, score in (("primary", primary), ("full", full)):
            targets = score["targets"]
            if (len(targets) != 19 or
                    score.get("target_mask") != [value is not None for value in targets]):
                raise ValueError(f"Selected {window_name} target mask differs")
            # The original scorer can know a prefix P0 failure, while all
            # other original target components require the complete horizon.
            if not score["full_horizon"] and any(
                    value is not None for value in targets[1:]):
                raise ValueError(f"Unknown selected {window_name} score was imputed")
        if (item.get("evidence_role") == "new_v6_train_unknown" and
                (primary["full_horizon"] or full["full_horizon"])):
            raise ValueError("Current train unknown raw has a complete horizon")
        rows.append({"origin_run": item["origin_run"], "category": item["category"],
                     "scenario_id": item["scenario_id"], "policy_id": item["policy_id"],
                     "evidence_role": item["evidence_role"],
                     "status": item["status"], "primary_full_horizon": primary["full_horizon"],
                     "full_observation_horizon": full["full_horizon"],
                     "raw_sha256": item["raw_sha256"], "score_match": True})
    return {"rechecked_raw": len(rows),
            "unknown_raw": sum(not row["primary_full_horizon"] for row in rows),
            "rows": rows}


def reload_model_cpu(extracted_root: str | Path, manifest: dict | None = None) -> dict:
    root = Path(extracted_root).resolve()
    manifest = verify_manifest(root) if manifest is None else manifest
    model_info = manifest["model"]
    model_dir = _within(root / model_info["relative_dir"], root)
    if (_sha(model_dir / "metadata.json") != model_info["metadata_sha256"] or
            _sha(model_dir / model_info["checkpoint"]) != model_info["checkpoint_sha256"]):
        raise ValueError("Model checkpoint/metadata SHA differs")
    import torch

    if model_info["kind"] == "v5_gru":
        from tools.policy_search.neural import _GRUNet, _weight_sha
        metadata = _read(model_dir / "metadata.json")
        model = _GRUNet(metadata["frame_dim"], metadata["context_dim"], metadata["hidden"])
        state = torch.load(model_dir / "model.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        if _weight_sha(model) != metadata["trained_weight_sha256"]:
            raise ValueError("Historical GRU reloaded weight SHA differs")
        frames = torch.zeros(1, 4, metadata["frame_dim"], dtype=torch.float32)
        context = torch.zeros(1, metadata["context_dim"], dtype=torch.float32)
    elif model_info["kind"] == "v6_best":
        from tools.policy_redesign.neural import load_cpu_for_repro
        model, example = load_cpu_for_repro(model_dir)
        frames, context = example["frames"], example["context"]
        if any(t.device.type != "cpu" for t in (frames, context)):
            raise ValueError("Portable v6 model example must be CPU tensors")
    else:
        raise ValueError("Unknown portable model kind")
    model.eval()
    with torch.inference_mode():
        prediction = model(frames, context)
    tensors = prediction if isinstance(prediction, (tuple, list)) else (prediction,)
    if not tensors or not all(isinstance(x, torch.Tensor) and x.device.type == "cpu"
                              and torch.isfinite(x).all().item() for x in tensors):
        raise ValueError("CPU model inference absent or nonfinite")
    return {"model_cpu_inference": True, "model_kind": model_info["kind"],
            "checkpoint": model_info["checkpoint"],
            "output_shapes": [list(x.shape) for x in tensors]}


def verify_all(extracted_root: str | Path) -> dict:
    manifest = verify_manifest(extracted_root)
    scores = recheck_selected_raw(extracted_root, manifest)
    model = reload_model_cpu(extracted_root, manifest)
    return {"manifest_valid": True, "selected_raw": len(manifest["examples"]),
            "historical_v5_raw": manifest["historical_v5_count"],
            "new_v6_raw": manifest["new_v6_count"],
            "new_v6_train_unknown_raw": manifest["new_v6_train_unknown_count"],
            "new_v6_matched_validation_raw": manifest[
                "new_v6_matched_validation_count"],
            "rechecked_raw": scores["rechecked_raw"],
            "unknown_raw": scores["unknown_raw"], **model}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-all", help="Verify extracted hashes, raw scores and CPU model")
    verify.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    if args.command == "verify-all":
        print(json.dumps(verify_all(args.root), ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
