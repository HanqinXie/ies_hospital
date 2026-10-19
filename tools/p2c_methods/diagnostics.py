"""Explicit diagnostics for approximate bases and D24 final OLS eligibility.

This version does not replace the frozen P2B implementation. Diagnostic
minimum-norm coefficients and nonconverged IRLS coefficients are not fitted
DSPCE models. No simulator or hospital training routine is called here.
"""
import warnings
import numpy as np
from scipy.linalg import lstsq, svdvals
from tools.p2b_methods.dmorph import fixed_weight_dmorph


def _basis(phi):
    phi = np.asarray(phi, dtype=float)
    if phi.ndim != 2 or len(phi) < 2 or phi.shape[1] < 1 or not np.all(np.isfinite(phi)):
        raise ValueError('finite two-dimensional basis with at least two samples required')
    return phi


def gram_diagnostics(phi):
    phi = _basis(phi)
    n, p = phi.shape
    gram = phi.T @ phi / n
    error = gram - np.eye(p)
    products_second = (phi * phi).T @ (phi * phi) / n
    se = np.sqrt(np.maximum(0., products_second - gram * gram) / n)
    worst = np.unravel_index(np.argmax(np.abs(error)), error.shape)
    singular = svdvals(gram)
    return {
        'sample_count': n, 'basis_dimension': p,
        'max_absolute_entry_error': float(np.max(np.abs(error))),
        'frobenius_error': float(np.linalg.norm(error, 'fro')),
        'frobenius_per_basis_dimension': float(np.linalg.norm(error, 'fro') / p),
        'spectral_error': float(np.linalg.norm(error, 2)),
        'mean_residual_max': float(np.max(np.abs(phi[:, 1:].mean(axis=0)))) if p > 1 else 0.,
        'max_entry_standard_error': float(se.max()),
        'standard_error_definition': 'sqrt((E[(phi_i phi_j)^2]-G_ij^2)/N), iid estimate; no guarantee in heavy tails',
        'entries_exceeding_max_005_or_6se': int(np.count_nonzero(np.abs(error) > np.maximum(.05, 6 * se))),
        'worst_entry_indices': [int(v) for v in worst],
        'worst_entry_error': float(error[worst]), 'worst_entry_standard_error': float(se[worst]),
        'condition_2': float(singular[0] / singular[-1]) if singular[-1] > 0 else None,
        'mu': phi.mean(axis=0), 'G': gram, 'entry_standard_errors': se,
    }


def moment_diagnostics(phi, beta):
    phi = _basis(phi); beta = np.asarray(beta, float)
    if beta.shape != (phi.shape[1],) or not np.all(np.isfinite(beta)):
        raise ValueError('finite coefficient vector must match basis columns')
    mu = phi.mean(axis=0); gram = phi.T @ phi / len(phi)
    prediction = phi @ beta
    general_mean = float(beta @ mu)
    general_variance = float(beta @ (gram - np.outer(mu, mu)) @ beta)
    direct_mean = float(prediction.mean()); direct_variance = float(prediction.var())
    return {
        'sample_count': len(phi), 'basis_dimension': phi.shape[1],
        'simplified_mean': float(beta[0]), 'simplified_variance': float(beta[1:] @ beta[1:]),
        'general_mean': general_mean, 'general_variance': general_variance,
        'direct_mean': direct_mean, 'direct_variance': direct_variance,
        'general_direct_mean_absolute_difference': abs(general_mean - direct_mean),
        'general_direct_variance_absolute_difference': abs(general_variance - direct_variance),
        'general_direct_mean_relative_difference': abs(general_mean - direct_mean) / max(1., abs(direct_mean)),
        'general_direct_variance_relative_difference': abs(general_variance - direct_variance) / max(1., abs(direct_variance)),
        'simplified_direct_mean_difference': float(beta[0]) - direct_mean,
        'simplified_direct_variance_difference': float(beta[1:] @ beta[1:]) - direct_variance,
    }


