import copy,importlib.util,json
from pathlib import Path
import pytest

def module():
    path=Path(__file__).resolve().parents[1]/'tools/model_review_v30/reference.py'
    spec=importlib.util.spec_from_file_location('v30_staged_reference',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def fixture():
    def action(n):return {'ordinary_dispatch':{'dispatch':{'gt_electric_kW':[n,0.,0.]}}}
    return dict(execution_identity='offline_onoff_preparation_plus_common_realtime_allocation',
        information_permission='offline_known_window',full_continuous_plan_replayed=False,
        requests=[dict(time_minute=0.,action=action(1)),dict(time_minute=14.,action=action(2)),dict(time_minute=16.,action=action(3))])

def test_reference_samples_only_registered_schedule_and_never_c_preparation(tmp_path):
    m=module();data=fixture();before=copy.deepcopy(data)
    p=tmp_path/'actions.json';p.write_text(json.dumps(data))
    policy=m.OfflineSchedulePolicy('R_off',{'actions_path':str(p)},policy_id='R_off_test')
    request,diag=policy.decide([{'decision_time_min':15.}])
    assert request['ordinary_dispatch']['dispatch']['gt_electric_kW']==[2.,0.,0.]
    assert diag['full_continuous_plan_replayed'] is False
    assert diag['information_permission']=='offline_known_window'
    request['ordinary_dispatch']['dispatch']['gt_electric_kW'][0]=999.
    assert policy.decide([{'decision_time_min':15.}])[0]['ordinary_dispatch']['dispatch']['gt_electric_kW'][0]==2.
    assert data==before
    with pytest.raises(ValueError):m.OfflineSchedulePolicy('C',{'actions_path':str(p)})

def test_unknown_or_infeasible_plan_never_returns_zero_action(tmp_path):
    m=module();data=fixture();data['requests']=[]
    p=tmp_path/'actions.json';p.write_text(json.dumps(data))
    with pytest.raises(ValueError):m.OfflineSchedulePolicy('R_off',{'actions_path':str(p)})
    data=fixture();data['information_permission']='causal_current'
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):m.OfflineSchedulePolicy('R_off',{'actions_path':str(p)})

def test_custom_controller_requires_explicit_named_study_before_any_execution(tmp_path):
    from tools.model_correctness_v23.validation import worker
    called=[]
    with pytest.raises(ValueError,match='named study'):
        worker(tmp_path,controller_factory=lambda *a,**k:called.append(True))
    assert called==[]
