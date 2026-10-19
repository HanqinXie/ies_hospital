"""Same-spec replay must retain recorded initial conditions and a closed S gate."""
import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_replay_gate_rejects_unresolved_inheritance(tmp_path):
    from tools.p2c_experiment.replay import validate_gate
    p = tmp_path / 'gate.json'
    p.write_text(json.dumps(dict(passed=True, attempted_original_failures=111,
        reproduced_original_failures=111, corrected_full_lex_success=110,
        unresolved_ids=['still_infeasible'], tolerance_unchanged=True,
        source_sha256={})), encoding='utf-8')
    with pytest.raises(ValueError, match='S gate'):
        validate_gate(p)


def test_replay_restores_recorded_initial_without_mutating_checkpoint():
    from tools.p2c_experiment.replay import RecordedInitialEnv, original_payload
    from hospital_ies.dataset_manifest import canonical_payload
    from hospital_ies.state_transition import clean
    m = json.loads((ROOT / 'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    s = m['scenarios'][0]
    old = original_payload(m, s['id'], 'priority_milp')
    initial = copy.deepcopy(old['initial_state'])
    env = RecordedInitialEnv(ROOT, execution_contract=m['execution_contract'],
        recorded_initial_by_id={s['id']:initial})
    env.reset(scenario=s, seed=m['options']['seed'])
    assert clean(dict(state=asdict(env.state), executed=env.core.executed)) == initial
    assert env.core.time == 0 and env.core.pending_commands == []
    assert env.history == [] and env.segment_history == []
    assert initial == old['initial_state']
    assert canonical_payload(env.replay_initialization_evidence['recorded']) == initial


def test_replay_manifest_preserves_all_specs_and_roles(tmp_path):
    from tools.p2c_experiment.replay import build_replay_manifest
    m = json.loads((ROOT / 'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    revised = build_replay_manifest(m)
    assert revised['signature'] != m['signature']
    for field in ('scenarios','policies','execution_contract','options','splits',
                  'p2b_time_blocks','p2b_shards','p2b_study_distribution'):
        assert revised[field] == m[field]
    assert revised['p2c_parent_signature'] == m['signature']
    assert revised['p2c_initial_state_policy'] == 'exact recorded P2B state and executed ports'


def test_replay_manifest_rejects_any_final_result_role():
    from tools.p2c_experiment.replay import build_replay_manifest
    m = json.loads((ROOT / 'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json').read_text(encoding='utf-8'))
    m['scenarios'][0]['experiment_role'] = 'final_blind'
    with pytest.raises(ValueError, match='role'):
        build_replay_manifest(m)


def test_freeze_binds_every_original_checkpoint_before_replay(tmp_path,monkeypatch):
    from tools.p2c_experiment.replay import freeze,file_hash,ref
    # This unit checks all original checkpoint bindings, not release approval.
    # A separate RED/GREEN guard requires the real global gate in production.
    monkeypatch.setattr('tools.p2c_experiment.replay.validate_release',lambda p: {})
    release = tmp_path/'unit_only_release.json'
    release.write_text('{}',encoding='utf-8')
    # The synthetic release is outside the review-package root. Its path
    # serialization is stubbed only here; real freeze rejects external refs.
    monkeypatch.setattr('tools.p2c_experiment.replay.ref',lambda p:
        dict(path=str(p),sha256=file_hash(p)) if Path(p)==release else ref(p))
    # The v3 local111 certificate was reopened by the real uniform replay.
    # This pure binding unit deliberately stubs both approval checks; the
    # actual certificate rejection is tested in test_p2c_gate_revocation.py.
    # All 400 recorded initial conditions and checkpoint assertions remain.
    monkeypatch.setattr('tools.p2c_experiment.replay.validate_gate',lambda p: {})
    gate = ROOT / 'outputs/v43_p2c_20261013_run01/solver_diagnostics/S_gate_v3.json'
    result = freeze(tmp_path/'fresh', gate,release)
    assert len(result['p2c_original_bindings']) == 400
    assert all(set(r['old_payload_sha256']) == {'priority_milp','secure_milp'}
               for r in result['p2c_original_bindings'])
    assert (tmp_path/'fresh/global_manifest.json').is_file()
