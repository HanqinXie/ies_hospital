"""Cross-batch saved JSON reporting with all registered denominators."""
import copy
import importlib
import json
from pathlib import Path

import pytest


def api():
    try:
        return importlib.import_module('tools.engineering_reporting.aggregate')
    except ModuleNotFoundError:
        pytest.fail('Independent engineering saved-results reporter is missing')


def saved_analysis(*, complete=True, loss=0., starts=0, long=False):
    names = ('P0_failure', 'P0_failure_minutes') + tuple(f'{p}_{c}_ENS_kWh' for p in ('P0', 'P1', 'P2', 'P3') for c in 'ehc') + ('P0_max_temperature_C', 'P1_max_temperature_C', 'P2_max_temperature_C', 'secondary_GT_trips', 'actual_energy_fee_USD')
    values = [0., 0.] + [0.]*12 + [21., 22., 23., 0., 10.]
    values[8] = loss
    if not complete:
        values = [None]*19
    matrix = [[0., 0., 0.] for _ in range(4)]; matrix[2][0] = loss
    duration = [[0., 0., 0.] for _ in range(4)]; duration[2][0] = 15. if loss else 0.
    prefix = dict(total_ENS_kWh=matrix, disconnected_ENS_kWh=matrix,
        connected_unserved_ENS_kWh=[[0., 0., 0.] for _ in range(4)], material_minutes=duration,
        equivalent_loss_minutes=duration, longest_material_minutes=duration,
        trailing_material_minutes=duration, P0_failure_minutes=0.,
        gt_actual={f'GT_{i}': dict(zero_to_positive_count=starts if i == 1 else 0,
            positive_to_zero_count=0, observed_running_minutes=100.) for i in (1, 2, 3)})
    window = dict(complete=complete, observed_minutes=600. if complete else 120.,
        window_minutes=[0., 600.], original_19=dict(target_names=list(names), targets=values, target_mask=[v is not None for v in values]),
        observed_prefix=prefix, all_registered=copy.deepcopy(prefix) if complete else None)
    windows = dict(primary=copy.deepcopy(window), full=copy.deepcopy(window))
    windows['primary']['window_minutes'] = [60., 420.]
    if long:
        windows.update(long1440=copy.deepcopy(window), extension_600_1440=copy.deepcopy(window))
        windows['long1440']['window_minutes'] = [0., 1440.]
        windows['extension_600_1440']['window_minutes'] = [600., 1440.]
    return dict(windows=windows, engineering=dict(gt={f'GT_{i}': dict(runs=[]) for i in (1, 2, 3)}),
        timelines=dict(events=[]), observed_until_minute=600. if complete else 120., requested_end_minute=1440. if long else 600.)


def batch(run, name, *, model='M0', policies=('F', 'S'), scenarios=('s',), records=None,
          classes=None, phase='development', source='a', config=None):
    folder = run/'experiments'/name; (folder/'analysis').mkdir(parents=True)
    rows = records if records is not None else [dict(scenario_id=s, policy_id=p, model_id=model,
        status='completed', analysis=saved_analysis()) for s in scenarios for p in policies]
    plan = dict(phase=phase, model_id=model, model_config=config or {},
        policy_specs=[dict(policy_id=p) for p in policies], scenario_ids=list(scenarios),
        registered_pairs=len(scenarios)*len(policies), source_sha256={'runtime.py': source*64})
    manifest = dict(scenarios=[dict(id=s, analysis_class=(classes or {}).get(s, 'N2'),
        source_day_index=2, source_v13_scenario_id='old_'+s) for s in scenarios])
    for path, value in ((folder/'plan.json', plan), (folder/'manifest.json', manifest),
                         (folder/'analysis/RESULTS.json', dict(records=rows))):
        path.write_text(json.dumps(value), encoding='utf8')
    return folder


