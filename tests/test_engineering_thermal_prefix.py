"""Two tiny synthetic gzip files; never open a production trajectory."""
import csv
import gzip
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def api():
    try:
        return importlib.import_module('tools.engineering_reporting.thermal_prefix')
    except ModuleNotFoundError:
        pytest.fail('Offline thermal-prefix exporter is missing')


def segment(a, b, p2=25., p3=None, flag2=False, flag3=None):
    return dict(start_minute=a, end_minute=b,
        temperature=dict(P0=21., P1=22., P2=p2, P3=p3),
        violation=dict(P0=False, P1=False, P2=flag2, P3=flag3))


def test_single_pass_gzip_export_preserves_unknown_missing_fields_and_long_windows(tmp_path, monkeypatch):
    a = api(); run = tmp_path/'run'; folder=run/'experiments/one'
    (folder/'analysis').mkdir(parents=True)
    scenes=[dict(id='short',analysis_class='L4b',duration_steps=40,step_minutes=15),
            dict(id='long',analysis_class='N2',duration_steps=96,step_minutes=15)]
    values=[dict(summary=dict(observed_until_minute=100.,requested_end_minute=600.),
        identity=dict(scenario_id='short',policy_id='P'),segments=[segment(0.,61.25,flag2=True),segment(61.25,100.,p2=27.)]),
        dict(summary=dict(observed_until_minute=1440.,requested_end_minute=1440.),
        identity=dict(scenario_id='long',policy_id='P'),segments=[segment(0.,600.,p3=28.,flag3=False),segment(600.,1440.,p3=29.,flag3=True)])]
    records=[]; paths=[]
    for scene,value in zip(scenes,values):
        raw=folder/'pairs'/scene['id']/'trajectory.json.gz'; raw.parent.mkdir(parents=True)
        compressed=gzip.compress(json.dumps(value).encode('utf8'));raw.write_bytes(compressed);paths.append(raw)
        records.append(dict(scenario_id=scene['id'],policy_id='P',raw_path=str(raw),raw_sha256=hashlib.sha256(compressed).hexdigest()))
    for name,value in [('plan.json',dict(phase='development',model_id='M0',model_config={},
          scenario_ids=['short','long'],policy_specs=[{'policy_id':'P'}],registered_pairs=2)),
          ('manifest.json',dict(scenarios=scenes)),('BATCH_RESULT.json',{})]:
        (folder/name).write_text(json.dumps(value),encoding='utf8')
    (folder/'analysis/RESULTS.json').write_text(json.dumps(dict(records=records)),encoding='utf8')
    opened=[]; original=Path.open
    def tracked(path,*args,**kwargs):
        if path in paths and args and args[0]=='rb':opened.append(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',tracked)
    with pytest.raises(ValueError,match='stopped'):
        a.export(run,tmp_path/'forbidden')
    assert not opened
    receipt=a.export(run,tmp_path/'thermal',all_stopped=True)
    assert opened.count(paths[0])==opened.count(paths[1])==1
    assert receipt['registered_pairs']==2 and receipt['raw_files_opened']==2
    assert receipt['complete_full_pairs']==1 and receipt['unknown_full_pairs']==1
    assert receipt['physical_episodes_started']==0 and receipt['extraction_errors']==[]
    with (tmp_path/'thermal/THERMAL_PREFIX.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.DictReader(stream))
    row=next(r for r in rows if r['scenario_id']=='short' and r['window']=='primary' and r['priority']=='P2')
    assert float(row['observed_prefix_max_temperature_C'])==27.
    assert float(row['observed_prefix_violation_minutes'])==1.25
    assert row['full_window_max_temperature_C']=='' and row['full_window_violation_minutes']==''
    missing=next(r for r in rows if r['scenario_id']=='short' and r['window']=='full' and r['priority']=='P3')
    assert missing['observed_prefix_max_temperature_C']=='' and missing['observed_prefix_violation_minutes']==''
    long=next(r for r in rows if r['scenario_id']=='long' and r['window']=='long1440' and r['priority']=='P3')
    assert float(long['full_window_max_temperature_C'])==29. and float(long['full_window_violation_minutes'])==840.
    full=next(r for r in rows if r['scenario_id']=='long' and r['window']=='full' and r['priority']=='P3')
    assert float(full['full_window_max_temperature_C'])==28. and float(full['full_window_violation_minutes'])==0.
    assert len(list((tmp_path/'thermal').iterdir()))==2
