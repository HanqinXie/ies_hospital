"""Create a compact, byte-verified v4 reliability review archive.

Bulk raw continuations stay in place with a per-file external SHA-256 index.
One complete matched development raw group and one passing D0 raw example are
included so the package contains inspectable trajectories, not just summaries.
All paths recorded in the archive and receipts are workspace-relative.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import zipfile


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _relative(root: Path, path: Path) -> str:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Symlink cannot enter evidence package: {path}")
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Package path leaves workspace: {path}") from error


def _present(path: Path, label: str) -> Path:
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"Required real {label} absent/empty: {path}")
    return path


def _gzip_payload(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), dict):
        raise ValueError(f"Raw trajectory is not a real episode payload: {path}")
    return payload


def _development_sample(root: Path, run: Path, development_dir: Path,
                        manifest: dict) -> tuple[list[Path], str]:
    _relative(root, development_dir)
    if not development_dir.resolve().is_relative_to(run.resolve()):
        raise ValueError("Development comparison must belong to this v4 run")
    plan = _json(_present(development_dir / "registered_plan.json", "development registration"))
    if (plan.get("phase") != "development" or plan.get("final_blind_opened") is not False or
            plan.get("manifest_signature") != manifest.get("signature")):
        raise ValueError("Development plan phase/final seal/source signature invalid")
    policies = [spec.get("policy_id") for spec in plan.get("policy_specs", [])]
    if len(policies) < 2 or len(policies) != len(set(policies)) or "secure_milp" not in policies:
        raise ValueError("Matched development plan lacks unique secure and comparison policies")
    results = {}
    for path in sorted(development_dir.rglob("result.json")):
        record = _json(path)
        sid, policy = record.get("scenario_id"), record.get("policy_id")
        if sid not in plan.get("scenario_ids", []) or policy not in policies:
            raise ValueError(f"Unregistered development result: {path}")
        raw_name = record.get("trajectory_path")
        if not record.get("terminal") or not raw_name:
            continue
        raw = path.parent / raw_name
        if not raw.is_file() or raw.suffix != ".gz":
            continue
        if record.get("trajectory_sha256") and _sha(raw) != record["trajectory_sha256"]:
            raise ValueError(f"Development raw hash differs from registered result: {raw}")
        payload = _gzip_payload(raw)
        summary = payload["summary"]
        if summary.get("scenario_id") != sid or summary.get("policy_id") != policy:
            raise ValueError(f"Development raw case/policy identity mismatch: {raw}")
        results[(sid, policy)] = raw
    for sid in sorted(plan.get("scenario_ids", [])):
        if all((sid, policy) in results for policy in policies):
            return [results[(sid, policy)] for policy in policies], sid
    raise FileNotFoundError("No complete matched development raw trajectory group")


def _d0_sample(run: Path) -> list[Path]:
    for raw in sorted((run / "branches").rglob("D0_secure.json.gz")):
        folder = raw.parent
        comparison = folder / "D0_comparison.json"
        result = folder / "BRANCH_RESULT.json"
        if not comparison.is_file() or not result.is_file():
            continue
        if _json(comparison).get("passed") is not True or _json(result).get("D0_passed") is not True:
            continue
        payload = _gzip_payload(raw)
        if not payload["summary"].get("scenario_id"):
            continue
        selected = [raw]
        for optional in ("reference_capture.json.gz", "execution_snapshot.json", "CURRENT_CANDIDATES.json"):
            if (folder / optional).is_file():
                selected.append(folder / optional)
        return selected
    raise FileNotFoundError("No passing real D0 raw snapshot/example")


def _source_files(root: Path) -> set[Path]:
    selected = set()
    if (root / "pytest.ini").is_file():
        selected.add(root / "pytest.ini")
    for folder in ("tools/reliability_first", "tools/control_improvement",
                   "tools/integrated_learning", "src/hospital_ies", "tests", "configs", "scripts"):
        base = root / folder
        if base.is_dir():
            selected.update(path for path in base.rglob("*") if path.is_file()
                            and path.suffix.lower() in {".py", ".json", ".md", ".txt", ".toml", ".ps1"}
                            and not any(part in {"__pycache__", ".pytest_cache"} for part in path.parts))
    for folder in ("Hospital_Reliability_First_v4", "docs/v43"):
        base = root / folder
        if base.is_dir():
            selected.update(path for path in base.rglob("*") if path.is_file()
                            and path.suffix.lower() in {".md", ".json", ".txt"})
    inputs = root / "inputs"
    if inputs.is_dir():
        selected.update(path for path in inputs.rglob("*") if path.is_file()
                        and path.suffix.lower() in {".csv", ".json", ".idf", ".epw", ".txt"}
                        and path.stat().st_size <= 50 * 1024 * 1024)
    return selected


def _run_evidence_files(run: Path) -> set[Path]:
    selected = set()
    for folder_name in ("design", "learning", "logs", "workflow", "review", "report",
                        "closed_loop", "new_data", "branches", "soft_objective_probe",
                        "soft_objective_probe_v2", "unattended_launcher"):
        base = run / folder_name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() in {".zip", ".gz", ".pyc"}:
                continue
            if any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
                continue
            if folder_name == "branches" and path.name in {"execution_snapshot.json", "CURRENT_CANDIDATES.json"}:
                continue
            if folder_name == "new_data" and "checkpoints" in path.parts:
                continue
            if path.stat().st_size > 64 * 1024 * 1024:
                continue
            selected.add(path)
    selected.update(path for path in run.iterdir() if path.is_file()
                    and path.suffix.lower() in {".json", ".md", ".csv", ".txt", ".log", ".xml", ".ps1"}
                    and path.stat().st_size <= 64 * 1024 * 1024)
    return selected


def _record(root: Path, path: Path) -> dict:
    before = path.stat()
    digest = _sha(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Evidence changed during package hashing: {path}")
    return {"path": _relative(root, path), "bytes": after.st_size, "sha256": digest}


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _verify_archive(path: Path, entries: list[dict]) -> int:
    """One decompression pass checks each member SHA, size and ZIP CRC."""
    expected = {entry["path"]: entry for entry in entries}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(expected) or set(names) != set(expected) or len(names) != len(set(names)):
            raise ValueError("ZIP member set differs from frozen package manifest")
        for name in names:
            digest = hashlib.sha256()
            size = 0
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(block)
                    digest.update(block)
            if size != expected[name]["bytes"] or digest.hexdigest() != expected[name]["sha256"]:
                raise ValueError(f"ZIP member byte hash failed: {name}")
    return len(entries)


def build_review_package(workspace_root: Path, run_dir: Path, *, report_path: Path,
                         paired_summary_path: Path, sensitivity_path: Path,
                         development_dir: Path, package_path: Path | None = None,
                         receipt_path: Path | None = None,
                         external_index_path: Path | None = None,
                         manifest_path: Path | None = None) -> dict:
    root, run = Path(workspace_root).resolve(), Path(run_dir).resolve()
    _relative(root, run)
    report = _present(Path(report_path), "main report")
    paired = _present(Path(paired_summary_path), "paired summary")
    sensitivity = _present(Path(sensitivity_path), "sensitivity result")
    frozen_manifest = _json(_present(run / "new_data/global_manifest.json", "new source manifest"))
    _present(run / "design/source_proposal.json", "new source proposal")
    _present(run / "design/snapshot_plan_v2.json", "corrected frozen snapshot plan")
    _json(paired)
    _json(sensitivity)
    destination = Path(package_path) if package_path is not None else run / "review/RELIABILITY_FIRST_REVIEW_PACKAGE.zip"
    receipt = Path(receipt_path) if receipt_path is not None else run / "review/PACKAGE_VERIFICATION.json"
    external = Path(external_index_path) if external_index_path is not None else run / "review/EXTERNAL_FILE_SHA256.jsonl"
    package_manifest = Path(manifest_path) if manifest_path is not None else run / "review/PACKAGE_MANIFEST.json"
    building = destination.with_name(destination.name + ".building")
    for path in (destination, receipt, external, package_manifest, building, report, paired, sensitivity):
        _relative(root, path)
    for path in (destination, receipt, external, package_manifest, building):
        if path.exists():
            raise FileExistsError(f"Never overwrite a review artifact: {path}")
    samples, sample_sid = _development_sample(root, run, Path(development_dir), frozen_manifest)
    d0 = _d0_sample(run)
    nn_reports = list((run / "learning").rglob("FIT_REPORT.json"))
    if not any(_json(path).get("actual_train_device") == "cuda:0" and
               _json(path).get("optimizer_updates", 0) > 0 for path in nn_reports):
        raise FileNotFoundError("No actual CUDA NN fit evidence for model package")
    selected = _source_files(root) | _run_evidence_files(run) | set(samples + d0)
    selected.update((report, paired, sensitivity))
    selected = {path for path in selected if path.is_file() and path not in
                {destination, receipt, external, package_manifest, building}}
    included = [_record(root, path) for path in sorted(selected, key=lambda p: _relative(root, p))]
    included_paths = {entry["path"] for entry in included}
    excluded = []
    for path in sorted((path for path in run.rglob("*") if path.is_file()),
                       key=lambda p: _relative(root, p)):
        relative = _relative(root, path)
        if relative not in included_paths and path not in {destination, receipt, external, package_manifest, building}:
            item = _record(root, path)
            item["reason"] = "bulk raw/unbundled evidence; retained at original workspace path"
            excluded.append(item)
    index_bytes = b"".join((json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                           for entry in excluded)
    index_record = {"path": _relative(root, external), "bytes": len(index_bytes),
                    "sha256": hashlib.sha256(index_bytes).hexdigest()}
    package_spec = {"schema": "reliability_first.portable_review.v1",
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                    "main_report_path": _relative(root, report),
                    "paired_summary_path": _relative(root, paired),
                    "sensitivity_path": _relative(root, sensitivity),
                    "development_plan_path": _relative(root, Path(development_dir) / "registered_plan.json"),
                    "sampled_development_scenario_id": sample_sid,
                    "sampled_raw_paths": [_relative(root, path) for path in samples + d0],
                    "included_files": included + [index_record],
                    "excluded_external_file_count": len(excluded),
                    "external_file_index_path": index_record["path"],
                    "bulk_data_rule": "All excluded v4 run files are individually SHA-indexed; no raw outcome is deleted or converted to zero.",
                    "package_manifest_self_hash": "omitted; the manifest cannot hash itself"}
    manifest_bytes = _json_bytes(package_spec)
    manifest_record = {"path": _relative(root, package_manifest), "bytes": len(manifest_bytes),
                       "sha256": hashlib.sha256(manifest_bytes).hexdigest()}
    entries = included + [index_record, manifest_record]
    destination.parent.mkdir(parents=True, exist_ok=True)
    external.parent.mkdir(parents=True, exist_ok=True)
    package_manifest.parent.mkdir(parents=True, exist_ok=True)
    with external.open("xb") as stream:
        stream.write(index_bytes)
    with package_manifest.open("xb") as stream:
        stream.write(manifest_bytes)
    with zipfile.ZipFile(building, "x", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as archive:
        for entry in included:
            path = root / entry["path"]
            mode = zipfile.ZIP_STORED if path.suffix.lower() in {".gz", ".npz", ".pt", ".joblib", ".npy"} else zipfile.ZIP_DEFLATED
            archive.write(path, entry["path"], compress_type=mode)
        archive.write(external, index_record["path"])
        archive.write(package_manifest, manifest_record["path"])
    verified = _verify_archive(building, entries)
    building.rename(destination)
    result = {"schema": "reliability_first.package_verification.v1",
              "zip_path": _relative(root, destination), "zip_bytes": destination.stat().st_size,
              "zip_sha256": _sha(destination), "crc_passed": True,
              "included_byte_hashes_checked": verified, "files": len(entries),
              "included_original_files": len(included),
              "excluded_external_files_indexed": len(excluded),
              "external_index_path": index_record["path"],
              "external_index_sha256": index_record["sha256"],
              "package_manifest_path": manifest_record["path"],
              "package_manifest_sha256": manifest_record["sha256"],
              "sampled_development_scenario_id": sample_sid,
              "sampled_raw_paths": package_spec["sampled_raw_paths"],
              "finished_utc": datetime.now(timezone.utc).isoformat()}
    with receipt.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--paired-summary", type=Path, required=True)
    parser.add_argument("--sensitivity", type=Path, required=True)
    parser.add_argument("--development-dir", type=Path, required=True)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--external-index", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    result = build_review_package(args.workspace_root, args.run_dir,
                                  report_path=args.report, paired_summary_path=args.paired_summary,
                                  sensitivity_path=args.sensitivity, development_dir=args.development_dir,
                                  package_path=args.package, receipt_path=args.receipt,
                                  external_index_path=args.external_index, manifest_path=args.manifest)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
