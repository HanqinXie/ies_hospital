"""M21 environment: shared executed services, ordinary commands, finite stores."""
from __future__ import annotations
import copy
import math
from types import SimpleNamespace
import numpy as np

from hospital_ies.control_env import HospitalIESEnv as NativeEnvironment
from hospital_ies.state_transition import clean,event_window,EPS
from hospital_ies.resilience import ZONE_PRIORITY
from tools.engineering_model.environment import isolated
from tools.load_accounting import environment as base
from tools.load_accounting.execution import LoadAccountingCore
from tools.operating_response.physical_balance import solve_physical
from tools.operating_response import environment as operating
from . import MODEL_ID,CONTRACT_ID
from .auxiliary import compute_auxiliary_allocation,apply_auxiliary_allocation
from .service import dependency_requirements,fan_independent_heat_bound
from .storage import FiniteStore

DEFAULT_ENGINEERING=dict(configuration_id='H0',ups={},cold_store={},cold_pump_kW_per_kW=.02,
    storage_max_segment_minutes=1.,auxiliary_share_scale=1.,diesel_start_delay_minutes=15.,
    gt_protection_trip_multiple=1.05,gt_overload_hold_minutes=15.)

def model_contract(model_id,model_config):
    if model_id!=MODEL_ID:raise ValueError('Explicit M21 model identity required')
    cfg=copy.deepcopy(model_config or {});new=cfg.pop('sci_engineering',{})
    if not isinstance(new,dict) or set(new)-set(DEFAULT_ENGINEERING):raise ValueError('Unknown M21 engineering configuration')
    engineering=copy.deepcopy(DEFAULT_ENGINEERING);engineering.update(new)
    for name in ('ups','cold_store'):FiniteStore(engineering[name])
    for name in ('cold_pump_kW_per_kW','storage_max_segment_minutes','auxiliary_share_scale','diesel_start_delay_minutes',
                 'gt_protection_trip_multiple','gt_overload_hold_minutes'):
        v=engineering[name]
        if isinstance(v,bool) or not math.isfinite(float(v)) or float(v)<0.:raise ValueError('Invalid M21 engineering scalar')
    if engineering['storage_max_segment_minutes']<=0.:raise ValueError('Positive storage subinterval required')
    if min(engineering['gt_protection_trip_multiple'],engineering['gt_overload_hold_minutes'])<=0.:
        raise ValueError('Positive common protection threshold and duration required')
    contract,resolved=base.model_contract('M20_load_accounting',cfg)
    resolved['sci_engineering']=engineering
    contract.update(name=contract['name']+'_sci_services_v21',sci_contract_id=CONTRACT_ID)
    return contract,resolved

def validate_scenario(scene):
    auxiliary=scene.get('auxiliary_faults',{})
    allowed={'AUX_FAN','AUX_FAN_OR','AUX_FAN_ER','AUX_FAN_ICU','AUX_CHW_PUMP','AUX_HEAT_PUMP'}
    if not isinstance(auxiliary,dict) or set(auxiliary)-allowed:
        raise ValueError('Unknown registered auxiliary device')
    if any(isinstance(v,bool) or not math.isfinite(float(v)) or not 0.<=float(v)<=1. for v in auxiliary.values()):
        raise ValueError('Invalid auxiliary availability')
    candidate=copy.deepcopy(scene)
    clinical=scene.get('shock_by_zone',{})
    if not isinstance(clinical,dict) or set(clinical)-set(ZONE_PRIORITY):raise ValueError('Unknown clinical zone')
    for values in clinical.values():
        if not isinstance(values,dict) or set(values)-{'e','h','c'}:raise ValueError('Unknown clinical carrier')
        if any(isinstance(v,bool) or not math.isfinite(float(v)) or not 0.<=float(v)<=4. for v in values.values()):
            raise ValueError('Invalid clinical demand increment')
    placeholder_shock=bool(clinical and not candidate.get('shock') and not candidate.get('shock_by_priority'))
    if placeholder_shock:candidate['shock']={'e':0.}
    # Native L4b requires a physical fault. Validate all its other fields
    # using one temporary ordinary fault, then restore the genuine aux-only
    # initiating device. The placeholder is never executed or persisted.
    placeholder=bool(auxiliary and not candidate.get('faults') and candidate.get('event_class')=='L4b')
    if placeholder:candidate['faults']={'GRID':1.}
    checked=base.validate_scenario(candidate)
    if placeholder:checked['faults']={}
    if placeholder_shock:checked['shock']={}
    return checked

