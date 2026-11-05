"""Full causal history and exact streaming execution produce identical commands."""
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from tools.model_correctness_v23.policy import SciPolicy
from tools.operating_rules_study.environment import StudySteamPolicy


def implementation():
    try:return importlib.import_module('tools.annual_program.policy_history')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_program.policy_history':raise
        pytest.fail('Exact annual policy-history reduction is missing',pytrace=False)


def observed(n):
    t=15.*n
    f=dict(time_minute=t,grid_outage_elapsed_minutes=15.*(n%5))
    for p in range(4):
        for j,c in enumerate(('e','h','c')):
            f[f'P{p}_{c}_demand_kW']=float(50.+(n*(p+1)+j*17)%39)
    for i in (1,2,3):f[f'availability_BOILER_{i}']=float((n+i)%4!=0)
    return dict(decision_time_min=t,features=f,
        feature_metadata={k:dict(available_time_min=t,valid_time_min=t) for k in f},
        model_closure_v22=dict(observation_time_minute=t,available_time_minute=t,
            current_non_diesel_feasibility=dict(ready=n%2==0)))


@pytest.mark.parametrize('kind',['M','C','steam'])
def test_requests_diagnostics_and_mailbox_identical_for_every_prefix(kind):
    if kind=='steam':
        plant=SimpleNamespace(boiler_steam_lbph=3000.,ec_electric_kW_per_rt=.77,
            cooling_tower_rejection_kW=4000.,electric_chillers_rt=[100.]*3,
            steam_kWh_per_lb=.3,exchanger_eff=.9)
        old=StudySteamPolicy('M',plant=plant,mailbox={})
        new=StudySteamPolicy('M',plant=plant,mailbox={})
    else:old=SciPolicy(kind);new=SciPolicy(kind)
    streamed=implementation().ExactPolicyHistory(new);records=[]
    for n in range(80):
        row=observed(n);records.append(row);before=copy.deepcopy(records)
        assert streamed.decide(row)==old.decide(records)
        assert records==before
        if kind=='steam':assert old.mailbox==new.mailbox
    assert streamed.observation_count==80
    assert streamed.retained_observation_count==0


def test_roundtrip_preserves_historical_minimum_and_monotonicity():
    cls=implementation().ExactPolicyHistory;old=SciPolicy('M');records=[];stream=cls(SciPolicy('M'))
    for n in range(7):records.append(observed(n));stream.decide(records[-1])
    stream=cls.from_state(stream.to_state(),controller=SciPolicy('M'))
    for n in range(7,11):
        records.append(observed(n));assert stream.decide(records[-1])==old.decide(records)
    with pytest.raises(ValueError):stream.decide(observed(10))


def test_future_or_invalid_observation_does_not_commit_partial_history():
    stream=implementation().ExactPolicyHistory(SciPolicy('M'))
    stream.decide(observed(0));before=stream.to_state()
    bad=observed(1);bad['feature_metadata']['time_minute']['valid_time_min']=30.
    with pytest.raises(ValueError):stream.decide(bad)
    assert stream.to_state()==before
    bad=observed(1);bad['events']=[{'time_minute':100.}]
    with pytest.raises(ValueError):stream.decide(bad)
    assert stream.to_state()==before


def test_configuration_drift_and_tampered_accumulator_rejected():
    cls=implementation().ExactPolicyHistory;policy=SciPolicy('M');stream=cls(policy)
    stream.decide(observed(0));state=stream.to_state()
    changed=copy.deepcopy(state);changed['minimum_demand_kW'][0][0]=0.
    with pytest.raises(ValueError):cls.from_state(changed,controller=SciPolicy('M'))
    with pytest.raises(ValueError):cls.from_state(state,controller=SciPolicy('C'))
    policy.config['support_minutes']+=1.
    with pytest.raises(ValueError):stream.decide(observed(1))
