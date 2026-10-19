"""Execution-aware policy interfaces; synthetic cases are not hospital validation."""
import copy
from types import SimpleNamespace
import numpy as np
import pytest


def legal_records(t=60.):
    f={'time_minute':t,'control_available':True,'P0_e_demand_kW':50.}
    return [{'decision_time_min':t,'features':f,'feature_metadata':{k:{'available_time_min':t,'valid_time_min':t} for k in f},
             'scenario':{'future_fault':999},'labels':{'120':1}}]


def context():
    return {'time_minute':60.,'arrival_minute':60.,'delayed':False,
        'execution_basis_targets_kW':[100.,160.,0.,120.,0.,200.],
        'current_plan_targets_kW':[180.,80.,0.,120.,0.,200.],
        'capacity_GT_kW':[320.,320.,0.], 'availability_HRSG':[1.,.3,1.],
        'overload_minutes_GT':[0.,10.,0.], 'capacity_ABS_kW':600.,
        'grid_import_limit_kW':350.,'loads_kW':np.ones((4,3)).tolist()}


def test_eight_current_candidates_preserve_distinct_requests_even_when_projected_same():
    from tools.control_improvement.candidates import current_candidates
    c=current_candidates(legal_records(),context())
    assert len(c)==8 and len({r['candidate_id'] for r in c})==8
    assert c[0]['request']=={}
    assert all(set(r['request'])<= {'targets','security'} for r in c)
    targets=[r['request'].get('targets',{}) for r in c]
    assert targets[1]!=targets[2]
    assert all(len(v)==6 for v in targets[1:])
    assert all(v>=0 and np.isfinite(v) for d in targets for v in d.values())


def test_future_payload_never_reaches_candidate_or_rule():
    from tools.control_improvement.candidates import current_candidates,choose_current_rule
    a=legal_records();b=copy.deepcopy(a);b[0]['scenario']['future_fault']=-999;b[0]['labels']={'120':0}
    ca=current_candidates(a,context());cb=current_candidates(b,context())
    assert ca==cb
    assert choose_current_rule(a,context(),ca)==choose_current_rule(b,context(),cb)


def test_explicit_future_context_or_unavailable_clock_rejected():
    from tools.control_improvement.candidates import current_candidates
    x=context();x['future_fault_time']=77.
    with pytest.raises(ValueError,match='context'):
        current_candidates(legal_records(),x)
    r=legal_records();r[0]['feature_metadata']['time_minute']['valid_time_min']=61.
    with pytest.raises(ValueError,match='future'):
        current_candidates(r,context())


def test_not_delayed_current_problem_hides_future_release():
    from tools.control_improvement.runtime import extract_current_problem
    from hospital_ies.resilience import RuntimeState
    state=RuntimeState(np.array([100.,160.,0.]),100.,set(),np.zeros(3),{},120.,0.)
    row=SimpleNamespace(timestamp='2017-01-01',outdoor_temperature_C=25.)
    dispatch={'gt_electric_kW':[100.,160.,0.],'grid_import_kW':120.,'diesel_kW':0.,'absorption_cooling_kW':200.}
    core=SimpleNamespace(time=60.,state=state,sequence=9,executed=dispatch,pending_commands=[{'id':9}],failed=False,
        data_at=lambda t:(2,row,np.ones((4,3)),{'GRID':1.,'GT_1':1.,'GT_2':1.,'GT_3':0.,'HRSG_1':1.,'HRSG_2':.3,'HRSG_3':1.,'ABS':1.}),
        delayed_at=lambda t:(False,999.),diesel_available=lambda t,f:False)
    plant=SimpleNamespace(as_dict=lambda:{'synthetic':True})
    env=SimpleNamespace(core=core,state=state,plant=plant,config={'grid_import_limit_kW':350.,'gt_ramp_kW_per_hour':100.,'solver_time_limit_sec':2.},
        execution_contract={'id':'synthetic'},dt_h=.25,steps_done=4,scenario={'grid_limit_kW':350.,'future_secret':999})
    p=extract_current_problem(env)
    assert p.arrival_minute==60. and p.delayed is False
    assert not hasattr(p,'scenario') and not hasattr(p,'hourly')
    p.state.gt_prev[0]=999.
    assert state.gt_prev[0]==100. and core.sequence==9 and core.pending_commands==[{'id':9}]


