"""Preregistered v5 matched-stage plans; never reads future-stage outcomes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import copy


ROOT = Path(__file__).resolve().parents[2]
GATE_THRESHOLDS = (0.25, 0.50, 0.75)
SELECTION_NEURAL_MODELS = (
    ("nn_gru_prototype", "gru_g1_blend_overload_seed5101"),
    ("nn_gru_aggregate_r1", "gru_agg1_blend_overload_seed5102"),
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value):
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return path


def prepare_alternative_audit_compat(run_dir):
    """Bind the original alternative registration to audit.py's receipt shape.

    `audit_batch` always reads a SEARCH_REGISTRATION.json and checks its search
    source hash. The alternative has its own prior registration/source; this
    once-only compatibility receipt records that original SHA and is checked
    again before a later selection plan may use the alternative AUDIT.json.
    """
    run = Path(run_dir)
    folder = run / "search/alternative_modes"
    output = folder / "SEARCH_REGISTRATION.json"
    if output.exists():
        raise FileExistsError("Alternative audit compatibility receipt is immutable")
    plan_path = folder / "registered_plan.json"
    original_path = folder / "ALTERNATIVE_REGISTRATION.json"
    manifest_path = run / "new_data/global_manifest.json"
    preference_path = run / "design/RESEARCH_PREFERENCE_v1.json"
    analysis_path = run / "search/generation_1/ANALYSIS.json"
    freeze_path = run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    plan = _read(plan_path)
    original = _read(original_path)
    freeze = _read(freeze_path)
    if (plan.get("schema") != "policy_search.matched_full_feedback.v1" or
            plan.get("phase") != "train" or
            plan.get("signature") != _digest({k: v for k, v in plan.items()
                                              if k != "signature"})):
        raise ValueError("alternative_plan_signature or phase invalid")
    if original.get("schema") != "policy_search.alternative_modes.v1":
        raise ValueError("alternative_registration_schema invalid")
    if original.get("source_sha256") != _sha(ROOT / "tools/policy_search/alternative.py"):
        raise ValueError("alternative_source_sha256 changed")
    if original.get("generation_1_analysis_sha256") != _sha(analysis_path):
        raise ValueError("generation_1_analysis_sha256 changed")
    if original.get("manifest_sha256") != _sha(manifest_path):
        raise ValueError("alternative_manifest_sha256 changed")
    for key in ("plan_signature", "scenario_ids", "policy_specs"):
        expected = plan["signature"] if key == "plan_signature" else plan[key]
        if original.get(key) != expected:
            raise ValueError("alternative_original_plan_binding differs: " + key)
    if (plan.get("manifest_sha256") != _sha(manifest_path) or
            plan.get("preference_sha256") != _sha(preference_path) or
            Path(plan["manifest_path"]).resolve() != manifest_path.resolve() or
            Path(plan["preference_path"]).resolve() != preference_path.resolve()):
        raise ValueError("alternative_plan_manifest_or_preference differs")
    for name, expected in plan.get("source_sha256", {}).items():
        if _sha(ROOT / name) != expected:
            raise ValueError("alternative_runner_source_sha256 changed: " + name)
    search_sha = _sha(ROOT / "tools/policy_search/search.py")
    if (freeze.get("schema") != "policy_search.algorithm_source_freeze.v1" or
            freeze.get("source_sha256", {}).get("tools/policy_search/search.py") != search_sha or
            freeze.get("v5_new_manifest_sha256") != _sha(manifest_path) or
            freeze.get("preference_sha256") != _sha(preference_path)):
        raise ValueError("alternative_analysis_source_freeze differs")
    receipt = {"schema": "policy_search.offline_generation.v1",
               "generation": "alternative_modes_audit_compat",
               "source_sha256": search_sha,
               "manifest_sha256": plan["manifest_sha256"],
               "preference_sha256": plan["preference_sha256"],
               "scenario_ids": plan["scenario_ids"],
               "policy_specs": plan["policy_specs"],
               "plan_signature": plan["signature"],
               "origin_registration_path": str(original_path.resolve()),
               "origin_registration_sha256": _sha(original_path),
               "origin_source_sha256": original["source_sha256"],
               "generation_1_analysis_sha256": original["generation_1_analysis_sha256"],
               "compatibility_only": True,
               "original_alternative_registration_preserved": True}
    return _new(output, receipt)


def choose_phase_scenes(manifest, *, role, roots_per_condition, forbidden_roots=(),
                        pressure_index=3):
    """Choose disjoint roots by registered role/condition, without outcomes."""
    by_condition = {}
    for scene in manifest["scenarios"]:
        if (scene.get("experiment_role") == role and
                scene.get("pressure_point_index") == pressure_index and
                not scene.get("is_final_blind")):
            by_condition.setdefault(scene["condition_id"], []).append(scene)
    if len(by_condition) != 6:
        raise ValueError("Six named conditions required for a balanced stage")
    forbidden = set(forbidden_roots)
    chosen, used = [], set()
    for condition in sorted(by_condition):
        rows = sorted(by_condition[condition],
                      key=lambda s: (s["time_block_id"], s["id"]))
        picked = 0
        for scene in rows:
            root = scene["root_family_id"]
            if root in forbidden or root in used:
                continue
            chosen.append(scene)
            used.add(root)
            picked += 1
            if picked == roots_per_condition:
                break
        if picked != roots_per_condition:
            raise ValueError("Insufficient disjoint roots in stage condition " + condition)
    return chosen


def expand_development_pressure_cross(manifest, anchor_scenes):
    """Expand preregistered pressure-3 development roots to all five inputs."""
    by_root = {}
    for scene in manifest["scenarios"]:
        if scene.get("experiment_role") == "development" and not scene.get("is_final_blind"):
            by_root.setdefault(scene["root_family_id"], []).append(scene)
    selected = []
    for anchor in anchor_scenes:
        rows = by_root[anchor["root_family_id"]]
        by_index = {s["pressure_point_index"]: s for s in rows}
        if len(rows) != 5 or set(by_index) != set(range(5)):
            raise ValueError("Development pressure cross requires all five supported pressure points")
        for key in ("condition_id", "time_block_id", "exogenous_stream_id",
                    "initialization_seed", "base_state_id"):
            if any(s.get(key) != anchor.get(key) for s in rows):
                raise ValueError("Development pressure cross differs in same-root identity: " + key)
        group = manifest["splits"][anchor["id"]]["group_id"]
        if any(manifest["splits"][s["id"]]["group_id"] != group for s in rows):
            raise ValueError("Development pressure cross differs in same-root group")
        selected.extend(by_index[index] for index in range(5))
    if len(selected) != len(anchor_scenes) * 5 or len({s["id"] for s in selected}) != len(selected):
        raise ValueError("Development pressure cross repeats a scenario")
    return selected


def _latest_summary(folder):
    summaries = sorted((Path(folder) / "results").glob("summary_*.json"))
    if not summaries:
        raise FileNotFoundError("Audited batch has no result summary: " + str(folder))
    return summaries[-1]


def _verify_predecessor(run, batch):
    """Require genuine audit bytes and v2 analysis for every train source."""
    from tools.policy_search.preference_v2 import analyze_audited_batch

    folder = run / "search" / batch
    plan_path = folder / "registered_plan.json"
    summary_path = _latest_summary(folder)
    audit_path = folder / "AUDIT.json"
    analysis_path = folder / "ANALYSIS_PREFERENCE_V2.json"
    freeze_path = run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"
    revision_path = run / "design/PREFERENCE_V2_SOURCE_FREEZE.json"
    registration_path = folder / "SEARCH_REGISTRATION.json"
    plan = _read(plan_path)
    summary = _read(summary_path)
    audit = _read(audit_path)
    registration = _read(registration_path)
    if audit.get("passed") is not True or audit.get("errors"):
        raise ValueError(batch + " AUDIT is not passing")
    for key, path in (("plan_sha256", plan_path),
                      ("summary_sha256", summary_path),
                      ("source_freeze_sha256", freeze_path),
                      ("registration_sha256", registration_path),
                      ("audit_source_sha256", ROOT / "tools/policy_search/audit.py")):
        if audit.get(key) != _sha(path):
            raise ValueError(batch + " " + key + " differs from AUDIT")
    if (plan.get("phase") != "train" or
            plan.get("manifest_sha256") != _sha(run / "new_data/global_manifest.json") or
            plan.get("preference_sha256") != _sha(run / "design/RESEARCH_PREFERENCE_v1.json") or
            summary.get("plan_signature") != plan.get("signature")):
        raise ValueError(batch + " train plan/manifest/preference binding differs")
    for key, expected in (("plan_signature", plan["signature"]),
                          ("scenario_ids", plan["scenario_ids"]),
                          ("policy_specs", plan["policy_specs"]),
                          ("manifest_sha256", plan["manifest_sha256"]),
                          ("preference_sha256", plan["preference_sha256"])):
        if registration.get(key) != expected:
            raise ValueError(batch + " registration binding differs: " + key)
    if batch == "alternative_modes":
        original_path = folder / "ALTERNATIVE_REGISTRATION.json"
        original = _read(original_path)
        if (original.get("schema") != "policy_search.alternative_modes.v1" or
                original.get("source_sha256") !=
                _sha(ROOT / "tools/policy_search/alternative.py") or
                original.get("generation_1_analysis_sha256") !=
                _sha(run / "search/generation_1/ANALYSIS.json") or
                original.get("manifest_sha256") != plan["manifest_sha256"] or
                original.get("plan_signature") != plan["signature"] or
                original.get("scenario_ids") != plan["scenario_ids"] or
                original.get("policy_specs") != plan["policy_specs"] or
                registration.get("origin_registration_sha256") != _sha(original_path) or
                registration.get("origin_source_sha256") != original["source_sha256"] or
                registration.get("generation_1_analysis_sha256") !=
                original["generation_1_analysis_sha256"]):
            raise ValueError("alternative original registration differs")
    elif registration.get("schema") != "policy_search.offline_generation.v1" or (
            registration.get("source_sha256") != _sha(ROOT / "tools/policy_search/search.py")):
        raise ValueError(batch + " search registration source differs")
    actual_analysis = analyze_audited_batch(plan_path, summary_path, audit_path,
                                            revision_path, freeze_path)
    if _read(analysis_path) != actual_analysis:
        raise ValueError(batch + " v2 analysis differs from audited inputs")
    return plan, {"plan_sha256": _sha(plan_path),
                  "summary_sha256": _sha(summary_path),
                  "audit_sha256": _sha(audit_path),
                  "analysis_v2_sha256": _sha(analysis_path),
                  "registration_sha256": _sha(registration_path),
                  "plan_signature": plan["signature"],
                  "unknown_primary_pair_count": len(audit.get("unknown_primary_pair_ids", []))}


def distinct_feedback_specs(source_plans):
    """Deduplicate exact feedback vectors; retain every origin in the registry."""
    specs, by_vector, by_id, origins = [], {}, {}, {}
    for batch, plan in source_plans.items():
        for spec in plan["policy_specs"]:
            if spec["kind"] != "feedback":
                continue
            vector_key = _digest([spec["family"], spec["theta"]])
            pid = spec["policy_id"]
            if pid in by_id and by_id[pid] != vector_key:
                raise ValueError("Same feedback policy ID has different vectors")
            by_id[pid] = vector_key
            if vector_key in by_vector:
                origins[by_vector[vector_key]].append({"batch": batch, "policy_id": pid})
                continue
            if pid in {"secure_milp", "strict_guard", "current_rule"}:
                raise ValueError("Feedback policy ID collides with named control")
            by_vector[vector_key] = pid
            origins[pid] = [{"batch": batch, "policy_id": pid}]
            specs.append(copy.deepcopy(spec))
    return specs, origins


def _verified_neural_model(run, model_dir, teacher_spec, manifest):
    """Bind a train-root GRU, including the one-round aggregated source shape."""
    from tools.policy_search.neural import _load_artifacts

    folder, metadata = _load_artifacts(model_dir)
    source = _read(folder / "source_audit.json")
    manifest_sha = _sha(run / "new_data/global_manifest.json")
    allowed = {s["root_family_id"] for s in manifest["scenarios"]
               if s["experiment_role"] == "train" and not s.get("is_final_blind")}
    if (metadata.get("gate_mode") != "none" or
            metadata.get("teacher_policy_id") != teacher_spec["policy_id"] or
            source.get("role") != "train" or
            source.get("manifest_sha256") != manifest_sha or
            source.get("final_12_backgrounds_opened") is not False):
        raise ValueError("Neural selection model lacks frozen train teacher/source")
    if "original_teacher" in source:
        original = source["original_teacher"]
        relabel = source.get("nn_visited_relabel") or {}
        original_roots = set(original.get("root_family_ids") or [])
        relabel_roots = set(relabel.get("root_family_ids") or [])
        if (original.get("role") != "train" or
                original.get("teacher_policy_id") != teacher_spec["policy_id"] or
                original.get("manifest_sha256") != manifest_sha or
                original.get("final_12_backgrounds_opened") is not False or
                original.get("intervention_decisions", 0) < 1 or
                relabel.get("manifest_sha256") != manifest_sha or
                relabel.get("teacher_spec") != teacher_spec or
                relabel.get("final_12_backgrounds_opened") is not False or
                relabel.get("teacher_interventions", 0) < 1 or
                not relabel_roots or not relabel_roots <= original_roots or
                source.get("join", {}).get("distinct_train_roots") != len(original_roots)):
            raise ValueError("Aggregated NN relabel source differs from train teacher")
        roots = original_roots | relabel_roots
        source_kind = "aggregated_teacher_on_nn_visited_states"
    else:
        roots = set(source.get("root_family_ids") or [])
        if (source.get("teacher_policy_id") != teacher_spec["policy_id"] or
                source.get("intervention_decisions", 0) < 1):
            raise ValueError("Neural prototype source differs from train teacher")
        source_kind = "original_teacher_rollouts"
    if not roots or not roots <= allowed:
        raise ValueError("Neural fit source must use train roots only")
    return folder, metadata, {"model_metadata_sha256": _sha(folder / "metadata.json"),
                              "source_audit_sha256": _sha(folder / "source_audit.json"),
                              "teacher_policy_id": teacher_spec["policy_id"],
                              "source_kind": source_kind,
                              "train_root_family_ids": sorted(roots)}


def freeze_stages(run_dir):
    """Freeze stage implementation and both actual NN artifacts before selection."""
    run = Path(run_dir)
    output = run / "design/STAGES_SOURCE_FREEZE.json"
    if output.exists():
        raise FileExistsError("Stage source freeze is immutable")
    g1, g1_binding = _verify_predecessor(run, "generation_1")
    manifest = _read(run / "new_data/global_manifest.json")
    teacher_id = _read(run / "models/gru_g1_blend_overload_seed5101/metadata.json").get(
        "teacher_policy_id")
    teachers = [s for s in g1["policy_specs"]
                if s["policy_id"] == teacher_id and s["kind"] == "feedback"]
    if len(teachers) != 1:
        raise ValueError("Frozen NN teacher is not in audited generation 1")
    sources = {}
    for policy_id, name in SELECTION_NEURAL_MODELS:
        _, _, provenance = _verified_neural_model(run, run / "models" / name,
                                                   teachers[0], manifest)
        sources[policy_id] = provenance
    files = {
        "stage_source_sha256": __file__,
        "neural_source_sha256": ROOT / "tools/policy_search/neural.py",
        "neural_aggregate_source_sha256": ROOT / "tools/policy_search/neural_aggregate.py",
        "preference_v2_source_sha256": ROOT / "tools/policy_search/preference_v2.py",
        "audit_source_sha256": ROOT / "tools/policy_search/audit.py",
        "manifest_sha256": run / "new_data/global_manifest.json",
        "preference_sha256": run / "design/RESEARCH_PREFERENCE_v1.json",
        "search_source_freeze_sha256": run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json",
        "preference_v2_revision_sha256": run / "design/PREFERENCE_V2_SOURCE_FREEZE.json",
    }
    receipt = {"schema": "policy_search.stage_source_freeze.v1",
               "frozen_before_selection_calibration_development_outcomes": True,
               "final_blind_opened": False,
               "generation_1_audit_sha256": g1_binding["audit_sha256"],
               "generation_1_analysis_v2_sha256": g1_binding["analysis_v2_sha256"],
               "selection_neural_model_sources": sources,
               "predeclared_gate_thresholds": list(GATE_THRESHOLDS),
               **{key: _sha(path) for key, path in files.items()}}
    return _new(output, receipt)


def _verify_stages_source_freeze(run):
    path = run / "design/STAGES_SOURCE_FREEZE.json"
    receipt = _read(path)
    expected = {
        "stage_source_sha256": __file__,
        "neural_source_sha256": ROOT / "tools/policy_search/neural.py",
        "neural_aggregate_source_sha256": ROOT / "tools/policy_search/neural_aggregate.py",
        "preference_v2_source_sha256": ROOT / "tools/policy_search/preference_v2.py",
        "audit_source_sha256": ROOT / "tools/policy_search/audit.py",
        "manifest_sha256": run / "new_data/global_manifest.json",
        "preference_sha256": run / "design/RESEARCH_PREFERENCE_v1.json",
        "search_source_freeze_sha256": run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json",
        "preference_v2_revision_sha256": run / "design/PREFERENCE_V2_SOURCE_FREEZE.json",
    }
    if (receipt.get("schema") != "policy_search.stage_source_freeze.v1" or
            receipt.get("frozen_before_selection_calibration_development_outcomes") is not True or
            receipt.get("final_blind_opened") is not False or
            receipt.get("predeclared_gate_thresholds") != list(GATE_THRESHOLDS)):
        raise ValueError("Stage source freeze contract differs")
    for key, source_path in expected.items():
        if receipt.get(key) != _sha(source_path):
            raise ValueError("Stage source freeze differs: " + key)
    if set(receipt.get("selection_neural_model_sources", {})) != {
            pid for pid, _ in SELECTION_NEURAL_MODELS}:
        raise ValueError("Stage source freeze NN source set differs")
    for policy_id, name in SELECTION_NEURAL_MODELS:
        model_dir = run / "models" / name
        source = receipt["selection_neural_model_sources"][policy_id]
        if (source.get("model_metadata_sha256") != _sha(model_dir / "metadata.json") or
                source.get("source_audit_sha256") != _sha(model_dir / "source_audit.json")):
            raise ValueError("Stage source freeze NN artifact differs: " + policy_id)
    return path, receipt


def register_selection(run_dir):
    """Freeze a six-background, fully matched selection comparison."""
    from tools.policy_search.closed_loop import register_batch

    run = Path(run_dir)
    folder = run / "selection"
    if folder.exists():
        raise FileExistsError("Selection registration requires a fresh directory")
    source_freeze_path, _ = _verify_stages_source_freeze(run)
    source_plans, bindings = {}, {}
    for batch in ("generation_1", "generation_2", "alternative_modes"):
        source_plans[batch], bindings[batch] = _verify_predecessor(run, batch)
    manifest_path = run / "new_data/global_manifest.json"
    manifest = _read(manifest_path)
    scenes = {s["id"]: s for s in manifest["scenarios"]}
    train_roots = {scenes[sid]["root_family_id"] for plan in source_plans.values()
                   for sid in plan["scenario_ids"]}
    selected = choose_phase_scenes(manifest, role="selection", roots_per_condition=1,
                                   forbidden_roots=train_roots)
    feedback, origins = distinct_feedback_specs(source_plans)
    if not feedback:
        raise ValueError("Selection needs at least one actual feedback candidate")
    by_feedback_id = {s["policy_id"]: s for s in feedback}
    prototype_dir = run / "models/gru_g1_blend_overload_seed5101"
    teacher_id = _read(prototype_dir / "metadata.json").get("teacher_policy_id")
    teacher = by_feedback_id.get(teacher_id)
    if teacher is None:
        raise ValueError("Selection NN teacher must be a registered feedback candidate")
    neural_specs, neural_sources = [], {}
    for policy_id, name in SELECTION_NEURAL_MODELS:
        model_dir, _, source = _verified_neural_model(run, run / "models" / name,
                                                       teacher, manifest)
        neural_specs.append({"policy_id": policy_id, "kind": "neural_feedback",
                             "model_dir": str(model_dir)})
        neural_sources[policy_id] = source
    strict_dir = "outputs/v43_reliability_first_20261015_run01/learning/full_nn_fit01"
    specs = ([{"policy_id": "secure_milp", "kind": "secure"},
              {"policy_id": "strict_guard", "kind": "strict_guard",
               "model_dir": strict_dir},
              {"policy_id": "current_rule", "kind": "current_rule"}] +
             feedback + neural_specs)
    plan_path = folder / "registered_plan.json"
    plan = register_batch(manifest_path, plan_path,
                          scenario_ids=[s["id"] for s in selected],
                          policy_specs=specs,
                          preference_path=run / "design/RESEARCH_PREFERENCE_v1.json",
                          phase="selection")
    compat = {"schema": "policy_search.offline_generation.v1",
              "generation": "selection_stage_audit_compat",
              "source_sha256": _sha(ROOT / "tools/policy_search/search.py"),
              "manifest_sha256": plan["manifest_sha256"],
              "preference_sha256": plan["preference_sha256"],
              "scenario_ids": plan["scenario_ids"],
              "policy_specs": plan["policy_specs"],
              "plan_signature": plan["signature"],
              "stage_source_sha256": _sha(__file__),
              "prerequisite_audit_sha256": {key: value["audit_sha256"]
                                             for key, value in bindings.items()}}
    compat_path = _new(folder / "SEARCH_REGISTRATION.json", compat)
    _new(folder / "STAGE_REGISTRATION.json",
         {"schema": "policy_search.selection_stage_registration.v1",
          "stage_source_sha256": _sha(__file__),
          "plan_sha256": _sha(plan_path), "plan_signature": plan["signature"],
          "manifest_sha256": plan["manifest_sha256"],
          "preference_sha256": plan["preference_sha256"],
          "v2_revision_sha256": _sha(run / "design/PREFERENCE_V2_SOURCE_FREEZE.json"),
          "stages_source_freeze_sha256": _sha(source_freeze_path),
          "selection_rule": "one previously unused root per six named conditions at pressure index 3; no outcome inputs",
          "scenario_ids": plan["scenario_ids"],
          "policy_specs": plan["policy_specs"],
          "feedback_origins": origins,
          "neural_model_sources": neural_sources,
          "neural_source_sha256": _sha(ROOT / "tools/policy_search/neural.py"),
          "neural_aggregate_source_sha256": _sha(ROOT / "tools/policy_search/neural_aggregate.py"),
          "strict_guard_model_binding": plan["model_bindings"]["strict_guard"],
          "prerequisite_batches": bindings,
          "audit_compat_registration_sha256": _sha(compat_path),
          "final_blind_opened": False})
    return plan


def _verify_stage_result(run, role):
    """Recheck a registered stage, its audit, and exact v2 analysis bytes."""
    from tools.policy_search.preference_v2 import analyze_audited_batch

    folder = run / role
    source_freeze_path, _ = _verify_stages_source_freeze(run)
    plan_path = folder / "registered_plan.json"
    summary_path = _latest_summary(folder)
    audit_path = folder / "AUDIT.json"
    analysis_path = folder / "ANALYSIS_PREFERENCE_V2.json"
    stage_path = folder / "STAGE_REGISTRATION.json"
    compat_path = folder / "SEARCH_REGISTRATION.json"
    plan, audit, stage, compat = (_read(p) for p in
                                  (plan_path, audit_path, stage_path, compat_path))
    if plan.get("phase") != role:
        raise ValueError(role + " plan phase differs")
    if (stage.get("schema") != f"policy_search.{role}_stage_registration.v1" or
            stage.get("stage_source_sha256") != _sha(__file__) or
            stage.get("plan_sha256") != _sha(plan_path) or
            stage.get("plan_signature") != plan.get("signature") or
            stage.get("scenario_ids") != plan.get("scenario_ids") or
            stage.get("policy_specs") != plan.get("policy_specs") or
            stage.get("manifest_sha256") != plan.get("manifest_sha256") or
            stage.get("preference_sha256") != plan.get("preference_sha256") or
            stage.get("audit_compat_registration_sha256") != _sha(compat_path) or
            stage.get("v2_revision_sha256") !=
            _sha(run / "design/PREFERENCE_V2_SOURCE_FREEZE.json") or
            stage.get("stages_source_freeze_sha256") != _sha(source_freeze_path)):
        raise ValueError(role + " stage registration differs")
    if (compat.get("schema") != "policy_search.offline_generation.v1" or
            compat.get("plan_signature") != plan["signature"] or
            compat.get("scenario_ids") != plan["scenario_ids"] or
            compat.get("policy_specs") != plan["policy_specs"] or
            compat.get("manifest_sha256") != plan["manifest_sha256"] or
            compat.get("preference_sha256") != plan["preference_sha256"] or
            compat.get("stage_source_sha256") != _sha(__file__)):
        raise ValueError(role + " audit compatibility registration differs")
    from tools.policy_search.closed_loop import _model_binding
    for spec in plan["policy_specs"]:
        if spec.get("model_dir") and _model_binding(spec) != plan["model_bindings"][spec["policy_id"]]:
            raise ValueError(role + " model bytes changed after registration: " + spec["policy_id"])
    if role == "selection":
        for batch, binding in stage["prerequisite_batches"].items():
            for key, filename in (("audit_sha256", "AUDIT.json"),
                                  ("analysis_v2_sha256", "ANALYSIS_PREFERENCE_V2.json")):
                if binding[key] != _sha(run / "search" / batch / filename):
                    raise ValueError("Selection prerequisite changed: " + batch + "/" + key)
    if role in ("calibration", "development"):
        for key, pathname in (("selection_audit_sha256", "selection/AUDIT.json"),
                              ("selection_analysis_v2_sha256",
                               "selection/ANALYSIS_PREFERENCE_V2.json")):
            if stage.get(key) != _sha(run / pathname):
                raise ValueError(role + " selection source changed: " + key)
    if role == "development":
        for key, pathname in (("calibration_audit_sha256", "calibration/AUDIT.json"),
                              ("calibration_analysis_v2_sha256",
                               "calibration/ANALYSIS_PREFERENCE_V2.json")):
            if stage.get(key) != _sha(run / pathname):
                raise ValueError("development calibration source changed: " + key)
    if audit.get("passed") is not True or audit.get("errors"):
        raise ValueError(role + " AUDIT is not passing")
    for key, path in (("plan_sha256", plan_path), ("summary_sha256", summary_path),
                      ("registration_sha256", compat_path),
                      ("source_freeze_sha256",
                       run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json"),
                      ("audit_source_sha256", ROOT / "tools/policy_search/audit.py")):
        if audit.get(key) != _sha(path):
            raise ValueError(role + " " + key + " differs from AUDIT")
    actual_analysis = analyze_audited_batch(
        plan_path, summary_path, audit_path,
        run / "design/PREFERENCE_V2_SOURCE_FREEZE.json",
        run / "design/SEARCH_ALGORITHM_SOURCE_FREEZE.json")
    if _read(analysis_path) != actual_analysis:
        raise ValueError(role + " v2 analysis differs from AUDIT")
    return plan, stage, actual_analysis, {"plan_sha256": _sha(plan_path),
                                         "summary_sha256": _sha(summary_path),
                                         "audit_sha256": _sha(audit_path),
                                         "analysis_v2_sha256": _sha(analysis_path),
                                         "stage_registration_sha256": _sha(stage_path)}


def _load_train_nn(run, model_dir, fixed_feedback_spec, forbidden_roots):
    manifest = _read(run / "new_data/global_manifest.json")
    folder, metadata, provenance = _verified_neural_model(
        run, model_dir, fixed_feedback_spec, manifest)
    if set(provenance["train_root_family_ids"]) & set(forbidden_roots):
        raise ValueError("Ungated NN train source overlaps later stages")
    return folder, metadata


def _register_later_stage(run, role, selected, specs, extra):
    from tools.policy_search.closed_loop import register_batch

    folder = run / role
    if folder.exists():
        raise FileExistsError(role + " registration requires a fresh directory")
    plan_path = folder / "registered_plan.json"
    plan = register_batch(
        run / "new_data/global_manifest.json", plan_path,
        scenario_ids=[s["id"] for s in selected], policy_specs=specs,
        preference_path=run / "design/RESEARCH_PREFERENCE_v1.json", phase=role)
    compat = {"schema": "policy_search.offline_generation.v1",
              "generation": role + "_stage_audit_compat",
              "source_sha256": _sha(ROOT / "tools/policy_search/search.py"),
              "manifest_sha256": plan["manifest_sha256"],
              "preference_sha256": plan["preference_sha256"],
              "scenario_ids": plan["scenario_ids"],
              "policy_specs": plan["policy_specs"],
              "plan_signature": plan["signature"],
              "stage_source_sha256": _sha(__file__)}
    compat_path = _new(folder / "SEARCH_REGISTRATION.json", compat)
    _new(folder / "STAGE_REGISTRATION.json",
         {"schema": f"policy_search.{role}_stage_registration.v1",
          "stage_source_sha256": _sha(__file__),
          "neural_source_sha256": _sha(ROOT / "tools/policy_search/neural.py"),
          "plan_sha256": _sha(plan_path), "plan_signature": plan["signature"],
          "manifest_sha256": plan["manifest_sha256"],
          "preference_sha256": plan["preference_sha256"],
          "v2_revision_sha256": _sha(run / "design/PREFERENCE_V2_SOURCE_FREEZE.json"),
          "stages_source_freeze_sha256": _sha(run / "design/STAGES_SOURCE_FREEZE.json"),
          "scenario_ids": plan["scenario_ids"], "policy_specs": plan["policy_specs"],
          "audit_compat_registration_sha256": _sha(compat_path),
          "final_blind_opened": False, **extra})
    return plan


def register_calibration(run_dir, *, fixed_feedback_id, nn_ungated_dir):
    """Preregister six matched roots and a fixed NN confidence-gate grid."""
    run = Path(run_dir)
    if (run / "calibration").exists():
        raise FileExistsError("Calibration registration requires a fresh directory")
    selection, _, analysis, binding = _verify_stage_result(run, "selection")
    by_id = {s["policy_id"]: s for s in selection["policy_specs"]}
    fixed = by_id.get(fixed_feedback_id)
    if (fixed is None or fixed["kind"] != "feedback" or
            analysis["metrics"][fixed_feedback_id]["complete_paired_case_count"] < 1):
        raise ValueError("Fixed nonlearning candidate requires an audited selection paired case")
    manifest = _read(run / "new_data/global_manifest.json")
    from tools.policy_search.neural import _load_artifacts
    nn_teacher_id = _load_artifacts(nn_ungated_dir)[1].get("teacher_policy_id")
    teacher = by_id.get(nn_teacher_id)
    if teacher is None or teacher["kind"] != "feedback":
        raise ValueError("NN teacher must be separately present in audited selection")
    prior_roots = {s["root_family_id"] for s in manifest["scenarios"]
                   if s["experiment_role"] in ("train", "selection")}
    selected = choose_phase_scenes(manifest, role="calibration", roots_per_condition=1,
                                   forbidden_roots=prior_roots)
    later_roots = {s["root_family_id"] for s in manifest["scenarios"]
                   if s["experiment_role"] in ("selection", "calibration", "development")}
    model_dir, metadata = _load_train_nn(run, nn_ungated_dir, teacher,
                                         later_roots)
    intent = {"schema": "policy_search.predeclared_gate_grid.v1",
              "calibration_scenario_ids": [s["id"] for s in selected],
              "calibration_root_family_ids": sorted({s["root_family_id"] for s in selected}),
              "manifest_sha256": _sha(run / "new_data/global_manifest.json"),
              "selection_audit_sha256": binding["audit_sha256"],
              "selection_analysis_v2_sha256": binding["analysis_v2_sha256"],
              "nn_ungated_metadata_sha256": _sha(model_dir / "metadata.json"),
              "thresholds": list(GATE_THRESHOLDS),
              "selection_note": "All thresholds fixed before calibration outcomes"}
    intent_sha = _digest(intent)
    from tools.policy_search.neural import create_gate_variant

    gate_specs = []
    for threshold in GATE_THRESHOLDS:
        code = round(threshold * 100)
        policy_id = f"nn_gate_t{code:03d}"
        gate_dir = run / "models" / f"calibration_gate_t{code:03d}"
        create_gate_variant(model_dir, gate_dir, threshold=threshold,
                            calibration_source={
                                "role": "calibration",
                                "root_family_ids": intent["calibration_root_family_ids"],
                                "source_sha256": intent_sha,
                                "predeclared_before_calibration_outcomes": True})
        gate_specs.append({"policy_id": policy_id, "kind": "neural_feedback",
                           "model_dir": str(gate_dir)})
    specs = [copy.deepcopy(by_id[pid]) for pid in
             ("secure_milp", "strict_guard", "current_rule")]
    specs.append(copy.deepcopy(fixed))
    if teacher["policy_id"] != fixed_feedback_id:
        specs.append(copy.deepcopy(teacher))
    specs.append({"policy_id": "nn_ungated", "kind": "neural_feedback",
                  "model_dir": str(model_dir)})
    specs += gate_specs
    plan = _register_later_stage(
        run, "calibration", selected, specs,
        {"fixed_nonlearning_candidate_policy_id": fixed_feedback_id,
         "fixed_candidate_on_selection_P0_frontier":
             fixed_feedback_id in analysis["p0_pareto_ids"],
         "fixed_candidate_selection_complete_paired_case_count":
             analysis["metrics"][fixed_feedback_id]["complete_paired_case_count"],
         "nn_teacher_policy_id": nn_teacher_id,
         "nn_teacher_selection_complete_paired_case_count":
             analysis["metrics"][nn_teacher_id]["complete_paired_case_count"],
         "selection_audit_sha256": binding["audit_sha256"],
         "selection_analysis_v2_sha256": binding["analysis_v2_sha256"],
         "nn_ungated_metadata_sha256": _sha(model_dir / "metadata.json"),
         "nn_ungated_source_audit_sha256": metadata["source_audit_sha256"],
         "gate_grid_intent": intent,
         "gate_grid_intent_sha256": intent_sha,
         "predeclared_gate_thresholds": list(GATE_THRESHOLDS),
         "gate_grid_policy_ids": [s["policy_id"] for s in gate_specs],
         "selection_only_for_candidate_choice": True,
         "calibration_outcomes_used_for_candidate_choice": False})
    if plan["model_bindings"]["strict_guard"] != selection["model_bindings"]["strict_guard"]:
        raise ValueError("Frozen v4 strict_guard model changed between stages")
    return plan


def register_development(run_dir, *, finalist_ids):
    """Select calibrated gate Pareto members and freeze matched dev controls."""
    finalists = list(finalist_ids)
    if not 1 <= len(finalists) <= 3 or len(set(finalists)) != len(finalists):
        raise ValueError("One to three distinct named feedback finalists required")
    run = Path(run_dir)
    if (run / "development").exists():
        raise FileExistsError("Development registration requires a fresh directory")
    selection, _, selection_analysis, selection_binding = _verify_stage_result(run, "selection")
    calibration, calibration_stage, calibration_analysis, calibration_binding = _verify_stage_result(run, "calibration")
    fixed_id = calibration_stage["fixed_nonlearning_candidate_policy_id"]
    by_id = {s["policy_id"]: s for s in selection["policy_specs"]}
    if fixed_id not in finalists:
        raise ValueError("Fixed nonlearning candidate must remain in development finalists")
    for pid in finalists:
        if (pid not in by_id or by_id[pid]["kind"] != "feedback" or
                selection_analysis["metrics"][pid]["complete_paired_case_count"] < 1):
            raise ValueError("Finalist lacks an audited selection paired case: " + pid)
    calibration_specs = {s["policy_id"]: s for s in calibration["policy_specs"]}
    teacher_id = calibration_stage["nn_teacher_policy_id"]
    if (fixed_id not in calibration_specs or
            calibration_specs[fixed_id] != by_id[fixed_id] or
            calibration["model_bindings"]["strict_guard"] !=
            selection["model_bindings"]["strict_guard"]):
        raise ValueError("Calibration fixed candidate or strict_guard differs from selection")
    if (teacher_id not in by_id or by_id[teacher_id]["kind"] != "feedback" or
            calibration_specs.get(teacher_id) != by_id[teacher_id]):
        raise ValueError("Calibration NN teacher differs from audited selection")
    model_dir = Path(calibration_specs["nn_ungated"]["model_dir"])
    from tools.policy_search.closed_loop import _model_binding
    if (_model_binding(calibration_specs["nn_ungated"]) !=
            calibration["model_bindings"]["nn_ungated"]):
        raise ValueError("Ungated NN bytes changed after calibration registration")
    base_metadata = _read(model_dir / "metadata.json")
    manifest = _read(run / "new_data/global_manifest.json")
    scenes = {s["id"]: s for s in manifest["scenarios"]}
    calibration_roots = {scenes[sid]["root_family_id"] for sid in calibration["scenario_ids"]}
    gate_ids = calibration_stage["gate_grid_policy_ids"]
    if (gate_ids != [f"nn_gate_t{round(t * 100):03d}" for t in GATE_THRESHOLDS] or
            calibration_stage["predeclared_gate_thresholds"] != list(GATE_THRESHOLDS) or
            calibration_stage["gate_grid_intent_sha256"] !=
            _digest(calibration_stage["gate_grid_intent"])):
        raise ValueError("Calibrated gate grid differs from preregistered threshold intent")
    gate_specs = []
    from tools.policy_search.neural import _load_artifacts
    for pid, threshold in zip(gate_ids, GATE_THRESHOLDS):
        spec = calibration_specs[pid]
        gate_path, gate_meta = _load_artifacts(spec["model_dir"])
        source = gate_meta.get("calibration_source") or {}
        roots = set(source.get("root_family_ids") or [])
        if (gate_meta.get("gate_mode") != "confidence" or
                gate_meta.get("gate_threshold") != threshold or
                gate_meta.get("trained_weight_sha256") !=
                base_metadata.get("trained_weight_sha256") or
                gate_meta.get("ungated_model_metadata_sha256") !=
                _sha(model_dir / "metadata.json") or
                source.get("role") != "calibration" or
                source.get("source_sha256") != calibration_stage["gate_grid_intent_sha256"] or
                source.get("predeclared_before_calibration_outcomes") is not True or
                roots != calibration_roots or
                _model_binding(spec) != calibration["model_bindings"][pid]):
            raise ValueError("gate_calibration_source is not the audited calibration roots")
        gate_specs.append(copy.deepcopy(spec))
    from tools.policy_search.preference_v2 import p0_dominates_v2
    gate_metrics = {pid: calibration_analysis["metrics"][pid] for pid in gate_ids}
    if any(m["complete_paired_case_count"] < 1 for m in gate_metrics.values()):
        raise ValueError("No complete paired calibration support for gate selection")
    selected_gate_ids = [pid for pid in gate_ids if not any(
        p0_dominates_v2(gate_metrics[other], gate_metrics[pid])
        for other in gate_ids if other != pid)]
    prior_roots = {s["root_family_id"] for s in manifest["scenarios"]
                   if s["experiment_role"] in ("train", "selection", "calibration")}
    anchor_scenes = choose_phase_scenes(
        manifest, role="development", roots_per_condition=2,
        forbidden_roots=prior_roots)
    selected = expand_development_pressure_cross(manifest, anchor_scenes)
    specs = [copy.deepcopy(by_id[pid]) for pid in
             ("secure_milp", "strict_guard", "current_rule")]
    specs += [copy.deepcopy(by_id[pid]) for pid in finalists]
    if teacher_id not in finalists:
        specs.append(copy.deepcopy(by_id[teacher_id]))
    specs.append({"policy_id": "nn_ungated", "kind": "neural_feedback",
                  "model_dir": str(model_dir)})
    specs += [s for s in gate_specs if s["policy_id"] in selected_gate_ids]
    return _register_later_stage(
        run, "development", selected, specs,
        {"fixed_nonlearning_candidate_policy_id": fixed_id,
         "finalist_policy_ids": finalists,
         "finalist_on_selection_P0_frontier":
             {pid: pid in selection_analysis["p0_pareto_ids"] for pid in finalists},
         "finalist_selection_complete_paired_case_count":
             {pid: selection_analysis["metrics"][pid]["complete_paired_case_count"]
              for pid in finalists},
         "selection_audit_sha256": selection_binding["audit_sha256"],
         "selection_analysis_v2_sha256": selection_binding["analysis_v2_sha256"],
         "calibration_audit_sha256": calibration_binding["audit_sha256"],
         "calibration_analysis_v2_sha256": calibration_binding["analysis_v2_sha256"],
         "nn_ungated_metadata_sha256": _sha(model_dir / "metadata.json"),
         "nn_teacher_policy_id": teacher_id,
         "gate_selection_rule": "P0 Pareto among three preregistered calibration gates on identical audited paired support; incomparable cases retained",
         "gate_grid_policy_ids": gate_ids,
         "calibration_selected_gate_policy_ids": selected_gate_ids,
         "gate_calibration_complete_paired_case_ids":
             {pid: gate_metrics[pid]["complete_paired_case_ids"] for pid in gate_ids},
         "pressure_sensitivity_indices": list(range(5)),
         "sensitivity_root_family_ids": [s["root_family_id"] for s in anchor_scenes],
         "same_root_cross_pressure_identity_fields":
             ["condition_id", "time_block_id", "exogenous_stream_id",
              "initialization_seed", "base_state_id"],
         "same_policy_set_at_every_registered_pressure": True,
         "gate_model_metadata_sha256": {s["policy_id"]:
                                        _sha(Path(s["model_dir"]) / "metadata.json")
                                        for s in gate_specs if s["policy_id"] in selected_gate_ids},
         "development_outcomes_used_for_selection": False,
         "all_policies_matched_on_same_development_cases": True})
