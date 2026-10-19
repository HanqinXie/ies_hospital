"""One lawful saved pre-shock problem and bounded static standby regressions."""
import copy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from test_autonomous_dispatch_planner import problem
from hospital_ies.model import Plant, gt_level_properties
from hospital_ies.resilience import RuntimeState
from hospital_ies.solver_diagnostics import LayerCollector, collect_layers
from tools.autonomous_dispatch import numerics

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/v11_autonomous_dispatch_20261016_run01/validation/standby'


def api():
    try:
        return importlib.import_module('tools.autonomous_dispatch.planner_standby')
    except ModuleNotFoundError:
        pytest.fail('Independent soft third-GT standby planner is missing')


def saved_problem():
    p = copy.deepcopy(json.loads((OUT/'current_problem.json').read_text(encoding='utf-8'))['current'])
    p['row'] = SimpleNamespace(**p['row']); p['row'].timestamp = pd.Timestamp(p['row'].timestamp)
    for k in ('electric_chillers_rt','levels'):p['plant'][k] = tuple(p['plant'][k])
    p['plant'] = Plant(**p['plant'])
    for k in ('gt_prev','overload_minutes','disconnected_loads'):p['state'][k] = np.asarray(p['state'][k],float)
    p['state']['extra_faulted'] = set(p['state']['extra_faulted']); p['state'] = RuntimeState(**p['state'])
    p['loads'] = np.asarray(p['loads'],float)
    if p.get('risk_loads') is not None:p['risk_loads'] = np.asarray(p['risk_loads'],float)
    return p


@pytest.fixture(scope='module')
def saved_baseline():
    p=saved_problem();collector=LayerCollector({'test_case':'e033_pre_shock_current_only'})
    with collect_layers(collector):old=numerics.solve_joint(**p)
    try:importlib.import_module('tools.autonomous_dispatch.planner_standby');prefix='green'
    except ModuleNotFoundError:prefix='red'
    collector.export(OUT/(prefix+'_v4'))
    assert sum(x>.5 for x in old['joint_diagnostic']['command_GT_on'])==3
    return p,old,collector


def validate_layers(collector):
    for layer in collector.layers:
        assert layer['result']['status']==0
        assert layer['validation']['max_original_violation']<=1e-7
        assert layer['validation']['max_lock_violation']<=1e-10
        assert layer['validation']['max_integrality_distance']<=1e-9
        np.testing.assert_array_equal(layer['row_scales'],np.ones(len(layer['rows'])))


def test_saved_pre_shock_prefers_at_most_two_after_current_priority_locks(saved_baseline):
    p,old,before=saved_baseline;module=api();p=copy.deepcopy(p)
    p['joint_options']['preserve_one_gt_standby']=True
    after=LayerCollector({'test_case':'same_e033_pre_shock_soft_standby'})
    with collect_layers(after):new=module.solve_joint(**p)
    after.export(OUT/'green_standby')
    validate_layers(after)
    assert new['joint_diagnostic']['standby_planned_GT_on_count']<=2.+1e-9
    assert new['joint_diagnostic']['standby_third_gt_on_excess']<=1e-7
    assert new['joint_diagnostic']['standby_is_execution_guarantee'] is False
    np.testing.assert_allclose(new['lex_values'][:2],old['lex_values'][:2],atol=1e-10,rtol=0.)
    names=[x['name'] for x in new['joint_diagnostic']['layers']]
    assert names[:4]==['current_P0','current_P1','standby_third_GT_exposure','preparation_P0']
    # No physical coefficient, original bound, variable, objective or current
    # normalized lock is relaxed. The appended z has no finite upper bound.
    for left,right in zip(before.layers[:2],after.layers[:2]):
        n=len(left['objective'])
        np.testing.assert_array_equal(left['objective'],right['objective'][:n])
        np.testing.assert_array_equal(left['bounds_lower'],right['bounds_lower'][:n])
        np.testing.assert_array_equal(left['bounds_upper'],right['bounds_upper'][:n])
        np.testing.assert_array_equal(left['integrality'],right['integrality'][:n])
        assert right['bounds_lower'][n]==0. and np.isposinf(right['bounds_upper'][n])
        index={row['name']:i for i,row in enumerate(right['rows'])}
        for i,row in enumerate(left['rows']):
            j=index[row['name']]
            np.testing.assert_array_equal(left['matrix'].getrow(i).toarray(),right['matrix'].getrow(j).toarray()[:,:n])
            assert right['matrix'][j,n]==0.
            assert left['lower'][i]==right['lower'][j] and left['upper'][i]==right['upper'][j]
        for a,b in zip(left['locks'],right['locks']):
            assert a['upper']==b['upper'] and a['tolerance']==b['tolerance']
            np.testing.assert_array_equal(a['objective'],b['objective'][:n])
    assert max(map(abs,new['energy_balance_residuals']))<=1e-7
    assert all(x['backend']=='gurobipy' for x in new['solver_backend_layers'])


