"""Synthetic same-current Gurobi regressions; no annual loads or trajectories."""
import copy
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'src')]

from hospital_ies.execution import execution_contract
from hospital_ies.model import gt_level_properties, load_plant
from hospital_ies.resilience import RuntimeState, empty_faults
from hospital_ies.solver_diagnostics import LayerCollector, collect_layers
from tools.autonomous_dispatch import planner_standby

OUT = ROOT/'outputs/v12_service_switch_20261017_run01/validation/planner'/os.environ.get('COORDINATED_PLANNER_TEST_RUN','unlabelled')
BASE = dict(variant='coupled', regulator_consistent=True,
            critical_reserve_multiplier=1.25, preserve_one_gt_standby=True)
PREFERENCES = ('standby_first', 'bridge_first', 'service_first', 'bridge_service_first')


def api():
    try:
        return importlib.import_module('tools.coordinated_dispatch.planner')
    except ModuleNotFoundError:
        pytest.fail('The bounded-service/source-handoff planner has not been implemented')


@pytest.fixture
def problem():
    return dict(row=SimpleNamespace(timestamp=pd.Timestamp('2025-01-15 10:00'), outdoor_temperature_C=20.),
        plant=load_plant(ROOT/'configs/plant_configs.json', 'doe_capacity_matched_exploratory'),
        loads=np.array([[80.,30.,40.],[60.,20.,30.],[100.,40.,60.],[120.,50.,70.]]),
        availability=empty_faults(),
        state=RuntimeState(np.array([150.,150.,150.]),2400.,set(),np.zeros(3),{}),
        dt_h=.25,grid_import_limit_kW=850.,diesel_start_available=True,
        gt_ramp_kW_per_h=900.,solver_time_limit_sec=6.,
        config=json.loads((ROOT/'configs/resilience_assumptions.json').read_text(encoding='utf-8')),
        execution_contract=execution_contract({'grid_import_mode':'arrived_ceiling'}))


def diesel_only(problem):
    p=copy.deepcopy(problem);p['loads']=np.zeros((4,3));p['loads'][:,0]=[20.,20.,200.,200.]
    p['availability'].update({f'GT_{i}':0. for i in (1,2,3)})
    p['availability']['GRID']=0.;p['grid_import_limit_kW']=0.
    p['state'].gt_prev[:]=0.;p['state'].diesel_energy_remaining_kWh=240.
    return p


def solve(p, options, name):
    collector=LayerCollector({'test_case':name,'synthetic_current_only':True})
    with collect_layers(collector):result=api().solve_joint(**p,joint_options=options)
    collector.export(OUT/name)
    for layer in collector.layers:
        assert layer['result']['status']==0
        assert layer['validation']['max_original_violation']<=1e-7
        assert layer['validation']['max_lock_violation']<=1e-10
        assert layer['validation']['max_integrality_distance']<=1e-9
        np.testing.assert_array_equal(layer['row_scales'],np.ones(len(layer['rows'])))
    assert all(x['backend']=='gurobipy' for x in result['solver_backend_layers'])
    assert max(map(abs,result['energy_balance_residuals']))<=1e-7
    return result,collector


def test_options_have_strict_current_only_defaults():
    module=api();o=module._options(None)
    assert o['service_budget_enabled'] is True and o['handoff_enabled'] is True
    assert o['features_enabled'] is True and o['service_budget_fractions']==[.05,.20]
    assert o['coordination_preference']=='standby_first'
    assert module.SERVICE_SWITCH_CONTRACT_ID=='current_service_budget_handoff.v1'
    for key in ('features_enabled','service_budget_enabled','handoff_enabled'):
        for bad in (0,1,None,'true',float('nan')):
            with pytest.raises(ValueError):module._options({key:bad})
    for bad in ([.1],[.1,.2,.3],[True,.2],['.1',.2],[float('nan'),.2],[-.1,.2],[.1,1.1],None):
        with pytest.raises(ValueError):module._options({'service_budget_fractions':bad})
    for bad in ('future_loads','future_fault_time','scenario_id','source_day_index','horizon_steps'):
        with pytest.raises(ValueError):module._options({bad:1})
    for mode in PREFERENCES:assert module._options({'coordination_preference':mode})['coordination_preference']==mode
    with pytest.raises(ValueError):module._options({'coordination_preference':'MPC'})


