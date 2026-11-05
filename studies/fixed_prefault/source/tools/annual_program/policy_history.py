"""Lossless reduction for the frozen causal M/C policy history.

The original controller validates and processes each actual current observation.
Its sole historical numerical dependency is the componentwise demand minimum;
we retain that minimum with exact ordering and archive observations separately.
No synthetic observation or future value is passed into the original controller.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from tools.sci_engineering.design import digest
from tools.sci_engineering.policy import validate_action

SCHEMA='hospital_ies.annual_program.exact_policy_history.v1'
SUPPORTED_CLASSES={
    'tools.model_correctness_v23.policy.SciPolicy',
    'tools.operating_rules_study.environment.StudySteamPolicy'}


def _identity(controller):
    name=type(controller).__module__+'.'+type(controller).__qualname__
    if name not in SUPPORTED_CLASSES or controller.alias not in ('M','C'):
        raise ValueError('Only the frozen M/C current-observation controllers are supported')
    plant=None if controller.plant is None else vars(controller.plant)
    from tools.model_review_v27.objective_snapshot import _encode
    return digest(dict(controller_class=name,alias=controller.alias,
        policy_id=controller.policy_id,config=controller.config,plant=_encode(plant)))


class ExactPolicyHistory:
    def __init__(self,controller):
        self.controller=controller
        self.controller_identity=_identity(controller)
        self.previous_minute=None
        self.minimum_demand_kW=None
        self.observation_count=0

    @property
    def retained_observation_count(self):return 0

    def decide(self,record):
        if _identity(self.controller)!=self.controller_identity:
            raise ValueError('Frozen controller identity/configuration changed')
        if not isinstance(record,dict):raise ValueError('Actual current observation required')
        minute=float(record['decision_time_min'])
        if not math.isfinite(minute) or minute<0. or (
                self.previous_minute is not None and minute<=self.previous_minute):
            raise ValueError('Actual observations must have strictly increasing causal clocks')
        # All original feature, provenance, availability and current-witness
        # checks still run on the unmodified real observation.
        request,diagnostic=self.controller.decide([copy.deepcopy(record)])
        f=record['features']
        current=np.asarray([[f[f'P{p}_{c}_demand_kW'] for c in ('e','h','c')]
            for p in range(4)],dtype=float)
        minimum=current if self.minimum_demand_kW is None else np.minimum(
            np.asarray(self.minimum_demand_kW,dtype=float),current)
        if set(request)!={'state_coordinated_joint'}:
            raise ValueError('Frozen M/C controller no longer has the supported historical dependency')
        request['state_coordinated_joint']['risk_loads_kW']=minimum.tolist()
        request=validate_action(request)
        self.previous_minute=minute
        self.minimum_demand_kW=minimum.tolist()
        self.observation_count+=1
        return request,diagnostic

    def to_state(self):
        value=dict(schema=SCHEMA,controller_identity=self.controller_identity,
            previous_minute=self.previous_minute,minimum_demand_kW=copy.deepcopy(self.minimum_demand_kW),
            observation_count=self.observation_count)
        return dict(value,content_sha256=digest(value))

    @classmethod
    def from_state(cls,state,*,controller):
        if not isinstance(state,dict):raise ValueError('Exact history state required')
        value=copy.deepcopy(state);checksum=value.pop('content_sha256',None)
        if value.get('schema')!=SCHEMA or digest(value)!=checksum:
            raise ValueError('Policy history integrity/identity mismatch')
        obj=cls(controller)
        if obj.controller_identity!=value['controller_identity']:
            raise ValueError('Policy history belongs to a different frozen controller')
        n=value['observation_count'];minute=value['previous_minute'];minimum=value['minimum_demand_kW']
        if type(n) is not int or n<0:raise ValueError('Invalid policy observation count')
        if n==0:
            if minute is not None or minimum is not None:raise ValueError('Invalid empty policy history')
        else:
            if isinstance(minute,bool) or not isinstance(minute,(int,float)) or not math.isfinite(minute) or minute<0.:
                raise ValueError('Invalid previous policy clock')
            a=np.asarray(minimum,dtype=float)
            if a.shape!=(4,3) or not np.isfinite(a).all() or np.any(a<0.):
                raise ValueError('Invalid historical demand minimum')
        obj.previous_minute=minute;obj.minimum_demand_kW=copy.deepcopy(minimum);obj.observation_count=n
        return obj
