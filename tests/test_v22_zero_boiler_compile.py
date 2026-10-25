"""A compiled zero steam command cannot be a phantom fixed electric sink."""
import copy
import sys
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_engineering_planner import problem
from test_state_coordinated_planner import context
from hospital_ies.execution import SolverExecutionError
from hospital_ies.model import gt_level_properties
from tools.sci_engineering import planner,policy
from tools.operating_response.physical_balance import solve_physical

def no_sink(problem):
    p=copy.deepcopy(problem);p['row'].outdoor_temperature_C=25.
    p['loads'][:]=0.;p['loads'][0,0]=127.5;p['state'].gt_prev[:]=[128.,0.,0.]
    p['availability'].update(GRID=0.,DIESEL=0.,GT_2=0.,GT_3=0.,ABS=0.,EC_1=0.,EC_2=0.,EC_3=0.)
    p['grid_import_limit_kW']=0.;p['diesel_start_available']=False
    p['config']['_model_closure_v22']={}
    p['config']['_sci_storage_ports']=dict(ups_discharge_kW=0.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,cold_pump_kW_per_kW=.02)
    ready=context(p,gt_ramp_anchor_kW=[128.,0.,0.],gt_ramp_budget_kW=0.,gt_can_stop=[False,True,True])
    ready['arrived_dispatch'].update(boiler_steam_lbph=0.,absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.],diesel_kW=0.)
    return p,ready

def test_zero_command_phantom_is_rejected_without_claiming_global_impossibility(problem):
    p,ready=no_sink(problem)
    with pytest.raises(SolverExecutionError) as caught:
        planner.solve_joint(**p,readiness_context=ready,coordination_options=policy.policy_config('M'))
    error=caught.value
    assert error.category=='command_compilation_domain_exhausted'
    evidence=error.command_compilation_diagnostic
    assert evidence['maximum_refinement_attempts']==1
    assert evidence['positive_command_domain_searched'] is False
    assert evidence['global_physical_infeasibility_claimed'] is False
    assert evidence['rejected_candidate']['command']['boiler_steam_lbph']==0.
    assert 'arrived_current' in evidence['rejected_candidate']['station_truth']['zero_permission_phantom_stages']
    initial=[layer for layer in error.solver_backend_attempts if layer.get('phase')=='initial_rejected_zero']
    assert len(initial)==len(evidence['rejected_candidate']['solver_backend_layers'])
    assert all(layer.get('compiler_candidate_valid') is False and layer['status']==0 for layer in initial)

def test_zero_domain_refinement_can_generate_and_execute_a_legal_GT_stop(problem):
    p,ready=no_sink(problem);p['row'].outdoor_temperature_C=12.416666666666666
    rated=gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)[0][-1]
    p['availability']['GT_1']=128./(.4*rated)
    ready.update(gt_ramp_budget_kW=225.,gt_can_stop=[True,True,True])
    result=planner.solve_joint(**p,readiness_context=ready,coordination_options=policy.policy_config('M'))
    assert result['action']['gt_electric_kW']==[0.,0.,0.]
    assert result['action']['boiler_steam_lbph']==0.
    diagnostic=result['joint_diagnostic'];refinement=diagnostic['m22_boiler_zero_domain_refinement']
    assert refinement['status']=='valid_zero_command_domain_refinement'
    assert refinement['full_priority_hierarchy_reoptimized'] and not refinement['prior_candidate_locks_reused']
    assert not diagnostic['m22_boiler_command_truth']['zero_permission_phantom_stages']
    initial=[layer for layer in result['solver_backend_layers'] if layer.get('phase')=='initial_rejected_zero']
    refined=[layer for layer in result['solver_backend_layers'] if layer.get('phase')=='zero_domain_refinement']
    original=refinement['rejected_candidate']['solver_backend_layers']
    assert len(initial)==len(original) and refined
    for saved,raw in zip(initial,original):
        assert saved['compiler_candidate_valid'] is False
        assert all(saved.get(key)==raw.get(key) for key in ('rank','status','attempts','elapsed_seconds'))
    assert all(layer['compiler_candidate_valid'] is True for layer in refined)
    # Apply the generated command through the shared ordinary arrival/clamp
    # executor, then account its actual terminal power with the M22 balance.
    from test_essential_execution import small_core
    core,_,_,_=small_core(gt=128.,load=127.5,budget=225.,anchor=128.)
    core.env.config.update(grid_import_limit_kW=0.,_model_closure_v22={})
    core.data_at=lambda minute:(1,p['row'],p['loads'].copy(),dict(p['availability']))
    core.executed['grid_import_kW']=0.
    core.pending_commands.append(dict(id=1,generated_minute=core.time,arrival_minute=core.time,dispatch=copy.deepcopy(result['action']),
        disconnected=np.asarray(result['command_disconnected']).tolist(),reference_cooling_kW=0.,weight=0.,request={}))
    core.boundary()
    assert core.executed['gt_electric_kW']==[0.,0.,0.]
    assert any(event['kind']=='command_executed' for event in core.events)
    actual=solve_physical(**dict(p,state=core.state),execution_action=core.executed)
    assert actual['action']['boiler_auxiliary_kW']==0.
    assert actual['lost'][0,0]==pytest.approx(127.5,abs=1e-4)

def test_positive_steam_keeps_its_real_fixed_and_variable_station_load(problem):
    p,ready=no_sink(problem);p['loads'][0]=[140.,1000.,0.]
    p['availability']['GRID']=1.;p['grid_import_limit_kW']=500.
    result=planner.solve_joint(**p,readiness_context=ready,coordination_options=policy.policy_config('M'))
    assert result['action']['boiler_steam_lbph']>1.
    actual=solve_physical(**p,execution_action=result['action'])
    assert actual['action']['boiler_auxiliary_kW']>.5
    assert np.max(actual['lost'])<1e-3

def test_zero_new_permission_revokes_boiler_restart_but_not_old_held_permission(problem):
    p,ready=no_sink(problem)
    p['config']['_model_closure_v22'].update(boiler_ready=False,boiler_restart_kW=.5)
    ready['arrived_dispatch']['boiler_steam_lbph']=1000.
    with pytest.raises(SolverExecutionError) as caught:
        planner.solve_joint(**p,readiness_context=ready,coordination_options=policy.policy_config('M'))
    assert caught.value.category=='command_compilation_domain_exhausted'
    p['row'].outdoor_temperature_C=12.416666666666666
    rated=gt_level_properties(p['plant'],p['row'].outdoor_temperature_C)[0][-1]
    p['availability']['GT_1']=128./(.4*rated)
    ready.update(gt_ramp_budget_kW=225.,gt_can_stop=[True,True,True])
    result=planner.solve_joint(**p,readiness_context=ready,coordination_options=policy.policy_config('M'))
    assert result['action']['boiler_steam_lbph']==0.
    assert result['planned_current_action']['boiler_restart_electric_kW']==0.
    truth=result['joint_diagnostic']['m22_boiler_command_truth']
    assert not truth['zero_permission_phantom_stages']
    assert truth['stages']['held_actual']['steam_permission_lbph']==1000.
    assert truth['stages']['held_actual']['restart_power_kW']==.5
