"""Opt-in exact current-step planning; no hospital trajectory or future data."""
from pathlib import Path
import json,os
import numpy as np
import pytest
from test_engineering_planner import problem
from test_state_coordinated_planner import context

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('method',['M','C'])
def test_planner_appends_only_three_exact_graph_coordinates_and_preserves_raw(problem,tmp_path,method):
    from hospital_ies.solver_diagnostics import LayerCollector,collect_layers
    from tools.sci_engineering.planner import solve_joint
    from tools.sci_engineering.policy import policy_config
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    from hospital_ies.nonlinear import nonlinear_evidence_scope
    from sf2_evidence_fixture import make_domain_sink
    curve=FuelCurve.from_source(ROOT,problem['plant'])
    problem['config']['_sf2_fuel_curve']=curve.as_dict()
    # A modest feasible component, deliberately not a registered long case.
    problem['loads'][:]=0.;problem['loads'][0,0]=200.
    collector=LayerCollector()
    sample_run=os.environ.get('SF2_NATIVE_SAMPLE_RUN')
    destination=(Path('E:/hospital_ies_single_fault_model_20261102_run01/epochs/SF2/analysis/fuel_impl/native_samples')/sample_run/('planner_'+method)) if sample_run else tmp_path/'component'
    sink,records=make_domain_sink(destination/'nonlinear_evidence')
    with collect_layers(collector),nonlinear_evidence_scope(sink) as session:
        result=solve_joint(**problem,readiness_context=context(problem),coordination_options=policy_config(method))
    d=result['joint_diagnostic']['sf2_fuel']
    assert d['curve_identity']==curve.identity and d['added_planner_variables']==3
    for layer in collector.layers:
        for attempt in layer['result']['attempts']:
            assert len(attempt['nonlinear_graph']['equalities'])==3
            if attempt['accepted']:
                assert attempt['nonlinear_validation']['accepted']
                assert attempt['nonlinear_validation']['native_primal_repaired'] is False
    for action in (result['action'],result['planned_current_action']):
        assert action['gt_fuel_MMBtu_h']==pytest.approx(sum(curve.rate(v,20.) for v in action['gt_electric_kW']))
    for layer in result['joint_diagnostic']['layers'] if 'layers' in result['joint_diagnostic'] else []:
        assert layer.get('objective_basis')=='analytic_source_fuel_validation_shadow'
    assert d['actual_interval_energy_committed'] is False
    assert session['status']=='closed_completed' and session['call_count']==session['completed_call_count']
    assert all(layer['nonlinear_domain_evidence']['status']=='recorded' for layer in result['solver_backend_layers'])
    if sample_run:
        from hospital_ies.solver_diagnostics import json_value
        payload=dict(schema='single_fault.sf2.unregistered_planner_component.v1',role='unregistered_component_not_formal',formal_attempt=False,
            method=method,case_basis='pure current-state component with electric-only200kW request; not hospital normal policy',
            nonlinear_evidence_session=session,nonlinear_index=records,solver_backend_layers=result['solver_backend_layers'],
            joint_diagnostic=result['joint_diagnostic'],action=result['action'],planned_current_action=result['planned_current_action'])
        (destination/'PLANNER_COMPONENT.json').write_text(json.dumps(json_value(payload),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')


def test_four_point_reference_opt_in_keeps_original_power_and_steam(problem):
    from hospital_ies.model import solve_hour
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    from types import SimpleNamespace
    curve=FuelCurve.from_source(ROOT,problem['plant'])
    row=SimpleNamespace(timestamp=problem['row'].timestamp,outdoor_temperature_C=20.,
        P_e_nonplant_kW=200.,P_e_unresolved_kW=0.,Q_h_demand_kW=0.,Q_c_demand_kW=0.,hour_of_year=1)
    # The explicit adapter validates the source even when the optimal GT is OFF.
    result=solve_hour(row,problem['plant'],sf2_fuel_config=curve.as_dict())
    assert result['sf2_reference_initialization']['curve_identity']==curve.identity
    assert result['sf2_reference_initialization']['actual_interval_energy_committed'] is False
