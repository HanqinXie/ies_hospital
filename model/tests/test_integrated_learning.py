"""Integrated milestone: complete snapshots and causal six-target controllers."""
from pathlib import Path
import copy
import json
import random
from types import SimpleNamespace
import numpy as np
import pytest


class ToyCore:
    def __init__(self, env):
        self.env = env; self.time = 75.; self.sequence = 4
        self.pending_commands = [{'id': 4, 'arrival_minute': 105., 'request': {'targets': {'gt_1': 120.}}}]
        self.active_command = {'id': 3}; self.executed = {'gt_electric_kW': [70., 80., 90.]}
        self.events = [{'kind': 'pending'}]; self.segments = []; self.errors = []; self.failed = False
        self.pending_shed = {'arrival': 80., 'power': 20.}; self.local_down = {'arrival': 76., 'target': [1., 2., 3.]}
        self.gt_start_pending = {1: {'ready': 85., 'started': 60., 'requested': 100.}}
        self.ramp_anchor = np.array([1., 2., 3.]); self.ramp_budget = 5.; self.auto_shed_latched = True
        self.automatic_disconnected = np.arange(12.).reshape(4, 3)
        self.new_future_core_field = {'must_be_preserved': 123}


class ToyEnv:
    def __init__(self):
        self.root = Path('toy'); self.hourly = 'immutable-hourly'; self.zones = 'immutable-zones'
        self.groups = 'immutable-groups'; self.plant = 'immutable-plant'; self.thermal_cache = 'immutable-thermal'
        self.scenario = {'id': 'toy', 'future_secret': 'not-policy-input'}; self.config = {'capacity': 100.}
        self.state = SimpleNamespace(gt_prev=np.array([70., 80., 90.]), diesel_energy_remaining_kWh=45.,
            overload_minutes=np.array([2., 3., 4.]), thermal_delta={'zone': 1.2}, disconnected_loads=np.arange(12.).reshape(4, 3),
            diesel_status='STARTING', diesel_start_minute=72., extra_faulted={'EC_2'})
        self.steps_done = 5; self.history = [{'unknown_numeric': float('nan')}]; self.zone_history = []
        self.trip_events = []; self.cumulative_p0 = {'e': 1.}; self.terminated = False
        self.new_future_env_field = {'must_be_preserved': 456}
        self.core = ToyCore(self); self.event_history = self.core.events
        self.segment_history = self.core.segments; self.solver_errors = self.core.errors


def test_snapshot_restores_all_dynamic_fields_without_aliasing_the_original():
    from tools.integrated_learning.snapshot import capture_execution
    env = ToyEnv(); snapshot = capture_execution(env, binding={'source': 'synthetic'})
    fork = snapshot.fork()
    assert fork.core.env is fork
    assert fork.core.pending_commands == env.core.pending_commands
    assert fork.core.gt_start_pending == env.core.gt_start_pending
    assert fork.core.pending_shed == env.core.pending_shed
    assert fork.core.local_down == env.core.local_down
    assert fork.core.new_future_core_field == env.core.new_future_core_field
    assert fork.new_future_env_field == env.new_future_env_field
    assert fork.state.extra_faulted == {'EC_2'}
    assert np.array_equal(fork.state.disconnected_loads, env.state.disconnected_loads)
    assert fork.event_history is fork.core.events and fork.solver_errors is fork.core.errors
    fork.core.pending_commands[0]['arrival_minute'] = 999.
    fork.state.gt_prev[0] = 999.; fork.config['capacity'] = 999.
    assert env.core.pending_commands[0]['arrival_minute'] == 105.
    assert env.state.gt_prev[0] == 70. and env.config['capacity'] == 100.


def test_snapshot_preserves_python_numpy_rng_and_typed_json_reload(tmp_path):
    from tools.integrated_learning.snapshot import capture_execution, ExecutionSnapshot
    random.seed(33); np.random.seed(34)
    env = ToyEnv(); snapshot = capture_execution(env, binding={'source': 'synthetic'})
    expected = (random.random(), np.random.random())
    path = tmp_path / 'snapshot.json'; snapshot.save(path)
    restored = ExecutionSnapshot.load(path, template=env, expected_binding={'source': 'synthetic'})
    fork = restored.fork()
    assert (random.random(), np.random.random()) == expected
    assert np.array_equal(fork.core.ramp_anchor, env.core.ramp_anchor)
    assert set(fork.core.gt_start_pending) == {1}
    assert np.isnan(fork.history[0]['unknown_numeric'])
    assert restored.content_sha256 == snapshot.content_sha256