def test_all_registered_pairs_include_missing_and_unknown_with_null_totals(tmp_path):
    a = api(); rows = [dict(scenario_id='s1', policy_id='F', model_id='M0', status='completed', analysis=saved_analysis(loss=2.)),
        dict(scenario_id='s1', policy_id='S', model_id='M0', status='partial', analysis=saved_analysis(complete=False, loss=1.)),
        dict(scenario_id='s2', policy_id='F', model_id='M0', status='completed', analysis=saved_analysis())]
    batch(tmp_path, 'one', scenarios=('s1', 's2'), records=rows)
    report = a.collect(tmp_path)
    assert report['registered_pairs'] == 4 and report['saved_analysis_pairs'] == 3
    assert report['missing_analysis_pairs'] == 1 and report['unknown_full_pairs'] == 2
    group = next(g for g in report['groups'] if g['policy_id'] == 'S' and g['window'] == 'full')
    assert group['registered_pairs'] == 2 and group['complete_pairs'] == 0 and group['unknown_pairs'] == 2
    assert group['metrics']['P2_e_ENS_kWh']['all_registered'] is None
    assert group['engineering']['P2_e_total_ENS_kWh']['observed_prefix_sum'] == 1.
    assert group['engineering']['P2_e_total_ENS_kWh']['observed_prefix_count'] == 1
    assert not any(g['analysis_class'] != 'N2' for g in report['groups'])


def test_paired_model_and_policy_deltas_use_only_exact_registered_cases(tmp_path):
    a = api(); batch(tmp_path, 'old')
    rows = [dict(scenario_id='s', policy_id=p, model_id='M14_engineering', status='completed',
        analysis=saved_analysis(loss=3. if p == 'F' else 1., starts=2 if p == 'S' else 0)) for p in ('F', 'S')]
    batch(tmp_path, 'new', model='M14_engineering', records=rows, config={'startup': 10.})
    report = a.collect(tmp_path, reference_policy='F')
    delta = next(r for r in report['policy_contrasts'] if r['model_id']=='M14_engineering' and r['window']=='full')
    assert delta['candidate_policy_id'] == 'S' and delta['deltas']['P2_e_ENS_kWh'] == -2.
    model = next(r for r in report['model_contrasts'] if r['policy_id']=='S' and r['window']=='full')
    assert model['candidate_model_id']=='M14_engineering' and model['deltas']['P2_e_ENS_kWh']==1.
    assert model['reference_model_id']=='M0'


def test_versions_and_long_normals_keep_separate_groups_and_ambiguity(tmp_path):
    a = api(); batch(tmp_path, 'v1', policies=('F',), source='a')
    batch(tmp_path, 'v2', policies=('F',), source='b')
    batch(tmp_path, 'repeat', policies=('F',), source='a')
    batch(tmp_path, 'long', policies=('F',), source='a', phase='long_normal',
        records=[dict(scenario_id='s', policy_id='F', model_id='M0', status='completed', analysis=saved_analysis(long=True))])
    report = a.collect(tmp_path)
    assert report['registered_pairs']==4
    assert len([g for g in report['groups'] if g['window']=='full'])==3
    long = [g for g in report['groups'] if g['window']=='long1440']
    assert len(long)==1 and long[0]['phase']=='long_normal' and long[0]['registered_pairs']==1
    assert report['repeated_case_policy_count']==1


def test_export_creates_new_versions_and_never_opens_trajectory_raw(tmp_path, monkeypatch):
    a = api(); batch(tmp_path, 'one')
    forbidden = tmp_path/'experiments/one/pairs/trajectory.json.gz'
    forbidden.parent.mkdir(); forbidden.write_bytes(b'not an input')
    original = Path.read_bytes
    def read_bytes(path):
        if path.suffix=='.gz':
            pytest.fail('Saved reporter opened physical raw')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    first = a.aggregate(tmp_path); second = a.aggregate(tmp_path)
    assert first['output_directory'] != second['output_directory']
    for receipt in (first, second):
        folder = Path(receipt['output_directory'])
        assert folder.parent==tmp_path/'summary'
        assert (folder/'CAMPAIGN_SUMMARY.json').is_file()
        assert (folder/'CLASS_MODEL_POLICY_SUMMARY.csv').is_file()
        assert (folder/'REGISTERED_PAIRS.csv').is_file()
        assert receipt['raw_opens']==0 and receipt['physical_episodes_started']==0


def test_result_outside_registered_panel_is_rejected(tmp_path):
    a = api(); batch(tmp_path, 'one', records=[dict(scenario_id='other', policy_id='F', model_id='M0',
        analysis=saved_analysis())])
    with pytest.raises(ValueError, match='registered'):
        a.collect(tmp_path)