def test_research_preference_respects_failure_before_cost_without_clipping_predictions():
    from tools.control_improvement.ranking import preference_key,TARGET_NAMES
    scale=np.ones((4,3))
    cheap=np.zeros(len(TARGET_NAMES));cheap[0]=1.;cheap[-1]=-100000.
    safe=np.zeros(len(TARGET_NAMES));safe[-1]=10.
    assert preference_key(safe,scale)<preference_key(cheap,scale)
    negative=safe.copy();negative[0]=-.1
    assert preference_key(negative,scale)[0]==-.1


def test_train_only_scale_rejects_validation_and_keeps_zero_carrier_finite():
    from tools.control_improvement.ranking import fit_demand_scale
    a={'role':'train','loads_kW':np.ones((4,3)).tolist()};b={'role':'validation','loads_kW':np.full((4,3),1000.).tolist()}
    assert np.array_equal(fit_demand_scale([a],120),np.full((4,3),2.))
    with pytest.raises(ValueError,match='train'):
        fit_demand_scale([a,b],120)
    zero=dict(a,loads_kW=np.zeros((4,3)).tolist())
    assert np.array_equal(fit_demand_scale([zero],120),np.ones((4,3)))


def test_difference_target_unknown_stays_masked_and_reference_zero_is_real_difference():
    from tools.control_improvement.ranking import paired_delta
    actual=[1.,3.,None];reference=[1.,1.,0.]
    d,m=paired_delta(actual,reference)
    assert d[0]==0 and d[1]==2 and d[2] is None and m==[True,True,False]


def test_zero_effect_baseline_and_regret_report_ties_and_single_action():
    from tools.control_improvement.ranking import ranking_metrics,TARGET_NAMES
    y=np.zeros((3,len(TARGET_NAMES)));y[1,0]=1.;y[1,-1]=-100.
    p=np.zeros_like(y)
    r=ranking_metrics(y,p,np.ones((4,3)),reference_index=0,rule_index=2)
    assert r['zero_effect_difference_MAE_by_target'][0]==pytest.approx(1/3)
    assert r['predicted_tie_count']==3 and r['selected_action_index']==2
    assert r['selected_regret_by_target'][0]==0.