def decisions():
    return [{'decision_time_min': 60., 'features': {'time_minute': 60., 'control_available': False, 'P0_e_demand_kW': 40.},
        'feature_metadata': {k: {'available_time_min': 60.,'valid_time_min':60.} for k in ['time_minute', 'control_available', 'P0_e_demand_kW']},
        'scenario': {'future_secret': 99}, 'labels': {'60': {'value': 1}}, 'post_outcome': {'failure': True}}]


def test_policy_receives_only_current_legal_history_not_future_or_labels():
    from tools.integrated_learning.policies import PolicyController
    class Predictor:
        def predict_targets(self, history):
            assert set(history[0]) == {'decision_time_min', 'features', 'feature_metadata'}
            assert history[0]['features']['time_minute'] == 60.
            history[0]['features']['P0_e_demand_kW'] = 999.
            return np.array([1., 2., 3., 4., 5., 6.])
    original = decisions()
    request, metadata = PolicyController(Predictor(), model_id='synthetic').decide(original)
    assert list(request['targets'].values()) == [1., 2., 3., 4., 5., 6.]
    assert metadata['strategy_fallback'] is False
    assert original[0]['features']['P0_e_demand_kW'] == 40.


def test_future_feature_availability_and_forged_future_feature_are_rejected():
    from tools.integrated_learning.policies import legal_history
    records = decisions(); records[0]['feature_metadata']['time_minute']['available_time_min'] = 61.
    with pytest.raises(ValueError, match='future'):
        legal_history(records)
    records = decisions(); records[0]['features']['future_fault'] = 1.
    records[0]['feature_metadata']['future_fault'] = {'available_time_min': 60.}
    with pytest.raises(ValueError, match='whitelist'):
        legal_history(records)


def test_invalid_prediction_is_a_queued_baseline_strategy_fallback_request():
    from tools.integrated_learning.policies import PolicyController
    class Predictor:
        def predict_targets(self, history):
            return [np.nan, 2., 3., 4., 5., 6.]
    request, metadata = PolicyController(Predictor(), model_id='synthetic', fallback_policy='priority_milp').decide(decisions())
    assert request == {}
    assert metadata['strategy_fallback'] is True
    assert metadata['fallback_reason']
    assert metadata['fallback_execution_semantics'] == 'ordinary_baseline_request_same_MILP_and_queue'


def test_policy_fallback_does_not_catch_physical_solver_errors():
    from tools.integrated_learning.policies import PolicyController
    class SolverError(RuntimeError):
        category = 'solver_exception'
    class Predictor:
        def predict_targets(self, history):
            raise SolverError('retained numerical failure')
    with pytest.raises(SolverError, match='retained'):
        PolicyController(Predictor(), model_id='synthetic').decide(decisions())


def test_external_adapter_uses_the_real_current_request_and_never_resets_a_continuation(monkeypatch):
    from tools.integrated_learning.episode import PolicyEnvironment
    from tools.integrated_learning.policies import PolicyController
    class Predictor:
        def predict_targets(self, history):
            return [1., 2., 3., 4., 5., 6.]
    env = ToyEnv(); env.reset = lambda **kw: pytest.fail('A continuation must not reset')
    actual = []
    env.step = lambda request: (actual.append(copy.deepcopy(request)), 0., False, False, {})
    monkeypatch.setattr('tools.integrated_learning.episode.decision_snapshot', lambda env: decisions()[0])
    proxy = PolicyEnvironment(env, PolicyController(Predictor(), model_id='synthetic'), reset=False)
    proxy.reset(scenario=env.scenario, seed=0)
    proxy.step({'security': {'gt_loading_penalty_USD_per_kWh': .35}})
    assert actual[0] == {'targets': dict(zip(['gt_1','gt_2','gt_3','grid_import','diesel','absorption_cooling_kW'], range(1,7)))}
    assert proxy.requests[60.]['requested_command'] == actual[0]
    assert proxy.requests[60.]['strategy_fallback'] is False


