"""v6 data registration must keep old and final annual support disjoint."""

import pytest

from tools.policy_redesign.data_design import choose_new_blocks, reject_overlaps


def test_new_blocks_are_condition_balanced_and_never_reuse_exposed_days():
    blocks = choose_new_blocks(
        available_days=list(range(100)),
        excluded_days={2, 4, 6},
        sealed_days={8, 10},
        seed=43,
    )
    assert len(blocks) == 12
    assert len({row["day_index"] for row in blocks}) == 12
    assert all(row["day_index"] not in {2, 4, 6, 8, 10} for row in blocks)
    for condition in {row["condition_id"] for row in blocks}:
        assert sorted(row["role"] for row in blocks if row["condition_id"] == condition) == [
            "development", "selection"
        ]


def test_full_day_intersection_is_rejected_including_embargo():
    exposures = [{"raw_start_minute": 1490, "raw_end_minute": 2000}]
    with pytest.raises(ValueError, match="overlap"):
        reject_overlaps([{"raw_start_minute": 1440, "raw_end_minute": 2880}], exposures)
