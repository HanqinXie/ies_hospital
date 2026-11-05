"""Inventory new v4 code and render an honest empty-tree review patch.

This is deliberately not a diff against v8, v9, or any prior source version:
this workspace has no trustworthy Git/base tree. Source SHA-256 values are the
byte-faithful evidence; the human-readable patch normalizes line endings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


COMPARISON_BASIS = "new code represented as additions to an empty tree; not a cross-version diff"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve(root: Path, path: Path) -> Path:
    path = Path(path)
    return (path if path.is_absolute() else root / path).resolve()


def _relative(root: Path, path: Path) -> str:
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Symlink is not a reproducible inventory input: {path}")
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Inventory path leaves workspace: {path}") from error


def _read_stable(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Input changed while inventorying: {path}")
    return data


def _source_paths(root: Path) -> list[Path]:
    paths = set((root / "tools/reliability_first").glob("*.py"))
    paths.update((root / "tests").glob("test_reliability_first_*.py"))
    if (root / "pytest.ini").is_file():
        paths.add(root / "pytest.ini")
    return sorted((path for path in paths if path.is_file()), key=lambda path: _relative(root, path))


def _addition_patch(path: str, source: bytes) -> tuple[bytes, int]:
    text = source.decode("utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    trailing_newline = normalized.endswith("\n")
    if trailing_newline:
        lines.pop()
    if not lines or (len(lines) == 1 and lines[0] == ""):
        raise ValueError(f"Empty source cannot be represented by this review patch: {path}")
    patch = [f"diff --git a/{path} b/{path}\n", "new file mode 100644\n",
             "--- /dev/null\n", f"+++ b/{path}\n", f"@@ -0,0 +1,{len(lines)} @@\n"]
    for line in lines:
        patch.append("+" + line + "\n")
    if not trailing_newline:
        patch.append("\\ No newline at end of file\n")
    return "".join(patch).encode("utf-8"), len(lines)


def compile_change_inventory(workspace_root: Path, run_dir: Path,
                             command_logs: list[Path]) -> tuple[bytes, bytes]:
    """Return deterministic manifest/patch bytes; write no workspace files."""
    root = Path(workspace_root).resolve()
    run = _resolve(root, Path(run_dir))
    run_relative = _relative(root, run)
    if not command_logs:
        raise ValueError("At least one completed command log must be referenced")
    source_paths = _source_paths(root)
    if not source_paths:
        raise FileNotFoundError("No new v4 source files matched the declared scope")
    records = []
    patch_parts = []
    for path in source_paths:
        relative = _relative(root, path)
        source = _read_stable(path)
        addition, line_count = _addition_patch(relative, source)
        records.append({"path": relative, "bytes": len(source), "sha256": _sha(source),
                        "source_line_count": line_count})
        patch_parts.append(addition)
    patch_bytes = b"".join(patch_parts)
    log_records = []
    for candidate in command_logs:
        path = _resolve(root, Path(candidate))
        relative = _relative(root, path)
        if not path.is_relative_to(run):
            raise ValueError(f"Command log must belong to this run: {path}")
        data = _read_stable(path)
        record = {"path": relative, "bytes": len(data), "sha256": _sha(data)}
        if path.suffix.lower() == ".json":
            parsed = json.loads(data.decode("utf-8"))
            if isinstance(parsed, dict) and "exit_code" in parsed:
                record["recorded_exit_code"] = parsed["exit_code"]
        log_records.append(record)
    log_records.sort(key=lambda item: item["path"])
    command = ["python", "-B", "-m", "tools.reliability_first.change_inventory",
               run_relative, "--workspace-root", "."]
    for entry in log_records:
        command.extend(("--command-log", entry["path"]))
    manifest = {
        "schema": "reliability_first.new_code_inventory.v1",
        "comparison_basis": COMPARISON_BASIS,
        "source_scope": ["tools/reliability_first/*.py", "tests/test_reliability_first_*.py",
                         "pytest.ini"],
        "byte_fidelity": "Source SHA-256 values cover original bytes; patch text normalizes CRLF/CR to LF.",
        "source_file_count": len(records),
        "source_files": records,
        "patch_path": f"{run_relative}/review/NEW_CODE_FROM_EMPTY.patch",
        "patch_bytes": len(patch_bytes),
        "patch_sha256": _sha(patch_bytes),
        "command_log_references": log_records,
        "reproduction_command_from_workspace_root": command,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    return manifest_bytes, patch_bytes


def write_change_inventory(workspace_root: Path, run_dir: Path,
                           command_logs: list[Path]) -> dict:
    root = Path(workspace_root).resolve()
    run = _resolve(root, Path(run_dir))
    manifest_bytes, patch_bytes = compile_change_inventory(root, run, command_logs)
    review = run / "review"
    manifest_path = review / "FILE_CHANGE_MANIFEST.json"
    patch_path = review / "NEW_CODE_FROM_EMPTY.patch"
    for path in (manifest_path, patch_path):
        _relative(root, path)
        if path.exists():
            raise FileExistsError(f"Never overwrite a review artifact: {path}")
    review.mkdir(parents=True, exist_ok=True)
    with patch_path.open("xb") as stream:
        stream.write(patch_bytes)
    with manifest_path.open("xb") as stream:
        stream.write(manifest_bytes)
    if _sha(_read_stable(patch_path)) != _sha(patch_bytes) or _sha(_read_stable(manifest_path)) != _sha(manifest_bytes):
        raise RuntimeError("Written code inventory does not match computed byte hashes")
    return {"manifest_path": _relative(root, manifest_path), "manifest_sha256": _sha(manifest_bytes),
            "patch_path": _relative(root, patch_path), "patch_sha256": _sha(patch_bytes),
            "source_file_count": json.loads(manifest_bytes)["source_file_count"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--command-log", action="append", type=Path, required=True)
    args = parser.parse_args()
    result = write_change_inventory(args.workspace_root, args.run_dir, args.command_log)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
