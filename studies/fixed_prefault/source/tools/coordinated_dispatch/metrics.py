"""Saved-payload descriptive accounting; no file reads, solver or score changes."""
from copy import deepcopy
import math

SCHEMA = 'coordinated_dispatch.saved_service_switch_metrics.v1'
WINDOWS = {'primary': (60.,420.), 'full': (0.,600.)}
DEVICES = ('GT_1','GT_2','GT_3','GRID','DIESEL')
EPS = 1e-8


def _number(value):
    value=float(value)
    if not math.isfinite(value):raise ValueError('Nonfinite saved metric input')
    return value


def _ports(action):
    gt=action['gt_electric_kW']
    if len(gt)!=3:raise ValueError('Expected three actual GT ports')
    return dict(zip(DEVICES,[_number(x) for x in gt]+[
        _number(action['grid_import_kW']),_number(action['diesel_kW'])]))


def _intervals(parts,predicate):
    runs=[]
    for a,b,s in parts:
        if not predicate(s):continue
        if runs and abs(runs[-1]['end_minute']-a)<=EPS:runs[-1]['end_minute']=b
        else:runs.append(dict(start_minute=a,end_minute=b))
    for r in runs:r['duration_minutes']=r['end_minute']-r['start_minute']
    return dict(total_minutes=sum(r['duration_minutes'] for r in runs),
        longest_minutes=max((r['duration_minutes'] for r in runs),default=0.),
        episode_count=len(runs),intervals=runs)


def _recovery(failed,observed_end,complete):
    runs=failed['intervals'];first=runs[0]['start_minute'] if runs else None
    clean=[(r['end_minute'],runs[i+1]['start_minute'] if i+1<len(runs) else observed_end)
           for i,r in enumerate(runs) if r['end_minute']<observed_end-EPS]
    first_clean=clean[0][0] if clean else None
    sustained=next(((a,a+30.) for a,b in clean if b-a>=30.-EPS),None)
    recurrences=[r['start_minute'] for r in runs if first_clean is not None and r['start_minute']>=first_clean-EPS]
    later=[t for t in recurrences if sustained and t>=sustained[1]-EPS]
    return dict(applicable=bool(runs),first_P0_failure_minute=first,first_recovery_minute=first_clean,
        confirmed_30min=True if sustained else (False if runs and complete else None),
        confirmed_30min_start=sustained[0] if sustained else None,
        confirmed_30min_minute=sustained[1] if sustained else None,
        recurrence_count=len(recurrences),recurrence_start_minutes=recurrences,
        recurrence_after_30min_count=len(later),recurrence_after_30min_start_minutes=later,
        sustained_after_30min_to_window_end=(not later if complete else (False if later else None)) if sustained else None)


def _end_ports(segment,time):
    if abs(time-segment['end_minute'])<=EPS and segment.get('terminal_action') is not None:
        return _ports(segment['terminal_action'])
    result=_ports(segment['action'])
    if 'source_start_gt_kW' in segment and 'source_end_gt_kW' in segment:
        ratio=(time-segment['start_minute'])/(segment['end_minute']-segment['start_minute'])
        for i in range(3):result[f'GT_{i+1}']=segment['source_start_gt_kW'][i]*(1-ratio)+segment['source_end_gt_kW'][i]*ratio
    return result


