"""Bounded original-domain engineering reuse; these tests use tiny native models."""
import importlib
import importlib.util

import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult


def api():
    assert importlib.util.find_spec('hospital_ies.engineering_numerics') is not None, 'engineering profile API is required'
    return importlib.import_module('hospital_ies.engineering_numerics')


def backend():
    return importlib.import_module('hospital_ies.solver_backend')


def solve(c=(1., 0.), rhs=1., **kwargs):
    return backend().milp(c, bounds=Bounds([0., 0.], [2., 2.]),
        constraints=[LinearConstraint([[1., 1.]], [rhs], [rhs])],
        options={'time_limit': 6., 'mip_rel_gap': 1e-6}, **kwargs)


def test_profile_is_explicit_and_scope_restores_exact_default():
    a = api(); b = backend()
    assert a.engineering_numerics_stats() is None
    exact = b.descriptor()
    p = a.engineering_profile()
    assert p['profile'] == 'engineering_fast_v1'
    # Exploratory wider tolerances/model pools were quarantined after the
    # matched trajectory failures; production keeps the original exact gates.
    assert p['tolerances']['physical_rows_bounds'] == 1e-7
    assert p['tolerances']['normalized_locks'] == 1e-10
    with a.engineering_numerics_scope() as state:
        assert b.descriptor()['engineering_profile'] == p
        assert a.engineering_numerics_stats()['profile'] == p['profile']
        assert state.stats()['checkpointed'] is False
    assert b.descriptor() == exact
    assert a.engineering_numerics_stats() is None


def test_current_domain_zero_bound_skips_optimizer_without_false_native_status():
    a = api()
    with a.engineering_numerics_scope() as state:
        first = solve()
        second = solve()
        assert first.status == second.status == 0
        assert second.optimizer_called is False
        assert second.native_status is None
        assert second.attempts[0]['optimizer_called'] is False
        assert 'native_optimize_performed' not in second.attempts[0]
        proof = second.engineering_acceptance
        assert proof['mode'] == 'current_domain_natural_bound'
        assert proof['original_domain_checked'] is True
        assert proof['optimality']['gap_upper_bound'] <= 1e-10
        assert state.stats()['native_calls'] == 1
        assert state.stats()['bound_skips'] == 1


def test_changed_current_rhs_rejects_old_candidate_and_reuses_native_model():
    a = api()
    with a.engineering_numerics_scope() as state:
        first = solve(rhs=1.)
        second = solve(rhs=1.25)
        assert second.status == 0 and second.optimizer_called is True
        assert abs(second.x.sum() - 1.25) < 1e-7
        assert abs(first.x.sum() - 1.) < 1e-7
        assert state.stats()['candidate_rejections'] >= 1
        assert state.stats()['native_model_builds'] == 0
        assert state.stats()['native_model_reuses'] == 0
        assert second.engineering_acceptance['mode'] == 'native_optimization'


def test_current_matrix_values_updated_without_changing_structure_or_truth():
    a = api(); b = backend()
    with a.engineering_numerics_scope() as state:
        r1 = b.milp([-1.], bounds=Bounds([0.], [3.]), constraints=LinearConstraint([[1.]], [-np.inf], [2.]))
        r2 = b.milp([-1.], bounds=Bounds([0.], [3.]), constraints=LinearConstraint([[2.]], [-np.inf], [2.]))
        assert r1.status == r2.status == 0
        assert r1.x[0] == pytest.approx(2.)
        assert r2.x[0] == pytest.approx(1.)
        assert len(r2.attempts) == 1 and r2.attempts[0]['error'] is None
        assert state.stats()['native_model_reuses'] == 0
        assert state.stats()['native_coefficient_updates'] == 0
        assert state.stats()['native_calls'] == 2


