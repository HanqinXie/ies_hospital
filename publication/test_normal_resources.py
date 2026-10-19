"""Publication-only normal supervision guards; no scientific episode runs."""
import copy
from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from publication import run_normal_serial as normal


def test_resource_sample_journal_preserves_observation_timestamp(tmp_path, monkeypatch):
    from types import SimpleNamespace
    observed = '2026-10-19T09:44:39.000000+00:00'
    monkeypatch.setattr(normal, 'windows_memory', lambda: dict(utc=observed,
        available_physical_bytes=30*normal.GIB, commit_headroom_bytes=30*normal.GIB))
    info = SimpleNamespace(rss=2*normal.GIB, private=3*normal.GIB, peak_wset=2*normal.GIB)
    fake_psutil = SimpleNamespace(Process=lambda pid: SimpleNamespace(memory_info=lambda: info), Error=RuntimeError)
    sample = normal._process_sample(SimpleNamespace(pid=17), fake_psutil)
    sample['batch'] = 'v20_normal_009'
    path = tmp_path / 'SAMPLES.jsonl'
    normal.journal(path, **sample)
    row = json.loads(path.read_text(encoding='utf8'))
    assert row == sample
    assert row['utc'] == observed and row['process_sample_status'] == 'measured'


def test_journal_adds_timestamp_for_events_without_one(tmp_path):
    path = tmp_path / 'EVENTS.jsonl'
    normal.journal(path, event='admission_checkpoint', batch='v20_normal_000')
    row = json.loads(path.read_text(encoding='utf8'))
    assert row['utc'] and row['event'] == 'admission_checkpoint'


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf8')


def registration(tmp_path):
    scene=dict(id='normal_scene',duration_steps=96,step_minutes=15.,start_hour=1129,source_day_index=47,
               initialization_seed=20261018,full_source_support={'raw_start_minute':67680.,'raw_end_minute':69120.},
               physical_scenario_signature='physical',normal_duration_hours=24.,faults={},shock={},exogenous_events=[])
    spec=dict(policy_id='E_M20',display_alias='E',kind='engineering',config={})
    freeze=dict(model_id='M20_load_accounting',source_sha256={'source.py':'a'*64},model_config={},
                runtime_inputs=[{'path':'input.csv','sha256':'b'*64,'bytes':1}],policies={'E':spec})
    freeze['signature']=normal.digest(freeze)
    manifest=dict(execution_model_id='M20_load_accounting',source_sha256=freeze['source_sha256'],model_config={},
        model_config_signature=normal.digest({}),files=freeze['runtime_inputs'],scenarios=[scene],
        policies=[spec['policy_id']],policy_specs_sha256=normal.digest([spec]),freeze_signature=freeze['signature'],
        source_freeze_artifact={'sha256':'f'*64},evaluation_role='seen_regression')
    manifest['signature']=normal.digest(manifest)
    directory=tmp_path/'experiments/v20_normal_000';write(directory/'manifest.json',manifest)
    plan=dict(model_id='M20_load_accounting',source_sha256=freeze['source_sha256'],model_config={},policy_specs=[spec],
        scenario_ids=[scene['id']],phase='normal',registered_pairs=1,manifest_sha256=normal.sha(directory/'manifest.json'),
        manifest_signature=manifest['signature'])
    write(directory/'plan.json',plan)
    job=dict(name='v20_normal_000',cohort='normal',policies=['E'],source_proposal='NORMAL_E_FINAL_R1_PROPOSAL.json',scene=scene)
    return directory,job,plan,manifest,freeze


def test_commit_shortage_blocks_even_when_physical_memory_is_plentiful():
    profile=dict(OS_lifetime_peak_working_set_bytes=10*normal.GIB,sampled_private_peak_bytes=12*normal.GIB,
                 source_model_id='M19_model_closeout')
    decision=normal.resource_admission(profile,dict(available_physical_bytes=40*normal.GIB,commit_headroom_bytes=5*normal.GIB))
    assert decision['admitted'] is False
    assert decision['physical_ok'] is True and decision['commit_ok'] is False
    assert decision['required_physical_bytes']==18*normal.GIB
    assert decision['required_commit_headroom_bytes']>=20.4*normal.GIB


def test_prior_partial_attempt_cannot_be_overwritten(tmp_path):
    directory,job,plan,manifest,freeze=registration(tmp_path)
    (directory/'ATTEMPTS.jsonl').write_text('{"event":"started"}\n',encoding='utf8')
    with pytest.raises(ValueError,match='partial'):
        normal.existing_state(directory,job,freeze,'f'*64)


