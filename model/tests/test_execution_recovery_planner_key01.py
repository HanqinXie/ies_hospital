"""Actual failed M18 observations, replayed without loading annual inputs."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
import numpy as np
import pandas as pd
import pytest
from hospital_ies.model import Plant
from hospital_ies.resilience import RuntimeState

FIXTURES=ROOT/'outputs/v18_execution_recovery_20261018_run01/review/key01_c18_finalize'


def saved_inputs(batch,fixture_root=FIXTURES):
    fixture=json.loads((fixture_root/batch/'fixture.json').read_text(encoding='utf-8'))
    d=fixture['diagnostic'];o=fixture['observation'];s=copy.deepcopy(d['state'])
    s['gt_prev']=np.asarray(s['gt_prev']);s['disconnected_loads']=np.asarray(s['disconnected_loads'])
    s['extra_faulted']=set(s['extra_faulted'])
    context=copy.deepcopy(o['operating_readiness'])
    context['diesel_local_response']=o['diesel_local_response'];context['diesel_start_minute']=s['diesel_start_minute']
    q=o['requested_command']['state_coordinated_joint'];cfg=d['config']
    return dict(row=SimpleNamespace(**{**d['row'],'timestamp':pd.Timestamp(d['row']['timestamp'])}),
        plant=Plant(**d['plant']),loads=d['loads_kW'],availability=d['availability'],state=RuntimeState(**s),dt_h=d['dt_h'],
        grid_import_limit_kW=cfg['grid_import_limit_kW'],diesel_start_available=True,
        gt_ramp_kW_per_h=cfg['gt_ramp_kW_per_hour'],solver_time_limit_sec=cfg['solver_time_limit_sec'],config=cfg,
        execution_contract=d['execution_contract'],joint_options=q['options'],risk_loads=q.get('risk_loads_kW'),
        coordination_options=q['coordination'],readiness_context=context,execution_limits=o['execution_limits'])


@pytest.mark.parametrize('batch',['key01_000','key01_001'])
def test_only_queued_coordinates_are_finalized(batch):
    from tools.execution_recovery import planner
    result=planner.solve_joint(**saved_inputs(batch))
    record=result['joint_diagnostic']['engineering']['command_finalization']
    assert record['exact_finite_nonnegative_dispatch_validated']
    assert result['action']['diesel_kW']>=0.
    assert all(c['name']!='diesel.electric'for c in record['corrections'])
    assert all(abs(x['delta'])<=1e-10 for x in record['objective_deltas'])


@pytest.mark.parametrize('recovery',[False,True])
def test_recovered_hardware_can_receive_conditional_import_without_current_grid_supply(recovery):
    from tools.execution_recovery import planner
    kwargs=saved_inputs('key01_000')
    kwargs['coordination_options']['execution_recovery']=recovery
    result=planner.solve_joint(**kwargs)
    assert result['action']['grid_import_kW']>0.
    assert result['planned_current_action']['grid_import_kW']==0.
    proof=result['joint_diagnostic']['engineering']['m18_conditional_return_permission']
    assert proof['all_non_ceiling_variables_unchanged']


@pytest.mark.parametrize('batch',['key01_000','key01_001'])
def test_c4_compatibility_keeps_service_locks_through_arrived_p3_layer(batch):
    from tools.execution_recovery import planner
    kwargs=saved_inputs(batch,ROOT/'outputs/v18_execution_recovery_20261018_run01/review/key01_c4_solver')
    assert kwargs['coordination_options']['execution_recovery'] is False
    result=planner.solve_joint(**kwargs)
    layers=result['joint_diagnostic']['layers']
    assert any(layer['name']=='arrived_current_P3' for layer in layers)
    engineering=result['joint_diagnostic']['engineering']
    assert engineering['command_finalization']['exact_finite_nonnegative_dispatch_validated']
    if engineering['late_soft_optimization_stopped']:
        assert engineering['cost_optimum_USD_step'] is None
        fallback=next(x for x in result['joint_diagnostic']['late_preference_fallbacks']
            if x.get('stage')=='late_soft_after_complete_service')
        assert fallback['all_current_and_arrived_service_locks_complete']
        assert fallback['optional_layer_optimum_claimed'] is False
        assert fallback['new_optimum_lock_added'] is False
    else:
        # The historical test required the old numerical failure to recur.
        # A strict same-layer refinement may now finish the later objectives.
        # All physical/service/finalizer assertions above remain unchanged.
        recoveries=[x for x in result['joint_diagnostic']['numerical_presentation_recoveries'] if x.get('accepted')]
        assert recoveries and engineering['cost_optimum_USD_step'] is not None
        assert {'standby_third_GT_exposure','normal_online_GT_count_after_preparation',
            'operating_cost_and_original_preferences','actual_GT_combination_changes',
            'actual_GT_power_change','healthy_converter_permission','held_actual_diagnostic'} <= {x['name'] for x in layers}
        for recovered in recoveries:
            assert recovered['exact_optimum_claimed'] is False
            assert recovered['objective_consistency_passed']
            assert recovered['relative_gap']<=1e-6
            assert recovered['validation']['max_original_violation']<=1e-7
            assert recovered['validation']['max_lock_violation']<=1e-10
            assert recovered['validation']['max_integrality_distance']<=1e-9
        for fallback in result['joint_diagnostic']['late_preference_fallbacks']:
            assert fallback['optional_layer_optimum_claimed'] is False


def test_late_soft_fallback_is_forbidden_before_every_service_lock():
    from tools.execution_recovery.planner import late_soft_service_locks_complete
    rows=[dict(name='lock.'+phase+'_'+g,kind='priority_lock')
        for phase in ('current','arrived_current') for g in ('P0','P1','P2','P3')]
    assert late_soft_service_locks_complete('standby_third_GT_exposure',rows,('arrived_current',))
    assert not late_soft_service_locks_complete('standby_third_GT_exposure',rows[:-1],('arrived_current',))
    assert not late_soft_service_locks_complete('current_P0',rows,('arrived_current',))
    assert not late_soft_service_locks_complete('preparation_P0',rows,('arrived_current',))
