"""Real driver/journal controls with a deterministic fake physical executor."""
from __future__ import annotations

import copy
import gzip
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def api():
    assert (ROOT/'tools/annual_program/runner.py').is_file(), 'Integrated annual runner is missing'
    return importlib.import_module('tools.annual_program.runner')


REQUEST={'ordinary_dispatch':{'dispatch':{
    'gt_electric_kW':[0.,0.,0.],'grid_import_kW':1.,'diesel_kW':0.,
    'boiler_steam_lbph':0.,'absorption_cooling_kW':0.,
    'electric_chiller_cooling_kW':[0.,0.,0.],'hrsg_steam_lbph':0.,
    'tower_rejection_kW':0.,'gt_fuel_MMBtu_h':0.},
    'disconnected_kW':[[0.,0.,0.] for _ in range(4)],'rule_diagnostic':{}}}
CONFIG={'p0_solver_feasibility_tol_kW':1e-6,'p0_unserved_abs_tol_kW':.1,
        'p0_unserved_rel_tol':.01}
SCENARIO={'id':'fake','duration_steps':3,'step_minutes':15.,'initialization_seed':1}


def segment(start,end,command):
    loads=[[12.,8.,4.] for _ in range(4)]
    lost=[[0.,0.,0.] for _ in range(4)]
    return {'start_minute':start,'end_minute':end,'hour_of_year':1,
        'command_id':command,'loads_kW':loads,'unserved_kW':lost,
        'service_request_ledger':{'original_demand_kW':loads,'delivered_kW':loads,
            'active_cut_kW':lost,'connected_unserved_kW':lost},
        'm21_storage':{key:{'initial_kWh':10.,'final_kWh':10.,'charged_input_kWh':0.,
            'discharged_kWh':0.,'standing_loss_kWh':0.,'conversion_loss_kWh':0.}
            for key in ('ups','cold_store')}}


class FakeCore:
    pass


class FakeController:
    def __init__(self):
        self.policy_id='pure-driver-fixture';self.config={};self.mailbox={}


class FakeEnv:
    def __init__(self,fail_step=None,fail_before=False):
        self.config=copy.deepcopy(CONFIG)
        self.reset_count=0;self.steps_done=0;self.actual_segment_observer=None
        self.fail_step=fail_step;self.fail_before=fail_before
        self.history=[];self.zone_history=[];self.trip_events=[];self.planning_queries=[]
        self.core=FakeCore()
        self.core.__dict__.update(env=self,time=0.,segments=[],events=[],errors=[],
            solver_calls=[],_m23_advance_depth=0,failed=False,_m23_observed_segments=0,
            arrived_history={None:{}},boundary_history={0.:{}},active_command=None,
            pending_commands=[{'id':7,'arrival_minute':35.}],
            ups=SimpleNamespace(initial_charged_input_kWh=4.),
            cold_store=SimpleNamespace(initial_charged_input_kWh=5.))
        self.segment_history=self.core.segments;self.event_history=self.core.events
        self.solver_errors=self.core.errors

    def reset(self,*,scenario,seed):
        self.reset_count+=1
        assert self.reset_count==1
        self.scenario=copy.deepcopy(scenario)
        if hasattr(self,'domain_evidence'):
            # Native initialization/probe evidence may legitimately be known to
            # the store without belonging to any later block call reference.
            self.domain_evidence.store([{'name':'native_initial_probe_fixture','value':1.}])
        return {},{}

    def step(self,request):
        assert request==REQUEST
        self.core.solver_calls.append({'status':'success','step':self.steps_done})
        self.core.events.append({'kind':'command_generated','event_id':len(self.core.events)+1,
            'time_minute':self.core.time,'command':{'id':self.steps_done+1}})
        failed=self.steps_done==self.fail_step
        if failed and self.fail_before:raise RuntimeError('failed before any actual segment')
        end=self.core.time+(5. if failed else 15.)
        row=segment(self.core.time,end,self.steps_done+1)
        # The driver now exercises native lookup-map retention too. These are
        # fake permissions/boundaries, never new plant facts or relaxed checks.
        self.core.arrived_history[self.steps_done+1]={'fixture_only':True}
        self.core.active_command={'id':self.steps_done+1}
        self.core.boundary_history[end]={'fixture_only':True}
        self.core.segments.append(row)
        self.core.time=end
        if self.actual_segment_observer:
            self.actual_segment_observer(row);self.core._m23_observed_segments+=1
        if failed:raise RuntimeError('failed after an actual partial segment')
        self.history.append({'step':self.steps_done})
        self.steps_done+=1
        return {},0.,self.steps_done>=self.scenario['duration_steps'],False,{
            'segments':[copy.deepcopy(row)],'command_id':self.steps_done}


class FakePolicyHistory:
    def __init__(self,controller=None):self.clocks=[];self.controller=controller
    def decide(self,record):
        assert set(record)=={'decision_time_min'}
        assert not self.clocks or record['decision_time_min']>self.clocks[-1]
        self.clocks.append(record['decision_time_min'])
        return copy.deepcopy(REQUEST),{'actual_current_only':True}
    def to_state(self):return {'clocks':list(self.clocks)}
    @classmethod
    def from_state(cls,state,*,controller):
        obj=cls(controller);obj.clocks=list(state['clocks']);return obj


