"""Same-current-problem projection through the unchanged command generator.

The disposable core receives no exogenous script or time table. It cannot
advance time. Only the eventual six-target request is given to the real env.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass
from types import SimpleNamespace
import time
import numpy as np
from hospital_ies.state_transition import TransitionCore,clean
from hospital_ies.data_contract import validate_action
from tools.integrated_learning.policies import legal_history,six_targets,ACTION_NAMES
from .candidates import current_candidates,choose_current_rule,candidate_numeric_features,CONTEXT_KEYS
from .ranking import preference_key

CORE_CURRENT_FIELDS=('executed','pending_commands','active_command','sequence','failed','pending_shed','local_down',
    'gt_start_pending','auto_shed_latched','automatic_disconnected','disconnected_fraction','automatic_fraction',
    'connection_basis_loads','grid_outage_start','ramp_time','ramp_anchor','ramp_budget')


@dataclass
class CurrentProblem:
    time_minute:float
    hour:int
    row:object
    loads:np.ndarray
    availability:dict
    state:object
    plant:object
    config:dict
    execution_contract:dict
    grid_import_limit_kW:float
    dt_h:float
    steps_done:int
    delayed:bool
    arrival_minute:float
    diesel_available:bool
    core_fields:dict

    def policy_context(self,history,command):
        legal=legal_history(history);f=legal[-1]['features']
        if float(legal[-1]['decision_time_min'])!=self.time_minute:raise ValueError('Current context history clock mismatch')
        executed=self.core_fields['executed']
        actual=np.asarray([*self.state.gt_prev,float(self.state.last_grid_import_kW),float(self.state.last_diesel_kW),float(executed['absorption_cooling_kW'])])
        pending=self.core_fields['pending_commands']
        last=max(pending,key=lambda c:(float(c['generated_minute']),int(c['id']))) if pending else None
        ledger={'available':last is not None,'generated_minute':None,'planned_arrival_minute':None,
            'planned_targets_kW':None,'requested_targets_kW':None,'request_mask':None}
        if last is not None:
            targets=last.get('request',{}).get('targets',{})
            ledger.update(generated_minute=float(last['generated_minute']),planned_arrival_minute=float(last['arrival_minute']),
                planned_targets_kW=six_targets(last['dispatch']).tolist(),
                requested_targets_kW=[float(targets.get(k,0.)) for k in ACTION_NAMES],request_mask=[k in targets for k in ACTION_NAMES])
        return {'time_minute':self.time_minute,'arrival_minute':self.arrival_minute,'delayed':self.delayed,
            'execution_basis_targets_kW':actual.tolist(),'current_plan_targets_kW':six_targets(command['dispatch']).tolist(),
            'capacity_GT_kW':[float(f[f'capacity_GT_{i}']) for i in (1,2,3)],
            'availability_HRSG':[float(self.availability[f'HRSG_{i}']) for i in (1,2,3)],
            'overload_minutes_GT':np.asarray(self.state.overload_minutes,float).tolist(),
            'capacity_ABS_kW':float(f['capacity_ABS']),'grid_import_limit_kW':self.grid_import_limit_kW,
            'loads_kW':self.loads.tolist(),'pending_command_ledger':ledger}


def extract_current_problem(env):
    """Called by simulator boundary adapter, never by the predictor itself."""
    core=env.core;t=float(core.time);h,row,loads,f=core.data_at(t)
    delayed,release=core.delayed_at(t)
    # A future release returned while not delayed is not current information.
    arrival=float(release) if delayed else t
    if not np.isfinite(arrival) or arrival<t:raise ValueError('Invalid current generated command arrival')
    grid=float(core.observe()['grid_import_limit_kW']) if hasattr(core,'observe') else float(env.config['grid_import_limit_kW'])
    defaults={'pending_commands':[],'active_command':None,'sequence':0,'failed':False,'pending_shed':None,'local_down':None,
        'gt_start_pending':{},'auto_shed_latched':False,'automatic_disconnected':np.zeros((4,3)),
        'disconnected_fraction':np.zeros((4,3)),'automatic_fraction':np.zeros((4,3)),
        'connection_basis_loads':np.asarray(loads).copy(),'grid_outage_start':None,'ramp_time':None,'ramp_anchor':None,'ramp_budget':0.}
    current={k:copy.deepcopy(getattr(core,k,defaults.get(k))) for k in CORE_CURRENT_FIELDS}
    return CurrentProblem(t,int(h),copy.deepcopy(row),np.asarray(loads,float).copy(),copy.deepcopy(f),copy.deepcopy(env.state),
        env.plant,copy.deepcopy(env.config),copy.deepcopy(env.execution_contract),grid,float(env.dt_h),int(env.steps_done),
        bool(delayed),arrival,bool(core.diesel_available(t,f)),current)


class CurrentPlanningCore(TransitionCore):
    """Uses the original generate_command/_solve but has no future data API."""
    def __init__(self,problem):
        self.problem=problem
        self.env=SimpleNamespace(state=copy.deepcopy(problem.state),plant=problem.plant,config=copy.deepcopy(problem.config),
            execution_contract=copy.deepcopy(problem.execution_contract),scenario={'grid_limit_kW':problem.grid_import_limit_kW},
            dt_h=problem.dt_h,steps_done=problem.steps_done)
        self.__dict__.update(copy.deepcopy(problem.core_fields));self.time=problem.time_minute
        self.events=[];self.segments=[];self.errors=[];self.solver_calls=[]
    def _current(self,t):
        if float(t)!=self.problem.time_minute:raise ValueError('Current planning core cannot read a future/past time')
    def data_at(self,t):
        self._current(t);p=self.problem
        return p.hour,copy.deepcopy(p.row),p.loads.copy(),copy.deepcopy(p.availability)
    def delayed_at(self,t):
        self._current(t);return self.problem.delayed,self.problem.arrival_minute
    def diesel_available(self,t,f):
        self._current(t);return self.problem.diesel_available
    def advance(self,end):raise ValueError('Current planning query cannot advance physical time')


def project_current(problem,request):
    validate_action(request);core=CurrentPlanningCore(problem);start=time.perf_counter()
    try:
        command=core.generate_command(copy.deepcopy(request))
    except Exception as error:
        error.current_projection_diagnostic=clean({'request':request,'time_minute':problem.time_minute,
            'solver_calls':core.solver_calls,'solver_errors':core.errors,'events':core.events,
            'elapsed_seconds':time.perf_counter()-start,'status':'error'})
        raise
    return {'command':command,'projected_targets_kW':six_targets(command['dispatch']).tolist(),
        'solver_calls':copy.deepcopy(core.solver_calls),'events':copy.deepcopy(core.events),
        'elapsed_seconds':time.perf_counter()-start,'status':'success','actual_time_advanced':0.}


def context_provenance(context):
    t=float(context['time_minute'])
    meanings={'time_minute':('Current event clock','min'),'arrival_minute':('This current command planned arrival, not actual actuation','min'),
        'delayed':('Current already active response window','boolean'),
        'execution_basis_targets_kW':('Terminal sources plus arrived ABS upper bound','kW'),
        'current_plan_targets_kW':('Unchanged priority MILP on pure current problem; planned not executed','kW'),
        'capacity_GT_kW':('Current ideal observed effective GT limits','kW'),
        'availability_HRSG':('Current ideal observed HRSG health','fraction'),
        'overload_minutes_GT':('Accumulated protection RuntimeState','min'),
        'capacity_ABS_kW':('Current ideal observed effective ABS upper','kW'),
        'grid_import_limit_kW':('Current known importer configured limit','kW'),
        'loads_kW':('Current carrier demands at the existing observation boundary','kW')}
    fields={k:{'source':meanings[k][0],'unit':meanings[k][1],'available_time_min':t,'valid_time_min':t,
        'measurement':'current derived ledger/ideal observation research assumption'} for k in CONTEXT_KEYS}
    fields['arrival_minute']['planned_execution_time_min']=float(context['arrival_minute'])
    components=[{'port':'gt_'+str(i+1),'source':'RuntimeState.gt_prev','meaning':'actual terminal electrical source'} for i in range(3)]
    components.extend([{'port':'grid_import','source':'RuntimeState.last_grid_import_kW','meaning':'actual terminal import'},
        {'port':'diesel','source':'RuntimeState.last_diesel_kW','meaning':'actual terminal diesel electrical source'},
        {'port':'absorption_cooling_kW','source':'TransitionCore.executed arrived converter upper','meaning':'arrived cooling ceiling, not actual cooling delivery'}])
    fields['execution_basis_targets_kW']['components']=components
    if 'pending_command_ledger' in context:
        ledger=context['pending_command_ledger']
        fields['pending_command_ledger']={'source':'TransitionCore.pending_commands latest already-generated command by clock/id',
            'unit':'six planned kW targets/known command clocks and request presence masks',
            'available_time_min':float(ledger['generated_minute']) if ledger['available'] else t,'valid_time_min':t,
            'planned_execution_time_min':ledger['planned_arrival_minute'],'actual_execution':False,
            'meaning':'Known generated plan and request ledger; planned ports are not future actual supply'}
    return {'schema':'control_improvement.current_execution_context.v2' if 'pending_command_ledger' in context else 'control_improvement.current_execution_context.v1','fields':fields,
        'projection_tracking_semantics':'Inherited0.20*dt; ABS absolute error is RT, other five ports kW; not uniform capacity-normalized penalty',
        'future_exogenous_event_input':False,'actual_execution_record':'Original subsegment action/service; separate from this planned/current context'}


def prepare_current_candidates(problem,history,*,attempt_callback=None):
    queries=[]
    def run(name,request):
        if attempt_callback:attempt_callback(event='started',attempt_type='isolated_current_projection',candidate_id=name)
        try:
            result=project_current(problem,request)
        except Exception as error:
            failure=copy.deepcopy(getattr(error,'current_projection_diagnostic',{'status':'error','request':request,'message':str(error)}))
            failure['candidate_id']=name;queries.append(failure)
            error.current_projection_attempts=copy.deepcopy(queries)
            if isinstance(getattr(error,'simulation_diagnostic',None),dict):
                error.simulation_diagnostic['current_projection_attempts']=copy.deepcopy(queries)
            if attempt_callback:attempt_callback(event='finished',attempt_type='isolated_current_projection',candidate_id=name,status='error')
            raise
        queries.append(dict(candidate_id=name,**result))
        if attempt_callback:attempt_callback(event='finished',attempt_type='isolated_current_projection',candidate_id=name,status='success')
        return result
    base=run('priority_reference',{})
    context=problem.policy_context(history,base['command']);candidates=current_candidates(history,context)
    for i,c in enumerate(candidates):
        result=base if i==0 else run(c['candidate_id'],c['request'])
        c.update(projected_targets_kW=result['projected_targets_kW'],current_command=copy.deepcopy(result['command']))
    # Descriptive only: equal plan output does NOT merge original requests or
    # imply equivalent fixed-execution tracking and holding after arrival.
    groups=[]
    for i,c in enumerate(candidates):
        key=np.asarray(c['projected_targets_kW'])
        found=next((g for g in groups if np.max(np.abs(key-np.asarray(candidates[g[0]]['projected_targets_kW'])))<=1e-7),None)
        if found is None:groups.append([i])
        else:found.append(i)
    return context,candidates,queries,groups


class ExecutionAwarePolicy:
    """Online finite candidate ranker; no rollout/future search occurs here."""
    def __init__(self,*,model=None,model_id='current_rule_projection0',scale=None):
        self.model=model;self.model_id=model_id;self.policy_id=model_id;self.scale=scale;self.current_problem=None
    def bind_current(self,problem):self.current_problem=problem
    def decide(self,records):
        legal=legal_history(records)
        if self.current_problem is None:raise ValueError('Policy needs a boundary-bound pure current problem')
        problem=self.current_problem;self.current_problem=None
        context,candidates,queries,groups=prepare_current_candidates(problem,legal)
        rule=choose_current_rule(legal,context,candidates);selected=rule;fallback=False;reason=None;prediction=None
        if self.model is not None:
            try:
                x=np.asarray([candidate_numeric_features(legal,context,c) for c in candidates])
                prediction=np.asarray(self.model.predict(x),float)
                if prediction.shape!=(len(candidates),19) or not np.all(np.isfinite(prediction)):raise ValueError('Unavailable candidate delta predictions')
                resolution=self.model.metadata.get('source_binding',{}).get('research_preference_resolution')
                keys=[preference_key(v,self.scale,resolution=resolution) for v in prediction];best=min(keys)
                ties=[i for i,k in enumerate(keys) if k==best]
                selected=rule if rule in ties else ties[0]
            except Exception as error:
                if getattr(error,'category',None) in ('solver_exception','model_constraint_infeasible'):raise
                fallback=True;reason={'error_type':type(error).__name__,'message':str(error)}
        diagnostic={'model_id':self.model_id,'strategy_fallback':fallback,'strategy_status':'current_rule_fallback' if fallback else 'execution_aware_current_candidate',
            'fallback_reason':reason,'raw_prediction':None,'projection_policy':'priority_milp','projection_weight':0.,
            'selected_candidate_id':candidates[selected]['candidate_id'],'selected_candidate_index':selected,'rule_candidate_index':rule,
            'current_context':context,'candidates':candidates,'candidate_delta_predictions':prediction.tolist() if prediction is not None else None,
            'current_context_metadata':context_provenance(context),
            'execution_basis_semantics':'GT/grid/diesel terminal RuntimeState ports; ABS arrived converter ceiling, not actual cooling delivery',
            'current_planning_queries':queries,'equal_dispatch_groups_descriptive_only':groups,'future_rollouts_online':0,
            'research_preference_not_clinical':True}
        return copy.deepcopy(candidates[selected]['request']),diagnostic
