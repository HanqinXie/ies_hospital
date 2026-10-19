"""v4.2 event-driven, non-MPC *research* control environment.

A policy sees only its current observation. The solver projects suggested
energy-station setpoints into feasible dispatch subject to v3.1's physical
and priority constraints; suggestions cannot authorize unsafe raw actuation.
This is a low-order energy-hub surrogate, NOT relay or clinical certification.
"""
from __future__ import annotations
import copy, json, math
from dataclasses import asdict
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from .model import load_plant, solve_hour
from .pipeline import read_data
from .resilience import (GROUPS,CARRIERS,ZONE_PRIORITY,RuntimeState,build_zone_groups,
    thermal_data,empty_faults,loads_for_step,validate_scenario,scenario_faults,
    pre_control_cascade,_solve_milp,update_thermal,p0_failure_classification)
from .state_transition import TransitionCore,aggregate_segments,clean,event_window
from .execution import execution_contract as validate_execution_contract

class HospitalIESEnv:
    """Minimal reset/step interface compatible with RL adaptors.

    Optional action dictionary:
      targets: {gt_1, gt_2, gt_3, grid_import, diesel,
                absorption_cooling_kW} (kW, not relative values)
      Pre-control fast shedding is a fixed scenario/plant capability, not a
      direct operator action before response latency elapses.
    No information from the future or MILP result appears in observation.
    """
    def __init__(self,root:Path|str,config:dict|None=None,thermal_cache=None,execution_contract=None):
        self.root=Path(root)
        self.execution_contract=validate_execution_contract(execution_contract)
        self.hourly=read_data(self.root)
        self.zones=pd.read_csv(self.root/'inputs/loads/critical_zones_8760.csv')
        self.groups=build_zone_groups(self.zones,self.hourly).set_index('hour_of_year')
        self.plant=load_plant(self.root/'configs/plant_configs.json','doe_capacity_matched_exploratory')
        self.config=(json.loads((self.root/'configs/resilience_assumptions.json').read_text())
                     if config is None else copy.deepcopy(config))
        self.thermal_cache=thermal_cache or thermal_data(self.root,self.zones,
                float(self.config['mass_participation']),float(self.config['effective_U_W_m2K']))
        self.scenario=None
        self.state=None
        self.terminated=True
        self.history=[]
        self.zone_history=[]
        self.steps_done=0
        self.cumulative_p0={k:0. for k in CARRIERS}

    def _row(self,step:int):
        hour=self.scenario['start_hour']+int(step*self.dt_h)
        return hour,self.hourly.iloc[hour-1]

    def _observation(self):
        if self.terminated:
            return {'terminal':True,'step':self.steps_done}
        return self.core.observe()

    def reset(self,*,scenario:dict,seed:int|None=None):
        self.scenario=validate_scenario(copy.deepcopy(scenario))
        self.dt_minutes=float(self.scenario.get('step_minutes',15))
        self.dt_h=self.dt_minutes/60.
        first=int(self.scenario['start_hour']);n=int(self.scenario['duration_steps'])
        if first+math.ceil(n*self.dt_h)>8761:raise ValueError('Scenario extends beyond year')
        self.steps_done=0;self.terminated=False;self.history=[];self.zone_history=[]
        self.cumulative_p0={k:0. for k in CARRIERS}
        self.grid_outage_start_step=None
        self.trip_events=[]
        self.core=None;self.event_history=[];self.segment_history=[];self.solver_errors=[]
        try:
            base=solve_hour(next(self.hourly.iloc[[first-1]].itertuples(index=False)),self.plant)
        except Exception as exc:
            error=clean(dict(scenario_id=self.scenario.get('id'),scenario=self.scenario,phase='reference_initialization',time_minute=0.,
                input_row=self.hourly.iloc[first-1].astype(str).to_dict(),config=self.config,execution_contract=self.execution_contract,
                plant=self.plant.as_dict(),category=getattr(exc,'category','solver_exception'),error_type=type(exc).__name__,message=str(exc)))
            self.solver_errors.append(error);self.terminated=True;self.state=None
            exc.simulation_diagnostic=copy.deepcopy(error)
            raise
        prev=np.zeros(3);i=0
        for level in self.plant.levels:
            for _ in range(int(base[f'GT_units_at_{int(level*100)}pct'])):
                if i<3:prev[i]=self.plant.gt_rated_kW*level;i+=1
        self.state=RuntimeState(prev,float(self.config['diesel_initial_energy_kWh']),set(),np.zeros(3),{},
                                float(base.get('grid_import_kW',0.)),0.)
        # Build a complete, feasible unshocked executed state. The old reset
        # reconstructed only GT levels and omitted the held converter commands.
        # A fixed 15-minute initialization budget makes observation-grid
        # refinement start from the same physical state (not a longer warmup).
        for _ in range(max(1,int(self.scenario.get('pre_event_warmup_steps',0)))):
            r=next(self.hourly.iloc[[first-1]].itertuples(index=False))
            loads=loads_for_step(self.groups.loc[first].to_dict(),{})
            try:
                x=_solve_milp(r,self.plant,loads,empty_faults(),self.state,.25,
                    float(self.scenario.get('pre_event_grid_limit_kW',self.scenario.get('grid_limit_kW',self.config['grid_import_limit_kW']))),
                    False,float(self.config['gt_ramp_kW_per_hour']),float(self.config['solver_time_limit_sec']),self.config)
            except Exception as exc:
                error=clean(dict(scenario_id=self.scenario.get('id'),scenario=self.scenario,phase='initialization',time_minute=0.,
                    loads_kW=loads,state=asdict(self.state),availability=empty_faults(),dt_h=.25,
                    row=dict(timestamp=str(r.timestamp),outdoor_temperature_C=float(r.outdoor_temperature_C)),
                    config=self.config,execution_contract=self.execution_contract,plant=self.plant.as_dict(),
                    category=getattr(exc,'category','solver_exception'),error_type=type(exc).__name__,message=str(exc)))
                self.solver_errors.append(error);self.terminated=True;exc.simulation_diagnostic=copy.deepcopy(error)
                raise
            self.state.gt_prev=np.asarray(x['action']['gt_electric_kW'])
            self.state.last_grid_import_kW=float(x['action']['grid_import_kW'])
            self.state.last_diesel_kW=float(x['action']['diesel_kW'])
        initial=loads_for_step(self.groups.loc[first].to_dict(),{})
        self.cascade_config=dict(self.config,pre_event_electric_demand_kW=float(initial[:,0].sum()),
                                pre_event_cooling_demand_kW=float(initial[:,2].sum()))
        if 'precontrol_sheddable_fraction_by_priority' in self.scenario:
            sh=self.scenario['precontrol_sheddable_fraction_by_priority']
            if not isinstance(sh,dict) or any(g not in GROUPS[1:] or not 0<=float(v)<=1 for g,v in sh.items()):
                raise ValueError('Invalid scenario-specific shedding limits')
            self.cascade_config['precontrol_sheddable_fraction_by_priority']=dict(sh)
        self.core=TransitionCore(self,x['action'])
        self.state.disconnected_loads=np.asarray(x['lost']).copy()
        self.event_history=self.core.events;self.segment_history=self.core.segments
        self.solver_errors=self.core.errors
        return self._observation(),{'scenario_id':self.scenario.get('id'),'model_status':'uncalibrated_research_surrogate'}

    def step(self,action:dict|None=None):
        if self.terminated:raise RuntimeError('Call reset() before step()')
        action={} if action is None else action
        self.core.boundary()
        pre=self._observation()
        step=self.steps_done;h=pre['hour_of_year'];f=pre['availability']
        delayed=self.core.delayed_at(self.core.time)[0]
        command=self.core.generate_command(action)
        trips_before=len(self.trip_events)
        segments=self.core.advance((step+1)*self.dt_minutes)
        sol=aggregate_segments(segments,self.dt_h)
        loads=sol['loads'];a=sol['action'];raw_loss=np.asarray(sol['lost'])
        new_events=self.trip_events[trips_before:]
        new_trips=[x['device'] for x in new_events]
        trip_event_time_minute=min((x['trip_time_minute'] for x in new_events),default=None)
        effective_step=(int(round(trip_event_time_minute/self.dt_minutes))
            if trip_event_time_minute is not None and abs(trip_event_time_minute/self.dt_minutes-round(trip_event_time_minute/self.dt_minutes))<1e-9
            else int(trip_event_time_minute/self.dt_minutes) if trip_event_time_minute is not None else None)
        temp={g:max(s['temperature'][g] for s in segments) for g in GROUPS}
        viol={g:any(s['violation'][g] for s in segments) for g in GROUPS}
        classification={key:any(s['classification'][key] for s in segments)
            for key in ('p0_energy_loss','p0_temperature_violation','p0_guarantee_failure')}
        classification.update({f'raw_P0_{k}_unserved_kW':float(raw_loss[0,i]) for i,k in enumerate(CARRIERS)})
        stress={dev:max(s['protection_stress'].get(dev,0.) for s in segments)
                for dev in set().union(*(s['protection_stress'] for s in segments))}
        cascade_record={key:max(s['protection_debug'].get(key,0.) for s in segments)
                for key in set().union(*(s['protection_debug'] for s in segments))}
        executed_now=self.core.active_command
        applied_targets=(executed_now['request'].get('targets',{}) if executed_now else None)
        projection_status='held_command_accounting' if delayed else ('security_proxy_milp' if action.get('security') else 'single_step_priority')
        if sol['optimizer_warning'] or command['optimizer_warning']:projection_status+='_economic_tiebreak_skipped'
        projection_error=''
        params,by_hour=self.thermal_cache
        info={'scenario_id':self.scenario.get('id'),'step':step,'hour_of_year':h,
            'pre_observation':pre,'requested_action':copy.deepcopy(action),'executed_action':a,
            'executed_action_semantics':'interval_average; terminal actuator state in next observation/segments',
            'loads_kW':loads,'served_kW':sol['served'],'unserved_kW':raw_loss,
            'energy_balance_residuals':sol['energy_balance_residuals'],
            'new_trips':new_trips,'trip_event_time_minute':trip_event_time_minute,
            'trip_effective_from_step':effective_step,
            'cascade_debug':cascade_record,'precontrol_GT_stress_ratio':stress,
            'max_temperature_by_priority_C':temp,'temperature_violation_by_priority':viol,
            **classification,'cost_USD_step':sol['cost_USD_step'],
            'tracking_penalty_USD_step':sol['tracking_penalty_USD_step'],
            'security_objective_penalty_USD_step':sol['security_objective_penalty_USD_step'],
            'projection_status':projection_status,'projection_error':projection_error,
            'optimizer_warning':';'.join(x for x in (sol['optimizer_warning'],command['optimizer_warning']) if x),
            'planned_action':copy.deepcopy(action),'control_delayed':delayed,'applied_targets':applied_targets,
            'segments':copy.deepcopy(segments),'command_id':command['id']}
        # Reward: normalized priorities, thermal violations, secondary trips.
        norm=np.maximum(1.,loads.sum(axis=0));energy_loss=np.sum(raw_loss/norm[None,:],axis=1)
        reward=-float(1000.*int(classification['p0_guarantee_failure'])+200.*energy_loss[0]+50.*energy_loss[1]+10.*energy_loss[2]+energy_loss[3]+10.*len(new_trips))
        record={'scenario_id':self.scenario.get('id'),'stage':self.scenario['stage'],'step':step,'hour_of_year':h,
            'p0_failed':int(classification['p0_guarantee_failure']),'p0_temperature_violation':int(viol['P0']),
            'new_trips':';'.join(new_trips),'trip_event_time_minute':trip_event_time_minute if new_trips else '',
            'trip_effective_from_step':effective_step if new_trips else '',
            'control_delayed':int(delayed),'projection_error':projection_error,
            'optimizer_warning':sol.get('optimizer_warning',''),
            'cost_USD_step':sol['cost_USD_step'],
            'tracking_penalty_USD_step':sol.get('tracking_penalty_USD_step',0.),
            'security_objective_penalty_USD_step':sol.get('security_objective_penalty_USD_step',0.),
            'reward':reward,'projection_status':projection_status,
            'pre_diesel_energy_kWh':pre['diesel_energy_kWh'],
            'pre_GT_1_kW':pre['gt_prev_kW'][0],'pre_GT_2_kW':pre['gt_prev_kW'][1],'pre_GT_3_kW':pre['gt_prev_kW'][2],
            'pre_P0_thermal_delta_max_C':max((v for z,v in pre['zone_thermal_delta_C'].items() if ZONE_PRIORITY[z]==0),default=0.),
            'temp_P0_max_C':temp['P0'],'grid_availability':f['GRID']}
        for g_idx,g in enumerate(GROUPS):
            for k_idx,k in enumerate(CARRIERS):
                record[f'{g}_{k}_demand_kW']=float(loads[g_idx,k_idx]);record[f'{g}_{k}_unserved_kW']=float(raw_loss[g_idx,k_idx])
        for dev,ratio in stress.items():
            record[f'precontrol_{dev}_estimated_overload_ratio']=float(ratio)
        proposed=action.get('targets',{})
        actual_targets={'gt_1':a['gt_electric_kW'][0],'gt_2':a['gt_electric_kW'][1],
                        'gt_3':a['gt_electric_kW'][2],
                        'grid_import':a['grid_import_kW'],'diesel':a['diesel_kW'],
                        'absorption_cooling_kW':a['absorption_cooling_kW']}
        for key,actual in actual_targets.items():
            record[f'executed_target_{key}_kW']=float(actual)
            record[f'proposed_target_{key}_kW']=float(proposed[key]) if key in proposed else np.nan
            record[f'proposal_execution_delta_{key}_kW']=(float(actual)-float(proposed[key])) if key in proposed else np.nan
        for key,v in a.items():
            if isinstance(v,list):
                for j,x in enumerate(v):record[f'{key}_{j+1}']=x
            else:record[key]=v
        record.update({'minute':step*self.dt_minutes,'end_minute':(step+1)*self.dt_minutes,
            'active_faults':';'.join(k for k,v in f.items() if v<1-1e-8),
            'initial_faults':';'.join(self.scenario.get('faults',{})),
            'secondary_faults':';'.join(sorted(self.state.extra_faulted)),
            'p0_energy_loss':int(classification['p0_energy_loss']),
            'p0_guarantee_failure':int(classification['p0_guarantee_failure']),
            'diesel_energy_remaining_kWh':self.state.diesel_energy_remaining_kWh,
            'balance_electric_residual_kW':sol['energy_balance_residuals'][0],
            'balance_cooling_residual_kW':sol['energy_balance_residuals'][1],
            'balance_steam_equivalent_residual_kW':sol['energy_balance_residuals'][2],
            'balance_steam_residual_lbph':sol['steam_balance_residual_lbph'],
            'segment_count':len(segments),'command_id':command['id']})
        record.update(cascade_record)
        material=any(np.any(np.asarray(s['unserved_kW'])>float(self.config['p0_solver_feasibility_tol_kW'])) for s in segments)
        p1=any(np.any(np.asarray(s['unserved_kW'])[1]>float(self.config['p0_solver_feasibility_tol_kW'])) or s['violation']['P1'] for s in segments)
        record['status']=('S3_P0_failure' if classification['p0_guarantee_failure'] else
                          'S2_P1_loss' if p1 else 'S1_managed_shedding' if material else 'S0_all_served')
        record['timestamp']=str(self.hourly.iloc[h-1].timestamp)
        record['grid_limit_kW']=pre['grid_import_limit_kW']
        shock_start,shock_end=event_window(self.scenario,'shock')
        active_fraction=max(0.,min((step+1)*self.dt_minutes,shock_end)-max(step*self.dt_minutes,shock_start))/self.dt_minutes
        record.update({f'demand_shock_{k}':active_fraction*float(self.scenario.get('shock',{}).get(k,0.)) for k in CARRIERS})
        record.update({f'demand_shock_{g}_{k}':active_fraction*float(self.scenario.get('shock_by_priority',{}).get(g,{}).get(k,0.)) for g in GROUPS for k in CARRIERS})
        for gi,g in enumerate(GROUPS):
            for ki,k in enumerate(CARRIERS):record[f'{g}_{k}_served_kW']=float(sol['served'][gi,ki])
        self.history.append(record)
        for name,d in self.state.thermal_delta.items():
            zone_hour=segments[-1]['hour_of_year'];z=by_hour[zone_hour][name]
            self.zone_history.append({'scenario_id':self.scenario.get('id','unnamed'),'step':step,'zone_name':name,'hour_of_year':zone_hour,
                'priority':ZONE_PRIORITY[name],'delta_temp_C':d,'baseline_zone_temp_C':float(z.zone_mean_air_temperature_C),'temperature_C':float(z.zone_mean_air_temperature_C)+d,
                'limit_C':float(z.thermostat_cooling_setpoint_C)+float(self.config['thermal_setpoint_margin_C'])})
        self.steps_done+=1
        self.terminated=self.steps_done>=int(self.scenario['duration_steps'])
        return self._observation(),reward,self.terminated,False,info

    def traces(self):
        return pd.DataFrame(self.history),pd.DataFrame(self.zone_history)
