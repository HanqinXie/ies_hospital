import importlib
import copy
import pytest
from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def module():
    try:
        return importlib.import_module('tools.essential_fixes.environment')
    except ModuleNotFoundError as exc:
        pytest.fail('V16 model contract is missing: '+str(exc), pytrace=False)


def test_v16_uses_same_physical_limits_but_names_local_correction_and_shaft_budget():
    m = module()
    old, old_cfg = m.model_contract('M15_local_response', {})
    new, cfg = m.model_contract('M16_essential', {})
    assert new['grid_import_mode'] == old['grid_import_mode']
    assert new['diesel_dispatch_mode'] == old['diesel_dispatch_mode'] == 'arrived_ceiling'
    assert cfg['gt_dynamics'] == old_cfg['gt_dynamics']
    assert cfg['diesel_local_response'] == old_cfg['diesel_local_response']
    assert cfg['gt_floor_correction'] == {'enabled': True}
    assert new['name'] != old['name']


def test_legacy_models_still_resolve_to_unchanged_contracts():
    from tools.operating_response.environment import model_contract as old
    for model in ('M14_engineering', 'M15_local_response'):
        assert module().model_contract(model, {}) == old(model, {})


def test_correction_sensitivity_is_explicit_and_unknown_capabilities_are_rejected():
    m = module()
    config = {'gt_floor_correction': {'enabled': False}}
    original = copy.deepcopy(config)
    assert m.model_contract('M16_essential', config)[1]['gt_floor_correction']['enabled'] is False
    assert config == original
    with pytest.raises(ValueError):
        m.model_contract('M16_essential', {'gt_floor_correction': {'enabled': True, 'free_heat': True}})


@pytest.mark.parametrize('model,actual_contract', [
    ('M16_essential', 'hospital_ies.essential_engineering.v16.v1'),
    ('M15_local_response', 'hospital_ies.operating_response.v15.v1'),
    ('M14_engineering', 'hospital_ies.engineering_dynamics.v14.v1'),
    ('M0', 'p1b_no_export_interlock'),
])
def test_payload_names_actual_reference_contract_separately_from_v16_study(monkeypatch, model, actual_contract):
    m = module()
    study = 'hospital_ies.essential_engineering.v16.v1'
    # No physical episode: isolate the legacy metadata mismatch. The old
    # wrapper labels M14/M0 payloads with its v15 wrapper contract.
    monkeypatch.setattr(m.old, 'run_episode', lambda *a, **kw: {
        'execution_model_id': model,
        'engineering_model_contract_id': 'hospital_ies.operating_response.v15.v1'})
    payload = m.run_episode(SimpleNamespace(model_id=model), {}, None, 20261017,
                            {'study_contract_id': study})
    assert payload['execution_model_id'] == model
    assert payload['engineering_model_contract_id'] == actual_contract
    assert payload['study_contract_id'] == study
