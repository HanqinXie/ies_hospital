"""Numerical scope wiring with fake intervals, no IES model or native solve."""
import copy

import pytest

from test_annual_program_indexed_integration import _driver, _IndexedFixtureEnv
from hospital_ies.engineering_numerics import engineering_profile, active_engineering_numerics
from hospital_ies.engineering_validation import active_validation_cache
from tools.single_fault_fuel_20261102.fuel import FuelCurve, CONTRACT


def curve():
    return FuelCurve(800.,400.,29477.,1954.9,.9851,-.327,(('fixture-only','a'*64),),CONTRACT)


@pytest.mark.parametrize('fail_native',[False,True])
def test_public_driver_preserves_exact_fake_reset_and_records_success_or_failure(tmp_path,monkeypatch,fail_native):
    module,case,output,runtimes,_=_driver(tmp_path,monkeypatch,fail_native=fail_native)
    case['numerical_execution']=engineering_profile()
    observed=[];steps=[];original=_IndexedFixtureEnv.reset;original_step=_IndexedFixtureEnv.step
    def reset(env,**kwargs):
        numerics=active_engineering_numerics();validation=active_validation_cache()
        observed.append((numerics is not None, validation is not None))
        assert numerics is None and validation is not None, 'Reference initialization must retain exact numerics'
        assert env.engineering_numerical_profile==engineering_profile()
        assert env.operating_model_config['engineering_numerical_profile']==engineering_profile()
        assert curve().identity==curve().identity
        return original(env,**kwargs)
    monkeypatch.setattr(_IndexedFixtureEnv,'reset',reset)
    def step(env,request):
        steps.append(active_engineering_numerics() is not None)
        return original_step(env,request)
    monkeypatch.setattr(_IndexedFixtureEnv,'step',step)
    result=module.run_case(case,output,approval_receipt={'fixture_only':True})
    assert result['status']==('failed' if fail_native else 'completed'),result.get('error')
    assert observed==[(False,True)]
    assert steps==[True], 'Engineering execution must resume after exact initialization'
    assert result['numerical_profile']=='engineering_fast_v1'
    assert result['numerical_execution']==engineering_profile()
    assert result['engineering_numerics']['closed'] is True
    validation=result['engineering_validation']
    assert validation['namespaces']['fuel_curve.identity']['hits']==1
    assert [entry['reason'] for entry in validation['invalidations']]==[
        'start_full_source_validation','publication_full_source_validation']
    assert validation['entries']==0
    assert active_engineering_numerics() is None and active_validation_cache() is None
    for env in runtimes:
        assert not any(type(value).__name__ in ('EngineeringNumerics','_ParameterCache') for value in vars(env).values())
    resolved=module._read(output/'RESOLVED_CASE.json')
    assert resolved['numerical_execution']==engineering_profile()
    assert module._read(output/'RESULT.json')['engineering_validation']==validation


def test_tampered_profile_is_refused_before_new_output(tmp_path,monkeypatch):
    module,case,output,_,_=_driver(tmp_path,monkeypatch)
    case['numerical_execution']=copy.deepcopy(engineering_profile())
    case['numerical_execution']['tolerances']['physical_rows_bounds']=.5
    with pytest.raises(ValueError,match='numerical.*(descriptor|policy)|canonical'):
        module.run_case(case,output,approval_receipt={'fixture_only':True})
    assert not output.exists()


def test_checkpoint_boundary_invalidates_before_capture_without_source_read(tmp_path):
    from tools.annual_program import runner
    events=[]
    def boundary(reason):
        events.append(reason)
        raise RuntimeError('fixture-stop-before-capture')
    try:
        callback=runner._checkpoint_callback(tmp_path,{},validation_boundary=boundary)
    except TypeError:
        pytest.fail('Checkpoint callback has no external cache invalidation hook')
    with pytest.raises(RuntimeError,match='fixture-stop-before-capture'):
        callback()
    assert events==['checkpoint']
