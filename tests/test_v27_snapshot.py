"""Complete-state and causal-history risks of the bounded V27 experiment."""
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def api():
    try:
        return importlib.import_module('tools.model_review_v27.objective_snapshot')
    except ModuleNotFoundError:
        pytest.fail('V27 complete snapshot adapter is absent')


class Core:
    @property
    def state(self):return self.env.state


class UncopyableIO:
    def __deepcopy__(self,memo):raise TypeError('Open evidence resource cannot be copied')


def initialized():
    from hospital_ies.resilience import RuntimeState
    from tools.sci_engineering.storage import FiniteStore
    from tools.operating_response.diesel import DieselRotor
    from tools.model_closure_v22.station import StationState
    from tools.model_closure_v22.storage import ColdRechargeState
    from tools.model_correctness_v23.protection import RestartState
    env=SimpleNamespace(root='fixed_root',hourly='immutable hourly',zones='immutable zones',groups='immutable groups',plant='fixed plant',thermal_cache='fixed thermal',steps_done=6,scenario={'duration_steps':12,'future_environment_only':[120]},config={'limits':[1,2]},history=[{'end_minute':90}],zone_history=[])
    env.state=RuntimeState(np.zeros(3),2400.,set(),np.zeros(3),{})
    env.core=Core();env.core.env=env;env.core.time=90.;env.core.executed={'gt':[0,0,0]};env.core.gt_start_pending={1:{'started':89.,'ready':92.,'requested':200.}}
    env.core.arrived_dispatch={'gt':[0,0,0]};env.core.pending_commands=[{'arrival_minute':95.,'request':{'targets':{'gt_2':200}}}]
    env.core.events=[{'kind':'fault_cleared','time_minute':90}];env.core.segments=[{'start_minute':75.,'end_minute':90.}];env.core.errors=[];env.core.solver_calls=[]
    env.core.ups=FiniteStore({'capacity_kWh':20.,'power_kW':10.});env.core.cold_store=FiniteStore();env.core.station=StationState();env.core.cold_recharge=ColdRechargeState();env.core._m23_restart=RestartState(continuous_powered_minutes=.3);env.core.diesel_rotor=DieselRotor(rated_kW=900.)
    env.event_history=env.core.events;env.segment_history=env.core.segments;env.solver_errors=env.core.errors
    env._solver_row_sidecar=None;env.domain_evidence=None;env.actual_segment_observer=None
    return env


def capture(env):
    return api().capture_execution(env,binding={'science':'fixed','input':'fixed'},legal_history=[{'decision_time_min':75.,'features':{'time_minute':75.}}],current_observation={'decision_time_min':90.,'features':{'time_minute':90.}})


def test_roundtrip_preserves_full_m25_objects_pending_start_and_aliases(tmp_path):
    e=initialized();s=capture(e);p=tmp_path/'state.json';s.save(p)
    restored=api().BoundExecutionSnapshot.load(p,template_env=e,binding={'science':'fixed','input':'fixed'})
    assert restored.content_sha256==s.content_sha256
    a=restored.fork();b=restored.fork()
    assert a.core.env is a and a.core.state is a.state
    assert a.event_history is a.core.events and a.segment_history is a.core.segments and a.solver_errors is a.core.errors
    assert a.core.gt_start_pending[1]['ready']==92.
    assert a.core._m23_restart.continuous_powered_minutes==.3
    assert a.core.ups.energy_kWh==e.core.ups.energy_kWh
    assert a.core.diesel_rotor._history==e.core.diesel_rotor._history
    assert capture(a).physical_state_sha256==s.physical_state_sha256
    a.core.ups.energy_kWh-=1;a.core.pending_commands.clear();a.state.gt_prev[0]=9
    assert b.core.ups.energy_kWh==e.core.ups.energy_kWh and b.core.pending_commands and b.state.gt_prev[0]==0


