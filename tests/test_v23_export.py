"""M23 export normalization and paid initial-inventory accounting contracts."""
import copy
import importlib
import json
from pathlib import Path
import sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]

def api():
    assert importlib.util.find_spec('tools.model_correctness_v23.export') is not None,'M23 single-pass exporter is missing'
    return importlib.import_module('tools.model_correctness_v23.export')

def test_supported_datatypes_preserve_legacy_export_json_semantics():
    from hospital_ies.dataset_export import json_clean as original
    values=[None,True,False,1,1.2,'医院',float('nan'),float('inf'),-float('inf'),np.float32(1.25),np.float64(np.nan),np.int64(7),np.bool_(True),
        np.str_('冷量'),np.array(7),np.array([1.,np.nan,np.inf]),np.array([[1.,2.],[3.,4.]]),np.array([{'x':np.float64(np.nan)},(1,2)],dtype=object),
        {'array':np.array([1.,2.]),'tuple':(1,np.float64(np.inf)),'set':{1,2,3},3:{np.int64(4):np.bool_(False)}},
        {1:'shadowed','1':{'kept':np.array([False,True])}}]
    for value in values:
        before=repr(value)
        expected=json.dumps(original(value),ensure_ascii=False,allow_nan=False,sort_keys=True)
        actual=json.dumps(api().json_clean(value),ensure_ascii=False,allow_nan=False,sort_keys=True)
        assert actual==expected
        assert repr(value)==before

def test_each_nested_container_is_traversed_once():
    calls=[]
    class CountedList(list):
        def __iter__(self):calls.append(1);return super().__iter__()
    value={'a':{'b':[{'c':CountedList([1,np.float64(np.nan)])}]}}
    assert api().json_clean(value)=={'a':{'b':[{'c':[1,None]}]}}
    assert len(calls)==1

@pytest.mark.parametrize('alias',['R','C'])
def test_actual_small_episode_exports_paid_inventory_once_in_fee_and_CO2(alias,tmp_path):
    from test_v23_recovery import make
    from test_sci_environment import scenario
    from hospital_ies import dataset_export
    from hospital_ies.model import RT_TO_KW
    from tools.model_correctness_v23.environment import run_episode
    from tools.model_correctness_v23.policy import SciPolicy
    from tools.model_correctness_v23 import CONTRACT_ID
    from tools.sci_engineering.runner import compact_metrics
    from tools.sci_engineering.solver_log import SolverRowSidecar
    from tools.model_correctness_v23.evidence import DomainEvidenceStore
    e=make(dict(sci_engineering=dict(cold_store=dict(capacity_kWh=60.,power_kW=120.,initial_soc=1.))))
    s=scenario();s.update(duration_steps=1,faults={'GRID':1.},fault_start_minute=0.,fault_duration_minutes=15.)
    s=dataset_export.normalize_scenario(s,7)
    manifest=dict(signature='small-export-regression',simulator_hash='test-only-source',execution_contract_id=CONTRACT_ID,
        assumption_set_id=CONTRACT_ID,model_config_signature='small-export-config',schemas={},
        splits={s['id']:dict(group_id=s['family_id'],split='development',fit_allowed=False)})
    with SolverRowSidecar(tmp_path/'solver_rows.jsonl.gz') as writer:
        e._solver_row_sidecar=writer;e.domain_evidence=DomainEvidenceStore(tmp_path/'domain_evidence')
        payload=run_episode(e,s,SciPolicy(alias,plant=e.plant),7,manifest)
    assert payload['summary']['status']=='completed' and payload['summary']['observed_until_minute']==15.,payload.get('error')
    event=next(x for x in payload['events'] if x['kind']=='m21_storage_initial_inventory')
    assert payload['m21_initial_storage']==event['initial_charged_input_kWh']
    assert payload['m21_initial_storage']['cold_store']==pytest.approx(60./.95)
    assert payload['segments'][0]['m21_storage']['cold_store']['initial_kWh']==60.
    registration=json.loads((ROOT/'configs/model_correctness_v23/cases.json').read_text(encoding='utf8'))
    factors=json.loads((ROOT/registration['emissions_factor_registry']['path']).read_text(encoding='utf8'))
    metrics=compact_metrics(payload,env=e,factor_registry=factors)
    zero=dict(payload,m21_initial_storage={'ups':0.,'cold_store':0.})
    uncharged=compact_metrics(zero,env=e,factor_registry=factors)
    expected_grid=60./.95*(e.plant.ec_electric_kW_per_rt/RT_TO_KW+e.sci_config['cold_pump_kW_per_kW'])
    from tools.execution_recovery.analysis import _grid_tariff
    expected_fee=expected_grid*_grid_tariff(payload['segments'][0],e)
    grid_factor=factors['cases'][factors['main_case_id']]['grid_kgCO2_per_kWh']
    economics=metrics['economics'];base=uncharged['economics']
    assert economics['initial_inventory_grid_input_kWh_conditional']==pytest.approx(expected_grid)
    assert economics['energy_cost_USD']-base['energy_cost_USD']==pytest.approx(expected_fee)
    assert economics['emissions_conditional']['total_CO2_kg_conditional']-base['emissions_conditional']['total_CO2_kg_conditional']==pytest.approx(expected_grid*grid_factor)
    assert economics['inventory_recharge_fee_added_twice'] is False
