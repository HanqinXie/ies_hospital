"""Explicit commanded contacts versus numerical unmet service, no episodes."""
import copy
import importlib

import numpy as np
import pytest

from test_autonomous_dispatch_planner import problem, diesel_only, planner
from hospital_ies.model import gt_level_properties


def api():
    try:
        return importlib.import_module('tools.autonomous_dispatch.planner_connections').solve_joint
    except ModuleNotFoundError:
        pytest.fail('Versioned command-connection planner is missing')


def test_retained_contacts_do_not_turn_unserved_service_into_delivered_energy(problem):
    p=diesel_only(problem, critical=(80.,20.), low=(200.,200.))
    low=np.zeros((4,3));low[:,0]=[20.,10.,60.,70.]
    r=api()(**p, joint_options={'variant':'coupled'}, risk_loads=low,
            connection_mode='retain_when_no_gt', retention_eligible=True)
    assert r['joint_diagnostic']['connection_gate_active']
    assert r['lost'][2:,0].sum()>300.
    np.testing.assert_array_equal(r['command_disconnected'][2:],np.zeros((2,3)))
    np.testing.assert_allclose(r['command_disconnected'][:2],r['lost'][:2],atol=0.,rtol=0.)
    assert r['planned_current_action']['diesel_kW']==pytest.approx(r['served'][:,0].sum(),abs=1e-4)
    assert max(map(abs,r['energy_balance_residuals']))<1e-4


def test_risk_copies_use_commanded_contacts_not_numerical_low_priority_loss(problem):
    p=diesel_only(problem, critical=(80.,20.), low=(200.,200.))
    low=np.zeros((4,3));low[:,0]=[20.,10.,60.,70.]
    old=planner()(**p,joint_options={'variant':'coupled'},risk_loads=low)
    new=api()(**p,joint_options={'variant':'coupled'},risk_loads=low,
              connection_mode='retain_when_no_gt',retention_eligible=True)
    assert old['joint_diagnostic']['risk_scenarios']['low_history']['electric_surplus_defect_kW']>60.
    assert new['joint_diagnostic']['risk_scenarios']['low_history']['electric_surplus_defect_kW']<1e-4
    assert new['lost'][:2].sum()<1e-3
    assert new['lost'][2:,0].sum()>300.
    np.testing.assert_array_equal(new['command_disconnected'][2:],np.zeros((2,3)))


def test_idle_available_gt_can_retain_only_if_new_command_does_not_start_it(problem):
    p=diesel_only(problem,critical=(10.,10.),low=(20.,20.),energy=60.)
    p['availability']['GT_1']=1.
    low=np.zeros((4,3));low[:,0]=[2.,2.,5.,5.]
    r=api()(**p,joint_options={'variant':'coupled','connection_mode':'retain_when_no_gt'},
            risk_loads=low,retention_eligible=True)
    assert sum(r['action']['gt_electric_kW'])<1e-4
    assert r['lost'][2:,0].sum()>20.
    assert r['joint_diagnostic']['connection_gate_active']
    np.testing.assert_array_equal(r['command_disconnected'][2:],np.zeros((2,3)))


def test_retention_requires_current_ledger_eligibility_even_if_all_gt_off(problem):
    p=diesel_only(problem,critical=(80.,20.),low=(200.,200.))
    low=p['loads']*.3
    r=api()(**p,joint_options={'variant':'coupled'},risk_loads=low,
            connection_mode='retain_when_no_gt')
    assert not r['joint_diagnostic']['connection_gate_active']
    np.testing.assert_allclose(r['command_disconnected'],r['lost'],atol=1e-7,rtol=0.)


def test_default_connection_mode_preserves_frozen_planner(problem):
    p=diesel_only(problem)
    old=planner()(**p,joint_options={'variant':'coupled'},risk_loads=p['loads']*.5)
    new=api()(**p,joint_options={'variant':'coupled'},risk_loads=p['loads']*.5)
    np.testing.assert_allclose(new['lex_values'],old['lex_values'],atol=1e-7,rtol=0.)
    assert new['cost_USD_step']==pytest.approx(old['cost_USD_step'],abs=1e-5)
    np.testing.assert_array_equal(new['command_disconnected'],new['lost'])
    assert not new['joint_diagnostic']['connection_gate_active']


@pytest.mark.parametrize('mode',['always_reconnect','future_GT_recovery',None])
def test_unknown_connection_mode_rejected(problem,mode):
    if mode is None:
        with pytest.raises(ValueError):api()(**problem,joint_options={'connection_mode':None})
    else:
        with pytest.raises(ValueError):api()(**problem,connection_mode=mode)


def test_running_gt_cannot_be_certified_eligible_by_caller(problem):
    with pytest.raises(ValueError):
        api()(**problem,retention_eligible=True,connection_mode='retain_when_no_gt')