class EngineeringCore(LoadAccountingCore):
    def __init__(self,env,initial_action):
        self.ups=FiniteStore(env.sci_config['ups']);self.cold_store=FiniteStore(env.sci_config['cold_store'])
        super().__init__(env,initial_action)
        if getattr(env,'_solver_row_sidecar',None) is not None:
            from .solver_log import SolverCallLog
            self.solver_calls=SolverCallLog(self.solver_calls,writer=env._solver_row_sidecar)
        self.log('m21_storage_initial_inventory',initial_charged_input_kWh={
            'ups':self.ups.initial_charged_input_kWh,'cold_store':self.cold_store.initial_charged_input_kWh},
            initial_inventory_is_paid_preparation=True,free_reset_on_repair=False)

    def _hardware_data_at(self,minute):
        h,row,loads,f=super()._hardware_data_at(minute)
        fixed_direct=getattr(row,'sci_demand_boundary_contract',None) is not None
        if not hasattr(row,'Q_h_DHW_water_use_kW'):
            raise ValueError('Actual M21 source row lacks Q_h_DHW_water_use_kW decomposition')
        original_P3_heat=float(getattr(row,'P3_reference_h_kW',loads[3,1]))
        clinical=np.zeros((4,3));auxiliary_increment=np.zeros(4)
        shock_start,shock_end=event_window(self.env.scenario,'shock')
        if shock_start<=minute<shock_end and self.env.scenario.get('shock_by_zone'):
            for zone,values in self.env.scenario['shock_by_zone'].items():
                observed=self.env.zones[(self.env.zones.hour_of_year==h)&(self.env.zones.zone_name.str.upper()==zone)]
                if len(observed)!=1:raise ValueError('Clinical template lacks unique current source zone')
                source=observed.iloc[0];g=ZONE_PRIORITY[zone]
                baseline=(float(source.interior_equipment_kW+source.interior_lighting_kW),
                    float(source.associated_reheat_coil_heating_kW),
                    float(source.zone_sensible_cooling_kW*source.zone_multiplier))
                for k,carrier in enumerate(('e','h','c')):clinical[g,k]+=baseline[k]*float(values.get(carrier,0.))
            loads=loads+clinical
            if not fixed_direct:
                requests=dependency_requirements(row,loads)
                data=row._asdict()
                for g in range(4):
                    old=np.array([float(getattr(row,f'P{g}_{kind}_kW')) for kind in ('fan','chw_pump','heat_pump')])
                    auxiliary_increment[g]=float(np.maximum(0.,requests[g]-old).sum())
                    loads[g,0]+=auxiliary_increment[g]
                    for j,kind in enumerate(('fan','chw_pump','heat_pump')):data[f'P{g}_{kind}_kW']=float(requests[g,j])
                    for j,carrier in enumerate(('e','h','c')):data[f'P{g}_reference_{carrier}_kW']=float(loads[g,j])
                row=SimpleNamespace(**data)
        if fixed_direct:
            from tools.model_review_v28.demand import bind_request
            row,loads=bind_request(row,loads)
            q=row.sci_direct_aux_request
            auxiliary_increment=(np.asarray(q['auxiliary_request_kW'])-np.asarray(q['auxiliary_reference_kW'])).sum(axis=1)
            # Source component fields remain immutable. Current components are
            # group-specific: applying each group's common fan factor preserves
            # its original physical fault-path shares under changed requests.
            components={}
            for name,item in self.env.auxiliary_metadata.get('fan_component_columns',{}).items():
                components[name]=[float(getattr(row,item['power_column']))*float(item['priority_shares'][f'P{g}'])*q['auxiliary_factors'][g][0] for g in range(4)]
            q['current_fan_components_by_priority_kW']=components
        if hasattr(row,'_asdict'):row=SimpleNamespace(**row._asdict())
        row.sci_clinical_increment_kW=clinical.tolist();row.sci_auxiliary_increment_kW=auxiliary_increment.tolist()
        row.sci_heat_decomposition_required=True
        row.sci_heat_decomposition_source=dict(path='inputs/loads/hospital_ies_demand_8760.csv',
            sha256=self.env.auxiliary_metadata['root_input_sha256']['inputs/loads/hospital_ies_demand_8760.csv'],
            hour_of_year=int(h),DHW_column='Q_h_DHW_water_use_kW',
            source_DHW_kW=float(row.Q_h_DHW_water_use_kW),original_P3_h_kW=original_P3_heat)
        fan_independent_heat_bound(row,loads) # reject invalid actual source trace before any solve
        f=dict(f);start,end=event_window(self.env.scenario,'fault')
        faults=self.env.scenario.get('auxiliary_faults',{}) if start<=minute<end else {}
        for g in range(4):
            remaining=float(faults.get('AUX_FAN',1.))
            total=float(getattr(row,f'P{g}_fan_kW'))
            for short,loop in (('OR','VAV_OR'),('ER','VAV_ER'),('ICU','VAV_ICU')):
                if f'AUX_FAN_{short}' not in faults:continue
                item=self.env.auxiliary_metadata['fan_component_columns'][loop]
                contribution=float(getattr(row,item['power_column']))*float(item['priority_shares'][f'P{g}'])
                if total>EPS:remaining-=(1.-float(faults[f'AUX_FAN_{short}']))*contribution/total
            f[f'AUX_FAN_P{g}']=max(0.,remaining)
        for name in ('AUX_CHW_PUMP','AUX_HEAT_PUMP'):f[name]=float(faults.get(name,1.))
        return h,row,loads,f

    def observe(self):
        value=super().observe()
        _,row,loads,f=self.data_at(self.time)
        auxiliary={k:v for k,v in f.items() if k.startswith('AUX_')}
        value['availability']={k:v for k,v in value['availability'].items() if not k.startswith('AUX_')}
        value['sci_auxiliary']=dict(observation_time_minute=self.time,available_time_minute=self.time,
            availability=auxiliary,required_kW=dependency_requirements(row,loads).tolist(),
            fan_independent_heat=fan_independent_heat_bound(row,loads))
        if hasattr(row,'sci_direct_aux_request'):
            value['sci_auxiliary']['demand_components']=copy.deepcopy(row.sci_direct_aux_request)
        value['sci_storage']=dict(observation_time_minute=self.time,available_time_minute=self.time,
            ups=self.ups.observe(),cold_store=self.cold_store.observe())
        value['sci_model_id']=MODEL_ID
        value['diesel_local_response']['model_id']=MODEL_ID
        for key in ('execution_recovery','model_closeout'):value[key]['contract_id']=CONTRACT_ID
        return value

    def _storage_ports(self,dt_h,f):
        # A local registered recharge rule: healthy connected utility may
        # charge only after P0-P3 service; outage/OPEN permits discharge.
        charge=bool(self.grid_bus_state=='GRID' and f['GRID']>=1.-EPS)
        def port(store,charging):
            c=store.config
            return dict(discharge_kW=c['power_kW'] if not charging and store.energy_kWh>1e-9 else 0.,
                charge_kW=c['power_kW'] if charging and store.energy_kWh<c['capacity_kWh']-1e-9 else 0.)
        ups=port(self.ups,charge)
        # During loss of the shared rejection path, cold storage discharges
        # even with the utility available. It still needs actual pump power.
        cold_charge=charge and f.get('TOWER',1.)>EPS
        cold=port(self.cold_store,cold_charge)
        return dict(ups_discharge_kW=ups['discharge_kW'],ups_charge_kW=ups['charge_kW'],
            cold_discharge_kW=cold['discharge_kW'],cold_charge_kW=cold['charge_kW'],
            cold_pump_kW_per_kW=self.env.sci_config['cold_pump_kW_per_kW'])

    def _physical_solver_config(self,dt_h,f):
        # A feasibility proof and actual accounting see the same currently
        # registered ports. Neither a proof nor this helper commits inventory.
        return dict(self.env.config,_sci_storage_ports=self._storage_ports(dt_h,f))

    def _electric_sink_upper(self,loads,f,row):
        """Bound real M21 consumption for the existing common no-export path.

        Failed auxiliary service remains in the original request/ENS, but its
        unexecuted electricity cannot absorb fixed GT production. All bounds
        are optimistic and diagnostic; the unchanged physical solve still
        determines delivery. Registered charging paths retain their ceilings.
        """
        from hospital_ies.execution import maximum_auxiliary_sink
        required=dependency_requirements(row,loads)
        fractions=np.asarray([[f.get(f'AUX_FAN_P{g}',1.),f.get('AUX_CHW_PUMP',1.),
            f.get('AUX_HEAT_PUMP',1.)] for g in range(4)],float)
        if not np.isfinite(fractions).all() or np.any((fractions<0.)|(fractions>1.)):
            raise ValueError('Invalid auxiliary availability for no-export bound')
        if getattr(row,'sci_demand_boundary_contract',None) is not None:
            from tools.model_review_v28.demand import dependency_request
            direct=np.asarray(dependency_request(row,loads)['direct_cap_kW'],float)
        else:direct=np.maximum(0.,loads[:,0]-required.sum(axis=1))
        electric=np.minimum(loads[:,0],direct+(required*fractions).sum(axis=1))
        cold=np.asarray(loads[:,2],float).copy()
        for g in range(4):
            cold[g]*=min(fractions[g,j] if required[g,j]>1e-9 else 1. for j in (0,1))
        ports=self._storage_ports(self.env.dt_h,f)
        ups_charge=ports['ups_charge_kW'];cold_charge=ports['cold_charge_kW']
        # Useful cold discharge is limited to actually connectable P0/P1
        # cold, as in the unchanged storage eligibility/dependency rows.
        cold_discharge=min(ports['cold_discharge_kW'],float(cold[:2].sum()))
        bound_loads=np.asarray(loads,float).copy();bound_loads[:,2]=cold
        # Stored cold charging is an additional real converter load, not a
        # clinical room requiring its air fan. Its own pump electricity is
        # included separately; this is an upper bound, never fake delivery.
        bound_loads[3,2]+=cold_charge
        converter=maximum_auxiliary_sink(self.env.plant,row,bound_loads,f,self.executed)
        pump=ports['cold_pump_kW_per_kW']*(cold_charge+cold_discharge)
        return dict(upper_kW=float(electric.sum())+converter+ups_charge+pump,
            maximum_converter_electric_kW=converter,
            connected_request_electric_kW=float(loads[:,0].sum()),
            maximum_building_electric_service_by_priority_kW=electric.tolist(),
            maximum_building_cooling_service_by_priority_kW=cold.tolist(),
            maximum_ups_charge_kW=ups_charge,maximum_cold_charge_kW=cold_charge,
            maximum_eligible_cold_discharge_kW=cold_discharge,
            maximum_storage_circulation_pump_kW=pump,
            semantics='Optimistic current hardware/held-converter/registered charge sink; original request unchanged, not actual consumption')

    def _solve(self,loads,f,dt_h,phase,action,**kwargs):
        import time
        began=time.perf_counter();e=self.env;_,row,_,_=self.data_at(self.time)
        cfg=self._physical_solver_config(dt_h,f)
        try:
            result=solve_physical(row,e.plant,loads,f,self.state,dt_h,
                float(e.scenario.get('grid_limit_kW',e.config['grid_import_limit_kW'])),
                self.diesel_available(self.time,f),float(e.config['gt_ramp_kW_per_hour']),
                float(e.config['solver_time_limit_sec']),cfg,execution_contract=e.operating_execution_contract,**kwargs)
            if phase!='local_down_endpoint':
                self._storage_candidate=dict(time_minute=self.time,action=copy.deepcopy(result['action']))
            self.solver_calls.append(dict(time_minute=self.time,phase=phase,dt_h=dt_h,status='success',
                elapsed_seconds=time.perf_counter()-began,solver_backend_layers=result['solver_backend_layers']))
            return result
        except Exception as exc:self._record_error(exc,loads,f,dt_h,phase,action,began)

    def _next_boundary(self,end):
        candidate=super()._next_boundary(end)
        if max(self.ups.config['capacity_kWh'],self.cold_store.config['capacity_kWh'])>0.:
            candidate=min(candidate,self.time+self.env.sci_config['storage_max_segment_minutes'])
        provisional=getattr(self,'_storage_candidate',None)
        if provisional is not None and abs(provisional['time_minute']-self.time)<=EPS:
            action=provisional['action']
            for prefix,store in (('ups',self.ups),('cold',self.cold_store)):
                span=store.boundary_hours(discharge_kW=float(action.get(prefix+'_discharge_kW',0.)),
                    charge_kW=float(action.get(prefix+'_charge_kW',0.)),max_hours=(candidate-self.time)/60.)
                if span>1e-12:candidate=min(candidate,self.time+span*60.)
        return candidate

    def advance(self,end):
        first=len(self.segments)
        try:return super().advance(end)
        finally:
            # The inherited M20 outer finally owns its old identity. M21 is
            # the final wrapper around that unchanged accounting foundation.
            for segment in self.segments[first:]:
                segment['execution_model_id']=MODEL_ID
                segment['sci_engineering_contract_id']=CONTRACT_ID

    def _after_actual_segment(self,segment,result):
        dt=(segment['end_minute']-segment['start_minute'])/60.;a=segment['action']
        segment['m21_storage']={name:store.commit(discharge_kW=float(a.get(prefix+'_discharge_kW',0.)),
            charge_kW=float(a.get(prefix+'_charge_kW',0.)),dt_h=dt)
            for name,prefix,store in (('ups','ups',self.ups),('cold_store','cold',self.cold_store))}
        segment['service_dependencies']=copy.deepcopy(result['service_dependencies'])
        _,row,_,_=self._hardware_data_at(segment['start_minute'])
        segment['m21_demand_components']=dict(clinical_increment_kW=row.sci_clinical_increment_kW,
            auxiliary_increment_e_kW=row.sci_auxiliary_increment_kW,
            unmodified_original_request_retained=True)
        if hasattr(row,'sci_direct_aux_request'):
            segment['m28_demand_components']=copy.deepcopy(result['service_dependencies']['demand_components'])
        segment['execution_model_id']=MODEL_ID

    def generate_command(self,action):
        import time
        from . import planner,policy
        request=policy.validate_action(action)
        if self.failed:raise RuntimeError('Prior physical failure retained')
        _,row,loads,f=self.data_at(self.time);began=time.perf_counter();context=self.readiness_context()
        if 'ordinary_dispatch' in request:
            raw=request['ordinary_dispatch'];contacts=np.asarray(raw['disconnected_kW'],float)
            if np.any(contacts>loads+1e-7):raise ValueError('Ordinary cuts exceed original request')
            dispatch=copy.deepcopy(raw['dispatch'])
            diagnostic=dict(raw.get('rule_diagnostic',{}),upper_optimizer_called=False)
            planned=dispatch;warning='';layers=[]
        else:
            joint=request['state_coordinated_joint']
            context['diesel_local_response']=copy.deepcopy(self.observe()['diesel_local_response'])
            context['diesel_start_minute']=self.state.diesel_start_minute
            try:
                result=planner.solve_joint(row,self.env.plant,loads,f,self.state,self.env.dt_h,
                    float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),
                    self.diesel_available(self.time,f),float(self.env.config['gt_ramp_kW_per_hour']),
                    float(self.env.config['solver_time_limit_sec']),self.env.config,
                    execution_contract=self.env.execution_contract,joint_options=joint['options'],
                    risk_loads=joint.get('risk_loads_kW'),coordination_options=joint['coordination'],
                    readiness_context=context,execution_limits=self.execution_limits(),
                    grid_bridge_request=self.bridge_wait_started_minute is not None and self.grid_bus_state=='ISLAND')
            except Exception as exc:self._record_error(exc,loads,f,self.env.dt_h,'sci_upper_planning',request,began)
            dispatch=copy.deepcopy(result['action']);contacts=np.asarray(result.get('command_disconnected',result['lost']))
            diagnostic=copy.deepcopy(result['joint_diagnostic']);planned=result['planned_current_action']
            warning=result.get('optimizer_warning','');layers=copy.deepcopy(result.get('solver_backend_layers',[]))
        if hasattr(self,'_prepare_command_dispatch'):
            dispatch,diagnostic=self._prepare_command_dispatch(dispatch,diagnostic,loads)
        self.solver_calls.append(dict(time_minute=self.time,phase='sci_upper_planning',status='success',
            elapsed_seconds=time.perf_counter()-began,upper_optimizer_called='ordinary_dispatch' not in request,
            optimizer_warning=warning,solver_backend_layers=layers))
        delayed,release=self.delayed_at(self.time);self.sequence+=1
        command=dict(id=self.sequence,generated_minute=self.time,arrival_minute=release if delayed else self.time,
            request=request,dispatch=dispatch,disconnected=contacts.tolist(),reference_cooling_kW=float(loads[:,2].sum()),
            weight=0.,optimizer_warning=warning,execution_model_id=MODEL_ID,
            joint_dispatch=clean(dict(planning_interface=policy.PLANNING_INTERFACE,planned_current_action=planned,
                diagnostic=diagnostic,readiness_context=context,planning_is_not_execution=True)))
        self.pending_commands.append(command);self.log('command_generated',command=command)
        return command

