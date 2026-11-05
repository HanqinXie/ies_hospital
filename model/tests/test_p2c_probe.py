"""Probe design cannot silently consume blind blocks or precede unified replay."""
import copy
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_fixed_probe_never_crosses_parent_or_background_roles():
    from tools.p2c_experiment.probe import assignments_for_specs
    p = json.loads((ROOT/'outputs/v43_p2c_20261013_run01/design/P2C_PROBE_PROPOSAL.json').read_text(encoding='utf-8'))
    assignments = assignments_for_specs(p['fixed_scenarios'])
    assert len(assignments) == 80
    groups = {}
    for sid, a in assignments.items():
        groups.setdefault(a['group_id'], set()).add(a['experiment_role'])
    assert len(groups) == 20 and all(len(x) == 1 for x in groups.values())
    bad = copy.deepcopy(p['fixed_scenarios'])
    bad[1]['experiment_role'] = 'pilot_validation'
    with pytest.raises(ValueError):
        assignments_for_specs(bad)


def test_probe_ready_gate_rejects_partial_replay_audit(tmp_path):
    from tools.p2c_experiment.probe import replay_ready
    p = tmp_path/'audit.json'
    p.write_text(json.dumps(dict(passed=True,complete_budget=False,attempted_trajectories=700,
        attempted_specifications=350,checkpoints=700,complete_pairs=350)),encoding='utf-8')
    with pytest.raises(ValueError,match='uniform replay'):
        replay_ready(p)


def test_probe_pressure_path_stays_in_original_joint_support():
    from tools.p2c_experiment.probe import pressure_values, supported_pressure
    for i in range(1001):
        assert supported_pressure(pressure_values(i/1000))
    with pytest.raises(ValueError):
        pressure_values(1.01)