def test_loading_into_constructed_template_does_not_require_paid_reset(tmp_path):
    e=initialized();s=capture(e);path=tmp_path/'saved.json';s.save(path)
    template=SimpleNamespace(**{k:getattr(e,k) for k in ['root','hourly','zones','groups','plant','thermal_cache']})
    template.core=None;template.engineering_core_type=Core
    loaded=api().BoundExecutionSnapshot.load(path,template_env=template,binding={'science':'fixed','input':'fixed'})
    restored=loaded.fork()
    assert restored.core.time==90. and restored.steps_done==6 and restored.core.gt_start_pending[1]['ready']==92.


def test_snapshot_does_not_copy_open_resources_but_requires_explicit_rebind():
    e=initialized();resource=UncopyableIO();e._solver_row_sidecar=resource;e.actual_segment_observer=lambda value:None
    s=capture(e)
    with pytest.raises(ValueError,match='rebind|resource'):s.fork()
    writer=UncopyableIO();callback=lambda value:None
    f=s.fork(io_resources={'_solver_row_sidecar':writer,'actual_segment_observer':callback})
    assert f._solver_row_sidecar is writer and f.actual_segment_observer is callback
    assert f._solver_row_sidecar is not resource


def test_snapshot_rejects_unknown_mutable_class_instead_of_silently_dropping_it():
    e=initialized();e.core.new_unknown_physics=UncopyableIO()
    with pytest.raises(TypeError,match='Unregistered|unsupported|unknown'):capture(e)


def test_post_labelled_or_future_history_is_not_accepted_as_online_history():
    e=initialized()
    with pytest.raises(ValueError,match='history|causal|offline'):
        api().capture_execution(e,binding={'science':'fixed'},legal_history=[{'decision_time_min':75.,'labels':{'60':{'value':1}}}],current_observation={'decision_time_min':90.})
    with pytest.raises(ValueError,match='history|causal|before'):
        api().capture_execution(e,binding={'science':'fixed'},legal_history=[{'decision_time_min':90.}],current_observation={'decision_time_min':90.})


def test_binding_and_content_tampering_fail_closed(tmp_path):
    import json
    e=initialized();s=capture(e);p=tmp_path/'s.json';s.save(p)
    with pytest.raises(ValueError,match='binding'):
        api().BoundExecutionSnapshot.load(p,template_env=e,binding={'science':'another'})
    d=json.loads(p.read_text());d['payload']['minute']=91.;p.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='SHA|content|hash'):
        api().BoundExecutionSnapshot.load(p,template_env=e,binding={'science':'fixed','input':'fixed'})


def test_actual_gt_minimum_up_down_ledger_is_preserved_as_state():
    from tools.engineering_model.execution import GTClockLedger
    e=initialized();e.core.gt_clocks=GTClockLedger([100.,0.,0.],time_minute=0.)
    e.core.gt_clocks.commit([0.,0.,0.],80.,'actual_trip')
    restored=capture(e).fork()
    assert restored.core.gt_clocks.earliest_start(0,90.)==110.
    assert restored.core.gt_clocks.last_off[0]==80.


def test_solver_prefix_refs_stay_complete_and_new_appends_use_distinct_sidecar(tmp_path):
    from tools.sci_engineering.solver_log import SolverCallLog,SolverRowSidecar
    e=initialized();old=SolverRowSidecar(tmp_path/'prefix_rows.gz');new=SolverRowSidecar(tmp_path/'A1_rows.gz')
    try:
        e._solver_row_sidecar=old;e.core.solver_calls=SolverCallLog(writer=old)
        e.core.solver_calls.append({'status':'success','solver_backend_layers':[{'status':0,'validation':{'rows':[{'value':1.}]}}]})
        original=copy.deepcopy(list(e.core.solver_calls));s=capture(e)
        f=s.fork(io_resources={'_solver_row_sidecar':new})
        f.core.solver_calls.append({'status':'success','solver_backend_layers':[{'status':0,'validation':{'rows':[{'value':2.}]}}]})
        assert f.core.solver_calls[0]==original[0] and len(e.core.solver_calls)==1
        assert 'A1_rows.gz' in repr(f.core.solver_calls[1])
        assert s.document['external_resources']['_solver_row_sidecar']['original_path']==str(old.path)
        with pytest.raises(ValueError,match='same|distinct|original'):
            s.fork(io_resources={'_solver_row_sidecar':old})
    finally:old.close();new.close()


