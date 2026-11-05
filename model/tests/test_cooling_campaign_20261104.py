"""Finite campaign IO arithmetic; these are not native physical trajectories."""
import importlib
from pathlib import Path
import json
import pytest


def api():
    assert (Path(__file__).parents[1]/'tools/cooling_dynamics_20261104/extract_results.py').exists(), 'New finite campaign extractor required'
    return importlib.import_module('tools.cooling_dynamics_20261104.extract_results')


def segment(start, end, delivered=5.):
    return dict(start_minute=start,end_minute=end,service_request_ledger={
        'original_demand_kW':[[10.,20.,30.]]*4,
        'delivered_kW':[[delivered,20.,30.]]*4,
        'active_cut_kW':[[0.,0.,0.]]*4,
        'connected_unserved_kW':[[10.-delivered,0.,0.]]*4})


def test_actual_ledger_integrates_native_subsegments_and_rejects_missing_tail():
    m=api(); rows=[segment(0.,.25,2.),segment(.25,1.,6.)]
    result=m.audit_segments(rows,1.)
    assert result['status']=='passed'
    assert result['served_kWh'][0][0]==pytest.approx((.25*2.+.75*6.)/60.)
    assert result['demand_kWh'][0][0]==pytest.approx(10./60.)
    with pytest.raises(ValueError,match='coverage'):
        m.audit_segments(rows,2.)
    with pytest.raises(ValueError,match='closure'):
        bad=segment(0.,1.);bad['service_request_ledger']['connected_unserved_kW'][0][0]=0.
        m.audit_segments([bad],1.)


def test_minute_rates_are_overlap_weighted_and_unknown_tail_is_not_filled():
    m=api(); rows=[segment(0.,.25,2.),segment(.25,1.,6.)]
    result=m.minute_rates(rows,1.)
    assert len(result)==1 and result[0]['P0_e_served_kW']==pytest.approx(5.)
    assert result[0]['total_e_served_kW']==pytest.approx(20.)
    with pytest.raises(ValueError,match='coverage'):
        m.minute_rates(rows,2.)


def test_reproduced_metadata_sentinels_never_zero_fill_actual_power():
    from tools.cooling_dynamics_20261104.run_worker import clean
    value=clean(dict(temperature=dict(P3=float('-inf'))))
    assert value['temperature']['P3']['nonfinite_sentinel']=='negative_infinity'
    assert value['temperature']['P3']['physical_quantity_imputed'] is False
    bounded=clean(dict(validation=dict(rows=[dict(lower=float('-inf'),upper=float('inf'))])))
    assert bounded['validation']['rows'][0]['upper']['meaning']=='unbounded_native_constraint_metadata'
    with pytest.raises(ValueError,match='service_request_ledger'):
        clean(dict(service_request_ledger=dict(delivered_kW=[[float('nan')]])))


def test_actual_nested_nonlinear_true_validation_unbounded_row_is_explicit():
    from tools.cooling_dynamics_20261104.run_worker import clean
    row=dict(lower=float('-inf'),upper=float('inf'))
    value=dict(command=dict(joint_dispatch=dict(diagnostic=dict(m23_boiler_domains=dict(
        checked_domains=[dict(solver_backend_layers=[dict(attempts=[dict(
            nonlinear_validation=dict(true_validation=dict(worst_rows=[row])))])])])))))
    result=clean(value)
    bound=result['command']['joint_dispatch']['diagnostic']['m23_boiler_domains']['checked_domains'][0]['solver_backend_layers'][0]['attempts'][0]['nonlinear_validation']['true_validation']['worst_rows'][0]
    assert bound['lower']['nonfinite_sentinel']=='negative_infinity'
    assert bound['upper']['nonfinite_sentinel']=='positive_infinity'
    assert bound['lower']['original_json_pointer']=='/command/joint_dispatch/diagnostic/m23_boiler_domains/checked_domains/0/solver_backend_layers/0/attempts/0/nonlinear_validation/true_validation/worst_rows/0/lower'
    assert bound['lower']['physical_quantity_imputed'] is False
    row['lower']=float('nan')
    # Diagnostic NaN is now an explicit failed-candidate log value, not an
    # actual measurement. Physical NaN rejection remains covered separately.
    failed=clean(value)['command']['joint_dispatch']['diagnostic']['m23_boiler_domains']['checked_domains'][0]['solver_backend_layers'][0]['attempts'][0]['nonlinear_validation']['true_validation']['worst_rows'][0]['lower']
    assert failed['nonfinite_sentinel']=='nan' and failed['not_physical_measurement'] is True


