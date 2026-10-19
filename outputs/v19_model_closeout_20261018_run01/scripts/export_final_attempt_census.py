"""Read-only census of every registered B19 pair; never opens raw/annual/CSV."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[3]
B19 = SCRIPT.parents[1]
MODEL = "M19_model_closeout"
EXPECTED = dict(registered=506, actual_started=466, terminal=466, results=466,
                completed=465, complete_registered=465, complete_audit_passed=465,
                failed_native=1, failed_other=0, not_started=40,
                started_without_terminal=0)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def classify(record):
    """Full-window and prefix audit are deliberately separate predicates."""
    if record is None:
        return dict(completed=False, complete_registered=False,
                    complete_audit_passed=False, failed_native=False, failed_other=False)
    analysis = record.get("analysis") or {}
    complete = analysis.get("windows", {}).get("registered_full", {}).get("complete") is True
    audit = analysis.get("v19", {}).get("episode_audit", {})
    done = record.get("status") == "completed"
    error = record.get("error") or {}
    native = (not done and record.get("status") == "model_constraint_infeasible"
              and "Native finite recovery exhausted" in error.get("message", ""))
    return dict(completed=done, complete_registered=complete,
        complete_audit_passed=done and complete and audit.get("status") == "passed"
            and audit.get("complete_registered_interval") is True,
        failed_native=native, failed_other=not done and not native)


def cohort_name(phase):
    return ("confirmation" if phase.startswith("confirmation_final_") else
            "final_nonconfirmation" if "_final_" in phase or phase.startswith("sensitivity_") else "development")


def self_test():
    base = {"status": "completed", "analysis": {"windows": {"registered_full": {"complete": True}},
        "v19": {"episode_audit": {"status": "passed", "complete_registered_interval": True}}}}
    assert classify(base)["complete_audit_passed"] is True
    failed = json.loads(json.dumps(base)); failed["status"] = "model_constraint_infeasible"
    failed["error"] = {"message": "Native finite recovery exhausted"}
    failed["analysis"]["windows"]["registered_full"]["complete"] = False
    failed["analysis"]["v19"]["episode_audit"]["complete_registered_interval"] = False
    flags = classify(failed)
    assert flags["failed_native"] is True and flags["complete_audit_passed"] is False
    missing = {"status": "completed"}
    assert classify(missing)["complete_audit_passed"] is False
    assert classify(None)["completed"] is False
    assert classify({"status": "unrecognized_error"})["failed_other"] is True
    assert digest({"x": "\u533b\u9662"}) == hashlib.sha256(b'{"x":"\\u533b\\u9662"}').hexdigest()
    assert cohort_name("sensitivity_contact025_r1") == "final_nonconfirmation"
    assert cohort_name("sensitivity_open1_r1") == "final_nonconfirmation"
    assert cohort_name("energy_tradeoff_development") == "development"
    print("9 finite census guards passed; prefix audit is not full-window completion")


def collect():
    inputs, issues, pair_rows, batch_rows = {}, [], [], []

    def require(condition, message):
        if not condition:
            issues.append(message)

    def read(path, *, lines=False):
        path = Path(path).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError("Read escaped project root")
        name = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() not in (".json", ".jsonl"):
            raise ValueError("Census metadata reader accepts JSON/JSONL only")
        data = path.read_bytes()
        inputs[name] = dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        text = data.decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)

    freeze_path = B19 / "MODEL_FREEZE_CLOSEOUT01.json"
    freeze = read(freeze_path)
    frozen_source = freeze["source_sha256"]
    current_epoch = digest(frozen_source)
    require(freeze.get("signature") == digest({k: v for k, v in freeze.items() if k != "signature"}),
            "Final freeze self-signature differs")
    require(freeze.get("model_id") == MODEL, "Final freeze model differs")
    source_check = []
    for name, expected in sorted(frozen_source.items()):
        path = ROOT / name
        if name.startswith("inputs/") or path.suffix.lower() in (".gz", ".csv", ".zip"):
            raise ValueError("Unexpected annual/raw source binding: " + name)
        actual = sha(path)
        source_check.append(dict(path=name, bytes=path.stat().st_size, expected_sha256=expected,
                                 actual_sha256=actual, passed=actual == expected))
        require(actual == expected, "Current frozen source SHA differs: " + name)

    for plan_path in sorted((B19 / "experiments").glob("*/plan.json")):
        directory = plan_path.parent
        plan, manifest = read(plan_path), read(directory / "manifest.json")
        batch_name = directory.name
        epoch = digest(plan["source_sha256"])
        phase = plan["phase"]
        cohort = cohort_name(phase)
        specs = [item["policy_id"] for item in plan["policy_specs"]]
        registered = [(scene, policy) for scene in plan["scenario_ids"] for policy in specs]
        require(len(registered) == len(set(registered)) == plan["registered_pairs"],
                batch_name + ": registered pair count/uniqueness differs")
        require(plan.get("model_id") == manifest.get("execution_model_id") == MODEL,
                batch_name + ": model differs")
        require(plan["source_sha256"] == manifest["source_sha256"], batch_name + ": source maps differ")
        require(plan["manifest_sha256"] == sha(directory / "manifest.json") and
                plan["manifest_signature"] == manifest["signature"], batch_name + ": manifest binding differs")
        journal = directory / "ATTEMPTS.jsonl"
        events = read(journal, lines=True) if journal.is_file() else []
        require(all(x.get("event") in ("started", "finished") for x in events), batch_name + ": unknown journal event")
        starts = [x for x in events if x.get("event") == "started"]
        ends = [x for x in events if x.get("event") == "finished"]
        key = lambda x: (x.get("scenario_id"), x.get("policy_id"))
        sc, ec = Counter(map(key, starts)), Counter(map(key, ends))
        require(all(x == 1 for x in sc.values()) and all(x == 1 for x in ec.values()),
                batch_name + ": duplicate actual attempt identity; not silently deduplicated")
        require(set(sc) <= set(registered) and set(ec) <= set(sc), batch_name + ": unexpected actual pair")
        saved = {}
        for result_path in sorted(directory.glob("pairs/*/RESULT.json")):
            record = read(result_path)
            identity = key(record)
            require(identity not in saved, batch_name + ": duplicate RESULT identity")
            saved[identity] = (result_path, record)
        require(set(saved) == set(sc), batch_name + ": started/RESULT sets differ")
        finish = {key(x): x for x in ends}
        begin = {key(x): x for x in starts}
        for scene, policy in registered:
            identity = (scene, policy)
            result_path, record = saved.get(identity, (None, None))
            flags = classify(record)
            analysis = (record or {}).get("analysis") or {}
            audit = analysis.get("v19", {}).get("episode_audit", {})
            error = (record or {}).get("error") or {}
            if record is not None:
                require(record.get("model_id") == MODEL and record.get("manifest_signature") == manifest["signature"]
                    and record.get("model_config_signature") == manifest["model_config_signature"],
                    batch_name + "/" + policy + ": RESULT manifest/model binding differs")
                require(result_path.parent.name == scene + "__" + policy, batch_name + ": RESULT folder identity differs")
            if identity in finish and record is not None:
                terminal = finish[identity]
                require(terminal.get("result_sha256") == sha(result_path)
                    and terminal.get("raw_sha256") == record.get("raw_sha256")
                    and terminal.get("status") == record.get("status"), batch_name + "/" + policy + ": terminal binding differs")
                require(terminal.get("analysis_status") == "saved", batch_name + "/" + policy + ": analysis not saved")
            row = dict(batch=batch_name, phase=phase, cohort=cohort, source_binding_digest=epoch,
                source_file_count=len(plan["source_sha256"]), scenario_id=scene, policy_id=policy,
                actual_started=sc[identity], terminal=ec[identity], results=int(record is not None),
                not_started=int(identity not in sc), started_without_terminal=max(0, sc[identity]-ec[identity]),
                status=(record or {}).get("status", "not_started"),
                result_path=result_path.relative_to(ROOT).as_posix() if result_path else None,
                result_sha256=sha(result_path) if result_path else None,
                raw_producer_sha256=(record or {}).get("raw_sha256"), raw_opened=False,
                observed_until_minute=analysis.get("observed_until_minute"),
                requested_end_minute=analysis.get("requested_end_minute"),
                saved_prefix_audit_status=audit.get("status"),
                audit_complete_registered_interval=audit.get("complete_registered_interval"),
                error_type=error.get("type"), error_classification=error.get("classification"),
                error_message=error.get("message"),
                started_utc=begin.get(identity, {}).get("utc"), finished_utc=finish.get(identity, {}).get("utc"),
                **flags)
            pair_rows.append(row)
        terminal_path = directory / "BATCH_RESULT.json"
        terminal = read(terminal_path) if terminal_path.is_file() else None
        if terminal is not None:
            require(terminal["registered_pairs"] == len(registered) and terminal["actual_attempts"] == len(starts)
                and terminal["complete_full"] == sum(classify(v[1])["complete_registered"] for v in saved.values()),
                batch_name + ": terminal batch counts differ")
            require(terminal.get("plan_sha256") == sha(plan_path) and terminal.get("manifest_sha256") == sha(directory / "manifest.json")
                and terminal.get("source_unchanged") is True, batch_name + ": terminal source/metadata binding differs")
        else:
            require(not starts and not saved, batch_name + ": missing terminal batch has actual work")
        batch_rows.append(dict(batch=batch_name, phase=phase, cohort=cohort, source_binding_digest=epoch,
            source_file_count=len(plan["source_sha256"]), registered=len(registered), actual_started=len(starts),
            terminal=len(ends), results=len(saved), batch_result_present=terminal is not None,
            plan_sha256=sha(plan_path), manifest_sha256=sha(directory / "manifest.json"),
            attempts_sha256=sha(journal) if journal.is_file() else None,
            batch_result_sha256=sha(terminal_path) if terminal is not None else None))

    def aggregate(rows):
        result = dict(registered=len(rows))
        for name in EXPECTED:
            if name != "registered":
                result[name] = sum(int(row[name]) for row in rows)
        result["status_counts"] = dict(sorted(Counter(row["status"] for row in rows).items()))
        result["batch_count"] = len({row["batch"] for row in rows})
        return result

    def groups(field):
        grouped = defaultdict(list)
        for row in pair_rows:
            grouped[row[field]].append(row)
        return {name: aggregate(rows) for name, rows in sorted(grouped.items())}

    actual = aggregate(pair_rows)
    expectation = {name: actual[name] == value for name, value in EXPECTED.items()}
    for name, passed in expectation.items():
        require(passed, "Actual count differs from requested expectation: " + name)
    current = [row for row in pair_rows if row["source_binding_digest"] == current_epoch]
    current_cohorts = {name: aggregate([row for row in current if row["cohort"] == name])
                       for name in ("development", "final_nonconfirmation", "confirmation")}
    require(current_cohorts["development"]["registered"] == 48 and
        current_cohorts["final_nonconfirmation"]["registered"] == 310 and
        current_cohorts["confirmation"]["registered"] == 96, "Current frozen cohort sizes differ")
    for name, row in current_cohorts.items():
        require(row["registered"] == row["completed"] == row["complete_audit_passed"],
                "Current cohort incomplete or audit unknown: " + name)
    for name, row in inputs.items():
        require(sha(ROOT / name) == row["sha256"], "Metadata changed during census: " + name)
    for row in source_check:
        require(sha(ROOT / row["path"]) == row["actual_sha256"], "Frozen source changed during census: " + row["path"])
    return dict(schema="hospital_ies.v19.final_attempt_census.v1", status="passed" if not issues else "failed_unknown",
        created_utc=datetime.now(timezone.utc).isoformat(), script_sha256=sha(SCRIPT),
        final_freeze_path=freeze_path.relative_to(ROOT).as_posix(), final_freeze_sha256=sha(freeze_path),
        final_freeze_signature=freeze["signature"], current_source_binding_digest=current_epoch,
        current_source_file_count=len(frozen_source), current_source_actual_sha_verified=all(x["passed"] for x in source_check),
        total=actual, requested_expectations=EXPECTED, expectation_checks=expectation,
        by_source_epoch=groups("source_binding_digest"), by_phase=groups("phase"), by_policy=groups("policy_id"),
        current_frozen_cohorts=current_cohorts, batches=batch_rows, registered_pair_rows=pair_rows,
        missing_batch_result=[row["batch"] for row in batch_rows if not row["batch_result_present"]],
        noncompleted_actual=[row for row in pair_rows if row["actual_started"] and not row["completed"]],
        input_sha256=list(inputs.values()), current_source_sha_review=source_check,
        input_census_digest=digest(list(inputs.values())), issues=issues,
        reading_boundary=dict(raw_opens=0, annual_inputs_loaded=0, csv_opens=0, physical_episodes_started=0,
            production_files_modified=0, historical_outputs_modified=0),
        semantics=["Counts derive from all plan registrations, ATTEMPTS started/finished and producer RESULT records.",
            "Repeated cumulative BATCH_RESULT.run_attempt_accounting snapshots are not added together.",
            "complete_audit_passed requires completed status, registered_full.complete and a passed audit covering the full registered interval.",
            "A passed audit of a failed saved prefix is retained; it does not establish full-window completion.",
            "Raw provenance is the terminal/producer SHA agreement only; raw bytes are not reopened by this census."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="final_attempt_census01")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.name):
        parser.error("Use a simple immutable review name")
    destination = B19 / "review" / args.name
    if destination.exists():
        raise FileExistsError("Census attempt is immutable; choose a new name")
    result = collect()
    destination.mkdir(parents=True)
    result_path = destination / "FINAL_ATTEMPT_CENSUS.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = result["total"]
    lines = ["# Final registered-attempt census", "", "Status: " + result["status"], "",
        f"Actual metadata census: {total['registered']} registered; {total['actual_started']} started; {total['terminal']} terminal; "
        f"{total['completed']} completed with {total['complete_audit_passed']} complete registered-window audits; "
        f"{total['failed_native']} failed native optimization; {total['not_started']} not started.", "",
        "| Source epoch | Registered | Started | Complete full audit | Failed native | Not started |",
        "|---|---:|---:|---:|---:|---:|"]
    for epoch, row in result["by_source_epoch"].items():
        lines.append(f"| `{epoch}` | {row['registered']} | {row['actual_started']} | {row['complete_audit_passed']} | {row['failed_native']} | {row['not_started']} |")
    lines += ["", "Current frozen source cohorts:", ""]
    for name, row in result["current_frozen_cohorts"].items():
        lines.append(f"- {name}: {row['batch_count']} batches / {row['registered']} pairs; completed and complete registered-window audit passed {row['complete_audit_passed']}.")
    lines += ["", "The retained old native failure is `dev_long_r1_000` / C19, observed to 255/1065 min at "
        "`arrived_current_P3` rank 20. Its saved-prefix audit passed while `complete_registered_interval=false`; "
        "that prefix is not a successful full trajectory.", "", f"JSON SHA256: `{sha(result_path)}`.", "",
        "All plan/manifest/terminal/producer-result SHA bindings and the current frozen source bytes were checked. "
        "No raw, annual input, CSV, build, stage or physical replay was opened/executed. "
        "Cumulative campaign snapshots in individual terminal files were not summed."]
    if result["issues"]:
        lines += ["", "Issues:", "", *["- " + x for x in result["issues"]]]
    md = destination / "FINAL_ATTEMPT_CENSUS.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    receipt = dict(status=result["status"], outputs=[dict(path=p.relative_to(ROOT).as_posix(),
        bytes=p.stat().st_size, sha256=sha(p)) for p in (result_path, md)],
        script_sha256=sha(SCRIPT), input_census_digest=result["input_census_digest"],
        physical_episodes_started=0, raw_opens=0)
    (destination / "RECEIPT.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(status=result["status"], total=total, issues=result["issues"], outputs=receipt["outputs"]), ensure_ascii=True))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
