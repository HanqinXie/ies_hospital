"""Station/DC requests are pure; only actual starts spend finite DC rights."""
from pathlib import Path
from types import SimpleNamespace
import sys
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]


def test_pending_start_can_be_cancelled_without_spending_dc():
    from tools.model_correctness_v23.station_paths import DedicatedDCState
    initial = DedicatedDCState()
    pending = initial.request(True)
    assert pending.pending and initial.starts_used == pending.starts_used == 0
    cancelled = pending.request(False)
    assert not cancelled.pending and cancelled.starts_used == 0
    assert cancelled.advance(100.).available(100., hardware_available=True)


def test_failed_actual_start_spends_dc_and_repeated_commit_is_idempotent():
    from tools.model_correctness_v23.station_paths import DedicatedDCState
    initial = DedicatedDCState().request(True)
    started = initial.commit(10., hardware_available=True, start_token='attempt-1')
    assert initial.starts_used == 0 and started.starts_used == 1
    failed = started.request(False).advance(11.)
    repeated = failed.commit(10., hardware_available=True, start_token='attempt-1')
    assert repeated == failed
    second = failed.request(True).commit(12., hardware_available=True, start_token='attempt-2')
    assert second.starts_used == 2
    assert not second.available(13., hardware_available=True)
    with pytest.raises(ValueError, match='unqualified'):
        second.request(True).commit(13., hardware_available=True, start_token='attempt-3')


def test_hardware_recovery_never_refills_exhausted_dc_or_expired_window():
    from tools.model_correctness_v23.station_paths import DedicatedDCState
    state = DedicatedDCState().request(True)
    assert not state.available(10., hardware_available=False)
    assert state.available(11., hardware_available=True)
    one = state.commit(11., hardware_available=True, start_token='one')
    two = one.request(True).commit(12., hardware_available=True, start_token='two')
    assert not two.advance(20.).available(20., hardware_available=True)
    expired = DedicatedDCState().advance(1440.)
    assert not expired.available(1440., hardware_available=True)
    assert not expired.request(True).available(1500., hardware_available=True)


def test_actual_start_requires_pending_request_and_live_dc():
    from tools.model_correctness_v23.station_paths import DedicatedDCState
    with pytest.raises(ValueError, match='request'):
        DedicatedDCState().commit(0., hardware_available=True, start_token='one')
    with pytest.raises(ValueError, match='unqualified'):
        DedicatedDCState().request(True).commit(0., hardware_available=False, start_token='one')


def test_boiler_gt_feeder_has_compatible_alias_without_expanding_topology():
    from tools.model_correctness_v23.station_paths import boiler_gt_ac_available
    assert not boiler_gt_ac_available({'STATION_AC': 0.})
    assert not boiler_gt_ac_available({'BOILER_GT_START_AC': 0.})
    assert boiler_gt_ac_available({})
    with pytest.raises(ValueError, match='conflict'):
        boiler_gt_ac_available({'STATION_AC': 0., 'BOILER_GT_START_AC': 1.})


def test_supply_audit_distinguishes_building_auxiliaries_from_plant_ports():
    from tools.model_correctness_v23.station_paths import audit_supply
    plant = SimpleNamespace(ec_electric_kW_per_rt=1., abs_electric_kW_per_rt=.1)
    action = dict(gt_electric_kW=[0., 0., 0.], grid_import_kW=5., diesel_kW=0.,
        electric_chiller_cooling_kW=[3.51685284 * 6., 0., 0.], absorption_cooling_kW=0.,
        ups_discharge_kW=15., ups_charge_kW=0., cold_storage_pump_kW=0.,
        boiler_auxiliary_kW=0., gt_start_electric_kW=0., boiler_restart_electric_kW=0.)
    report = audit_supply(action, plant, building_delivered_electric_kW=14.)
    assert report['plant_electric_kW'] == pytest.approx(6.)
    assert report['non_UPS_plant_deficit_kW'] == pytest.approx(1.)
    assert not report['plant_source_eligible']
    action['grid_import_kW'] = 6.
    action['ups_discharge_kW'] = 14.
    assert audit_supply(action, plant, building_delivered_electric_kW=14.)['plant_source_eligible']


def _ups_only_ec_port():
    from hospital_ies.model import load_plant
    from hospital_ies.resilience import RuntimeState, empty_faults
    from tools.operating_response.physical_balance import solve_physical
    plant = load_plant(ROOT/'configs/plant_configs.json', 'doe_capacity_matched_exploratory')
    row = SimpleNamespace(timestamp=pd.Timestamp('2017-07-10 12:00'), outdoor_temperature_C=25.)
    availability = empty_faults()
    availability.update(GT_1=0., GT_2=0., GT_3=0., GRID=0., DIESEL=0., ABS=0., EC_2=0., EC_3=0.)
    state = RuntimeState(np.zeros(3), 2400., set(), np.zeros(3), {})
    loads = np.zeros((4, 3)); loads[0] = [10., 0., 10.]
    config = dict(sci_service_dependencies=True,
        _sci_storage_ports=dict(ups_discharge_kW=160., ups_charge_kW=0.),
        _model_closure_v22=dict(station_ac_available=True, boiler_ready=True,
            ups_energy_kWh=40., ups_target_kWh=40., preserve_bridge=True))
    action = dict(gt_electric_kW=[0.,0.,0.], grid_import_kW=0., diesel_kW=0.,
        boiler_steam_lbph=0., absorption_cooling_kW=0., electric_chiller_cooling_kW=[10.,0.,0.])
    return solve_physical(row, plant, loads, availability, state, .25, 0., False, 900., 6., config,
        execution_action=action, execution_contract=dict(diesel_mode='legacy_explicit_permission',
            diesel_dispatch_mode='arrived_ceiling', grid_import_mode='arrived_ceiling'))


def test_building_only_ups_cannot_indirectly_pay_ec_main_power():
    result = _ups_only_ec_port()
    assert result['action']['electric_chiller_cooling_kW'][0] < 1e-4
    assert result['served'][0, 0] == pytest.approx(10., abs=1e-4)
    assert result['served'][0, 2] < 1e-4
