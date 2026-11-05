"""Independent report arithmetic; unknown trajectories stay in denominators."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / 'outputs/v43_control_improvement_20261014_run01/result_audit.py'


def load():
    spec = importlib.util.spec_from_file_location('control_result_audit', AUDIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score(flag=False, fee=20., full=True, ens=None):
    return {'full_horizon': full, 'P0_failure_in_window': flag,
            'targets': [flag, 15. if flag and full else (0. if full else None)] +
            (ens or [0.] * 12) + [24., 24., 24., 0., fee if full else None]}


def row(sid, root, policy, value):
    return {'scenario_id': sid, 'root_family_id': root, 'policy_id': policy,
            'recipe_id': policy, 'training_seed': None, 'primary_score': value,
            'full_observation': value, 'status': 'completed' if value['full_horizon'] else 'model_constraint_infeasible'}


def test_unknown_is_not_negative_or_comparable():
    m = load()
    rows = [row('a', 'r1', 'rule', score(False)), row('a', 'r1', 'learn', score(True, full=False)),
            row('b', 'r2', 'rule', score(True)), row('b', 'r2', 'learn', score(False))]
    r = m.pair_compare(rows, 'rule', 'learn', 'primary_score')
    assert r['registered_pairs'] == 2 and r['complete_pairs'] == 1 and r['unknown_pairs'] == 1
    assert r['P0_failure_improved'] == 1 and r['P0_failure_worsened'] == 0
    assert r['known_positive_any_prefix']['learn'] == 1
    assert r['pair_records'][0]['delta'] is None


def test_low_fee_does_not_hide_P0_harm():
    m = load()
    rows = [row('a', 'r1', 'rule', score(False, 100.)), row('a', 'r1', 'learn', score(True, 1.))]
    r = m.pair_compare(rows, 'rule', 'learn', 'primary_score')
    assert r['preference_counts'] == {'improved': 0, 'same': 0, 'worsened': 1, 'unknown': 0}
    assert r['totals_delta']['actual_energy_fee_USD'] == -99.
    assert r['P0_failure_worsened_ids'] == ['a']


def test_duplicate_policy_episode_and_mismatched_root_refused():
    import pytest
    m = load()
    rows = [row('a', 'r1', 'rule', score()), row('a', 'r2', 'learn', score())]
    with pytest.raises(ValueError): m.pair_compare(rows, 'rule', 'learn', 'primary_score')
    rows[1]['root_family_id'] = 'r1'
    with pytest.raises(ValueError): m.pair_compare(rows + [rows[0]], 'rule', 'learn', 'primary_score')


def test_background_bootstrap_counts_roots_not_windows():
    m = load()
    rows = [row(s, r, p, score(fee=f)) for s, r in [('a', 'r1'), ('b', 'r1'), ('c', 'r2')]
            for p, f in [('rule', 20.), ('learn', 19.)]]
    result = m.pair_compare(rows, 'rule', 'learn', 'primary_score')
    assert result['background_roots'] == 2
    ci = result['root_bootstrap']['actual_energy_fee_USD']
    assert ci['root_mean_delta'] == -1. and ci['p025'] == -1. and ci['p975'] == -1.


def test_partial_unknown_even_if_P0_true_has_no_invented_consequence():
    m = load()
    a = score(True, full=False)
    a['targets'][2:14] = [None] * 12
    assert not m.valid_complete(a)


def test_exporter_integer_flag_is_known_positive():
    m = load()
    a = score(True, full=False)
    a['P0_failure_in_window'] = 1
    rows = [row('a','r','rule',score()), row('a','r','learn',a)]
    assert m.pair_compare(rows,'rule','learn','primary_score')['known_positive_any_prefix']['learn'] == 1


def test_named_research_resolution_keeps_raw_harm_and_exact_comparison():
    from tools.control_improvement.ranking import RESEARCH_RESOLUTION
    m = load()
    candidate = score(fee=19., ens=[0.00002] + [0.] * 11)
    rows = [row('a', 'r', 'rule', score()), row('a', 'r', 'learn', candidate)]
    result = m.pair_compare(rows, 'rule', 'learn', 'primary_score', resolution=RESEARCH_RESOLUTION)
    record = result['pair_records'][0]
    assert record['delta'][2] == 0.00002
    assert record['exact_research_lex_preference'] == 'worsened'
    assert record['registered_resolution_preference'] == 'improved'
    assert record['any_P0_consequence_worse'] is True
    assert result['registered_resolution_preference_counts']['improved'] == 1


def test_resolution_never_rounds_a_new_P0_failure_away():
    from tools.control_improvement.ranking import RESEARCH_RESOLUTION
    m = load()
    rows = [row('a', 'r', 'rule', score()), row('a', 'r', 'learn', score(True, 0.))]
    result = m.pair_compare(rows, 'rule', 'learn', 'primary_score', resolution=RESEARCH_RESOLUTION)
    assert result['registered_resolution_preference_counts']['worsened'] == 1