def test_external_adapter_retains_strategy_decision_when_physical_step_throws(monkeypatch):
    from tools.integrated_learning.episode import PolicyEnvironment
    from tools.integrated_learning.policies import BaselinePolicy
    env = ToyEnv()
    def fail(request):
        raise RuntimeError('actual physical failure')
    env.step = fail
    monkeypatch.setattr('tools.integrated_learning.episode.decision_snapshot', lambda env: decisions()[0])
    proxy = PolicyEnvironment(env, BaselinePolicy('priority_milp'), reset=False)
    with pytest.raises(RuntimeError, match='physical'):
        proxy.step({})
    assert proxy.requests[60.]['requested_command'] == {}
    assert proxy.requests[60.]['strategy_status'] == 'original_baseline'


def bc_payload(sid='train', role='pilot_train', group='g_train', times=(0.,15.,30.)):
    rows=[]
    for t in times:
        row=copy.deepcopy(decisions()[0]); row['decision_time_min']=t
        row['features']['time_minute']=t
        row['feature_metadata']={k:{'available_time_min':t,'valid_time_min':t} for k in row['features']}
        row['command_generated']={'generated_minute':t,'dispatch':{'gt_electric_kW':[1.,2.,3.],
            'grid_import_kW':4.,'diesel_kW':0.,'absorption_cooling_kW':6.}}
        rows.append(row)
    return {'scenario':{'id':sid,'experiment_role':role,'root_family_id':sid},
        'identity':{'policy_id':'priority_milp','group_id':group},'decisions':rows,'summary':{'status':'completed'}}


def test_bc_teacher_uses_current_generated_command_and_never_unknown_future_zero():
    from tools.integrated_learning.bc import build_teacher_records
    p=bc_payload();p['decisions'][1]['command_generated']=None
    p['decisions'][2]['actual_execution']={'gt_electric_kW':[999.,999.,999.]}
    rows,stats=build_teacher_records([p])
    assert len(rows)==2 and stats['excluded_no_current_teacher']==1
    assert rows[-1]['targets_kW']==[1.,2.,3.,4.,0.,6.]
    assert rows[-1]['padding_mask']==[0,1,1,1]
    assert all('labels' not in item for item in rows[-1]['sequence'])


def test_bc_teacher_rejects_future_generation_and_split_leakage():
    from tools.integrated_learning.bc import build_teacher_records, split_teacher_records
    p=bc_payload();p['decisions'][0]['command_generated']['generated_minute']=1.
    with pytest.raises(ValueError,match='current'):
        build_teacher_records([p])
    rows,_=build_teacher_records([bc_payload(),bc_payload('val','pilot_validation','g_train')])
    with pytest.raises(ValueError,match='group'):
        split_teacher_records(rows)


def test_bc_development_evaluation_is_excluded_and_cap_is_outcome_independent():
    from tools.integrated_learning.bc import build_teacher_records, split_teacher_records, cap_training_rows
    rows,_=build_teacher_records([bc_payload(),bc_payload('v','pilot_validation','g_v'),
                                 bc_payload('e','development_boundary','g_e')])
    train,val,held=split_teacher_records(rows)
    assert len(train)==len(val)==len(held)==3
    selected=cap_training_rows(train,2)
    assert [r['decision_time_min'] for r in selected]==[0.,30.]
    mutated=copy.deepcopy(train)
    for r in mutated:r['targets_kW']=[999.]*6
    assert [r['row_id'] for r in cap_training_rows(mutated,2)]==[r['row_id'] for r in selected]


def test_bc_metrics_use_fixed_training_scale_and_report_all_six_port_units():
    from tools.integrated_learning.bc import port_metrics
    target=np.array([[0.,10.,0.,10.,0.,10.]])
    pred=np.array([[1.,12.,3.,14.,5.,16.]])
    result=port_metrics(target,pred,np.array([1.,2.,3.,4.,5.,6.]))
    assert result['normalized_six_port_MAE']==1.
    assert result['MAE_kW']==[1.,2.,3.,4.,5.,6.]
    assert result['negative_prediction_count']==0


