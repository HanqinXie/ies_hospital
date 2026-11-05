from tools.integrated_experiment.run_batches import unknown_statuses


def test_new_native_infeasible_requires_review_before_next_wave():
    assert unknown_statuses({'completed': 100, 'model_constraint_infeasible': 1}) == {'model_constraint_infeasible': 1}


def test_interrupted_and_unsupported_attempts_are_retained_for_review():
    assert unknown_statuses({'completed': 0, 'interrupted_unknown': 1, 'unsupported_model_regime': 2}) == {'interrupted_unknown': 1, 'unsupported_model_regime': 2}
    assert unknown_statuses({'completed': 100}) == {}
