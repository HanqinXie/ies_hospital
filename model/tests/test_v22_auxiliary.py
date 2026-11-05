"""V22 ownership changes conserve power; equipment scaling changes real demand."""
from pathlib import Path
import hashlib
import importlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.sci_engineering.auxiliary import compute_auxiliary_allocation, sha256

GROUPS = ('P0', 'P1', 'P2', 'P3')
SOURCES = dict(fan='source_fans_total_kW', chw_pump='source_chw_pump_kW',
               heat_pump='source_heat_pump_kW')


def api():
    try:
        return importlib.import_module('tools.model_closure_v22.auxiliary')
    except ModuleNotFoundError as exc:
        if exc.name == 'tools.model_closure_v22.auxiliary':
            pytest.fail('V22 legal auxiliary allocation interface is missing')
        raise


@pytest.fixture(scope='module')
def baseline():
    return compute_auxiliary_allocation(ROOT, critical_share_scale=1.)


def owned(frame, kind):
    return frame[[f'{g}_{kind}_kW' for g in GROUPS]].to_numpy()


def test_default_replays_every_frozen_value_and_preserves_source_bindings(baseline):
    out = api().compute_allocation(ROOT)
    pd.testing.assert_frame_equal(out[baseline.columns], baseline, check_exact=True)
    assert np.array_equal(out.actual_power_delta_e_kW, np.zeros(8760))
    meta = out.attrs['metadata']
    assert meta['contract_id'] == baseline.attrs['metadata']['contract_id']
    assert meta['root_input_sha256'] == baseline.attrs['metadata']['root_input_sha256']
    assert meta['source_files'] == baseline.attrs['metadata']['source_files']
    assert meta['baseline_allocation_sha256'] == baseline.attrs['metadata']['allocation_sha256']
    assert meta['source_metadata_sha256'] == sha256(ROOT / 'inputs/sci_engineering/auxiliary_metadata.json')


@pytest.mark.parametrize('exponent', [.75, 1.25])
def test_pump_ownership_points_conserve_every_hour_and_keep_zero_support(exponent, baseline):
    out = api().compute_allocation(ROOT, pump_weight_exponent=exponent)
    for kind in ('chw_pump', 'heat_pump'):
        before, after = owned(baseline, kind), owned(out, kind)
        assert np.allclose(after.sum(axis=1), before.sum(axis=1), rtol=0, atol=1e-12)
        assert np.array_equal(after[before == 0.], np.zeros(np.count_nonzero(before == 0.)))
        assert (after >= 0.).all()
        shared = (before > 0.).sum(axis=1) > 1
        assert not np.array_equal(after[shared], before[shared])
        # Flattening ownership reduces the largest share; sharpening increases it.
        if exponent < 1.:
            assert (after[shared].max(axis=1) <= before[shared].max(axis=1) + 1e-12).all()
        else:
            assert (after[shared].max(axis=1) >= before[shared].max(axis=1) - 1e-12).all()
    assert np.array_equal(owned(out, 'fan'), owned(baseline, 'fan'))
    assert np.array_equal(out.fan_component_VAV_OR_kW, baseline.fan_component_VAV_OR_kW)
    assert np.array_equal(out.actual_power_delta_e_kW, np.zeros(8760))
    assert out.attrs['metadata']['actual_power_delta_annual_kWh'] == 0.


def test_explicit_shared_fan_ownership_changes_only_connected_priorities(baseline):
    out = api().compute_allocation(ROOT,
        fan_priority_shares={'VAV_ER': dict(P0=2/7, P1=3/7, P2=0., P3=2/7)})
    power = baseline.fan_component_VAV_ER_kW.to_numpy()
    assert np.allclose(out.P0_fan_kW - baseline.P0_fan_kW, power/7, atol=1e-12, rtol=0)
    assert np.allclose(out.P1_fan_kW - baseline.P1_fan_kW, -power/7, atol=1e-12, rtol=0)
    assert np.allclose(owned(out, 'fan').sum(axis=1), baseline.source_fans_total_kW, atol=1e-10, rtol=0)
    for group in ('P2', 'P3'):
        assert np.allclose(out[f'{group}_fan_kW'], baseline[f'{group}_fan_kW'], atol=1e-12, rtol=0)
    assert out.attrs['metadata']['fan_component_columns']['VAV_OR']['priority_shares']['P0'] == 1.
    assert np.array_equal(out.actual_power_delta_e_kW, np.zeros(8760))


def test_zero_fan_ownership_keeps_connection_evidence_and_nonnegative_power():
    out = api().compute_allocation(ROOT, fan_priority_shares={
        'VAV_1': {'P3': 1.}, 'VAV_2': {'P3': 1.},
        'VAV_ER': {'P3': 1.}, 'VAV_ICU': {'P0': 1.}})
    assert np.array_equal(out.P1_fan_kW, np.zeros(8760))
    assert (owned(out, 'fan') >= 0.).all()
    assert out.attrs['metadata']['fan_connected_priorities']['VAV_ER'] == ['P0', 'P1', 'P3']
    assert out.attrs['metadata']['fan_connected_priorities']['VAV_OR'] == ['P0']


