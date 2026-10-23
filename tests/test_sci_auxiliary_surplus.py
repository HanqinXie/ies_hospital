"""Failed M21 boundary: original aux service is not an actual electrical sink.

Only the failed current state is frozen. This is not a new episode, policy
comparison or a change to capacities, permissions, protection or tolerances.
"""
from pathlib import Path
from types import SimpleNamespace
import copy,json,sys
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.execution import maximum_auxiliary_sink,ModelConstraintInfeasible
from tools.sci_engineering.environment import EngineeringCore,DEFAULT_ENGINEERING
from tools.sci_engineering.storage import FiniteStore
from tools.sci_engineering.service import dependency_requirements
from test_execution_recovery_core import small_core


def failed_boundary(*,heat_availability=.5,ups=None,cold=None):
    fixture=json.loads((ROOT/'tests/fixtures/sci_heat_pump_start_surplus.json').read_text())
    loads=np.asarray(fixture['loads_kW'],float);cuts=np.asarray(fixture['cuts_kW'],float)
    base,_,_,_=small_core(loads=loads+cuts,gt=0.,diesel=0.)
    core=EngineeringCore.__new__(EngineeringCore);core.__dict__.update(base.__dict__)
    core.env.config.update(fixture['config']);core.env.sci_config=copy.deepcopy(DEFAULT_ENGINEERING)
    core.env.sci_config.update(ups=ups or {},cold_store=cold or {})
    core.ups=FiniteStore(ups);core.cold_store=FiniteStore(cold)
    row=SimpleNamespace(timestamp=pd.Timestamp(fixture['row']['timestamp']),outdoor_temperature_C=fixture['row']['outdoor_temperature_C'])
    for g in range(4):
        for k,carrier in enumerate(('e','h','c')):setattr(row,f'P{g}_reference_{carrier}_kW',float(loads[g,k]))
        for j,kind in enumerate(('fan','chw_pump','heat_pump')):setattr(row,f'P{g}_{kind}_kW',fixture['auxiliary_required_kW'][g][j])
    decomposition=fixture['heat_decomposition']
    row.Q_h_DHW_water_use_kW=decomposition['source_DHW_kW'];row.sci_heat_decomposition_required=True
    row.sci_heat_decomposition_source=decomposition['source_trace']
    availability=copy.deepcopy(fixture['availability']);availability['AUX_HEAT_PUMP']=heat_availability
    core.time=fixture['provenance']['time_minute'];core.state.disconnected_loads=cuts.copy()
    core.executed=copy.deepcopy(fixture['executed_action']);core.arrived_dispatch=copy.deepcopy(core.executed)
    core._commit_sources(core.executed);core.grid_bus_state='GRID'
    core._hardware_data_at=lambda minute:(2135,row,(loads+cuts).copy(),dict(availability))
    core.active_command=dict(id=13,generated_minute=180.,arrival_minute=180.,request={},dispatch=copy.deepcopy(core.executed))
    assert np.array_equal(dependency_requirements(row,loads),np.asarray(fixture['auxiliary_required_kW']))
    return core,loads,availability,row,fixture


def current_balance(core,loads,f,row,fixture):
    return core._solve(loads,f,fixture['dt_h'],'failed_boundary_regression',core.active_command,
        execution_action=copy.deepcopy(core.executed))


def test_original_fixed_gt_snapshot_remains_physically_infeasible_not_a_tolerance_fix():
    core,loads,f,row,fixture=failed_boundary()
    legacy_sink=float(loads[:,0].sum())+maximum_auxiliary_sink(core.env.plant,row,loads,f,core.executed)
    assert sum(core.executed['gt_electric_kW'])<legacy_sink
    required=dependency_requirements(row,loads)
    available=required[:,0]+required[:,1]+required[:,2]*.5
    direct=np.maximum(0.,loads[:,0]-required.sum(axis=1))
    actual_upper=float(np.minimum(loads[:,0],direct+available).sum())
    converter=maximum_auxiliary_sink(core.env.plant,row,loads,f,core.executed)
    surplus=sum(core.executed['gt_electric_kW'])-actual_upper-converter
    assert surplus==pytest.approx(.0020343345791364875,abs=1e-10)
    with pytest.raises(ModelConstraintInfeasible):current_balance(core,loads,f,row,fixture)


def test_common_no_export_interlock_isolates_gt_before_impossible_actual_balance():
    core,loads,f,row,fixture=failed_boundary();request=copy.deepcopy(core.active_command)
    original=loads.copy();core._surplus_interlock(loads,f,row)
    event=next((e for e in core.events if e['kind']=='no_export_interlock'),None)
    assert event is not None,'Bad auxiliary service cannot remain a fictitious electrical sink'
    assert event['device']=='GT_2'
    assert core.executed['gt_electric_kW'][1]==0.
    assert core.active_command==request and np.array_equal(loads,original)
    result=current_balance(core,loads,f,row,fixture)
    assert np.max(np.abs(result['energy_balance_residuals']))<1e-7
    assert result['action']['gt_electric_kW']==core.executed['gt_electric_kW']
    assert result['action']['grid_import_kW']<=core.arrived_dispatch['grid_import_kW']


@pytest.mark.parametrize('store',['ups','cold_store'])
def test_actual_registered_charge_path_is_not_discarded_as_export(store):
    config=dict(capacity_kWh=40. if store=='ups' else 60.,power_kW=160. if store=='ups' else 120.,initial_soc=.5)
    core,loads,f,row,fixture=failed_boundary(**({'ups':config} if store=='ups' else {'cold':config}))
    core._surplus_interlock(loads,f,row)
    assert not any(e['kind']=='no_export_interlock' for e in core.events)
    result=current_balance(core,loads,f,row,fixture)
    assert result['action']['ups_charge_kW' if store=='ups' else 'cold_charge_kW']>0.
    assert np.max(np.abs(result['energy_balance_residuals']))<1e-7


