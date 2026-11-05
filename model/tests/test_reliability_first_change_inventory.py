"""New-code inventory must be byte-bound and honest about its comparison basis."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest

from tools.reliability_first.change_inventory import (
    compile_change_inventory,
    write_change_inventory,
)


def test_new_code_patch_is_deterministic_and_does_not_claim_old_version_diff(tmp_path):
    root = tmp_path
    run = root / "outputs" / "v4_run"
    source = root / "tools" / "reliability_first" / "new_policy.py"
    test = root / "tests" / "test_reliability_first_new_policy.py"
    unrelated = root / "tools" / "control_improvement" / "old.py"
    config = root / "pytest.ini"
    for path, content in (
        (source, "def choose():\n    return '安全'\n"),
        (test, "def test_choose():\n    assert True\n"),
        (unrelated, "old = True\n"),
        (config, "[pytest]\ntestpaths = tests\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    log = run / "logs" / "actual_command.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"command": ["python", "-m", "pytest"], "exit_code": 0}), encoding="utf-8")

    manifest_a, patch_a = compile_change_inventory(root, run, [log])
    manifest_b, patch_b = compile_change_inventory(root, run, [log])
    assert manifest_a == manifest_b and patch_a == patch_b
    inventory = json.loads(manifest_a)
    assert inventory["comparison_basis"] == "new code represented as additions to an empty tree; not a cross-version diff"
    assert len(inventory["source_files"]) == 3
    assert {entry["path"] for entry in inventory["source_files"]} == {
        "tools/reliability_first/new_policy.py",
        "tests/test_reliability_first_new_policy.py",
        "pytest.ini",
    }
    assert inventory["patch_sha256"] == hashlib.sha256(patch_a).hexdigest()
    assert inventory["command_log_references"][0]["sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()
    assert inventory["command_log_references"][0]["recorded_exit_code"] == 0
    patch = patch_a.decode("utf-8")
    assert "--- /dev/null\n+++ b/tools/reliability_first/new_policy.py\n" in patch
    assert "+    return '安全'\n" in patch
    assert "old.py" not in patch

    if shutil.which("git"):
        empty_tree = root / "empty_tree"
        empty_tree.mkdir()
        patch_file = root / "new_code.patch"
        patch_file.write_bytes(patch_a)
        subprocess.run(["git", "apply", "--check", str(patch_file)], cwd=empty_tree, check=True)
        subprocess.run(["git", "apply", str(patch_file)], cwd=empty_tree, check=True)
        assert (empty_tree / "tools/reliability_first/new_policy.py").read_bytes() == source.read_bytes()

    receipt = write_change_inventory(root, run, [log])
    assert receipt["source_file_count"] == 3
    assert (run / "review/FILE_CHANGE_MANIFEST.json").read_bytes() == manifest_a
    assert (run / "review/NEW_CODE_FROM_EMPTY.patch").read_bytes() == patch_a
    with pytest.raises(FileExistsError):
        write_change_inventory(root, run, [log])


def test_change_inventory_rejects_missing_or_unscoped_command_log(tmp_path):
    root = tmp_path
    run = root / "outputs" / "v4_run"
    source = root / "tools" / "reliability_first" / "new.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="command log"):
        compile_change_inventory(root, run, [])
    outside = root / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="run"):
        compile_change_inventory(root, run, [outside])
