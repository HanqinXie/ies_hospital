"""Continuation guards; mocked children never dispatch physical models."""
import ast
import importlib.util
from pathlib import Path
import sys
import pytest

PATH=Path(__file__).with_name('continue_normal008.py')

def module():
    assert PATH.is_file(), 'The lightweight continuation and pre405 qualifier are required'
    spec=importlib.util.spec_from_file_location('_continue008_test',PATH)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

def test_persistent_parent_has_no_heavy_project_imports():
    m=module();tree=ast.parse(PATH.read_text(encoding='utf8'))
    forbidden=('tools','scripts','hospital_ies','numpy','pandas','scipy','torch')
    for n in tree.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            names=[a.name for a in n.names] if isinstance(n,ast.Import) else [n.module or '']
            assert not any(name.startswith(forbidden) for name in names)
    assert m.TARGET=='v20_normal_008'

@pytest.mark.parametrize('artifact',['ATTEMPTS.jsonl','pairs','BATCH_RESULT.json'])
def test_partial_or_terminal_008_is_never_overwritten(tmp_path,artifact):
    m=module();p=tmp_path/artifact
    if artifact=='pairs':p.mkdir()
    else:p.write_text('{}',encoding='utf8')
    with pytest.raises(ValueError,match='008.*started|008.*partial|008.*terminal'):
        m.require_unstarted(tmp_path)

def test_untouched_registration_is_not_started(tmp_path):
    module().require_unstarted(tmp_path)

def test_wrong_canonical_remaining_pair_is_not_accepted():
    m=module()
    jobs=[dict(name='v20_normal_008',policies=['C19'],cohort='normal')]
    m.validate_pending_target(jobs[0])
    for bad in [dict(jobs[0],name='v20_normal_007'),dict(jobs[0],policies=['E']),dict(jobs[0],cohort='long')]:
        with pytest.raises(ValueError):m.validate_pending_target(bad)

def test_active_science_or_unknown_enumeration_refuses_qualification_start():
    m=module()
    with pytest.raises(ValueError):m.require_no_science([{'pid':1}],[])
    with pytest.raises(ValueError):m.require_no_science([],[{'pid':1,'error':'unknown'}])
    m.require_no_science([],[])

def test_qualifier_calls_existing_readonly_release_gate_interfaces():
    gate_path=PATH.with_name('release_gate.py')
    tree=ast.parse(gate_path.read_text(encoding='utf8'))
    public={n.name for n in tree.body if isinstance(n,ast.FunctionDef)}
    qualifier=next(n for n in ast.parse(PATH.read_text(encoding='utf8')).body if isinstance(n,ast.FunctionDef) and n.name=='qualify405')
    used={n.func.attr for n in ast.walk(qualifier) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='gate'}
    assert used and used<=public

def profile():
    return dict(source_model_id='M19_model_closeout',is_M20_measurement=False,alias='C19',
        duration_minutes=4320.,original_batch='normal_C19_final_r1_002',
        OS_lifetime_peak_working_set_bytes=24352612352,sampled_private_peak_bytes=26497581056)

def samples(m,commit=40,physical=40,seconds=32):
    return [dict(monotonic=float(t),available_physical_bytes=int(physical*m.GIB),commit_headroom_bytes=int(commit*m.GIB)) for t in range(0,seconds+1,2)]

def test_resource_budget_uses_exact_same72_observations_and_two_counters():
    m=module();decision=m.resource_admission(profile(),samples(m)[0],2)
    assert decision['admitted'] and decision['growth_factor']==1.1 and decision['host_reserve_bytes']==2*m.GIB
    assert decision['required_commit_headroom_bytes']==__import__('math').ceil(1.1*26497581056)+2*m.GIB
    assert not m.resource_admission(profile(),dict(available_physical_bytes=40*m.GIB,commit_headroom_bytes=1),2)['admitted']
    assert not m.resource_admission(profile(),dict(available_physical_bytes=None,commit_headroom_bytes=40*m.GIB),2)['admitted']

