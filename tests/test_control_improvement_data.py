"""New-round data contract tests; no simulation, resampling or training."""
import copy
import importlib
import json
from pathlib import Path
from collections import Counter
from functools import lru_cache
import pytest

ROOT=Path(__file__).resolve().parents[1]
def mod():return importlib.import_module('tools.control_improvement.data_design')

@lru_cache(maxsize=1)
def proposal():return mod().build_proposal(ROOT,seed=20261015)

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')

def fake_root(tmp_path):
    save(tmp_path/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json',
        dict(scenarios=[],p2b_time_blocks=[dict(time_block_id='p2b_day_021',raw_start_minute=21*1440,raw_end_minute=22*1440,reserved_only=True)]))
    return tmp_path

def write_attempt(root,phase='new_fixed_v1',failed=False,started=True):
    base=root/'outputs/v43_integrated_20261014_run01'/phase
    spec=dict(id='actual_case',start_hour=3000,duration_steps=32,step_minutes=15)
    save(base/'global_manifest.json',dict(scenarios=[spec]))
    if started:
        path=base/'shards/shard_00/attempts.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
        rows=[dict(event='started',scenario_id='actual_case',policy_id='priority_milp')]
        if failed:rows.append(dict(event='finished',scenario_id='actual_case',policy_id='priority_milp',status='solver_error'))
        path.write_text('\n'.join(map(json.dumps,rows))+'\n',encoding='utf-8')
    return base,spec

@pytest.mark.parametrize('phase',['new_fixed_v1','new_adaptive_v1'])
def test_previous_integrated_actual_exposure_included(tmp_path,phase):
    root=fake_root(tmp_path);base,s=write_attempt(root,phase)
    rows,info=mod().exposure_registry(root)
    actual=[r for r in rows if r.get('scenario_id')==s['id']]
    assert len(actual)==1 and actual[0]['source']==(base/'global_manifest.json').relative_to(root).as_posix()
    assert actual[0]['raw_start_minute']==(3000-1)*60-60
    assert actual[0]['raw_end_minute']==(3000-1)*60+540+60

@pytest.mark.parametrize('failed',[False,True])
def test_failed_and_only_started_attempts_remain_exposure(tmp_path,failed):
    root=fake_root(tmp_path);write_attempt(root,failed=failed)
    rows,info=mod().exposure_registry(root)
    assert any(r.get('scenario_id')=='actual_case' for r in rows)
    assert info['actual_manifest_attempted_specs']==1

def test_planned_unrun_is_disclosed_not_incident_exposure(tmp_path):
    root=fake_root(tmp_path);base,s=write_attempt(root,started=False)
    rows,info=mod().exposure_registry(root)
    assert not any(r.get('scenario_id')=='actual_case' for r in rows)
    assert any(r['path']==(base/'global_manifest.json').relative_to(root).as_posix() for r in info['planned_unrun_manifests'])

def test_registered_budgets_roles_and_mechanisms():
    p=proposal();d=mod()
    assert len(p['fixed_scenarios'])==300 and len(p['blocks'])==60
    assert not p['adaptive_slots']
    assert Counter(b['role'] for b in p['blocks'])==dict(pilot_train=36,pilot_validation=12,development_boundary=12)
    assert Counter(s['experiment_role'] for s in p['fixed_scenarios'])==dict(pilot_train=180,pilot_validation=60,development_boundary=60)
    assert len(d.CONDITIONS)==6 and Counter(b['condition_id'] for b in p['blocks'])==dict.fromkeys(d.CONDITIONS,10)
    assert p['budget']['initial_specifications']==300 and p['budget']['maximum_new_specifications']==1200

def test_real_all_exposures_and_sealed_excluded():
    p=proposal()
    assert len(p['exposure']['sealed_final_days'])==12 and p['candidate_support_days_available']>=60
    assert any('new_fixed_v1' in r['source'] for r in p['excluded_support'])
    assert any('new_adaptive_v1' in r['source'] for r in p['excluded_support'])
    for b in p['blocks']:
        assert b['day_index'] not in p['exposure']['sealed_final_days']
        assert all(b['raw_end_minute']<=r['raw_start_minute'] or b['raw_start_minute']>=r['raw_end_minute'] for r in p['excluded_support'])

