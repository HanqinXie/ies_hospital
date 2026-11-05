"""Read-only, evidence-based audit of the reliability-first development run.

An audit status describes evidence completeness/integrity, never policy benefit.
Started attempts, failed executions and unknown outcomes remain in denominators.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import zipfile


BUDGET_CAPS = {
    "new_specifications": 1200,
    "baseline_started": 2400,
    "snapshots_started": 600,
    "action_future_started": 24000,
    "other_full_started": 8000,
    "method_classes": 8,
    "configurations": 60,
    "fit_started": 180,
    "aggregation_rounds": 3,
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _status(issues: list[str], missing: list[str]) -> str:
    return "failed" if issues else "incomplete" if missing else "passed"


def _safe_under(root: Path, relative: str) -> Path | None:
    path = (root / relative).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def audit_snapshot_groups(manifest: dict, plan: list[dict]) -> dict:
    """Check frozen group/role identity and predeclared snapshot selection."""
    issues, missing = [], []
    scenarios = {row["id"]: row for row in manifest.get("scenarios", []) if "id" in row}
    splits = manifest.get("splits", {})
    seen = set()
    roles_by_root = defaultdict(set)
    roles_by_group = defaultdict(set)
    for scenario_id, scenario in scenarios.items():
        split = splits.get(scenario_id)
        if split is None:
            missing.append(f"split missing for {scenario_id}")
            continue
        role = split.get("experiment_role")
        if role != scenario.get("experiment_role"):
            issues.append(f"scenario/split role mismatch: {scenario_id}")
        roles_by_root[scenario.get("root_family_id")].add(role)
        roles_by_group[split.get("group_id")].add(role)
        if split.get("is_final_blind") or role in ("final", "final_blind"):
            issues.append(f"final-blind source entered new manifest: {scenario_id}")
    for root, roles in roles_by_root.items():
        if len(roles) > 1:
            issues.append(f"root crosses experiment roles: {root}")
    for group, roles in roles_by_group.items():
        if len(roles) > 1:
            issues.append(f"group_id crosses experiment roles: {group}")
    intervals_by_role = defaultdict(list)
    for scenario_id, split in splits.items():
        left, right = split.get("raw_start_minute"), split.get("raw_end_minute")
        if left is not None and right is not None:
            intervals_by_role[split.get("experiment_role")].append(
                (float(left) - float(split.get("embargo_minutes_each_side", 0)),
                 float(right) + float(split.get("embargo_minutes_each_side", 0)), scenario_id))
    roles = list(intervals_by_role)
    for i, role in enumerate(roles):
        for other in roles[i + 1:]:
            for a, b, sid in intervals_by_role[role]:
                for c, d, oid in intervals_by_role[other]:
                    if max(a, c) < min(b, d):
                        issues.append(f"raw support/embargo overlaps roles: {sid}, {oid}")
                        if len(issues) >= 25:
                            break
                if len(issues) >= 25:
                    break
    for row in plan:
        sid = row.get("scenario_id")
        scenario, split = scenarios.get(sid), splits.get(sid)
        if scenario is None or split is None:
            issues.append(f"snapshot references unregistered scenario: {sid}")
            continue
        key = (sid, row.get("snapshot_minute"))
        if key in seen:
            issues.append(f"duplicate snapshot scenario/clock: {key}")
        seen.add(key)
        if row.get("root_family_id") != scenario.get("root_family_id"):
            issues.append(f"root_family_id mismatch: {sid}")
        if row.get("experiment_role") != split.get("experiment_role"):
            issues.append(f"experiment_role mismatch: {sid}")
        if row.get("group_id") != split.get("group_id"):
            issues.append(f"group_id mismatch: {sid}")
        if row.get("is_final_blind") or row.get("selection_uses_future_event_clock"):
            issues.append(f"final/future-selected snapshot forbidden: {sid}")
        if row.get("conditional_futures", 0) < 4:
            missing.append(f"fewer than four future designs: {sid}")
    if len(plan) > BUDGET_CAPS["snapshots_started"]:
        issues.append("registered snapshot count exceeds budget")
    if not plan:
        missing.append("no frozen snapshot plan")
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                registered_scenarios=len(scenarios), registered_snapshot_rows=len(plan),
                snapshot_roots=len({row.get("root_family_id") for row in plan}),
                role_counts=dict(Counter(row.get("experiment_role") for row in plan)))


def audit_baseline_attempts(manifest: dict, journals: list[Path]) -> dict:
    """Count every start, including retries, errors and unfinished processes."""
    issues, missing = [], []
    registered = {row["id"] for row in manifest.get("scenarios", [])}
    policies = tuple(manifest.get("policies", ("priority_milp", "secure_milp")))
    expected_keys = {(sid, policy) for sid in registered for policy in policies}
    signature = manifest.get("signature")
    started = Counter()
    finished = defaultdict(list)
    statuses = Counter()
    for path in journals:
        if not path.exists():
            missing.append(f"baseline journal missing: {path}")
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                issues.append(f"invalid JSONL: {path}:{line_number}")
                continue
            key = (event.get("scenario_id"), event.get("policy_id"))
            if key not in expected_keys:
                issues.append(f"unregistered baseline attempt: {key}")
            if signature is not None and event.get("manifest_signature") != signature:
                issues.append(f"manifest signature mismatch: {path}:{line_number}")
            if event.get("event") == "started":
                started[key] += 1
            elif event.get("event") in {"finished", "adjudicated_interrupted_unknown", "reconciled_checkpoint"}:
                status = str(event.get("status", "unknown"))
                statuses[status] += 1
                finished[key].append(status)
            else:
                issues.append(f"unknown baseline journal event: {path}:{line_number}")
    orphan_finishes = sum(max(0, len(values) - started[key]) for key, values in finished.items())
    if orphan_finishes:
        issues.append(f"{orphan_finishes} baseline finishes lack starts")
    unfinished = sum(max(0, count - len(finished[key])) for key, count in started.items())
    terminal_keys = {key for key in expected_keys if finished[key]}
    complete_pairs = sum(all("completed" in finished[(sid, policy)] for policy in policies)
                         for sid in registered)
    terminal_pairs = sum(all((sid, policy) in terminal_keys for policy in policies)
                         for sid in registered)
    if len(terminal_keys) < len(expected_keys):
        missing.append(f"{len(expected_keys) - len(terminal_keys)} registered baseline policy runs lack terminal result")
    if unfinished:
        missing.append(f"{unfinished} started baseline attempts unfinished")
    if not journals:
        missing.append("no baseline attempt journals")
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                registered_pairs=len(registered), registered_policy_runs=len(expected_keys),
                started_attempts=sum(started.values()), finished_attempts=sum(map(len, finished.values())),
                unfinished_attempts=unfinished, orphan_finishes=orphan_finishes,
                fully_completed_pairs=complete_pairs, terminal_pairs=terminal_pairs,
                statuses=dict(statuses))


def audit_branch_attempts(run_dir: Path) -> dict:
    """Count branch launches and inner reference/D0/action continuations separately."""
    branches = Path(run_dir) / "branches"
    collector = branches / "collection_attempts.jsonl"
    issues, missing = [], []
    wrappers = Counter()
    wrapper_finishes = Counter()
    by_folder_started = defaultdict(Counter)
    by_folder_finished = defaultdict(Counter)
    statuses = Counter()
    if collector.is_file():
        try:
            for row in _read_jsonl(collector):
                folder = str(Path(row.get("attempt_directory", "")).resolve())
                if row.get("event") == "started":
                    wrappers[folder] += 1
                elif row.get("event") == "finished":
                    wrapper_finishes[folder] += 1
                    statuses[str(row.get("result_status", "unknown"))] += 1
                else:
                    issues.append(f"unknown branch collection journal event: {folder}")
        except (OSError, ValueError, TypeError) as error:
            issues.append(f"branch collection journal unreadable: {error}")
    if branches.is_dir():
        for journal_path in branches.rglob("attempts.jsonl"):
            folder = str(journal_path.parent.resolve())
            try:
                for row in _read_jsonl(journal_path):
                    kind = row.get("attempt_type", "unknown")
                    if row.get("event") == "started":
                        by_folder_started[folder][kind] += 1
                    elif row.get("event") == "finished":
                        by_folder_finished[folder][kind] += 1
                        statuses[str(row.get("status", "unknown"))] += 1
                    else:
                        issues.append(f"unknown inner branch event: {journal_path}")
            except (OSError, ValueError, TypeError) as error:
                issues.append(f"branch journal unreadable {journal_path}: {error}")
    all_folders = set(wrappers) | set(by_folder_started)
    capture = sum(max(wrappers[folder], by_folder_started[folder]["snapshot_capture"])
                  for folder in all_folders)
    d0 = sum(values["D0_secure"] for values in by_folder_started.values())
    futures = sum(values["current_action_future_continuation"]
                  for values in by_folder_started.values())
    unfinished_inner = sum(max(0, count - by_folder_finished[folder][kind])
                           for folder, kinds in by_folder_started.items()
                           for kind, count in kinds.items())
    unfinished_wrapper = sum(max(0, count - wrapper_finishes[folder])
                             for folder, count in wrappers.items())
    if not all_folders:
        missing.append("no branch attempt journals")
    if unfinished_inner:
        missing.append(f"{unfinished_inner} inner branch attempts unfinished")
    if unfinished_wrapper:
        missing.append(f"{unfinished_wrapper} branch collection launches unfinished")
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                snapshots_started=capture,
                action_future_started=capture + d0 + futures,
                reference_started=capture, d0_started=d0,
                candidate_future_started=futures,
                unfinished_inner_attempts=unfinished_inner,
                unfinished_collection_attempts=unfinished_wrapper,
                statuses=dict(statuses),
                other_attempt_types=dict(Counter({kind: sum(v[kind] for v in by_folder_started.values())
                                                  for kind in {k for v in by_folder_started.values() for k in v}
                                                  if kind not in {"snapshot_capture", "D0_secure",
                                                                  "current_action_future_continuation"}})))


def audit_budget(counts: dict) -> dict:
    issues, missing = [], []
    observed = {}
    for key, cap in BUDGET_CAPS.items():
        value = counts.get(key)
        if value is None:
            missing.append(f"{key} attempt count unavailable")
            continue
        if not isinstance(value, int) or value < 0:
            issues.append(f"{key} count invalid: {value}")
            continue
        observed[key] = value
        if value > cap:
            issues.append(f"{key} {value} exceeds {cap}")
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                observed=observed, caps=BUDGET_CAPS)


def fit_instance_evidence(fit_root: Path) -> dict:
    """Charge actual estimator fits, including every started fit invocation.

    A completed masked tree trains up to 19 independent target estimators. If
    its report is absent or invalid, 19 is the conservative reservation; a
    started NN without a report reserves one model fit. No start is free.
    """
    root = Path(fit_root)
    starts = sorted(root.rglob("fit_started.json")) if root.is_dir() else []
    rows = []
    for path in starts:
        start = _load(path)
        kind = ("nn" if start.get("requested_device", "").startswith("cuda")
                else "tree" if start.get("model") == "per-target ExtraTreesRegressor"
                or start.get("device") == "cpu" else "unknown")
        report_path = path.parent / "FIT_REPORT.json"
        actual = _load(report_path).get("actual_fits") if report_path.is_file() else None
        valid_actual = isinstance(actual, int) and not isinstance(actual, bool) and actual >= 1
        charge = actual if valid_actual else 19 if kind == "tree" else 1
        rows.append({"fit_directory": str(path.parent), "kind": kind,
                     "actual_fits_reported": actual, "charged_instances": charge,
                     "basis": "reported_actual_fits" if valid_actual else "conservative_started_reservation"})
    return {"invocation_starts": len(starts),
            "reported_actual_instances": sum(row["actual_fits_reported"] for row in rows
                                             if isinstance(row["actual_fits_reported"], int)
                                             and not isinstance(row["actual_fits_reported"], bool)
                                             and row["actual_fits_reported"] > 0),
            "conservative_unreported_reservation": sum(row["charged_instances"] for row in rows
                                                       if row["basis"] != "reported_actual_fits"),
            "charged_instances": sum(row["charged_instances"] for row in rows),
            "fits": rows}


def audit_cuda_fits(fit_root: Path, *, expected_manifest_signature: str | None,
                    expected_manifest_sha256: str | None = None) -> dict:
    """Verify actual new neural fit evidence, not CUDA availability or dry runs."""
    fit_root = Path(fit_root)
    starts = sorted(fit_root.rglob("fit_started.json")) if fit_root.exists() else []
    reports = sorted(fit_root.rglob("FIT_REPORT.json")) if fit_root.exists() else []
    possible = {path.parent for path in starts + reports}
    directories = set()
    for folder in possible:
        try:
            start_record = _load(folder / "fit_started.json") if (folder / "fit_started.json").is_file() else {}
            metadata = _load(folder / "metadata.json") if (folder / "metadata.json").is_file() else {}
        except (OSError, ValueError):
            start_record, metadata = {}, {}
        if (start_record.get("requested_device") == "cuda:0" or
                metadata.get("schema") == "reliability_first.gpu_paired_action_value.v1"):
            directories.add(folder)
    issues, missing, fit_rows = [], [], []
    for folder in sorted(directories):
        start_path, report_path = folder / "fit_started.json", folder / "FIT_REPORT.json"
        failure_path, metadata_path = folder / "fit_error.json", folder / "metadata.json"
        item = dict(path=str(folder), started=start_path.exists(), fit_error=failure_path.exists(),
                    report_present=report_path.exists())
        if not start_path.exists():
            issues.append(f"fit report lacks start receipt: {folder}")
        if not report_path.exists():
            missing.append(f"fit lacks successful report: {folder}")
            fit_rows.append(item)
            continue
        if not metadata_path.exists():
            issues.append(f"fit metadata missing: {folder}")
            fit_rows.append(item)
            continue
        try:
            report, metadata = _load(report_path), _load(metadata_path)
            binding = report.get("source_binding", {})
            if binding != metadata.get("source_binding"):
                issues.append(f"fit source binding differs from metadata: {folder}")
            if expected_manifest_signature is not None:
                signature_matches = binding.get("manifest_signature") == expected_manifest_signature
                bytes_match = (expected_manifest_sha256 is not None and
                               binding.get("manifest_sha256") == expected_manifest_sha256)
                if not (signature_matches or bytes_match):
                    issues.append(f"fit manifest signature/byte binding mismatch: {folder}")
            if report.get("metadata_sha256") != _sha(metadata_path):
                issues.append(f"fit metadata hash mismatch: {folder}")
            if metadata.get("schema") != "reliability_first.gpu_paired_action_value.v1":
                issues.append(f"unexpected new NN schema: {folder}")
            if report.get("actual_fits") != 1 or report.get("train_rows", 0) < 1 or report.get("validation_rows", 0) < 1:
                issues.append(f"fit has no real train/validation rows: {folder}")
            if report.get("optimizer_updates", 0) < 1 or not report.get("epoch_losses"):
                issues.append(f"fit has no optimizer update/epoch evidence: {folder}")
            if report.get("initial_weight_sha256") == report.get("trained_weight_sha256"):
                issues.append(f"fit model weights did not change: {folder}")
            for phase in ("train", "validation"):
                name = f"actual_{phase}_device"
                if report.get(name) != "cuda:0" or metadata.get(name) != "cuda:0":
                    issues.append(f"fit {phase} was not on CUDA: {folder}")
            if report.get("peak_cuda_allocated_bytes", 0) < 1:
                issues.append(f"fit lacks actual CUDA allocation: {folder}")
            if report.get("reload_max_abs_difference") != 0.0:
                issues.append(f"fit reloaded prediction mismatch: {folder}")
            artifacts = metadata.get("artifacts", {})
            if "model.pt" not in artifacts:
                issues.append(f"model artifact not registered: {folder}")
            for name, expected in artifacts.items():
                path = _safe_under(folder, name)
                if path is None or not path.is_file() or _sha(path) != expected:
                    issues.append(f"fit artifact hash mismatch: {folder}/{name}")
            item.update(actual_train_device=report.get("actual_train_device"),
                        optimizer_updates=report.get("optimizer_updates"),
                        train_rows=report.get("train_rows"), validation_rows=report.get("validation_rows"),
                        source_binding=binding)
        except (OSError, ValueError, TypeError, KeyError) as error:
            issues.append(f"unreadable NN fit evidence {folder}: {error}")
        fit_rows.append(item)
    if not directories:
        missing.append("no new NN fit attempt/evidence")
    successful = sum(row.get("report_present", False) and
                     not any(str(row["path"]) in issue for issue in issues) for row in fit_rows)
    if not successful:
        missing.append("no verified new CUDA NN fit")
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                fit_started=sum((folder / "fit_started.json").is_file() for folder in directories),
                fit_reports=sum((folder / "FIT_REPORT.json").is_file() for folder in directories),
                successful_new_cuda_fits=successful,
                failed_fit_attempts=sum(row["fit_error"] for row in fit_rows), fits=fit_rows)


def audit_protected_hashes(workspace_root: Path, reference: dict) -> dict:
    """Recheck immutable originals once against the existing v9 hash register."""
    workspace_root = Path(workspace_root)
    checks = reference.get("sections", {}).get("input_and_source_integrity", {}).get("original_file_checks", [])
    issues, missing, mismatched, absent = [], [], [], []
    if not checks:
        missing.append("protected-original hash register absent")
    for entry in checks:
        relative = entry.get("path", "")
        path = _safe_under(workspace_root, relative)
        if path is None:
            issues.append(f"protected path leaves workspace: {relative}")
        elif not path.is_file():
            absent.append(relative)
            issues.append(f"protected original missing: {relative}")
        elif _sha(path) != entry.get("expected_sha256"):
            mismatched.append(relative)
            issues.append(f"protected original hash mismatch: {relative}")
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                checked_files=len(checks), mismatched_files=mismatched, missing_files=absent)


def audit_development_pairing(rows: list[dict], *, required_policy_ids: tuple[str, ...]) -> dict:
    """Require same registered backgrounds and retain every terminal status."""
    issues, missing = [], []
    by_scenario = defaultdict(lambda: defaultdict(list))
    statuses = Counter()
    for row in rows:
        sid, policy = row.get("scenario_id"), row.get("policy_id")
        if not sid or not policy:
            issues.append("development row lacks scenario_id/policy_id")
            continue
        by_scenario[sid][policy].append(row)
        statuses[str(row.get("status", "unknown"))] += 1
    if not rows:
        missing.append("no new matched development closed-loop rows")
    for sid, policies in by_scenario.items():
        for policy in required_policy_ids:
            if policy not in policies:
                missing.append(f"matched policy {policy} missing for {sid}")
        active = [policies[policy][-1] for policy in required_policy_ids if policy in policies]
        if any(not row.get("terminal", False) for row in active):
            missing.append(f"nonterminal matched outcome: {sid}")
        bindings = [row.get("binding") for row in active]
        if any(not isinstance(binding, dict) for binding in bindings):
            missing.append(f"physical pairing binding absent: {sid}")
        elif bindings:
            for key in ("initial_state_sha256", "exogenous_stream_id"):
                if len({binding.get(key) for binding in bindings}) != 1 or bindings[0].get(key) is None:
                    issues.append(f"matched {key} differs: {sid}")
            if not all(binding.get("passed") is True for binding in bindings):
                issues.append(f"physical pair binding check failed: {sid}")
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                registered_scenarios=len(by_scenario), attempted_rows=len(rows),
                matched_scenarios=sum(all(p in policies for p in required_policy_ids)
                                      for policies in by_scenario.values()), statuses=dict(statuses),
                required_policy_ids=list(required_policy_ids))


def audit_closed_loop(output_dir: Path) -> dict:
    """Read an actual registered comparison and its cumulative attempt journal."""
    output_dir = Path(output_dir)
    plan_path = output_dir / "registered_plan.json"
    journal_path = output_dir / "attempts.jsonl"
    summaries = sorted(output_dir.glob("summary_*.json")) if output_dir.is_dir() else []
    if not plan_path.is_file() or not summaries:
        return dict(status="incomplete", issues=[], missing=["registered closed-loop plan/summary absent"],
                    started_attempts=0, registered_pairs=0)
    issues = []
    plan, summary = _load(plan_path), _load(summaries[-1])
    required = tuple(row["policy_id"] for row in plan.get("policy_specs", []))
    rows = summary.get("results", [])
    pairing = audit_development_pairing(rows, required_policy_ids=required)
    expected_ids = set(plan.get("scenario_ids", []))
    actual_ids = {row.get("scenario_id") for row in rows}
    if actual_ids - expected_ids:
        issues.append("closed-loop result references unregistered scenario")
    if summary.get("plan_signature") != plan.get("signature"):
        issues.append("closed-loop summary/registered-plan signature mismatch")
    if summary.get("registered_pair_count") != len(expected_ids) * len(required):
        issues.append("closed-loop registered pair count mismatch")
    attempts = _read_jsonl(journal_path) if journal_path.is_file() else []
    starts = [row for row in attempts if row.get("event") == "started"]
    finishes = [row for row in attempts if row.get("event") == "finished"]
    if any(row.get("scenario_id") not in expected_ids or row.get("policy_id") not in required for row in attempts):
        issues.append("closed-loop journal contains unregistered attempt")
    missing = list(pairing["missing"])
    if len(starts) > len(finishes):
        missing.append(f"{len(starts) - len(finishes)} closed-loop attempts unfinished")
    if not journal_path.is_file():
        missing.append("closed-loop attempt journal absent")
    issues.extend(pairing["issues"])
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                started_attempts=len(starts), finished_attempts=len(finishes),
                registered_pairs=len(expected_ids) * len(required), pairing=pairing,
                plan_signature=plan.get("signature"), statuses=pairing["statuses"])


def audit_review_package(workspace_root: Path, zip_path: Path | None,
                         receipt_path: Path | None) -> dict:
    issues, missing = [], []
    if zip_path is None or receipt_path is None:
        return dict(status="incomplete", issues=[], missing=["portable review ZIP/verification receipt absent"])
    zip_path, receipt_path = Path(zip_path), Path(receipt_path)
    if not zip_path.is_file() or not receipt_path.is_file():
        return dict(status="incomplete", issues=[], missing=["portable review ZIP/verification receipt absent"])
    try:
        receipt = _load(receipt_path)
        if receipt.get("zip_sha256") != _sha(zip_path):
            issues.append("review ZIP hash mismatch")
        if receipt.get("zip_bytes") != zip_path.stat().st_size:
            issues.append("review ZIP byte count mismatch")
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                issues.append("review ZIP contains duplicate member names")
            if receipt.get("files") not in (None, len(names)):
                issues.append("review ZIP member count differs from receipt")
        if receipt.get("crc_passed") is not True:
            issues.append("review package verifier receipt lacks CRC pass")
        if not receipt.get("included_byte_hashes_checked"):
            missing.append("review package member byte hashes not recorded")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        issues.append(f"review package unreadable: {error}")
    return dict(status=_status(issues, missing), issues=issues, missing=missing,
                zip_path=str(zip_path), receipt_path=str(receipt_path),
                crc_basis="recorded verifier receipt bound to rehashed ZIP; audit avoids a second full decompression")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit_run(workspace_root: Path, run_dir: Path, *,
              development_rows: Path | None = None,
              closed_loop_dir: Path | None = None,
              required_policy_ids: tuple[str, ...] = ("secure_milp", "rule", "nn"),
              package_zip: Path | None = None, package_receipt: Path | None = None) -> dict:
    """Build a factual audit ledger. Absent future stages remain incomplete."""
    workspace_root, run_dir = Path(workspace_root), Path(run_dir)
    source = run_dir / "design/source_proposal.json"
    plan_v1 = run_dir / "design/snapshot_plan.json"
    plan_v2 = run_dir / "design/snapshot_plan_v2.json"
    correction = run_dir / "design/SNAPSHOT_PLAN_V2_CORRECTION.json"
    prepare_receipt = run_dir / "design/V4_SOURCE_PREPARE.json"
    manifest_path = run_dir / "new_data/global_manifest.json"
    sections = {}
    identity_issues, identity_missing = [], []
    for path in (source, plan_v1, plan_v2, correction, prepare_receipt, manifest_path):
        if not path.is_file():
            identity_missing.append(f"frozen source evidence missing: {path}")
    if manifest_path.is_file() and plan_v2.is_file():
        manifest, plan = _load(manifest_path), _load(plan_v2)
        sections["snapshot_groups"] = audit_snapshot_groups(manifest, plan)
        journals = sorted((run_dir / "new_data/shards").glob("shard_*/attempts.jsonl"))
        sections["baseline_attempts"] = audit_baseline_attempts(manifest, journals)
        sections["branch_attempts"] = audit_branch_attempts(run_dir)
        sections["cuda_nn_training"] = audit_cuda_fits(
            run_dir / "learning", expected_manifest_signature=manifest.get("signature"),
            expected_manifest_sha256=_sha(manifest_path))
        if correction.is_file():
            receipt = _load(correction)
            for key, path in (("old_plan_sha256", plan_v1), ("new_plan_sha256", plan_v2),
                              ("manifest_sha256", manifest_path), ("proposal_sha256", source)):
                if path.is_file() and receipt.get(key) != _sha(path):
                    identity_issues.append(f"snapshot plan correction {key} mismatch")
            if receipt.get("manifest_signature") != manifest.get("signature"):
                identity_issues.append("snapshot plan correction manifest signature mismatch")
        if prepare_receipt.is_file():
            receipt = _load(prepare_receipt)
            for key, path in (("proposal_sha256", source), ("manifest_sha256", manifest_path)):
                if key in receipt and path.is_file() and receipt[key] != _sha(path):
                    identity_issues.append(f"source prepare {key} mismatch")
            if receipt.get("manifest_signature") != manifest.get("signature"):
                identity_issues.append("source prepare manifest signature mismatch")
        fits = fit_instance_evidence(run_dir / "learning")
        sections["fit_instance_accounting"] = dict(status="passed", issues=[], missing=[], **fits)
        counts = {"new_specifications": len(manifest.get("scenarios", [])),
                  "baseline_started": sections["baseline_attempts"]["started_attempts"],
                  "snapshots_started": sections["branch_attempts"]["snapshots_started"],
                  "action_future_started": sections["branch_attempts"]["action_future_started"],
                  "fit_started": fits["charged_instances"]}
        if closed_loop_dir is not None:
            sections["development_pairing"] = audit_closed_loop(closed_loop_dir)
            counts["other_full_started"] = (sections["baseline_attempts"]["started_attempts"] +
                                            sections["development_pairing"]["started_attempts"])
        budget_path = run_dir / "BUDGET_LEDGER.json"
        if budget_path.is_file():
            ledger = _load(budget_path)
            registered = ledger.get("started_counts", ledger)
            for key, value in registered.items():
                if key in counts and isinstance(value, int) and value < counts[key]:
                    identity_issues.append(f"budget ledger undercounts source-backed {key}: {value} < {counts[key]}")
                elif key in counts and isinstance(value, int):
                    counts[key] = max(value, counts[key])
                else:
                    counts[key] = value
        sections["attempt_budget"] = audit_budget(counts)
    else:
        for name in ("snapshot_groups", "baseline_attempts", "branch_attempts", "cuda_nn_training",
                     "fit_instance_accounting", "attempt_budget"):
            sections[name] = dict(status="incomplete", issues=[], missing=["frozen manifest/plan unavailable"])
    sections["frozen_source_identity"] = dict(status=_status(identity_issues, identity_missing),
                                              issues=identity_issues, missing=identity_missing,
                                              plan_version="v2; v1 retained unchanged")
    original_register = workspace_root / "outputs/v43_gpu_existing_nn_v9_run01/review/GPU_NN_TRAINING_AUDIT_01.json"
    sections["protected_originals"] = (audit_protected_hashes(workspace_root, _load(original_register))
                                        if original_register.is_file() else
                                        dict(status="incomplete", issues=[], missing=["v9 protected-original hash register absent"]))
    if "development_pairing" not in sections:
        if development_rows is not None and Path(development_rows).is_file():
            rows = _read_jsonl(Path(development_rows))
        else:
            rows = []
        sections["development_pairing"] = audit_development_pairing(rows, required_policy_ids=required_policy_ids)
    sections["review_package"] = audit_review_package(workspace_root, package_zip, package_receipt)
    failed = [name for name, value in sections.items() if value["status"] == "failed"]
    incomplete = [name for name, value in sections.items() if value["status"] == "incomplete"]
    return dict(schema="reliability_first.audit.v1", status="failed" if failed else "incomplete" if incomplete else "passed",
                run_dir=str(run_dir), sections=sections, failed_sections=failed, incomplete_sections=incomplete,
                report_rule="Actual evidence only; incomplete/unknown outcomes are never imputed as success")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--development-rows", type=Path)
    parser.add_argument("--closed-loop-dir", type=Path, help="Registered closed_loop.py output directory")
    parser.add_argument("--required-policies", nargs="+", default=("secure_milp", "rule", "nn"))
    parser.add_argument("--package-zip", type=Path)
    parser.add_argument("--package-receipt", type=Path)
    parser.add_argument("--out", type=Path, help="New JSON path; existing outputs are never overwritten")
    args = parser.parse_args()
    result = audit_run(args.workspace_root, args.run_dir, development_rows=args.development_rows,
                       closed_loop_dir=args.closed_loop_dir,
                       required_policy_ids=tuple(args.required_policies), package_zip=args.package_zip,
                       package_receipt=args.package_receipt)
    if args.out:
        with args.out.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(status=result["status"], failed_sections=result["failed_sections"],
                          incomplete_sections=result["incomplete_sections"]), ensure_ascii=False))
    return 2 if result["status"] == "failed" else 1 if result["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())
