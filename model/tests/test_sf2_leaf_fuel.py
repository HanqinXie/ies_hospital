"""Small native fixed-path and explicit nonactual free-leaf components."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]


def arguments(*, fixed=True):
    from hospital_ies.model import load_plant
    from hospital_ies.resilience import RuntimeState,empty_faults
    from tools.single_fault_fuel_20261102.fuel import FuelCurve,FuelPath
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    curve=FuelCurve.from_source(ROOT,plant)
    row=SimpleNamespace(timestamp=pd.Timestamp('2017-01-01 00:00'),outdoor_temperature_C=15.)
    state=RuntimeState(np.array([224.,0.,0.]),2400.,set(),np.zeros(3),{})
    action=dict(gt_electric_kW=[224.,0.,0.],grid_import_kW=26.,diesel_kW=0.,boiler_steam_lbph=0.,
        absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    loads=np.zeros((4,3));loads[0,0]=250.
    path=FuelPath.build(curve,role='actual_interval',start_minute=15.,end_minute=30.,temperature_C=15.,
        start_kW=(128.,0.,0.),end_kW=(320.,0.,0.))
    return dict(row=row,plant=plant,loads=loads,availability=empty_faults(),state=state,dt_h=.25,
        grid_import_limit_kW=350.,diesel_start_available=False,gt_ramp_kW_per_h=900.,solver_time_limit_sec=6.,
        config={'_sf2_fuel_curve':curve.as_dict()},execution_action=action if fixed else None),curve,path


def test_fixed_actual_leaf_uses_integrated_cost_without_adding_primal_coordinates():
    from hospital_ies.solver_diagnostics import LayerCollector,collect_layers
    from tools.operating_response.physical_balance import solve_physical
    args,curve,path=arguments(); collector=LayerCollector()
    with collect_layers(collector):
        result=solve_physical(**args,sf2_fuel_path=path,sf2_fuel_role='actual_interval')
    receipt=result['_sf2_fuel_receipt']
    assert len(collector.layers[-1]['objective'])==26
    assert receipt['native_vector_dimension']==26
    assert receipt['accounting_committed'] is False
    fuel=curve.integral(128.,320.,.25,15.)
    assert receipt['running_fuel_MMBtu']==pytest.approx(fuel,rel=1e-13)
    assert result['action']['gt_fuel_MMBtu_h']==pytest.approx(fuel/.25,rel=1e-13)
    from hospital_ies.model import illustrative_tariff
    expected=fuel*args['plant'].gas_price_usd_per_mmbtu+26.*.25*illustrative_tariff(args['row'].timestamp,args['plant'])
    # The historical cost field includes the unused-steam 1e-8 artificial tie.
    # Preserve that objective, while the new explicit fee excludes this tie.
    assert result['cost_USD_step']==pytest.approx(expected+receipt['accepted_raw_x'][13]*1e-8,abs=1e-7)
    assert result['sf2_physical_operating_fee_USD_step']==pytest.approx(expected,abs=1e-7)
    assert result['action']['gt_electric_kW']==[224.,0.,0.]
    assert receipt['path_identity']==path.identity


@pytest.mark.parametrize('change',['missing','wrong_mean','wrong_dt','wrong_T','point_role'])
def test_actual_leaf_rejects_missing_or_mismatched_bound_path(change):
    from tools.operating_response.physical_balance import solve_physical
    args,curve,path=arguments()
    if change=='missing':path=None
    elif change=='wrong_mean':args['execution_action']['gt_electric_kW'][0]=225.
    elif change=='wrong_dt':args['dt_h']=.125
    elif change=='wrong_T':args['row'].outdoor_temperature_C=30.
    elif change=='point_role':
        from dataclasses import replace
        path=replace(path,role='point_probe')
    with pytest.raises(ValueError,match='SF2|path|actual'):
        solve_physical(**args,sf2_fuel_path=path,sf2_fuel_role='actual_interval')


def test_free_reference_leaf_is_exact_nonlinear_but_not_an_actual_interval_meter():
    from hospital_ies.solver_diagnostics import LayerCollector,collect_layers
    from tools.operating_response.physical_balance import solve_physical
    args,curve,path=arguments(fixed=False); collector=LayerCollector()
    with collect_layers(collector):result=solve_physical(**args,sf2_fuel_role='free_reference_probe')
    assert len(collector.layers[-1]['objective'])==29
    receipt=result['_sf2_fuel_receipt']
    assert receipt['role']=='free_reference_probe' and receipt['running_fuel_MMBtu'] is None
    assert receipt['accounting_committed'] is False
    assert result['action']['gt_fuel_MMBtu_h']==pytest.approx(sum(curve.rate(v,15.) for v in result['action']['gt_electric_kW']))
    assert collector.layers[-1]['result']['attempts'][-1]['nonlinear_graph']['equalities']
