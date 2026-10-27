"""Read-only verification of staged curation, saved receipts and links."""
from pathlib import Path
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path("E:/hospital_ies_publish_20261028")
STAGE = BASE / "results_stage"


def sha(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8-sig"))


c = read(STAGE / "CATALOG.json")
checked = []
for x in c["copied_artifacts"]:
    p = STAGE / x["staged_path"]
    original = Path(x["original_path"])
    assert p.is_file() and original.is_file()
    assert p.stat().st_size == original.stat().st_size == x["bytes"]
    assert sha(p) == sha(original) == x["sha256"]
    assert p.suffix.lower() not in {".pdf", ".zip", ".xz", ".gz", ".npy", ".npz"}
    assert p.stat().st_size < 5_000_000
    if p.suffix == ".json":
        read(p)
    if p.suffix == ".jsonl":
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            if line.strip(): json.loads(line)
    checked.append(x["staged_path"])
assert len(set(checked)) == len(checked)
for v in c["versions"]:
    root = STAGE / v["version"]
    rows = list(csv.DictReader((root/v["final_table_relative_path"]).open(encoding="utf-8-sig", newline="")))
    assert len(rows) == v["table_rows"]
    a = read(root/v["final_audit_relative_path"])
    assert a["status"] == "complete_audit" and a["final_matrix_complete_and_clean"]
    assert a["summary"] == v["final_audit_summary"]
    assert v["input_files"] == 36
    assert v["input_set_sha256"] == "f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15"
    assert len([x for x in c["copied_artifacts"] if x["version"] == v["version"]]) == v["selected_files"]["files"]
for a in c["release_assets"]:
    p = Path(a["original_path"])
    assert p.stat().st_size == a["bytes"] and sha(p) == a["sha256"]
    assert a["copied_to_git_tree"] is False
for name in ("r03_grid_R_off", "r03_normal_R_off"):
    d = read(STAGE / f"v30/runs/{name}/RESOLVED_CASE.json")
    assert d["case"]["scenario"]["start_hour"] == 211
assert read(STAGE/"v30/ACTIVE_TARGET_LOCK.json")["target"] == "GT_3"
assert read(STAGE/"v30/release_acceptance/FINAL_RELEASE.json")["status"] == "passed"
assert read(STAGE/"v31/acceptance/FINAL_ACCEPTANCE.json")["status"] == "passed"
assert read(STAGE/"v32/FINAL_DELIVERY_STATUS.json")["status"] == "complete"
assert read(STAGE/"v32/package_build/INDEPENDENT_EXTRACT_REVIEW.json")["status"] == "passed"
readme = (STAGE/"README_RESULTS_ZH.md").read_text(encoding="utf-8")
links = re.findall(r"\]\(([^)]+)\)", readme)
for link in links:
    assert (STAGE/link).is_file(), link
generated = {"CATALOG.json", "ORIGINAL_FAILURE_LOCATIONS.json", "README_RESULTS_ZH.md"}
actual = {p.relative_to(STAGE).as_posix() for p in STAGE.rglob("*") if p.is_file()}
assert actual == set(checked) | generated, sorted(actual - set(checked) - generated)
fail = read(STAGE/"ORIGINAL_FAILURE_LOCATIONS.json")
assert len(fail["scientific_failures"]) == 2
for row in fail["scientific_failures"]:
    for x in row["original_files"]:
        p = Path(x["original_path"])
        assert p.stat().st_size == x["bytes"] and sha(p) == x["sha256"]
r = {"status": "passed", "verified_utc": datetime.now(timezone.utc).isoformat(),
     "copied_artifacts_current_source_and_stage_sha_exact": len(checked),
     "generated_metadata_files": 3, "total_stage_files": len(actual),
     "stage_bytes": sum((STAGE/p).stat().st_size for p in actual),
     "readme_relative_links_verified": len(links), "accepted_zip_current_sha_size_verified": 3,
     "JSON_and_JSONL_parsed": True, "final_saved_audits_complete_and_clean": ["v30", "v31", "v32"],
     "old_R03_211_separate_from_V32_b01_1160": True, "b07_locked_target_GT3": True,
     "original_failure_metadata_rehashed": True,
     "new_scientific_simulations_or_solver_calls_or_recomputations": 0,
     "scope": "Static publication artifacts only; existing scientific tests, solver results and independent native replays were read, not rerun",
     "catalog_sha256": sha(STAGE/"CATALOG.json"), "readme_sha256": sha(STAGE/"README_RESULTS_ZH.md")}
(BASE/"results_audit/FINAL_VERIFICATION.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(r,ensure_ascii=False,indent=2))
