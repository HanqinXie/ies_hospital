"""Preregister corrected mode feedback on independent selection roots."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "outputs/v5_policy_search_20261015_run01"


def test_design_is_exact_existing_selection_roots_and_five_matched_policies():
    from tools.policy_search.mode_v2_selection import build_selection_design

    design = build_selection_design(RUN)
    assert design["phase"] == "selection"
    assert len(design["scenario_ids"]) == 6
    assert len(set(design["root_family_ids"])) == 6
    assert design["scenario_ids"] == design["main_selection_scenario_ids"]
    assert [item["policy_id"] for item in design["policy_specs"]] == [
        "secure_milp", "prepare_only", "recovery_only",
        "prepare_only_mode_v2", "recovery_only_mode_v2"]
    assert set(design["root_family_ids"]).isdisjoint(design["train_mode_v2_root_family_ids"])


def test_selection_design_is_outcome_free_and_source_bound():
    from tools.policy_search.mode_v2_selection import (build_selection_design,
                                                        source_binding)

    design = build_selection_design(RUN)
    assert design["selection_outcomes_read"] is False
    assert design["final_blind_opened"] is False
    sources = source_binding()
    assert "tools/policy_search/mode_v2_selection.py" in sources
    assert "tools/policy_search/mode_v2.py" in sources
    assert "tools/policy_search/mode_v2_batch.py" in sources
    assert "configs/resilience_assumptions.json" in sources