def test_unbounded_constraint_table_uses_structural_validation_scope():
    from tools.cooling_dynamics_20261104.run_worker import clean
    for scope in ('final_validation','previously_unseen_validation'):
        original=dict(joint_dispatch=dict(diagnostic=dict(sf2_fuel={scope:dict(rows=[
            dict(lower=float('-inf'),upper=float('inf'),custom_residual=float('-inf'))])})))
        output=clean(original)['joint_dispatch']['diagnostic']['sf2_fuel'][scope]['rows'][0]
        assert output['upper']['nonfinite_sentinel']=='positive_infinity'
        assert output['custom_residual']['nonfinite_sentinel']=='negative_infinity'
        assert output['upper']['original_json_pointer']=='/joint_dispatch/diagnostic/sf2_fuel/'+scope+'/rows/0/upper'
        assert output['upper']['physical_quantity_imputed'] is False
    with pytest.raises(ValueError,match='actual_flow'):
        clean(dict(actual_flow=dict(upper=float('inf'))))


def test_failed_optimizer_diagnostics_are_explicit_but_physical_nonfinite_fails():
    import math
    from tools.cooling_dynamics_20261104.run_worker import clean,compact_solver
    original=dict(solver_backend_layers=[dict(attempts=[dict(status='failed',
        mip_dual_bound=float('-inf'),failed_candidate_score=float('nan'))])])
    output=compact_solver(original)['solver_backend_layers'][0]['attempts'][0]
    assert output['mip_dual_bound']['nonfinite_sentinel']=='negative_infinity'
    assert output['failed_candidate_score']['nonfinite_sentinel']=='nan'
    assert output['failed_candidate_score']['not_physical_measurement'] is True
    assert output['failed_candidate_score']['original_numeric_type']=='float'
    assert math.isnan(original['solver_backend_layers'][0]['attempts'][0]['failed_candidate_score'])
    for original in [dict(action=dict(grid_import_kW=float('inf'))),
                     dict(service_request_ledger=dict(delivered_kW=[[float('nan')]])),
                     dict(m21_storage=dict(cold_store=dict(final_kWh=float('-inf'))))]:
        with pytest.raises(ValueError,match='Nonfinite actual log quantity'):
            clean(original)


def test_observation_logging_does_not_feed_serialized_tags_to_controller():
    import math
    from types import SimpleNamespace
    from tools.cooling_dynamics_20261104.run_worker import record_observation,compact_solver,controller_records
    raw=dict(decision_time_min=3.,diagnostic=dict(candidate_bound=float('inf'),failed_score=float('nan')),raw_tuple=(1.,2.))
    env=SimpleNamespace(episode_snapshot_function=lambda _:raw);history=[]
    current=record_observation(env,history)
    logged=compact_solver(current)
    class ReadOnlySpy:
        def decide(self,records):
            assert math.isinf(records[-1]['diagnostic']['candidate_bound'])
            assert math.isnan(records[-1]['diagnostic']['failed_score'])
            assert isinstance(records[-1]['raw_tuple'],tuple)
            return {},{}
    spy=ReadOnlySpy();spy.decide(controller_records(spy,history))
    assert logged['diagnostic']['failed_score']['not_physical_measurement'] is True
    assert math.isnan(history[-1]['diagnostic']['failed_score'])
