"""Pure input construction only; no Hospital initialization, episode or solver."""
from pathlib import Path
import builtins
import copy
import io
import json
import shutil
import sys
from unittest.mock import patch
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def api():
    from tools.model_reference_v32 import inputs
    return inputs

def initial(mode='legacy_explicit_permission'):
    return dict(initial_stocks=dict(ups=40.,cold_store=0.),diesel_shaft_budget_kWh=2400.,
        initial_state={'synthetic_fixture':True},
        execution_contract=dict(diesel_mode=mode,diesel_allowed_priorities=['P0','P1']),
        provenance=dict(origin='synthetic_test_fixture',not_historical_trajectory=True))

@pytest.fixture(scope='module')
def pair():
    return {b:api().build_hospital_spec(ROOT,branch=b,initial_snapshot=initial()) for b in ['normal','grid']}

def test_missing_initial_certificate_fails_before_input_reads(monkeypatch):
    def fail(*a,**k):raise AssertionError('No input read permitted without explicit initial certificate')
    with patch('builtins.open',fail),patch('io.open',fail):
        with pytest.raises(ValueError,match='initial'):api().build_hospital_spec(ROOT,initial_snapshot=None)

def test_source_clock_and_grid_are_registered_not_native(pair):
    mod=api();assert mod.source_hour_at(1160,0)==1160
    assert mod.source_hour_at(1160,59.999)==1160
    assert mod.source_hour_at(1160,60)==1161
    assert mod.source_hour_at(1160,180)==1163
    assert mod.planning_grid(180,15,[62.5])==sorted(set(range(0,181,15))|{62.5})
    for s in pair.values():
        assert s['time_grid_minutes']==list(range(0,181,15))
        assert s['source_hours_by_period']==[1160]*4+[1161]*4+[1162]*4
        assert s['source_terminal_hour']==1163
        assert s['duration_h']==[.25]*12
        assert len(s['original_demand_by_priority_kW'])==12
        assert s['provenance']['grid_source']=='decision_clock+raw_hourly+registered_exogenous_event_edges'

def test_priority_and_building_totals_are_conservative(pair):
    frame=pd.read_csv(ROOT/'inputs/loads/hospital_ies_demand_8760.csv')
    thermal=pd.read_csv(ROOT/'inputs/model_review_v28/THERMAL_SERVICE_COMPONENTS_8760.csv')
    s=pair['normal'];dem=np.array(s['original_demand_by_priority_kW'])
    for i,ledger in enumerate(s['demand_ledger']):
        h=s['source_hours_by_period'][i];row=frame.iloc[h-1];target=thermal.iloc[h-1]
        raw=np.array(ledger['raw_group_loads_before_aux_kW'])
        np.testing.assert_allclose(raw.sum(0),[row.P_e_nonplant_kW+row.P_e_unresolved_kW,row.Q_h_demand_kW,row.Q_c_demand_kW],rtol=0,atol=1e-8)
        np.testing.assert_allclose(dem[i].sum(0),[s['demand_e'][i],s['demand_h'][i],s['demand_c'][i]],rtol=0,atol=1e-8)
        assert abs(dem[i,:,1].sum()-target.central_heat_request_kW)<1e-8
        assert abs(dem[i,:,2].sum()-row.Q_c_demand_kW)<1e-8
        assert ledger['source_timestamp']==str(pd.Timestamp(row.timestamp))

def test_direct_aux_and_DHW_fan_boundary_are_not_double_counted(pair):
    s=pair['normal'];dem=np.array(s['original_demand_by_priority_kW'])
    direct=np.array(s['electric_direct_by_priority_kW']);aux=np.array(s['electric_auxiliary_by_priority_kW'])
    np.testing.assert_allclose(dem[:,:,0],direct+aux,rtol=0,atol=1e-10)
    changed_DHW=False
    for i,ledger in enumerate(s['demand_ledger']):
        req=ledger['direct_aux_request'];components=np.array(req['auxiliary_request_kW'])
        np.testing.assert_allclose(aux[i],components.sum(1),rtol=0,atol=1e-10)
        np.testing.assert_allclose(components[:,0],np.array(req['auxiliary_reference_kW'])[:,0],rtol=0,atol=1e-10)
        np.testing.assert_allclose(components[:,1],np.array(req['auxiliary_reference_kW'])[:,1],rtol=0,atol=1e-10)
        changed_DHW |= abs(req['ordinary_DHW_target_kW']-req['ordinary_DHW_legacy_delivered_kW'])>1e-4
        assert req['ordinary_DHW_target_source_trace']['boundary']=='ordinary_wateruse_central_target_v28'
    assert changed_DHW,'This real source window must exercise adopted target versus old delivered DHW'

