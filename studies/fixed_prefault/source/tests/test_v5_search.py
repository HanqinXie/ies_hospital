"""The searched vector is shared across roots, selected before outcomes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_pilot_scenes_cover_six_conditions_without_duplicate_root():
    from tools.policy_search.search import choose_scenes

    scenarios = []
    for condition in "abcdef":
        for root in (1, 2):
            for pressure in (0, 3):
                scenarios.append({"id": f"{condition}{root}{pressure}",
                                  "condition_id": condition, "root_family_id": f"{condition}{root}",
                                  "pressure_point_index": pressure, "experiment_role": "train",
                                  "time_block_id": f"{condition}{root}"})
    selected = choose_scenes({"scenarios": scenarios}, role="train", roots_per_condition=1,
                             pressure_index=3)
    assert len(selected) == 6
    assert len({s["condition_id"] for s in selected}) == 6
    assert all(s["pressure_point_index"] == 3 for s in selected)
    assert len({s["root_family_id"] for s in selected}) == 6


def test_initial_search_includes_both_feedback_families_and_secure():
    from tools.policy_search.search import initial_policy_specs

    specs = initial_policy_specs()
    assert specs[0] == {"policy_id": "secure_milp", "kind": "secure"}
    assert {s.get("family") for s in specs[1:]} == {"blend", "mode"}
    assert len({s["policy_id"] for s in specs}) == len(specs)
    assert 5 < len(specs) <= 16
