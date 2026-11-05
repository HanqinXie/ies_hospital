"""Read-only adjudication of set-order identity unknowns in frozen probe v2."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tools.reliability_first.soft_objective_probe_audit import (
    build_identity_order_audit,
    verify_initial_state_order_only,
)


RUN = Path(__file__).resolve().parents[1] / "outputs/v43_reliability_first_20261015_run01"


def test_only_documented_set_field_may_be_reordered():
    reference = {"state": {"extra_faulted": ["GT_2", "GT_3"], "grid_kW": 4.},
                 "executed": {"gt_electric_kW": [1., 0., 0.]}}
    actual = deepcopy(reference)
    actual["state"]["extra_faulted"].reverse()
    assert verify_initial_state_order_only(reference, actual) is True
    actual["executed"]["gt_electric_kW"][0] = 2.
    with pytest.raises(ValueError, match="non-set"):
        verify_initial_state_order_only(reference, actual)


@pytest.mark.parametrize("field", [["GT_2", "GT_2"], [""], [1, 2]])
def test_malformed_set_serialization_is_never_canonicalized(field):
    reference = {"state": {"extra_faulted": ["GT_2", "GT_3"]}, "executed": {}}
    actual = deepcopy(reference)
    actual["state"]["extra_faulted"] = field
    with pytest.raises(ValueError, match="extra_faulted"):
        verify_initial_state_order_only(reference, actual)


def test_real_unknowns_are_adjudicated_from_saved_raw_without_changing_v2():
    audit = build_identity_order_audit(RUN)
    assert audit["schema"] == "reliability_first.soft_probe_identity_order_audit.v1"
    assert audit["status"] == "complete"
    assert audit["official_v2_status_preserved"] == {"completed_snapshots": 22, "unknown_snapshots": 2}
    assert audit["affected_snapshot_indices"] == [139, 176]
    assert audit["verified_new_raw_count"] == 32
    assert audit["verified_secure_raw_count"] == 8
    assert audit["order_only_verified_pair_count"] == 32
    assert audit["unresolved_pair_count"] == 0
    assert audit["strict_opportunity_candidate_count"] == 0
    assert len(audit["raw_trajectory_hashes"]) == 40
