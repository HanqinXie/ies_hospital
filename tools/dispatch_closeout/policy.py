"""Current coupled readiness and sustainability preferences on six old ports.

No physical state is changed by this module. It plans once on the current
problem, then submits targets to the original service locks and queue.
"""
from __future__ import annotations
import copy
import math
import numpy as np
from hospital_ies.data_contract import validate_action
from hospital_ies.model import RT_TO_KW,gt_level_properties
from tools.control_improvement.runtime import project_current
from tools.integrated_learning.policies import ACTION_NAMES,legal_history

DEFAULTS=dict(sustain=True,support_hours=2.,downward_fraction=.85,
              minimum_fraction=.45,electric_buffer_fraction=.1)


def diesel_preference(planned,energy,observed_electric,*,support_hours,downward_fraction):
    floor=min(map(float,observed_electric))
    fuel=max(0.,float(energy))/support_hours
    downward=max(0.,floor)*downward_fraction
    return min(float(planned),fuel,downward),dict(fuel_bound_kW=fuel,
        historical_demand_floor_kW=floor,downward_preference_kW=downward,
        support_hours=support_hours,not_a_future_load_or_restoration_prediction=True)


def coupled_heat_kW(gt_power,gt_full,hrsg_full,steam_kWh_per_lb,exchanger_eff):
    gt=np.asarray(gt_power,float); caps=np.asarray(gt_full,float)
    alpha=np.divide(np.asarray(hrsg_full,float),caps,out=np.zeros(3),where=caps>0)
    return float(np.dot(gt,alpha)*steam_kWh_per_lb*exchanger_eff)


def allocate_targets(plan,capacities,hrsg_health,stress,*,grid_limit,diesel_target,ready_target,minimum_fraction,hold_minutes=15.):
    original=np.asarray(plan,float); caps=np.asarray(capacities,float)
    if original.shape!=(6,) or caps.shape!=(3,) or not np.all(np.isfinite(original)):
        raise ValueError('Invalid current source vector')
    result=original.copy(); total=float(original[:5].sum())
    requested_diesel=max(0.,min(float(original[4]),float(diesel_target)))
    diesel=max(requested_diesel,total-float(sum(caps))-grid_limit)
    gen_budget=max(0.,total-diesel)
    eligible=list(np.flatnonzero(caps>1e-8))
    weights=caps*(.25+.75*np.asarray(hrsg_health,float))*(1-.5*np.minimum(np.asarray(stress,float)/hold_minutes,1.))
    while eligible and gen_budget+1e-7<sum(minimum_fraction*caps[eligible]):
        eligible.remove(min(eligible,key=lambda i:weights[i]))
    targets=np.zeros(3);fallback=False
    if not eligible and gen_budget>grid_limit+1e-6:
        targets=np.asarray(original[:3],float).copy()
        diesel=float(original[4]);gen_budget=float(original[:4].sum());fallback=True
    if eligible:
        needed=max(0.,gen_budget-grid_limit)
        goal=min(gen_budget,float(sum(caps[eligible])),max(needed,ready_target,float(sum(minimum_fraction*caps[eligible]))))
        # Keep enough capacity online to meet the observed no-grid request.
        if goal+grid_limit+1e-6<gen_budget:
            targets=np.asarray(original[:3],float).copy()
            diesel=float(original[4]);gen_budget=float(original[:4].sum());fallback=True
        else:
            targets[eligible]=minimum_fraction*caps[eligible]
            remaining=goal-float(sum(targets))
            for _ in range(5):
                active=[i for i in eligible if targets[i]<caps[i]-1e-9]
                if not active or remaining<1e-8:break
                w=weights[active]; addition=np.minimum(caps[active]-targets[active],remaining*w/sum(w))
                targets[active]+=addition;remaining-=float(sum(addition))
    result[:3]=targets;result[3]=max(0.,gen_budget-float(sum(targets)));result[4]=diesel
    if result[3]>grid_limit+1e-5 or abs(float(sum(result[:5]))-total)>1e-5:
        raise ValueError('Source allocation cannot preserve current electrical budget')
    return result,dict(request_is_not_execution=True,allocation_fallback=fallback,
        allocation_fallback_reason='Cannot transfer diesel within current stable GT/grid capacity' if fallback else None,
        untransferable_diesel_kW=max(0.,diesel-requested_diesel),
        diesel_preference_kW=requested_diesel,GT_target_total_kW=float(sum(targets)),
        grid_request_kW=float(result[3]),current_source_budget_kW=total)


