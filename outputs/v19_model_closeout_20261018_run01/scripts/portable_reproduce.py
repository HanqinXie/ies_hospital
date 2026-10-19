"""Fresh staged long000/E scientific replay; never substitutes archived results.

Builder workflow: prepare -> actual M19 runner CLI -> verify.  The optional
reproduce command executes exactly those three commands and records each exit.
Only standard-library modules load before final configuration/stage checks.
No physical episode starts in prepare, verify, or static verification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time
import traceback

RUN_REL = "outputs/v19_model_closeout_20261018_run01"
TARGET_REL = RUN_REL + "/experiments/standalone_replay01"
REVIEW_REL = RUN_REL + "/review/standalone_replay01"
CONFIG_REL = RUN_REL + "/design/PORTABLE_REPRODUCTION_CONFIG.json"
FREEZE_REL = RUN_REL + "/MODEL_FREEZE_CLOSEOUT01.json"
PROPOSAL_REL = RUN_REL + "/design/LONG_FINAL_R1_PROPOSAL.json"
MODEL_CONFIG_REL = RUN_REL + "/M19_MODEL_CONFIG.json"
SCENE_ID = "v17_L1_c3293aa6e9401f244ec3406c"
POLICY_ID = "endurance_restore_p0_ready_v14r6"
MODEL_ID = "M19_model_closeout"
SCHEMA = "hospital_ies.v19.portable_scientific_reproduction.v1"
SCIENCE_NAME = "SCIENCE_ROOT"
RUNTIME_METADATA = {
    'outputs/v13_executed_service_20261017_run01/design/DEVELOPMENT_POLICY_SPECS.json':
        'c89c50716465e8b0b3ee34e134e2a9ae7dcbf99f359472e69e3fe40885a15e9d',
    'outputs/v11_autonomous_dispatch_20261016_run01/new_data/standby_manifest.json':
        'ca01d42fd1e2cefdf5e9ead956d4237910eedcf382177695682a04e53088e1a3',
    'outputs/v15_engineering_operation_20261017_run01/source_data/p3_development_baseline/P3_SOURCE_RECEIPT.json':
        'c21a8f9302f38327fa3ec50122147e01e346c20253b859d1dc08602258cb2644',
    'outputs/v15_engineering_operation_20261017_run01/source_data/p3_frozen_baseline/P3_SOURCE_RECEIPT.json':
        '8ae27f4313e8a25e6f8e02b90b9be19c4509ea5994cf1c999baa02d8983709e9',
}
TOLERANCES = {
    "ENS_kWh": {"absolute": 1e-5, "relative": 1e-8},
    "material_minutes": {"absolute": 1e-6, "relative": 0.0},
    "first_clean_minute": {"absolute": 1e-6, "relative": 0.0},
    "actual_energy": {"absolute": 1e-5, "relative": 1e-8},
    "fee_USD": {"absolute": 1e-5, "relative": 1e-8},
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local(root, relative):
    """Every file opened by this helper must resolve inside the staged root."""
    require(isinstance(relative, str) and "\\" not in relative and ":" not in relative,
        "Use a stage-relative POSIX path")
    name = PurePosixPath(relative)
    require(not name.is_absolute() and name.parts and
        all(part not in (".", "..") for part in name.parts), "Unsafe relative path")
    path = root.joinpath(*name.parts).resolve()
    require(path.is_relative_to(root), "Path escapes staged root")
    return path


def pinned(root, item):
    require(isinstance(item, dict) and set(item) == {"path", "sha256"},
        "Each final artifact needs an exact path and SHA256")
    expected = item["sha256"]
    require(isinstance(expected, str) and len(expected) == 64 and
        all(c in "0123456789abcdef" for c in expected), "Unconfigured artifact SHA256")
    path = local(root, item["path"])
    require(path.is_file() and sha(path) == expected,
        "Missing/changed staged artifact: " + item["path"])
    return path


def verified_signature(value, label):
    require(value.get("signature") == digest({k: v for k, v in value.items() if k != "signature"}),
        label + " signature differs")


def staged_root():
    actual_root = Path(__file__).resolve().parents[3]
    require(Path.cwd().resolve() == actual_root, "Run from this helper's staged project root")
    require(actual_root.drive.lower() in ("d:", "e:"), "Scientific stage must use D/E")
    root = actual_root.parent if actual_root.name == SCIENCE_NAME else actual_root
    if actual_root != root:
        marker = read(local(actual_root, "delivery_metadata/SCIENCE_ROOT_CLONE.json"))
        require(Path(marker["evidence_root"]).resolve() == root and
            Path(marker["science_root"]).resolve() == actual_root,
            "Independent reproduction root marker differs")
    require(local(root, "delivery_metadata/SOURCE_BINDING.json").is_file(),
        "No staged delivery source-binding metadata; refuse original-workspace execution")
    return root


def metrics(record):
    """Fixed preregistered registered-full metrics; no ranked case selection."""
    analysis = record["analysis"]
    require(analysis.get("execution_model_id") == MODEL_ID and
        analysis.get("v19", {}).get("execution_model_id") == MODEL_ID,
        "Analysis is not produced for the actual M19 model")
    window = analysis["v18"]["windows"]["registered_full"]["all_registered"]
    result = {key: {} for key in TOLERANCES}
    for priority in ("P0", "P1", "P2", "P3"):
        for carrier in ("e", "h", "c"):
            service = window["carrier_service"][priority][carrier]
            stem = priority + "." + carrier + "."
            for key in ("ENS_kWh", "disconnected_ENS_kWh", "connected_unserved_ENS_kWh"):
                result["ENS_kWh"][stem + key] = service[key]
            for key in ("material_minutes", "longest_material_minutes"):
                result["material_minutes"][stem + key] = service[key]
            result["first_clean_minute"][stem + "first_clean_minute"] = service["first_clean_minute"]
    economics = window["economics"]
    for key in ("GT_gas_MMBtu", "boiler_gas_MMBtu", "natural_gas_total_MMBtu",
            "grid_purchase_kWh", "DG_electric_work_kWh", "DG_shaft_work_kWh",
            "DG_loss_work_kWh", "DG_rotor_delta_kWh", "DG_fuel_budget_debit_kWh_equivalent"):
        result["actual_energy"][key] = economics[key]
    for key, value in analysis["windows"]["registered_full"]["all_registered"]["source_electric_energy_kWh"].items():
        result["actual_energy"]["source_electric_kWh." + key] = value
    for key in ("actual_energy_fee_USD", "grid_fee_USD", "GT_gas_fee_USD", "boiler_gas_fee_USD",
            "DG_original_electric_fee_USD", "DG_shaft_equivalent_fee_USD"):
        result["fee_USD"][key] = economics[key]
    return result


def compare_metrics(actual, expected):
    require(set(actual) == set(expected) == set(TOLERANCES), "Metric group coverage differs")
    rows = []
    for group, tolerance in TOLERANCES.items():
        require(set(actual[group]) == set(expected[group]), "Metric fields differ: " + group)
        for key, target in expected[group].items():
            value = actual[group][key]
            if value is None or target is None:
                passed = value is target
                error = budget = None
            else:
                require(isinstance(value, (int, float)) and not isinstance(value, bool) and
                    isinstance(target, (int, float)) and not isinstance(target, bool) and
                    math.isfinite(value) and math.isfinite(target), "Nonfinite/invalid metric: " + key)
                error = abs(value - target)
                budget = tolerance["absolute"] + tolerance["relative"] * abs(target)
                passed = error <= budget
            rows.append(dict(group=group, metric=key, actual=value, reference=target,
                absolute_difference=error, comparison_budget=budget, passed=passed))
    return rows


def complete_record(record):
    require(record.get("scenario_id") == SCENE_ID and record.get("policy_id") == POLICY_ID and
        record.get("model_id") == MODEL_ID, "Unexpected scientific scenario/policy/model")
    require(record.get("status") == "completed" and record.get("error") is None and
        record.get("analysis_error") is None, "Scientific attempt failed or analysis unknown")
    analysis = record["analysis"]
    require(analysis.get("execution_model_id") == MODEL_ID and
        analysis.get("v19", {}).get("execution_model_id") == MODEL_ID,
        "Analysis is not produced for the actual M19 model")
    for section in (analysis["windows"], analysis["v18"]["windows"]):
        full = section["registered_full"]
        require(full["complete"] is True and full["window_minutes"] == [0.0, 1065.0],
            "Entire 1065-minute registered window must be complete")
    require(analysis["observed_until_minute"] == 1065.0 and
        analysis["requested_end_minute"] == 1065.0 and analysis.get("unknown_tail_minutes") is None,
        "Unknown or truncated physical tail")
    audit = analysis["v19"]["episode_audit"]
    require(audit["status"] == "passed" and audit["issues"] == [] and
        audit["complete_registered_interval"] is True, "M19 episode audit did not pass")


def binding_match(plan, manifest, frozen, policy, *, label):
    verified_signature(manifest, label + " manifest")
    require(plan["model_id"] == MODEL_ID and manifest["execution_model_id"] == MODEL_ID,
        label + " model differs")
    require(plan["source_sha256"] == manifest["source_sha256"] == frozen["source_sha256"],
        label + " source differs from final freeze")
    require(plan["model_config"] == manifest["model_config"] == frozen["model_config"],
        label + " model configuration differs")
    require(manifest["model_config_signature"] == digest(frozen["model_config"]),
        label + " model configuration signature differs")
    require(plan["manifest_signature"] == manifest["signature"], label + " manifest binding differs")
    require(policy in plan["policy_specs"] and manifest.get("freeze_signature") == frozen["signature"],
        label + " policy/freeze differs")
    require(manifest.get("source_freeze_artifact", {}).get("sha256") == frozen["_artifact_sha256"],
        label + " freeze artifact differs")


def preflight(config_name, *, fresh):
    root = staged_root()
    config_path = local(root, config_name)
    config = read(config_path)
    require(config.get("schema") == SCHEMA + ".config" and config.get("status") == "final_configured",
        "Final configuration/freeze/reference not configured; no scientific result generated")
    require(config.get("scenario_id") == SCENE_ID and config.get("policy_id") == POLICY_ID and
        config.get("duration_minutes") == 1065.0 and config.get("target_directory") == TARGET_REL,
        "Only preregistered long000/E full1065 replay is supported")
    require(config.get("tolerances") == TOLERANCES, "Predeclared comparison tolerances changed")
    require(config.get("helper_sha256") == sha(__file__), "Helper bytes differ from final configuration")
    freeze_path = pinned(root, config["freeze"])
    proposal_path = pinned(root, config["proposal"])
    require(config["freeze"]["path"] == FREEZE_REL and config["proposal"]["path"] == PROPOSAL_REL,
        "Use the explicitly declared final freeze and long proposal")
    frozen, proposal = read(freeze_path), read(proposal_path)
    verified_signature(frozen, "Final freeze")
    verified_signature(proposal, "Final long proposal")
    require(frozen.get("kind") == "v19_final_shared_model_freeze", "Development freeze cannot certify replay")
    references = {key: pinned(root, config["reference"][key]) for key in ("plan", "manifest", "batch", "result")}
    ref_plan, ref_manifest, ref_batch, ref_record = (read(references[k]) for k in ("plan", "manifest", "batch", "result"))
    require(str((root / RUN_REL).resolve()).casefold() != str(ref_plan["canonical_run"]).casefold(),
        "Independent replay cannot execute in the original canonical run")
    require(references["plan"].parent == references["manifest"].parent == references["batch"].parent,
        "Reference registration/terminal artifacts must share one batch")
    require(references["result"] == references["plan"].parent / "pairs" / (SCENE_ID + "__" + POLICY_ID) / "RESULT.json",
        "Reference must be declared final long000/E RESULT")
    require(ref_plan["manifest_sha256"] == sha(references["manifest"]) and
        ref_batch["plan_sha256"] == sha(references["plan"]) and
        ref_batch["manifest_sha256"] == sha(references["manifest"]) and
        ref_batch["source_unchanged"] is True, "Reference plan/manifest/source binding differs")
    execution_root = Path(__file__).resolve().parents[3]
    sys.path[:0] = [str(execution_root), str(execution_root / "src")]
    from tools.model_closeout import runner as r
    from tools.model_closeout.freeze import validate_test_gate, resolved_evaluation_contract
    from tools.model_closeout.gate import test_binding
    binding = r.source_binding()
    require(r.ROOT.resolve() == execution_root and r.RUN.resolve() == execution_root / RUN_REL,
        "Production runner root escaped its independent copy")
    for relative, expected_sha256 in RUNTIME_METADATA.items():
        for metadata_root in set((root, execution_root)):
            path = local(metadata_root, relative)
            require(path.is_file() and sha(path) == expected_sha256,
                "Lazy historical runtime metadata absent or changed: " + relative)
    require(binding == frozen["source_sha256"] == read(local(root,
        "delivery_metadata/SOURCE_BINDING.json"))["binding"], "Staged final source closure differs")
    policy = r.policy_specs()["E"]
    resolved = r.model_contract(MODEL_ID, read(local(execution_root, MODEL_CONFIG_REL)))[1]
    require(resolved == frozen["model_config"], "Actual staged model configuration differs from final freeze")
    r.validate_final_freeze(frozen, binding, MODEL_ID, resolved, [policy])
    validate_test_gate(frozen["source_bound_test_gate"], binding, test_binding())
    require(frozen["affected_regression_command"]["exit_code"] == 0 and
        resolved_evaluation_contract(frozen["evaluation_contract"]) == frozen["evaluation_contract"],
        "Frozen actual gate or evaluation parameters differ")
    frozen["_artifact_sha256"] = sha(freeze_path)
    binding_match(ref_plan, ref_manifest, frozen, policy, label="Reference")
    complete_record(ref_record)
    require(ref_record["manifest_signature"] == ref_manifest["signature"] and
        ref_record["model_config_signature"] == digest(resolved), "Reference RESULT producer binding differs")
    matches = [s for s in proposal["scenarios"] if s["id"] == SCENE_ID]
    previous = [s for s in ref_manifest["scenarios"] if s["id"] == SCENE_ID]
    require(len(matches) == len(previous) == 1 and matches[0] == previous[0],
        "Final proposal scene differs from archived actual long000 scene")
    scene = matches[0]
    require(scene["duration_steps"] * scene.get("step_minutes", 15.0) == 1065.0 and
        scene.get("requires_policy_freeze") is True and proposal["freeze_signature"] == frozen["signature"],
        "Long000 is not a frozen full1065 specification")
    inventory = ref_manifest["files"]
    require(len(inventory) == config["expected_inventory_files"] == 66 and
        digest(inventory) == config["expected_input_inventory_sha256"], "Original 66-file inventory differs")
    require(len({x["path"] for x in inventory}) == len(inventory), "Duplicate inventory path")
    for item in inventory:
        for inventory_root in set((root, execution_root)):
            path = local(inventory_root, item["path"])
            require(path.is_file() and path.stat().st_size == item["bytes"] and sha(path) == item["sha256"],
                "Staged source/input inventory differs: " + item["path"])
    for name, module in list(sys.modules.items()):
        if name == "tools" or name.startswith(("tools.", "hospital_ies")):
            if getattr(module, "__file__", None):
                require(Path(module.__file__).resolve().is_relative_to(execution_root), "Runtime project import escaped copy: " + name)
    science_root = root / SCIENCE_NAME
    target = local(science_root, TARGET_REL)
    require(target.exists() is not fresh, "Fresh replay directory already exists" if fresh else "Fresh replay was not registered")
    return dict(root=root, execution_root=execution_root, science_root=science_root,
        config=config, config_path=config_path, runner=r, target=target,
        review=local(root, REVIEW_REL), freeze_path=freeze_path, frozen=frozen, proposal=proposal,
        policy=policy, reference_paths=references, reference_plan=ref_plan,
        reference_manifest=ref_manifest, reference_batch=ref_batch, reference_record=ref_record,
        inventory=inventory, scene=scene)


def command_receipt(mode, context):
    return dict(schema=SCHEMA + ".command", mode=mode, argv=[sys.executable, *sys.argv],
        cwd=str(context["root"]), started_utc=now(), config_sha256=sha(context["config_path"]),
        helper_sha256=sha(__file__), actual_physical_attempts_started=None if mode == "verify" else 0)


def register_runtime(config_name):
    c = preflight(config_name, fresh=True)
    require(c["execution_root"] == c["science_root"], "Registration must run inside SCIENCE_ROOT")
    require(not (c["execution_root"] / RUN_REL / "experiments").exists(),
        "Independent reproduction starts with no historical/previous experiment ledger")
    r = c["runner"]
    before = r.attempt_accounting()
    require(before["registered_pairs"] == before["actual_started_attempts"] == 0,
        "Independent reproduction budget is not fresh")
    plan = r.register(c["proposal"], c["target"], [c["policy"]], model_id=MODEL_ID,
        model_config=c["frozen"]["model_config"], scenario_ids=[SCENE_ID],
        phase="independent_portable_scientific_reproduction", freeze_path=local(c["execution_root"], FREEZE_REL))
    manifest = read(c["target"] / "manifest.json")
    binding_match(plan, manifest, c["frozen"], c["policy"], label="Independent reproduction")
    require(manifest["files"] == c["inventory"] and manifest["scenarios"] == [c["scene"]],
        "New registration changed actual source/input/exogenous specification")
    require(plan["registered_pairs"] == 1 and plan["policy_specs"] == [c["policy"]],
        "New registration is not exactly one E attempt")
    require(not (c["target"] / "BATCH_RESULT.json").exists() and
        not (c["target"] / "ATTEMPTS.jsonl").exists(), "Old physical result substituted during preparation")
    write_new(c["review"] / "REGISTRATION.json", dict(schema=SCHEMA + ".registration",
        canonical_root=str(c["execution_root"]), prior_independent_attempt_accounting=before,
        registered_pairs=1, actual_attempts=0, runtime_imports_from_independent_copy=True,
        plan_sha256=sha(c["target"] / "plan.json"), manifest_sha256=sha(c["target"] / "manifest.json"),
        original_stage_experiments_unmodified=True, original_campaign_budget_not_reset=True,
        fresh_budget_scope="independent scientific reproduction only", physical_episodes_started=0))
    print(json.dumps(dict(registered_pairs=1, actual_attempts=0, independent_science_root=str(c["execution_root"]))))


def child_environment(root):
    temporary = root / "delivery_verification_temp"
    temporary.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONPATH=str(root / "src") + os.pathsep + str(root),
        TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return env


def clone_runtime(c, config_name):
    """Necessary ~100 MB independent copy; no old experiment/raw tree is copied."""
    root, destination = c["root"], c["science_root"]
    require(not destination.exists(), "SCIENCE_ROOT must never have existed")
    paths = set(c["frozen"]["source_sha256"])
    paths.update(RUNTIME_METADATA)
    paths.update(item["path"] for item in c["inventory"])
    paths.update(p.relative_to(root).as_posix() for p in (root / "tests").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts)
    gate_name = c["frozen"]["affected_regression_command"]["name"]
    require(gate_name.replace("_", "").isalnum(), "Unsafe frozen gate name")
    gate_relative = RUN_REL + "/test_gates/" + gate_name + ".json"
    gate_command = RUN_REL + "/logs/" + gate_name + ".command.json"
    require(read(local(root, gate_relative)) == c["frozen"]["source_bound_test_gate"] and
        read(local(root, gate_command)) == c["frozen"]["affected_regression_command"], "Actual frozen gate artifacts differ")
    paths.update((FREEZE_REL, PROPOSAL_REL, MODEL_CONFIG_REL, config_name,
        RUN_REL + "/scripts/portable_reproduce.py", gate_relative, gate_command,
        "delivery_metadata/SOURCE_BINDING.json"))
    for relative, item in c["proposal"].get("source_metadata_bindings", {}).items():
        require(sha(local(root, relative)) == item["sha256"], "Proposal metadata source changed")
        paths.add(relative)
    inventory = []
    for relative in sorted(paths):
        path = local(root, relative)
        require(path.is_file() and not any(part in ("experiments", "source_snapshots") for part in PurePosixPath(relative).parts)
            and not path.name.endswith((".json.gz", ".zip")), "Scientific clone cannot include archived experiment/raw trees")
        inventory.append(dict(path=relative, bytes=path.stat().st_size, sha256=sha(path)))
    total = sum(item["bytes"] for item in inventory)
    free = shutil.disk_usage(root).free
    reserve = 512 * 1024 * 1024
    require(free >= total + reserve, "Insufficient D/E space for independent source/input copy plus raw allowance")
    write_new(c["review"] / "CLONE_ESTIMATE.json", dict(clone_files=len(inventory), clone_bytes=total,
        destination=str(destination), free_bytes=free, additional_raw_allowance_bytes=reserve,
        allowance_is_not_a_hard_runtime_cap=True, original_experiment_and_raw_files_copied=0))
    destination.mkdir()
    for item in inventory:
        source = local(root, item["path"])
        target = local(destination, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        require(sha(source) == item["sha256"], "Source changed while cloning")
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        require(sha(target) == item["sha256"] and target.stat().st_size == item["bytes"], "Clone bytes differ")
    marker = dict(schema=SCHEMA + ".independent_copy", evidence_root=str(root), science_root=str(destination),
        inventory=inventory, inventory_sha256=digest(inventory), copied_files=len(inventory), copied_bytes=total,
        original_experiment_and_raw_files_copied=0, original_stage_unmoved_unmodified=True,
        independent_budget_scope="new scientific reproduction; original campaign ledger remains in evidence root")
    marker_path = local(destination, "delivery_metadata/SCIENCE_ROOT_CLONE.json")
    write_new(marker_path, marker)
    write_new(c["review"] / "CLONE_RECEIPT.json", dict(marker_path=str(marker_path),
        marker_sha256=sha(marker_path), inventory_sha256=digest(inventory), clone_bytes=total,
        original_stage_experiments_unmodified=True))
    return marker


def prepare(config_name):
    c = preflight(config_name, fresh=True)
    require(c["execution_root"] == c["root"], "Launch preparation from the original extracted evidence stage")
    require(not c["science_root"].exists(), "SCIENCE_ROOT must be fresh")
    c["review"].mkdir(parents=True, exist_ok=False)
    command = command_receipt("prepare", c)
    write_new(c["review"] / "PREPARE_STARTED.json", command)
    try:
        marker = clone_runtime(c, config_name)
        argv = ["python", "-B", RUN_REL + "/scripts/portable_reproduce.py", "register-runtime", "--config", config_name]
        with (c["review"] / "registration.stdout.log").open("xb") as stdout, (c["review"] / "registration.stderr.log").open("xb") as stderr:
            done = subprocess.run([sys.executable, *argv[1:]], cwd=c["science_root"],
                env=child_environment(c["science_root"]), stdout=stdout, stderr=stderr, check=False)
        write_new(c["review"] / "REGISTRATION_COMMAND.json", dict(argv=argv, cwd=str(c["science_root"]),
            exit_code=done.returncode, stdout_sha256=sha(c["review"] / "registration.stdout.log"),
            stderr_sha256=sha(c["review"] / "registration.stderr.log")))
        require(done.returncode == 0, "Independent registration child failed; retain full partial clone/evidence")
        command.update(finished_utc=now(), exit_code=0, registered_pairs=1,
            plan_sha256=sha(c["target"] / "plan.json"), manifest_sha256=sha(c["target"] / "manifest.json"),
            source_input_inventory_sha256=digest(c["inventory"]), independent_clone_bytes=marker["copied_bytes"],
            reference_artifact_sha256={k: sha(v) for k, v in c["reference_paths"].items()},
            final_freeze_sha256=sha(c["freeze_path"]), no_physics_executed=True)
        write_new(c["review"] / "PREPARATION.json", command)
        print(json.dumps(dict(prepared=True, registered_pairs=1, actual_attempts=0, directory=TARGET_REL)))
    except Exception as exc:
        command.update(finished_utc=now(), exit_code=2, error_type=type(exc).__name__,
            message=str(exc), traceback=traceback.format_exc(), partial_registration_retained=True)
        write_new(c["review"] / "PREPARATION_FAILURE.json", command)
        raise


def verify(config_name):
    c = preflight(config_name, fresh=False)
    require(c["review"].is_dir(), "Preparation evidence is missing")
    command = command_receipt("verify", c)
    require(not (c["review"] / "COMPARISON_PROOF.json").exists() and
        not (c["review"] / "COMPARISON_FAILURE.json").exists(), "Comparison attempt is immutable")
    try:
        directory = c["target"]
        preparation = read(c["review"] / "PREPARATION.json")
        require(preparation["config_sha256"] == sha(c["config_path"]) and
            preparation["plan_sha256"] == sha(directory / "plan.json") and
            preparation["manifest_sha256"] == sha(directory / "manifest.json"), "Preparation binding changed")
        journal_path = directory / "ATTEMPTS.jsonl"
        events = ([json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line]
            if journal_path.is_file() else [])
        starts = [x for x in events if x.get("event") == "started"]
        finishes = [x for x in events if x.get("event") == "finished"]
        command.update(actual_physical_attempts_started=len(starts), actual_terminal_attempts=len(finishes),
            actual_started_without_terminal=len(starts) - len(finishes),
            attempts_journal_sha256=sha(journal_path) if journal_path.is_file() else None)
        plan, manifest, batch = (read(directory / name) for name in ("plan.json", "manifest.json", "BATCH_RESULT.json"))
        binding_match(plan, manifest, c["frozen"], c["policy"], label="New staged")
        require(plan["registered_pairs"] == batch["registered_pairs"] == batch["actual_attempts"] ==
            batch["complete_full"] == 1 and batch["unknown_full"] == 0 and
            batch["analysis_failure_or_unavailable_count"] == 0 and batch["source_unchanged"] is True,
            "Actual standalone attempt is failed/unknown/incomplete")
        accounting = batch["run_attempt_accounting"]
        require(accounting["registered_pairs"] == accounting["actual_started_attempts"] ==
            accounting["terminal_attempts"] == 1 and accounting["started_without_terminal_record"] == 0,
            "Independent reproduction budget contains other/unknown actual attempts")
        require(batch["plan_sha256"] == sha(directory / "plan.json") and
            batch["manifest_sha256"] == sha(directory / "manifest.json") and
            Path(plan["canonical_run"]).resolve() == c["science_root"] / RUN_REL,
            "New terminal artifact is not bound to this staged registration")
        require(manifest["files"] == c["inventory"] and manifest["scenarios"] == [c["scene"]],
            "Actual source/input/scenario registration changed")
        marker = read(local(c["science_root"], "delivery_metadata/SCIENCE_ROOT_CLONE.json"))
        require(marker["inventory_sha256"] == digest(marker["inventory"]), "Independent clone inventory signature differs")
        for item in marker["inventory"]:
            path = local(c["science_root"], item["path"])
            require(path.is_file() and path.stat().st_size == item["bytes"] and sha(path) == item["sha256"],
                "Independent runtime source/input changed: " + item["path"])
        require(len(events) == 2 and len(starts) == len(finishes) == 1 and events[0] is starts[0] and
            all(x["scenario_id"] == SCENE_ID and x["policy_id"] == POLICY_ID for x in events),
            "Fresh actual started/finished attempt journal does not prove exactly one run")
        require(starts[0]["model_id"] == MODEL_ID and
            datetime.fromisoformat(starts[0]["utc"]) >= datetime.fromisoformat(preparation["finished_utc"]) and
            datetime.fromisoformat(finishes[0]["utc"]) >= datetime.fromisoformat(starts[0]["utc"]),
            "Actual attempt did not start after this independent preparation")
        result_path = directory / "pairs" / (SCENE_ID + "__" + POLICY_ID) / "RESULT.json"
        record = read(result_path)
        complete_record(record)
        require(finishes[0]["result_sha256"] == sha(result_path) and
            finishes[0]["raw_sha256"] == record["raw_sha256"] and
            finishes[0]["status"] == "completed" and finishes[0]["analysis_status"] == "saved" and
            record["manifest_signature"] == manifest["signature"] and
            record["model_config_signature"] == manifest["model_config_signature"], "Actual terminal pair binding differs")
        raw_path = directory / "pairs" / (SCENE_ID + "__" + POLICY_ID) / "trajectory.json.gz"
        require(raw_path.is_file() and sha(raw_path) == record["raw_sha256"], "Fresh producer raw SHA differs")
        new_matching = batch["matching_evidence"][SCENE_ID]
        old_matching = c["reference_batch"]["matching_evidence"][SCENE_ID]
        index = [x["policy_id"] for x in c["reference_plan"]["policy_specs"]].index(POLICY_ID)
        matched = {}
        for key in ("initial_state_sha256", "applied_exogenous_definition_sha256"):
            expected = old_matching[key][index]
            actual = new_matching[key]
            require(expected is not None and actual == [expected], "Actual reset/exogenous/injection state differs: " + key)
            matched[key] = expected
        injection_diagnostic = dict(reference=old_matching["injection_state_sha256"][index],
            actual=new_matching["injection_state_sha256"],
            exact_equal=new_matching["injection_state_sha256"] == [old_matching["injection_state_sha256"][index]],
            role="diagnostic only; complete-interval numeric comparisons use the preregistered tolerances")
        require(new_matching["same_initial_state"] is True and new_matching["same_exogenous_definition"] is True,
            "Fresh reset/exogenous stream not known")
        actual, expected = metrics(record), metrics(c["reference_record"])
        comparisons = compare_metrics(actual, expected)
        passed = all(row["passed"] for row in comparisons)
        command.update(finished_utc=now(), exit_code=0 if passed else 2,
            status="actual_scientific_reproduction_verified" if passed else "failed_numeric_comparison",
            actual_scientific_reproduction_verified=passed, actual_attempts=1, complete_full=1, unknown_full=0,
            episode_audit="passed", source_config_input_reset_exogenous_matching=matched,
            injection_state_diagnostic=injection_diagnostic,
            final_freeze_sha256=sha(c["freeze_path"]), preparation_sha256=sha(c["review"] / "PREPARATION.json"),
            proof_json=(directory / "BATCH_RESULT.json").relative_to(c["root"]).as_posix(),
            proof_sha256=sha(directory / "BATCH_RESULT.json"), attempts_journal_sha256=sha(directory / "ATTEMPTS.jsonl"),
            actual_result_sha256=sha(result_path), actual_raw_sha256=record["raw_sha256"],
            expected_reference_path=c["config"]["reference"]["result"]["path"],
            expected_reference_sha256=sha(c["reference_paths"]["result"]),
            predeclared_tolerances=TOLERANCES, metric_count=len(comparisons), metrics=actual,
            comparisons=comparisons, additional_archived_raw_opens=0,
            actual_runner_command=["python", "-m", "tools.model_closeout.runner", "run", "--directory", TARGET_REL],
            failed_and_unknown_evidence_retained=True,
            independent_scientific_budget_scope=True, original_campaign_budget_not_reset=True,
            independent_run_attempt_accounting=batch["run_attempt_accounting"],
            clone_inventory_sha256=marker["inventory_sha256"], original_experiments_and_raw_copied=0,
            limitations=["Solver/runtime/license are external prerequisites.",
                "Fresh reproduction has its own unchanged 700-attempt/48-hour engineering gate; no archived attempts are moved or reset.",
                "This is one full scientific replay; no field or clinical certification is claimed."])
        write_new(c["review"] / "COMPARISON_PROOF.json", command)
        print(json.dumps({key: command[key] for key in ("status", "actual_attempts", "complete_full", "unknown_full", "metric_count")}))
        return 0 if passed else 2
    except Exception as exc:
        command.update(finished_utc=now(), exit_code=2, status="failed_unknown", error_type=type(exc).__name__,
            message=str(exc), traceback=traceback.format_exc(), all_existing_evidence_retained=True,
            actual_scientific_reproduction_verified=False)
        write_new(c["review"] / "COMPARISON_FAILURE.json", command)
        raise


def reproduce(config_name):
    """One optional launcher; builder deliberately uses the three commands."""
    root = staged_root()
    preflight(config_name, fresh=True)
    logdir = local(root, RUN_REL + "/review/standalone_reproduce_launcher01")
    logdir.mkdir(parents=True, exist_ok=False)
    commands = [["python", "-B", str(Path(__file__).relative_to(root)), mode, "--config", config_name]
        if mode != "runner" else ["python", "-B", "-m", "tools.model_closeout.runner", "run", "--directory", TARGET_REL]
        for mode in ("prepare", "runner", "verify")]
    for index, argv in enumerate(commands):
        started = time.perf_counter()
        name = f"command_{index:02d}"
        cwd = root / SCIENCE_NAME if index == 1 else root
        with (logdir / (name + ".stdout.log")).open("xb") as stdout, (logdir / (name + ".stderr.log")).open("xb") as stderr:
            completed = subprocess.run([sys.executable, *argv[1:]], cwd=cwd, env=child_environment(cwd), stdout=stdout, stderr=stderr, check=False)
        write_new(logdir / (name + ".json"), dict(argv=argv, cwd=str(cwd), exit_code=completed.returncode,
            elapsed_seconds=time.perf_counter() - started, stdout_sha256=sha(logdir / (name + ".stdout.log")),
            stderr_sha256=sha(logdir / (name + ".stderr.log"))))
        if completed.returncode:
            return completed.returncode
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "register-runtime", "verify", "reproduce"))
    parser.add_argument("--config", default=CONFIG_REL)
    args = parser.parse_args(argv)
    try:
        if args.mode == "prepare":
            prepare(args.config)
            return 0
        if args.mode == "verify":
            return verify(args.config)
        if args.mode == "register-runtime":
            register_runtime(args.config)
            return 0
        return reproduce(args.config)
    except Exception as exc:
        print(json.dumps(dict(status="refused_or_failed_unknown", error_type=type(exc).__name__,
            message=str(exc), actual_scientific_reproduction_verified=False)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