def test_registered_scene_change_is_rejected_even_after_rehash(tmp_path):
    directory,job,plan,manifest,freeze=registration(tmp_path)
    manifest['scenarios']=copy.deepcopy(manifest['scenarios']);manifest['scenarios'][0]['duration_steps']+=1
    manifest.pop('signature');manifest['signature']=normal.digest(manifest)
    write(directory/'manifest.json',manifest)
    plan['manifest_sha256']=normal.sha(directory/'manifest.json');plan['manifest_signature']=manifest['signature']
    write(directory/'plan.json',plan)
    with pytest.raises(ValueError,match='specification'):
        normal.existing_state(directory,job,freeze,'f'*64)


def test_active_scientific_child_blocks_dispatch_without_failure():
    decision=normal.dispatch_decision(comparisons_qualified=True,active_science=[{'pid':19}],enumeration_issues=[],
        admission={'admitted':True})
    assert decision['start'] is False and decision['state']=='waiting_other_scientific_child'


def test_fake_terminal_does_not_skip_actual_evidence(tmp_path):
    directory,job,plan,manifest,freeze=registration(tmp_path)
    write(directory/'BATCH_RESULT.json',{'registered_pairs':1,'actual_attempts':1,'complete_full':1,'unknown_full':0})
    with pytest.raises(ValueError,match='terminal'):
        normal.existing_state(directory,job,freeze,'f'*64)


def test_unobserved_resource_profile_cannot_authorize_run():
    with pytest.raises(ValueError):
        normal.resource_admission({'sampled_private_peak_bytes':1},dict(available_physical_bytes=10**12,commit_headroom_bytes=10**12))


def test_low_resource_cases_can_proceed_before_waiting_large_case():
    jobs=[dict(name='large',profile={'sampled_private_peak_bytes':20*normal.GIB,'OS_lifetime_peak_working_set_bytes':18*normal.GIB,'source_model_id':'M19_model_closeout'}),
          dict(name='small',profile={'sampled_private_peak_bytes':3*normal.GIB,'OS_lifetime_peak_working_set_bytes':2*normal.GIB,'source_model_id':'M19_model_closeout'})]
    chosen,decisions=normal.select_admitted_job(jobs,dict(available_physical_bytes=15*normal.GIB,commit_headroom_bytes=15*normal.GIB))
    assert chosen['name']=='small'
    assert decisions['large']['admitted'] is False


def test_probe_enumeration_unknown_is_not_permission_to_start():
    decision=normal.dispatch_decision(comparisons_qualified=True,active_science=[],enumeration_issues=[{'pid':1,'error':'denied'}],
                                      admission={'admitted':True})
    assert decision['start'] is False and decision['state']=='waiting_process_enumeration_unknown'


def complete_evidence(directory,job,plan,manifest,freeze):
    scene=manifest['scenarios'][0];spec=plan['policy_specs'][0];end=scene['duration_steps']*scene['step_minutes']
    service={p:{c:dict(requested_kWh=1.,delivered_kWh=1.,ENS_kWh=0.,disconnected_ENS_kWh=0.,connected_unserved_ENS_kWh=0.)
                  for c in ('e','h','c')} for p in ('P0','P1','P2','P3')}
    window=dict(complete=True,window_minutes=[0.,end],unknown_tail_minutes=None,all_registered={'carrier_service':service})
    ledger=dict(complete=True,window_minutes=[0.,end],unknown_tail_minutes=None,all_registered=service,observed_finish_minute=end)
    record=dict(status='completed',error=None,analysis_error=None,scenario_id=scene['id'],policy_id=spec['policy_id'],
        model_id=normal.MODEL,manifest_signature=manifest['signature'],model_config_signature=manifest['model_config_signature'],raw_sha256='c'*64,
        analysis=dict(execution_model_id=normal.MODEL,observed_until_minute=end,v18={'windows':{'registered_full':window}},
            v20={'episode_audit':{'status':'passed','issues':[]},'service_ledgers':{'registered_full':ledger}}))
    path=directory/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json';write(path,record)
    started=dict(event='started',scenario_id=scene['id'],policy_id=spec['policy_id'],model_id=normal.MODEL)
    finished=dict(event='finished',scenario_id=scene['id'],policy_id=spec['policy_id'],status='completed',analysis_status='saved',
                  raw_sha256=record['raw_sha256'],result_sha256=normal.sha(path))
    (directory/'ATTEMPTS.jsonl').write_text(json.dumps(started)+'\n'+json.dumps(finished)+'\n',encoding='utf8')
    terminal=dict(registered_pairs=1,actual_attempts=1,complete_full=1,unknown_full=0,analysis_failure_or_unavailable_count=0,
        status_counts={'completed':1},source_unchanged=True,plan_sha256=normal.sha(directory/'plan.json'),manifest_sha256=normal.sha(directory/'manifest.json'),
        matching_evidence={scene['id']:dict(same_initial_state=True,same_exogenous_definition=True,initial_state_sha256=['d'*64],
            applied_exogenous_definition_sha256=[normal.stream_hash(scene)],injection_state_sha256=[None],injection_state_coverage_known=False)})
    write(directory/'BATCH_RESULT.json',terminal)
    return record,path