class ClockCore(Core):
    def boundary(self):
        self.current_healthy=self.time>=90.
    def observe(self):return {'loads_kW':np.zeros((4,3)),'availability':{'GT_2':float(self.current_healthy)}}


class ClockEnv:
    def reset(self,*,scenario,seed):
        self.resets=getattr(self,'resets',0)+1;self.scenario=copy.deepcopy(scenario);self.steps_done=0;self.terminated=False;self.dt_minutes=15.
        self.state=SimpleNamespace(budget=100.);self.core=ClockCore();self.core.env=self;self.core.time=0.;self.core.events=[];self.core.segments=[];self.core.errors=[];self.core.solver_calls=[];self.core.current_healthy=False
        self.core.executed={};self.event_history=self.core.events;self.segment_history=self.core.segments;self.solver_errors=self.core.errors;self.history=[];self.zone_history=[];return None
    @staticmethod
    def episode_snapshot_function(e):return {'decision_time_min':e.core.time,'features':{'healthy':e.core.current_healthy}}
    def step(self,request):
        start=self.core.time;self.state.budget-=1;self.core.time+=15;self.steps_done+=1;self.terminated=self.steps_done>=self.scenario['duration_steps']
        seg=dict(start_minute=start,end_minute=self.core.time,unserved_kW=np.zeros((4,3)),cost_USD_step=1.,energy_balance_residuals=[0,0,0],steam_balance_residual_lbph=0.,temperature={f'P{i}':20 for i in range(4)},violation={f'P{i}':False for i in range(4)},classification={'p0_guarantee_failure':False})
        self.core.segments.append(seg);self.history.append({'step':self.steps_done-1,'cost_USD_step':1.})
        command=dict(id=self.steps_done,generated_minute=start,request=request,dispatch={})
        self.core.events.append(dict(kind='command_generated',command=command,time_minute=start,event_id=str(self.steps_done)))
        return None,0.,self.terminated,False,dict(command_id=self.steps_done,executed_action={},p0_guarantee_failure=False,unserved_kW=np.zeros((4,3)),cost_USD_step=1.)


class LegalPolicy:
    policy_id='test_policy'
    def __init__(self):self.seen=[]
    def decide(self,history):
        assert not any({'labels','post_outcome'}&set(row) for row in history)
        from tools.model_correctness_v23.policy import validate_action
        # The old empty action exercised the P1 exporter schema, whereas
        # this adapter must execute the actual current SCI command route.
        self.seen.append(copy.deepcopy(history));return validate_action({'state_coordinated_joint':{}}),{'strategy_fallback':False}


def experiment():
    try:return importlib.import_module('tools.model_review_v27.objective_experiment')
    except ModuleNotFoundError:pytest.fail('V27 prefix/continuation adapter absent')


def scene_and_manifest():
    scene=dict(id='case',duration_steps=12,step_minutes=15.,stage=3,event_class='fault',design_family='fixed',sampling_cohort='fixed',family_id='f',parent_template_id='f',base_state_id='s',exogenous_stream_id='e',distribution_id='fixed')
    manifest=dict(execution_contract_id='contract',assumption_set_id='contract',simulator_hash='source',signature='signature',schemas={},splits={'case':{'split':'development'}})
    return scene,manifest


def test_actual_sci_coordinated_command_route_can_reach_public_step():
    scene,m=scene_and_manifest();r=experiment().capture_prefix(ClockEnv(),scene,LegalPolicy(),17,stop_minute=90.,binding={'source':'fixed'},manifest=m)
    assert r.snapshot is not None and r.payload['error'] is None
    assert 'state_coordinated_joint' in r.payload['decisions'][0]['requested_command']