def test_bc_model_reload_reproduces_six_current_ports_and_detects_artifact_tampering(tmp_path):
    from tools.integrated_learning.bc import build_teacher_records, sequence_record
    from tools.integrated_learning.models import fit_candidate, load_predictor
    from tools.p2b_learning.risk import SequencePreprocessor
    rows,_=build_teacher_records([bc_payload()]);pre=SequencePreprocessor().fit(rows)
    x=pre.transform(rows);y=np.array([r['targets_kW'] for r in rows]);scale=np.ones(6)
    config={'class':'constant','id':'synthetic_constant'}
    result=fit_candidate(config,4101,x,y,x,y,tmp_path,pre.to_dict(),scale)
    model=load_predictor(tmp_path)
    assert np.array_equal(model.predict_targets(decisions()),y[0])
    assert result['reload_max_abs_diff_kW']==0.
    assert result['fit_device']==model.metadata['training_device']=='cpu'
    metadata=tmp_path/'preprocessor.json'
    metadata.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='hash'):
        load_predictor(tmp_path)


def test_bc_rf_and_gru_share_tensor_inputs_and_nonnegative_outputs(tmp_path):
    from tools.integrated_learning.bc import build_teacher_records
    from tools.integrated_learning.models import fit_candidate
    from tools.p2b_learning.risk import SequencePreprocessor
    rows,_=build_teacher_records([bc_payload()]);pre=SequencePreprocessor().fit(rows)
    x=pre.transform(rows);y=np.array([r['targets_kW'] for r in rows]);scale=np.ones(6)
    rf=fit_candidate({'class':'RF','id':'synthetic_RF','n_estimators':4,'max_depth':2,'min_samples_leaf':1},
        4101,x,y,x,y,tmp_path/'rf',pre.to_dict(),scale)
    gru=fit_candidate({'class':'GRU','id':'synthetic_GRU','hidden_size':2,'epochs':2,'patience':1},
        4101,x,y,x,y,tmp_path/'gru',pre.to_dict(),scale)
    assert rf['negative_prediction_count']==gru['negative_prediction_count']==0
    assert rf['input_array_sha256']==gru['input_array_sha256']
    assert rf['reload_max_abs_diff_kW']==gru['reload_max_abs_diff_kW']==0.
    assert rf['fit_device']=='cpu'
    assert json.loads((tmp_path/'rf'/'model_metadata.json').read_text(encoding='utf-8'))['training_device']=='cpu'
    assert gru['fit_device']=='cuda:0'
    assert json.loads((tmp_path/'gru'/'fit_started.json').read_text(encoding='utf-8'))['requested_device']=='cuda:0'
    assert json.loads((tmp_path/'gru'/'model_metadata.json').read_text(encoding='utf-8'))['training_device']=='cuda:0'


