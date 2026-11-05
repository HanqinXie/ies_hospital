"""Static registered critical envelope; no annual data or trajectory runs."""
import copy
import importlib

import numpy as np
import pytest

from test_autonomous_dispatch_planner import problem
from hospital_ies.model import gt_level_properties


def api():
    try:
        return importlib.import_module('tools.autonomous_dispatch.planner_margin').solve_joint
    except ModuleNotFoundError:
        pytest.fail('Independent static-envelope planner is missing')


def test_default_multiplier_delegates_exact_v2_numerical_route(problem):
    base=importlib.import_module('tools.autonomous_dispatch.planner_connections').solve_joint
    options={'variant':'coupled','regulator_consistent':True,'connection_mode':'retain_when_no_gt'}
    floor=problem['loads']*.6
    old=base(**problem,joint_options=options,risk_loads=floor)
    new=api()(**problem,joint_options=options,risk_loads=floor)
    assert new['action']==old['action']
    assert new['lex_values']==old['lex_values']
    assert new['cost_USD_step']==old['cost_USD_step']
    np.testing.assert_array_equal(new['command_disconnected'],old['command_disconnected'])
    assert new['joint_diagnostic']['critical_reserve_multiplier']==1.


@pytest.mark.parametrize('value',[True,1,'1.25',float('nan'),float('inf'),.99,2.01])
def test_malformed_or_out_of_range_multiplier_rejected(problem,value):
    with pytest.raises(ValueError):
        api()(**problem,joint_options={'variant':'coupled','critical_reserve_multiplier':value})


@pytest.mark.parametrize('variant',['economic','endurance'])
def test_multiplier_above_one_rejects_variants_without_static_copies(problem,variant):
    with pytest.raises(ValueError):
        api()(**problem,joint_options={'variant':variant,'critical_reserve_multiplier':1.25})


def test_envelope_never_accepts_future_parameters(problem):
    with pytest.raises(ValueError):
        api()(**problem,joint_options={'variant':'coupled','critical_reserve_multiplier':1.25,
                                      'future_load_multiplier':2.})


@pytest.mark.parametrize('multiplier',[1.25,1.5])
def test_static_envelope_changes_only_critical_risk_demands_and_preserves_physics(problem,multiplier):
    p=copy.deepcopy(problem);p['loads']*=20.
    p['execution_contract']['gt_local_down_rate_kW_per_hour']=900.
    p['execution_contract']['gt_local_down_delay_minutes']=.25
    options={'variant':'coupled','regulator_consistent':True,'connection_mode':'retain_when_no_gt'}
    floor=p['loads']*.5
    old=api()(**p,joint_options=options,risk_loads=floor)
    new=api()(**p,joint_options={**options,'critical_reserve_multiplier':multiplier},risk_loads=floor)
    np.testing.assert_allclose(new['lex_values'][:2],old['lex_values'][:2],atol=1e-7,rtol=0.)
    assert max(map(abs,new['energy_balance_residuals']))<1e-4
    assert max(new['action']['gt_electric_kW'])<=gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)[0][-1]+1e-5
    assert new['action']['boiler_steam_lbph']<=p['plant'].boiler_steam_lbph+1e-5
    assert new['action']['diesel_kW']<=min(p['plant'].emergency_diesel_kW,
                                        p['state'].diesel_energy_remaining_kWh/p['dt_h'])+1e-5
    assert new['action']['grid_import_kW']<=p['grid_import_limit_kW']+1e-5
    expected=p['loads'].copy();expected[:2]*=multiplier
    for label in ('GRID_loss','GT_common_loss'):
        side=new['joint_diagnostic']['risk_scenarios'][label]
        np.testing.assert_array_equal(side['loads_kW'],expected)
        assert max(map(abs,side['balance_residuals']))<1e-4
        ratio=np.divide(expected,p['loads'],out=np.zeros((4,3)),where=p['loads']>0)
        assert np.all(np.asarray(side['unserved_kW'])>=new['command_disconnected']*ratio-1e-4)
    common=new['joint_diagnostic']['risk_scenarios']['GT_common_loss']
    assert common['gt_electric_kW']==pytest.approx([0.,0.,0.])
    assert common['hrsg_steam_lbph']==pytest.approx(0.)
    np.testing.assert_array_equal(new['joint_diagnostic']['risk_scenarios']['low_history']['loads_kW'],floor)
    assert new['joint_diagnostic']['critical_envelope_is_prediction'] is False
    assert all(x['backend']=='gurobipy' for x in new['solver_backend_layers'])
