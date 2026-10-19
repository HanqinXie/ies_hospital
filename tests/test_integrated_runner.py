"""New scope changes budgets, never permits mixed versions or sealed data."""
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def specification(i, role='pilot_train'):
    return {'id': f'new_{i}', 'experiment_role': role, 'is_final_blind': False}


def manifest(n):
    specs = [specification(i) for i in range(n)]
    return {'scenarios': specs, 'policies': ['priority_milp', 'secure_milp'],
            'integrated_cohort': 'new_development',
            'p2b_shards': [{'id': 'only', 'scenario_ids': [s['id'] for s in specs]}],
            'splits': {s['id']: {'group_id': 'g', 'is_final_blind': False} for s in specs}}


def test_new_development_budget_is_attempted_specs_not_old_400():
    from tools.integrated_experiment.runner import validate_partition
    validate_partition(manifest(1200))
    with pytest.raises(ValueError, match='budget'):
        validate_partition(manifest(1201))


def test_original_replay_has_exactly_400_specs():
    from tools.integrated_experiment.runner import validate_partition
    m = manifest(399)
    m['integrated_cohort'] = 'original_replay'
    with pytest.raises(ValueError, match='400'):
        validate_partition(m)


def test_frozen_blind_role_is_not_a_new_development_permission():
    from tools.integrated_experiment.runner import validate_partition
    m = manifest(1)
    m['scenarios'][0]['is_final_blind'] = True
    with pytest.raises(ValueError, match='sealed'):
        validate_partition(m)


def test_same_connected_background_cannot_cross_roles():
    from tools.integrated_experiment.runner import validate_partition
    m = manifest(2)
    m['scenarios'][1]['experiment_role'] = 'pilot_validation'
    with pytest.raises(ValueError, match='group'):
        validate_partition(m)


def test_shard_duplicates_still_rejected():
    from tools.integrated_experiment.runner import validate_partition
    m = manifest(2)
    m['p2b_shards'][0]['scenario_ids'].append('new_0')
    with pytest.raises(ValueError, match='partition'):
        validate_partition(m)


def test_registered_proposal_bytes_cannot_drift(tmp_path):
    import hashlib
    from tools.integrated_experiment.runner import verify_registered_design
    p = tmp_path / 'proposal.json'
    p.write_text('{}', encoding='utf-8')
    ref = {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    p.write_text('{"altered": true}', encoding='utf-8')
    with pytest.raises(ValueError, match='evidence'):
        verify_registered_design({'integrated_cohort': 'new_development', 'integrated_proposal': ref})


def test_freeze_new_verifies_support_after_canonical_scenario_order(tmp_path, monkeypatch):
    """Actual freeze failure: input registration order differs from ID order."""
    import json
    from tools.integrated_experiment import runner, data_design
    proposal = data_design.build_proposal(ROOT, seed=20261014)
    assert proposal['fixed_scenarios'] != sorted(proposal['fixed_scenarios'], key=lambda s: s['id'])
    path = tmp_path / 'proposal.json'
    path.write_text(json.dumps(proposal), encoding='utf-8')
    gate = tmp_path / 'gate.json'
    gate.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(runner, 'validate_core_gate', lambda path: None)
    monkeypatch.setattr(runner, 'reference', lambda path: {'path': str(Path(path).resolve()), 'sha256': runner.sha(path)})
    # Leave the registered-script and support/role checks live. Core release
    # evidence has its separate raw-array and regression tests.
    monkeypatch.setattr(runner, 'verify_manifest', runner.verify_registered_design)
    result = runner.freeze_new(tmp_path / 'new', gate, path)
    assert [s['scenario_id'] for s in result['p2b_support']] == [s['id'] for s in result['scenarios']]