def test_bc_gru_rejects_validation_tensor_left_on_cpu(tmp_path,monkeypatch):
    import torch
    from tools.integrated_learning.bc import build_teacher_records
    from tools.integrated_learning.models import fit_candidate
    from tools.p2b_learning.risk import SequencePreprocessor

    if not torch.cuda.is_available():pytest.skip('CUDA hardware required for device mismatch test')
    rows,_=build_teacher_records([bc_payload()]);pre=SequencePreprocessor().fit(rows)
    x=pre.transform(rows);y=np.array([r['targets_kW'] for r in rows]);scale=np.ones(6)
    tensor=torch.tensor;calls=0
    def leave_validation_on_cpu(data,*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==3:kwargs['device']='cpu'
        return tensor(data,*args,**kwargs)
    monkeypatch.setattr(torch,'tensor',leave_validation_on_cpu)
    directory=tmp_path/'misplaced_gru'
    with pytest.raises(RuntimeError,match='BC GRU.*cuda:0'):
        fit_candidate({'class':'GRU','id':'synthetic_GRU','hidden_size':2,'epochs':1,'patience':1},
            4101,x,y,x,y,directory,pre.to_dict(),scale)
    assert calls>=4
    started=json.loads((directory/'fit_started.json').read_text(encoding='utf-8'))
    error=json.loads((directory/'fit_error.json').read_text(encoding='utf-8'))
    assert started['requested_device']=='cuda:0'
    assert error['type']=='RuntimeError' and 'validation' in error['message']


def test_registered_secure_projection_is_fixed_metadata_not_a_seventh_learned_action():
    from tools.integrated_learning.policies import PolicyController
    class Predictor:
        def predict_targets(self,history):return [1.,2.,3.,4.,5.,6.]
    request,meta=PolicyController(Predictor(),model_id='test',projection_policy='secure_milp',
                                  fallback_policy='secure_milp').decide(decisions())
    assert len(request['targets'])==6
    assert request['security']=={'gt_loading_penalty_USD_per_kWh':.35}
    assert meta['projection_policy']=='secure_milp'


@pytest.mark.parametrize('valid',[75.,float('nan'),float('inf'),None])
def test_model_guard_checks_both_feature_clocks_including_missing_clock(valid):
    from tools.integrated_learning.policies import legal_history
    records=decisions();meta=records[0]['feature_metadata']['P0_e_demand_kW']
    if valid is None:meta.pop('valid_time_min')
    else:meta['valid_time_min']=valid
    with pytest.raises(ValueError,match='clock|future'):
        legal_history(records)


def test_d0_comparison_ignores_wall_clock_only_and_detects_physical_energy_change():
    from tools.integrated_learning.branches import compare_continuation
    p={'segments':[{'start_minute':0.,'end_minute':15.,'cost_USD_step':2.,'unserved_kW':[[1.]]}],
       'events':[{'time_minute':0.,'kind':'command_generated','command':{'dispatch':{'grid_import_kW':3.}}}],
       'decisions':[{'decision_time_min':0.,'requested_command':{},'labels':{'60':{'value':None,'valid':False}},
                     'decision_ms':1.}], 'summary':{'status':'completed'}}
    q=copy.deepcopy(p);q['decisions'][0]['decision_ms']=999.
    assert compare_continuation(p,q,0.)['passed']
    q['segments'][0]['unserved_kW'][0][0]+=1e-3
    result=compare_continuation(p,q,0.)
    assert not result['passed'] and 'unserved' in result['differences'][0]['path']


def test_d0_runtime_fault_set_permutation_is_equivalent_but_ports_remain_ordered():
    from tools.integrated_learning.branches import compare_continuation
    p={'segments':[{'start_minute':0.,'end_minute':15.,
        'pre_state':{'extra_faulted':['GT_1','GT_2','GT_3']},
        'post_state':{'extra_faulted':['GT_1','GT_2','GT_3']},
        'action':{'gt_electric_kW':[10.,20.,30.]}}],
       'events':[],'decisions':[],'summary':{'status':'completed'}}
    q=copy.deepcopy(p)
    q['segments'][0]['pre_state']['extra_faulted']=['GT_2','GT_3','GT_1']
    q['segments'][0]['post_state']['extra_faulted']=['GT_3','GT_1','GT_2']
    assert compare_continuation(p,q,0.)['passed']
    q['segments'][0]['action']['gt_electric_kW']=[20.,30.,10.]
    assert not compare_continuation(p,q,0.)['passed']


@pytest.mark.parametrize('members',[['GT_1','GT_2','EC_1'],['GT_1','GT_2'],
    ['GT_1','GT_2','GT_3','EC_1'],['GT_1','GT_2','GT_2'],[None,'GT_2','GT_3']])
def test_d0_runtime_fault_set_rejects_changed_members_duplicates_and_invalid_ids(members):
    from tools.integrated_learning.branches import compare_continuation
    p={'segments':[{'start_minute':0.,'end_minute':15.,
        'pre_state':{'extra_faulted':['GT_1','GT_2','GT_3']}}],
       'events':[],'decisions':[],'summary':{'status':'completed'}}
    q=copy.deepcopy(p);q['segments'][0]['pre_state']['extra_faulted']=members
    assert not compare_continuation(p,q,0.)['passed']


def test_branch_candidate_generation_accepts_current_legal_view_only():
    from tools.integrated_learning.branches import current_candidates
    dispatch={'gt_electric_kW':[10.,20.,30.],'grid_import_kW':40.,'diesel_kW':0.,'absorption_cooling_kW':50.}
    candidates=current_candidates(decisions(),dispatch,'secure_milp')
    assert len(candidates)==3 and candidates[0]['request']=={'security':{'gt_loading_penalty_USD_per_kWh':.35}}
    assert candidates[1]['request']['targets']['gt_1']==9.
    assert candidates[2]['request']['targets']['absorption_cooling_kW']==45.
    altered=decisions();altered[0]['labels']={'60':{'value':0}}
    assert current_candidates(altered,dispatch,'secure_milp')==candidates


def raw_bc_fixture(folder):
    from tools.p2b_experiment.runner import save_payload,payload_path
    from tools.p2b_learning.risk import payload_digest
    from tools.integrated_learning.models import sha
    manifest={'signature':'synthetic','policies':['priority_milp','secure_milp'],
        'execution_contract_id':'fixture','assumption_set_id':'fixture','simulator_hash':'fixture',
        'schemas':{'fixture':'1'},'options':{'seed':7},'scenarios':[],'splits':{}}
    payloads=[];entries=[]
    for sid,role in [('t','pilot_train'),('v','pilot_validation'),('e','development_boundary')]:
        manifest['scenarios'].append({'id':sid,'experiment_role':role,'root_family_id':sid})
        manifest['splits'][sid]={'group_id':'g_'+sid,'experiment_role':role}
        for policy in manifest['policies']:
            p=bc_payload(sid,role,'g_'+sid);p['scenario']=copy.deepcopy(manifest['scenarios'][-1]);p['seed']=7
            identity=dict(manifest['splits'][sid],scenario_id=sid,policy_id=policy,continuation_policy_id=policy,
                manifest_signature='synthetic',**{k:manifest[k] for k in ('execution_contract_id','assumption_set_id','simulator_hash','schemas')})
            p['identity']=identity;p['summary'].update(scenario_id=sid,policy_id=policy)
            for d in p['decisions']:d.update(identity)
            save_payload(folder,sid,policy,'synthetic',p);path=payload_path(folder,sid,policy)
            entries.append({'scenario_id':sid,'policy_id':policy,'path':path.relative_to(folder).as_posix(),
                'checkpoint_sha256':sha(path),'payload_sha256':payload_digest(p)})
            if role!='development_boundary':payloads.append(p)
    return manifest,{'passed':True,'complete_budget':True,'manifest_signature':'synthetic','checkpoint_digests':entries},payloads


def test_bc_stream_checks_raw_canonical_and_bytes_before_extracting_compact_teachers(tmp_path):
    from tools.integrated_learning.fit_bc import bind_and_extract,stream_pilot_payloads
    manifest,audit,payloads=raw_bc_fixture(tmp_path)
    rows,stats,binding=bind_and_extract(manifest,audit,stream_pilot_payloads(manifest,audit,tmp_path),tmp_path)
    assert len(rows)==6 and {r['policy_id'] for r in rows}=={'secure_milp'}
    assert {r['role'] for r in rows}=={'pilot_train','pilot_validation'}
    assert binding['verified_raw_pilot_payloads']==4 and binding['compact_teacher_rows_are_not_raw_payloads']
    mutated=copy.deepcopy(payloads);mutated[0]['decisions'][0]['features']['P0_e_demand_kW']=999.
    with pytest.raises(ValueError,match='canonical'):
        bind_and_extract(manifest,audit,iter(mutated),tmp_path)
    entry=audit['checkpoint_digests'][0]
    path=tmp_path/entry['path'];path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='bytes'):
        bind_and_extract(manifest,audit,iter(payloads),tmp_path)


