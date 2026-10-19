"""V18 registration and artifact storage stay separate from old results."""
import gzip
import importlib
import json
from pathlib import Path

import pytest


def api():
    try:
        return importlib.import_module('tools.execution_recovery.runner')
    except ModuleNotFoundError:
        pytest.fail('Missing V18 bounded runner')


def test_new_runner_has_own_output_and_keeps_baseline_preferences():
    from tools.essential_fixes.runner import policy_specs
    m = api()
    assert m.RUN.name == 'v18_execution_recovery_20261018_run01'
    assert m.policy_specs()['E'] == policy_specs()['E']
    assert m.policy_specs()['K1'] == policy_specs()['K1']
    assert m.policy_specs()['C']['policy_id'].startswith('C_execution_recovery_v18')


def test_registration_path_cannot_target_old_outputs_or_parent(tmp_path, monkeypatch):
    m = api()
    # The host default pytest temp is C:, which is deliberately rejected by
    # this research output gate. Path-only registration checks use D:.
    tmp_path = m.ROOT / 'outputs' / 'v18_path_only_test'
    monkeypatch.setattr(m, 'RUN', tmp_path / 'new')
    for path in [tmp_path / 'old' / 'experiments' / 'x', tmp_path / 'new' / 'experiments']:
        with pytest.raises(ValueError):
            m._experiment_path(path)
    assert m._experiment_path(tmp_path / 'new' / 'experiments' / 'x') == (tmp_path / 'new' / 'experiments' / 'x').resolve()


def test_streamed_raw_roundtrip_and_existing_evidence_is_immutable(tmp_path):
    m = api()
    payload = {'segments':[{'actual':0.0, 'observed':[-2e-10, 3.2], 'message':'许可'}], 'error':None}
    path = tmp_path / 'raw.json.gz'
    digest = m.save_raw(path, payload)
    assert digest == m.sha(path)
    with gzip.open(path, 'rt', encoding='utf8') as f:
        assert json.load(f) == payload
    with pytest.raises(FileExistsError):
        m.save_raw(path, {'overwrite':'forbidden'})
    with gzip.open(path, 'rt', encoding='utf8') as f:
        assert json.load(f) == payload


def test_seen_panel_preserves_original_exogenous_and_reset_fields():
    m = api()
    from tools.execution_recovery import data_design as d
    source = json.loads((m.ROOT / 'outputs/v17_state_coordinated_20261018_run01/design/MAIN_PROPOSAL.json').read_text(encoding='utf8'))
    original = source['scenarios'][1]
    panel = d.panel('identity_regression', [original])
    scene = panel['scenarios'][0]
    assert {k:scene[k] for k in original} == original
    assert panel['old80_role'] == 'seen_regression'
    assert panel['historical_unseen_claim'] is False
    assert panel['signature'] == d.digest({k:v for k,v in panel.items() if k!='signature'})
