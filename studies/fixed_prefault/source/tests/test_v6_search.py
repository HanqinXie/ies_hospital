"""State-machine tests for v6 feedback search; simulated scores are fixtures only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.control_improvement.ranking import TARGET_NAMES
from tools.policy_redesign import search
from tools.policy_search.preference import paired_metrics


def _row(scenario, policy, *, minutes=0.0, failure=0.0, electric=0.0,
         cost=0.0, complete=True):
    targets = [0.0] * len(TARGET_NAMES)
    targets[0], targets[1], targets[2], targets[-1] = failure, minutes, electric, cost
    score = {"targets": targets, "full_horizon": complete}
    return {"scenario_id": scenario, "policy_id": policy, "status": "complete",
            "terminal": True, "primary_score": score, "full_observation": score,
            "binding": {"passed": True, "initial_state_sha256": "same",
                        "exogenous_stream_id": "same"}}


def test_priority_key_keeps_unknown_and_p0_ahead_of_cost():
    rows = [_row("s0", "secure_milp"),
            _row("s0", "safe", cost=10000.0),
            _row("s0", "cheap_harm", failure=1, minutes=15, cost=-10000.0),
            _row("s0", "unknown", cost=-20000.0, complete=False)]
    metrics = {name: paired_metrics(rows, name) for name in
               ("safe", "cheap_harm", "unknown")}
    assert search.priority_key(metrics["safe"]) < search.priority_key(metrics["cheap_harm"])
    assert search.priority_key(metrics["cheap_harm"]) < search.priority_key(metrics["unknown"])

    rows += [_row("s1", "secure_milp", failure=1, minutes=15),
             _row("s1", "safe", failure=1, minutes=15),
             _row("s1", "cheap_harm", failure=0, minutes=0),
             _row("s1", "unknown", failure=1, minutes=15, complete=False)]
    # The registered new-minus-resolved event count is used before cost, with
    # added events retained as a separate tie-breaker and in the raw metrics.
    assert search.priority_key(paired_metrics(rows, "cheap_harm"))[1:3] == (0, 1)


def test_population_uses_real_parent_and_can_continue_to_generation_four():
    archive = [
        {"policy_id": "seed7_g1_c00", "family": "port_residual",
         "theta": [0.1] * 6, "priority_key": [0, 1, 1, 10]},
        {"policy_id": "seed7_g1_c01", "family": "port_residual",
         "theta": [-0.2] * 6, "priority_key": [0, 0, 0, -5]},
        {"policy_id": "seed7_g2_c00", "family": "source_allocation",
         "theta": [0.3] * 7, "priority_key": [0, 0, 0, -2]},
    ]
    first = search.propose_population(seed=7, generation=4, population_size=6,
                                      families=("port_residual", "source_allocation"),
                                      archive=archive, mutation_scale=0.3)
    second = search.propose_population(seed=7, generation=4, population_size=6,
                                       families=("port_residual", "source_allocation"),
                                       archive=archive, mutation_scale=0.3)
    assert first == second
    assert len(first["policy_specs"]) == 6
    assert {x["parent_policy_id"] for x in first["lineage"] if x["family"] == "port_residual"} == {"seed7_g1_c01"}
    assert all(-1 <= x <= 1 for p in first["policy_specs"] for x in p["theta"])
    assert all(p["kind"] == "v6_feedback" for p in first["policy_specs"])


def test_first_generation_sparsely_covers_signed_physical_directions_across_seeds():
    populations = [search.propose_population(
        seed=seed, seed_index=index, generation=1, population_size=8,
        families=("port_residual", "source_allocation"))
        for index, seed in enumerate((11, 23, 37))]
    all_specs = [spec for proposal in populations for spec in proposal["policy_specs"]]
    for family, coordinates in (("port_residual", {"GT": (0, 1, 2), "grid": (3,),
                                                   "diesel": (4,), "ABS": (5,)}),
                                ("source_allocation", {"GT": (0,), "grid": (4,),
                                                       "diesel": (5,), "ABS": (6,)})):
        specs = [spec for spec in all_specs if spec["family"] == family]
        for indices in coordinates.values():
            effects = [sum(spec["theta"][j] for j in indices) for spec in specs]
            assert max(effects) >= 0.75
            assert min(effects) <= -0.75
        assert all(1 <= sum(abs(x) > 1e-9 for x in spec["theta"]) <= 3
                   for spec in specs)


def test_search_rejects_two_generation_design(tmp_path):
    manifest = tmp_path / "manifest.json"
    preference = tmp_path / "preference.json"
    manifest.write_text("{}", encoding="utf-8")
    preference.write_text('{"schema":"policy_redesign.v6.preregistered_preference.v1"}',
                          encoding="utf-8")
    with pytest.raises(ValueError, match="three generations"):
        search.register_search(manifest, tmp_path / "search", scenario_ids=["train_a"],
                               preference_path=preference, seeds=(1, 2, 3),
                               max_generations=2)


def test_search_requires_enough_initial_candidates_for_signed_direction_coverage(tmp_path):
    manifest = tmp_path / "manifest.json"
    preference = tmp_path / "preference.json"
    manifest.write_text("{}", encoding="utf-8")
    preference.write_text('{"schema":"policy_redesign.v6.preregistered_preference.v1"}',
                          encoding="utf-8")
    with pytest.raises(ValueError, match="signed directional coverage"):
        search.register_search(manifest, tmp_path / "search", scenario_ids=["train_a"],
                               preference_path=preference, seeds=(1, 2, 3),
                               families=("port_residual", "source_allocation"),
                               population_size=4)


def test_resume_and_three_adaptive_generations_on_common_panel(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    preference = tmp_path / "preference.json"
    manifest.write_text("{}", encoding="utf-8")
    preference.write_text('{"schema":"policy_redesign.v6.preregistered_preference.v1"}',
                          encoding="utf-8")
    root = tmp_path / "search"
    search.register_search(manifest, root, scenario_ids=["train_a", "train_b"],
                           preference_path=preference, seeds=(3, 5, 7),
                           families=("port_residual",), population_size=3,
                           min_generations=3, max_generations=3,
                           max_actual_attempts=72)
    calls = []

    def register_fake(manifest_path, plan_path, *, scenario_ids, policy_specs,
                      preference_path, phase):
        assert list(scenario_ids) == ["train_a", "train_b"]
        assert phase == "train"
        calls.append(("register", str(plan_path)))
        plan = {"signature": "fixture", "scenario_ids": list(scenario_ids),
                "policy_specs": policy_specs}
        Path(plan_path).write_text(json.dumps(plan), encoding="utf-8")
        return plan

    def run_fake(plan_path, out_dir, *, max_new_pairs=None):
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        gen = int(Path(plan_path).parent.name.split("_")[-1])
        rows = []
        for sid in plan["scenario_ids"]:
            rows.append(_row(sid, "secure_milp"))
            for spec in plan["policy_specs"][1:]:
                # A genuine adaptive signal for the state-machine fixture:
                # the second candidate wins in generation 1, then the first.
                idx = int(spec["policy_id"].split("_c")[-1])
                rows.append(_row(sid, spec["policy_id"],
                                 minutes=-float((gen + 1) if (gen > 1 or idx == 1) else 1)))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        marker = Path(out_dir) / "attempts.fixture"
        marker.write_text("fixture", encoding="utf-8")
        calls.append(("run", str(plan_path)))
        return {"registered_pair_count": len(rows), "terminal_pair_count": len(rows),
                "recorded_pair_count": len(rows), "attempted_this_call": len(rows),
                "matched_initial_state_and_stream": True, "results": rows}

    monkeypatch.setattr(search, "register_batch", register_fake)
    monkeypatch.setattr(search, "run_batch", run_fake)
    outcome = search.run_search(root)
    assert outcome["status"] == "complete"
    assert len([x for x in calls if x[0] == "register"]) == 9
    assert len([x for x in calls if x[0] == "run"]) == 9
    registered = list(root.glob("seed_*/generation_*/registered_plan.json"))
    assert len(registered) == 9
    assert len({tuple(json.loads(p.read_text(encoding="utf-8"))["scenario_ids"])
                for p in registered}) == 1
    again = search.run_search(root)
    assert again["status"] == "complete"
    assert len(calls) == 18  # No rerun or regenerated registration on resume.
    journal = [json.loads(line) for line in (root / "journal.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert sum(event["event"] == "evaluated" for event in journal) == 9
    proposal = json.loads((root / "seed_0003" / "generation_0002" / "proposals.json").read_text(
        encoding="utf-8"))
    assert proposal["lineage"][0]["parent_policy_id"] == "seed3_g1_c01"
