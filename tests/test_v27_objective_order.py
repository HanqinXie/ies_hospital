"""One optional GT-count preference moves behind the unchanged cost layer."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_engineering_planner import problem
from test_state_coordinated_planner import context
from tools.sci_engineering import planner,policy

COUNT='normal_online_GT_count_after_preparation'
COST='operating_cost_and_original_preferences'


def solve(p,order=None,**kwargs):
    overrides={} if order is None else {'online_GT_count_order':order}
    return planner.solve_joint(**copy.deepcopy(p),readiness_context=context(p),
        coordination_options=policy.policy_config('C',overrides),**kwargs)


def diagnostic(result):
    return result['joint_diagnostic']


def test_legacy_is_explicit_default_and_invalid_orders_are_rejected():
    assert policy.policy_config('C')['online_GT_count_order']=='legacy_before_cost'
    with pytest.raises(ValueError):policy.policy_config('C',{'online_GT_count_order':'future_cheapest'})
    with pytest.raises(ValueError):policy.policy_config('C',{'online_GT_count_order':True})


def test_explicit_legacy_keeps_current_solver_order_and_dispatch(problem):
    implicit=solve(problem);explicit=solve(problem,'legacy_before_cost')
    assert diagnostic(implicit)['objective_order']==diagnostic(explicit)['objective_order']
    for key,value in implicit['action'].items():
        np.testing.assert_array_equal(value,explicit['action'][key])
    assert diagnostic(implicit)['objective_order'].index(COUNT)<diagnostic(implicit)['objective_order'].index(COST)


def test_active_C_route_moves_only_online_count_and_keeps_earlier_service_locks(problem):
    old=solve(problem);new=solve(problem,'after_cost')
    od=diagnostic(old);nd=diagnostic(new)
    assert [x for x in od['objective_order'] if x!=COUNT]==[x for x in nd['objective_order'] if x!=COUNT]
    order=nd['objective_order'];assert order.index(COST)<order.index(COUNT)<order.index('actual_GT_combination_changes')
    old_layers={x['name']:x for x in od['layers']};new_layers={x['name']:x for x in nd['layers']}
    for name in od['objective_order'][:od['objective_order'].index(COUNT)]:
        assert old_layers[name]['unit']==new_layers[name]['unit']
        assert old_layers[name].get('lock_tolerance')==new_layers[name].get('lock_tolerance')
    assert nd['engineering']['online_GT_count_order']['only_online_count_layer_moved'] is True


def test_after_cost_count_cannot_undo_cost_when_continuity_tie_break_is_disabled(problem):
    result=solve(problem,'after_cost',engineering_options={'continuity':False})
    d=diagnostic(result);cost=next(x for x in d['layers'] if x['name']==COST)
    assert cost['lock_tolerance']==planner.ENGINEERING_DEFAULTS['cost_equivalence_USD_per_step']
    assert cost['final_objective_value']<=cost['lock_upper']+1e-10
    assert d['objective_order'].index(COST)<d['objective_order'].index(COUNT)


def test_failed_new_post_cost_count_keeps_accepted_cost_incumbent_and_logs_failure(problem,monkeypatch):
    original=planner.solve_layer;failed=[]
    def fail_first_post_cost(*args,**kwargs):
        if not failed and any(x.get('name')=='lock.'+COST for x in kwargs['rows']):
            failed.append(True)
            return SimpleNamespace(status=4,x=None,message='injected post-cost preference numerical failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(planner,'solve_layer',fail_first_post_cost)
    result=solve(problem,'after_cost');d=diagnostic(result)
    assert failed
    layer=next(x for x in d['layers'] if x['name']==COUNT)
    assert layer['optimization_status']=='not_optimized_reused_preceding_feasible_incumbent'
    assert layer['solver_status']==4 and layer['fallback_used']
    assert 'lock_upper' not in layer
    records=d['late_preference_fallbacks']
    record=next(x for x in records if x['layer']==COUNT)
    assert record['source_layer']==COST
    assert record['validation']['all_original_rows_bounds_and_locks_checked']
    cost=next(x for x in d['layers'] if x['name']==COST)
    assert cost['final_objective_value']<=cost['lock_upper']+1e-10