class OperatingHospitalIESEnv(base.OperatingHospitalIESEnv):
    def __init__(self,root,*,allowed_hours,sealed_days,model_id=MODEL_ID,model_config=None,
                 sci_final_release_receipt=None,solver_row_sidecar=None):
        self.sci_final_release_receipt=copy.deepcopy(sci_final_release_receipt)
        self._solver_row_sidecar=solver_row_sidecar
        contract,resolved=model_contract(model_id,model_config or {})
        self.sci_config=copy.deepcopy(resolved.pop('sci_engineering'))
        super().__init__(root,allowed_hours=allowed_hours,sealed_days=sealed_days,
                         model_id='M20_load_accounting',model_config=resolved)
        self.model_id=MODEL_ID
        self.operating_model_config=dict(resolved,sci_engineering=copy.deepcopy(self.sci_config))
        self.operating_execution_contract=contract
        self.execution_contract={k:v for k,v in contract.items() if k not in ('diesel_dispatch_mode','load_accounting_contract_id','sci_contract_id')}
        self.config['sci_service_dependencies']=True
        self.config['diesel_start_delay_minutes']=float(self.sci_config['diesel_start_delay_minutes'])
        for key in ('gt_protection_trip_multiple','gt_overload_hold_minutes'):
            self.config[key]=float(self.sci_config[key])
        self.default_diesel_initial_energy_kWh=float(self.config['diesel_initial_energy_kWh'])
        auxiliary=compute_auxiliary_allocation(self.root,critical_share_scale=self.sci_config['auxiliary_share_scale'])
        self.auxiliary_metadata=copy.deepcopy(auxiliary.attrs['metadata'])
        selected=auxiliary[auxiliary.hour_of_year.isin(self.allowed_hours)].copy()
        groups=self.groups.loc[sorted(self.allowed_hours)].reset_index()
        reassigned=apply_auxiliary_allocation(groups,selected).set_index('hour_of_year')
        self.groups.loc[reassigned.index,reassigned.columns]=reassigned
        for name in selected.columns:
            if name!='hour_of_year':self.hourly.loc[selected.hour_of_year.to_numpy()-1,name]=selected[name].to_numpy()
        for g in range(4):
            for carrier in ('e','h','c'):
                self.hourly.loc[reassigned.index.to_numpy()-1,f'P{g}_reference_{carrier}_kW']=reassigned[f'P{g}_{carrier}_kW'].to_numpy()
        self.input_access_receipt['sci_auxiliary_source_sha256']=self.auxiliary_metadata['allocation_sha256']

    def reset(self,*,scenario,seed=None):
        from .design import allowed_hours
        required=allowed_hours(dict(scenarios=[scenario]))
        if not required<=self.allowed_hours:raise ValueError('Scenario outside registered complete support')
        receipt=getattr(self,'sci_final_release_receipt',None)
        if receipt is not None:
            from .release import validate_scene_release
            self.sci_final_reset_receipt=validate_scene_release(self.root,scenario,sorted(self.allowed_hours),
                sorted(self.sealed_days),receipt)
        elif any((h-1)//24 in self.sealed_days for h in required):raise ValueError('Sealed final background')
        initial=float(scenario.get('initial_reserve_kWh',self.default_diesel_initial_energy_kWh))
        if not math.isfinite(initial) or initial<0.:raise ValueError('Invalid initial shaft-equivalent reserve')
        self.config['diesel_initial_energy_kWh']=initial
        self.planning_queries=[]
        return isolated(NativeEnvironment.reset,dict(NativeEnvironment.reset.__globals__,TransitionCore=EngineeringCore,
            validate_scenario=validate_scenario,_solve_milp=solve_physical))(self,scenario=scenario,seed=seed)

def decision_snapshot(env):
    value=base.decision_snapshot(env);observed=env.core.observe()
    value['engineering_execution_model_id']=MODEL_ID
    for key in ('sci_auxiliary','sci_storage'):value[key]=copy.deepcopy(observed[key])
    return value

class PolicyEnvironment(operating.OperatingPolicyEnvironment):
    step=isolated(operating.native_episode.PolicyEnvironment.step,
        dict(operating.native_episode.PolicyEnvironment.step.__globals__,decision_snapshot=decision_snapshot,
             validate_action=lambda value:__import__('tools.sci_engineering.policy',fromlist=['validate_action']).validate_action(value)))

def run_episode(env,scenario,controller,seed,manifest):
    execute=isolated(operating.run_episode,dict(operating.run_episode.__globals__,
        decision_snapshot=decision_snapshot,OperatingPolicyEnvironment=PolicyEnvironment))
    payload=execute(env,scenario,controller,seed,manifest)
    payload.update(execution_model_id=MODEL_ID,study_contract_id=CONTRACT_ID,engineering_model_contract_id=CONTRACT_ID,
        m21_initial_storage={k:getattr(env.core,k).initial_charged_input_kWh for k in ('ups','cold_store')} if env.core else None)
    from .contracts import observed_engineering_events
    payload['events']=sorted(payload.get('events',[])+observed_engineering_events(
        payload.get('scenario',scenario),payload.get('summary',{}).get('observed_until_minute',0.),
        has_segments=bool(payload.get('segments'))),key=lambda e:(e['time_minute'],str(e.get('event_id',''))))
    return payload
