"""Bind simulator current inputs to new policies; reuse old actual exporter."""
from __future__ import annotations
import copy
import math
from tools.integrated_learning.episode import run_episode
from .runtime import extract_current_problem


class BoundaryBoundPolicy:
    def __init__(self,env,policy,*,extractor=extract_current_problem):
        self.env=env;self.policy=policy;self.policy_id=policy.policy_id;self.extractor=extractor
    def decide(self,records):
        self.policy.bind_current(self.extractor(self.env))
        return self.policy.decide(records)


class ObservationBoundEnvironment:
    """Administrative observer endpoint; never alter exogenous scenario/core."""
    def __init__(self,env,end_minute):
        self.env=env;self.observation_end=float(end_minute)
        if not math.isfinite(self.observation_end) or self.observation_end<=float(env.core.time):
            raise ValueError('Observation endpoint must follow current clock')
        if abs(self.observation_end/float(env.dt_minutes)-round(self.observation_end/float(env.dt_minutes)))>1e-9:
            raise ValueError('Observation endpoint must be an original decision boundary')
    def __getattr__(self,name):return getattr(self.env,name)
    def step(self,request):
        now=float(self.env.core.time)
        if now>=self.observation_end-1e-9 or now+float(self.env.dt_minutes)>self.observation_end+1e-9:
            raise ValueError('Registered observation endpoint already reached or would be crossed')
        obs,reward,done,truncated,info=self.env.step(request)
        return obs,reward,bool(done or self.env.core.time>=self.observation_end-1e-9),truncated,info


def finalize_bounded_observation(payload,end_minute):
    result=copy.deepcopy(payload);summary=result['summary'];observed=float(summary['observed_until_minute'])
    summary.update(requested_observation_end_minute=float(end_minute),
        original_scenario_end_minute=float(summary['requested_end_minute']),
        observation_scope='Registered administrative observation bound; unchanged original600 scenario')
    if summary['status']=='completed' and observed<float(summary['requested_end_minute'])-1e-9:
        if abs(observed-float(end_minute))>1e-9:raise ValueError('Bounded completed endpoint was not actually observed')
        first=summary.get('first_failure_observed_minute')
        summary.update(status='bounded_observation_completed',bounded_observation_completed=True,
            completed=False,scenario_completed=False,full_trajectory_outcome_valid=False,
            outcome_unknown_after_minute=observed,observed_prefix_physical_service_failure=first is not None,
            p0_failed_any=1 if first is not None else None,physical_service_failure=True if first is not None else None)
        for row in result['decisions']:
            for label in row.get('labels',{}).values():
                if not label['valid'] and label.get('unknown_reason')=='administrative_end':
                    label.update(unknown_reason='bounded_observation_end',administrative_censoring=True)
    else:summary['bounded_observation_completed']=False
    return result


def run_policy_episode(env,scenario,policy,seed,manifest,**options):
    observation_end=options.pop('observation_end_minute',None)
    if observation_end is not None:env=ObservationBoundEnvironment(env,observation_end)
    bound=BoundaryBoundPolicy(env,policy) if hasattr(policy,'bind_current') else policy
    payload=run_episode(env,scenario,bound,seed,manifest,**options)
    return payload if observation_end is None else finalize_bounded_observation(payload,observation_end)
