"""Auxiliary ownership must preserve real electricity and reject lost evidence."""
from pathlib import Path
import importlib
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get('SCI_BASE_REPO', str(ROOT)))
sys.path[:0] = [str(ROOT), str(REPO / 'src'), str(REPO)]


def api():
    try:
        return importlib.import_module('tools.sci_engineering.auxiliary')
    except ModuleNotFoundError:
        pytest.fail('Auxiliary ownership implementation is missing')


def test_reassignment_preserves_existing_total_and_adds_only_heating_pump():
    groups = pd.DataFrame({'hour_of_year': [1], **{f'P{p}_e_kW': [v] for p, v in enumerate([10., 20., 30., 100.])}})
    allocation = pd.DataFrame({'hour_of_year': [1], 'auxiliary_added_e_kW': [4.]})
    for p in range(4):
        allocation[f'P{p}_fan_kW'] = [float(p + 1)]
        allocation[f'P{p}_chw_pump_kW'] = [.5]
        allocation[f'P{p}_heat_pump_kW'] = [1.]
    out = api().apply_auxiliary_allocation(groups, allocation)
    assert out['P0_e_kW'].iat[0] == pytest.approx(12.5)
    assert out['P1_e_kW'].iat[0] == pytest.approx(23.5)
    assert out['P2_e_kW'].iat[0] == pytest.approx(34.5)
    assert out['P3_e_kW'].iat[0] == pytest.approx(93.5)
    assert sum(out[f'P{p}_e_kW'].iat[0] for p in range(4)) == pytest.approx(164.)
    assert groups['P0_e_kW'].iat[0] == 10.


def test_reassignment_rejects_missing_hours_and_negative_p3():
    groups = pd.DataFrame({'hour_of_year': [1], **{f'P{p}_e_kW': [0.] for p in range(4)}})
    allocation = pd.DataFrame({'hour_of_year': [2], 'auxiliary_added_e_kW': [0.]})
    for p in range(4):
        for kind in ('fan', 'chw_pump', 'heat_pump'):
            allocation[f'P{p}_{kind}_kW'] = [1. if p == 0 and kind == 'fan' else 0.]
    with pytest.raises(ValueError, match='hour'):
        api().apply_auxiliary_allocation(groups, allocation)
    allocation['hour_of_year'] = [1]
    with pytest.raises(ValueError, match='P3'):
        api().apply_auxiliary_allocation(groups, allocation)


def test_persisted_allocation_requires_matching_metadata_and_hash(tmp_path):
    dest = tmp_path / 'inputs' / 'sci_engineering'
    dest.mkdir(parents=True)
    frame = pd.DataFrame({'hour_of_year': [1], 'auxiliary_added_e_kW': [0.]})
    for p in range(4):
        for kind in ('fan', 'chw_pump', 'heat_pump'):
            frame[f'P{p}_{kind}_kW'] = [0.]
    path = dest / 'auxiliary_allocation_8760.csv'
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match='metadata'):
        api().compute_auxiliary_allocation(tmp_path)
    (dest / 'auxiliary_metadata.json').write_text(json.dumps({'allocation_sha256': 'wrong'}))
    with pytest.raises(ValueError, match='hash'):
        api().compute_auxiliary_allocation(tmp_path)


def test_shared_fan_uses_component_design_flow_once_and_preserves_noncritical_share():
    allocation = api().allocate_fan_power(
        {'VAV_ER': np.array([14.])},
        {'VAV_ER': {'P0': 1., 'P1': 4., 'P2': 0., 'P3': 2.}},
        critical_share_scale=1.)
    assert allocation.tolist() == [[2., 8., 0., 4.]]
    lower = api().allocate_fan_power(
        {'VAV_ER': np.array([14.])},
        {'VAV_ER': {'P0': 1., 'P1': 4., 'P2': 0., 'P3': 2.}},
        critical_share_scale=.75)
    assert lower.tolist() == [[1.5, 6., 0., 6.5]]
    assert np.sum(lower) == 14.


def test_rejects_shared_fan_sensitivity_that_invents_power():
    with pytest.raises(ValueError, match='share'):
        api().allocate_fan_power({'VAV_OR': np.array([14.])},
            {'VAV_OR': {'P0': 1., 'P1': 0., 'P2': 0., 'P3': 0.}}, critical_share_scale=1.25)