def test_rf_rank_model_three_seeds_exact_reloads_and_root_overlap_rejected(tmp_path):
    from tools.control_improvement.models import fit_rank_models,load_rank_model
    from tools.control_improvement.ranking import TARGET_NAMES
    rows=[{'row_id':str(i),'role':'train','group_id':'root'+str(i//4),'x':[float(i),float(i%3)],
           'delta_y':[float(i%3)]+[0.]*(len(TARGET_NAMES)-1)} for i in range(12)]
    val=[{'row_id':'v'+str(i),'role':'validation','group_id':'validation','x':[float(i),0.],
          'delta_y':[0.] *len(TARGET_NAMES)} for i in range(3)]
    report=fit_rank_models(rows,val,np.ones((4,3)),tmp_path/'actual_synthetic',source_binding={'scope':'synthetic'})
    assert [r['seed'] for r in report['models']]==[4101,4102,4103]
    for r in report['models']:
        model=load_rank_model(tmp_path/'actual_synthetic'/r['directory'])
        prediction=model.predict(np.asarray([v['x'] for v in val],float))
        saved=np.load(tmp_path/'actual_synthetic'/r['directory']/'validation_predictions.npy')
        assert np.array_equal(prediction,saved)
    val[0]['group_id']='root0'
    with pytest.raises(ValueError,match='group'):
        fit_rank_models(rows,val,np.ones((4,3)),tmp_path/'overlap',source_binding={'scope':'synthetic'})


def test_current_planning_core_does_not_mutate_problem_or_read_other_times(monkeypatch):
    from tools.control_improvement.runtime import extract_current_problem,project_current,CurrentPlanningCore
    from hospital_ies.resilience import RuntimeState
    import hospital_ies.state_transition as transition
    state=RuntimeState(np.array([100.,160.,0.]),100.,set(),np.zeros(3),{},120.,0.)
    action={'gt_electric_kW':[100.,160.,0.],'grid_import_kW':120.,'diesel_kW':0.,'absorption_cooling_kW':200.}
    row=SimpleNamespace(timestamp='2017-01-01',outdoor_temperature_C=25.)
    core=SimpleNamespace(time=60.,sequence=9,executed=action,pending_commands=[{'id':9}],failed=False,
        data_at=lambda t:(2,row,np.ones((4,3)),{'GRID':1.}),delayed_at=lambda t:(True,90.),diesel_available=lambda t,f:False)
    env=SimpleNamespace(core=core,state=state,plant=SimpleNamespace(as_dict=lambda:{}),dt_h=.25,steps_done=4,
        config={'grid_import_limit_kW':350.,'gt_ramp_kW_per_hour':100.,'solver_time_limit_sec':2.},execution_contract={})
    problem=extract_current_problem(env)
    def solve(*args,**kwargs):
        assert args[5]==.25 and args[6]==350. and kwargs['security_gt_penalty_USD_per_kWh']==0.
        return {'action':copy.deepcopy(action),'lost':np.zeros((4,3)),'optimizer_warning':'','solver_backend_layers':[]}
    monkeypatch.setattr(transition,'_solve_milp',solve)
    first=project_current(problem,{})
    second=project_current(problem,{})
    assert first['command']['id']==second['command']['id']==10
    assert first['command']['arrival_minute']==90.
    assert core.sequence==9 and core.pending_commands==[{'id':9}]
    assert problem.core_fields['sequence']==9 and problem.core_fields['pending_commands']==[{'id':9}]
    isolated=CurrentPlanningCore(problem)
    assert not hasattr(isolated.env,'hourly') and set(isolated.env.scenario)=={'grid_limit_kW'}
    with pytest.raises(ValueError,match='time'):
        isolated.data_at(75.)
    with pytest.raises(ValueError,match='advance'):
        isolated.advance(75.)


def test_equal_projected_dispatch_does_not_delete_different_holding_requests(monkeypatch):
    from tools.control_improvement.runtime import prepare_current_candidates
    import tools.control_improvement.runtime as runtime
    class Problem:
        def policy_context(self,history,command):return context()
    calls=[]
    def project(problem,request):
        calls.append(copy.deepcopy(request))
        return {'command':{'dispatch':{},'request':copy.deepcopy(request)},'projected_targets_kW':[1.,2.,3.,4.,0.,6.],
            'solver_calls':[],'events':[],'elapsed_seconds':0.,'status':'success'}
    monkeypatch.setattr(runtime,'project_current',project)
    ctx,candidates,queries,groups=prepare_current_candidates(Problem(),legal_records())
    assert len(candidates)==len(queries)==len(calls)==8
    assert groups==[list(range(8))] and candidates[1]['request']!=candidates[2]['request']


def test_four_history_inputs_are_causal_and_missing_present_values_are_not_zero_imputed():
    from tools.control_improvement.candidates import HISTORY_KEYS,current_candidates,candidate_numeric_features
    a=legal_records();a[0]['features'].update({k:0. for k in HISTORY_KEYS if k not in a[0]['features']})
    a[0]['feature_metadata']={k:{'available_time_min':60.,'valid_time_min':60.} for k in a[0]['features']}
    c=current_candidates(a,context())[2];c['projected_targets_kW']=[1.,2.,3.,4.,0.,6.]
    x=candidate_numeric_features(a,context(),c)
    old=copy.deepcopy(a[0]);old['decision_time_min']=45.;old['features']['time_minute']=45.;old['features']['P0_e_demand_kW']=10.
    old['feature_metadata']={k:{'available_time_min':45.,'valid_time_min':45.} for k in old['features']}
    assert candidate_numeric_features([old]+a,context(),c)!=x
    del a[0]['features'][HISTORY_KEYS[-1]];del a[0]['feature_metadata'][HISTORY_KEYS[-1]]
    with pytest.raises(ValueError,match='missing'):
        candidate_numeric_features(a,context(),c)


def test_branch_outcome_full_horizon_and_duration_are_actual_subinterval_integrals(monkeypatch):
    from tools.control_improvement.branches import window_targets
    import tools.integrated_learning.truth as truth
    monkeypatch.setattr(truth,'actual_energy_fee',lambda env,s:float(s['rate'])*(s['end_minute']-s['start_minute'])/60.)
    def segment(a,b,failed,power):
        return {'start_minute':a,'end_minute':b,'unserved_kW':np.full((4,3),power).tolist(),'cost_USD_step':999.,
            'classification':{'p0_guarantee_failure':failed},'temperature':{'P0':25.,'P1':26.,'P2':27.},'rate':60.}
    p={'segments':[segment(0,60,False,0),segment(60,75,True,4),segment(75,180,False,0)],'events':[],
        'summary':{'status':'completed','observed_until_minute':180.}}
    outcome=window_targets(p,60.,120.,SimpleNamespace())
    assert outcome['full_horizon'] and outcome['targets'][0]==1 and outcome['targets'][1]==15.
    assert outcome['targets'][2:14]==[1.]*12 and outcome['targets'][-1]==120.
    p['segments']=p['segments'][:2];p['summary'].update(status='solver_error',observed_until_minute=75.)
    truncated=window_targets(p,60.,120.,SimpleNamespace())
    assert truncated['targets'][0]==1 and truncated['targets'][1] is None and truncated['targets'][-1] is None
    assert truncated['observed_prefix_failure_minutes']==15.


def test_branch_outcome_cannot_manufacture_complete_support_across_gap(monkeypatch):
    from tools.control_improvement.branches import window_targets
    p={'segments':[{'start_minute':0.,'end_minute':60.},{'start_minute':75.,'end_minute':195.}],
       'events':[],'summary':{'status':'completed','observed_until_minute':195.}}
    with pytest.raises(ValueError,match='contiguous'):
        window_targets(p,60.,120.,None)


def test_new_policy_adapter_binds_only_current_problem_before_decide():
    from tools.control_improvement.episode import BoundaryBoundPolicy
    class Policy:
        policy_id='synthetic'
        def bind_current(self,p):self.problem=p
        def decide(self,r):
            assert self.problem=='only-current'
            return {},{'strategy_fallback':False}
    policy=Policy();calls=[]
    adapter=BoundaryBoundPolicy(SimpleNamespace(scenario={'secret':999}),policy,
                                extractor=lambda env:calls.append('current') or 'only-current')
    assert adapter.decide(legal_records())[0]=={} and calls==['current']


def test_fixed_prototype_snapshot_selector_preserves_three_phases_and_does_not_reselect_failures():
    from tools.control_improvement.run import select_prototype_snapshots
    rows=[{'scenario_id':str(i),'snapshot_minute':t,'experiment_role':'pilot_train'} for i in range(6) for t in (60.,180.,420.)]
    selected=select_prototype_snapshots(rows,[str(i) for i in range(8)])
    assert len(selected)==8
    assert [(r['scenario_id'],r['snapshot_minute']) for r in selected]==[(str(i),180.) for i in range(6)]+[('0',60.),('1',420.)]
    missing=[r for r in rows if not (r['scenario_id']=='1' and r['snapshot_minute']==420.)]
    with pytest.raises(ValueError,match='registered'):
        select_prototype_snapshots(missing,[str(i) for i in range(8)])


def test_current_context_provenance_distinguishes_planned_arrival_and_arrived_ABS_ceiling():
    from tools.control_improvement.runtime import context_provenance
    c=context();c.update(delayed=True,arrival_minute=90.)
    metadata=context_provenance(c)
    assert metadata['schema']=='control_improvement.current_execution_context.v1'
    assert metadata['fields']['arrival_minute']['available_time_min']==60.
    assert metadata['fields']['arrival_minute']['valid_time_min']==60.
    assert metadata['fields']['arrival_minute']['planned_execution_time_min']==90.
    assert 'ceiling' in metadata['fields']['execution_basis_targets_kW']['components'][5]['meaning']
    assert 'actual' in metadata['fields']['execution_basis_targets_kW']['components'][0]['meaning']


def test_partial_current_projection_attempts_are_retained_on_solver_error(monkeypatch):
    from tools.control_improvement.runtime import prepare_current_candidates
    from hospital_ies.execution import SolverExecutionError
    import tools.control_improvement.runtime as runtime
    class Problem:
        def policy_context(self,history,command):return context()
    count=[]
    def project(problem,request):
        count.append(1)
        if len(count)==2:
            error=SolverExecutionError('finite numerical example')
            error.simulation_diagnostic={'original_error':'retained'}
            error.current_projection_diagnostic={'status':'error','request':request,'solver_calls':[{'status':'error'}]}
            raise error
        return {'command':{'dispatch':{},'request':request},'projected_targets_kW':[1.,2.,3.,4.,0.,6.],
            'solver_calls':[{'status':'success'}],'events':[],'elapsed_seconds':0.,'status':'success'}
    monkeypatch.setattr(runtime,'project_current',project);journal=[]
    with pytest.raises(SolverExecutionError) as raised:
        prepare_current_candidates(Problem(),legal_records(),attempt_callback=lambda **r:journal.append(r))
    assert len(raised.value.current_projection_attempts)==2
    assert raised.value.current_projection_attempts[0]['status']=='success'
    assert raised.value.current_projection_attempts[1]['status']=='error'
    assert raised.value.simulation_diagnostic['original_error']=='retained'
    assert len(journal)==4 and [r['event'] for r in journal]==['started','finished','started','finished']


def test_policy_factory_retains_original_requests_and_rule_has_no_extra_authority():
    from tools.control_improvement.policies import make_policy
    assert make_policy('priority_milp').decide(legal_records())[0]=={}
    assert make_policy('secure_milp').decide(legal_records())[0]=={'security':{'gt_loading_penalty_USD_per_kWh':.35}}
    assert make_policy('current_rule_projection0').policy_id=='current_rule_projection0'
    with pytest.raises(ValueError,match='recipe'):
        make_policy('new_hidden_power_source')


def test_six_recipes_and_three_rank_seeds_remain_distinct_variants_not_new_families():
    from tools.control_improvement.closed_loop import policy_variants
    variants=policy_variants()
    assert len(variants)==8 and len({r['recipe_id'] for r in variants})==6
    assert [r['seed'] for r in variants if r['recipe_id']=='execution_aware_RF_rank_projection0']==[4101,4102,4103]


def test_all_registered_validation_states_remain_in_denominator_when_actions_unknown_or_unreached():
    from tools.control_improvement.run import evaluate_registered_states
    from tools.control_improvement.ranking import TARGET_NAMES
    class Model:
        def predict(self,x):return np.zeros((len(x),len(TARGET_NAMES)))
    rows=[]
    for sid in ('complete','partial'):
        for i in range(8):
            y=[0.]*len(TARGET_NAMES);mask=[True]*len(TARGET_NAMES)
            if sid=='partial' and i==7:y[1]=None;mask[1]=False
            rows.append({'scenario_id':sid,'snapshot_minute':180.,'group_id':sid,'candidate_index':i,'rule_index':0,
                'x':[float(i)],'delta_y':y,'target_mask':mask})
    states=[{'scenario_id':sid,'snapshot_minute':180.,'group_id':sid} for sid in ('complete','partial','unreached')]
    result=evaluate_registered_states(rows,Model(),np.ones((4,3)),states)
    assert result['registered_state_count']==3 and result['full_ranking_state_count']==1
    assert result['unknown_or_unreached_state_count']==2
    byid={r['scenario_id']:r for r in result['states']}
    assert byid['partial']['selected_regret_by_target'] is None and byid['partial']['known_target_count'][1]==7
    assert byid['unreached']['selected_regret_by_target'] is None and byid['unreached']['candidate_rows_observed']==0
    assert byid['complete']['predicted_tie_count']==8


def test_rank_fit_loss_has_training_only_per_target_scale_and_raw_unit_reload(tmp_path):
    from tools.control_improvement.models import fit_rank_models,load_rank_model
    from tools.control_improvement.ranking import TARGET_NAMES
    n=len(TARGET_NAMES)
    train=[{'row_id':str(i),'role':'train','group_id':'t'+str(i//4),'x':[float(i)],
        'delta_y':[float(i%2),float(i%3)]+[float(i*1000)]*(n-2)} for i in range(12)]
    validation=[{'row_id':'v','role':'validation','group_id':'v','x':[20.],
                 'delta_y':[1000000.]*n}]
    report=fit_rank_models(train,validation,np.ones((4,3)),tmp_path/'scaled',source_binding={'scope':'synthetic'})
    expected=np.maximum(1.,np.std([r['delta_y'] for r in train],axis=0))
    model=load_rank_model(tmp_path/'scaled'/report['models'][0]['directory'])
    assert np.array_equal(model.metadata['training_target_scale'],expected)
    assert model.metadata['loss']=='multioutput_squared_error_on_train_only_standardized_delta_targets'
    raw=model.predict(np.array([[20.]]))
    scaled=model.model.predict(np.array([[20.]]))*expected
    assert np.array_equal(raw,scaled)


def test_raw_branch_bytes_binding_rejects_any_trajectory_snapshot_or_candidate_change(tmp_path):
    from tools.control_improvement.run import verify_bound_branch_bytes
    from tools.control_improvement.models import sha
    names=('execution_snapshot.json','CURRENT_CANDIDATES.json','candidate.json')
    for name in names:(tmp_path/name).write_text('{}',encoding='utf-8')
    result={'input_artifacts':{name:sha(tmp_path/name) for name in names[:2]},
        'outcomes':[{'trajectory_file':'candidate.json','trajectory_sha256':sha(tmp_path/'candidate.json')}]}
    assert verify_bound_branch_bytes(tmp_path,result)
    for name in names:
        original=(tmp_path/name).read_bytes();(tmp_path/name).write_text('{"changed":true}',encoding='utf-8')
        with pytest.raises(ValueError,match='bytes'):
            verify_bound_branch_bytes(tmp_path,result)
        (tmp_path/name).write_bytes(original)


def test_raw_fit_row_reconstruction_rejects_role_or_group_laundering(tmp_path,monkeypatch):
    import json
    import tools.control_improvement.run as run
    from tools.control_improvement.candidates import current_candidates,candidate_numeric_features,HISTORY_KEYS
    from tools.control_improvement.models import sha
    from tools.control_improvement.ranking import TARGET_NAMES
    history=legal_records();history[0]['features'].update({k:0. for k in HISTORY_KEYS if k not in history[0]['features']})
    history[0]['feature_metadata']={k:{'available_time_min':60.,'valid_time_min':60.} for k in history[0]['features']}
    candidates=current_candidates(history,context())
    for c in candidates:c['projected_targets_kW']=[1.,2.,3.,4.,0.,6.]
    (tmp_path/'execution_snapshot.json').write_text('{}',encoding='utf-8')
    current={'context':context(),'history':history,'candidates':candidates}
    (tmp_path/'CURRENT_CANDIDATES.json').write_text(json.dumps(current),encoding='utf-8')
    y=[0.]*len(TARGET_NAMES);w={'targets':y};outcomes=[];rows=[]
    for i,c in enumerate(candidates):
        name=str(i)+'.json';(tmp_path/name).write_text('{}',encoding='utf-8')
        outcomes.append({'candidate_id':c['candidate_id'],'trajectory_file':name,'trajectory_sha256':sha(tmp_path/name),'windows':[w,w]})
        rows.append({'row_id':'s:t60:'+c['candidate_id'],'scenario_id':'s','snapshot_minute':60.,'role':'validation','group_id':'registered_root',
            'candidate_index':i,'rule_index':0,'x':candidate_numeric_features(history,context(),c),'delta_y':y,'target_mask':[True]*len(y),
            'snapshot_sha256':sha(tmp_path/'execution_snapshot.json'),'outcome_sha256':sha(tmp_path/name)})
    result={'scenario_id':'s','snapshot_minute':60.,'role':'pilot_validation','group_id':'registered_root',
        'input_artifacts':{name:sha(tmp_path/name) for name in ('execution_snapshot.json','CURRENT_CANDIDATES.json')},'outcomes':outcomes,'fit_rows':rows}
    monkeypatch.setattr(run,'window_targets',lambda *args,**kwargs:w)
    assert len(run.reconstruct_bound_fit_rows(tmp_path,result,None))==8
    for key,value in [('role','train'),('group_id','fake_independent_root')]:
        changed=copy.deepcopy(result);changed['fit_rows'][0][key]=value
        with pytest.raises(ValueError,match='lineage'):
            run.reconstruct_bound_fit_rows(tmp_path,changed,None)


def test_final_encoded_core_preserves_RuntimeState_fault_set_semantics_only():
    from tools.control_improvement.branches import compare_control_continuation
    from tools.integrated_learning.snapshot import ExecutionSnapshot
    pre={'extra_faulted':['GT_2','GT_3'],'gt_prev':[1.,2.,3.]}
    post=copy.deepcopy(pre);segment={'start_minute':0.,'end_minute':15.,'pre_state':pre,'post_state':post}
    a={'segments':[segment],'events':[],'decisions':[],'summary':{'status':'completed'}}
    b=copy.deepcopy(a)
    b['segments'][0]['pre_state']['extra_faulted'].reverse();b['segments'][0]['post_state']['extra_faulted'].reverse()
    template=SimpleNamespace(core=SimpleNamespace())
    def final(payload):
        return ExecutionSnapshot(template,{'steps_done':1,'terminated':True},SimpleNamespace(extra_faulted={'GT_2','GT_3'}),
            {'segments':copy.deepcopy(payload['segments']),'errors':[]}, {}, {'scope':'synthetic'})
    assert compare_control_continuation(a,b,0.,reference_final=final(a),restored_final=final(b))['passed']
    assert a['segments'][0]['pre_state']['extra_faulted']==['GT_2','GT_3']
    changed=copy.deepcopy(b);changed['segments'][0]['pre_state']['extra_faulted']=['GT_2','GT_1']
    assert not compare_control_continuation(a,changed,0.,reference_final=final(a),restored_final=final(changed))['passed']
    changed=copy.deepcopy(b);changed['segments'][0]['pre_state']['gt_prev'].reverse()
    assert not compare_control_continuation(a,changed,0.,reference_final=final(a),restored_final=final(changed))['passed']
    changed=copy.deepcopy(b);changed['segments'][0]['pre_state']['extra_faulted']=['GT_2','GT_2','GT_3']
    with pytest.raises(ValueError,match='duplicate'):
        compare_control_continuation(a,changed,0.,reference_final=final(a),restored_final=final(changed))


def test_only_native_remaining_TimeLimit_clock_is_excluded_not_budget_status_or_raw_x():
    from tools.control_improvement.branches import compare_control_continuation
    from tools.integrated_learning.snapshot import ExecutionSnapshot
    payload={'segments':[],'events':[],'decisions':[],'summary':{'status':'model_constraint_infeasible'}}
    native={'rank':0,'status':2,'attempts':[{'parameters':{'TimeLimit':5.99946,'FeasibilityTol':1e-9},'status':2,'native_status':3,'raw_x':None}]}
    error={'config':{'solver_time_limit_sec':6.},'solver_backend_attempts':[native]}
    template=SimpleNamespace(core=SimpleNamespace())
    def final(value):
        return ExecutionSnapshot(template,{'steps_done':1,'terminated':False},SimpleNamespace(gt_prev=[1.,2.,3.]),
            {'segments':[],'errors':[copy.deepcopy(value)]},{},{'scope':'synthetic'})
    changed=copy.deepcopy(error);changed['solver_backend_attempts'][0]['attempts'][0]['parameters']['TimeLimit']=5.99929
    assert compare_control_continuation(payload,payload,0.,reference_final=final(error),restored_final=final(changed))['passed']
    wrong=copy.deepcopy(changed);wrong['config']['solver_time_limit_sec']=5.
    assert not compare_control_continuation(payload,payload,0.,reference_final=final(error),restored_final=final(wrong))['passed']
    for field,value in [('status',0),('raw_x',[1.,2.,3.])]:
        wrong=copy.deepcopy(changed);wrong['solver_backend_attempts'][0]['attempts'][0][field]=value
        assert not compare_control_continuation(payload,payload,0.,reference_final=final(error),restored_final=final(wrong))['passed']
    wrong=copy.deepcopy(changed);wrong['solver_backend_attempts'][0]['attempts'][0]['parameters']['FeasibilityTol']=1e-5
    assert not compare_control_continuation(payload,payload,0.,reference_final=final(error),restored_final=final(wrong))['passed']


def test_pending_candidate_is_known_generated_plan_not_future_actual_supply():
    from tools.control_improvement.candidates import current_candidates
    c=context();c['pending_command_ledger']={'available':True,'generated_minute':45.,'planned_arrival_minute':90.,
        'planned_targets_kW':[90.,150.,0.,180.,0.,220.],'requested_targets_kW':[0.]*6,'request_mask':[False]*6}
    candidates=current_candidates(legal_records(),c)
    assert candidates[2]['candidate_id']=='latest_generated_pending_targets'
    assert candidates[2]['request']['targets']['grid_import']==180.
    c['pending_command_ledger']['generated_minute']=75.
    with pytest.raises(ValueError,match='generated'):
        current_candidates(legal_records(),c)


def test_registered_effect_resolution_preserves_raw_targets_and_failure_precedence():
    from tools.control_improvement.ranking import preference_key,RESEARCH_RESOLUTION,TARGET_NAMES
    y=np.zeros(len(TARGET_NAMES));y[2]=-.000045;y[-1]=8.
    reference=np.zeros_like(y);original=y.copy();scale=np.ones((4,3))
    assert preference_key(y,scale)<preference_key(reference,scale)
    assert preference_key(y,scale,resolution=RESEARCH_RESOLUTION)>preference_key(reference,scale,resolution=RESEARCH_RESOLUTION)
    assert np.array_equal(y,original)
    y[2]=-.02
    assert preference_key(y,scale,resolution=RESEARCH_RESOLUTION)<preference_key(reference,scale,resolution=RESEARCH_RESOLUTION)
    y[0]=1.
    assert preference_key(reference,scale,resolution=RESEARCH_RESOLUTION)<preference_key(y,scale,resolution=RESEARCH_RESOLUTION)


def test_observation_bound_keeps_original_scenario_and_actual_termination_unchanged():
    from tools.control_improvement.episode import ObservationBoundEnvironment
    class Env:
        dt_minutes=15.
        terminated=False
        scenario={'id':'original','duration_steps':40,'exogenous_events':[{'start_minute':99.}]}
        core=SimpleNamespace(time=60.)
        def step(self,request):
            self.core.time+=15.
            return 'obs',0.,False,False,{'original_request':request}
    env=Env();bounded=ObservationBoundEnvironment(env,90.)
    first=bounded.step({});second=bounded.step({})
    assert not first[2] and second[2] and not env.terminated
    assert bounded.scenario=={'id':'original','duration_steps':40,'exogenous_events':[{'start_minute':99.}]}
    with pytest.raises(ValueError,match='observation'):
        bounded.step({})


def test_bounded_summary_cannot_claim_parent600_negative_or_full_completion():
    from tools.control_improvement.episode import finalize_bounded_observation
    from hospital_ies.data_contract import first_failure_label
    payload={'scenario':{'id':'same','duration_steps':40,'step_minutes':15},'decisions':[
        {'decision_time_min':165.,'labels':{'120':first_failure_label(165.,120.,None,180.,'completed')}}],
        'summary':{'status':'completed','observed_until_minute':180.,'requested_end_minute':600.,
            'p0_failed_any':0,'physical_service_failure':False,'first_failure_observed_minute':None,
            'completed':True,'full_trajectory_outcome_valid':True}}
    result=finalize_bounded_observation(payload,180.)
    assert result['summary']['status']=='bounded_observation_completed'
    assert not result['summary']['completed'] and not result['summary']['full_trajectory_outcome_valid']
    assert result['summary']['p0_failed_any'] is None and result['summary']['physical_service_failure'] is None
    assert result['summary']['outcome_unknown_after_minute']==180.
    assert result['decisions'][0]['labels']['120']['value'] is None
    assert result['scenario']['duration_steps']==40
    error=copy.deepcopy(payload);error['summary']['status']='model_constraint_infeasible';error['summary']['observed_until_minute']=170.
    unchanged=finalize_bounded_observation(error,180.)
    assert unchanged['summary']['status']=='model_constraint_infeasible' and not unchanged['summary'].get('bounded_observation_completed',False)


def test_first_candidate_request_then_same_registered_rule_continuation():
    from tools.control_improvement.branches import FirstCandidatePolicy
    class Rule:
        policy_id='rule'
        def bind_current(self,p):self.current=p
        def decide(self,records):return {'targets':{'grid_import':123.}}, {'strategy_fallback':False,'current':self.current}
    rule=Rule();policy=FirstCandidatePolicy({'targets':{'grid_import':5.}},rule,'candidate')
    policy.bind_current('current0');first=policy.decide(legal_records())
    policy.bind_current('current1');second=policy.decide(legal_records())
    assert first[0]['targets']['grid_import']==5.
    assert second[0]['targets']['grid_import']==123. and second[1]['current']=='current1'