def test_real_normal_without_fault_injection_can_be_strictly_skipped(tmp_path):
    directory,job,plan,manifest,freeze=registration(tmp_path)
    complete_evidence(directory,job,plan,manifest,freeze)
    assert normal.existing_state(directory,job,freeze,'f'*64)['state']=='skip_strictly_qualified_complete'


def test_last_moment_commit_drop_defers_without_starting_or_failing(tmp_path,monkeypatch):
    monkeypatch.setattr(normal,'existing_state',lambda *a,**k:{'state':'not_started'})
    monkeypatch.setattr(normal,'active_scientific_children',lambda:([],[]))
    monkeypatch.setattr(normal,'windows_memory',lambda:dict(available_physical_bytes=100*normal.GIB,commit_headroom_bytes=0))
    def forbidden(*args,**kwargs):raise AssertionError('Physics must not start after lost resource admission')
    monkeypatch.setattr(normal.subprocess,'Popen',forbidden)
    job=dict(name='v20_normal_000',profile=dict(source_model_id='M19_model_closeout',
        OS_lifetime_peak_working_set_bytes=normal.GIB,sampled_private_peak_bytes=2*normal.GIB))
    result=normal.run_one(job,tmp_path,{},'f'*64,{'admitted':True},2.)
    assert result['not_started'] is True
    assert result['state']=='waiting_physical_or_commit_resources'
    assert not list(tmp_path.glob('*.command.json'))


def test_rehashed_invented_historical_profile_is_not_observed_evidence(tmp_path,monkeypatch):
    path=tmp_path/'profile.json';monkeypatch.setattr(normal,'PROFILE_PATH',path)
    profile=dict(source_model_id='M19_model_closeout',is_M20_measurement=False,growth_factor=1.2,
        host_reserve_bytes=6*normal.GIB,profiles=[{'sampled_private_peak_bytes':1} for _ in range(12)])
    profile['signature']=normal.digest(profile);write(path,profile)
    with pytest.raises(ValueError,match='Historical'):
        normal.load_profile()


def test_late_other_science_child_is_waiting_not_failure_or_permission(tmp_path,monkeypatch):
    monkeypatch.setattr(normal,'existing_state',lambda *a,**k:{'state':'not_started'})
    monkeypatch.setattr(normal,'active_scientific_children',lambda:([{'pid':9}],[]))
    def forbidden(*args,**kwargs):raise AssertionError('A second scientific process must not start')
    monkeypatch.setattr(normal.subprocess,'Popen',forbidden)
    result=normal.run_one({'name':'v20_normal_000'},tmp_path,{},'f'*64,{'admitted':True},2.)
    assert result['not_started'] is True
    assert result['state']=='waiting_other_scientific_child'


class FakeScientificProcess:
    """Only a fake object: these tests never invoke an executable."""
    pid=987654
    returncode=None
    def __init__(self):self.wait_calls=0
    def poll(self):return self.returncode
    def wait(self):
        self.wait_calls+=1;self.returncode=0;return 0


def fake_launch_ready(monkeypatch):
    process=FakeScientificProcess()
    monkeypatch.setattr(normal,'existing_state',lambda *a,**k:{'state':'not_started'})
    monkeypatch.setattr(normal,'active_scientific_children',lambda:([],[]))
    monkeypatch.setattr(normal,'windows_memory',lambda:dict(available_physical_bytes=100*normal.GIB,commit_headroom_bytes=100*normal.GIB))
    monkeypatch.setattr(normal.subprocess,'Popen',lambda *a,**k:process)
    job=dict(name='v20_normal_000',policies=['E'],profile=dict(source_model_id='M19_model_closeout',duration_minutes=1440,
        OS_lifetime_peak_working_set_bytes=normal.GIB,sampled_private_peak_bytes=2*normal.GIB))
    return process,job


