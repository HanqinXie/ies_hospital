"""Synthetic mathematical fixtures only; no hospital episode or C/M input."""
from pathlib import Path
import copy
import importlib.util
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]


def api():
    path = ROOT/'tools/model_reference_v32/planner.py'
    assert path.is_file(), 'Missing independent V32 priority planning module'
    spec = importlib.util.spec_from_file_location('v32_planner_under_test',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tiny(demands=None, durations=None):
    demands = demands or [[[1.,0.,0.],[1.,0.,0.],[0.,0.,0.],[0.,0.,0.]]]
    n = len(demands)
    return dict(schema='hospital_ies.reference_v32.spec.v1',evidence_class='synthetic_mathematical_fixture',
        information_permission='offline_known_window',duration_h=durations or [1.]*n,
        original_demand_by_priority_kW=demands,
        demand_e=[sum(p[0] for p in d) for d in demands],
        demand_h=[sum(p[1] for p in d) for d in demands],
        demand_c=[sum(p[2] for p in d) for d in demands],
        grid_capacity=[2.]*n,grid_price=[3.]*n,generators=[],
        boiler=dict(capacity=0.,fuel_slope=0.,fuel_type='gas'),
        electric_chillers=[],absorption=dict(capacity=0.,heat_per_c=1.,electric_per_c=0.,tower_per_c=0.),
        tower_capacity=[10.]*n,storages=[],fuel_budgets={},fuel_prices={'gas':1.})


def storage(initial=1.):
    return dict(name='ups',carrier='e',capacity=2.,power=2.,initial=initial,minimum=0.,
        charge_efficiency=1.,discharge_efficiency=1.,decay_per_h=0.,loss_mode='none',
        charge_allowed=[False],discharge_allowed=[True],
        discharge_scope='building_critical_only',eligible_priorities=[0,1])


def test_strict_feasible_plan_has_independent_energy_fee_and_priority_gate():
    m = api(); spec = tiny()
    result = m.solve_reference(spec,formulation='strict',time_limit_s=5.)
    assert result['status']=='optimal'
    assert result['verified_incumbent'] and result['adoptable']
    assert result['objective_fee']==pytest.approx(6.,abs=1e-8)
    assert result['plan']['grid']==pytest.approx([2.])
    assert result['audit']['valid']
    assert result['audit']['service']['P0']['e']['ENS_kWh']==0.
    assert result['audit']['service']['P1']['e']['requested_kWh']==1.


def test_strict_infeasibility_never_becomes_unknown_or_a_zero_plan():
    m = api(); spec = tiny();spec['grid_capacity']=[1.]
    result = m.solve_reference(spec,formulation='strict',time_limit_s=5.)
    assert result['status']=='infeasible'
    assert result['termination_reason']=='proved_infeasible'
    assert result['plan'] is None and result['objective_fee'] is None
    assert not result['verified_incumbent'] and not result['adoptable']


def test_tiered_serves_P0_before_P1_and_fee_without_a_big_penalty():
    m = api();spec=tiny();spec['grid_capacity']=[1.];spec['grid_price']=[1e6]
    result=m.solve_reference(spec,formulation='tiered',time_limit_s=5.)
    assert result['status']=='optimal' and result['adoptable']
    assert result['plan']['unmet.P0.e']==pytest.approx([0.],abs=2e-9)
    assert result['plan']['unmet.P1.e']==pytest.approx([1.],abs=2e-9)
    assert result['objective_fee']==pytest.approx(1e6,abs=0.01)
    assert [layer['name'] for layer in result['layers']]==['P0','P1','P2','P3','fee']
    assert result['lexicographic_optimality_proven']


def test_carrier_normalization_uses_fixed_full_window_positive_demand():
    m=api();spec=tiny([[[100.,1.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]])
    spec['grid_capacity']=[0.]
    spec['generators']=[dict(name='E',capacity=100.,min_power=0.,fuel_type='gas',fuel_slope=1.,fuel_fixed=0.,heat_slope=0.,heat_fixed=0.)]
    spec['boiler']=dict(capacity=1.,fuel_type='gas',fuel_slope=1.)
    spec['fuel_budgets']={'gas':1.} # Explicit synthetic constraint, not hospital gas inventory.
    result=m.solve_reference(spec,formulation='tiered',time_limit_s=5.)
    denom=result['normalization']['denominators_kWh']['P0']
    assert denom=={'e':100.,'h':1.,'c':0.}
    assert result['normalization']['zero_demand_contribution']=='omitted'
    assert result['plan']['boiler']==pytest.approx([1.],abs=2e-8)
    assert result['audit']['service']['P0']['h']['ENS_kWh']==pytest.approx(0.,abs=2e-9)
    assert result['audit']['service']['P0']['e']['ENS_kWh']==pytest.approx(100.,abs=2e-7)
    assert 'total_cross_carrier_ENS' not in result['audit']


def test_multiperiod_ups_efficiency_initial_and_terminal_are_not_gifted():
    m=api();spec=tiny([[[0.,0.,0.]]*4,[[1.,0.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]])
    spec.update(grid_capacity=[2.,0.],grid_price=[1.,10.])
    ups=storage(initial=0.);ups.update(charge_efficiency=0.5,charge_allowed=[True,False],discharge_allowed=[False,True])
    spec['storages']=[ups]
    result=m.solve_reference(spec,formulation='strict',time_limit_s=5.)
    assert result['adoptable'],result
    assert result['plan']['ups.charge']==pytest.approx([2.,0.])
    assert result['plan']['ups.discharge']==pytest.approx([0.,1.])
    assert result['plan']['ups.stock']==pytest.approx([1.,0.])
    assert result['objective_fee']==pytest.approx(2.)


def test_ups_cannot_supply_general_load_when_critical_demand_is_zero():
    m=api();spec=tiny([[[0.,0.,0.],[0.,0.,0.],[0.,0.,0.],[1.,0.,0.]]])
    spec.update(grid_capacity=[0.],storages=[storage()])
    result=m.solve_reference(spec,formulation='tiered',time_limit_s=5.)
    assert result['adoptable']
    assert result['plan']['ups.discharge']==pytest.approx([0.],abs=1e-9)
    assert result['plan']['ups.stock']==pytest.approx([1.])
    assert result['audit']['service']['P3']['e']['ENS_kWh']==pytest.approx(1.)


def test_independent_audit_catches_ups_to_unserved_critical_relabeling():
    m=api();spec=tiny([[[1.,0.,0.],[0.,0.,0.],[0.,0.,0.],[1.,0.,0.]]])
    spec.update(grid_capacity=[0.],storages=[storage()])
    result=m.solve_reference(spec,formulation='tiered',time_limit_s=5.)
    bad=copy.deepcopy(result['plan']);bad['unmet.P0.e']=[1.];bad['unmet.P3.e']=[0.]
    audit=m.audit_plan(spec,bad,formulation='tiered')
    assert not audit['valid']
    assert any('critical' in v['check'] for v in audit['violations'])


def test_tower_capacity_is_independently_binding_not_only_chiller_nameplate():
    m=api();spec=tiny([[[0.,0.,1.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]])
    spec['electric_chillers']=[dict(name='EC',capacity=2.,electric_per_c=1.,tower_per_c=2.)]
    spec['tower_capacity']=[1.]
    result=m.solve_reference(spec,formulation='tiered',time_limit_s=5.)
    assert result['plan']['EC']==pytest.approx([0.5],abs=1e-8)
    assert result['audit']['service']['P0']['c']['ENS_kWh']==pytest.approx(0.5,abs=1e-8)
    bad=copy.deepcopy(result['plan']);bad['EC']=[1.]
    assert not m.audit_plan(spec,bad,formulation='tiered')['valid']


def test_diesel_shaft_budget_and_inventory_corruption_are_independently_checked():
    m=api();spec=tiny();spec['grid_capacity']=[0.]
    spec['generators']=[dict(name='DG',capacity=2.,min_power=0.,fuel_type='diesel_shaft',fuel_slope=1.,fuel_fixed=0.,heat_slope=0.,heat_fixed=0.)]
    spec['fuel_budgets']={'diesel_shaft':1.};spec['fuel_prices']['diesel_shaft']=0.3
    result=m.solve_reference(spec,formulation='tiered',time_limit_s=5.)
    assert result['audit']['fuel_used']['diesel_shaft']==pytest.approx(1.)
    bad=copy.deepcopy(result['plan']);bad['DG']=[2.]
    assert any('fuel_budget' in v['check'] for v in m.audit_plan(spec,bad,formulation='tiered')['violations'])
    ss=tiny([[[1.,0.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]]);ss.update(grid_capacity=[0.],storages=[storage()])
    ok=m.solve_reference(ss,formulation='strict',time_limit_s=5.)
    bad=copy.deepcopy(ok['plan']);bad['ups.stock'][0]+=0.1
    assert not m.audit_plan(ss,bad,formulation='strict')['valid']


def test_termination_distinguishes_limit_incumbent_absence_and_proof():
    m=api()
    assert m.classify_termination(SimpleNamespace(status=1,x=None))['status']=='no_incumbent'
    assert m.classify_termination(SimpleNamespace(status=1,x=[1.]))['status']=='time_limit'
    assert m.classify_termination(SimpleNamespace(status=2,x=None))['termination_reason']=='proved_infeasible'
    assert not m.classify_termination(SimpleNamespace(status=0,x=[float('nan')]))['incumbent']


def test_pair_gate_keeps_strict_limit_incumbent_and_unknown_infeasibility_separate():
    m=api();good=dict(formulation='strict',status='optimal',verified_incumbent=True,adoptable=True)
    limit=dict(good,status='time_limit');unknown=dict(formulation='strict',status='no_incumbent',verified_incumbent=False,adoptable=False)
    infeasible=dict(unknown,status='infeasible',termination_reason='proved_infeasible')
    assert m.select_common_formulation(good,limit)['selected_formulation']=='strict'
    assert m.select_common_formulation(good,infeasible)['selected_formulation']=='tiered'
    assert m.select_common_formulation(good,unknown)['selected_formulation'] is None
    selection=m.select_common_formulation(good,unknown,allow_unknown_tiered=True)
    assert selection['selected_formulation']=='tiered' and selection['strict_infeasibility_proven'] is False


def test_source_and_aggregate_boundary_rejects_trace_input_and_double_counting():
    m=api();spec=tiny();spec['demand_e']=[3.]
    with pytest.raises(ValueError,match='priority|aggregate'):m.validate_spec(spec)
    spec=tiny();spec['evidence_class']='historical_C_conditioned_static_planning_envelope'
    with pytest.raises(ValueError,match='historical|independent'):m.validate_spec(spec)
    spec=tiny();spec['information_permission']='causal_online'
    with pytest.raises(ValueError,match='offline'):m.validate_spec(spec)


def test_symmetric_generator_labels_preserve_tiered_unmet_and_raw_plan():
    m=api();spec=tiny([[[1.,0.,0.],[0.,0.,0.],[0.,0.,0.],[1.,0.,0.]]]*2)
    spec['grid_capacity']=[0.,0.]
    spec['generators']=[dict(name=name,capacity=1.,min_power=1.,fuel_type='gas',fuel_slope=1.,fuel_fixed=0.,heat_slope=0.,heat_fixed=0.) for name in ('A','B')]
    spec['fuel_budgets']={'gas':2.}
    result=m.solve_reference(spec,formulation='tiered',time_limit_s=5.)
    raw=copy.deepcopy(result['plan'])
    raw.update({'A':[0.,1.],'A.on':[0.,1.],'B':[1.,0.],'B.on':[1.,0.]})
    original=copy.deepcopy(raw)
    relabeled=m.stabilize_symmetric_generators(spec,raw,['A','B'],[1.,0.],formulation='tiered')
    assert relabeled['verified_equivalent'] and relabeled['independent_audit']['valid']
    assert relabeled['plan']['A']==[1.,1.] and relabeled['plan']['B']==[0.,0.]
    assert raw==original
    assert relabeled['proves_dynamic_minimum_up_down_or_startup_feasibility'] is False
    spec['generators'][1]['capacity']=[1.,0.5]
    refused=m.stabilize_symmetric_generators(spec,raw,['A','B'],[1.,0.],formulation='tiered')
    assert not refused['verified_equivalent'] and refused['reason']=='not_identical_over_complete_window'


def test_full_window_fuel_budget_requires_a_scalar_not_a_per_period_array():
    m=api();spec=tiny([[[1.,0.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]]*2)
    spec['fuel_budgets']={'diesel_shaft':[1.,1.]}
    with pytest.raises(ValueError,match='budget|scalar'):
        m.validate_spec(spec)


def test_priority_lock_uses_its_dimensionless_tolerance_not_energy_tolerance():
    m=api();spec=tiny();result=m.solve_reference(spec,formulation='strict',time_limit_s=5.)
    bad=copy.deepcopy(result['plan']);bad['unmet.P0.e']=[1e-7];bad['grid'][0]-=1e-7
    audit=m.audit_plan(spec,bad,formulation='tiered',objective_locks=[dict(priority='P0',upper=0.)])
    assert not audit['valid']
    assert any(v['check']=='priority_lock.P0' for v in audit['violations'])


def test_solver_objective_metadata_must_match_independent_named_arithmetic(monkeypatch):
    m=api();actual=m.milp
    def wrong_metadata(**kwargs):
        result=actual(**kwargs)
        result.fun+=1.
        return result
    monkeypatch.setattr(m,'milp',wrong_metadata)
    result=m.solve_reference(tiny(),formulation='strict',time_limit_s=5.)
    assert not result['adoptable']
    assert result['status']=='invalid_incumbent'
    assert result['layers'][0]['independent_objective_matches_solver'] is False


def test_limited_math_backend_no_incumbent_is_unknown_not_infeasible(monkeypatch):
    m=api()
    monkeypatch.setattr(m,'milp',lambda **kwargs:SimpleNamespace(status=1,x=None,fun=None,message='synthetic limit fixture'))
    result=m.solve_reference(tiny(),formulation='strict',time_limit_s=0.)
    assert result['status']=='no_incumbent' and result['termination_reason']=='limit_reached'
    assert result['plan'] is None and result['objective_fee'] is None and not result['adoptable']


def test_later_missing_incumbent_retains_verified_plan_but_not_complete_lex_claim(monkeypatch):
    m=api();actual=m.milp;calls=[]
    def limited_after_first(**kwargs):
        calls.append(1)
        return actual(**kwargs) if len(calls)==1 else SimpleNamespace(status=1,x=None,fun=None,message='synthetic later limit fixture')
    monkeypatch.setattr(m,'milp',limited_after_first)
    result=m.solve_reference(tiny(),formulation='tiered',time_limit_s=5.)
    assert result['verified_incumbent'] and result['plan'] is not None
    assert result['status']=='time_limit' and not result['lexicographic_layers_completed']
    assert not result['lexicographic_optimality_proven']
    assert result['layers'][-1]['status']=='no_incumbent'


def test_lock_infeasibility_is_not_misreported_as_original_physical_infeasibility(monkeypatch):
    m=api();actual=m.milp;calls=[]
    def inconsistent_lock_backend(**kwargs):
        calls.append(1)
        return actual(**kwargs) if len(calls)==1 else SimpleNamespace(status=2,x=None,fun=None,message='synthetic inconsistent lock fixture')
    monkeypatch.setattr(m,'milp',inconsistent_lock_backend)
    result=m.solve_reference(tiny(),formulation='tiered',time_limit_s=5.)
    assert result['status']=='lexicographic_lock_infeasible'
    assert not result['adoptable'] and result['plan'] is not None


def test_hospital_ups_cannot_be_redeclared_as_unrestricted_generic_battery():
    m=api();spec=tiny();spec['evidence_class']='independent_original_hospital_input'
    general=storage();general['name']='battery';general.pop('discharge_scope');spec['storages']=[general]
    with pytest.raises(ValueError,match='generic|Hospital'):
        m.validate_spec(spec)


def test_unavailable_generator_cannot_create_fixed_heat_in_independent_audit():
    m=api();spec=tiny([[[0.,0.,0.]]*4]);spec['grid_capacity']=[0.]
    spec['generators']=[dict(name='CHP',capacity=0.,min_power=0.,fuel_type='gas',fuel_slope=0.,fuel_fixed=0.,heat_slope=0.,heat_fixed=1.)]
    plan=m.solve_reference(spec,formulation='strict',time_limit_s=5.)['plan']
    spec['original_demand_by_priority_kW'][0]=[[0.,1.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]];spec['demand_h']=[1.]
    plan['CHP.on']=[1.]
    audit=m.audit_plan(spec,plan,formulation='strict')
    assert not audit['valid']
    assert any('availability' in row['check'] for row in audit['violations'])


def test_chp_absorption_station_electricity_tower_and_fuel_match_hand_balance():
    m=api();spec=tiny([[[0.8,1.,0.5],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]])
    spec['grid_capacity']=[0.]
    spec['generators']=[dict(name='CHP',capacity=1.,min_power=1.,fuel_type='gas',fuel_slope=3.,fuel_fixed=2.,heat_slope=2.,heat_fixed=0.,fixed_electric=0.1)]
    spec['absorption']=dict(capacity=0.5,heat_per_c=2.,electric_per_c=0.2,tower_per_c=3.)
    spec['tower_capacity']=[1.5];spec['fuel_prices']['gas']=4.
    result=m.solve_reference(spec,formulation='strict',time_limit_s=5.)
    assert result['adoptable'] and result['audit']['valid']
    assert result['plan']['CHP']==pytest.approx([1.])
    assert result['plan']['ABS']==pytest.approx([0.5])
    assert result['audit']['fuel_used']['gas']==pytest.approx(5.)
    assert result['objective_fee']==pytest.approx(20.)
    assert result['audit']['maximum_balance_residual']<1e-8
    assert result['audit']['fuel_budget_scope']['gas']=='not_registered_no_budget_imposed'


def test_implicit_end_thermal_decay_matches_independent_two_period_certificate():
    m=api();spec=tiny([[[0.,0.,0.]]*4,[[0.,0.4,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]])
    spec.update(grid_capacity=[0.,0.],boiler=dict(capacity=[0.25,0.],fuel_slope=0.,fuel_type='gas'))
    spec['storages']=[dict(name='hot',carrier='h',capacity=2.,power=0.5,initial=1.,minimum=0.,
        charge_efficiency=0.8,discharge_efficiency=0.8,decay_per_h=0.1,loss_mode='implicit_end',
        charge_allowed=[True,False],discharge_allowed=[False,True],terminal_min=65/121,terminal_max=65/121)]
    result=m.solve_reference(spec,formulation='strict',time_limit_s=5.)
    assert result['adoptable']
    assert result['plan']['hot.stock']==pytest.approx([12/11,65/121])
    assert result['plan']['hot.charge']==pytest.approx([0.25,0.])
    assert result['plan']['hot.discharge']==pytest.approx([0.,0.4])


def test_negative_station_coefficient_cannot_create_free_electricity():
    m=api();spec=tiny([[[1.,0.,0.],[0.,0.,0.],[0.,0.,0.],[0.,0.,0.]]])
    spec['grid_capacity']=[0.]
    spec['generators']=[dict(name='G',capacity=0.5,min_power=0.5,fuel_type='gas',fuel_slope=1.,fuel_fixed=0.,
        heat_slope=0.,heat_fixed=0.,fixed_electric=-0.5)]
    with pytest.raises(ValueError,match='electric|coefficient'):
        m.solve_reference(spec,formulation='strict',time_limit_s=5.)


@pytest.mark.parametrize('field,value',[
    ('fixed_electric',float('nan')),('fixed_electric',[0.,0.]),('electric_service_limit',-1.)])
def test_extra_used_generator_coefficients_have_finite_nonnegative_horizon_domain(field,value):
    m=api();spec=tiny();g=dict(name='G',capacity=1.,min_power=0.,fuel_type='gas',fuel_slope=0.,fuel_fixed=0.,heat_slope=0.,heat_fixed=0.)
    g[field]=value;spec['generators']=[g]
    with pytest.raises(ValueError,match='electric|coefficient'):
        m.validate_spec(spec)


@pytest.mark.parametrize('field,value',[
    ('charge_electric_per_carrier',-1.),('discharge_electric_per_carrier',float('nan')),
    ('charge_electric_per_carrier',[0.1])])
def test_storage_pump_electricity_cannot_be_negative_missing_or_a_scalar_alias_array(field,value):
    m=api();spec=tiny();ups=storage();ups[field]=value;spec['storages']=[ups]
    with pytest.raises(ValueError,match='electric|coefficient|scalar'):
        m.validate_spec(spec)


def test_active_fuel_cost_is_not_silently_zero_when_price_is_missing():
    m=api();spec=tiny();spec['fuel_prices']={}
    spec['generators']=[dict(name='G',capacity=1.,min_power=0.,fuel_type='gas',fuel_slope=1.,fuel_fixed=0.,heat_slope=0.,heat_fixed=0.)]
    with pytest.raises(ValueError,match='price'):
        m.validate_spec(spec)