def test_old_positive_objective_is_not_a_proof_without_current_lower_bound():
    a = api(); b = backend()
    with a.engineering_numerics_scope() as state:
        first = b.milp([1., 1.], integrality=[1, 0], bounds=Bounds([0., 0.], [1., 10.]),
            constraints=LinearConstraint([[2., 1.]], [3.], [4.]))
        # Old primal stays feasible, but the new objective has a better binary choice.
        second = b.milp([4., 1.], integrality=[1, 0], bounds=Bounds([0., 0.], [1., 10.]),
            constraints=LinearConstraint([[2., 1.]], [3.], [4.]))
        assert first.fun == pytest.approx(2.)
        assert second.fun == pytest.approx(3.)
        assert second.optimizer_called is True
        assert state.stats()['native_calls'] == 2


def test_cached_zero_candidate_is_rechecked_against_new_priority_lock():
    a = api(); b = backend()
    with a.engineering_numerics_scope():
        first = solve()
        assert first.x[1] > .9
        with b.original_problem([[1., 1.], [0., 1.]], [1., -np.inf], [1., .25],
                [0., 0.], [2., 2.], [0, 0], [dict(kind='physical'), dict(kind='priority_lock')]):
            second = b.milp([1., 0.], bounds=Bounds([0., 0.], [2., 2.]),
                constraints=[LinearConstraint([[1., 1.], [0., 1.]], [1., -np.inf], [1., .25])])
        assert second.optimizer_called is True
        assert second.x[1] <= .25 + 1e-9
        assert second.fun == pytest.approx(.75)


def test_positive_objective_production_does_not_reuse_old_dual_bound():
    a = api(); b = backend()
    with a.engineering_numerics_scope() as state:
        first = b.milp([1.], bounds=Bounds([0.], [3.]), constraints=LinearConstraint([[1.]], [1.], [np.inf]))
        second = b.milp([1.], bounds=Bounds([0.], [3.]), constraints=LinearConstraint([[1.]], [1.], [np.inf]))
        assert first.fun == second.fun == pytest.approx(1.)
        assert second.optimizer_called is True
        assert second.engineering_acceptance['mode'] == 'native_optimization'
        assert second.engineering_acceptance['optimality']['lower_bound'] == pytest.approx(1.)
        assert state.stats()['dual_bound_skips'] == 0


def test_nested_exact_scope_disables_engineering_without_destroying_outer_pool():
    a = api()
    with a.engineering_numerics_scope() as state:
        solve()
        with a.engineering_numerics_scope(profile='exact'):
            r = solve()
            assert not hasattr(r, 'engineering_acceptance')
        replay = solve()
        assert replay.optimizer_called is False
        assert state.stats()['native_calls'] == 1


def test_engineering_physical_threshold_does_not_relax_priority_lock_more_than_declared(monkeypatch):
    a = api(); b = backend()
    def native(problem, parameters):
        return OptimizeResult(status=0, x=np.array([1. + 2e-8]), fun=1. + 2e-8,
            mip_dual_bound=1. + 2e-8, mip_gap=0., native_status=2, backend='gurobipy')
    monkeypatch.setattr(b, '_gurobi_attempt', native)
    with a.engineering_numerics_scope():
        with b.original_problem([[1.]], [-np.inf], [1.], [0.], [2.], [0], [dict(kind='priority_lock')]):
            r = b.milp([1.], bounds=Bounds([0.], [2.]), constraints=LinearConstraint([[1.]], [-np.inf], [1.]))
        assert r.status == 4
        assert all(not attempt['accepted'] for attempt in r.attempts)


def test_corrupt_nonfinite_performance_candidate_is_a_rejection_then_native_recovery():
    a = api()
    with a.engineering_numerics_scope() as state:
        solve()
        next(iter(state.candidates.values()))[-1][0] = np.nan
        result = solve()
        assert result.status == 0 and result.optimizer_called is True
        assert np.isfinite(result.x).all()
        assert state.stats()['candidate_rejections'] >= 1


