from copy import deepcopy
from tools.single_fault_fuel_20261102.runtime import accepted_actual_attempt
from hospital_ies.engineering_numerics import engineering_profile


def attempt():
    return dict(status=0, native_status=None, error=None, optimizer_called=False,
        engineering_acceptance=dict(schema='hospital_ies.engineering_acceptance.v1',
            profile='engineering_fast_v1', mode='current_domain_natural_bound',
            proof_kind='current_domain_natural_bound', original_domain_checked=True,
            current_domain_sha256='a'*64,
            validation_thresholds=engineering_profile()['tolerances'],
            validation_summary=dict(max_original_violation=0.,max_lock_violation=0.,max_integrality_distance=0.),
            optimality=dict(lower_bound=0.,objective=0.,gap_upper_bound=0.,
                objective_bound_abs_tolerance=1e-10,native_optimal_status_claimed=False,
                previous_problem_bound_used=False),physical_state_replaced=False))


def test_engineering_skip_is_not_native_optimal_and_needs_registered_profile():
    value=attempt()
    assert not accepted_actual_attempt(value, None)
    assert accepted_actual_attempt(value, engineering_profile())
    bad=deepcopy(value);bad['engineering_acceptance']['optimality']['previous_problem_bound_used']=True
    assert not accepted_actual_attempt(bad, engineering_profile())
    bad=deepcopy(value);bad['native_status']=2
    assert not accepted_actual_attempt(bad, engineering_profile())


def test_exact_native_owner_unchanged():
    assert accepted_actual_attempt(dict(status=0,native_status=2,error=None),None)
    assert not accepted_actual_attempt(dict(status=0,native_status=None,error=None),None)
