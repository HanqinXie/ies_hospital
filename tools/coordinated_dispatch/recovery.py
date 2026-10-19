"""Pure saved-payload control-mode and observed device-state diagnostics.

No file/raw access, environment, physical solve, score or repair model. A mode
exit, an availability return and an exogenous fault-end record are distinct.
"""
from __future__ import annotations
import math

SCHEMA='coordinated_dispatch.execution_recovery_diagnostics.v1'


def _minute(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Invalid saved recovery timestamp')
    return float(value)


def _modes(payload,observed):
    episodes=[];active=None;missing=0;previous=-1.
    for decision in payload.get('decisions',[]):
        time=_minute(decision['decision_time_min'])
        if time<=previous or time>observed:raise ValueError('Saved mode observations are out of order or unobserved')
        previous=time;diagnostic=decision.get('policy_diagnostic') or {}
        source='coordination_state' if diagnostic.get('coordination_state') is not None else 'mode'
        mode=diagnostic.get(source)
        if mode is None:
            missing+=1
            if active is not None:active['censoring_reason']='missing_mode_diagnostic';active=None
            continue
        if not isinstance(mode,str) or not mode:raise ValueError('Invalid saved control mode')
        same=active is not None and active['source_field']==source
        if same and active['mode']==mode:
            active['last_observed_minute']=time;continue
        entry_kind='first_observed'
        if same:
            active.update(exit_observed_minute=time,exit_destination=mode,right_censored=False,censoring_reason=None)
            entry_kind='observed_mode_change'
        elif active is not None:active['censoring_reason']='mode_source_changed'
        active=dict(mode=mode,source_field=source,entry_observed_minute=time,entry_kind=entry_kind,
            last_observed_minute=time,exit_observed_minute=None,exit_destination=None,
            right_censored=True,censoring_reason='observation_ended_without_mode_exit')
        episodes.append(active)
    recovery=[r for r in episodes if r['mode'].upper()=='RECOVERY']
    exits=[dict(observed_minute=r['exit_observed_minute'],destination=r['exit_destination'],
        to_baseline_mode=r['exit_destination'].upper() in ('NORMAL','PREPARE'))
        for r in recovery if r['exit_observed_minute'] is not None]
    return dict(extraction_status='available' if episodes else 'not_extracted',episodes=episodes,
        missing_mode_decisions=missing,recovery_entries_minutes=[r['entry_observed_minute'] for r in recovery],
        recovery_exits=exits,first_recovery_entry_observed_minute=recovery[0]['entry_observed_minute'] if recovery else None,
        first_recovery_exit_observed_minute=exits[0]['observed_minute'] if exits else None,
        recovery_exit_status='observed' if exits else 'right_censored' if recovery else 'not_observed' if episodes else 'not_extracted',
        unclosed_recovery_episodes=sum(r['right_censored'] for r in recovery),
        timestamps_are_decision_observations=True,administrative_end_is_not_mode_exit=True)


def _devices(payload,observed,unknown_tail):
    availability_changes=[];flag_changes=[];fault_ends=[];states={};last_flags=None;last_flags_time=None
    last_availability={};state_observations=0;previous_end=-1.
    def record(device):
        if device not in states:
            states[device]=dict(device=device,first_effective_impairment_observed_minute=None,
                first_effective_full_return_observed_minute=None,first_protection_flag_clear_observed_minute=None,
                last_observed_effective_availability=None,last_availability_observation_minute=None,
                recorded_fault_end_minutes=[],physical_repair_time_minute=None,
                physical_repair_status='not_modelled_or_not_recorded')
        return states[device]
    def flags(snapshot,time):
        nonlocal last_flags,last_flags_time,state_observations
        if 'extra_faulted' not in snapshot:return
        value=snapshot['extra_faulted']
        if not isinstance(value,(list,tuple,set)) or any(not isinstance(x,str) for x in value):
            raise ValueError('Invalid observed protection flag set')
        current=set(value);state_observations+=1
        for device in current:record(device)
        if last_flags is not None:
            for device in sorted(last_flags-current):
                item=record(device)
                if item['first_protection_flag_clear_observed_minute'] is None:item['first_protection_flag_clear_observed_minute']=time
                flag_changes.append(dict(device=device,observed_minute=time,change='flag_cleared',physical_repair_proven=False))
            for device in sorted(current-last_flags):
                flag_changes.append(dict(device=device,observed_minute=time,change='flag_set',physical_repair_proven=False))
        last_flags=current;last_flags_time=time
    for segment in payload.get('segments',[]):
        start,end=map(_minute,(segment['start_minute'],segment['end_minute']))
        if end<=start or start<previous_end or end>observed:raise ValueError('Invalid or unobserved saved device segment')
        previous_end=end;flags(segment.get('pre_state') or {},start)
        for device,value in segment.get('availability',{}).items():
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:
                raise ValueError('Invalid observed availability fraction')
            state_observations+=1;item=record(device);old=last_availability.get(device)
            if value<1 and item['first_effective_impairment_observed_minute'] is None:
                item['first_effective_impairment_observed_minute']=start
            if old is not None and old!=value:
                availability_changes.append(dict(device=device,observed_minute=start,before=old,after=value,
                    change='full_effective_return' if old<1 and value==1 else 'partial_improvement' if value>old else 'availability_decreased'))
                if old<1 and value==1 and item['first_effective_full_return_observed_minute'] is None:
                    item['first_effective_full_return_observed_minute']=start
            last_availability[device]=value
            item.update(last_observed_effective_availability=value,last_availability_observation_minute=start)
        flags(segment.get('post_state') or {},end)
    for event in payload.get('events',[]):
        if event.get('kind')!='exogenous_fault_end':continue
        time=_minute(event['time_minute'])
        if time>observed:continue
        details=event.get('details')
        devices=sorted(details) if isinstance(details,dict) else [event['device']] if event.get('device') else []
        fault_ends.append(dict(observed_minute=time,devices=devices,event_id=event.get('event_id'),
            semantics='Observed registered exogenous fault boundary, not repair or actual output'))
        for device in devices:record(device)['recorded_fault_end_minutes'].append(time)
    for device,item in states.items():
        item.update(last_observed_protection_flagged=device in last_flags if last_flags is not None else None,
            last_protection_flags_observation_minute=last_flags_time,
            future_return_unknown=unknown_tail and item['first_effective_full_return_observed_minute'] is None)
    return dict(extraction_status='available' if state_observations else 'not_extracted',
        devices=[states[k] for k in sorted(states)],availability_changes=availability_changes,
        protection_flag_changes=flag_changes,registered_fault_end_events=fault_ends,
        effective_availability_return_is_physical_repair=False,protection_flag_clear_is_physical_repair=False,
        physical_repair_status='not_modelled_or_not_recorded',physical_repair_time_minute=None)


def extract_execution_recovery(payload):
    """Distinguish observed control exit, effective availability and fault registry.

    Existing service-recovery metrics remain authoritative for first P0 reach
    and sustained clean intervals. This helper never infers those from modes.
    """
    summary=payload['summary'];observed=_minute(summary['observed_until_minute'])
    requested=_minute(summary['requested_end_minute'])
    if observed>requested:raise ValueError('Observation exceeds the requested saved horizon')
    unknown=observed<requested
    return dict(schema=SCHEMA,extraction_status='extracted_from_existing_in_memory_payload',
        observed_until_minute=observed,requested_end_minute=requested,unknown_terminal_tail=unknown,
        control_modes=_modes(payload,observed),device_health=_devices(payload,observed,unknown),
        mode_exit_is_service_recovery=False,availability_is_actual_output=False,fault_end_is_device_repair=False,
        first_P0_and_sustained_service_recovery='Use the unchanged existing service metrics, not this diagnostic',
        missing_older_summary_field_status='not_extracted',additional_raw_opens=0,physical_episodes_started=0)