def setup(tmp_path,monkeypatch,*,fail_step=None,fail_before=False,journal_type=None):
    module=api()
    monkeypatch.setattr(module,'_snapshot',lambda env:{'decision_time_min':env.core.time})
    monkeypatch.setattr(module,'_terminal',lambda env,end:{'snapshot_time_minute':end,
        'pending_commands':copy.deepcopy(env.core.pending_commands)})
    monkeypatch.setattr(module,'_seal_solver_evidence',lambda *a,**k:(None,{}))
    monkeypatch.setattr(module,'_block_evaluation',lambda segments,start,end,**kwargs:{
        'economics':{'observed_operating_fee_USD2001':(end-start)/60.,
            'initial_preparation_fee_USD2001':sum(kwargs['preparation'].values())}})
    from tools.annual_readiness.journal import BlockJournal
    from tools.annual_program.service_history import ServiceHistoryAccumulator
    journal=(journal_type or BlockJournal)(tmp_path/'evidence',identity={'case_id':'fake'})
    env=FakeEnv(fail_step,fail_before)
    service=ServiceHistoryAccumulator(env.config);policy=FakePolicyHistory()
    return module,env,service,policy,journal


def run(module,env,service,policy,journal,path,**kwargs):
    return module._execute_loop(env,SimpleNamespace(),SCENARIO,journal,path,
        service_history=service,policy_history=policy,history_identity={'case':'fake'},**kwargs)


def raw_blocks(path):
    result=[]
    for file in sorted((path/'evidence/blocks').glob('*.gz')):
        with gzip.open(file,'rt',encoding='utf8') as stream:result.append(json.load(stream))
    return result