def test_healthy_auxiliary_boundary_preserves_existing_gt_and_request():
    core,loads,f,row,fixture=failed_boundary(heat_availability=1.);before=copy.deepcopy(core.executed)
    core._surplus_interlock(loads,f,row)
    assert not any(e['kind']=='no_export_interlock' for e in core.events)
    assert core.executed==before
    assert np.max(np.abs(current_balance(core,loads,f,row,fixture)['energy_balance_residuals']))<1e-7

@pytest.mark.parametrize('store',['ups','cold_store'])
def test_near_full_registered_store_has_native_short_charge_boundary(store):
    config=dict(capacity_kWh=40. if store=='ups' else 60.,power_kW=160. if store=='ups' else 120.,initial_soc=1.)
    core,loads,f,row,fixture=failed_boundary(**({'ups':config} if store=='ups' else {'cold':config}))
    inventory=core.ups if store=='ups' else core.cold_store
    inventory.energy_kWh=inventory.config['capacity_kWh']-.0001
    # Ports are current power permissions; exact inventory bounds split the
    # next native segment rather than averaging headroom over15minutes.
    assert core._storage_ports(.25,f)==core._storage_ports(1e-5,f)
    core._surplus_interlock(loads,f,row)
    assert not any(e['kind']=='no_export_interlock' for e in core.events)
    result=current_balance(core,loads,f,row,fixture)
    power=result['action']['ups_charge_kW' if store=='ups' else 'cold_charge_kW']
    assert power>0.
    hours=inventory.boundary_hours(discharge_kW=0.,charge_kW=power,max_hours=fixture['dt_h'])
    assert 0.<hours<fixture['dt_h']
    receipt=inventory.commit(discharge_kW=0.,charge_kW=power,dt_h=hours)
    assert receipt['final_kWh']==pytest.approx(inventory.config['capacity_kWh'],abs=1e-9)
    assert abs(receipt['balance_residual_kWh'])<1e-7
    # At the true full endpoint that charging sink is gone; unchanged
    # no-export protection must no longer pretend it remains available.
    core._surplus_interlock(loads,f,row)
    assert any(e['kind']=='no_export_interlock' for e in core.events)

def cold_discharge_boundary(*,chw=1.):
    store=dict(capacity_kWh=60.,power_kW=120.,initial_soc=.5)
    core,loads,f,row,fixture=failed_boundary(cold=store)
    f['TOWER']=0.;f['AUX_CHW_PUMP']=chw
    required=dependency_requirements(row,loads)
    direct=np.maximum(0.,loads[:,0]-required.sum(axis=1))
    building_upper=float(np.minimum(loads[:,0],direct+required[:,0]+required[:,1]*chw+required[:,2]*.5).sum())
    core.executed['gt_electric_kW'][1]=building_upper+.5-core.executed['gt_electric_kW'][2]
    core.arrived_dispatch=copy.deepcopy(core.executed);core._commit_sources(core.executed)
    return core,loads,f,row,fixture


def test_cold_discharge_circulation_pump_is_a_legal_actual_sink_during_tower_loss():
    core,loads,f,row,fixture=cold_discharge_boundary();before=copy.deepcopy(core.executed)
    proof=current_balance(core,loads,f,row,fixture)
    assert proof['action']['cold_discharge_kW']>0. and proof['action']['cold_charge_kW']==0.
    assert proof['action']['cold_storage_pump_kW']>0.
    assert np.max(np.abs(proof['energy_balance_residuals']))<1e-7
    core._surplus_interlock(loads,f,row)
    assert not any(e['kind']=='no_export_interlock' for e in core.events), 'Real cold discharge pump power cannot be omitted'
    assert core.executed==before


def test_cold_discharge_pump_is_not_a_sink_without_critical_delivery_path():
    core,loads,f,row,fixture=cold_discharge_boundary(chw=0.)
    with pytest.raises(ModelConstraintInfeasible):current_balance(core,loads,f,row,fixture)
    core._surplus_interlock(loads,f,row)
    assert any(e['kind']=='no_export_interlock' for e in core.events)

def test_real_contact_restore_proof_uses_the_same_registered_ups_charge_port():
    store=dict(capacity_kWh=40.,power_kW=160.,initial_soc=.5)
    core,loads,f,row,fixture=failed_boundary(heat_availability=1.,ups=store)
    sink=core._electric_sink_upper(loads,f,row);needed=.001
    core.executed['gt_electric_kW'][0]=150.
    core.executed['gt_electric_kW'][1]=sink['upper_kW']+needed-150.-core.executed['gt_electric_kW'][2]
    core.arrived_dispatch=copy.deepcopy(core.executed);core._commit_sources(core.executed)
    candidate=loads.copy();candidate[3,0]+=needed
    manual=copy.deepcopy(core);manual.state.disconnected_loads[3,0]-=needed
    proof=current_balance(manual,candidate,f,row,fixture)
    assert proof['action']['ups_charge_kW']>0.
    assert float(np.asarray(proof['lost'])[:,0].sum())<=1e-4
    assert np.max(np.asarray(proof['lost'])[:2,1:])<=1e-4
    original_total=loads+core.state.disconnected_loads
    request=copy.deepcopy(core.arrived_dispatch)
    assert core._restore_real_contacts(loads,f,row),'Strict proof must keep the actual registered storage ports'
    assert np.allclose(loads+core.state.disconnected_loads,original_total,rtol=0.,atol=1e-12)
    assert core.arrived_dispatch==request
    assert any(e['kind']=='local_real_load_restored' for e in core.events)