def final_ols_diagnostics(a, y, beta, prune_relative=1e-4, rank_cutoff=1e-12):
    a = np.asarray(a, float); y = np.asarray(y, float); beta = np.asarray(beta, float)
    if a.ndim != 2 or y.shape != (len(a),) or beta.shape != (a.shape[1],):
        raise ValueError('matching design, response and coefficient dimensions required')
    if not all(np.all(np.isfinite(v)) for v in [a, y, beta]) or prune_relative < 0:
        raise ValueError('finite inputs and nonnegative pruning threshold required')
    threshold = max(1e-9, prune_relative * (np.max(np.abs(beta[1:])) if len(beta) > 1 else 0.))
    keep = np.abs(beta) > threshold; keep[0] = True
    coefficients, _, rank, singular = lstsq(a[:, keep], y, cond=rank_cutoff, check_finite=False)
    candidate = np.zeros(len(beta)); candidate[keep] = coefficients
    eligible = int(rank) == int(keep.sum())
    return {
        'threshold': float(threshold), 'retained_terms': int(keep.sum()), 'retained_rank': int(rank),
        'retained_indices': np.flatnonzero(keep), 'retained_singular_values': singular,
        'rank_relative_cutoff': rank_cutoff, 'eligible_final_ols': eligible,
        'reason': None if eligible else 'retained_design_not_full_column_rank',
        'fitted_coefficients': candidate if eligible else None,
        'diagnostic_minimum_norm_coefficients': candidate,
        'diagnostic_minimum_norm_constraint_relative_residual': float(np.linalg.norm(a @ candidate - y) / max(1., np.linalg.norm(y))),
        'coefficient_change_from_irls': float(np.linalg.norm(candidate - beta) / max(np.linalg.norm(beta), 1e-8)),
    }


def iterative_diagnostics(a, y, degree, initial_exponent, max_iter=100, tolerance=1e-6, epsilon=1e-8, prune_relative=1e-4):
    a = np.asarray(a, float); y = np.asarray(y, float)
    if a.ndim != 2 or y.shape != (len(a),) or not np.all(np.isfinite(a)) or not np.all(np.isfinite(y)):
        raise ValueError('finite design and matching observed response required')
    if not degree <= initial_exponent <= 3 * degree or max_iter < 1:
        raise ValueError('source-permitted p <= k <= 3p and positive iteration ceiling required')
    weights = (2 * np.arange(1, a.shape[1] + 1, dtype=float)) ** initial_exponent
    history = []; coefficients = []; warning_records = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        beta, initial = fixed_weight_dmorph(a, y, weights)
    warning_records.extend({'iteration': 0, 'message': str(v.message)} for v in caught)
    history.append({'iteration': 0, 'relative_change': None, 'weight_ratio': float(weights.max() / weights.min()), **initial})
    coefficients.append(beta.copy()); initial_weights = weights.copy()
    converged = False
    for iteration in range(1, max_iter + 1):
        weights = 1 / (np.abs(beta) + epsilon)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            nxt, diag = fixed_weight_dmorph(a, y, weights)
        warning_records.extend({'iteration': iteration, 'message': str(v.message)} for v in caught)
        change = float(np.linalg.norm(nxt - beta) / max(np.linalg.norm(beta), epsilon))
        history.append({'iteration': iteration, 'relative_change': change, 'weight_ratio': float(weights.max() / weights.min()), **diag})
        beta = nxt; coefficients.append(beta.copy())
        if change <= tolerance:
            converged = True
            break
    final = final_ols_diagnostics(a, y, beta, prune_relative)
    status = 'converged_identifiable' if converged and final['eligible_final_ols'] else ('nonconverged' if not converged else 'final_ols_unidentified')
    if initial['constraint_relative_residual'] > 1e-8:
        status = 'initial_constraint_residual_failure' if status == 'converged_identifiable' else status
    accepted = final['fitted_coefficients'] if status == 'converged_identifiable' else None
    return {
        'algorithm': 'D24_IRLS_k_equals_p_v2' if initial_exponent == degree else 'D24_P2B_3p_diagnostic_replay',
        'initial_exponent': initial_exponent, 'degree': degree, 'max_iter': max_iter,
        'tolerance': tolerance, 'epsilon': epsilon, 'prune_relative': prune_relative,
        'status': status, 'converged': converged, 'iterations': history, 'warnings': warning_records,
        'initial_weights': initial_weights, 'last_weights': weights,
        'iteration_coefficients': np.array(coefficients), 'last_irls_coefficients': beta,
        'accepted_final_coefficients': accepted, 'final_ols': final,
    }
