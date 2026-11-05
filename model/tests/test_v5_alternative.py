"""Response timing and port isolation as a distinct feedback search route."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_alternative_modes_isolate_when_feedback_is_active():
    from tools.policy_search.alternative import alternative_specs

    specs = {s["policy_id"]: s for s in alternative_specs()}
    assert specs["secure_milp"] == {"policy_id": "secure_milp", "kind": "secure"}
    prep = specs["prepare_only"]["theta"]
    recovery = specs["recovery_only"]["theta"]
    grid = specs["damaged_grid_only"]["theta"]
    assert prep[0] > 0 and all(v == 0 for v in prep[4:])
    assert recovery[8] > 0 and all(v == 0 for v in recovery[:8])
    assert grid[4] > 0 and grid[6] > 0 and grid[7] == 0
    assert all(v == 0 for v in grid[:4] + grid[8:])
    assert len({s["policy_id"] for s in specs.values()}) == len(specs)
