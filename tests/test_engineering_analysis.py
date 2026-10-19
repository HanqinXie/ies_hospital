"""Small in-memory executed payloads; no physical solves or raw rereads."""
import copy
import importlib
import json

import pytest


def api():
    try:
        return importlib.import_module('tools.engineering_model.analysis')
    except ModuleNotFoundError:
        pytest.fail('v14 engineering analysis is missing')


def segment(a, b, gt=(5., 0., 0.), *, loss=0., disconnected=0., failed=False):
    loads = [[10., 10., 10.] for _ in range(4)]
    unserved = [[0., 0., 0.] for _ in range(4)]; unserved[2][0] = loss
    cuts = [[0., 0., 0.] for _ in range(4)]; cuts[2][0] = disconnected
    connected = [[loads[p][k] - cuts[p][k] for k in range(3)] for p in range(4)]
    return dict(start_minute=a, end_minute=b, command_id=None, loads_kW=loads,
        connected_loads_kW=connected, disconnected_loads_kW=cuts, unserved_kW=unserved,
        action=dict(gt_electric_kW=list(gt), grid_import_kW=0., diesel_kW=0.),
        source_start_gt_kW=list(gt), source_end_gt_kW=list(gt), cost_USD_step=2.,
        energy_balance_residuals=[0., 0., 0.], steam_balance_residual_lbph=0., auxiliary_electric_kW=0.,
        temperature=dict(P0=21., P1=22., P2=23., P3=None),
        violation=dict(P0=False, P1=False, P2=False, P3=False),
        classification=dict(p0_guarantee_failure=failed, material_unserved_by_carrier=dict(e=failed, h=False, c=False)))


def payload(segments, requested=600., events=None):
    return dict(scenario=dict(id='s', analysis_class='N2', root_family_id='r', source_day_index=282,
                             duration_steps=int(requested/15), step_minutes=15),
        identity=dict(scenario_id='s', policy_id='p'), execution_model_id='M14_engineering',
        initial_state=dict(state=dict(gt_prev=[5., 0., 0.], last_grid_import_kW=0., last_diesel_kW=0.)),
        summary=dict(observed_until_minute=segments[-1]['end_minute'] if segments else 0.,
                     requested_end_minute=requested, status='completed' if segments and segments[-1]['end_minute']==requested else 'partial'),
        segments=segments, events=events or [], decisions=[])


def test_original_nineteen_and_unknown_prefix_are_retained_without_mutation():
    a = api(); p = payload([segment(0., 60.), segment(60., 80., loss=3., disconnected=2., failed=True)])
    before = copy.deepcopy(p)
    r = a.analyze_payload(p, None)
    from tools.control_improvement.branches import window_targets
    assert r['windows']['primary']['original_19'] == window_targets(p, 60., 360., None)
    assert r['windows']['full']['original_19'] == window_targets(p, 0., 600., None)
    w = r['windows']['full']
    assert not w['complete'] and w['all_registered'] is None
    assert w['observed_prefix']['disconnected_ENS_kWh'][2][0] == pytest.approx(2./3.)
    assert w['observed_prefix']['connected_unserved_ENS_kWh'][2][0] == pytest.approx(1./3.)
    assert w['observed_prefix']['P0_failure_minutes'] == 20.
    assert p == before
    json.dumps(r, allow_nan=False)


def test_gt_counts_exclude_initial_online_and_keep_right_censored_runs():
    a = api(); p = payload([segment(0., 15.), segment(15., 30., gt=(0., 2e-4, 0.)),
                           segment(30., 600., gt=(5., 2e-4, 0.))])
    r = a.analyze_payload(p, None)['engineering']
    assert r['actual_positive_threshold_kW'] == 1e-4
    assert r['gt']['GT_1']['zero_to_positive_count'] == 1
    assert r['gt']['GT_1']['positive_to_zero_count'] == 1
    assert r['gt']['GT_2']['zero_to_positive_count'] == 1
    runs = r['gt']['GT_1']['runs']
    assert runs[0]['left_censored'] and runs[0]['end_minute'] == 15.
    assert runs[1]['right_censored'] and runs[1]['end_minute'] is None
    assert runs[1]['observed_duration_minutes'] == 570.