def test_current_critical_service_can_still_require_three_gt(problem):
    module=api();p=copy.deepcopy(problem)
    rated=gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)[0][-1]
    p['loads']=np.zeros((4,3));p['loads'][0,0]=2.5*rated
    p['grid_import_limit_kW']=0.;p['diesel_start_available']=False
    p['state'].gt_prev[:]=0.;p['gt_ramp_kW_per_h']=1e6
    options={'variant':'coupled','regulator_consistent':True,'critical_reserve_multiplier':1.}
    old=numerics.solve_joint(**p,joint_options=options)
    collector=LayerCollector({'test_case':'current_P0_requires_three_GT'})
    with collect_layers(collector):new=module.solve_joint(**p,joint_options={**options,'preserve_one_gt_standby':True})
    collector.export(OUT/'green_critical_three')
    validate_layers(collector)
    assert new['joint_diagnostic']['standby_planned_GT_on_count']==pytest.approx(3.,abs=1e-9)
    assert new['joint_diagnostic']['standby_third_gt_on_excess']==pytest.approx(1.,abs=1e-7)
    np.testing.assert_allclose(new['lex_values'][:2],old['lex_values'][:2],atol=1e-10,rtol=0.)
    assert new['lex_values'][0]==pytest.approx(0.,abs=1e-10)
    assert max(new['action']['gt_electric_kW'])<=rated+1e-7


def test_disabled_and_default_are_exact_v4_canonical_route(saved_baseline):
    p,old,before=saved_baseline;module=api()
    for explicit in (False,None):
        q=copy.deepcopy(p)
        if explicit is not None:q['joint_options']['preserve_one_gt_standby']=False
        collector=LayerCollector({'test_case':'standby_disabled_exact'})
        with collect_layers(collector):new=module.solve_joint(**q)
        assert new['action']==old['action'] and new['lex_values']==old['lex_values']
        assert new['cost_USD_step']==old['cost_USD_step']
        np.testing.assert_array_equal(new['command_disconnected'],old['command_disconnected'])
        assert len(collector.layers)==len(before.layers)
        for a,b in zip(before.layers,collector.layers):
            for field in ('objective','integrality','bounds_lower','bounds_upper','lower','upper'):
                np.testing.assert_array_equal(a[field],b[field])
            np.testing.assert_array_equal(a['matrix'].data,b['matrix'].data)
            np.testing.assert_array_equal(a['matrix'].indices,b['matrix'].indices)
            np.testing.assert_array_equal(a['matrix'].indptr,b['matrix'].indptr)
            assert a['rows']==b['rows']


def test_options_are_current_only_strict_and_coupled():
    module=api()
    assert module._options(None)['preserve_one_gt_standby'] is False
    assert module.STANDBY_CONTRACT_ID=='soft_third_gt_exposure.v1'
    assert module.NUMERICAL_CONTRACT_ID=='unscaled_original_rows.v1'
    for bad in (0,1,None,'true',float('nan')):
        with pytest.raises(ValueError):module._options({'variant':'coupled','preserve_one_gt_standby':bad})
    for variant in ('economic','endurance','diverse'):
        with pytest.raises(ValueError):module._options({'variant':variant,'preserve_one_gt_standby':True})
    for bad in ('future_loads','future_fault_time','scenario_id','source_day_index'):
        with pytest.raises(ValueError):module._options({'variant':'coupled','preserve_one_gt_standby':True,bad:1})