@pytest.mark.parametrize('mutation',['too_short','gap','unknown','low_then_recovered','nonfinite','nonmonotonic'])
def test_stable_window_cannot_average_or_skip_unknown_pressure(mutation):
    m=module();rows=samples(m)
    if mutation=='too_short':rows=rows[:10]
    elif mutation=='gap':rows[6]['monotonic']+=1.5
    elif mutation=='unknown':rows[5]['commit_headroom_bytes']=None
    elif mutation=='low_then_recovered':rows[5]['commit_headroom_bytes']=1
    elif mutation=='nonfinite':rows[5]['commit_headroom_bytes']=float('nan')
    elif mutation=='nonmonotonic':rows[5]['monotonic']=rows[4]['monotonic']
    assert m.stable_admission(rows,profile(),30,2)['admitted'] is False

def test_stable_window_prefers_threeGiB_only_when_every_observation_supports_it():
    m=module();r=m.stable_admission(samples(m),profile(),30,2)
    assert r['admitted'] and r['selected_reserve_gib']==3
    r=m.stable_admission(samples(m,commit=29.5),profile(),30,2)
    assert r['admitted'] and r['selected_reserve_gib']==2

def test_real_sampling_jitter_can_complete_window_without_assuming_exact_clocks():
    m=module();rows=samples(m)
    for i,r in enumerate(rows):r['monotonic']+=i*.001
    result=m.stable_admission(rows,profile(),30,2)
    assert result['admitted'] and result['stable_observed_seconds']>=30

def test_admission_rechecks_partial_and_other_child_after_stable_window(tmp_path,monkeypatch):
    m=module();monkeypatch.setattr(m,'active_science',lambda:([dict(pid=17)],[]))
    with pytest.raises(ValueError):m.final_launch_guard(tmp_path,profile(),dict(selected_reserve_gib=2),dict())

def test_prior_result_is_not_an_authorization_to_doublelaunch(tmp_path):
    m=module();(tmp_path/'SUMMARY.json').write_text('{}',encoding='utf8')
    with pytest.raises((ValueError,FileExistsError)):m.new_run_directory(tmp_path)

def stop_records():
    completed=[dict(batch=f'v20_normal_{i:03d}',passed=True,exit_code=0) for i in range(12) if i not in (8,9)]
    summary=dict(exit_code=125,state='explicit_stop_new_launches',completed=completed,
        actual_scientific_children_started=10,existing_qualified_skipped=['v20_normal_009'],pending_not_started=['v20_normal_008'],
        started_without_completed_supervisor_result=[])
    command=dict(name='normal03',exit_code=125,cwd=str(module().ROOT))
    s3=dict(status='failed_unknown',source_modified=False,checkpoint=dict(registered=406,actual_started=405,actual_finished=405,active=0,not_started=1,failed_terminal_journals=[]))
    return summary,command,s3

@pytest.mark.parametrize('mutation',['ok','false_success','pending_other','active','summary0','fake_complete','009_measured'])
def test_stopped_chain_preserves_actual125_and_ten_measured_plus009_unknown(mutation):
    m=module();s,c,t=stop_records()
    if mutation=='false_success':t['status']='passed'
    elif mutation=='pending_other':s['pending_not_started']=['v20_normal_007']
    elif mutation=='active':t['checkpoint']['active']=1
    elif mutation=='summary0':s['exit_code']=0
    elif mutation=='fake_complete':s['completed'][0]['passed']=False
    elif mutation=='009_measured':s['completed'][0]['batch']='v20_normal_009'
    if mutation=='ok':assert m.validate_stop_chain(s,c,t)['qualified'] is True
    else:
        with pytest.raises(ValueError):m.validate_stop_chain(s,c,t)

class FakeProcess:
    pid=12345
    def __init__(self):self.waits=0;self.returncode=None;self.polls=0
    def poll(self):self.polls+=1;return None if self.polls==1 else 0
    def wait(self):self.waits+=1;self.returncode=0;return 0