def test_linear_gt_ramp_threshold_edge_is_inside_actual_segment():
    a = api(); p = payload([segment(0., 600., gt=(0., 0., 0.))])
    p['segments'][0]['source_start_gt_kW'] = [0., 0., 0.]
    p['segments'][0]['source_end_gt_kW'] = [1., 0., 0.]
    r = a.analyze_payload(p, None)['engineering']
    assert r['gt']['GT_1']['positive_to_zero_count'] == 1
    assert r['gt']['GT_1']['zero_to_positive_count'] == 1
    activation = next(t for t in r['gt_transitions'] if t['kind'] == 'activated')
    assert activation['time_minute'] == pytest.approx(.06)
    assert activation['basis'] == 'linear_segment_threshold_crossing'


def test_start_requests_completions_and_actual_positive_are_distinct():
    a = api(); p = payload([segment(0., 600.)], events=[
        dict(kind='gt_start_requested', device='GT_2', time_minute=15., ready_minute=20.),
        dict(kind='gt_start_process_begun', device='GT_2', time_minute=17., actual_start_minute=17.),
        dict(kind='gt_start_completed', device='GT_2', time_minute=20., actual_kW=0.),
        dict(kind='no_export_interlock', device='GT_1', time_minute=25.)])
    gt = a.analyze_payload(p, None)['engineering']['gt']
    assert gt['GT_2']['start_requested_count'] == 1 and gt['GT_2']['start_completed_event_count'] == 1
    assert gt['GT_2']['start_process_begun_count'] == 1
    assert gt['GT_2']['start_completed_positive_count'] == 0 and gt['GT_2']['zero_to_positive_count'] == 0


def test_long_windows_never_replace_original_six_hundred_minute_scores():
    a = api(); p = payload([segment(0., 600.), segment(600., 1440., loss=2., disconnected=1.)], requested=1440.)
    r = a.analyze_payload(p, None)
    assert r['windows']['full']['original_19']['targets'][8] == 0.
    assert r['windows']['long1440']['observed_prefix']['total_ENS_kWh'][2][0] == 28.
    assert r['windows']['extension_600_1440']['observed_prefix']['total_ENS_kWh'][2][0] == 28.
    assert r['windows']['extension_600_1440']['window_minutes'] == [600., 1440.]
    assert r['windows']['full']['window_minutes'] == [0., 600.]


def test_actual_gt_energy_clips_linear_source_ports_to_each_window():
    a = api(); s = segment(0., 600., gt=(5., 0., 0.))
    s['source_start_gt_kW'] = [0., 0., 0.]
    s['source_end_gt_kW'] = [10., 0., 0.]
    r = a.analyze_payload(payload([s]), None)
    assert r['windows']['primary']['observed_prefix']['source_electric_energy_kWh']['GT_1'] == pytest.approx(24.)
    assert r['windows']['full']['observed_prefix']['source_electric_energy_kWh']['GT_1'] == pytest.approx(50.)


def test_connection_inconsistency_is_explicitly_rejected():
    a = api(); p = payload([segment(0., 600., loss=1., disconnected=2.)])
    with pytest.raises(ValueError, match='disconnected'):
        a.analyze_payload(p, None)


def test_exports_keep_unknown_rows_and_refuse_overwrite(tmp_path):
    a = api(); result = a.analyze_payload(payload([segment(0., 15.)]), None)
    record = dict(scenario_id='s', policy_id='p', model_id='M14_engineering', analysis=result)
    receipt = a.export_rows([record], tmp_path)
    assert receipt['registered_trajectory_count'] == 1 and receipt['physical_episodes_started'] == 0
    assert receipt['unknown_full_trajectory_count'] == 1
    assert (tmp_path/'TRAJECTORY_WINDOWS.csv').is_file() and (tmp_path/'RESULTS.json').is_file()
    assert len(json.loads((tmp_path/'RESULTS.json').read_text(encoding='utf8'))['records']) == 1
    with pytest.raises(FileExistsError):
        a.export_rows([record], tmp_path)
