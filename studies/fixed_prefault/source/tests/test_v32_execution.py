"""V32 registry/initialization/request identity contracts; no hospital episode."""
import copy,importlib,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
def api(name):
 try:return importlib.import_module('tools.model_reference_v32.'+name)
 except ModuleNotFoundError as error:pytest.fail('V32 independent execution component missing: '+str(error))

@pytest.mark.parametrize('branch,mode',[('normal','normal'),('grid','disturbed')])
def test_registration_is_actual_b01_and_not_old_dev_default(branch,mode):
 m=api('snapshot');case,registry=m.load_frozen_case(ROOT,branch)
 assert case['id']=='b01_L1_grid_'+mode+'_M'
 assert case['scenario']['start_hour']==1160 and case['configuration']=='H1'
 assert case['scenario']['duration_steps']*case['scenario']['step_minutes']==180
 assert case['scenario']['shock_by_zone']=={}
 assert case['scenario']['faults']==({} if branch=='normal' else {'GRID':0.})
 cfg,rule=m.physical_configuration(case,registry)
 assert cfg['sci_engineering']['ups']['capacity_kWh']==40.
 assert cfg['sci_engineering']['cold_store']=={}
 assert cfg['model_correctness_v23']['thermal_coupling'] is None
 assert rule['name']=='current_service_bridge'
 with pytest.raises(ValueError):m.load_frozen_case(ROOT,'dev_grid_180')

def test_configuration_does_not_mutate_frozen_registry():
 m=api('snapshot');case,reg=m.load_frozen_case(ROOT,'grid');before=copy.deepcopy((case,reg))
 cfg,rule=m.physical_configuration(case,reg)
 cfg['sci_engineering']['ups']['capacity_kWh']=999.
 assert (case,reg)==before

def minimal_spec():
 return dict(duration_h=[.25,.25],start_minute=0.,generators=[dict(name='GT_1',capacity=[100.,100.],fuel_slope=.01,fuel_fixed=0.)],
  boiler={'capacity':[50.,50.]},electric_chillers=[dict(name='EC_1',tower_per_c=1.2)],absorption={'tower_per_c':2.},
  storages=[{'name':'ups','carrier':'e'}],dispatch_mapping=dict(gt_names=['GT_1',None,None],dg_name=None,ec_names=['EC_1',None,None],
   boiler_heat_to_steam=2.,gt_steam_per_e=[[3.,3.],[0.,0.],[0.,0.]]))
def minimal_plan():
 p={'GT_1':[0.,80.],'GT_1.on':[0.,1.],'boiler':[0.,10.],'boiler.on':[0.,1.],'EC_1':[20.,30.],'ABS':[5.,6.],
    'grid':[90.,0.],'ups.charge':[0.,0.],'ups.discharge':[0.,10.],'ups.stock':[40.,37.5]}
 for g in range(4):
  for c in 'ehc':p[f'unmet.P{g}.{c}']=[0.,0.]
 return p

def test_request_keeps_unexecuted_storage_unmet_and_fixed_preparation(monkeypatch):
 m=api('actions');monkeypatch.setattr(m,'audit_plan',lambda *a,**k:{'valid':True})
 plan=minimal_plan();plan['unmet.P3.e'][1]=2.
 r=m.extract_requests(minimal_spec(),plan,formulation='tiered',preparation_lead_minutes=45.)
 assert r['information_permission']=='offline_known_window'
 assert r['full_continuous_plan_replayed'] is False and r['planned_storage_ports_executed'] is False
 one=r['requests'][0];d=one['action']['ordinary_dispatch']['dispatch']
 assert d['gt_electric_kW']==[80.,0.,0.] and d['boiler_steam_lbph']==20.
 assert one['action']['ordinary_dispatch']['disconnected_kW']==[[0.,0.,0.]]*4
 assert 'ups.stock' in one['unexecuted_storage_plan']
 assert r['requests'][1]['unexecuted_unmet_plan']['P3']['e']==2.
 assert one['requested_dispatch_is_not_execution_receipt'] is True
 assert plan['GT_1'][0]==0.

def test_invalid_or_no_plan_is_never_converted_to_zero_request(monkeypatch):
 m=api('actions');monkeypatch.setattr(m,'audit_plan',lambda *a,**k:{'valid':False})
 with pytest.raises(ValueError):m.extract_requests(minimal_spec(),minimal_plan(),formulation='strict')
 with pytest.raises(ValueError):m.extract_requests(minimal_spec(),None,formulation='strict')

def test_reference_controller_is_explicit_offline_and_never_a_C_M_fallback(tmp_path):
 m=api('reference');from tools.model_reference_v32 import METHOD_ID,EXECUTION_ID
 schedule=dict(method_id=METHOD_ID,execution_identity=EXECUTION_ID,information_permission='offline_known_window',
  full_continuous_plan_replayed=False,planned_storage_ports_executed=False,
  requests=[{'time_minute':0.,'action':{'ordinary_dispatch':{'test_marker':1}}},{'time_minute':15.,'action':{'ordinary_dispatch':{'test_marker':2}}}])
 p=tmp_path/'actions.json';p.write_text(json.dumps(schedule),encoding='utf8')
 with pytest.raises(ValueError):m.OfflinePlanPolicy('M',config={'actions_path':str(p)})
 controller=m.OfflinePlanPolicy(METHOD_ID,config={'actions_path':str(p)},policy_id=METHOD_ID)
 action,diagnostic=controller.decide([{'decision_time_min':15.}])
 assert action['ordinary_dispatch']['test_marker']==2 and diagnostic['strategy_fallback'] is False
 assert diagnostic['information_permission']=='offline_known_window'
 assert diagnostic['full_continuous_plan_replayed'] is False
 schedule['information_permission']='causal_online';p.write_text(json.dumps(schedule),encoding='utf8')
 with pytest.raises(ValueError):m.OfflinePlanPolicy(METHOD_ID,config={'actions_path':str(p)})