class StateDispatchPolicy:
    def __init__(self,config,policy_id):
        if not isinstance(config,dict) or set(config)-set(DEFAULTS):raise ValueError('Unknown closeout config')
        self.config={**DEFAULTS,**copy.deepcopy(config)};self.policy_id=policy_id
        if type(self.config['sustain']) is not bool:raise ValueError('Invalid sustain switch')
        for key,lo,hi in [('support_hours',.25,8.),('downward_fraction',.1,1.),
                          ('minimum_fraction',.4,.8),('electric_buffer_fraction',0.,.5)]:
            x=self.config[key]
            if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or not lo<=x<=hi:
                raise ValueError('Invalid preference '+key)
        self.reset_policy_memory()

    def reset_policy_memory(self):
        self.current_problem=None;self.last_time=None

    def bind_current(self,problem):self.current_problem=problem

    def decide(self,records):
        history=legal_history(records);f=history[-1]['features'];now=float(history[-1]['decision_time_min'])
        if self.last_time is not None and now<=self.last_time:raise ValueError('Nonincreasing decision clock')
        self.last_time=now;problem=self.current_problem;self.current_problem=None
        if problem is None or float(problem.time_minute)!=now:raise ValueError('Current problem clock missing/mismatched')
        query=project_current(problem,{})
        plan=np.asarray(query['projected_targets_kW'],float);dispatch=query['command']['dispatch']
        loads=np.asarray(problem.loads,float);plant=problem.plant
        caps=np.asarray([f[f'capacity_GT_{i}'] for i in (1,2,3)],float)
        health=np.asarray([f[f'availability_HRSG_{i}'] for i in (1,2,3)],float)
        stress=np.asarray([f[f'overload_minutes_GT_{i}'] for i in (1,2,3)],float)
        full_power,full_steam,_=gt_level_properties(plant,float(problem.row.outdoor_temperature_C))
        full=np.full(3,float(full_power[-1]));steam=float(full_steam[-1])*health
        executed=problem.core_fields['executed'];heat_factor=plant.steam_kWh_per_lb*plant.exchanger_eff
        current_gt=np.asarray(problem.state.gt_prev,float)
        arrived_heat=coupled_heat_kW(current_gt,full,steam,plant.steam_kWh_per_lb,plant.exchanger_eff)
        arrived_boiler=float(executed['boiler_steam_lbph'])*heat_factor
        critical_e=float(loads[:2,0].sum());critical_h=float(loads[:2,1].sum())
        ec_equivalent=float(loads[:2,2].sum())/RT_TO_KW*plant.ec_electric_kW_per_rt
        planned_abs_steam=plan[5]/RT_TO_KW*plant.abs_steam_lb_per_rt
        heat_gap=max(0.,critical_h+planned_abs_steam*heat_factor-arrived_boiler)
        eligible_heat=(caps>0)&(health>0)
        available_heat_per_power=float(np.max(np.where(eligible_heat,np.divide(steam,full,out=np.zeros(3),where=full>0),0.))*heat_factor)
        thermal_power_preference=heat_gap/max(available_heat_per_power,1e-9) if np.any((caps>0)&(health>0)) else 0.
        ready=max(critical_e+ec_equivalent+self.config['electric_buffer_fraction']*float(loads[:,0].sum()),thermal_power_preference)
        wait=max(0.,float(problem.arrival_minute)-now)
        remaining=np.maximum(0.,float(problem.config['gt_overload_hold_minutes'])-stress)
        clock_exposed=bool(np.any((stress>0)&(remaining<=wait+float(problem.dt_h)*60)))
        if clock_exposed:ready=max(ready,float(sum(caps)))
        electric_history=[sum(float(h['features'][f'{p}_e_demand_kW']) for p in ('P0','P1','P2','P3')) for h in history]
        diesel=float(plan[4]);sustain_detail={}
        if self.config['sustain']:
            diesel,sustain_detail=diesel_preference(diesel,float(problem.state.diesel_energy_remaining_kWh),electric_history,
                support_hours=self.config['support_hours'],downward_fraction=self.config['downward_fraction'])
        grid_limit=float(f['grid_import_limit_kW'])*float(f['availability_GRID'])
        targets,allocation=allocate_targets(plan,caps,health,stress,grid_limit=grid_limit,diesel_target=diesel,
            ready_target=ready,minimum_fraction=self.config['minimum_fraction'],hold_minutes=float(problem.config['gt_overload_hold_minutes']))
        pending=[]
        for command in problem.core_fields['pending_commands']:
            dep=np.asarray(command['dispatch']['gt_electric_kW'],float)
            invalid=[i+1 for i in range(3) if dep[i]>1e-8 and (caps[i]<=0 or dep[i]<.4*caps[i]-1e-4)]
            pending.append(dict(id=command['id'],generated_minute=command['generated_minute'],
                planned_arrival_minute=command['arrival_minute'],currently_invalid_GT_dependencies=invalid,
                planned_boiler_steam_lbph=command['dispatch']['boiler_steam_lbph']))
        request={'targets':dict(zip(ACTION_NAMES,map(float,targets)))};validate_action(request)
        diagnostic=dict(model_id=self.policy_id,feedback_family='dispatch_closeout',mode='current_coupled_allocation',
            selected_candidate_id=self.policy_id,registered_config=copy.deepcopy(self.config),strategy_fallback=allocation['allocation_fallback'],
            future_rollouts_online=0,request_is_not_execution=True,guard_active=False,
            strategy_status='current_coupled_sustain_request',baseline_request_submitted_unchanged=False,
            same_state_base_plan_targets_kW=plan.tolist(),requested_six_targets_kW=targets.tolist(),
            delta_from_same_state_plan_kW=(targets-plan).tolist(),current_planned_arrival_minute=float(problem.arrival_minute),
            current_known_response_wait_minutes=wait,current_planning_queries=[dict(candidate_id='same_state_base_plan',
                status=query['status'],projected_targets_kW=plan.tolist(),elapsed_seconds=query['elapsed_seconds'],solver_calls=query['solver_calls'])],
            critical_electric_kW=critical_e,critical_heat_kW=critical_h,critical_cooling_ec_equivalent_kW=ec_equivalent,
            arrived_GT_heat_proxy_kW=arrived_heat,arrived_boiler_heat_kW=arrived_boiler,
            current_planned_boiler_steam_lbph=float(dispatch['boiler_steam_lbph']),planned_ABS_steam_lbph=planned_abs_steam,
            thermal_GT_preference_kW=thermal_power_preference,readiness_preference_kW=ready,
            conditional_protection_remaining_minutes=remaining.tolist(),clock_exposed=clock_exposed,
            conditional_clock_is_not_trip_forecast=True,pending_command_dependencies=pending,
            readiness_is_preference_not_joint_capacity_certificate=True,
            cooling_basis='EC-equivalent electrical preference and reference ABS steam; conservative preferences, not simultaneous capacity credit',
            no_arrival_reoptimization_added=True,diesel_sustainability=sustain_detail,allocation=allocation)
        return request,diagnostic


def candidate_specs():
    return [dict(policy_id='execution_reserve',kind='state_dispatch',config={}),
            dict(policy_id='execution_reserve_no_sustain',kind='state_dispatch',config={'sustain':False})]
