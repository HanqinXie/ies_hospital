"""Pure parameter regressions; no IES trajectory or solver is executed."""
import copy
from dataclasses import FrozenInstanceError
import importlib

import pytest

from tools.single_fault_fuel_20261102 import fuel

try:
    validation = importlib.import_module('hospital_ies.engineering_validation')
except ModuleNotFoundError:
    validation = None


@pytest.fixture
def cache_api():
    return validation


def config():
    return dict(iso_original_kW=800., iso_scaled_kW=400.,
        heat_rate_constant=29477., heat_rate_log_coefficient=1954.9,
        partload_multiplier=.9851, partload_exponent=-.327,
        source_pins=[['configs/plant_configs.json', 'a'*64]], contract=fuel.CONTRACT)


def count_construction(monkeypatch):
    calls = []
    original = fuel.FuelCurve.__post_init__
    def counted(self):
        calls.append(self.iso_scaled_kW)
        return original(self)
    monkeypatch.setattr(fuel.FuelCurve, '__post_init__', counted)
    return calls


def test_only_expensive_identity_is_reused_in_explicit_scope(cache_api, monkeypatch):
    assert cache_api is not None, 'Explicit frozen-run parameter cache is not implemented'
    calls = count_construction(monkeypatch)
    value = config()
    fuel.FuelCurve.from_config(value)
    fuel.FuelCurve.from_config(value)
    assert len(calls) == 2
    with cache_api.engineering_validation_scope(run_identity='source-input-case-profile-sha') as cache:
        first = fuel.FuelCurve.from_config(value)
        second = fuel.FuelCurve.from_config(copy.deepcopy(value))
        assert first == second
        assert len(calls) == 4  # Cheap construction validation stays fresh.
        with pytest.raises(FrozenInstanceError):
            second.iso_scaled_kW = 999.
        assert type(first.source_pins) is tuple
        assert all(type(pin) is tuple for pin in first.source_pins)
        assert first.identity == second.identity
        assert cache.stats()['namespaces']['fuel_curve.identity']['hits'] == 1


def test_config_mutation_and_failed_validation_never_reuse_old_success(cache_api, monkeypatch):
    assert cache_api is not None, 'Explicit frozen-run parameter cache is not implemented'
    calls = count_construction(monkeypatch)
    value = config()
    with cache_api.engineering_validation_scope(run_identity='frozen-run') as cache:
        original = fuel.FuelCurve.from_config(value)
        value['iso_scaled_kW'] = 401.
        assert fuel.FuelCurve.from_config(value).iso_scaled_kW == 401.
        value['source_pins'][0][1] = 'b'*64
        assert fuel.FuelCurve.from_config(value).source_pins != original.source_pins
        value['partload_exponent'] = -.3
        for _ in range(2):
            with pytest.raises(ValueError, match='frozen PNNL'):
                fuel.FuelCurve.from_config(value)
        assert len(calls) == 5
        assert cache.stats()['namespaces'] == {}


def test_identity_and_temperature_coefficients_are_exact_and_invalidated(cache_api, monkeypatch):
    assert cache_api is not None, 'Explicit frozen-run parameter cache is not implemented'
    curve = fuel.FuelCurve.from_config(config())
    expected_identity = curve.identity
    expected = {t: curve.coefficient(t) for t in (20., 21.)}
    digests = []
    logs = []
    original_digest = fuel.digest
    original_log = fuel.math.log
    def digest(value):
        digests.append(value)
        return original_digest(value)
    def log(value):
        logs.append(value)
        return original_log(value)
    monkeypatch.setattr(fuel, 'digest', digest)
    monkeypatch.setattr(fuel.math, 'log', log)
    with cache_api.engineering_validation_scope(run_identity='frozen-run') as cache:
        for _ in range(4):
            assert curve.identity == expected_identity
            assert curve.coefficient(20.) == expected[20.]
        assert curve.coefficient(21.) == expected[21.]
        assert len(digests) == 1
        assert len(logs) == 5  # Coefficient math is cheaper than its cache key.
        cache.invalidate('checkpoint_full_source_validation')
        assert curve.identity == expected_identity
        assert curve.coefficient(20.) == expected[20.]
        assert len(digests) == 2
        assert len(logs) == 6
        assert cache.stats()['generation'] == 1
        assert cache.stats()['invalidations'][-1]['reason'] == 'checkpoint_full_source_validation'


def test_forced_frozen_object_corruption_cannot_pollute_a_later_hit(cache_api):
    assert cache_api is not None, 'Explicit frozen-run parameter cache is not implemented'
    value = config()
    with cache_api.engineering_validation_scope(run_identity='frozen-run') as cache:
        curve = fuel.FuelCurve.from_config(value)
        original_identity = curve.identity
        object.__setattr__(curve, 'iso_scaled_kW', 999.)
        assert curve.identity != original_identity
        repaired = fuel.FuelCurve.from_config(value)
        assert repaired.iso_scaled_kW == 400.
        assert repaired.identity == original_identity
        assert cache.stats()['namespaces']['fuel_curve.identity']['hits'] == 1


def test_run_scope_exit_and_bounded_eviction_require_fresh_validation(cache_api, monkeypatch):
    assert cache_api is not None, 'Explicit frozen-run parameter cache is not implemented'
    calls = count_construction(monkeypatch)
    value = config()
    with cache_api.engineering_validation_scope(run_identity='run-a', max_entries=1) as cache:
        fuel.FuelCurve.from_config(value).identity
        changed = dict(value, iso_scaled_kW=401.)
        fuel.FuelCurve.from_config(changed).identity
        fuel.FuelCurve.from_config(value).identity
        assert cache.stats()['entries'] == 1
        assert cache.stats()['evictions'] == 2
    with cache_api.engineering_validation_scope(run_identity='run-b'):
        fuel.FuelCurve.from_config(value).identity
    assert len(calls) == 4


def test_actual_path_clock_power_and_temperature_are_never_memoized(cache_api):
    assert cache_api is not None, 'Explicit frozen-run parameter cache is not implemented'
    curve = fuel.FuelCurve.from_config(config())
    with cache_api.engineering_validation_scope(run_identity='frozen-run'):
        first = fuel.FuelPath.build(curve, role='actual_interval', start_minute=0.,
            end_minute=1., temperature_C=20., start_kW=[100.]*3, end_kW=[100.]*3)
        later = fuel.FuelPath.build(curve, role='actual_interval', start_minute=1.,
            end_minute=2., temperature_C=21., start_kW=[100.]*3, end_kW=[101.]*3)
        assert first.identity != later.identity
        assert first.integrals(curve) != later.integrals(curve)
        with pytest.raises(ValueError, match='Finite current source temperature'):
            curve.coefficient(float('nan'))


def test_failed_identity_is_not_stored_and_type_changes_invalidate(cache_api):
    assert cache_api is not None, 'Explicit frozen-run parameter cache is not implemented'
    value = config()
    with cache_api.engineering_validation_scope(run_identity='frozen-run') as cache:
        first = fuel.FuelCurve.from_config(value)
        original = first.identity
        changed = dict(value, iso_original_kW=800)
        # JSON identities distinguish int from float even when values compare equal.
        assert fuel.FuelCurve.from_config(changed).identity != original
        invalid = fuel.FuelCurve.from_config(dict(value, source_pins=[['x', float('nan')]]))
        for _ in range(2):
            with pytest.raises(ValueError):
                invalid.identity
        counts = cache.stats()['namespaces']['fuel_curve.identity']
        assert counts['failed_computations'] == 2
        assert counts['computations'] == 4
        assert cache.stats()['entries'] == 2