def test_normal_grid_share_raw_demands_and_only_exogenous_grid_changes(pair):
    n,g=pair['normal'],pair['grid']
    assert n['original_demand_by_priority_kW']==g['original_demand_by_priority_kW']
    assert n['grid_capacity']==[850.]*12
    assert g['grid_capacity']==[850.]*4+[0.]*4+[850.]*4
    assert all(v==1. for a in g['exogenous_availability_by_period'] for k,v in a.items() if k!='GRID')
    assert g['information_permission']=='offline_known_window'
    assert g['provenance']['historical_operating_results_read'] is False

def test_H1_storage_and_DG_match_actual_permission_no_old_critical_cap(pair):
    s=pair['grid'];stores={x['name']:x for x in s['storages']};dg=next(x for x in s['generators'] if x['name']=='DG')
    assert stores['ups']['capacity']==40 and stores['ups']['power']==160 and stores['ups']['initial']==40
    assert stores['ups']['discharge_scope']=='building_critical_only' and stores['ups']['eligible_priorities']==[0,1]
    assert stores['cold_store']['capacity']==stores['cold_store']['initial']==0
    assert s['fuel_budgets']=={'diesel_shaft':2400.}
    assert 'electric_service_limit' not in dg and dg.get('electric_service_scope')!='building_critical_only'
    assert dg['capacity']==[0.]*4+[900.]*4+[0.]*4
    assert s['initialization_provenance']['origin']=='synthetic_test_fixture'

def test_emergency_bus_explicitly_uses_registered_priority_scope():
    s=api().build_hospital_spec(ROOT,branch='grid',initial_snapshot=initial('emergency_bus'))
    dg=next(x for x in s['generators'] if x['name']=='DG')
    assert dg['electric_service_scope']=='building_critical_only' and dg['eligible_priorities']==[0,1]

def test_independent_common_initialization_source_provenance_is_preserved():
    certificate=initial()
    certificate['provenance']=dict(source='independent common initialization',completed_policy_results_read=False,
        new_hospital_episodes=0,C_M_decision_calls=0)
    s=api().build_hospital_spec(ROOT,branch='normal',initial_snapshot=certificate)
    assert s['initialization_provenance']==certificate['provenance']
    assert 'origin' not in s['initialization_provenance']

def test_nonzero_initial_time_is_rejected_without_reading_sources():
    certificate=initial();certificate['time_minute']=60.
    with pytest.raises(ValueError,match='t=0'):
        api().build_hospital_spec(ROOT,branch='normal',initial_snapshot=certificate)

def test_isolated_raw_copy_builds_with_global_result_read_denial(tmp_path):
    root=tmp_path/'raw_only';root.mkdir()
    for folder in ['inputs','configs']:
        shutil.copytree(ROOT/folder,root/folder)
    seen=[];original_builtin=builtins.open;original_io=io.open
    def guard(op):
        def wrapped(file,*args,**kwargs):
            if isinstance(file,(str,bytes,Path)):
                p=Path(file);parts=[x.casefold() for x in p.parts]
                if any(x in ('outputs','runs','trajectory.json.gz','result.json','actual_prefix.jsonl') for x in parts):
                    raise AssertionError('Forbidden operating-result read: '+str(file))
                seen.append(str(file))
            return op(file,*args,**kwargs)
        return wrapped
    mod=api()
    with patch('builtins.open',guard(original_builtin)),patch('io.open',guard(original_io)):
        n=mod.build_hospital_spec(root,branch='normal',initial_snapshot=initial())
        g=mod.build_hospital_spec(root,branch='grid',initial_snapshot=initial())
    assert seen and n['demand_e']==g['demand_e']
    assert not (root/'outputs').exists() and not (root/'runs').exists()
    assert all(not any(x in ('outputs','runs') for x in Path(p).parts) for p in seen)
