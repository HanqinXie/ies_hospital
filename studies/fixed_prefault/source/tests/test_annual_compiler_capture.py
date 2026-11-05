"""Source compiler instrumentation is output-only; no optimization executed."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import OptimizeResult

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.model import load_plant
from hospital_ies.resilience import RuntimeState,empty_faults
from hospital_ies.solver_backend import active_original_problem
from hospital_ies.solver_diagnostics import validate_vector
from tools.operating_response import physical_balance as compiler
from tools.annual_program.compiled_capture import capture_scope
from tools.annual_program.compiled_flow_input import validate_compiled_flow_input,SOURCE_SCHEMA


def inputs():
    plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    availability=empty_faults()
    for name in ('GT_1','GT_2','GT_3','DIESEL','ABS','EC_1','EC_2','EC_3'):availability[name]=0.
    state=RuntimeState(np.zeros(3),2400.,set(),np.zeros(3),{})
    loads=np.zeros((4,3));loads[:,0]=[3.,2.,1.,4.]
    config=dict(sci_service_dependencies=True,
        _sci_storage_ports=dict(ups_discharge_kW=0.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02),
        _model_closure_v22=dict(boiler_ready=True,gt_start_kW=10.,ups_energy_kWh=0.,ups_target_kWh=0.,preserve_bridge=True))
    action=dict(gt_electric_kW=[0.,0.,0.],grid_import_kW=100.,diesel_kW=0.,
        boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.])
    return dict(row=SimpleNamespace(timestamp=pd.Timestamp('2001-01-01'),outdoor_temperature_C=20.),
        plant=plant,loads=loads,availability=availability,state=state,dt_h=.25,
        grid_import_limit_kW=100.,diesel_start_available=False,gt_ramp_kW_per_h=900.,
        config=config,execution_action=action,execution_contract=dict(grid_import_mode='arrived_ceiling',diesel_dispatch_mode='arrived_ceiling'))


def test_capture_preserves_all_original_solver_arguments_outputs_and_call_order(monkeypatch):
    calls=[]
    def injected(c,**kwargs):
        problem=active_original_problem();assert problem is not None
        x=np.zeros(len(c));assert len(x)==47;x[11]=20.;x[44]=1.
        checked=validate_vector(problem['matrix'],problem['lower'],problem['upper'],
            problem['bounds_lower'],problem['bounds_upper'],problem['integrality'],x,problem['rows'])
        assert checked['max_original_violation']<=1e-7
        calls.append(dict(c=np.asarray(c).copy(),matrix=problem['matrix'].toarray().copy(),
            lower=problem['lower'].copy(),upper=problem['upper'].copy(),
            bounds_lower=problem['bounds_lower'].copy(),bounds_upper=problem['bounds_upper'].copy(),
            integrality=problem['integrality'].copy(),rows=deepcopy(problem['rows']),options=deepcopy(kwargs['options'])))
        return OptimizeResult(status=0,x=x,fun=float(np.asarray(c)@x),message='explicit valid injected fixture',backend='injected')
    monkeypatch.setattr(compiler,'milp',injected)
    off=compiler.solve_physical(**inputs());first=deepcopy(calls);calls.clear()
    origin=dict(schema=SOURCE_SCHEMA,source_root='synthetic_no_source_auth',
        source_files={'fixture/compiler.py':'a'*64},leaf_function='fixture.solve_physical')
    with capture_scope(origin):on=compiler.solve_physical(**inputs())
    assert '_compiled_flow_input' not in off
    assert '_compiled_flow_input' in on
    packet=on.pop('_compiled_flow_input')
    assert len(first)==len(calls)>0
    for a,b in zip(first,calls):
        for key in ('c','matrix','lower','upper','bounds_lower','bounds_upper','integrality'):
            np.testing.assert_array_equal(a[key],b[key])
        assert a['rows']==b['rows'] and a['options']==b['options']
    np.testing.assert_array_equal(off.pop('lost'),on.pop('lost'))
    np.testing.assert_array_equal(off.pop('served'),on.pop('served'))
    assert off==on
    raw=np.zeros(47);raw[11]=20.;raw[44]=1.
    assert validate_compiled_flow_input(packet,raw_x=raw)==packet
    assert packet['closure']['gt_start_kW']==10.
    assert packet['balance_rows']['Aeq'][0][44]==-10.
    assert packet['actual_execution_bound'] is packet['source_verification_performed'] is False
    assert off['action']['grid_import_kW']==20.


def test_valid_shifted_tracking_probe_is_not_made_infeasible_by_capture(monkeypatch):
    calls=[]
    def injected(c,**kwargs):
        problem=active_original_problem();assert len(c)==48
        x=np.zeros(48);x[11]=20.;x[45]=1.
        checked=validate_vector(problem['matrix'],problem['lower'],problem['upper'],
            problem['bounds_lower'],problem['bounds_upper'],problem['integrality'],x,problem['rows'])
        assert checked['max_original_violation']<=1e-7
        calls.append((np.asarray(c).copy(),problem['matrix'].toarray().copy()))
        return OptimizeResult(status=0,x=x,fun=float(np.asarray(c)@x),message='valid shifted tracking fixture',backend='injected')
    monkeypatch.setattr(compiler,'milp',injected)
    values=inputs();values['tracking_targets']={'grid_import':20.}
    off=compiler.solve_physical(**values);first=deepcopy(calls);calls.clear()
    origin=dict(schema=SOURCE_SCHEMA,source_root='synthetic_no_source_auth',
        source_files={'fixture/compiler.py':'a'*64},leaf_function='fixture.solve_physical')
    with capture_scope(origin):on=compiler.solve_physical(**inputs(),tracking_targets={'grid_import':20.})
    unknown=on.pop('_compiled_flow_input_unavailable')
    assert unknown['reason']=='unsupported_leaf_index_map' and unknown['status']=='unknown'
    assert '_compiled_flow_input' not in on
    for name in ('lost','served'):np.testing.assert_array_equal(off.pop(name),on.pop(name))
    assert off==on and len(first)==len(calls)
    for a,b in zip(first,calls):
        np.testing.assert_array_equal(a[0],b[0]);np.testing.assert_array_equal(a[1],b[1])
