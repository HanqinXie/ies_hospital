from tools.integrated_experiment.closedloop_analysis import compare_pair


def test_unknown_full_outcome_not_converted_to_safe_comparison():
    a = {'score_outcome_valid': True, 'score_P0_failure': 0}
    b = {'score_outcome_valid': False, 'score_P0_failure': 1}
    row = compare_pair(a, b)
    assert row['service_comparison'] == 'unknown'
    assert row['observed_failure_change'] == 1
    assert row['guarantee_comparison'] == 'unknown'


def test_guarantee_tie_is_separate_from_continuous_service_tradeoff():
    from tools.integrated_experiment.summarize import SERVICE_FIELDS
    a = {k: 0. for k in SERVICE_FIELDS}
    a.update(score_outcome_valid=True, actual_energy_fee_USD=5.)
    b = dict(a, P0_e_ENS_kWh=2., P1_c_ENS_kWh=-1., actual_energy_fee_USD=4.)
    row = compare_pair(a, b)
    assert row['guarantee_comparison'] == 'same'
    assert row['service_comparison'] == 'tradeoff'
    assert row['fee_comparison'] == 'improved'
