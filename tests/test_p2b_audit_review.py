"""Independent review counterexamples. Synthetic only; no environment created."""
import copy
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.p2b_experiment import audit as module
from tools.p2b_experiment.runner import save_payload
from hospital_ies.data_contract import first_failure_label


def segment(a, b, failed=False):
    return dict(start_minute=a, end_minute=b,
        unserved_kW=np.zeros((4, 3)).tolist(), cost_USD_step=b-a,
        classification={'p0_guarantee_failure': failed},
        energy_balance_residuals=[0, 0, 0], steam_balance_residual_lbph=0,
        temperature=dict.fromkeys(('P0', 'P1', 'P2', 'P3'), 23.),
        violation=dict.fromkeys(('P0', 'P1', 'P2', 'P3'), False))


def test_thermal_different_pair_is_not_same_other():
    a = module.score_payload(dict(summary={'status': 'completed'}, segments=[segment(0, 480)]))
    b = copy.deepcopy(a)
    b['P1_max_temperature_C'] += 2.
    assert module.pair_category(a, b) == 'both_no_P0_other_different'


def test_wrong_label_reason_rejected():
    label = first_failure_label(0, 60, None, 15, 'solver_error')
    label['unknown_reason'] = 'administrative_end'
    label['administrative_censoring'] = True
    label['model_error_truncated'] = False
    payload = dict(summary={'status': 'solver_error'}, segments=[segment(0, 15)],
        decisions=[dict(decision_time_min=0, feature_metadata={}, known_command_queue=[], labels={'60': label})])
    with pytest.raises(AssertionError):
        module.check_payload(payload)


def test_summary_cannot_hide_observed_failure():
    payload = dict(summary={'status': 'completed', 'physical_service_failure': False},
                   segments=[segment(0, 480, failed=True)], decisions=[])
    with pytest.raises(AssertionError):
        module.check_payload(payload)


def test_started_without_checkpoint_is_explicit_unknown_or_rejected(tmp_path, monkeypatch):
    # Bypass real dependency-file verification only. The ordinary audit journal,
    # checkpoint reader, scorer, pair assembly and reporter all execute.
    monkeypatch.setattr(module, 'verify_manifest', lambda _: None)
    spec = dict(id='synthetic', event_class='L2', study_stratum='L2_mixed',
        experiment_role='pilot_train', distribution_id='p_study',
        root_family_id='root', time_block_id='block')
    manifest = dict(signature='synthetic_manifest', scenarios=[spec],
        splits={'synthetic': {'group_id': 'group'}}, policies=['priority_milp', 'secure_milp'],
        p2b_shards=[{'id': 'batch_00', 'scenario_ids': ['synthetic']}])
    path = tmp_path/'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    folder = tmp_path/'shards/batch_00'
    folder.mkdir(parents=True)
    records = [dict(event='started', scenario_id='synthetic', policy_id=policy,
                    manifest_signature='synthetic_manifest')
               for policy in ('priority_milp', 'secure_milp')]
    (folder/'attempts.jsonl').write_text('\n'.join(json.dumps(r) for r in records)+'\n', encoding='utf-8')
    payload = dict(scenario=spec, seed=20261012, segments=[segment(0, 480)], decisions=[],
        summary={'status': 'completed', 'physical_service_failure': False,
                 'full_trajectory_outcome_valid': True})
    save_payload(folder, 'synthetic', 'priority_milp', 'synthetic_manifest', payload)
    try:
        report = module.audit(path, 'synthetic_review')
    except (AssertionError, ValueError):
        return
    assert report['attempted_trajectories'] == 2
    assert report.get('missing_checkpoint_attempts', 0) == 1 or report['score_unknown'] == 1


def test_all_carrier_priority_units_remain_separate_and_tail_failure_is_retained():
    history = segment(0, 60)
    score = segment(60, 420)
    score['unserved_kW'] = np.arange(1, 13, dtype=float).reshape(4, 3).tolist()
    tail = segment(420, 450, failed=True)
    payload = dict(summary={'status': 'solver_error'}, segments=[history, score, tail])
    result = module.score_payload(payload)
    assert result['score_outcome_valid'] and result['score_P0_failure'] == 0
    assert result['first_failure_observed_minute'] == 450
    for gi, priority in enumerate(('P0', 'P1', 'P2', 'P3')):
        for ci, carrier in enumerate(('e', 'h', 'c')):
            assert result[f'{priority}_{carrier}_ENS_kWh'] == (gi*3+ci+1)*6


def test_history_failure_and_first_recovery_are_not_restarted_at_score():
    payload = dict(summary={'status': 'completed'}, segments=[
        segment(0, 15, failed=True), segment(15, 60), segment(60, 480)])
    result = module.score_payload(payload)
    assert result['score_outcome_valid'] and result['score_P0_failure'] == 0
    assert result['first_failure_observed_minute'] == 15
    assert result['recovery_observed_minute'] == 60
