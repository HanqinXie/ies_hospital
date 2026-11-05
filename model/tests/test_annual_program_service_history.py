"""Incremental telemetry must equal the original complete-prefix arithmetic."""
import copy
import importlib
from types import SimpleNamespace as NS

import pytest

from tools.executed_service.history import make_snapshot


def implementation():
    try:
        return importlib.import_module('tools.annual_program.service_history')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_program.service_history':
            raise
        pytest.fail('Annual exact service accumulator is missing', pytrace=False)


def environment():
    return NS(config=dict(p0_solver_feasibility_tol_kW=.001,
        p0_unserved_abs_tol_kW=.1, p0_unserved_rel_tol=.01),
        core=NS(time=0., segments=[]))


def segment(start, end, n=0):
    return dict(start_minute=start, end_minute=end, command_id=n+1,
        loads_kW=[[10.+p+j for j in range(3)] for p in range(4)],
        unserved_kW=[[0. if (n+p+j)%3 else .23*(p+j+1) for j in range(3)]
            for p in range(4)])


def base(t):
    return dict(decision_time_min=t, observation_time_min=t, current_only={'keep': 1})


def test_ordered_segment_arithmetic_and_every_boundary_equal_original():
    env=environment(); acc=implementation().ServiceHistoryAccumulator(env.config)
    assert acc.snapshot(base(0.)) == make_snapshot(env,base(0.))
    for n,duration in enumerate([.25, 1., 7.5, 6.25, .1, 14.9, 15., 30.]):
        start=env.core.time; env.core.time+=duration
        s=segment(start,env.core.time,n); original=copy.deepcopy(s)
        env.core.segments.append(s); acc.append(s)
        actual=acc.snapshot(base(env.core.time))
        assert actual == make_snapshot(env,base(env.core.time))
        assert s == original
        assert acc.segment_count == n+1
    assert acc.retained_segment_count == 1


def test_state_roundtrip_continues_without_reset_or_double_counting():
    cls=implementation().ServiceHistoryAccumulator
    env=environment(); first=cls(env.config)
    for n,(start,end) in enumerate([(0.,7.5),(7.5,15.)]):
        s=segment(start,end,n);first.append(s);env.core.segments.append(s)
    restored=cls.from_state(first.to_state(),config=env.config)
    s=segment(15.,30.,2);restored.append(s);env.core.segments.append(s)
    env.core.time=30.
    assert restored.snapshot(base(30.)) == make_snapshot(env,base(30.))
    assert first.segment_count == 2 and restored.segment_count == 3


@pytest.mark.parametrize('bad', [segment(1.,2.),segment(0.,0.),segment(0.,-1.)])
def test_missing_overlapping_or_reversed_support_rejected(bad):
    acc=implementation().ServiceHistoryAccumulator(environment().config)
    with pytest.raises(ValueError):acc.append(bad)


def test_future_snapshot_and_changed_thresholds_rejected():
    cls=implementation().ServiceHistoryAccumulator;env=environment();acc=cls(env.config)
    acc.append(segment(0.,15.))
    with pytest.raises(ValueError):acc.snapshot(base(30.))
    changed=dict(env.config,p0_unserved_abs_tol_kW=.2)
    with pytest.raises(ValueError):cls.from_state(acc.to_state(),config=changed)


def test_unsupported_fields_and_negative_values_not_silently_filled():
    acc=implementation().ServiceHistoryAccumulator(environment().config)
    bad=segment(0.,15.);bad['unserved_kW'][2][0]=-1.
    with pytest.raises(ValueError):acc.append(bad)
    assert acc.segment_count==0
    with pytest.raises(ValueError):acc.snapshot(dict(base(0.),events=[]))


def test_resigned_state_cannot_disagree_about_material_thresholds():
    from tools.sci_engineering.design import digest
    cls=implementation().ServiceHistoryAccumulator;env=environment();acc=cls(env.config)
    acc.append(segment(0.,15.));state=acc.to_state()
    state['history']['material_thresholds']['absolute_kW']=100.
    state['history']['material_thresholds']['relative']=100.
    state.pop('content_sha256');state['content_sha256']=digest(state)
    with pytest.raises(ValueError,match='threshold'):cls.from_state(state,config=env.config)


def test_explicit_native_snapshot_adapter_uses_exact_committed_accumulator():
    env=environment();acc=implementation().ServiceHistoryAccumulator(env.config)
    for n,(start,end) in enumerate([(0.,7.5),(7.5,15.)]):
        s=segment(start,end,n);acc.append(s);env.core.segments.append(s)
    env.core.time=15.;expected=make_snapshot(env,base(15.))
    env._annual_service_history=acc
    # An explicitly activated incremental route no longer re-reads the prefix.
    env.core.segments=[]
    assert make_snapshot(env,base(15.))==expected
    env.config['p0_unserved_abs_tol_kW']=1.
    with pytest.raises(ValueError,match='threshold'):make_snapshot(env,base(15.))