def test_bc_stream_refuses_missing_pair_and_frozen_evaluation_input(tmp_path):
    from tools.integrated_learning.fit_bc import bind_and_extract
    manifest,audit,payloads=raw_bc_fixture(tmp_path)
    with pytest.raises(ValueError,match='missing'):
        bind_and_extract(manifest,audit,iter(payloads[:-1]),tmp_path)
    p=copy.deepcopy(payloads[0]);p['scenario']=manifest['scenarios'][-1];p['identity']['policy_id']='priority_milp'
    with pytest.raises(ValueError,match='frozen evaluation'):
        bind_and_extract(manifest,audit,iter([p]),tmp_path)


def test_branch_window_preserves_unknown_total_and_observed_positive_prefix():
    from tools.integrated_learning.truth import window_outcome
    p={'summary':{'status':'solver_error'},'segments':[{'start_minute':90.,'end_minute':105.,
        'cost_USD_step':2.,'unserved_kW':np.ones((4,3)).tolist(),'classification':{'p0_guarantee_failure':True}}]}
    result=window_outcome(p,90.,60.)
    assert result['full_horizon'] is False and result['ENS_kWh_by_priority_carrier'] is None
    assert result['P0_failure_in_window']==1
    assert result['observed_prefix_ENS_kWh_by_priority_carrier'][0][0]==.25
    assert result['actual_energy_fee_USD'] is None


