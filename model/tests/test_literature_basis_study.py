"""Literature checks reuse, rather than redefine, registered plant cases."""
import copy
import importlib

import pytest

from tools.annual_readiness.registration import validate_case
from tools.operating_rules_study.registration import load_registry


def build():
    return importlib.import_module('tools.literature_basis.study').build_study()


def test_eight_cases_are_canonical_and_source_inputs_remain_unchanged(monkeypatch):
    registry = load_registry()
    before = copy.deepcopy(registry)
    module = importlib.import_module('tools.literature_basis.study')
    monkeypatch.setattr(module, 'load_registry', lambda: registry)
    study = build()
    assert study['study_id'] == 'literature_basis_20261030'
    assert len(study['pairs']) == 4
    assert len(study['cases']) == 8
    assert len({c['id'] for c in study['cases']}) == 8
    assert registry == before
    for case in study['cases']:
        assert validate_case(case, registry) == case
        meta = case['annual_readiness']
        assert meta['method'] == 'M_ready'
        assert meta['parameters']['response_wait_minutes'] == 15.
        assert meta['parameters']['demand_amplitude'] == 0.
        assert meta['calendar_is_online_policy_input'] is False
        assert meta['physical_inputs_unchanged'] is True
        assert meta['source_support']['sealed_final_day_overlap'] is False


@pytest.mark.parametrize('name,port,duration,repair,configuration', [
    ('boiler_24h', 'BOILER_1', 1620., 1500., 'H0'),
    ('abs_48h', 'ABS', 3060., 2940., 'H2'),
    ('shared_tower', 'TOWER', 180., 120., 'H2'),
    ('shared_chw', 'AUX_CHW_PUMP', 180., 120., 'H0'),
])
def test_pairs_hold_hardware_and_preparation_and_allow_full_observation_tail(
        name, port, duration, repair, configuration):
    study = build()
    pair = next(p for p in study['pairs'] if p['name'] == name)
    cases = {c['id']: c for c in study['cases']}
    healthy, fault = cases[pair['healthy']], cases[pair['fault']]
    for c in (healthy, fault):
        assert c['configuration'] == configuration
        assert c['annual_readiness']['parameters']['duration_minutes'] == duration
    hp, fp = (copy.deepcopy(c['annual_readiness']['parameters']) for c in (healthy, fault))
    assert hp.pop('calendar') == []
    assert fp.pop('calendar') == [dict(time_minute=60., availability={port: 0.}),
        dict(time_minute=repair, availability={port: 1.})]
    assert hp == fp
    assert duration - repair >= 60.
    assert healthy['policy'] == fault['policy']
    assert healthy['operating_rule_id'] == fault['operating_rule_id']
    assert pair['annual_inventory_initialization'] is False
    assert pair['hospital_failure_probability_claim'] is False


def test_shared_cause_cases_do_not_add_constituent_machine_failures():
    study = build()
    by_id = {c['id']: c for c in study['cases']}
    for pair in study['pairs']:
        if pair['name'].startswith('shared_'):
            events = by_id[pair['fault']]['scenario']['model_closure_events']
            assert len(events) == 2
            assert all(len(e['availability']) == 1 for e in events)
            assert pair['probability_per_hour'] is None


def test_build_returns_fresh_cases():
    first = build()
    first['cases'][0]['configuration'] = 'INVALID'
    assert build()['cases'][0]['configuration'] == 'H0'
