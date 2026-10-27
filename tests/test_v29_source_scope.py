"""Offline source scope: native rows are evidence, never controller inputs."""
from __future__ import annotations

import copy
import csv
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
QUALITY = Path('inputs/model_review_v29')
THERMAL = Path('inputs/model_review_v28/THERMAL_SERVICE_COMPONENTS_8760.csv')
RAW_SHA = '8a33b9c050b8adf81aea5649328757cc71163a2ff96ef3d1cf900471d1c97799'
SCREEN_SHA = '92a5346bb8954d3ba9cb6eb229b1c9ac8ab89b7b2c6cb3b308fbb554f0e6c2ba'


def _api():
    path = ROOT / 'tools/model_review_v29/source_scope.py'
    assert path.is_file(), 'V29 offline source-quality and window-scope API is absent'
    spec = importlib.util.spec_from_file_location('v29_source_scope_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario(hour=424, steps=1, minutes=30):
    return dict(start_hour=hour, duration_steps=steps, step_minutes=minutes,
                retained_unrelated_input={'events': ['do not mutate']})


@pytest.fixture
def isolated(tmp_path):
    destination = tmp_path / 'project'
    (destination / QUALITY).mkdir(parents=True)
    (destination / THERMAL).parent.mkdir(parents=True)
    for name in ('SOURCE_COOLING_UNMET_HOURS.csv', 'SOURCE_QUALITY_METADATA.json',
                 'ALL55_ZONES_NATIVE_UNMET_SUMMARY.csv'):
        shutil.copyfile(ROOT / QUALITY / name, destination / QUALITY / name)
    shutil.copyfile(ROOT / THERMAL, destination / THERMAL)
    return destination


def test_native_rows_screening_and_frozen_bytes_are_preserved():
    quality = _api().load_quality(ROOT)
    assert quality['source_mode'] == 'delivery_proxy'
    assert quality['zone_hour_record_count'] == 26
    assert quality['unique_hour_count'] == 25
    assert quality['screened_zone_count'] == 55
    assert quality['native_unmet_zone_hours'] == pytest.approx(9.5)
    assert quality['regions'] == ['Kitchen_Flr_5', 'Radiology_Flr_4']
    with (ROOT / QUALITY / 'SOURCE_COOLING_UNMET_HOURS.csv').open(newline='') as handle:
        original_rows = list(csv.DictReader(handle))
    assert quality['zone_hour_records'] == original_rows
    for name, expected in (('SOURCE_COOLING_UNMET_HOURS.csv', RAW_SHA),
                           ('ALL55_ZONES_NATIVE_UNMET_SUMMARY.csv', SCREEN_SHA)):
        assert hashlib.sha256((ROOT / QUALITY / name).read_bytes()).hexdigest() == expected
    assert quality['metadata']['units']['native_unmet_zone_hours'] == 'zone*h'
    assert quality['metadata']['source_version'] == 'V28'


def test_partial_hour_keeps_original_native_unmet_and_separates_plan_from_observation():
    scope = _api().window_scope(ROOT, _scenario())
    assert scope['time_support']['planned']['absolute_start_minute'] == 423 * 60
    assert scope['time_support']['planned']['absolute_end_minute'] == 423 * 60 + 30
    assert scope['time_support']['observed'] is None
    assert scope['source_quality']['observed'] is None
    planned = scope['source_quality']['planned']
    assert planned['overlap_hours'] == [424]
    assert planned['regions'] == ['Kitchen_Flr_5']
    assert planned['support_overlap_hours'] == 0.5
    assert float(planned['zone_hour_records'][0]['native_unmet_zone_hours']) == pytest.approx(2 / 3)
    assert planned['exact_unmet_within_window_zone_hours'] is None
    assert planned['source_native_unmet_zone_hours_in_intersecting_rows'] == pytest.approx(2 / 3)


def test_end_is_exclusive_and_actual_prefix_cannot_claim_future_overlap():
    api = _api()
    scope = api.window_scope(ROOT, _scenario(423, 2, 60), observed_until_minute=60)
    assert scope['source_quality']['planned']['overlap_hours'] == [424]
    assert scope['source_quality']['observed']['overlap_hours'] == []
    assert scope['time_support']['observed']['status'] == 'prefix'
    complete = api.window_scope(ROOT, _scenario(423, 2, 60), observed_until_minute=120)
    assert complete['source_quality']['observed']['overlap_hours'] == [424]
    assert complete['time_support']['observed']['status'] == 'complete'
    assert complete['source_quality']['observed']['exact_unmet_within_window_zone_hours'] == pytest.approx(2 / 3)


def test_two_zone_same_hour_is_one_support_hour():
    scope = _api().window_scope(ROOT, _scenario(2942, 1, 60), observed_until_minute=60)
    observed = scope['source_quality']['observed']
    assert observed['overlap_hours'] == [2942]
    assert len(observed['zone_hour_records']) == 2
    assert observed['regions'] == ['Kitchen_Flr_5', 'Radiology_Flr_4']
    assert observed['support_overlap_hours'] == 1.0
    assert observed['exact_unmet_within_window_zone_hours'] == pytest.approx(0.5)


@pytest.mark.parametrize('hour,duration', [(211, 360), (4001, 90)])
def test_declared_mechanism_windows_have_no_overlap_but_remain_delivery_proxy(hour, duration):
    scope = _api().window_scope(ROOT, _scenario(hour, duration // 5, 5),
                                observed_until_minute=duration)
    assert scope['source_quality']['observed']['overlap_hours'] == []
    assert scope['source_quality']['source_mode'] == 'delivery_proxy'
    assert scope['source_quality']['full_target_demand_qualified'] is False
    assert scope['source_quality']['humidity_hydraulics_cleanliness_qualified'] is False


def test_fractional_source_reference_energy_unknowns_and_no_double_charge():
    scope = _api().window_scope(ROOT, _scenario(424, 3, 30), observed_until_minute=45)
    with (ROOT / THERMAL).open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    laundry = 'local_laundry_source_reference_NaturalGas_input_kW'
    booster = 'local_booster_source_reference_electric_input_already_in_Dref_kW'
    preheat = 'booster_unmodelled_upstream_preheat_useful_kW'
    boundary = scope['energy_service_boundary']
    planned = boundary['planned_source_reference']
    observed = boundary['observed_source_reference']
    assert planned['laundry_source_reference_kWh'] == pytest.approx(float(rows[423][laundry]) + 0.5 * float(rows[424][laundry]))
    assert observed['laundry_source_reference_kWh'] == pytest.approx(0.75 * float(rows[423][laundry]))
    assert observed['booster_electric_already_in_direct_kWh'] == pytest.approx(0.75 * float(rows[423][booster]))
    assert observed['booster_upstream_preheat_useful_kWh'] == pytest.approx(0.75 * float(rows[423][preheat]))
    assert boundary['unknown_upstream_energy_kWh'] is None
    assert boundary['unknown_upstream_cost'] is None
    assert boundary['unknown_upstream_CO2_kg'] is None
    assert boundary['additional_booster_electric_charge_kWh'] == 0.0
    assert boundary['additional_charge_reason'] == 'already_in_direct_reference_no_duplication'
    assert boundary['source_reference_is_actual_IES_supply'] is False
    json.dumps(scope, allow_nan=False)


def test_unobserved_source_reference_stays_null_and_inputs_are_immutable():
    api = _api()
    scenario = _scenario(211, 72, 5)
    before_scenario = copy.deepcopy(scenario)
    paths = [ROOT / QUALITY / name for name in ('SOURCE_COOLING_UNMET_HOURS.csv',
             'SOURCE_QUALITY_METADATA.json', 'ALL55_ZONES_NATIVE_UNMET_SUMMARY.csv')] + [ROOT / THERMAL]
    before_files = {str(path): path.read_bytes() for path in paths}
    scope = api.window_scope(ROOT, scenario)
    assert scenario == before_scenario
    assert {str(path): path.read_bytes() for path in paths} == before_files
    assert scope['energy_service_boundary']['observed_source_reference'] is None


@pytest.mark.parametrize('field,value', [('start_hour', 0), ('start_hour', 1.5),
    ('duration_steps', -1), ('duration_steps', True), ('step_minutes', float('nan')),
    ('step_minutes', float('inf')), ('step_minutes', 0), ('start_hour', 8760)])
def test_invalid_or_out_of_support_windows_are_rejected(field, value):
    scenario = _scenario(424, 2, 60)
    scenario[field] = value
    with pytest.raises(ValueError):
        _api().window_scope(ROOT, scenario)


@pytest.mark.parametrize('observed', [-1, 31, float('nan'), float('inf'), True])
def test_invalid_observed_prefix_is_rejected(observed):
    with pytest.raises(ValueError):
        _api().window_scope(ROOT, _scenario(), observed_until_minute=observed)


def test_zero_observed_prefix_is_explicit_and_empty():
    scope = _api().window_scope(ROOT, _scenario(), observed_until_minute=0)
    assert scope['time_support']['observed']['duration_minutes'] == 0
    assert scope['source_quality']['observed']['overlap_hours'] == []
    assert scope['energy_service_boundary']['observed_source_reference']['laundry_source_reference_kWh'] == 0


@pytest.mark.parametrize('name', ['SOURCE_COOLING_UNMET_HOURS.csv',
    'ALL55_ZONES_NATIVE_UNMET_SUMMARY.csv', 'SOURCE_QUALITY_METADATA.json'])
def test_missing_quality_source_is_rejected(isolated, name):
    (isolated / QUALITY / name).unlink()
    with pytest.raises(ValueError):
        _api().load_quality(isolated)


@pytest.mark.parametrize('kind', ['raw_sha', 'raw_nan', 'screen_sha', 'units', 'version', 'metadata_nan', 'thermal_sha'])
def test_changed_nonfinite_or_unbound_source_is_rejected(isolated, kind):
    metadata_path = isolated / QUALITY / 'SOURCE_QUALITY_METADATA.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf8'))
    if kind.startswith('raw'):
        path = isolated / QUALITY / 'SOURCE_COOLING_UNMET_HOURS.csv'
        data = path.read_bytes()
        path.write_bytes(data + b'\n' if kind == 'raw_sha' else data.replace(b'0.16666666666666666', b'nan', 1))
    elif kind == 'screen_sha':
        with (isolated / QUALITY / 'ALL55_ZONES_NATIVE_UNMET_SUMMARY.csv').open('ab') as handle:
            handle.write(b'\n')
    elif kind == 'thermal_sha':
        with (isolated / THERMAL).open('ab') as handle:
            handle.write(b'\n')
    else:
        if kind == 'units':
            metadata['units']['native_unmet_zone_hours'] = 'hospital*h'
        elif kind == 'version':
            metadata['source_version'] = 'unregistered'
        else:
            metadata['unexpected_nonfinite'] = float('nan')
        metadata_path.write_text(json.dumps(metadata), encoding='utf8')
    with pytest.raises(ValueError):
        _api().window_scope(isolated, _scenario())