def test_actual_energy_fee_uses_original_illustrative_tariff_and_no_objective_terms(monkeypatch):
    import pandas as pd
    from tools.integrated_learning.truth import actual_energy_fee
    from hospital_ies.resilience import KWH_PER_MMBTU
    timestamp=pd.Timestamp('2025-01-01 00:00')
    env=SimpleNamespace(plant=SimpleNamespace(gas_price_usd_per_mmbtu=2.,steam_kWh_per_lb=.1,boiler_eff=.5),
        # Match read_data's real RangeIndex; hour_of_year remains one-based.
        hourly=pd.DataFrame({'timestamp':[timestamp]},index=[0]))
    seen=[]
    monkeypatch.setattr('tools.integrated_experiment.energy_metrics.illustrative_tariff',lambda stamp,p:(seen.append(stamp) or .2))
    segment={'start_minute':0.,'end_minute':15.,'hour_of_year':1,'action':{'gt_fuel_MMBtu_h':1.,
        'boiler_steam_lbph':100.,'grid_import_kW':20.,'diesel_kW':5.},'security_objective_penalty_USD_step':999.,
        'tracking_penalty_USD_step':999.,'cost_USD_step':999.}
    assert actual_energy_fee(env,segment)==pytest.approx(.25*(2.+100*.1/.5/KWH_PER_MMBTU*2+20*.2+5*.3))
    assert seen==[timestamp]


def test_snapshot_selection_uses_train_roots_current_clocks_and_no_outcomes():
    from tools.integrated_learning.run_control import select_training_snapshots
    specs=[]
    for root in range(8):
        for pressure in (0.,.5,1.):
            specs.append({'id':f'r{root}_{pressure}','experiment_role':'pilot_train' if root<6 else 'development_boundary',
                'root_family_id':f'r{root}','condition_id':f'c{root%2}','pressure_coordinate':pressure,'event_class':'L4a',
                'fake_future_failure':root%2})
    selected=select_training_snapshots({'scenarios':specs},4)
    assert len({r['root_family_id'] for r in selected})==4
    assert [r['condition_id'] for r in selected]==['c0','c1','c0','c1']
    assert [r['snapshot_minute'] for r in selected]==[90.,165.,90.,165.]
    assert all(r['scenario_id'].endswith('_0.5') for r in selected)


@pytest.mark.parametrize('hour',[31,8760])
def test_energy_fee_real_hourly_boundary_and_last_hour(hour):
    from hospital_ies.pipeline import read_data
    from hospital_ies.model import load_plant,illustrative_tariff
    from tools.integrated_learning.truth import actual_energy_fee
    root=Path(__file__).resolve().parents[1]
    env=SimpleNamespace(hourly=read_data(root),plant=load_plant(root/'configs/plant_configs.json','doe_capacity_matched_exploratory'))
    segment={'hour_of_year':hour,'start_minute':0.,'end_minute':15.,'action':{
        'gt_fuel_MMBtu_h':0.,'boiler_steam_lbph':0.,'grid_import_kW':100.,'diesel_kW':0.}}
    expected=100.*.25*illustrative_tariff(env.hourly.iloc[hour-1].timestamp,env.plant)
    assert actual_energy_fee(env,segment)==pytest.approx(expected,abs=1e-12)
