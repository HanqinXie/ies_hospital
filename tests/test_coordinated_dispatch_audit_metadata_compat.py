"""One local display-sort regression; no files, environment or raw trajectory."""
import copy
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_original_mixed_null_sort_fails_and_compat_preserves_groups_and_metrics():
    from tools.policy_redesign import audit as old
    plan=dict(scenario_ids=['a','b'],policy_specs=[dict(policy_id='secure_milp'),dict(policy_id='candidate')])
    scenes={'a':dict(root_family_id='r',pressure_point_index=0),
            'b':dict(root_family_id='r',condition_id='registered',pressure_point_index=1)}
    rows=[]
    for sid in ('a','b'):
        for pid in ('secure_milp','candidate'):
            score=dict(full_horizon=True,targets=[0.]+[float((i+1)*(1 if pid=='secure_milp' else 2)) for i in range(18)])
            rows.append(dict(scenario_id=sid,policy_id=pid,status='completed',primary_score=score,
                full_observation=score if sid=='b' or pid=='secure_milp' else dict(full_horizon=False,targets=[None]*19)))
    before=copy.deepcopy((plan,scenes,rows));original=old._subgroups
    with pytest.raises(TypeError,match='not supported'):
        original(plan,scenes,rows)
    path=ROOT/'outputs/v12_service_switch_20261017_run01/review/audit_metadata_compat.py'
    if not path.exists():pytest.fail('Scoped audit display-sort compatibility adapter missing')
    spec=importlib.util.spec_from_file_location('audit_metadata_compat',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.subgroups_compat(plan,scenes,rows)
    assert result['by_root'][0]['condition_ids']==[None,'registered']
    assert {r['condition_id'] for r in result['by_condition']}=={'unregistered_condition','registered'}
    for collection in ('by_condition','by_root','by_pressure_point'):
        for group in result[collection]:
            selected=[row for row in rows if row['scenario_id'] in group['scenario_ids']]
            expected={pid:{w:old._compact_metric(old.paired_metrics(selected,pid,score_field=field))
                for w,field in [('primary','primary_score'),('full','full_observation')]}
                for pid in ('secure_milp','candidate')}
            assert group['policy_comparisons']==expected
    homogeneous=copy.deepcopy(scenes);homogeneous['a']['condition_id']='other'
    assert module.subgroups_compat(plan,homogeneous,rows)==original(plan,homogeneous,rows)
    assert (plan,scenes,rows)==before and old._subgroups is original
    assert module.subgroups_compat.__globals__ is not original.__globals__
    assert module.subgroups_compat.__globals__['paired_metrics'] is original.__globals__['paired_metrics']
