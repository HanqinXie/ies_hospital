"""v12 metadata registration over exposed v11 scenarios; no load-value reads.

The original final twelve are never eligible. New recipes change exogenous
scripts on seen backgrounds, not physical capacities, initial states or queues.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from hospital_ies.dataset_manifest import build_manifest as _build_manifest, digest
from hospital_ies.resilience import validate_scenario
from tools.control_improvement.data_design import validate_observation_support
from tools.autonomous_dispatch.data_design import PHYSICAL_FIELDS

VERSION = "coordinated_dispatch.v12.data_design.v1"
V11 = Path("outputs/v11_autonomous_dispatch_20261016_run01")
SEALED_DAYS = (21, 26, 59, 77, 92, 127, 140, 196, 264, 284, 344, 359)
RESOURCE_LIMITS = dict(max_registered_pairs=2400, max_actual_attempts=2400,
    max_new_physical_specs=480, max_major_recipes=24, max_wall_hours=24,
    limits_are_ceilings_not_quotas=True)
FROZEN_RECIPES = [
    dict(id="phase_plus_6p25", phase_delta_minutes=6.25, shock_scale=1., duration_scale=1.),
    dict(id="phase_plus_11p25_shock_1p10", phase_delta_minutes=11.25, shock_scale=1.10, duration_scale=1.),
    dict(id="phase_plus_8p75_duration_1p10", phase_delta_minutes=8.75, shock_scale=1., duration_scale=1.10),
]
TARGET_IDS = (
    "v11_novel_4c903f1c88151444ed883bf6", "v11_novel_dca6c589b4ea293350c4fd20",
    "v11_novel_ee55231ab149259ba1b122cd", "v11_novel_a4ff25b5d68ba51de824f552",
    "v11_novel_bbfba5b41ecce0552bedb62d", "v11_confirm_236e68f829de83f073e2e5e4",
    "v11_confirm_484dea56799511eec6ee6946", "v11_case_e0332c3a4e66694efcdb152f",
    "v11_regression_54d04ba521cf2f5e2506c0cd", "v11_regression_2c74ba9a9326152f90dac59d",
    "v11_regression_84389b261d1dc25b3a06d594", "v11_regression_2c8b05c4ef9d792214bae64a",
)
CLASSES = ("L1", "L2", "L3", "L4a", "L4b", "N2")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _signed(path):
    value = _read(path)
    if value.get("signature") != digest({k: v for k, v in value.items() if k != "signature"}):
        raise ValueError("Source metadata signature differs: " + str(path))
    return value


def _seal_check(scene):
    support = validate_observation_support(scene)
    if support != scene.get("full_source_support"):
        raise ValueError("Full source support differs from the registered physical clock")
    if scene.get("is_final_blind") or any(
            support["raw_start_minute"] < (day + 1) * 1440
            and day * 1440 < support["raw_end_minute"] for day in SEALED_DAYS):
        raise ValueError("Sealed original final12 support is not authorized")
    validate_scenario(scene)


def _physical(scene):
    return {key: scene.get(key) for key in PHYSICAL_FIELDS}


def _finish(value):
    value.setdefault("source_outcomes_used_for_selection", False)
    value.update(strict_final_gate="not_verified", original_final12_evaluated=False,
        original_final_release_authorized=False, sealed_final_days=list(SEALED_DAYS),
        source_load_values_read=False,
        hospital_count=1, source_year_count=1, statistical_iid_claim=False,
        new_independent_background_count=0,
        historical_annual_qc_already_parsed_physical_values=True)
    value["signature"] = digest(value)
    return value


def build_exposed_pool(workspace_root=ROOT):
    """Read signed definitions/plans/manifests only; exclude 264 unrun definitions.

    Exposure follows the published completed v11 campaign supplied by the root
    task. This metadata helper does not independently rescore or inspect runs.
    """
    root = Path(workspace_root).resolve()
    paths = (V11 / "design/SOURCE_PROPOSAL.json", V11 / "design/NOVEL_BACKGROUND_PROPOSAL.json")
    proposals = [_signed(root / path) for path in paths]
    definitions, roots = {}, {}
    for proposal, path in zip(proposals, paths):
        if proposal["sealed_final_days"] != list(SEALED_DAYS):
            raise ValueError("Original sealed identity registry differs")
        for scene in proposal["scenarios"]:
            if scene["id"] in definitions:
                raise ValueError("Duplicate source scenario ID")
            _seal_check(scene)
            definitions[scene["id"]] = scene
        for record in proposal["roots"]:
            row = copy.deepcopy(record)
            row["source_day_index"] = row.get("source_day_index", row.get("day_index"))
            row["v11_origin_metadata_path"] = path.as_posix()
            row["background_status"] = "previously_exposed_v11_same_hospital_year"
            row["previously_exposed_v11"] = True
            row["content_read_requires_method_freeze"] = False
            roots[row["root_family_id"]] = row
    campaign_path = V11 / "design/FINAL_CAMPAIGN.json"
    campaign = _read(root / campaign_path)
    exposed, evidence, manifests = set(), [], {}
    for batch in campaign["batches"]:
        path = root / batch["directory"] / "registered_plan.json"
        plan = _signed(path)
        manifest_path = Path(plan["manifest_path"]).resolve()
        if not manifest_path.is_relative_to(root / V11):
            raise ValueError("Published v11 manifest escapes its source run")
        if manifest_path not in manifests:
            manifests[manifest_path] = (_sha(manifest_path), _signed(manifest_path))
        sha, manifest = manifests[manifest_path]
        if sha != plan["manifest_sha256"] or manifest["signature"] != plan["manifest_signature"]:
            raise ValueError("Published plan manifest binding differs")
        actual = {s["id"]: s for s in manifest["scenarios"]}
        for sid in plan["scenario_ids"]:
            if sid not in definitions or sid not in actual:
                raise ValueError("Published scenario is outside original definition pool")
            if (_physical(actual[sid]) != _physical(definitions[sid]) or
                    actual[sid]["exogenous_stream_id"] != definitions[sid]["exogenous_stream_id"]):
                raise ValueError("Published scenario physical fields or stream differ")
        exposed.update(plan["scenario_ids"])
        evidence.append(dict(name=batch["name"], plan_path=path.relative_to(root).as_posix(),
            plan_sha256=_sha(path), plan_signature=plan["signature"],
            manifest_path=manifest_path.relative_to(root).as_posix(), manifest_sha256=sha,
            scenario_ids=plan["scenario_ids"]))
    selected = [copy.deepcopy(definitions[sid]) for sid in sorted(exposed)]
    selected_roots = {s["root_family_id"] for s in selected}
    if len(definitions) != 840 or len(selected) != 576 or len(selected_roots) != 70:
        raise ValueError("Published exposed inventory must be 576 specifications / 70 roots of 840 definitions")
    return _finish(dict(version=VERSION + ".exposed_pool", scenarios=selected,
        source_workspace_root=str(root),
        roots=sorted((roots[rid] for rid in selected_roots), key=lambda r: r["start_hour"]),
        registered_definition_count=len(definitions), unrun_definition_count=len(definitions) - len(selected),
        unrun_definition_ids=sorted(set(definitions) - exposed),
        exposure_basis="Root-authorized completed published v11 campaign; plans and manifests only, no result inspection",
        source_metadata_sha256={p.as_posix(): _sha(root / p) for p in (*paths, campaign_path)},
        published_batch_bindings=evidence, analysis_class_counts=dict(Counter(s["analysis_class"] for s in selected))))


def _development(scene, cohort):
    new = copy.deepcopy(scene)
    new["v11_origin_metadata"] = {k: scene.get(k) for k in (
        "experiment_role", "analysis_cohort", "sampling_role", "demand_background",
        "requires_policy_freeze", "new_registered_support_window")}
    new.update(source_v11_scenario_id=scene["id"], experiment_role="development",
        analysis_cohort=cohort, sampling_cohort=cohort, selection_allowed=True, fit_allowed=False,
        requires_policy_freeze=False, previously_exposed_v11=True, is_final_blind=False,
        new_independent_background=False, new_registered_support_window=False,
        demand_background="previously_exposed_v11_same_hospital_year",
        sampling_role="previously_exposed_v11_development", study_name="hospital_ies_service_switch_v12",
        sample_schema_version=VERSION, purpose=cohort)
    _seal_check(new)
    return new


def _subset(pool, ids, cohort, **metadata):
    source = {s["id"]: s for s in pool["scenarios"]}
    scenes = [_development(source[sid], cohort) for sid in ids]
    root_ids = {s["root_family_id"] for s in scenes}
    return _finish(dict(version=VERSION + "." + cohort, scenarios=scenes,
        roots=[copy.deepcopy(r) for r in pool["roots"] if r["root_family_id"] in root_ids],
        source_pool_signature=pool["signature"], analysis_class_counts=dict(Counter(s["analysis_class"] for s in scenes)),
        resource_limits=copy.deepcopy(RESOURCE_LIMITS), **metadata))


def build_targeted(pool):
    """Twelve disclosed cases, exact original physical fields and IDs."""
    return _subset(pool, TARGET_IDS, "targeted_development",
        selection_rule="Root-specified disclosed counterexamples plus day47 N2 negative control",
        source_outcomes_used_for_selection=True,
        outcome_selection_disclosure="Root-selected known disclosed failures and negative control; no outcome files read by this helper",
        original_physical_fields_exact=True, planned_policy_count=7, planned_pair_count=84)


def build_development_selection(pool):
    """Use the existing metadata-balanced 20 roots and earliest novel root/quarter."""
    source_root = Path(pool["source_workspace_root"]).resolve()
    # The source reference comes from the pool binding, never an output value.
    first_binding = Path(pool["published_batch_bindings"][0]["plan_path"])
    if not (source_root / first_binding).is_file():
        raise ValueError("Published source workspace must be available for selection")
    selection_path = V11 / "design/MARGIN_EXPANSION_SELECTION.json"
    selection = _signed(source_root / selection_path)
    by_id = {s["id"]: s for s in pool["scenarios"]}
    ids, root_choices = [], []
    for row in selection["selected_roots"]:
        chosen = [row["scene_ids"][name] for name in CLASSES]
        if any(sid not in by_id for sid in chosen):
            raise ValueError("Existing balanced selection contains an unexposed specification")
        ids.extend(chosen)
        root_choices.append(dict(root_family_id=row["root_family_id"], selected_cell=row["selected_cell"],
            scene_ids=dict(zip(CLASSES, chosen)), rule="Previously metadata-balanced v11 20-root subset"))
    for quarter in range(1, 5):
        candidates = [r for r in pool["roots"] if r["season_quarter"] == quarter
            and r["root_family_id"].startswith("v11_novel_root:")]
        record = min(candidates, key=lambda r: r["start_hour"])
        scenes = sorted((s for s in pool["scenarios"] if s["root_family_id"] == record["root_family_id"]),
                        key=lambda s: CLASSES.index(s["analysis_class"]))
        if {s["analysis_class"] for s in scenes} != {"L1", "L2", "L3", "L4b", "N2"}:
            raise ValueError("Original novel root has unexpected real classes")
        ids.extend(s["id"] for s in scenes)
        root_choices.append(dict(root_family_id=record["root_family_id"], selected_cell="original_novel",
            scene_ids={s["analysis_class"]: s["id"] for s in scenes},
            rule="Earliest previously exposed original novel root in each quarter"))
    result = _subset(pool, ids, "distributed_development", root_choices=root_choices,
        selection_rule="Pure metadata: existing 20-root balanced subset plus earliest novel root per quarter; no outcomes",
        source_selection_path=selection_path.as_posix(), source_selection_sha256=_sha(source_root / selection_path),
        coverage_gaps=["L4a does not exist in the four added original novel roots; no fabricated replacement"],
        planned_policy_count=5, planned_pair_count=700)
    if len(result["roots"]) != 24 or len(ids) != len(set(ids)) or len(ids) != 140:
        raise ValueError("Development selection must contain 24 roots and 140 real specifications")
    return result


def _recipe(value):
    if set(value) != {"id", "phase_delta_minutes", "shock_scale", "duration_scale"} or not value["id"]:
        raise ValueError("Unknown or missing frozen recipe field")
    for key in ("phase_delta_minutes", "shock_scale", "duration_scale"):
        x = value[key]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 or (key != "phase_delta_minutes" and x == 0):
            raise ValueError("Invalid frozen recipe numeric parameter")
    return copy.deepcopy(value)


def _scale_shock(mapping, scale):
    return {k: (_scale_shock(v, scale) if isinstance(v, dict) else
                (v * scale if k in ("e", "h", "c") and v != 0 else v)) for k, v in mapping.items()}


def _perturb(scene, recipe):
    new = _development(scene, "frozen_phase_confirmation")
    normal = scene["analysis_class"] == "N2"
    new.update(experiment_role="confirmation", selection_allowed=False, fit_allowed=False,
        requires_policy_freeze=True, recipe=copy.deepcopy(recipe),
        same_background_new_perturbation=not normal, physical_duplicate_of=scene["id"] if normal else None,
        sampling_role="frozen_new_recipe_on_seen_v11_root" if not normal else "repeated_normal_cost_regression",
        is_new_physical_spec=not normal, normal_is_new_perturbation=False)
    if not normal:
        phase, scale, duration = (recipe[k] for k in ("phase_delta_minutes", "shock_scale", "duration_scale"))
        for event in new["exogenous_events"]:
            old_start = event["start_minute"]
            event["start_minute"] = old_start + phase
            event["end_minute"] = old_start + phase + (event["end_minute"] - old_start) * duration
            if event["kind"] == "load_shock":
                for key in ("shock", "shock_by_priority"):
                    event[key] = _scale_shock(event.get(key, {}), scale)
        for kind in ("fault", "shock"):
            if kind + "_start_minute" in new:
                new[kind + "_start_minute"] += phase
            if kind + "_duration_minutes" in new:
                new[kind + "_duration_minutes"] *= duration
        for key in ("shock", "shock_by_priority"):
            new[key] = _scale_shock(new.get(key, {}), scale)
        new["actual_p0_load_multiplier"] = {k: (1 + new["shock"].get(k, 0.)) *
            (1 + new["shock_by_priority"].get("P0", {}).get(k, 0.)) for k in ("e", "h", "c")}
        horizon = new["duration_steps"] * new["step_minutes"]
        tail = max(new["label_horizons_minutes"])
        if any(event["end_minute"] > horizon - tail or event["start_minute"] < 0
               for event in new["exogenous_events"]):
            raise ValueError("Frozen recipe exceeds authorized support or complete label-tail horizon")
        new["exogenous_stream_id"] = "v12_exogenous:" + digest([new["root_family_id"], new["exogenous_events"]])
    new["physical_scenario_signature"] = digest(_physical(new))
    new["id"] = "v12_confirm_" + digest([scene["id"], recipe, new["physical_scenario_signature"]])[:24]
    new["design_cell_id"] = new["matched_group_id"] = "v12_recipe_cell:" + digest([new["root_family_id"], recipe])[:24]
    new["design_axis"] = "frozen_recipe" if not normal else "repeated_normal"
    new["design_axis_value"] = recipe["id"]
    _seal_check(new)
    return new


def build_frozen_proposal(pool, development_selection, *, recipes=FROZEN_RECIPES):
    """Pre-register paired recipes only; policy freeze is still required to run."""
    chosen = [_recipe(r) for r in recipes]
    if len(chosen) != 3 or len({r["id"] for r in chosen}) != 3:
        raise ValueError("Exactly three distinct registered recipes are required")
    source = {s["id"]: s for s in pool["scenarios"]}
    assignments = {}
    for quarter in range(1, 5):
        roots = sorted((r for r in development_selection["roots"] if r["season_quarter"] == quarter),
                       key=lambda r: r["start_hour"])
        for index, record in enumerate(roots):
            assignments[record["root_family_id"]] = chosen[index % len(chosen)]
    scenes = [_perturb(source[s["source_v11_scenario_id"]], assignments[s["root_family_id"]])
              for s in development_selection["scenarios"]]
    return _finish(dict(version=VERSION + ".frozen_recipe_proposal", scenarios=scenes,
        roots=copy.deepcopy(development_selection["roots"]), recipes=chosen,
        root_recipe_assignments={rid: r["id"] for rid, r in assignments.items()},
        recipe_root_counts=dict(Counter(r["id"] for r in assignments.values())),
        source_pool_signature=pool["signature"], source_development_signature=development_selection["signature"],
        resource_limits=copy.deepcopy(RESOURCE_LIMITS), planned_policy_count=4, planned_pair_count=560,
        new_physical_spec_count=sum(s["is_new_physical_spec"] for s in scenes),
        normal_physical_repeat_count=sum(s["analysis_class"] == "N2" for s in scenes),
        shock_scaling_semantics="Multiply only existing nonzero e/h/c increments, including priority increments; preserve zeros",
        timing_semantics="Add the stated phase to all original event clocks; multiply duration without truncation",
        confirmation_gate="Method/source/specification freeze required; no plan registered by this helper",
        independence="All backgrounds exposed in v11; new recipes are not independent hospitals, years or unseen roots"))


def build_manifest(scenarios, roots, *, source_sha256, planning_interface_id, action_schema,
                   extra_contracts=None, policies=("priority_milp", "secure_milp"),
                   workspace_root=ROOT, execution_model_id="M0"):
    """Reuse original inventory/physics construction with injected runtime IDs.

    This reads metadata and hashes input bytes only. It never constructs an env
    or parses physical rows. Actual registration is owned by the new runner.
    """
    scenes = copy.deepcopy(list(scenarios))
    contracts = dict(extra_contracts or {})
    if any(not isinstance(k, str) or not k.endswith("_contract_id") or
           not isinstance(v, str) or not v for k, v in contracts.items()):
        raise ValueError("Extra contract fields must be named nonempty *_contract_id strings")
    if "execution_contract_id" in contracts:
        raise ValueError("Original execution contract cannot be replaced")
    if execution_model_id != "M0":
        raise ValueError("This registration authorizes the unchanged M0 execution model only")
    if not planning_interface_id or not action_schema or not source_sha256:
        raise ValueError("Explicit source and interface binding is required")
    if any(not isinstance(k, str) or not isinstance(v, str) or len(v) != 64 or
           any(c not in "0123456789abcdef" for c in v) for k, v in source_sha256.items()):
        raise ValueError("Invalid runtime source SHA256 map")
    if not scenes or len({s["id"] for s in scenes}) != len(scenes):
        raise ValueError("Manifest requires nonempty unique scenario identities")
    root_ids = {r["root_family_id"] for r in roots}
    splits = {}
    for scene in scenes:
        _seal_check(scene)
        if scene["root_family_id"] not in root_ids:
            raise ValueError("Scenario root is absent from source roots")
        role = scene["experiment_role"]
        if role not in ("development", "confirmation") or scene.get("fit_allowed"):
            raise ValueError("Only nontraining development/confirmation roles are authorized")
        frozen = role == "confirmation"
        if bool(scene["requires_policy_freeze"]) != frozen or bool(scene["selection_allowed"]) == frozen:
            raise ValueError("Scenario selection/freeze role metadata is inconsistent")
        support = scene["full_source_support"]
        splits[scene["id"]] = dict(group_id=scene["root_family_id"], split=role, experiment_role=role,
            fit_allowed=False, selection_allowed=not frozen, development_only=not frozen,
            requires_policy_freeze=frozen, is_final_blind=False, support_independence_claim=False,
            raw_start_minute=support["raw_start_minute"], raw_end_minute=support["raw_end_minute"],
            time_block_id=scene["time_block_id"], embargo_minutes_each_side=60,
            split_version=VERSION, purpose=scene["purpose"])
    root = Path(workspace_root).resolve()
    old = _signed(root / V11 / "new_data/standby_manifest.json")
    schemas = dict(old["schemas"], action=action_schema)
    manifest = _build_manifest(root, scenes, policies, copy.deepcopy(old["execution_contract"]),
                               schemas, copy.deepcopy(old["options"]), splits)
    manifest.pop("signature", None)
    manifest.update(manifest_version=VERSION + ".manifest", study_name="hospital_ies_service_switch_v12",
        source_sha256=copy.deepcopy(source_sha256), planning_interface_id=planning_interface_id,
        execution_model_id=execution_model_id, roots=copy.deepcopy(list(roots)),
        data_loading_contract="Original plan-bound selective inputs; unauthorized rows NaN; no final12 release",
        analysis_class_counts=dict(Counter(s["analysis_class"] for s in scenes)),
        probability_semantics="Designed stress cases; not hospital failure probabilities", **contracts)
    return _finish(manifest)