def test_three_actual_steps_reset_once_and_preserve_complete_global_history(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    result=run(module,env,service,policy,journal,tmp_path)
    assert result['status']=='completed' and env.reset_count==1
    assert result['observed_until_minute']==45. and result['unknown_tail_minutes']==0.
    assert policy.clocks==[0.,15.,30.] and service.segment_count==3
    assert len(env.core.segments)==3 and env.core.segments.retained_count<=1
    assert env.segment_history is env.core.segments and env.event_history is env.core.events
    assert [row['event_id'] for row in env.core.events]==[1,2,3]
    assert len(env.core.solver_calls)==0
    assert result['economics']['initial_preparation_fee_USD2001']==9.
    assert result['economics']['observed_operating_fee_USD2001']==.75
    assert [b['solver_index_base'] for b in raw_blocks(tmp_path)]==[0,1,2]
    assert sum(b['initial_preparation'] is not None for b in raw_blocks(tmp_path))==1
    assert result['bounded_all_live_state'] is False
    assert result['execution_map_retention']['acknowledged_block_count']==3
    assert result['execution_map_retention']['last_receipt']['committed_end_minute']==45.
    assert 'core.arrived_history' not in result['unbounded_state_remaining']
    assert 'core.boundary_history' not in result['unbounded_state_remaining']
    assert result['log_migrations']['failure_history_references_are_inline_lists'] is False


def test_failed_native_reset_retains_primary_error_before_history_installation(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    def fail_reset(*,scenario,seed):
        raise ValueError('original native parser failure')
    monkeypatch.setattr(env,'reset',fail_reset)
    result=run(module,env,service,policy,journal,tmp_path,histories={})
    assert result['status']=='failed'
    assert result['error']['message']=='original native parser failure'
    assert result['observed_until_minute']==0.
    assert result['economics']['actual_fee_USD2001'] is None
    assert result['legal_observation_count']==0


def test_controlled_suspend_then_continue_without_reset_or_duplicate_preparation(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    observed=[]
    def checkpoint(**context):
        assert len(raw_blocks(tmp_path))==2
        assert context['loop_state']['committed_end_minute']==30.
        assert context['env'].core.pending_commands==[{'id':7,'arrival_minute':35.}]
        observed.append(context)
        return {'publication_path':'fake-controlled-marker'}
    first=run(module,env,service,policy,journal,tmp_path,stop_after_blocks=2,
        checkpoint_callback=checkpoint)
    assert first['status']=='suspended' and first['unknown_tail_minutes']==15.
    assert not (tmp_path/'evidence/SUMMARY.json').exists()
    assert len(observed)==1
    second=run(module,env,service,policy,journal,tmp_path,reset=False,
        loop_state=first['loop_state'],histories=observed[0]['histories'])
    assert second['status']=='completed' and env.reset_count==1
    assert policy.clocks==[0.,15.,30.]
    assert second['economics']['initial_preparation_fee_USD2001']==9.
    assert second['economics']['observed_operating_fee_USD2001']==.75
    assert len(raw_blocks(tmp_path))==3


def test_partial_failed_step_is_preserved_and_never_checkpointed(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch,fail_step=1)
    result=run(module,env,service,policy,journal,tmp_path,
        checkpoint_callback=lambda **kwargs:pytest.fail('failed core must not checkpoint'))
    assert result['status']=='failed' and result['observed_until_minute']==20.
    assert result['unknown_tail_minutes']==25. and service.segment_count==2
    assert raw_blocks(tmp_path)[-1]['error']['type']=='RuntimeError'
    assert result['summary']['block_count']==2


def test_acknowledged_failure_keeps_exact_error_history_database_versions(tmp_path,monkeypatch):
    from tools.annual_program.failure_history import _history_reference,resolve_history_reference
    module,env,service,policy,journal=setup(tmp_path,monkeypatch,fail_step=1)
    original=env.step;references=[]
    def fail_with_history_reference(request):
        try:return original(request)
        except RuntimeError as exc:
            env.core.failed=True
            for history in (env.core.segments,env.core.events):
                reference=_history_reference(history,exc);references.append(reference)
            env.core.errors.append({'completed_segments':references[0],'completed_events':references[1]})
            raise
    monkeypatch.setattr(env,'step',fail_with_history_reference)
    result=run(module,env,service,policy,journal,tmp_path)
    assert result['status']=='failed' and result['summary']['block_count']==2
    for reference in references:
        view=resolve_history_reference(reference,reference['history_state']['identity'])
        try:assert len(view)==2
        finally:view.close()


def test_healthy_maps_compact_only_after_durable_acknowledgement(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch);seen=[]
    def compact(core,*,committed_end_minute):
        assert core is env.core
        assert len(raw_blocks(tmp_path))==int(committed_end_minute/15.)
        assert core.time==committed_end_minute and core.failed is False
        seen.append(committed_end_minute)
        return {'schema':'fixture-only-compaction-proof','committed_end_minute':committed_end_minute,
            'arrived_history':{'before_count':2,'kept_count':2,'purged_count':0},
            'boundary_history':{'before_count':2,'kept_count':1,'purged_count':1}}
    monkeypatch.setattr(module,'compact_execution_maps',compact,raising=False)
    result=run(module,env,service,policy,journal,tmp_path)
    assert result['status']=='completed' and seen==[15.,30.,45.]


def test_indexed_domain_installation_precedes_native_core_creation(tmp_path):
    from tools.model_correctness_v23.evidence import DomainEvidenceStore
    module=api();original=DomainEvidenceStore(tmp_path/'domain_evidence',enable_all_validation_rows=True)
    env=SimpleNamespace(domain_evidence=original)
    binding=dict(source={'tools/model_correctness_v23/evidence.py':module._sha(ROOT/'tools/model_correctness_v23/evidence.py')},
        inputs={'fixture':'no numeric parsing'},config={'fixture_only':True})
    store=module._attach_indexed_domains(env,tmp_path,binding,'unreset-native-constructor')
    try:
        assert not hasattr(env,'core')
        assert env.domain_evidence is store and store.to_state()['committed_count']==0
    finally:store.close()


def test_failure_before_actual_interval_retains_solver_and_unknown_tail(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch,fail_step=0,fail_before=True)
    result=run(module,env,service,policy,journal,tmp_path)
    assert result['status']=='failed' and result['observed_until_minute']==0.
    assert result['unknown_tail_minutes']==45. and len(env.core.solver_calls)==1
    assert list(tmp_path.glob('FAILURE_CONTEXT*.json.gz'))


def test_rejected_append_never_releases_live_solver_or_accepts_actual_prefix(tmp_path,monkeypatch):
    from tools.annual_readiness.journal import BlockJournal
    class RejectingJournal(BlockJournal):
        def append(self,block):raise OSError('injected rejected journal append')
    module,env,service,policy,journal=setup(tmp_path,monkeypatch,journal_type=RejectingJournal)
    result=run(module,env,service,policy,journal,tmp_path)
    assert result['status']=='failed' and result['observed_until_minute']==0.
    assert result['actual_core_until_minute']==15. and len(env.core.solver_calls)==1
    assert len(env.core.segments)==1 and env.core.segments.retained_count==1
    assert list(tmp_path.glob('REJECTED_BLOCK*.json.gz'))


@pytest.mark.parametrize('limit',[0,-1,True,1.5])
def test_invalid_stop_limit_fails_before_reset(tmp_path,monkeypatch,limit):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='stop_after_blocks'):
        run(module,env,service,policy,journal,tmp_path,stop_after_blocks=limit)
    assert env.reset_count==0


def test_suspend_requires_real_checkpoint_callback_instead_of_fake_success(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    result=run(module,env,service,policy,journal,tmp_path,stop_after_blocks=1)
    assert result['status']=='failed'
    assert result['error']['type']=='ValueError'
    assert result['observed_until_minute']==15.


def test_resume_rejects_changed_actual_prefix_before_any_new_step(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    captured=[]
    first=run(module,env,service,policy,journal,tmp_path,stop_after_blocks=1,
        checkpoint_callback=lambda **kw:captured.append(kw) or {'publication_path':'fake'})
    with (tmp_path/first['loop_state']['actual_prefix_parts'][0]['file']).open('ab') as stream:
        stream.write(b'newer-unacknowledged\n')
    result=run(module,env,service,policy,journal,tmp_path,reset=False,
        loop_state=first['loop_state'],histories=captured[0]['histories'])
    assert result['status']=='failed' and env.steps_done==1
    assert result['error']['type']=='ValueError'


def test_restored_checkpoint_frequency_rejects_boolean_integer_alias_before_new_step(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    captured=[]
    first=run(module,env,service,policy,journal,tmp_path,stop_after_blocks=1,
        checkpoint_every_blocks=1,
        checkpoint_callback=lambda **kw:captured.append(kw) or {'publication_path':'fake'})
    state=copy.deepcopy(first['loop_state']);state['checkpoint_every_blocks']=True
    with pytest.raises(ValueError,match='checkpoint'):
        run(module,env,service,policy,journal,tmp_path,reset=False,loop_state=state,
            histories=captured[0]['histories'],checkpoint_every_blocks=1)
    assert env.steps_done==1 and env.reset_count==1


def test_current_snapshot_called_once_per_actual_decision(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    calls=[]
    def snapshot(actual):
        calls.append(actual.core.time)
        return {'decision_time_min':actual.core.time}
    monkeypatch.setattr(module,'_snapshot',snapshot)
    result=run(module,env,service,policy,journal,tmp_path)
    assert result['status']=='completed' and calls==[0.,15.,30.]


def test_preexisting_stop_file_rejects_public_run_before_any_runtime_or_writer(tmp_path,monkeypatch):
    module=api();flag=tmp_path/'stop.request';flag.touch();output=tmp_path/'never-started'
    for name in ('_build_runtime','SolverRowSidecar','BlockJournal','create_runtime_freeze','_bindings'):
        monkeypatch.setattr(module,name,lambda *a,**k:pytest.fail('preexisting stop must prevent resource construction'))
    with pytest.raises(RuntimeError,match='[Ss]top.*before|before.*[Ss]top'):
        module.run_case({},output,approval_receipt={},stop_file=flag)
    assert flag.exists() and not output.exists()


def test_preexisting_stop_file_rejects_loop_before_reset_or_observation(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    flag=tmp_path/'stop.request';flag.touch()
    with pytest.raises(RuntimeError,match='[Ss]top.*before|before.*[Ss]top'):
        run(module,env,service,policy,journal,tmp_path,stop_file=flag)
    assert env.reset_count==0 and env.steps_done==0 and policy.clocks==[]
    assert not (tmp_path/'actual_prefix').exists()


def test_stop_created_during_admission_records_not_started_without_writers(tmp_path,monkeypatch):
    module=api();flag=tmp_path/'stop.request';output=tmp_path/'producer'
    case={'id':'preconstruction-stop-fixture','scenario':copy.deepcopy(SCENARIO)}
    bindings={'source':{'fixture':'same'},'inputs':{'fixture':'same'},'git_commit':'fixture-head'}
    monkeypatch.setattr(module,'validate_case',lambda value:copy.deepcopy(value))
    monkeypatch.setattr(module,'load_registry',lambda:{})
    monkeypatch.setattr(module,'_configuration',lambda *a:{'fixture_only':True})
    monkeypatch.setattr(module,'required_hours',lambda case:{1})
    monkeypatch.setattr(module,'_bindings',lambda:copy.deepcopy(bindings))
    monkeypatch.setattr(module,'validate_annual_access',lambda *a:{})
    def freeze(*args,**kwargs):
        flag.touch()
        return {'signature':'fixture-runtime-freeze'}
    monkeypatch.setattr(module,'create_runtime_freeze',freeze)
    for name in ('_build_runtime','SolverRowSidecar','BlockJournal'):
        monkeypatch.setattr(module,name,lambda *a,**k:pytest.fail('stop before construction must prevent writers'))
    result=module.run_case(case,output,approval_receipt={},stop_file=flag)
    assert result['status']=='not_started' and result['actual_episode_attempts']==0
    assert result.get('checkpoint') is None and result['observed_until_minute']==0.
    assert result['error']['type']=='StopRequestedBeforeStart'
    assert flag.exists() and not list(output.glob('solver_rows*'))
    attempts=[json.loads(line) for line in (output/'ATTEMPTS.jsonl').read_text(encoding='utf8').splitlines()]
    assert attempts[-1]['status']=='not_started' and attempts[-1]['stop_file']==str(flag.resolve())


def test_manual_stop_after_ack_forces_checkpoint_and_preserves_queue_without_extra_work(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    flag=tmp_path/'stop.request';snapshots=[];checkpoints=[]
    env.core.ups.energy_kWh=12.;env.core.cold_store.energy_kWh=17.
    def snapshot(actual):
        snapshots.append(actual.core.time)
        return {'decision_time_min':actual.core.time}
    monkeypatch.setattr(module,'_snapshot',snapshot)
    original_step=env.step
    def step(request):
        returned=original_step(request)
        if env.steps_done==2:flag.touch()
        return returned
    monkeypatch.setattr(env,'step',step)
    def checkpoint(**context):
        assert len(raw_blocks(tmp_path))==2
        assert context['env'].core.pending_commands==[{'id':7,'arrival_minute':35.}]
        assert context['env'].core.ups.energy_kWh==12.
        assert context['env'].core.cold_store.energy_kWh==17.
        checkpoints.append(context)
        return {'publication_path':'fake-manual-marker'}
    first=run(module,env,service,policy,journal,tmp_path,stop_file=flag,checkpoint_callback=checkpoint)
    assert first['status']=='suspended' and first['observed_until_minute']==30.
    assert first['controlled_suspension_reason']=='manual_stop_file'
    assert first['stop_file']==str(flag.resolve()) and flag.exists()
    assert first['loop_state']['checkpoint_reason']=='manual_stop_file'
    assert snapshots==policy.clocks==[0.,15.] and len(checkpoints)==1
    assert not (tmp_path/'evidence/SUMMARY.json').exists()
    with pytest.raises(RuntimeError,match='[Ss]top.*before|before.*[Ss]top'):
        run(module,env,service,policy,journal,tmp_path,reset=False,
            loop_state=first['loop_state'],histories=checkpoints[0]['histories'])
    assert env.reset_count==1 and env.steps_done==2 and flag.exists()
    flag.unlink()  # Explicit external orchestration, never a producer action.
    second=run(module,env,service,policy,journal,tmp_path,reset=False,
        loop_state=first['loop_state'],histories=checkpoints[0]['histories'])
    assert second['status']=='completed' and second['controlled_suspension_reason'] is None
    assert env.reset_count==1 and snapshots==policy.clocks==[0.,15.,30.]
    assert second['economics']['initial_preparation_fee_USD2001']==9.
    assert second['economics']['observed_operating_fee_USD2001']==.75


def test_manual_stop_at_final_positive_boundary_remains_explicitly_suspended(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    flag=tmp_path/'stop.request';original_step=env.step;checkpoints=[]
    def step(request):
        returned=original_step(request);flag.touch();return returned
    monkeypatch.setattr(env,'step',step)
    result=module._execute_loop(env,SimpleNamespace(),dict(SCENARIO,duration_steps=1),journal,tmp_path,
        service_history=service,policy_history=policy,history_identity={'case':'fake'},stop_file=flag,
        checkpoint_callback=lambda **kw:checkpoints.append(kw) or {'publication_path':'fake-final-marker'})
    assert result['status']=='suspended' and result['observed_until_minute']==15.
    assert result['unknown_tail_minutes']==0. and len(checkpoints)==1
    assert result['controlled_suspension_reason']=='manual_stop_file'
    assert policy.clocks==[0.] and env.reset_count==1 and env.steps_done==1
    assert flag.exists() and not (tmp_path/'evidence/SUMMARY.json').exists()


def test_cli_registers_stop_file_as_external_orchestration(tmp_path,monkeypatch):
    module=api();captured=[];flag=tmp_path/'stop.request'
    monkeypatch.setattr(module,'_read',lambda path:{'fixture_only':True})
    monkeypatch.setattr(module,'run_case',lambda *a,**kw:captured.append(kw) or {'status':'suspended'})
    assert module.main(['--case-file','fixture.json','--output',str(tmp_path/'producer'),
        '--approval-receipt','approval.json','--stop-file',str(flag)])==0
    assert captured[0]['stop_file']==flag


@pytest.mark.parametrize('minutes',[5.,15.])
def test_registered_restart_assumption_reaches_actual_runtime_configuration(minutes):
    from tools.annual_program.registration import make_case,load_registry
    case=make_case('M_ready',duration_minutes=180.,boiler_restart_minutes=minutes)
    configuration=api()._configuration(case,load_registry())
    assert configuration['model_config']['model_closure_v22']['station']['boiler_restart_minutes']==minutes


def test_constructor_failure_keeps_actual_annual_source_access_receipt():
    exc=RuntimeError('failed after authorized input parsing')
    exc.annual_program_access={'phase':'nonblind_frozen_annual','parsed_hours':[1,2]}
    assert api()._error(exc)['annual_program_access']==exc.annual_program_access


def test_periodic_checkpoint_closes_rotated_gzip_parts_without_extra_decisions(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch)
    checkpoints=[]
    result=run(module,env,service,policy,journal,tmp_path,checkpoint_every_blocks=1,
        checkpoint_callback=lambda **kw:checkpoints.append(kw['loop_state']) or {'publication_path':'fake'})
    assert result['status']=='completed' and len(checkpoints)==2
    parts=result['loop_state']['actual_prefix_parts']
    assert len(parts)==3 and all(row['complete_gzip'] for row in parts)
    actual=[]
    for receipt in parts:
        with gzip.open(tmp_path/receipt['file'],'rt',encoding='utf8') as stream:
            actual.extend(json.loads(line) for line in stream)
    assert [(r['start_minute'],r['end_minute']) for r in actual]==[(0.,15.),(15.,30.),(30.,45.)]
    assert not (tmp_path/'ACTUAL_PREFIX.jsonl').exists()
    assert all(row['checkpoint_reason']=='periodic_checkpoint' for row in checkpoints)
    assert all(row['controlled_suspension_reason'] is None for row in checkpoints)


def external_evidence_fixture(path):
    from tools.annual_readiness.journal import BlockJournal
    from tools.sci_engineering.solver_log import SolverRowSidecar
    writer=SolverRowSidecar(path/'solver_rows_block_000000.jsonl.gz');writer.close()
    receipt=writer.receipt();receipt['durable_and_stream_verified_before_block_ack']=True
    domain=path/'domain_evidence';domain.mkdir()
    blob=domain/'actual-typed-rows.json.gz';blob.write_bytes(gzip.compress(b'[]',mtime=0))
    journal=BlockJournal(path/'evidence',identity={'case_id':'pure-external-evidence-fixture'})
    journal.append({'start_minute':0.,'end_minute':15.,'segments':[segment(0.,15.,1)],
        'solver_calls':[],'events':[],'decision':{},'terminal':{},
        'solver_row_sidecar':receipt,'domain_evidence_files':{
            'domain_evidence/actual-typed-rows.json.gz':{'gzip_sha256':api()._sha(blob),
                'gzip_bytes':blob.stat().st_size,'durable_and_verified_before_block_ack':True}}})
    return blob


def test_resume_verifies_native_row_and_domain_witness_bytes(tmp_path):
    blob=external_evidence_fixture(tmp_path)
    assert api()._verify_external_evidence(tmp_path)['verified_domain_files']==1
    with blob.open('ab') as stream:stream.write(b'changed-after-checkpoint')
    with pytest.raises(ValueError,match='domain|witness|SHA|changed'):
        api()._verify_external_evidence(tmp_path)


def test_resume_rejects_new_orphan_solver_sidecar(tmp_path):
    external_evidence_fixture(tmp_path)
    (tmp_path/'solver_rows_block_000001.jsonl.gz').write_bytes(b'unacknowledged')
    with pytest.raises(ValueError,match='orphan|extra|Unacknowledged'):
        api()._verify_external_evidence(tmp_path)


def known_domain_fixture(path):
    from tools.annual_readiness.journal import BlockJournal
    from tools.sci_engineering.solver_log import SolverRowSidecar
    from tools.model_correctness_v23.evidence import DomainEvidenceStore
    module=api();store=DomainEvidenceStore(path/'domain_evidence',enable_all_validation_rows=True)
    referenced=store.store([{'name':'referenced_native_row_fixture','rhs':1.}])
    probe=store.store([{'name':'known_probe_without_block_reference_fixture','rhs':2.}])
    writer=SolverRowSidecar(path/'solver_rows_block_000000.jsonl.gz');writer.close()
    receipt=writer.receipt();receipt['durable_and_stream_verified_before_block_ack']=True
    first=path/referenced['directory']/referenced['file']
    journal=BlockJournal(path/'evidence',identity={'case_id':'pure-known-domain-fixture'})
    journal.append({'start_minute':0.,'end_minute':15.,'segments':[segment(0.,15.,1)],
        'solver_calls':[],'events':[],'decision':{},'terminal':{},'solver_row_sidecar':receipt,
        'domain_evidence_files':{first.relative_to(path).as_posix():{
            'gzip_sha256':module._sha(first),'gzip_bytes':first.stat().st_size,
            'rows_sha256':referenced['rows_sha256'],'row_count':referenced['row_count'],
            'durable_and_verified_before_block_ack':True}}})
    return store,path/probe['directory']/probe['file']


def test_known_unreferenced_domain_blob_requires_bound_complete_manifest(tmp_path):
    store,_=known_domain_fixture(tmp_path)
    with pytest.raises(ValueError,match='orphan'):
        api()._verify_external_evidence(tmp_path)
    manifest=api()._domain_manifest(tmp_path,store._known)
    verified=api()._verify_external_evidence(tmp_path,manifest=manifest,domain_known=store._known)
    assert verified['verified_domain_files']==2 and verified['checkpoint_only_domain_files']==1
    assert len(manifest['files'])==len(store._known)==2


def test_complete_domain_manifest_does_not_admit_truly_unregistered_extra(tmp_path):
    store,_=known_domain_fixture(tmp_path)
    manifest=api()._domain_manifest(tmp_path,store._known)
    (tmp_path/'domain_evidence/unregistered.json.gz').write_bytes(gzip.compress(b'[]',mtime=0))
    with pytest.raises(ValueError,match='orphan|extra|Unacknowledged'):
        api()._verify_external_evidence(tmp_path,manifest=manifest,domain_known=store._known)


def test_known_unreferenced_raw_gzip_header_tampering_fails_even_if_rows_unchanged(tmp_path):
    store,probe=known_domain_fixture(tmp_path)
    manifest=api()._domain_manifest(tmp_path,store._known)
    original=gzip.decompress(probe.read_bytes());changed=bytearray(probe.read_bytes());changed[4]^=1
    probe.write_bytes(changed)
    assert gzip.decompress(probe.read_bytes())==original
    with pytest.raises(ValueError,match='SHA|bytes|changed'):
        api()._verify_external_evidence(tmp_path,manifest=manifest,domain_known=store._known)


def test_domain_manifest_checks_native_canonical_rows_and_complete_known_catalog(tmp_path):
    store,_=known_domain_fixture(tmp_path)
    wrong=copy.deepcopy(store._known);next(iter(wrong.values()))['row_count']+=1
    with pytest.raises(ValueError,match='rows|count|canonical'):
        api()._domain_manifest(tmp_path,wrong)
    manifest=api()._domain_manifest(tmp_path,store._known)
    missing=copy.deepcopy(store._known);missing.pop(next(iter(missing)))
    with pytest.raises(ValueError,match='catalog|known|manifest'):
        api()._verify_external_evidence(tmp_path,manifest=manifest,domain_known=missing)


@pytest.mark.parametrize('stop_mode',['registered_blocks','manual_file'])
@pytest.mark.parametrize('frequency',[96,35041])
def test_public_driver_checkpoint_roundtrip_uses_no_reset_on_resume_and_closes_histories(tmp_path,monkeypatch,stop_mode,frequency):
    """Exercise real journal/checkpoint/disk protocol, with no physical plant."""
    module=api();order=[];runtimes=[];opened=[];flag=tmp_path/'stop.request'
    if stop_mode=='manual_file':
        original_step=FakeEnv.step
        def step(env,request):
            returned=original_step(env,request)
            if env.steps_done==2:flag.touch()
            return returned
        monkeypatch.setattr(FakeEnv,'step',step)
    case={'id':'pure-driver-fixture','scenario':copy.deepcopy(SCENARIO),
        'configuration':'H2','annual_readiness':{'method':'M_ready','fixture_only':True},
        'annual_program':{'fixture_only':True}}
    configuration={'model_config':{'fixture':True},'policy':{'alias':'M','config':{}}}
    bindings={'source':{'fixture.py':'fixture-source-only'},'inputs':{'fixture.csv':'fixture-input-only'},'git_commit':'fixture-head'}
    # New annual storage uses the unchanged native writer through its exact
    # source pin, even in this no-plant protocol fixture.
    bindings['source']['tools/model_correctness_v23/evidence.py']=module._sha(ROOT/'tools/model_correctness_v23/evidence.py')
    monkeypatch.setattr(module,'validate_case',lambda value:copy.deepcopy(value))
    monkeypatch.setattr(module,'load_registry',lambda:{})
    monkeypatch.setattr(module,'_configuration',lambda case,registry:copy.deepcopy(configuration))
    monkeypatch.setattr(module,'required_hours',lambda case:{1})
    monkeypatch.setattr(module,'_bindings',lambda:copy.deepcopy(bindings))
    monkeypatch.setattr(module,'create_runtime_freeze',lambda *a,**k:order.append('freeze') or {'signature':'fixture-runtime-freeze'})
    monkeypatch.setattr(module,'validate_annual_access',lambda *a:order.append('access-check') or {})
    monkeypatch.setattr(module,'ExactPolicyHistory',FakePolicyHistory)
    monkeypatch.setattr(module,'_activate_restored_inputs',lambda env:order.append('deterministic-input-rebuild'))
    monkeypatch.setattr(module,'_snapshot',lambda env:{'decision_time_min':env.core.time})
    monkeypatch.setattr(module,'_terminal',lambda env,end:{'snapshot_time_minute':end})
    monkeypatch.setattr(module,'_block_evaluation',lambda segments,start,end,**kw:{'economics':{
        'observed_operating_fee_USD2001':(end-start)/60.,'initial_preparation_fee_USD2001':sum(kw['preparation'].values())}})
    from tools.model_review_v30 import evaluation
    monkeypatch.setattr(evaluation,'_load_frozen_accounting',lambda *a:({}, {}, {'fixture':'no-physical-input-read'}))
    original_attach=module._attach_histories
    def attach(*args):
        result=original_attach(*args);opened.extend(result.values());return result
    monkeypatch.setattr(module,'_attach_histories',attach)
    def build(case,config,context,output,writer):
        from tools.sci_engineering.solver_log import SolverCallLog
        from tools.model_correctness_v23.evidence import DomainEvidenceStore
        assert order[0]=='freeze'
        order.append('construct-fake')
        env=FakeEnv();env.engineering_core_type=FakeCore
        env._solver_row_sidecar=writer;env.core.solver_calls=SolverCallLog(writer=writer)
        env.domain_evidence=DomainEvidenceStore(output/'domain_evidence',enable_all_validation_rows=True)
        env.sci_config={};env.operating_model_config={'fixture_only':True};env.control_rule={}
        env.input_access_receipt={'fixture_only':True}
        runtimes.append(env)
        return env,FakeController()
    monkeypatch.setattr(module,'_build_runtime',build)
    output=tmp_path/'producer'
    first=module.run_case(case,output,approval_receipt={'fixture_only':True},
        stop_after_blocks=2 if stop_mode=='registered_blocks' else None,
        stop_file=flag if stop_mode=='manual_file' else None,
        checkpoint_every_blocks=frequency)
    assert first['status']=='suspended',first.get('error')
    assert first['solver_row_sidecars']==[block['solver_row_sidecar'] for block in raw_blocks(output)]
    assert len(first['solver_row_sidecars'])==2
    assert runtimes[0].reset_count==1
    assert all(history._closed for history in opened)
    marker=first['checkpoint']['publication_path']
    published=module._read(marker)
    saved=module._decode(module._read(published['checkpoint_path'])['runner_state'])
    # The earlier protocol copied the complete known map and full raw manifest
    # into every snapshot. Its separate legacy-manifest regression tests remain.
    # New indexed snapshots bind both exact streams without that year-sized copy.
    assert 'domain_known' not in saved and 'domain_manifest' not in saved
    indexed=saved['domain_store']
    assert indexed['committed_count']==indexed['catalogue_state']['count']==1
    assert indexed['proof_history_state']['prefix_count']==1
    assert indexed['proof_history_state']['global_count']==1
    assert indexed['catalogue_state']['raw_blob_proof_included'] is False
    expected_stop=str(flag.resolve()) if stop_mode=='manual_file' else None
    frozen=module._read(output/'ORCHESTRATION_FREEZE.json')
    assert frozen['stop_file']==first['stop_file']==saved['stop_file']==expected_stop
    assert frozen['checkpoint_every_blocks']==saved['checkpoint_every_blocks']==frequency
    started=json.loads((output/'ATTEMPTS.jsonl').read_text(encoding='utf8').splitlines()[0])
    assert started['stop_file']==expected_stop
    expected_reason='manual_stop_file' if stop_mode=='manual_file' else 'registered_block_limit'
    assert first['controlled_suspension_reason']==expected_reason
    with pytest.raises(ValueError,match='stop.file|orchestration|registration'):
        module.run_case(case,output,approval_receipt={'fixture_only':True},resume_checkpoint=marker,
            stop_file=tmp_path/'different-stop.request',checkpoint_every_blocks=frequency)
    assert len(runtimes)==1
    if stop_mode=='manual_file':
        with pytest.raises(RuntimeError,match='[Ss]top.*before|before.*[Ss]top'):
            # Hold the newly frozen frequency equal while testing the original
            # preexisting-stop guard; the assertion/guard remains unchanged.
            module.run_case(case,output,approval_receipt={'fixture_only':True},resume_checkpoint=marker,
                checkpoint_every_blocks=frequency)
        assert len(runtimes)==1 and flag.exists()
        flag.unlink()
    before_attempts=(output/'ATTEMPTS.jsonl').read_bytes()
    with pytest.raises(ValueError,match='checkpoint|orchestration'):
        module.run_case(case,output,approval_receipt={'fixture_only':True},resume_checkpoint=marker,
            checkpoint_every_blocks=35041 if frequency==96 else 96)
    assert len(runtimes)==1
    assert (output/'ATTEMPTS.jsonl').read_bytes()==before_attempts
    second=module.run_case(case,output,approval_receipt={'fixture_only':True},resume_checkpoint=marker,
        checkpoint_every_blocks=frequency)
    assert second['status']=='completed',second.get('error')
    assert runtimes[1].reset_count==0
    assert second['reset_count']==1 and second['actual_episode_attempts']==0
    assert first['invocation']==0 and second['invocation']==1
    assert second['economics']['initial_preparation_fee_USD2001']==9.
    assert second['solver_row_sidecars']==[block['solver_row_sidecar'] for block in raw_blocks(output)]
    assert len(second['solver_row_sidecars'])==3
    assert second['configuration']=='H2' and second['annual_readiness']==case['annual_readiness']
    assert second['annual_program']==case['annual_program']
    assert second['resolved_model_config']=={'fixture_only':True}
    assert [row['start_minute'] for b in raw_blocks(output) for row in b['segments']]==[0.,15.,30.]
    assert (output/'RESULT.json').is_file()


def test_incremental_recovery_period_is_post_ack_without_extra_decisions(tmp_path,monkeypatch):
    module,env,service,policy,journal=setup(tmp_path,monkeypatch);saved=[]
    def capture(**context):
        assert len(raw_blocks(tmp_path))==2
        assert context['env'].core.time==30.
        saved.append(context['loop_state'])
        return {'publication_path':'immutable-recovery-COMMIT'}
    result=run(module,env,service,policy,journal,tmp_path,checkpoint_every_blocks=35041,
        recovery_every_blocks=2,checkpoint_callback=capture)
    assert result['status']=='completed' and len(saved)==1
    assert saved[0]['checkpoint_reason']=='periodic_recovery_snapshot'
    assert env.reset_count==1 and policy.clocks==[0.,15.,30.]


def test_incremental_recovery_orchestration_is_explicit_and_frozen(tmp_path):
    module=api();vault=tmp_path/'vault'
    old=module._orchestration_freeze(None,35041,'engineering_v1')
    new=module._orchestration_freeze(None,35041,'engineering_v1',
        recovery_every_blocks=4,recovery_vault=vault)
    assert old['schema'].endswith('.v3') and 'recovery_vault' not in old
    assert new['schema'].endswith('.v4') and new['recovery_every_blocks']==4
    assert new['recovery_vault']==str(vault.resolve()) and new['signature']!=old['signature']
    with pytest.raises(ValueError):module._orchestration_freeze(None,35041,'engineering_v1',recovery_every_blocks=4)
def test_accepted_result_cache_is_explicit_and_bound_in_orchestration():
    module=api()
    disabled=module._orchestration_freeze(None,96,'engineering_v1')
    enabled=module._orchestration_freeze(None,96,'engineering_v1',accepted_result_cache=True)
    assert 'accepted_result_cache' not in disabled
    # The measured cache budget now admits 2048 entries under the same 32 MiB
    # bound; this changes execution storage, not any plant or service tolerance.
    assert enabled['accepted_result_cache']==dict(mode='exact_verified_problem',max_entries=2048,max_bytes=33554432)
    assert enabled['signature']!=disabled['signature']


@pytest.mark.parametrize('value',[None,1,'yes'])
def test_accepted_result_cache_rejects_nonboolean_orchestration(value):
    with pytest.raises(ValueError,match='boolean'):
        api()._orchestration_freeze(None,96,'engineering_v1',accepted_result_cache=value)
