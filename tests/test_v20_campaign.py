from pathlib import Path
import sys
import copy
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def test_registered_recheck_denominators_and_normal_serial_classification():
    from scripts.v20_campaign import campaign_jobs
    jobs=campaign_jobs(ROOT/'outputs/v19_model_closeout_20261018_run01/design')
    # One process per normal-window policy prevents retaining three analyses.
    assert len(jobs)==148
    counts={}
    for job in jobs:counts[job['cohort']]=counts.get(job['cohort'],0)+len(job['policies'])
    assert counts==dict(main=240,long=24,normal=12,compat=7,fixed8=96,propagation=18,open1=6,contact025=3)
    assert sum(counts.values())==406
    assert sum(len(j['policies']) for j in jobs if j['normal_serial'])==12
    assert all(len(j['policies'])==1 for j in jobs if j['normal_serial'])
    assert all(not j['scene'].get('is_final_blind') for j in jobs)

def test_job_builder_does_not_mutate_or_relabel_exogenous_source():
    import json
    from scripts.v20_campaign import campaign_jobs
    folder=ROOT/'outputs/v19_model_closeout_20261018_run01/design'
    original=json.loads((folder/'MAIN_FINAL_R1_PROPOSAL.json').read_text(encoding='utf-8-sig'))
    before=copy.deepcopy(original)
    jobs=campaign_jobs(folder)
    first=next(j for j in jobs if j['cohort']=='main')
    assert first['scene']==original['scenarios'][0]
    assert original==before
    assert len({j['name'] for j in jobs})==148

def test_orchestrator_retains_pending_after_failure_while_other_child_finishes(tmp_path,monkeypatch):
    import json
    from scripts import v20_campaign as c
    run=tmp_path/'outputs/run';(run/'design').mkdir(parents=True)
    jobs=[dict(name='job'+str(i),normal_serial=False) for i in range(3)]
    (run/'design/CAMPAIGN_test.json').write_text(json.dumps(dict(jobs=jobs)),encoding='utf-8')
    monkeypatch.setattr(c,'ROOT',tmp_path);monkeypatch.setattr(c,'RUN',run)
    monkeypatch.setattr(c,'available_memory',lambda:64*1024**3)
    monkeypatch.setattr(c.time,'sleep',lambda _:None)
    calls=[]
    class FakeProcess:
        pid=123
        def __init__(self,sequence):self.sequence=iter(sequence)
        def poll(self):return next(self.sequence)
    def launch(*args,**kwargs):
        calls.append(args)
        return FakeProcess([2] if len(calls)==1 else [None,0])
    monkeypatch.setattr(c.subprocess,'Popen',launch)
    assert c.run_campaign('test','all',2,8.,8.)==2
    result=json.loads(next((run/'orchestration').glob('*/RESULT.json')).read_text(encoding='utf-8'))
    assert result['pending_retained']==['job2']
    assert len(calls)==2

def test_failed_terminal_batch_cannot_be_skipped_as_success(tmp_path,monkeypatch):
    import json,pytest
    from scripts import v20_campaign as c
    run=tmp_path/'run';(run/'design').mkdir(parents=True)
    (run/'design/CAMPAIGN_test.json').write_text(json.dumps(dict(jobs=[dict(name='failed',normal_serial=False)])),encoding='utf-8')
    old=run/'experiments/failed';old.mkdir(parents=True)
    (old/'BATCH_RESULT.json').write_text(json.dumps(dict(registered_pairs=3,complete_full=2,unknown_full=1)),encoding='utf-8')
    monkeypatch.setattr(c,'ROOT',tmp_path);monkeypatch.setattr(c,'RUN',run)
    with pytest.raises(ValueError,match='failed terminal'):c.run_campaign('test','all',1,6.,8.)

def test_ram_admission_reserves_for_children_that_have_not_grown_yet():
    from scripts.v20_campaign import memory_admission
    assert memory_admission(24*1024**3,0,6.,8.)
    assert not memory_admission(24*1024**3,2,6.,8.)

def test_declared_batch_counts_without_actual_records_are_not_verified(tmp_path):
    from scripts.v20_campaign import terminal_issues
    terminal=dict(registered_pairs=3,complete_full=3,unknown_full=0,status_counts={'completed':3})
    assert terminal_issues(tmp_path,terminal)

def test_malformed_child_terminal_is_retained_as_failure_not_uncaught(tmp_path,monkeypatch):
    import json
    from scripts import v20_campaign as c
    run=tmp_path/'run';(run/'design').mkdir(parents=True)
    (run/'design/CAMPAIGN_test.json').write_text(json.dumps(dict(jobs=[dict(name='broken',normal_serial=False)])),encoding='utf-8')
    monkeypatch.setattr(c,'ROOT',tmp_path);monkeypatch.setattr(c,'RUN',run)
    monkeypatch.setattr(c,'available_memory',lambda:64*1024**3)
    class Child:
        pid=321
        def poll(self):return 2
    def launch(*args,**kwargs):
        p=run/'experiments/broken';p.mkdir(parents=True)
        (p/'BATCH_RESULT.json').write_text('{truncated',encoding='utf-8')
        return Child()
    monkeypatch.setattr(c.subprocess,'Popen',launch)
    assert c.run_campaign('test','all',1,6.,8.)==2
    v=json.loads(next((run/'orchestration').glob('*/RESULT.json')).read_text(encoding='utf-8'))
    assert v['failures'] and 'malformed terminal' in v['failures'][0]['validation_issues'][0]
