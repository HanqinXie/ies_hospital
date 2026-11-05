"""Opt-in control preparation at the original pre-queue hook; no physical fork."""
from __future__ import annotations
import copy
from types import SimpleNamespace
from tools.model_review_v30.environment import with_terminal as original_adapter
from tools.model_correctness_v23.policy import SciPolicy as OriginalM23Policy
from .rules import observe_boiler_capacity,prepare_steam_permission
from . import METHODS

class SteamPermissionPolicy(OriginalM23Policy):
    def __init__(self,alias,config=None,policy_id=None,plant=None,*,mailbox):
        if alias!='M':raise ValueError('V31 steam preparation is registered only on M')
        super().__init__(alias,config=config,policy_id=policy_id,plant=plant)
        self.mailbox=mailbox
    def reset_policy_memory(self):
        super().reset_policy_memory();self.mailbox.clear()
    def decide(self,records):
        action,diagnostic=super().decide(records)
        receipt=observe_boiler_capacity(records[-1],self.plant.boiler_steam_lbph)
        self.mailbox.clear();self.mailbox.update(copy.deepcopy(receipt))
        diagnostic['v31_current_boiler_observation']=receipt
        return action,diagnostic

class SteamPermissionMixin:
    def _prepare_command_dispatch(self,dispatch,diagnostic,loads):
        dispatch,diagnostic=super()._prepare_command_dispatch(dispatch,diagnostic,loads)
        return prepare_steam_permission(dispatch,diagnostic,self.env._v31_permission_mailbox,self.time)

def with_preparation(method,overlay_spec=None,*,thermal_profile=None):
    if method not in (None,*METHODS):raise ValueError('Unregistered V31 preparation method')
    adapter=original_adapter(overlay_spec,thermal_profile=thermal_profile)
    if method!='M_steam_permission':return adapter
    mailbox={}
    def create(*args,**kwargs):
        env=adapter.OperatingHospitalIESEnv(*args,**kwargs)
        env._v31_permission_mailbox=mailbox
        env.engineering_core_type=type('V31QueuedSteamPermissionCore',(SteamPermissionMixin,env.engineering_core_type),{})
        return env
    def factory(alias,config=None,plant=None,policy_id=None):
        return SteamPermissionPolicy(alias,config=config,plant=plant,policy_id=policy_id,mailbox=mailbox)
    return SimpleNamespace(validate_scenario=adapter.validate_scenario,validate_events=adapter.validate_events,
        OperatingHospitalIESEnv=create,run_episode=adapter.run_episode,controller_factory=factory)
