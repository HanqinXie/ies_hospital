"""Selection mode-v2 audit adapter must disclose source and sidecar binding."""
from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

RUN = Path(__file__).resolve().parents[1] / "outputs/v5_policy_search_20261015_run01"


def test_selection_audit_adapter_is_explicit_and_source_bound():
    from tools.policy_search.mode_v2_selection_review import (build_compat_registration,
                                                               verify_compat_registration)

    value = build_compat_registration(RUN)
    assert value["schema"] == "policy_search.offline_generation.v1"
    assert value["audit_compatibility_only"] is True
    assert value["comparison_phase"] == "selection"
    assert value["registered_pair_count"] == 30
    assert value["scenario_ids"] == value["main_selection_scenario_ids"]
    assert value["actual_registration_source"] == "tools/policy_search/mode_v2_selection.py"
    verify_compat_registration(RUN, value)
    tampered = copy.deepcopy(value)
    tampered["actual_mode_v2_registration_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="signature|sidecar"):
        verify_compat_registration(RUN, tampered)
