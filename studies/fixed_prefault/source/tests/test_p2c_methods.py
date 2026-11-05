"""Independent P2C mathematical contracts, without hospital calls."""
import numpy as np
import pytest


def api():
    from tools.p2c_methods.diagnostics import gram_diagnostics, moment_diagnostics, final_ols_diagnostics, iterative_diagnostics
    return gram_diagnostics, moment_diagnostics, final_ols_diagnostics, iterative_diagnostics


def test_actual_basis_moments_agree_with_direct_nonorthogonal_predictions():
    _, moments, _, _ = api()
    phi = np.array([[1., 2., -1.], [1., 3., 0.], [1., 5., 4.], [1., 6., 2.]])
    beta = np.array([1.5, 2., -.5])
    result = moments(phi, beta)
    assert result['general_mean'] == pytest.approx(np.mean(phi @ beta), abs=1e-12)
    assert result['general_variance'] == pytest.approx(np.var(phi @ beta), abs=1e-12)
    assert result['simplified_mean'] != pytest.approx(result['direct_mean'])
    assert result['simplified_variance'] != pytest.approx(result['direct_variance'])


def test_gram_norms_and_dimension_are_not_interchangeable():
    grams, _, _, _ = api()
    phi = np.array([[1., 2.], [1., -2.]])
    result = grams(phi)
    assert result['basis_dimension'] == 2
    assert result['sample_count'] == 2
    assert result['max_absolute_entry_error'] == pytest.approx(3.)
    assert result['frobenius_error'] == pytest.approx(3.)
    assert result['frobenius_per_basis_dimension'] == pytest.approx(1.5)
    assert result['spectral_error'] == pytest.approx(3.)


def test_final_ols_refuses_unidentified_interpolation():
    _, _, final, _ = api()
    a = np.array([[1., 1., 0.], [1., 0., 1.]])
    beta = np.array([2., 1., -1.])
    result = final(a, a @ beta, beta, prune_relative=0.)
    assert result['retained_terms'] == 3
    assert result['retained_rank'] == 2
    assert result['eligible_final_ols'] is False
    assert result['reason'] == 'retained_design_not_full_column_rank'
    assert result['fitted_coefficients'] is None
    assert result['diagnostic_minimum_norm_coefficients'] is not None


def test_identifiable_final_ols_is_not_rejected_by_three_times_basis_rule():
    _, _, final, _ = api()
    a = np.array([[1., -1.], [1., 0.], [1., 1.]])
    beta = np.array([2., .5])
    result = final(a, a @ beta, beta)
    assert result['eligible_final_ols']
    assert result['retained_rank'] == 2
    np.testing.assert_allclose(result['fitted_coefficients'], beta, atol=1e-12)


def test_nonconvergence_remains_failure_even_if_interpolation_is_exact():
    _, _, _, iterative = api()
    rng = np.random.default_rng(2471)
    a = np.column_stack([np.ones(10), rng.normal(size=(10, 19))])
    truth = np.zeros(20); truth[[0, 3, 7]] = [2., 1., -.7]
    result = iterative(a, a @ truth, degree=3, initial_exponent=3, max_iter=1)
    assert result['converged'] is False
    assert result['status'] == 'nonconverged'
    assert result['accepted_final_coefficients'] is None
    assert np.linalg.norm(a @ result['last_irls_coefficients'] - a @ truth) < 1e-8


@pytest.mark.parametrize('bad', [np.array([[1., np.nan], [1., 2.]]), np.array([1., 2.])])
def test_invalid_basis_samples_are_explicit_errors(bad):
    grams, _, _, _ = api()
    with pytest.raises(ValueError):
        grams(bad)
