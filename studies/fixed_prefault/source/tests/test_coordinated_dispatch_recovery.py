"""Three synthetic saved-payload diagnostics; no environment or raw access."""
import copy
import importlib
import pytest


def api():
    try:return importlib.import_module('tools.coordinated_dispatch.recovery').extract_execution_recovery
    except ModuleNotFoundError:pytest.fail('Independent saved-payload recovery diagnostics missing')


def payload(decisions,segments=None,events=None,observed=600.):
    return dict(summary=dict(observed_until_minute=observed,requested_end_minute=600.,
        status='completed' if observed==600. else 'solver_error'),decisions=decisions,
        segments=segments or [],events=events or [])


def decision(time,mode,key='coordination_state'):
    return dict(decision_time_min=time,policy_diagnostic={key:mode})


def segment(start,end,availability,flags):
    return dict(start_minute=start,end_minute=end,availability=availability,
        pre_state={'extra_faulted':flags},post_state={'extra_faulted':flags})


def test_control_mode_exits_are_not_service_recovery_and_reentry_stays_open():
    p=payload([decision(0.,'NORMAL'),decision(15.,'SUPPORT'),decision(30.,'RECOVERY'),
        decision(45.,'NORMAL'),decision(60.,'RECOVERY')]);before=copy.deepcopy(p)
    r=api()(p);m=r['control_modes']
    assert m['recovery_entries_minutes']==[30.,60.]
    assert m['recovery_exits']==[dict(observed_minute=45.,destination='NORMAL',to_baseline_mode=True)]
    assert m['episodes'][-1]['exit_observed_minute'] is None and m['episodes'][-1]['right_censored'] is True
    assert r['mode_exit_is_service_recovery'] is False and p==before
    legacy=api()(payload([decision(0.,'prepare','mode'),decision(15.,'recovery','mode'),decision(30.,'emergency','mode')]))
    assert legacy['control_modes']['recovery_exits']==[dict(observed_minute=30.,destination='emergency',to_baseline_mode=False)]


def test_fault_end_registry_availability_return_and_protection_clear_are_separate():
    p=payload([],segments=[segment(0.,10.,{'GRID':0.,'GT_1':0.,'HRSG_1':0.},['GT_1']),
        segment(10.,20.,{'GRID':0.,'GT_1':0.,'HRSG_1':1.},['GT_1']),
        segment(20.,30.,{'GRID':1.,'GT_1':0.,'HRSG_1':1.},['GT_1']),
        segment(30.,600.,{'GRID':1.,'GT_1':1.,'HRSG_1':1.},[])],
        events=[dict(kind='exogenous_fault_end',time_minute=5.,details={'GRID':0.,'GT_1':0.})])
    r=api()(p);devices={x['device']:x for x in r['device_health']['devices']}
    assert devices['GRID']['recorded_fault_end_minutes']==[5.]
    assert devices['GRID']['first_effective_full_return_observed_minute']==20.
    assert devices['HRSG_1']['first_effective_full_return_observed_minute']==10.
    assert devices['GT_1']['first_effective_full_return_observed_minute']==30.
    assert devices['GT_1']['first_protection_flag_clear_observed_minute']==30.
    assert all(x['physical_repair_time_minute'] is None for x in devices.values())
    assert r['availability_is_actual_output'] is False and r['fault_end_is_device_repair'] is False


def test_unknown_tail_missing_mode_and_unobserved_device_return_are_not_zero():
    p=payload([decision(0.,'SUPPORT'),decision(15.,'RECOVERY')],
        segments=[segment(0.,30.,{'GRID':0.},['GT_1'])],observed=30.,events=[
            dict(kind='exogenous_fault_end',time_minute=15.,details={'GRID':0.}),
            dict(kind='exogenous_fault_end',time_minute=50.,details={'GRID':0.})])
    r=api()(p);devices={x['device']:x for x in r['device_health']['devices']}
    assert r['unknown_terminal_tail'] is True
    assert r['control_modes']['first_recovery_exit_observed_minute'] is None
    assert devices['GRID']['first_effective_full_return_observed_minute'] is None
    assert devices['GRID']['recorded_fault_end_minutes']==[15.]
    missing=api()(payload([],observed=30.))
    assert missing['control_modes']['extraction_status']=='not_extracted'
    assert missing['control_modes']['first_recovery_exit_observed_minute'] is None
