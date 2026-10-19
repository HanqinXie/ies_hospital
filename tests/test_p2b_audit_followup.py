"""Second independent synthetic source review. No hospital simulation."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src')]
from tools.p2b_experiment import audit as module
from tools.p2b_experiment.runner import save_payload
from hospital_ies.data_contract import first_failure_label
from test_p2b_audit_review import segment


def test_partial_thermal_statistics_are_masked_and_margin_is_observed():
    p=dict(summary={'status':'solver_error'},segments=[segment(0,75)],
        decisions=[dict(decision_time_min=60,features={'zone_margin_ICU_FLR_2':1.25})])
    result=module.score_payload(p)
    assert result['P0_max_temperature_C'] is None
    assert result['observed_P0_max_temperature_C']==23
    assert result['P0_min_zone_margin_C'] is None
    assert result['observed_P0_min_zone_margin_C']==1.25


def test_pair_handles_unmodeled_P3_temperature_as_unavailable():
    a=module.score_payload(dict(summary={'status':'completed'},segments=[segment(0,480)]))
    a['P3_max_temperature_C']=None
    assert module.pair_category(a,dict(a))=='both_no_P0_same_other'
    b=dict(a);b['P0_max_temperature_C']=None
    with pytest.raises(ValueError):module.pair_category(a,b)


def checkpoint_fixture(tmp_path, monkeypatch, records):
    monkeypatch.setattr(module, 'verify_manifest', lambda _: None)
    spec = dict(id='synthetic', event_class='L2', study_stratum='L2_mixed',
        experiment_role='pilot_train', distribution_id='p_study',
        root_family_id='root', time_block_id='block')
    manifest = dict(signature='synthetic_manifest', scenarios=[spec],
        splits={'synthetic': {'group_id': 'group'}},
        policies=['priority_milp', 'secure_milp'],
        p2b_shards=[{'id': 'batch_00', 'scenario_ids': ['synthetic']}])
    path = tmp_path/'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    folder = tmp_path/'shards/batch_00'
    folder.mkdir(parents=True)
    (folder/'attempts.jsonl').write_text(
        ''.join(json.dumps(r)+'\n' for r in records), encoding='utf-8')
    payload = dict(scenario=spec, seed=20261012, segments=[segment(0, 480)],
        decisions=[], summary=dict(status='completed', physical_service_failure=False,
            full_trajectory_outcome_valid=True))
    save_payload(folder, 'synthetic', 'priority_milp', 'synthetic_manifest', payload)
    return path


def test_checkpoint_without_started_journal_rejected(tmp_path, monkeypatch):
    path = checkpoint_fixture(tmp_path, monkeypatch, [])
    with pytest.raises((AssertionError, ValueError)):
        module.audit(path, 'orphan_checkpoint_review')


def test_journal_manifest_signature_checked_by_audit(tmp_path, monkeypatch):
    records = [dict(event='started', scenario_id='synthetic',
        policy_id='priority_milp', manifest_signature='a_different_manifest')]
    path = checkpoint_fixture(tmp_path, monkeypatch, records)
    with pytest.raises((AssertionError, ValueError)):
        module.audit(path, 'journal_signature_review')


def test_observed_window_duration_and_endpoint_contract_checked():
    label = first_failure_label(0, 60, None, 15, 'solver_error')
    label.update(observed_window_minutes=60., window_left_open=False,
                 window_right_closed=False)
    payload = dict(summary={'status': 'solver_error'}, segments=[segment(0, 15)],
        decisions=[dict(decision_time_min=0, feature_metadata={},
            known_command_queue=[], labels={'60': label})])
    with pytest.raises(AssertionError):
        module.check_payload(payload)


def test_summary_cannot_claim_full_trajectory_after_only_15_minutes():
    payload = dict(summary=dict(status='completed', completed=True,
        physical_service_failure=False, p0_failed_any=0,
        observed_until_minute=480., requested_end_minute=480.,
        full_trajectory_outcome_valid=True),
        segments=[segment(0, 15)], decisions=[])
    with pytest.raises(AssertionError):
        module.check_payload(payload)
