"""Preserve unique C: portable history on E: before removing exact old copies.

The three portable copies were created by SHA-checked staging. This utility
checks their inventories and stage timestamps against those receipts, pins the
existing D: review ZIP, and copies every historical difference or later extra
file to E:. It never deletes files outside the three hard-coded C: roots.

Run ``stage`` first, then ``delete``. The latter repeats all source and backup
checks before removing anything. The full raw files are not re-hashed here;
their bytes are covered by the SHA-checked stage receipts and the pinned ZIP,
provided their size and modification time have not changed since staging.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from datetime import datetime, timezone
import zipfile


PROJECT = Path(__file__).resolve().parents[1]
RUN = PROJECT / "outputs" / "v43_control_improvement_20261014_run01"
DEST = Path(r"E:\Hospital_IES_Storage\portable_history_20261014")
REPORT = PROJECT / "outputs" / "storage_cleanup_20261016"
LEDGER = DEST / "PRESERVATION_LEDGER.json"
ARCHIVE = PROJECT / "CONTROL_IMPROVEMENT_REVIEW_PACKAGE.zip"
ARCHIVE_SHA = "5824b90a02b25f1e3f073b35626dc96eb39427b0cc9aba948a2f76831198ff6d"
PACKAGE_MANIFEST = RUN / "PACKAGE_CONTENTS.json"
PACKAGE_RECEIPT = RUN / "PACKAGE_VERIFICATION.json"
ROOTS = {
    "run01": (
        Path(r"C:\hospital_control_improvement_portable_20261014_run01"),
        RUN / "PORTABLE_STAGING_ATTEMPT01.json",
    ),
    "run02": (
        Path(r"C:\hospital_control_improvement_portable_20261014_run02"),
        RUN / "PORTABLE_STAGING_ATTEMPT02.json",
    ),
    "run03": (
        Path(r"C:\hospital_control_improvement_portable_20261014_run03"),
        RUN / "PORTABLE_STAGING.json",
    ),
}


def long_path(path: Path) -> str:
    absolute = os.path.abspath(str(path))
    if not (len(absolute) >= 3 and absolute[1:3] == ":\\"):
        raise RuntimeError(f"Expected a drive-qualified Windows path: {absolute}")
    return "\\\\?\\" + absolute


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(long_path(path), "rb") as stream:
        while block := stream.read(16 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with open(long_path(path), "r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected an object in {path}")
    return data


def relpath(raw: str) -> PurePosixPath:
    value = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or ":" in raw
        or value.is_absolute()
        or value.as_posix() != raw
        or any(part in (".", "..") for part in value.parts)
    ):
        raise RuntimeError(f"Unsafe manifest path: {raw!r}")
    return value


def indexed(rows: list[dict], label: str) -> dict[str, dict]:
    result = {}
    folded = set()
    for row in rows:
        key = row["path"]
        relpath(key)
        if key.casefold() in folded:
            raise RuntimeError(f"Duplicate path in {label}: {key}")
        folded.add(key.casefold())
        if int(row["bytes"]) < 0 or len(row["sha256"]) != 64:
            raise RuntimeError(f"Invalid size or hash in {label}: {key}")
        result[key] = row
    return result


def check_exact_root(root: Path) -> None:
    expected = Path("C:\\") / root.name
    if root != expected or root.name not in {
        Path(item[0]).name for item in ROOTS.values()
    }:
        raise RuntimeError(f"Refusing unexpected delete root: {root}")
    if not root.is_dir() or root.resolve(strict=True) != root:
        raise RuntimeError(f"Missing or redirected root: {root}")
    attrs = os.stat(long_path(root), follow_symlinks=False).st_file_attributes
    if attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise RuntimeError(f"Reparse-point root: {root}")


def inventory(root: Path) -> dict[str, os.stat_result]:
    check_exact_root(root)
    result = {}
    folded = set()
    prefix = long_path(root)
    def walk_error(error: OSError) -> None:
        raise error

    for dirpath, dirs, files in os.walk(prefix, topdown=True, followlinks=False, onerror=walk_error):
        for name in dirs + files:
            child = os.path.join(dirpath, name)
            info = os.stat(child, follow_symlinks=False)
            if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise RuntimeError(f"Reparse point inside protected copy: {child}")
        for name in files:
            child = os.path.join(dirpath, name)
            key = os.path.relpath(child, prefix).replace("\\", "/")
            relpath(key)
            if key.casefold() in folded:
                raise RuntimeError(f"Case-colliding path: {key}")
            folded.add(key.casefold())
            result[key] = os.stat(child, follow_symlinks=False)
    return result


def checked_copy(source: Path, target: Path, expected_hash: str) -> None:
    if sha256(source) != expected_hash:
        raise RuntimeError(f"Source hash changed: {source}")
    os.makedirs(long_path(target.parent), exist_ok=True)
    if os.path.exists(long_path(target)):
        if sha256(target) != expected_hash:
            raise RuntimeError(f"Existing E: copy has wrong hash: {target}")
    else:
        shutil.copy2(long_path(source), long_path(target))
    if sha256(target) != expected_hash:
        raise RuntimeError(f"E: copy failed verification: {target}")


def archive_index() -> dict[str, dict]:
    receipt = load_json(PACKAGE_RECEIPT)
    if (
        receipt["zip_sha256"] != ARCHIVE_SHA
        or ARCHIVE.stat().st_size != receipt["zip_bytes"]
        or sha256(ARCHIVE) != ARCHIVE_SHA
        or not receipt["crc_passed"]
    ):
        raise RuntimeError("D: review ZIP differs from its pinned verified receipt")
    manifest = load_json(PACKAGE_MANIFEST)
    rows = indexed(manifest["files"], "archive manifest")
    if receipt["included_byte_hashes_checked"] != len(rows):
        raise RuntimeError("Archive receipt and manifest member count disagree")
    with zipfile.ZipFile(long_path(ARCHIVE)) as bundle:
        names = bundle.namelist()
        manifest_member = PACKAGE_MANIFEST.relative_to(PROJECT).as_posix()
        if len(names) != len(set(names)) or set(names) != set(rows) | {manifest_member}:
            raise RuntimeError("ZIP member list does not match package manifest")
        if bundle.read(manifest_member) != PACKAGE_MANIFEST.read_bytes():
            raise RuntimeError("ZIP manifest differs from D: manifest")
    return rows


def inspect_all(base: dict[str, dict], *, copy: bool) -> dict:
    result = {
        "format": "hospital_ies.c_portable_preservation.v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "project": str(PROJECT),
        "archive": str(ARCHIVE),
        "archive_sha256": ARCHIVE_SHA,
        "destination": str(DEST),
        "stage_basis": "SHA-verified staging receipts plus unchanged size/mtime",
        "full_c_raw_rehashed": False,
        "runs": {},
        "evidence": [],
    }
    for label, (root, receipt_path) in ROOTS.items():
        stage = load_json(receipt_path)
        if Path(stage["destination"]) != root:
            raise RuntimeError(f"Stage receipt points to a different root: {label}")
        records = indexed(stage["files"], label)
        if stage["copied_file_count"] != len(records):
            raise RuntimeError(f"Stage count mismatch: {label}")
        if stage["copied_bytes"] != sum(row["bytes"] for row in records.values()):
            raise RuntimeError(f"Stage byte count mismatch: {label}")
        observed = inventory(root)
        missing = set(records) - set(observed)
        if missing:
            raise RuntimeError(f"Staged files missing from {label}: {sorted(missing)[:3]}")
        preserved = []
        covered_count = 0
        covered_bytes = 0
        stage_time = receipt_path.stat().st_mtime_ns
        for key, row in records.items():
            info = observed[key]
            source = root.joinpath(*relpath(key).parts)
            if info.st_size != row["bytes"] or info.st_mtime_ns > stage_time:
                raise RuntimeError(f"Staged file changed size or mtime: {source}")
            baseline = base.get(key)
            if baseline and baseline["bytes"] == row["bytes"] and baseline["sha256"] == row["sha256"]:
                covered_count += 1
                covered_bytes += info.st_size
                continue
            actual_hash = sha256(source)
            if actual_hash != row["sha256"]:
                raise RuntimeError(f"Historical file no longer matches stage receipt: {source}")
            target = DEST / label / Path(*relpath(key).parts)
            if copy:
                checked_copy(source, target, actual_hash)
            elif sha256(target) != actual_hash:
                raise RuntimeError(f"Historical file not intact on E: {target}")
            preserved.append({"path": key, "bytes": info.st_size, "sha256": actual_hash, "reason": "historical_stage_version"})
        for key in sorted(set(observed) - set(records)):
            source = root.joinpath(*relpath(key).parts)
            info = observed[key]
            actual_hash = sha256(source)
            target = DEST / label / Path(*relpath(key).parts)
            if copy:
                checked_copy(source, target, actual_hash)
            elif sha256(target) != actual_hash:
                raise RuntimeError(f"Extra file not intact on E: {target}")
            preserved.append({"path": key, "bytes": info.st_size, "sha256": actual_hash, "reason": "post_stage_extra"})
        if covered_count + len(preserved) != len(observed):
            raise RuntimeError(f"Unaccounted files in {label}")
        result["runs"][label] = {
            "source": str(root),
            "stage_manifest": str(receipt_path),
            "stage_manifest_sha256": sha256(receipt_path),
            "observed_files": len(observed),
            "observed_bytes": sum(item.st_size for item in observed.values()),
            "archive_covered_files": covered_count,
            "archive_covered_bytes": covered_bytes,
            "e_preserved_files": len(preserved),
            "e_preserved_bytes": sum(item["bytes"] for item in preserved),
            "preserved": preserved,
        }
    for source in [PACKAGE_MANIFEST, PACKAGE_RECEIPT, *(item[1] for item in ROOTS.values())]:
        expected_hash = sha256(source)
        target = DEST / "_evidence" / source.name
        if copy:
            checked_copy(source, target, expected_hash)
        elif sha256(target) != expected_hash:
            raise RuntimeError(f"Evidence file not intact on E: {target}")
        result["evidence"].append({"source": str(source), "destination": str(target), "sha256": expected_hash})
    return result


def stable_payload(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "created_utc"}


def write_json(path: Path, value: dict) -> None:
    os.makedirs(long_path(path.parent), exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(long_path(temporary), "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(long_path(temporary), long_path(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["stage", "delete"])
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("This guarded cleanup is intended for Windows only")
    if not DEST.is_dir() or DEST.resolve(strict=True) != DEST or DEST.drive.upper() != "E:":
        raise RuntimeError(f"E: preservation destination is missing or redirected: {DEST}")
    if args.mode == "stage" and LEDGER.exists():
        raise RuntimeError("Ledger already exists; run delete or inspect it manually")
    base = archive_index()
    measured = inspect_all(base, copy=args.mode == "stage")
    if args.mode == "stage":
        write_json(LEDGER, measured)
        write_json(REPORT / "PRESERVATION_LEDGER.json", measured)
        print(json.dumps({"mode": "stage", "runs": {
            key: {k: run[k] for k in ("observed_files", "observed_bytes", "archive_covered_files", "e_preserved_files", "e_preserved_bytes")}
            for key, run in measured["runs"].items()
        }, "ledger": str(LEDGER)}, ensure_ascii=False))
        return
    recorded = load_json(LEDGER)
    if stable_payload(measured) != stable_payload(recorded):
        raise RuntimeError("Current C:/D:/E: state differs from staged preservation ledger")
    if sha256(REPORT / "PRESERVATION_LEDGER.json") != sha256(LEDGER):
        raise RuntimeError("D: and E: preservation ledgers differ")
    before = shutil.disk_usage("C:\\").free
    completed = []
    for label, (root, _) in ROOTS.items():
        check_exact_root(root)
        # The exact, hard-coded, already inventoried root is the only delete target.
        shutil.rmtree(long_path(root))
        if os.path.exists(long_path(root)):
            raise RuntimeError(f"Deletion did not complete: {root}")
        completed.append(label)
        write_json(DEST / "DELETION_RESULT.json", {
            "deleted_roots": completed,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "c_free_before": before,
            "c_free_after": shutil.disk_usage("C:\\").free,
            "preservation_ledger_sha256": sha256(LEDGER),
        })
        print(f"Deleted exact verified root: {root}", flush=True)
    write_json(REPORT / "DELETION_RESULT.json", load_json(DEST / "DELETION_RESULT.json"))


if __name__ == "__main__":
    main()