def _transitions(payload,tolerance):
    rows=[];initial=payload.get('initial_state') or {};state=initial.get('state',initial)
    previous=(_ports(dict(gt_electric_kW=state['gt_prev'],grid_import_kW=state['last_grid_import_kW'],
        diesel_kW=state['last_diesel_kW'])) if all(k in state for k in ('gt_prev','last_grid_import_kW','last_diesel_kW')) else None)
    last_grid=None
    def record(time,current,segment,basis):
        nonlocal previous
        for device,power in current.items():
            before=None if previous is None else previous[device]
            if before is None:
                if power>tolerance:rows.append(dict(time_minute=time,device=device,kind='initial_positive',power_before_kW=None,power_after_kW=power,basis=basis,command_id=segment.get('command_id')))
            elif (before>tolerance)!=(power>tolerance):
                rows.append(dict(time_minute=time,device=device,kind='activated' if power>tolerance else 'deactivated',
                    power_before_kW=before,power_after_kW=power,basis=basis,command_id=segment.get('command_id')))
        previous=current
    for s in payload['segments']:
        start=_ports(s['action'])
        if 'source_start_gt_kW' in s:
            for i,power in enumerate(s['source_start_gt_kW']):start[f'GT_{i+1}']=_number(power)
        record(s['start_minute'],start,s,'actual segment source start')
        grid=s.get('availability',{}).get('GRID')
        if last_grid is not None and grid is not None and grid!=last_grid:
            rows.append(dict(time_minute=s['start_minute'],device='GRID',kind='GRID_availability_changed',
                availability_before=last_grid,availability_after=grid,basis='offline observed segment label'))
        last_grid=grid
        record(s['end_minute'],_end_ports(s,s['end_minute']),s,'actual segment end or terminal action')
    return sorted(rows,key=lambda r:r['time_minute'])


def _decision_rows(payload):
    rows=[]
    for decision in payload.get('decisions',[]):
        c=decision.get('command_generated')
        if not isinstance(c,dict):continue
        d=c.get('joint_dispatch',{}).get('diagnostic',{});b=d.get('service_budget')
        row=dict(command_id=c['id'],generated_minute=_number(c['generated_minute']),
            planned_arrival_minute=_number(c['arrival_minute']),coordination_preference=d.get('coordination_preference'),
            budget_record_present=b is not None,budget_enabled=None if b is None else b['enabled'])
        for key in ('allowed_extra_kWh','used_extra_kWh','violation_kWh'):
            value=None if b is None else b[key]
            if value is not None:
                if len(value)!=2 or any(len(x)!=3 for x in value):raise ValueError('Budget record must be 2-by-3 kWh')
                value=[[_number(x) for x in part] for part in value]
            row[key]=value
        ports=_ports(c['dispatch'])
        row.update(planned_GT_kW=[ports[x] for x in DEVICES[:3]],planned_DIESEL_kW=ports['DIESEL'],planned_GRID_ceiling_kW=ports['GRID'])
        rows.append(row)
    return rows