@pytest.mark.parametrize('options',[
    {'future_GT_recovery_minute':30.},
    {'connection_mode':'always_reconnect'},
    {'connection_mode':'retain_when_no_gt','support_hours':'2'},
])
def test_new_mode_rejects_future_or_malformed_options(problem,options):
    with pytest.raises(ValueError):api()(**problem,joint_options=options)


def regulated(problem):
    p=copy.deepcopy(problem)
    p['execution_contract']['gt_local_down_rate_kW_per_hour']=900.
    p['execution_contract']['gt_local_down_delay_minutes']=.25
    return p


def test_regulator_grid_loss_uses_same_on_ports_at_stable_electric_and_hrsg_floor(problem):
    p=regulated(problem)
    p['state'].gt_prev[:]=300.;p['gt_ramp_kW_per_h']=10.
    p['loads']=np.array([[500.,30.,0.],[400.,20.,0.],[0.,0.,0.],[0.,0.,0.]])
    p['availability'].update(DIESEL=0.,HRSG_1=.3,HRSG_2=.6,HRSG_3=1.)
    r=api()(**p,joint_options={'variant':'diverse','regulator_consistent':True})
    d=r['joint_diagnostic'];risk=d['risk_scenarios']['GRID_loss']
    power,steam,_=gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)
    floor=.4*float(power[-1])*np.array([p['availability'][f'GT_{i}'] for i in (1,2,3)])
    expected=floor*np.array(d['command_GT_on'])
    assert d['regulator_pressure_active']
    assert np.all(np.array(r['action']['gt_electric_kW'])>expected+100.)
    np.testing.assert_allclose(risk['gt_electric_kW'],expected,atol=1e-6,rtol=0.)
    assert risk['hrsg_steam_lbph']==pytest.approx(expected@np.array([.3,.6,1.])*float(steam[-1]/power[-1]))
    assert risk['GT_source_basis']=='current_command_on_at_existing_stable_floor'
    assert max(map(abs,risk['balance_residuals']))<1e-4


def test_regulator_soft_excess_preserves_original_current_p0_p1_optimum(problem):
    p=regulated(problem)
    p['grid_import_limit_kW']=0.;p['availability']['DIESEL']=0.
    p['loads']=np.zeros((4,3));p['loads'][:2,0]=[500.,200.]
    old=planner()(**p,joint_options={'variant':'diverse'})
    new=api()(**p,joint_options={'variant':'diverse','regulator_consistent':True})
    np.testing.assert_allclose(new['lex_values'][:2],old['lex_values'][:2],atol=1e-7,rtol=0.)
    assert new['joint_diagnostic']['regulator_excess_kW']>100.
    names=[x['name'] for x in new['joint_diagnostic']['layers']]
    assert names.index('preparation_P1')<names.index('regulator_GT_excess')<names.index('current_P2')
    assert max(map(abs,new['energy_balance_residuals']))<1e-4


def test_regulator_disabled_preserves_original_retention_route_exactly(problem):
    p=regulated(diesel_only(problem));floor=p['loads']*.3
    a=api()(**p,joint_options={'variant':'coupled','connection_mode':'retain_when_no_gt'},
            risk_loads=floor,retention_eligible=True)
    b=api()(**p,joint_options={'variant':'coupled','connection_mode':'retain_when_no_gt','regulator_consistent':False},
            risk_loads=floor,retention_eligible=True)
    assert a['action']==b['action']
    assert a['lex_values']==b['lex_values']
    assert a['cost_USD_step']==b['cost_USD_step']
    np.testing.assert_array_equal(a['command_disconnected'],b['command_disconnected'])
    assert not b['joint_diagnostic']['regulator_pressure_active']
    with pytest.raises(ValueError):api()(**p,joint_options={'regulator_consistent':'true'})


@pytest.mark.parametrize('inactive',['GRID_unavailable','fixed_contract','local_down_disabled'])
def test_regulator_has_no_extra_layer_outside_existing_local_down_contract(problem,inactive):
    p=regulated(problem)
    if inactive=='GRID_unavailable':p['availability']['GRID']=0.
    elif inactive=='fixed_contract':p['execution_contract']['grid_import_mode']='fixed'
    else:p['execution_contract']['gt_local_down_rate_kW_per_hour']=0.
    old=api()(**p,joint_options={'variant':'diverse'})
    new=api()(**p,joint_options={'variant':'diverse','regulator_consistent':True})
    assert not new['joint_diagnostic']['regulator_pressure_active']
    assert all(x['name']!='regulator_GT_excess' for x in new['joint_diagnostic']['layers'])
    np.testing.assert_allclose(new['lex_values'],old['lex_values'],atol=1e-7,rtol=0.)
    assert new['cost_USD_step']==pytest.approx(old['cost_USD_step'],abs=1e-5)
    np.testing.assert_allclose(new['joint_diagnostic']['risk_scenarios']['GRID_loss']['gt_electric_kW'],
                               new['action']['gt_electric_kW'],atol=1e-6,rtol=0.)