@pytest.mark.parametrize('shares', [
    {'VAV_OR': dict(P0=1.25, P1=0., P2=0., P3=-.25)},
    {'VAV_OR': dict(P0=1.25, P1=0., P2=0., P3=0.)},
    {'VAV_OR': dict(P0=.9, P1=0., P2=0., P3=.1)},
    {'VAV_ER': dict(P0=.2, P1=.5, P2=.1, P3=.2)},
    {'UNKNOWN_LOOP': dict(P0=1.)},
    {'VAV_ER': dict(P0=float('nan'), P1=0., P2=0., P3=1.)},
])
def test_impossible_fan_ownership_is_rejected_before_simulation(shares):
    with pytest.raises(ValueError, match='fan|Fan|share|connected|loop'):
        api().compute_allocation(ROOT, fan_priority_shares=shares)


@pytest.mark.parametrize('scales', [dict(fan=1.1), dict(chw_pump=.75),
                                   dict(heat_pump=1.25), dict(fan=.9, chw_pump=1.2, heat_pump=1.3)])
def test_actual_equipment_scaling_changes_power_and_declares_exact_demand_delta(scales, baseline):
    out = api().compute_allocation(ROOT, actual_power_scale=scales)
    expected_delta = np.zeros(len(out))
    for kind, source in SOURCES.items():
        scale = scales.get(kind, 1.)
        assert np.allclose(owned(out, kind), owned(baseline, kind)*scale, rtol=0, atol=1e-12)
        assert np.allclose(out[source], baseline[source]*scale, rtol=0, atol=1e-12)
        assert np.allclose(owned(out, kind).sum(axis=1), out[source], rtol=0, atol=1e-10)
        expected_delta += baseline[source].to_numpy()*(scale-1.)
    assert np.allclose(out.auxiliary_added_e_kW, out.source_heat_pump_kW, rtol=0, atol=1e-12)
    assert np.allclose(out.actual_power_delta_e_kW, expected_delta, rtol=0, atol=1e-12)
    assert out.attrs['metadata']['actual_power_delta_annual_kWh'] == pytest.approx(expected_delta.sum(), abs=1e-8)
    for item in baseline.attrs['metadata']['fan_component_columns'].values():
        column = item['power_column']
        assert np.allclose(out[column], baseline[column]*scales.get('fan', 1.), rtol=0, atol=1e-12)


def test_all_zero_power_is_valid_and_does_not_corrupt_pump_ownership(baseline):
    out = api().compute_allocation(ROOT, pump_weight_exponent=.75,
        actual_power_scale=dict(fan=0., chw_pump=0., heat_pump=0.))
    for kind, source in SOURCES.items():
        assert not owned(out, kind).any()
        assert not out[source].any()
    assert not out.auxiliary_added_e_kW.any()
    assert np.allclose(out.actual_power_delta_e_kW,
        -baseline[list(SOURCES.values())].sum(axis=1), atol=1e-12, rtol=0)


@pytest.mark.parametrize('kwargs', [dict(pump_weight_exponent=0.), dict(pump_weight_exponent=-1.),
    dict(pump_weight_exponent=float('nan')), dict(actual_power_scale={'fan': -1.}),
    dict(actual_power_scale={'chw_pump': float('inf')}), dict(actual_power_scale={'tower': 1.})])
def test_invalid_transform_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        api().compute_allocation(ROOT, **kwargs)


def test_transform_is_pure_and_content_digest_binds_actual_returned_values(baseline, tmp_path):
    original_meta = baseline.attrs['metadata'].copy()
    out = api().compute_allocation(ROOT, pump_weight_exponent=.75, actual_power_scale={'chw_pump': 1.2})
    target = tmp_path / 'transformed.csv'
    out.to_csv(target, index=False, lineterminator='\n', encoding='utf-8')
    assert hashlib.sha256(target.read_bytes()).hexdigest() == out.attrs['metadata']['allocation_sha256']
    assert out.attrs['metadata']['allocation_sha256'] != original_meta['allocation_sha256']
    again = compute_auxiliary_allocation(ROOT, critical_share_scale=1.)
    pd.testing.assert_frame_equal(again, baseline, check_exact=True)
    assert again.attrs['metadata'] == original_meta
    assert out.attrs['metadata']['fan_annual_kWh'] == pytest.approx(out.source_fans_total_kW.sum())
    assert out.attrs['metadata']['chw_pump_annual_kWh'] == pytest.approx(out.source_chw_pump_kW.sum())
    assert out.attrs['metadata']['added_heat_pump_annual_kWh'] == pytest.approx(out.source_heat_pump_kW.sum())


def test_fan_fault_paths_are_independent_of_electricity_ownership_and_power_scaling(baseline):
    m = api()
    normal = m.compute_allocation(ROOT)
    changed = m.compute_allocation(ROOT, actual_power_scale={'fan': 1.25},
        fan_priority_shares={'VAV_ER': dict(P0=0., P1=0., P2=0., P3=1.)})
    for hour in (0, 4000, 6915):
        for group in GROUPS:
            expected = m.fan_path_availability(normal.iloc[hour], normal.attrs['metadata'],
                                              {'AUX_FAN_ER': 0.}, group)
            actual = m.fan_path_availability(changed.iloc[hour], changed.attrs['metadata'],
                                            {'AUX_FAN_ER': 0.}, group)
            assert actual == pytest.approx(expected, abs=1e-14)
    assert m.fan_path_availability(normal.iloc[0], normal.attrs['metadata'],
                                   {'AUX_FAN_ER': 0.}, 'P0') < 1.
    assert changed.attrs['metadata']['fan_fault_design_components']['VAV_ER']['priority_shares'] == \
        baseline.attrs['metadata']['fan_component_columns']['VAV_ER']['priority_shares']
