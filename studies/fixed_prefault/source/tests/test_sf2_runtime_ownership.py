"""Pure accepted-result/receipt ownership; no physical or optimizer execution."""
from copy import deepcopy
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_sf2_runtime import path_core,guarded_core
from tools.single_fault_fuel_20261102.fuel import digest,FuelCurve
from tools.single_fault_fuel_20261102.leaf import receipt

def owned_result():
    core,curve=path_core()
    _,start,end=core._local_interval_action(16.,{},core.data_at(15.)[1])
    action,_,_=core._local_interval_action(16.,{},core.data_at(15.)[1])
    path=action['_sf2_fuel_path'];raw=np.r_[path.mean_kW,np.zeros(44)].tolist()
    meter=receipt(curve,path,'actual_interval',raw,path.hours,15.,1.21)
    segment={'start_minute':15.,'end_minute':16.,'source_start_gt_kW':start.tolist(),
        'source_end_gt_kW':end.tolist(),'action':{'gt_electric_kW':list(path.mean_kW),
        'gt_fuel_MMBtu_h':sum(meter['average_running_fuel_MMBtu_h'])}}
    core.single_fault_metadata=lambda:{'physical_model_id':'SF2'}
    result={'_sf2_fuel_receipt':meter,'sf2_physical_operating_fee_USD_step':1.,'sf2_artificial_unused_steam_tie_USD_step':0.,
        'solver_backend_layers':[{'layer':'cost','backend':'gurobipy','status':0,
            'attempts':[{'accepted':True,'status':0,'native_status':2,'raw_x':deepcopy(raw)}]}]}
    return core,segment,result,raw

def test_matching_complete_native_result_and_receipt_can_commit():
    core,segment,result,raw=owned_result()
    core._after_actual_segment(segment,result)
    assert segment['sf2_running_fuel']['accounting_committed'] is True
    assert segment['sf2_running_fuel']['accepted_raw_x']==raw

@pytest.mark.parametrize('index',[13,14,42])
def test_meter_cannot_self_certify_a_different_full_native_primal(index):
    core,segment,result,raw=owned_result()
    meter=result['_sf2_fuel_receipt']
    meter['accepted_raw_x'][index]+=1.
    meter['accepted_raw_x_sha256']=digest(meter['accepted_raw_x'])
    assert meter['accepted_raw_x']!=result['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    assert meter['accepted_raw_x'][:3]==raw[:3]
    with pytest.raises(ValueError,match='SF2|primal|native|accepted'):
        core._after_actual_segment(segment,result)
    assert 'sf2_running_fuel' not in segment
    assert 'inherited_called' not in segment

def test_same_method_object_with_changed_running_fuel_code_is_rejected(monkeypatch):
    core=guarded_core();core._check_sf2_model()
    original=FuelCurve.rate
    def changed(self,power_kW,temperature_C):return 0.
    monkeypatch.setattr(original,'__code__',changed.__code__)
    assert FuelCurve.rate is original
    assert core.env.single_fault_fuel_curve.rate(128.,15.)==0.
    with pytest.raises(ValueError,match='SF2.*loaded|code|method'):
        core._check_sf2_model()


@pytest.mark.parametrize('change',['missing','not_cost','ambiguous','not_accepted','layer_bool','attempt_bool','native_error'])
def test_receipt_needs_one_typed_successful_final_native_owner(change):
    core,segment,result,raw=owned_result()
    final=result['solver_backend_layers'][-1]
    if change=='missing':result['solver_backend_layers']=[]
    elif change=='not_cost':final['layer']='P3'
    elif change=='ambiguous':final['attempts'].append(deepcopy(final['attempts'][0]))
    elif change=='not_accepted':final['attempts'][0]['accepted']=False
    elif change=='layer_bool':final['status']=False
    elif change=='attempt_bool':final['attempts'][0]['status']=False
    else:final['attempts'][0]['error']='reported native failure'
    with pytest.raises(ValueError,match='SF2|native|accepted'):
        core._after_actual_segment(segment,result)
    assert 'sf2_running_fuel' not in segment and 'inherited_called' not in segment


def test_in_place_function_defaults_mutation_is_rejected(monkeypatch):
    from hospital_ies.model import solve_hour
    core=guarded_core();core._check_sf2_model()
    monkeypatch.setattr(solve_hour,'__defaults__',(12.,))
    with pytest.raises(ValueError,match='SF2.*loaded|code|defaults'):
        core._check_sf2_model()
