"""Source and opt-in contracts; no physical environment is imported."""
import importlib
import math

import pytest


def api():
    return importlib.import_module('tools.literature_basis.parameters')


def test_main_has_only_three_boiler_ports_and_absorption_port():
    value = api().profile('main')
    assert value['name'] == 'main'
    assert {a['port'] for a in value['assets']} == {'BOILER_1', 'BOILER_2', 'BOILER_3', 'ABS'}
    assert len(value['assets']) == 4
    for asset in value['assets']:
        expected = (0.0039 / 100, 24) if asset['port'].startswith('BOILER') else (0.0220 / 100, 48)
        assert asset['probability_per_hour'] == expected[0]
        assert asset['duration_hours'] == expected[1]
        assert asset['hospital_calibrated'] is False
    assert value['gt_proxy_opt_in'] is False


def test_all_twelve_source_entries_preserve_units_sha_and_source_chain():
    value = api().profile('main')
    entries = {row['parameter_id']: row for row in value['source_entries']}
    assert len(entries) == 12
    assert entries['gas_boiler']['raw_percent_per_hour'] == '0.0039'
    assert entries['absorption_chiller']['raw_percent_per_hour'] == '0.0220'
    assert entries['power_connection']['provenance_class'] == 'author_assumption'
    assert entries['emergency_generator']['provenance_class'] == 'author_proxy_assumption'
    assert entries['fuel_cell']['provenance_class'] == 'author_proxy_assumption'
    assert entries['photovoltaic']['complete_outages_only'] is True
    assert value['source']['pdf_sha256'] == 'afbcca5998bd5d32ee019cd0d4c7b913e07841c0169b71a0710815e80678d1ce'
    assert value['source']['table'] == 'Table 2'
    assert value['source']['page'] == 8
    assert value['source']['jiang_original_table11_independently_verified'] is False
    assert 'gas engine' in value['source']['jiang_equipment_limit']
    assert 'fuel cell' in value['source']['jiang_equipment_limit']
    assert all(e['duration_kind'] == 'fixed_failure_duration' for e in entries.values())
    assert all(e['repair_distribution'] is None for e in entries.values())


def test_unknown_probabilities_remain_none_and_are_not_source_zeroes():
    value = api().profile('main')
    assert value['unknown_probabilities'] == {
        'hospital_common_cause': None,
        'storage': None,
        'gas_connection': None,
        'other_unspecified_mechanisms': None,
    }
    assert value['hospital_reliability_estimate'] is False


def test_profiles_are_fresh_at_every_nested_level():
    first = api().profile('main')
    first['assets'][0]['probability_per_hour'] = 1
    first['source_entries'][0]['raw_percent_per_hour'] = 'changed'
    first['source']['pdf_sha256'] = 'changed'
    first['unknown_probabilities']['hospital_common_cause'] = 0
    fresh = api().profile('main')
    assert fresh['assets'][0]['probability_per_hour'] != 1
    assert fresh['source_entries'][0]['raw_percent_per_hour'] == '0.0114'
    assert fresh['source']['pdf_sha256'].startswith('afbcca')
    assert fresh['unknown_probabilities']['hospital_common_cause'] is None


def test_gt_requires_an_explicit_named_profile_and_permission():
    with pytest.raises(ValueError, match='explicit'):
        api().profile('gt_proxy')
    with pytest.warns(UserWarning, match='gas engine'):
        value = api().profile('gt_proxy', allow_gt_proxy=True)
    assert len(value['assets']) == 7
    gt = [a for a in value['assets'] if a['port'].startswith('GT_')]
    assert {a['port'] for a in gt} == {'GT_1', 'GT_2', 'GT_3'}
    assert all(a['probability_per_hour'] == 0.0335 / 100 and a['duration_hours'] == 34 for a in gt)
    assert all(a['evidence_group'] == 'incompatible_equipment_proxy' for a in gt)
    assert value['gt_proxy_opt_in'] is True
    assert 'gas engine' in value['warnings'][0]
    assert len(api().profile('main', allow_gt_proxy=True)['assets']) == 4


@pytest.mark.parametrize('name', ['', 'GT', 'MAIN', None, 2, [], True])
def test_unknown_profile_name_rejected(name):
    with pytest.raises(ValueError):
        api().profile(name)


@pytest.mark.parametrize('permission', [1, 0, 'yes', None, math.nan])
def test_proxy_permission_must_be_boolean(permission):
    with pytest.raises(ValueError):
        api().profile('main', allow_gt_proxy=permission)