@pytest.mark.parametrize('failure',['first_journal','sample','exception_journal'])
def test_owned_child_is_always_drained_despite_diagnostic_failure(tmp_path,monkeypatch,failure):
    m=module();proc=FakeProcess();calls=[]
    monkeypatch.setattr(m.subprocess,'Popen',lambda *a,**k:proc)
    def journal(*a,**k):
        if failure in ('first_journal','exception_journal'):raise OSError('synthetic journal failure')
    monkeypatch.setattr(m,'journal',journal)
    def sample(*a):raise RuntimeError('synthetic sampling failure')
    monkeypatch.setattr(m,'process_sample',sample)
    with pytest.raises((OSError,RuntimeError)):
        m.monitor_child(['python','-m','tools.load_accounting.runner','run'],tmp_path,{},calls)
    assert proc.waits==1 and calls[0]['successful_Popen'] and calls[0]['owned_child_drained']
    assert calls[0]['pid']==12345 and calls[0]['exit_code']==0

def test_short_lived_qualifier_failure_cannot_return_success(tmp_path,monkeypatch):
    m=module();monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:__import__('types').SimpleNamespace(returncode=7))
    with pytest.raises(ValueError,match='qualification|child'):
        m.short_qualification(tmp_path,'preflight')

def test_no_hidden_heavy_module_can_reside_in_persistent_parent(monkeypatch):
    m=module();monkeypatch.setitem(sys.modules,'tools.synthetic_heavy_validator',object())
    with pytest.raises(ValueError,match='Heavy'):
        m.parent_footprint()

def test_pressure_is_recorded_without_killing_a_scientific_child(tmp_path,monkeypatch):
    m=module();proc=FakeProcess();calls=[]
    monkeypatch.setattr(m.subprocess,'Popen',lambda *a,**k:proc)
    monkeypatch.setattr(m,'active_science',lambda:([],[]));monkeypatch.setattr(m.time,'sleep',lambda n:None)
    monkeypatch.setattr(m,'process_sample',lambda p:dict(batch=m.TARGET,pid=p.pid,process_sample_status='measured',
        private_bytes=17,working_set_bytes=11,OS_lifetime_peak_working_set_bytes=13,
        available_physical_bytes=1,commit_headroom_bytes=1))
    result=m.monitor_child(['same_frozen_runner'],tmp_path,{},calls)
    assert proc.waits==1 and result['minimum_observed_commit_headroom_bytes']==1
    assert result['pressure_policy']=='record only; never terminate science'
    assert calls[0]['successful_Popen'] and calls[0]['owned_child_drained']

def test_preflight_failure_prevents_any_scientific_launch(tmp_path,monkeypatch):
    from contextlib import nullcontext
    m=module();monkeypatch.setattr(m,'HERE',tmp_path/'new_runs')
    monkeypatch.setattr(m,'stop_evidence',lambda *a:dict(evidence_files=[]));monkeypatch.setattr(m,'historical_profile',profile)
    monkeypatch.setattr(m,'serial_lease',nullcontext);monkeypatch.setattr(m,'active_science',lambda:([],[]))
    monkeypatch.setattr(m,'parent_footprint',lambda:dict(heavy_modules=[]))
    def fail(*a):raise ValueError('qualification deliberately failed')
    monkeypatch.setattr(m,'short_qualification',fail)
    def forbidden(*a,**k):raise AssertionError('Scientific Popen must never occur')
    monkeypatch.setattr(m.subprocess,'Popen',forbidden)
    assert m.continuation('TEST',run=True,baseline_root=tmp_path,stop_sha='a'*64,plan_sha='b'*64)==2
    summary=m.read(tmp_path/'new_runs/TEST/SUMMARY.json')
    assert summary['actual_scientific_children_started']==0 and summary['last_child'] is None
    assert summary['status']=='failed_preserve_evidence'
