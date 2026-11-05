"""The factorial rule comparison changes permission, never physical capability."""
import copy, importlib, json, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def api():
    try:return importlib.import_module('tools.operating_rules_study.environment')
    except ModuleNotFoundError as exc:pytest.fail('Study method composition is missing: '+str(exc))

def base():
    p=json.loads((ROOT/'configs/model_review_v30/cases.json').read_text(encoding='utf8'))
    return next(c['policy']['config'] for c in p['cases'] if c['id']=='b05_L3_ER_normal_M')

def test_two_permission_factors_are_independent_and_preserve_frozen_M():
    mod=api();old=base()
    for method in ('M','M_steam'):
        assert mod.method_config(old,method)==old
    for method in ('M_cooling','M_both'):
        new=mod.method_config(old,method)
        assert {k for k in old if old[k]!=new[k]}=={'cooling_preparation'}
        assert new['cooling_preparation'] is True
    assert old==base()

def test_bare_default_and_unknown_method_are_rejected():
    mod=api();bad=base();bad['online_GT_count_order']='legacy_before_cost'
    with pytest.raises(ValueError):mod.method_config(bad,'M_both')
    with pytest.raises(ValueError):mod.method_config(base(),'unregistered')

def test_combined_steam_hook_preserves_common_cooling_and_all_other_ports():
    mod=api();events=[]
    class Base:
        def _prepare_command_dispatch(self,d,diag,loads):
            events.append('common');out=copy.deepcopy(d);out['electric_chiller_cooling_kW']=[50.,60.,70.]
            return out,dict(diag)
    class Core(mod.StudySteamMixin,Base):pass
    t=45.;features={f'availability_BOILER_{i}':1. for i in (1,2,3)}
    record=dict(decision_time_min=t,features=features,feature_metadata={k:dict(available_time_min=t,valid_time_min=t) for k in features})
    from tools.model_closeout_v31.rules import observe_boiler_capacity
    core=Core();core.time=t
    core.env=type('Env',(),{'study_boiler_mailbox':observe_boiler_capacity(record,3000.)})()
    dispatch=dict(boiler_steam_lbph=200.,electric_chiller_cooling_kW=[0.,0.,0.],grid_import_kW=100.,gt_electric_kW=[120.,0.,0.])
    out,diag=core._prepare_command_dispatch(dispatch,{},[[0.,0.,0.]]*4)
    assert events==['common'] and out['boiler_steam_lbph']==3000.
    assert out['electric_chiller_cooling_kW']==[50.,60.,70.]
    assert out['grid_import_kW']==100. and out['gt_electric_kW']==[120.,0.,0.]
    assert dispatch['boiler_steam_lbph']==200.
