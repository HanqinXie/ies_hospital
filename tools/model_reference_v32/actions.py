"""Explicit offline plan -> ordinary queued requests; no state or storage writes."""
import copy,math
from . import METHOD_ID,EXECUTION_ID
from .planner import audit_plan

def at(value,i):return float(value[i] if isinstance(value,(list,tuple)) else value)

def extract_requests(spec,plan,*,formulation,preparation_lead_minutes=45.,objective_locks=None):
    if plan is None or not audit_plan(spec,plan,formulation=formulation,objective_locks=objective_locks)['valid']:
        raise ValueError('A verified adopted incumbent is required; no empty-plan fallback')
    if not math.isfinite(preparation_lead_minutes) or preparation_lead_minutes<0.:raise ValueError('Invalid fixed preparation lead')
    starts=[];clock=float(spec.get('start_minute',0.))
    for dt in spec['duration_h']:starts.append(clock);clock+=float(dt)*60.
    n=len(starts);mapping=spec['dispatch_mapping'];gens={g['name']:g for g in spec['generators']};requests=[]
    for h,minute in enumerate(starts):
        future=[j for j in range(h,n) if starts[j]<=minute+preparation_lead_minutes+1e-9];gt=[];preparations=[]
        for name in mapping['gt_names']:
            target=0. if name is None else min(max(float(plan[name][j]) for j in future),at(gens[name]['capacity'],h))
            gt.append(target)
            if name and target>float(plan[name][h])+1e-7:preparations.append(dict(device=name,current_plan_kW=plan[name][h],requested_kW=target,basis='known-window future target maximum clipped by current exogenous capacity'))
        boiler=min(max(float(plan['boiler'][j]) for j in future),at(spec['boiler']['capacity'],h))
        if boiler>float(plan['boiler'][h])+1e-7:preparations.append(dict(device='boiler',current_plan_kW_h=plan['boiler'][h],requested_kW_h=boiler))
        ec=[float(plan[name][h]) if name else 0. for name in mapping['ec_names']];ab=float(plan['ABS'][h])
        tower=ab*at(spec['absorption'].get('tower_per_c',0.),h)+sum(float(plan[obj['name']][h])*at(obj.get('tower_per_c',0.),h) for obj in spec['electric_chillers'])
        steam=sum(p*at(mapping['gt_steam_per_e'][i],h) for i,p in enumerate(gt))
        fuel=sum(gt[i]*at(gens[name].get('fuel_slope',0.),h)+(gt[i]>1e-9)*at(gens[name].get('fuel_fixed',0.),h) for i,name in enumerate(mapping['gt_names']) if name)
        dispatch=dict(gt_electric_kW=gt,grid_import_kW=float(plan['grid'][h]),diesel_kW=float(plan[mapping['dg_name']][h]) if mapping.get('dg_name') else 0.,
            boiler_steam_lbph=boiler*float(mapping['boiler_heat_to_steam']),absorption_cooling_kW=ab,electric_chiller_cooling_kW=ec,
            hrsg_steam_lbph=steam,tower_rejection_kW=tower,gt_fuel_MMBtu_h=fuel)
        corrections=[]
        for field,v in list(dispatch.items()):
            values=v if isinstance(v,list) else [v];revised=[]
            for i,x in enumerate(values):
                if not math.isfinite(x):raise ValueError('Nonfinite plan request')
                if x<0.:
                    if x < -1e-9:raise ValueError('Material negative plan request')
                    corrections.append(dict(field=field,index=i,before=x,after=0.,reason='solver_nonnegative_boundary_roundoff'));x=0.
                revised.append(x)
            dispatch[field]=revised if isinstance(v,list) else revised[0]
        action={'ordinary_dispatch':dict(dispatch=dispatch,disconnected_kW=[[0.,0.,0.] for _ in range(4)],rule_diagnostic=dict(policy_id=METHOD_ID,
            information_permission='offline_known_window',full_continuous_plan_replayed=False,planned_storage_ports_executed=False,
            planned_unmet_directly_executed=False,preparation_lead_minutes=preparation_lead_minutes))}
        from tools.sci_engineering.policy import validate_action
        validate_action(action)
        requests.append(dict(time_minute=minute,period=h,preparation_requests=preparations,numeric_roundoff_corrections=corrections,action=action,
            unexecuted_storage_plan={k:float(plan[k][h]) for s in spec['storages'] for k in (s['name']+'.charge',s['name']+'.discharge',s['name']+'.stock')},
            unexecuted_unmet_plan={f'P{g}':{c:float(plan.get(f'unmet.P{g}.{c}',[0.]*n)[h]) for c in 'ehc'} for g in range(4)},
            requested_dispatch_is_not_execution_receipt=True))
    return dict(schema='hospital_ies.v32.frozen_offline_requests.v1',method_id=METHOD_ID,execution_identity=EXECUTION_ID,formulation=formulation,
        information_permission='offline_known_window',preparation_lead_minutes=preparation_lead_minutes,full_continuous_plan_replayed=False,
        planned_storage_ports_executed=False,planned_unmet_directly_executed=False,requests=requests,
        adaptation_limits=['No ordinary UPS/cold charge/discharge command ports','Plan unmet is not mapped to active disconnection',
            'Common M23 current minimum preparation may enlarge boiler/EC ceilings after the frozen controller request',
            'Actual queue, powered startup, minimum on/off, ramp, transfer, protection and local reallocation remain original'])