def test_actual_annual_sources_close_without_recounting_zone_multipliers():
    source = os.environ.get('SCI_SOURCE_ROOT')
    if not source:
        pytest.skip('Original read-only SQL is optional; published allocation covers clean-directory replay')
    data = api().compute_auxiliary_allocation(REPO, source)
    boundary = pd.read_csv(REPO / 'inputs/loads/electricity_boundary_8760.csv')
    assert len(data) == 8760
    assert data.hour_of_year.tolist() == list(range(1, 8761))
    assert np.allclose(data[[f'P{p}_fan_kW' for p in range(4)]].sum(axis=1), boundary.fans_kW, atol=1e-8, rtol=0)
    assert np.allclose(data[[f'P{p}_chw_pump_kW' for p in range(4)]].sum(axis=1), boundary.distribution_pumps_unresolved_kW, atol=1e-8, rtol=0)
    assert np.allclose(data[[f'P{p}_heat_pump_kW' for p in range(4)]].sum(axis=1), data.auxiliary_added_e_kW, atol=1e-10, rtol=0)
    meta = data.attrs['metadata']
    er = next(x for x in meta['fan_mapping'] if x['airloop'] == 'VAV_ER')
    assert er['priority_shares'] == pytest.approx({'P0': 1/7, 'P1': 4/7, 'P2': 0., 'P3': 2/7}, abs=1e-12)
    assert meta['zone_multiplier_applied_to_component_flow'] is False
    assert meta['fan_meter_max_abs_residual_kW'] < 1e-8
    component = meta['fan_component_columns']['VAV_OR']
    assert component['power_column'] == 'fan_component_VAV_OR_kW'
    assert component['priority_shares'] == {'P0': 1., 'P1': 0., 'P2': 0., 'P3': 0.}
    assert (data['fan_component_VAV_OR_kW'] > 0.).any()
    assert np.allclose(sum(data[x['power_column']] for x in meta['fan_component_columns'].values()),
        data.source_fans_total_kW, rtol=0, atol=1e-10)
    from hospital_ies.resilience import build_zone_groups
    groups = build_zone_groups(pd.read_csv(REPO / 'inputs/loads/critical_zones_8760.csv'),
        pd.read_csv(REPO / 'inputs/loads/hospital_ies_demand_8760.csv'))
    for p in range(4):
        for carrier in ('e', 'h', 'c'):
            assert np.allclose(data[f'P{p}_base_{carrier}_kW'], groups[f'P{p}_{carrier}_kW'], rtol=0, atol=1e-10)


def test_cached_replay_remains_bound_to_original_load_input_bytes(tmp_path):
    import hashlib
    module = api()
    dest = tmp_path / 'inputs/sci_engineering'
    dest.mkdir(parents=True)
    rows = pd.DataFrame({'hour_of_year': range(1, 8761), 'auxiliary_added_e_kW': np.zeros(8760)})
    for p in range(4):
        for kind in ('fan', 'chw_pump', 'heat_pump'):
            rows[f'P{p}_{kind}_kW'] = 0.
    allocation = dest / module.ALLOCATION_FILE
    rows.to_csv(allocation, index=False)
    metadata = {'allocation_sha256': hashlib.sha256(allocation.read_bytes()).hexdigest(),
        'contract_id': module.CONTRACT_ID, 'critical_share_scale': 1.,
        'root_input_sha256': {'inputs/loads/hospital_ies_demand_8760.csv': 'different input'}}
    (dest / module.METADATA_FILE).write_text(json.dumps(metadata), encoding='utf-8')
    with pytest.raises(ValueError, match='input hash'):
        module.compute_auxiliary_allocation(tmp_path)


def test_cached_replay_is_independent_of_the_original_absolute_sql_path():
    if not (REPO / 'inputs/sci_engineering/auxiliary_metadata.json').is_file():
        pytest.skip('Published auxiliary allocation is not prepared yet')
    data = api().compute_auxiliary_allocation(REPO)
    assert len(data) == 8760
    assert data.attrs['metadata']['allocation_sha256'] == api().sha256(
        REPO / 'inputs/sci_engineering/auxiliary_allocation_8760.csv')
