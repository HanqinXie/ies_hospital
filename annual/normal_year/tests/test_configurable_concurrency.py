"""Pure process orchestration checks; no IES environment or solver is used."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
KIND='normal_year' if (ROOT/'transfer_checks.py').is_file() else 'busy_year'
sys.path.insert(0,str(ROOT))

def load(relative,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/relative)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def limits():
    return dict(max_producers=3,max_accounts=2,max_total_processes=5,threads_per_producer=1,
        max_recoveries_global=1,max_wall_seconds=100,rss_limit_bytes=10**12,
        min_available_ram_bytes=1,min_free_disk_bytes=1,initial_free_disk_required_bytes=1,
        grace_seconds=0,poll_seconds=.01,restore_timeout_seconds=60)

@pytest.mark.parametrize('count',[3,6])
def test_requested_counts_are_not_bound_to_legacy_package_caps(count):
    runner=load('server_runner.py','configurable_runner')
    configured=dict(limits(),max_producers=count,max_accounts=count,max_total_processes=count+1)
    assert runner.validate_limits(configured)['max_producers']==count
    checks=load('transfer_checks.py' if KIND=='normal_year' else 'runtime_checks.py','configurable_checks')
    assert checks.validate_limits(configured)['max_accounts']==count
    profile=load('scripts/server_profile.py','configurable_profile')
    assert profile.validate_execution_policy(KIND,max_producers=count)['max_producers']==count

@pytest.mark.parametrize('value',[0,-1,True,1.5])
def test_malformed_counts_remain_rejected(value):
    runner=load('server_runner.py','invalid_count_runner')
    with pytest.raises(ValueError):runner.validate_limits(dict(limits(),max_producers=value))

def test_counts_cannot_exceed_the_explicit_total_budget():
    runner=load('server_runner.py','inconsistent_count_runner')
    with pytest.raises(ValueError):runner.validate_limits(dict(limits(),max_total_processes=2))

@pytest.mark.parametrize('selected',[None] if KIND=='normal_year' else [None,['M_ready']])
def test_three_distinct_annual_producers_or_one_selected_ready(tmp_path,monkeypatch,selected):
    runner=load('server_runner.py','scheduled_count_runner')
    if KIND=='normal_year':
        import test_server_runner as fixture
        package,work,admission=fixture.example.__wrapped__(tmp_path)
        owner=fixture.FakeOwner;owner.launched=[];owner.failures_left=0;owner.restore_fails=False
        options={}
        if selected:
            cases=[c for c in runner.load_cases(package) if c['method'] in selected]
            monkeypatch.setattr(runner,'load_cases',lambda *a:cases)
    else:
        import test_busy_runtime as fixture
        package,work,frozen=(tmp_path/n for n in ('package','work','frozen'))
        package.mkdir();work.mkdir();options=dict(frozen_root=frozen,methods=selected)
        cases=[dict(method=n,case_id=n,file=str(frozen/(n+'.json'))) for n in runner.METHODS]
        monkeypatch.setattr(runner,'load_cases',lambda *a:cases)
        owner=fixture.FakeOwner;owner.commands=[];owner.failures_left=0;owner.restore_fails=False
    monkeypatch.setattr(runner,'require_admission',lambda *a:dict(limits=limits()))
    result=runner.run_campaign(package,work,owner_factory=owner,
        resource_probe=lambda *a:dict(available_ram_bytes=10**12,free_disk_bytes=10**12,rss_bytes=0),
        sleeper=lambda _:None,**options)
    assert result['status']=='passed'
    events=[json.loads(line) for line in (work/'runtime/EVENTS.jsonl').read_text(encoding='utf8').splitlines()]
    first_exit=next(i for i,r in enumerate(events) if r['event']=='process_exited')
    launches=[r for r in events[:first_exit] if r['event']=='process_started' and r['phase']=='producer']
    expected=set(selected or runner.METHODS)
    assert len(launches)==len(expected) and {r['method'] for r in launches}==expected
    assert len({r['paths']['output'] for r in launches})==len(expected)
    assert len(result['completed'])==len(expected)