def test_nonlinear_production_is_fresh_uses_current_graph_and_never_borrows_old_bound():
    a = api(); b = backend(); nl = importlib.import_module('hospital_ies.nonlinear')
    graph = nl.NonlinearSpec((nl.PowerLawEquality(0, 1, .1065990073870326, .673, 'a' * 64),))
    with a.engineering_numerics_scope() as state:
        args = dict(bounds=Bounds([128., 0.], [128., 10.]), nonlinear=graph)
        first = b.milp([0., 1.], **args)
        second = b.milp([0., 1.], **args)
        assert first.status == second.status == 0
        assert second.optimizer_called is True
        assert state.stats()['native_model_builds'] == 0
        assert state.stats()['native_model_reuses'] == 0
        # All nonlinear production reuse is retired, including zero goals.
        # Analytic/current-curve checks remain; linear zero skips are covered
        # independently by the strict-zero production tests.
        first_zero = b.milp([0., 0.], **args)
        assert first_zero.status == 0 and first_zero.optimizer_called is True
        zero = b.milp([0., 0.], **args)
        assert zero.status == 0 and zero.optimizer_called is True
        assert zero.native_status == 2
        assert zero.attempts[0]['nonlinear_validation']['accepted'] is True
        assert zero.engineering_acceptance['mode'] == 'native_optimization'
        changed = nl.NonlinearSpec((nl.PowerLawEquality(0, 1, .11, .673, 'a' * 64),))
        third = b.milp([0., 1.], bounds=Bounds([128., 0.], [128., 10.]), nonlinear=changed)
        assert third.optimizer_called is True
        assert third.x[1] == pytest.approx(.11 * 128. ** .673, abs=1e-7)
        assert state.stats()['native_model_builds'] == 0
        assert state.stats()['native_calls'] == 5
        assert not state.candidates


def test_nonlinear_production_preserves_original_tolerance_and_raw_values():
    from scipy import sparse
    a = api(); nl = importlib.import_module('hospital_ies.nonlinear')
    graph = nl.NonlinearSpec((nl.PowerLawEquality(0, 1, .1, .673, 'a' * 64),))
    raw = np.array([10., .1 * 10. ** .673 + 3e-7])
    original = dict(matrix=sparse.csr_matrix((0, 2)), lower=np.empty(0), upper=np.empty(0),
        bounds_lower=np.zeros(2), bounds_upper=np.ones(2) * 20., integrality=np.zeros(2), rows=[])
    assert nl.evaluate_nonlinear(graph, raw, np.zeros(2), original)['accepted'] is False
    with a.engineering_numerics_scope():
        check = nl.evaluate_nonlinear(graph, raw, np.zeros(2), original)
        assert check['accepted'] is False
        assert check['raw_x'] == raw.tolist()
        assert check['native_primal_repaired'] is False
    assert nl.evaluate_nonlinear(graph, raw, np.zeros(2), original)['accepted'] is False


def test_natural_bound_hit_does_not_evaluate_unnecessary_lp_dual_witnesses(monkeypatch):
    a = api()
    with a.engineering_numerics_scope():
        solve()
        def forbidden(*args):
            raise AssertionError('A known natural bound needs no LP dual calculation')
        monkeypatch.setattr(a, '_dual_bound', forbidden)
        assert solve().optimizer_called is False


def test_rejected_candidate_checks_all_rows_without_rendering_a_full_diagnostic_twice(monkeypatch):
    a = api(); diagnostics = importlib.import_module('hospital_ies.solver_diagnostics')
    original = diagnostics.validate_vector; calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(diagnostics, 'validate_vector', counted)
    with a.engineering_numerics_scope() as state:
        # Only exact-zero production candidates are remembered now. Their
        # rejection still checks every current row but does not render rows.
        solve(rhs=1.)
        result = solve(rhs=1.25)
        assert result.status == 0 and result.optimizer_called is True
        assert state.stats()['candidate_rejections'] == 1
        assert len(calls) == 0
        assert result.attempts[-1]['validation']['all_current_rows_checked'] is True
        # Retain the original full-render count check on the positive native
        # path; that path is deliberately not cached or compacted.
        solve(c=(1.,1.),rhs=1.)
        solve(c=(1.,1.),rhs=1.25)
        assert len(calls) == 2, 'Positive native results retain full diagnostic rows'