def test_disabled_features_delegate_exact_v11_behavior_and_canonical_rows(problem):
    before=LayerCollector({'test_case':'old_v11_readonly_comparator'})
    with collect_layers(before):old=planner_standby.solve_joint(**problem,joint_options=BASE)
    before.export(OUT/'disabled_old_v11')
    new,after=solve(problem,{**BASE,'features_enabled':False},'disabled_exact')
    assert new['action']==old['action'] and new['planned_current_action']==old['planned_current_action']
    assert new['lex_values']==old['lex_values'] and new['cost_USD_step']==old['cost_USD_step']
    assert new['joint_diagnostic']==old['joint_diagnostic']
    np.testing.assert_array_equal(new['command_disconnected'],old['command_disconnected'])
    assert len(before.layers)==len(after.layers)
    for a,b in zip(before.layers,after.layers):
        for field in ('objective','integrality','bounds_lower','bounds_upper','lower','upper'):
            np.testing.assert_array_equal(a[field],b[field])
        np.testing.assert_array_equal(a['matrix'].data,b['matrix'].data)
        np.testing.assert_array_equal(a['matrix'].indices,b['matrix'].indices)
        np.testing.assert_array_equal(a['matrix'].indptr,b['matrix'].indptr)
        assert a['rows']==b['rows']


def test_service_reference_prevents_endurance_from_unbounded_low_service_cut(problem):
    p=diesel_only(problem)
    old=planner_standby.solve_joint(**p,joint_options={'variant':'endurance'})
    new,_=solve(p,{'variant':'endurance','handoff_enabled':False},'fuel_low_service_budget')
    d=new['joint_diagnostic'];b=d['service_budget'];ref=d['service_reference']
    assert old['action']['diesel_kW']<121.
    assert new['action']['diesel_kW']>389.99
    np.testing.assert_allclose(new['lex_values'][:2],old['lex_values'][:2],atol=1e-10,rtol=0.)
    np.testing.assert_allclose(b['allowed_extra_kWh'],[[2.5,0.,0.],[10.,0.,0.]],atol=1e-12)
    assert np.max(np.asarray(b['violation_kWh']))<=1e-7
    extra=np.maximum(0.,(new['lost'][2:]-np.asarray(ref['unserved_kW'])[2:])*p['dt_h'])
    np.testing.assert_allclose(b['used_extra_kWh'],extra,atol=1e-10)
    assert np.all(extra<=np.asarray(b['allowed_extra_kWh'])+1e-7)
    assert [x['name'] for x in ref['layers']]==['reference_P2','reference_P3','reference_operating_cost']
    assert d['objective_order'][:4]==['current_P0','current_P1','service_budget_P2_violation','service_budget_P3_violation']


def test_budget_units_scale_with_current_step_and_zero_demand(problem):
    for dt in (.25,.5):
        p=diesel_only(problem);p['dt_h']=dt
        new,_=solve(p,{'variant':'endurance','handoff_enabled':False,'service_budget_fractions':[0.,.1]},'units_'+str(dt))
        b=new['joint_diagnostic']['service_budget']
        np.testing.assert_array_equal(b['allowed_extra_kWh'],[[0.,0.,0.],[20.*dt,0.,0.]])
        assert new['lost'][2,0]<1e-3
        assert np.asarray(b['violation_kWh']).max()<=1e-7