def test_successful_fake_child_sampling_uses_the_real_journal_writer(tmp_path, monkeypatch):
    process, job = fake_launch_ready(monkeypatch)
    polls = []
    def poll():
        polls.append(True)
        return None if len(polls) == 1 else 0
    process.poll = poll
    stamp = '2026-10-19T09:44:39.000000+00:00'
    sample = dict(utc=stamp, pid=process.pid, available_physical_bytes=30*normal.GIB,
                  commit_headroom_bytes=30*normal.GIB, private_bytes=3*normal.GIB,
                  working_set_bytes=2*normal.GIB, OS_lifetime_peak_working_set_bytes=2*normal.GIB,
                  process_sample_status='measured')
    monkeypatch.setattr(normal, '_process_sample', lambda *a: dict(sample))
    monkeypatch.setattr(normal.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(normal, 'qualify_complete', lambda *a: {'qualified': True})
    result = normal.run_one(job, tmp_path, {}, 'f'*64, {'admitted': True}, 2.)
    assert result['passed'] is True and process.wait_calls == 1
    assert result['resource_samples'] == 1 and result['sampled_private_peak_bytes'] == 3*normal.GIB
    saved = json.loads((tmp_path / 'SAMPLES.jsonl').read_text(encoding='utf8'))
    assert saved['utc'] == stamp and saved['batch'] == job['name']


def test_start_journal_failure_still_drains_successfully_launched_child(tmp_path,monkeypatch):
    process,job=fake_launch_ready(monkeypatch)
    def broken_journal(*a,**k):raise OSError('simulated first journal failure')
    monkeypatch.setattr(normal,'journal',broken_journal)
    with pytest.raises(OSError,match='first journal'):
        normal.run_one(job,tmp_path,{},'f'*64,{'admitted':True},2.)
    assert process.wait_calls==1
    receipt=normal.read(tmp_path/(job['name']+'.interrupted.json'))
    assert receipt['successful_Popen'] is True and receipt['owned_child_drained'] is True
    assert receipt['exit_code']==0 and receipt['error_type']=='OSError'


def test_exception_journal_failure_cannot_skip_drain_or_replace_original_error(tmp_path,monkeypatch):
    process,job=fake_launch_ready(monkeypatch)
    def sample_error(*a,**k):raise RuntimeError('simulated sample failure')
    def journal_error(path,**value):
        if value['event']!='scientific_child_started':raise OSError('simulated interruption journal failure')
    monkeypatch.setattr(normal,'_process_sample',sample_error)
    monkeypatch.setattr(normal,'journal',journal_error)
    with pytest.raises(RuntimeError,match='sample failure'):
        normal.run_one(job,tmp_path,{},'f'*64,{'admitted':True},2.)
    assert process.wait_calls==1
    receipt=normal.read(tmp_path/(job['name']+'.interrupted.json'))
    assert receipt['owned_child_drained'] is True and receipt['diagnostic_write_errors'][0]['error_type']=='OSError'


def test_missing_start_journal_never_reports_zero_launches_or_not_started(tmp_path,monkeypatch):
    process,job=fake_launch_ready(monkeypatch)
    monkeypatch.setattr(normal,'PROFILE_DIR',tmp_path)
    monkeypatch.setattr(normal,'PROFILE_PATH',tmp_path/'profile.json');write(normal.PROFILE_PATH,{})
    monkeypatch.setattr(normal,'load_profile',lambda:{})
    monkeypatch.setattr(normal,'verify_frozen_project',lambda:({},'f'*64))
    monkeypatch.setattr(normal,'normal_jobs',lambda profile:[job])
    monkeypatch.setattr(normal,'comparison_gate',lambda:dict(qualified=True,failed=[]))
    monkeypatch.setattr(normal,'serial_lease',nullcontext)
    original_journal=normal.journal
    def fail_start_journal(path,**value):
        if value['event']=='scientific_child_started':raise OSError('simulated start event failure')
        original_journal(path,**value)
    monkeypatch.setattr(normal,'journal',fail_start_journal)
    # run=True exercises supervision only; every Popen is a fake above.
    assert normal.supervise('FAKE_START_IO',run=True)==2
    summary=normal.read(tmp_path/'supervision_FAKE_START_IO/SUMMARY.json')
    assert process.wait_calls==1
    assert summary['actual_scientific_children_started']==1
    assert summary['journal_scientific_children_started']==0
    assert summary['pending_not_started']==[]
    assert summary['started_without_completed_supervisor_result']==['v20_normal_000']
