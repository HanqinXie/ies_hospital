"""V31 configuration, causal permission and ordinary-hook regression tests."""
import copy,importlib,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]

def api(name='rules'):
    try:return importlib.import_module('tools.model_closeout_v31.'+name)
    except ModuleNotFoundError as exc:pytest.fail('V31 registered preparation implementation is missing: '+str(exc))

def frozen_m():
    reg=json.loads((ROOT/'configs/model_review_v30/cases.json').read_text(encoding='utf8'))
    return copy.deepcopy(next(x for x in reg['cases'] if x['id']=='b05_L3_ER_normal_M')['policy']['config'])

def record(time=45.,availability=(1.,.5,0.)):
    features={f'availability_BOILER_{i+1}':v for i,v in enumerate(availability)}
    return dict(decision_time_min=time,features=features,feature_metadata={k:dict(available_time_min=time,valid_time_min=time) for k in features})

def dispatch(steam=200.):
    from tools.sci_engineering.policy import DISPATCH_FIELDS
    return {k:([0.,0.,0.] if k in ('gt_electric_kW','electric_chiller_cooling_kW') else steam if k=='boiler_steam_lbph' else 0.) for k in DISPATCH_FIELDS}

def test_only_the_cooling_bit_changes_in_complete_frozen_M():
    from tools.model_correctness_v23.policy import SciPolicy
    base=frozen_m();new=api().expanded_method_config(base,'M_cooling_permission')
    assert {k for k in base if base[k]!=new[k]}=={'cooling_preparation'}
    assert new['cooling_preparation'] is True
    assert SciPolicy('M',config=new).config==new
    assert api().expanded_method_config(base,'M_steam_permission')==base
    assert base==frozen_m()

def test_bare_factory_defaults_cannot_replace_after_cost_M():
    from tools.model_correctness_v23.policy import policy_config
    base=policy_config('M');assert base['online_GT_count_order']=='legacy_before_cost'
    with pytest.raises(ValueError,match='frozen M'):api().expanded_method_config(base,'M_cooling_permission')

def test_receipt_uses_only_three_current_features_and_fixed_rating():
    r=record();r['future_fault_time']=object();before=copy.deepcopy(r['features'])
    receipt=api().observe_boiler_capacity(r,900.)
    assert receipt['healthy_capacity_lbph']==450.
    assert receipt['boiler_total_rating_lbph']==900.
    assert receipt['decision_time_minute']==45.
    assert set(receipt['observable_availability'])=={f'availability_BOILER_{i}' for i in (1,2,3)}
    assert 'future_fault_time' not in receipt
    assert r['features']==before

@pytest.mark.parametrize('bad',[True,float('nan'),-0.1,1.1])
def test_invalid_observable_availability_is_rejected(bad):
    r=record();r['features']['availability_BOILER_1']=bad
    with pytest.raises(ValueError):api().observe_boiler_capacity(r,900.)

def test_future_or_missing_feature_clock_is_rejected():
    r=record();r['feature_metadata']['availability_BOILER_2']['available_time_min']=46.
    with pytest.raises(ValueError):api().observe_boiler_capacity(r,900.)
    del r['feature_metadata']['availability_BOILER_2']
    with pytest.raises(ValueError):api().observe_boiler_capacity(r,900.)

def test_only_boiler_permission_changes_without_mutating_common_result():
    original=dispatch();diagnostic={'m23_preparation':{'original_boiler_target_lbph':100.}}
    evidence=api().observe_boiler_capacity(record(),900.)
    result,trace=api().prepare_steam_permission(original,diagnostic,evidence,45.)
    assert result['boiler_steam_lbph']==450.
    assert {k for k in result if result[k]!=original[k]}=={'boiler_steam_lbph'}
    assert original==dispatch() and 'v31_steam_permission' not in diagnostic
    assert trace['v31_steam_permission']['common_permission_lbph']==200.
    assert trace['v31_steam_permission']['original_planner_permission_lbph']==100.
    assert trace['v31_steam_permission']['generated_minute']==45.

def test_common_permission_is_never_lowered_and_overshoot_is_explicit():
    e=api().observe_boiler_capacity(record(),900.)
    out,_=api().prepare_steam_permission(dispatch(800.),{},e,45.)
    assert out['boiler_steam_lbph']==800.
    with pytest.raises(ValueError,match='rating'):api().prepare_steam_permission(dispatch(901.),{},e,45.)
    with pytest.raises(ValueError,match='clock'):api().prepare_steam_permission(dispatch(),{},e,60.)

def test_shared_preparation_runs_first_and_ordinary_other_fields_survive():
    mod=api('environment');events=[]
    class Common:
        def _prepare_command_dispatch(self,d,diag,loads):
            events.append('common');d=copy.deepcopy(d);d['boiler_steam_lbph']=250.;d['electric_chiller_cooling_kW']=[60.,0.,0.]
            return d,dict(diag,m23_preparation={'original_boiler_target_lbph':200.})
    class Core(mod.SteamPermissionMixin,Common):pass
    core=Core();core.time=45.;core.env=SimpleNamespace(_v31_permission_mailbox=api().observe_boiler_capacity(record(),900.))
    out,diag=core._prepare_command_dispatch(dispatch(),{},[[0.,0.,0.]]*4)
    assert events==['common'] and out['boiler_steam_lbph']==450.
    assert out['electric_chiller_cooling_kW']==[60.,0.,0.]
    assert diag['v31_steam_permission']['common_permission_lbph']==250.

def test_disabled_and_cooling_adapter_leave_original_environment_factory(monkeypatch):
    mod=api('environment');base=SimpleNamespace(OperatingHospitalIESEnv=object)
    monkeypatch.setattr(mod,'original_adapter',lambda *a,**k:base)
    assert mod.with_preparation(None) is base
    assert mod.with_preparation('M_cooling_permission') is base

def test_publishing_policy_retains_M23_request_and_witness():
    from test_sci_policy import observed
    from tools.model_correctness_v23.policy import SciPolicy
    from hospital_ies.model import load_plant
    mod=api('environment');r=observed(time=45.)
    r['model_closure_v22']=dict(observation_time_minute=45.,available_time_minute=45.,current_non_diesel_feasibility={'ready':False,'status':'fixture'})
    # The real M23 policy also checks existing converter/tower ratings. Use the
    # unchanged complete plant instead of an incomplete one-field test stub.
    mailbox={};plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    base=SciPolicy('M',config=frozen_m(),policy_id='base',plant=plant)
    new=mod.SteamPermissionPolicy('M',config=frozen_m(),policy_id='M_steam_permission',plant=plant,mailbox=mailbox)
    assert new.decide([r])[0]==base.decide([r])[0]
    assert mailbox['healthy_capacity_lbph']==plant.boiler_steam_lbph
    assert new.config==base.config

def test_four_registered_scenarios_are_exact_originals_and_only_fixed_methods():
    mod=api('validation');reg=mod.load_registry()
    old=json.loads((ROOT/'configs/model_review_v30/cases.json').read_text(encoding='utf8'))
    assert len(reg['cases'])==4
    for row in reg['cases']:
        source=next(x for x in old['cases'] if x['id']==row['v31_preparation']['base_case_id'])
        assert row['scenario']==source['scenario']
        assert row['configuration']==source['configuration']=='H0'
        resolved=mod.resolve_case(row,reg)
        assert resolved['policy']['config']==row['policy']['config']
        assert resolved['policy']['config']['online_GT_count_order']=='after_cost'
        assert resolved['policy']['config']['time_energy_preparation'] is False
        assert resolved['v31_preparation']['method'] in ('M_cooling_permission','M_steam_permission')
