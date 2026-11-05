from tools.integrated_experiment.summarize import metric_comparison


def test_unknown_is_not_zero_or_an_improvement():
    assert metric_comparison({'a': None}, {'a': 0}, ['a']) == 'unknown'
    assert metric_comparison({'a': float('nan')}, {'a': 0}, ['a']) == 'unknown'


def test_compare_all_named_lower_is_better_metrics_without_hiding_tradeoffs():
    assert metric_comparison({'e': 2, 'c': 1}, {'e': 1, 'c': 1}, ['e', 'c']) == 'improved'
    assert metric_comparison({'e': 1, 'c': 1}, {'e': 2, 'c': 1}, ['e', 'c']) == 'worse'
    assert metric_comparison({'e': 2, 'c': 1}, {'e': 1, 'c': 2}, ['e', 'c']) == 'tradeoff'
    assert metric_comparison({'e': 1}, {'e': 1 + 5e-8}, ['e']) == 'same'
