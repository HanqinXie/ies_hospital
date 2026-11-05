"""Named current-time permission study over the unchanged V30 physical core."""
from __future__ import annotations
import copy, math
from types import SimpleNamespace
from tools.model_review_v30.environment import with_terminal
from tools.model_correctness_v23.policy import SciPolicy
from tools.model_closeout_v31.rules import observe_boiler_capacity, prepare_steam_permission
from . import METHODS, STUDY_ID

def _number(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0.:
        raise ValueError('Invalid finite nonnegative '+name)
    return float(value)

def _values(value):
    if isinstance(value,dict):return [x for v in value.values() for x in _values(v)]
    if isinstance(value,(list,tuple)):return [x for v in value for x in _values(v)]
    return [_number(value,'event magnitude')]

def response_windows(scene,contract,*,observed_until=None,merge=True):
    """Simulator-only causal response windows; never exposed to the policy.

    event_triggered retains the existing release-at-window-end contract but
    triggers only on an actual registered fault/demand onset, independently of
    category labels. registered_window is the exact historical stage anchor.
    """
    if contract not in ('event_triggered','registered_window'):
        raise ValueError('Unknown response clock contract')
    delay=_number(scene.get('response_delay_minutes',0.),'response wait')
    if delay==0.:return []
    if contract=='registered_window':
        prefix='fault' if scene['stage'] in (1,3) else 'shock'
        start=_number(scene.get(prefix+'_start_minute',0.),'registered response start')
        return [(start,start+delay)]
    if scene.get('exogenous_events'):
        raise ValueError('This study requires explicit base fault/shock or model_closure clocks, not legacy exogenous_events')
    starts=[]
    fault=_values(scene.get('faults',{}))+_values(scene.get('auxiliary_faults',{}))
    shock=sum((_values(scene.get(k,{})) for k in ('shock','shock_by_priority','shock_by_zone')),[])
    if any(v<1.-1e-9 for v in fault) and _number(scene.get('fault_duration_minutes',0.),'fault duration')>0.:
        starts.append(_number(scene.get('fault_start_minute',0.),'fault start'))
    if any(v>0. for v in shock) and _number(scene.get('shock_duration_minutes',0.),'shock duration')>0.:
        starts.append(_number(scene.get('shock_start_minute',0.),'shock start'))
    previous={}
    for event in sorted(scene.get('model_closure_events',[]),key=lambda e:e['time_minute']):
        minute=_number(event['time_minute'],'closure event start')
        availability=event.get('availability',{})
        if any(_number(v,'availability')<previous.get(k,1.)-1e-9 for k,v in availability.items()):
            starts.append(minute)
        previous.update(availability)
    windows=[]
    for start in sorted(set(starts)):
        if observed_until is not None and start>observed_until:continue
        if not merge:
            windows.append((start,start+delay));continue
        if windows and start<=windows[-1][1]:windows[-1]=(windows[-1][0],max(windows[-1][1],start+delay))
        else:windows.append((start,start+delay))
    return windows

class StudyClockMixin:
    def delayed_at(self,t):
        # A future second onset may define a simulator boundary, but cannot
        # extend today's command release before that onset has occurred.
        for start,end in response_windows(self.env.scenario,self.env.study_clock_contract,observed_until=t):
            if start<=t<end:return True,end
        return False,t
    def _next_boundary(self,end):
        boundary=super()._next_boundary(end)
        future=[v for pair in response_windows(self.env.scenario,self.env.study_clock_contract,merge=False)
                for v in pair if v>self.time+1e-9]
        return min([boundary]+future)

def method_config(base,method):
    if method not in METHODS:raise ValueError('Unknown study method')
    if method=='C':
        from tools.model_correctness_v23.policy import policy_config
        if base.get('policy_mode')!='coordinated' or base.get('online_GT_count_order')!='after_cost' or policy_config('C',base)!=base:
            raise ValueError('Complete frozen C after_cost configuration required')
        return copy.deepcopy(base)
    from tools.model_closeout_v31.rules import expanded_method_config
    return expanded_method_config(base,'M_cooling_permission' if method in ('M_cooling','M_both') else 'M_steam_permission')

class StudySteamPolicy(SciPolicy):
    def __init__(self,alias,config=None,policy_id=None,plant=None,*,mailbox):
        super().__init__(alias,config=config,policy_id=policy_id,plant=plant)
        self.mailbox=mailbox
    def reset_policy_memory(self):
        super().reset_policy_memory();self.mailbox.clear()
    def decide(self,records):
        request,diagnostic=super().decide(records)
        receipt=observe_boiler_capacity(records[-1],self.plant.boiler_steam_lbph)
        self.mailbox.clear();self.mailbox.update(copy.deepcopy(receipt))
        diagnostic['study_current_boiler_observation']=receipt
        return request,diagnostic

class StudySteamMixin:
    def _prepare_command_dispatch(self,dispatch,diagnostic,loads):
        dispatch,diagnostic=super()._prepare_command_dispatch(dispatch,diagnostic,loads)
        return prepare_steam_permission(dispatch,diagnostic,self.env.study_boiler_mailbox,self.time)

def with_study(method,clock_contract,*,overlay_spec=None,thermal_profile=None):
    if method not in METHODS:raise ValueError('Unknown study method')
    if clock_contract not in ('event_triggered','registered_window'):raise ValueError('Unknown response clock contract')
    original=with_terminal(overlay_spec,thermal_profile=thermal_profile)
    mailbox={}
    def create(*args,**kwargs):
        env=original.OperatingHospitalIESEnv(*args,**kwargs)
        env.study_clock_contract=clock_contract;env.study_boiler_mailbox=mailbox
        bases=(StudyClockMixin,StudySteamMixin,env.engineering_core_type) if method in ('M_steam','M_both') else (StudyClockMixin,env.engineering_core_type)
        env.engineering_core_type=type('OperatingRulesStudyCore',bases,{})
        env.operating_model_config['operating_rules_study']=dict(study_id=STUDY_ID,method=method,clock_contract=clock_contract,
            physical_core_unchanged=True,online_future_event_access=False,permission_is_actual_output=False)
        return env
    def factory(alias,config=None,plant=None,policy_id=None):
        if method in ('M_steam','M_both'):
            return StudySteamPolicy(alias,config=config,policy_id=policy_id,plant=plant,mailbox=mailbox)
        return SciPolicy(alias,config=config,policy_id=policy_id,plant=plant)
    return SimpleNamespace(validate_scenario=original.validate_scenario,validate_events=original.validate_events,
        OperatingHospitalIESEnv=create,run_episode=original.run_episode,controller_factory=factory)