def test_same_root_five_joint_pressure_points_and_no_on_grid_permission():
    p=proposal();d=mod()
    for block in p['blocks']:
        values=[s for s in p['fixed_scenarios'] if s['time_block_id']==block['time_block_id']]
        assert len(values)==5 and sorted(s['pressure_coordinate'] for s in values)==[0.,.25,.5,.75,1.]
        assert len({s['root_family_id'] for s in values})==1
        assert len({s['shock_start_minute'] for s in values})==1
        for s in values:
            assert d.approved_pressure([s['shock']['e'],s['shock']['c'],s['shock']['h'],s['shock_by_priority'].get('P0',{}).get('e',0)])
            assert s.get('diesel_on_grid_allowed',False) is False
            assert s['automatic_fast_shedding'] is False
            assert s['response_clock_anchor']=='shock_start_minute'
    grid=[s for s in p['fixed_scenarios'] if s.get('initiating_device')=='GRID']
    assert len(grid)==50 and all(s['faults']=={'GRID':0.} for s in grid)
    assert all(s['fault_start_minute']+s['fault_duration_minutes']<600 for s in grid)

def test_full600_tail_H120_and_real_annual_support():
    p=proposal()
    for s in p['fixed_scenarios']:
        assert s['duration_steps']==40 and s['step_minutes']==15
        assert s['history_interval_minutes']==[0,60] and s['score_interval_minutes']==[60,420]
        assert s['tail_interval_minutes']==[420,600] and 420+120<=600
        a=s['full_source_support']
        assert a['raw_end_minute']-a['raw_start_minute']==660
        assert 0<=a['raw_start_minute']<a['raw_end_minute']<=525600
    with pytest.raises(ValueError,match='year|8760|support'):
        mod().validate_observation_support(dict(start_hour=8759,duration_steps=40,step_minutes=15))

def test_joint_boxes_not_marginal_rectangle():
    assert not mod().approved_pressure([0.,1.25,.55,.9])
    assert mod().approved_pressure([2.1,1.25,.55,.9])

def test_reproducible_proposal_without_response_selection():
    assert mod().build_proposal(ROOT,seed=20261015)==proposal()
    assert proposal()['probability_semantics']['p_reference'] is None

def test_signature_and_fixed_materialization_tamper_rejected():
    p=copy.deepcopy(proposal());p['fixed_scenarios'][0]['grid_limit_kW']+=1
    with pytest.raises(ValueError,match='content|signature|changed'):mod().validate_proposal(p)
    p=proposal();specs=copy.deepcopy(p['fixed_scenarios']);specs[0]['experiment_role']='pilot_validation'
    with pytest.raises(ValueError,match='registered|role|changed'):mod().manifest_components(p,specs)

def test_manifest_split_shard_partition_and_group_support():
    p=proposal();c=mod().manifest_components(p,p['fixed_scenarios'])
    assert len(c['splits'])==300 and len({v['group_id'] for v in c['splits'].values()})==60
    assert c['options']['observed_end_minute']==600 and c['options']['score_end_minute']==420
    ids=[sid for sh in c['p2b_shards'] for sid in sh['scenario_ids']]
    assert len(ids)==len(set(ids))==300
    for s in p['fixed_scenarios']:
        a=c['splits'][s['id']]
        assert a['experiment_role']==s['experiment_role'] and a['fit_allowed']==(s['experiment_role']=='pilot_train')
        assert a['is_final_blind'] is False

def test_static_snapshot_plan_root_roles_and_full_H120():
    p=proposal();plan=mod().snapshot_plan(p);specs={s['id']:s for s in p['fixed_scenarios']}
    assert len(plan)==72 and len({r['root_family_id'] for r in plan})==24
    assert {r['snapshot_minute'] for r in plan}=={60.,180.,420.}
    assert Counter(r['experiment_role'] for r in plan)==dict(pilot_train=54,pilot_validation=18)
    for r in plan:
        s=specs[r['scenario_id']]
        assert r['snapshot_step']*15==r['snapshot_minute'] and r['snapshot_minute']+120<=600
        assert s['pressure_coordinate']==.75 and r['selection_uses_future_event_clock'] is False
        assert r['conditional_futures']==1

def test_initial_training_eight_specs_are_preregistered_without_results():
    p=proposal();ids=mod().initial_training_subset(p,8)
    assert len(ids)==len(set(ids))==8
    byid={s['id']:s for s in p['fixed_scenarios']}
    assert all(byid[s]['experiment_role']=='pilot_train' for s in ids)
    assert len({byid[s]['condition_id'] for s in ids[:6]})==6
    assert all(byid[s]['pressure_point_index']==3 for s in ids[:6])

def test_source_domain_provenance_has_actual_sha():
    import hashlib
    p=proposal()
    assert p['approved_domain_source']['source_sha256']==hashlib.sha256((ROOT/'scenario_sampler.py').read_bytes()).hexdigest()

def test_orphan_started_identity_rejected(tmp_path):
    root=fake_root(tmp_path);base,s=write_attempt(root)
    path=base/'shards/shard_00/attempts.jsonl'
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps(dict(event='started',scenario_id='not_registered'))+'\n')
    with pytest.raises(ValueError,match='orphan|outside'):mod().exposure_registry(root)