def extract_metrics(payload,scenario,config=None):
    """Describe observed prefix and complete windows; consume an ALREADY read raw.

    P2/P3 material interruption extends the original instantaneous P0 kW/ratio
    thresholds. P0 uses saved classification unchanged. Thirty clean minutes
    after a within-window P0 failure is descriptive recovery, not clinical safety.
    Plan budgets sum issued decisions in [start,end), without execution weighting.
    """
    cfg=config or {};tol=_number(cfg.get('p0_solver_feasibility_tol_kW',1e-3))
    absolute=_number(cfg.get('p0_unserved_abs_tol_kW',.01));relative=_number(cfg.get('p0_unserved_rel_tol',.001))
    if min(tol,absolute,relative)<0:raise ValueError('Negative material threshold')
    segments=payload.get('segments',[]);previous=0.
    for s in segments:
        a=_number(s['start_minute']);b=_number(s['end_minute'])
        if abs(a-previous)>EPS or b<=a:raise ValueError('Segments must be contiguous from zero')
        if any(len(s[k])!=4 or any(len(row)!=3 for row in s[k]) for k in ('loads_kW','unserved_kW')):
            raise ValueError('Expected 4-by-3 load and unserved arrays')
        previous=b
    observed=_number(payload['summary']['observed_until_minute'])
    if abs(previous-observed)>EPS:raise ValueError('Segment endpoint differs from observed prefix')
    transitions=_transitions(payload,tol);decisions=_decision_rows(payload)
    result=dict(schema=SCHEMA,scenario_id=scenario.get('id'),analysis_class=scenario.get('analysis_class'),
        root_family_id=scenario.get('root_family_id'),policy_id=payload['summary'].get('policy_id'),
        observed_until_minute=observed,decision_rows=decisions,source_transition_rows=transitions,
        definitions=dict(material_thresholds=dict(solver_tol_kW=tol,absolute_kW=absolute,relative=relative),
            P2_P3_material='short>solver_tol and (short>=absolute_kW or short/demand>=relative); no cumulative P0 threshold extension',
            P0='saved classification unchanged; material gap includes e/h/c separately from guarantee failure',
            recovery='first clean interval after within-window first failure; confirmed after 30 continuous observed clean minutes; not clinical',
            planned_budget='sum original per-step kWh of commands generated in the half-open window; neither prorated actual execution nor an actual ENS guarantee',
            temperature='saved segment temperature maximum and classified-duration integration; no threshold interpolation; unavailable is null',
            source='actual segment ports/endpoints only; planned, CONNECTED and queued targets are not delivered power',
            gap='zero actual electrical supply with positive demand, or original P0 material shortfall; contemporaneous labels do not establish causation'))
    for window,(start,end) in WINDOWS.items():
        parts=[(max(start,s['start_minute']),min(end,s['end_minute']),s) for s in segments if s['end_minute']>start and s['start_minute']<end]
        complete=observed>=end-EPS;finish=min(end,max(start,observed));service={}
        for p in (2,3):
            service[f'P{p}']={}
            for k,carrier in enumerate('ehc'):
                def material(s,p=p,k=k):
                    loss=max(0.,_number(s['unserved_kW'][p][k]));demand=max(0.,_number(s['loads_kW'][p][k]))
                    ratio=loss/demand if demand>tol else 0.
                    return loss>tol and (loss>=absolute or ratio>=relative)
                item=_intervals(parts,material)
                item['ENS_kWh']=sum(_number(s['unserved_kW'][p][k])*(b-a)/60. for a,b,s in parts)
                service[f'P{p}'][carrier]=item
        temperature={}
        for priority in ('P0','P1','P2','P3'):
            available=bool(parts) and all(s.get('temperature',{}).get(priority) is not None for _,_,s in parts)
            temperature[priority]=dict(maximum_C=max(_number(s['temperature'][priority]) for _,_,s in parts),
                violation_minutes=sum(b-a for a,b,s in parts if s['violation'][priority])) if available else None
        gaps=dict(zero_electric_supply=_intervals(parts,lambda s:sum(_ports(s['action']).values())<=tol and
                sum(_number(row[0]) for row in s['loads_kW'])+_number(s.get('auxiliary_electric_kW',0.))>tol),
            P0_material_unserved=_intervals(parts,lambda s:any(s['classification']['material_unserved_by_carrier'].values())),
            P0_guarantee_failure=_intervals(parts,lambda s:s['classification']['p0_guarantee_failure']))
        for item in gaps.values():
            for interval in item['intervals']:
                interval['contemporaneous_transition_labels']=[r['kind']+':'+r['device'] for r in transitions if abs(r['time_minute']-interval['start_minute'])<=EPS]
        issued=[d for d in decisions if start<=d['generated_minute']<end and d['generated_minute']<=observed+EPS]
        budget_records=[d for d in issued if d['budget_record_present']]
        budget=dict(decision_count=len(issued),budget_record_count=len(budget_records),
            enabled_decision_count=sum(d['budget_enabled'] is True for d in budget_records))
        for key in ('allowed_extra_kWh','used_extra_kWh','violation_kWh'):
            budget[key]=[[sum(d[key][p][k] for d in budget_records) for k in range(3)] for p in range(2)] if budget_records else None
        prefix=dict(service=service,temperature=temperature,gaps=gaps,
            recovery=_recovery(gaps['P0_guarantee_failure'],finish,complete),planned_budget=budget,
            source_activations={d:sum(r['device']==d and r['kind']=='activated' and start<=r['time_minute']<end for r in transitions) for d in DEVICES},
            source_exits={d:sum(r['device']==d and r['kind']=='deactivated' and start<=r['time_minute']<end for r in transitions) for d in DEVICES},
            first_positive_output_minute={d:next((a for a,b,s in parts if _ports(s['action'])[d]>tol),None) for d in DEVICES},
            terminal_actual_sources_kW=_end_ports(parts[-1][2],finish) if parts else None,
            terminal_P0_failure=bool(parts[-1][2]['classification']['p0_guarantee_failure']) if parts else None)
        result[window]=dict(window_minutes=[start,end],complete=complete,observed_minutes=sum(b-a for a,b,s in parts),
            observed_prefix=prefix,all_registered=deepcopy(prefix) if complete else None)
    return result