def test_prefix_keeps_full_scene_and_captures_after_recovery_before_current_command():
    scene,m=scene_and_manifest();p=LegalPolicy();r=experiment().capture_prefix(ClockEnv(),scene,p,17,stop_minute=90.,binding={'source':'fixed'},manifest=m)
    assert r.snapshot.minute==90. and r.env.scenario['duration_steps']==12 and r.env.steps_done==6
    assert r.snapshot.current_observation['features']['healthy'] is True
    assert len(p.seen)==6 and max(p.seen[-1][-1]['decision_time_min'],0)==75.
    assert r.payload['summary']['status']=='captured_prefix' and not r.payload['summary']['full_trajectory_outcome_valid']


def test_restored_continuation_does_not_reset_or_lose_prefix_and_policy_stays_causal():
    scene,m=scene_and_manifest();p=LegalPolicy();first=experiment().capture_prefix(ClockEnv(),scene,p,17,stop_minute=90.,binding={'source':'fixed'},manifest=m)
    q=LegalPolicy();r=experiment().continue_from_snapshot(first.snapshot,q,end_minute=180.,manifest=m)
    assert r.env.resets==1 and r.env.steps_done==12 and r.env.state.budget==88.
    assert len(r.payload['segments'])==12 and r.payload['summary']['full_trajectory_outcome_valid']
    assert q.seen[0][-1]['decision_time_min']==90. and q.seen[0][-1]['features']['healthy'] is True
    assert len(q.seen[0])==7


def test_prefix_does_not_suppress_step_failure_or_create_zero_tail():
    class Failing(ClockEnv):
        def step(self,request):
            if self.core.time==30:raise RuntimeError('actual failed branch')
            return super().step(request)
    scene,m=scene_and_manifest();r=experiment().capture_prefix(Failing(),scene,LegalPolicy(),17,stop_minute=90.,binding={'source':'fixed'},manifest=m)
    assert r.snapshot is None and r.payload['error']['message']=='actual failed branch'
    assert r.payload['summary']['status']=='failed_unknown' and r.payload['summary']['observed_until_minute']==30.
    assert len(r.payload['segments'])==2 and not r.payload['summary']['full_trajectory_outcome_valid']


def test_reset_failure_retains_error_and_unknown_full_horizon():
    class ResetFailure:
        def reset(self,*,scenario,seed):raise RuntimeError('failed original initialization')
    scene,m=scene_and_manifest();r=experiment().capture_prefix(ResetFailure(),scene,LegalPolicy(),17,stop_minute=90.,binding={'source':'fixed'},manifest=m)
    assert r.snapshot is None and r.payload['error']['message']=='failed original initialization'
    assert r.payload['summary']['status']=='failed_unknown' and r.payload['summary']['outcome_unknown_after_minute']==0.
    assert r.payload['summary']['requested_end_minute']==180. and r.payload['segments']==[]


def test_codec_failure_is_detected_at_t0_without_advancing_to_capture_point():
    class UnserializableState(ClockEnv):
        def reset(self,*,scenario,seed):
            super().reset(scenario=scenario,seed=seed);self.core.new_physics=UncopyableIO()
    scene,m=scene_and_manifest();r=experiment().capture_prefix(UnserializableState(),scene,LegalPolicy(),17,stop_minute=90.,binding={'source':'fixed'},manifest=m)
    assert r.snapshot is None and r.payload['error']['type']=='TypeError'
    assert r.env.steps_done==0 and r.env.core.time==0. and r.payload['segments']==[]


def test_codec_preflight_uses_existing_current_once_and_does_not_add_observations(monkeypatch):
    module=experiment();original=module.capture_execution;calls=[]
    def recording(env,**kwargs):
        calls.append(env.core.time);return original(env,**kwargs)
    monkeypatch.setattr(module,'capture_execution',recording)
    class ObservedClock(ClockEnv):
        @staticmethod
        def episode_snapshot_function(env):
            env.snapshot_reads=getattr(env,'snapshot_reads',0)+1
            return ClockEnv.episode_snapshot_function(env)
    scene,m=scene_and_manifest();r=module.capture_prefix(ObservedClock(),scene,LegalPolicy(),17,stop_minute=90.,binding={'source':'fixed'},manifest=m)
    assert calls==[0.,90.] and r.env.snapshot_reads==14 and r.env.resets==1