def test_current_p0_can_require_three_gt_despite_preparation(problem):
    p=copy.deepcopy(problem);rated=gt_level_properties(p['plant'],20.)[0][-1]
    p['loads']=np.zeros((4,3));p['loads'][0,0]=2.5*rated
    p['grid_import_limit_kW']=0.;p['availability']['GRID']=0.
    p['diesel_start_available']=False;p['state'].gt_prev[:]=0.;p['gt_ramp_kW_per_h']=1e6
    old=planner_standby.solve_joint(**p,joint_options={**BASE,'critical_reserve_multiplier':1.})
    new,_=solve(p,{**BASE,'critical_reserve_multiplier':1.},'critical_needs_three')
    assert new['joint_diagnostic']['standby_planned_GT_on_count']==pytest.approx(3.,abs=1e-9)
    assert new['joint_diagnostic']['standby_third_gt_on_excess']==pytest.approx(1.,abs=1e-7)
    np.testing.assert_allclose(new['lex_values'][:2],old['lex_values'][:2],atol=1e-10,rtol=0.)
    assert new['lost'][0,0]<1e-3


def test_handoff_copy_withdraws_diesel_without_fictitious_import_or_gt_heat(problem):
    p=copy.deepcopy(problem);p['availability']['GRID']=0.;p['state'].gt_prev[:]=0.
    p['gt_ramp_kW_per_h']=1e6
    new,collector=solve(p,{**BASE,'critical_reserve_multiplier':1.,'coordination_preference':'bridge_first'},'handoff_zero_import')
    d=new['joint_diagnostic'];side=d['risk_scenarios']['DG_withdrawal_GRID_ceiling']
    assert side['diesel_kW']==0. and side['grid_import_kW']==0.
    assert new['action']['grid_import_kW']==0.
    np.testing.assert_allclose(side['gt_electric_kW'],new['action']['gt_electric_kW'],atol=1e-9)
    assert side['hrsg_steam_lbph']==pytest.approx(new['action']['hrsg_steam_lbph'],abs=1e-7)
    assert side['boiler_steam_lbph']==pytest.approx(new['action']['boiler_steam_lbph'],abs=1e-9)
    assert max(map(abs,side['balance_residuals']))<=1e-7
    assert side['unserved_kW'][0][0]<1e-3
    assert sum(new['action']['gt_electric_kW'])>79.
    common=d['risk_scenarios']['GT_common_loss']
    assert common['gt_electric_kW']==[0.,0.,0.] and common['hrsg_steam_lbph']==0.
    first=collector.layers[0];rows={x['name']:i for i,x in enumerate(first['rows'])}
    e=first['matrix'].getrow(rows['risk.DG_withdrawal_GRID_ceiling.balance.electric']).toarray()[0]
    assert e[12]==0.
    assert 'electric_surplus_defect_kW' not in new['action']


def test_preference_changes_only_order_not_reference_physics_or_budget(problem):
    saved=[]
    for mode in PREFERENCES:
        new,collector=solve(problem,{**BASE,'coordination_preference':mode},'order_'+mode)
        saved.append((new,collector))
    initial,first=saved[0]
    for new,collector in saved[1:]:
        for field in ('objective','integrality','bounds_lower','bounds_upper','lower','upper'):
            np.testing.assert_array_equal(first.layers[0][field],collector.layers[0][field])
        np.testing.assert_array_equal(first.layers[0]['matrix'].toarray(),collector.layers[0]['matrix'].toarray())
        assert new['joint_diagnostic']['service_reference']['unserved_kW']==initial['joint_diagnostic']['service_reference']['unserved_kW']
        assert new['joint_diagnostic']['service_budget']['allowed_extra_kWh']==initial['joint_diagnostic']['service_budget']['allowed_extra_kWh']
    orders={r['joint_diagnostic']['coordination_preference']:r['joint_diagnostic']['objective_order'] for r,_ in saved}
    assert orders['standby_first'].index('standby_third_GT_exposure')<orders['standby_first'].index('preparation_P0')
    assert orders['bridge_first'].index('preparation_P0')<orders['bridge_first'].index('standby_third_GT_exposure')
    assert orders['service_first'].index('current_P2')<orders['service_first'].index('standby_third_GT_exposure')
    assert orders['bridge_service_first'].index('preparation_P0')<orders['bridge_service_first'].index('current_P2')<orders['bridge_service_first'].index('standby_third_GT_exposure')
