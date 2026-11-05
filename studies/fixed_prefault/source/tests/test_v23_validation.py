"""Entry-point evidence and offline source facts, not another physical model."""
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def test_recovery_references_separate_external_repair_from_actual_grid_return():
    from tools.model_correctness_v23.validation import recovery_references
    def segment(a,b,power,branch=1.):
        return dict(start_minute=a,end_minute=b,action=dict(grid_import_kW=power,gt_electric_kW=[0.,0.,0.],diesel_kW=0.),
            hardware_availability={'STATION_AC':branch})
    payload=dict(scenario=dict(faults={'GRID':0.},fault_start_minute=60.,fault_duration_minutes=720.),
        segments=[segment(0.,60.,100.),segment(60.,780.,0.),segment(780.,780.5,0.),segment(780.5,900.,100.)])
    refs,facts=recovery_references(payload)
    assert refs['external_fault_clear_minute']==780.
    assert refs['actual_source_restore_minute']==dict(e=780.5,h=780.5,c=780.5)
    assert facts['reference_information_is_offline_only'] is True

def test_always_powered_grid_does_not_invent_an_electric_source_repair_for_tower_fault():
    from tools.model_correctness_v23.validation import recovery_references
    payload=dict(scenario=dict(faults={'TOWER':.01},fault_start_minute=15.,fault_duration_minutes=60.),
        segments=[dict(start_minute=0.,end_minute=90.,action=dict(grid_import_kW=100.,diesel_kW=0.,gt_electric_kW=[0.,0.,0.]),hardware_availability={'STATION_AC':1.})])
    refs,_=recovery_references(payload)
    assert refs['external_fault_clear_minute']==75.
    assert refs['actual_source_restore_minute']==dict.fromkeys('ehc','not_applicable')

def test_station_branch_restore_is_distinct_from_always_live_bulk_bus():
    from tools.model_correctness_v23.validation import recovery_references
    segs=[]
    for a,b,branch in [(0.,90.,1.),(90.,120.,0.),(120.,180.,1.)]:
        segs.append(dict(start_minute=a,end_minute=b,action=dict(grid_import_kW=100.,diesel_kW=0.,gt_electric_kW=[0.,0.,0.]),hardware_availability={'STATION_AC':branch}))
    payload=dict(scenario=dict(model_closure_events=[dict(time_minute=90.,availability={'STATION_AC':0.}),dict(time_minute=120.,availability={'STATION_AC':1.})]),segments=segs)
    refs,_=recovery_references(payload)
    assert refs['external_fault_clear_minute']==120.
    assert refs['actual_source_restore_minute']==dict(e='not_applicable',h=120.,c='not_applicable')

def test_output_never_overwrites_existing_evidence(tmp_path):
    from tools.model_correctness_v23.validation import checked_output
    existing=tmp_path/'evidence';existing.mkdir();(existing/'unique.txt').write_text('original')
    with pytest.raises(FileExistsError):checked_output(existing)
    assert (existing/'unique.txt').read_text()=='original'

def test_initial_paid_charge_is_not_initial_stored_energy():
    from types import SimpleNamespace as S
    from tools.model_correctness_v23.validation import _inventory
    store=lambda capacity:S(config=dict(capacity_kWh=capacity,initial_soc=1.),energy_kWh=0.)
    env=S(core=S(ups=store(40.),cold_store=store(60.),state=S(diesel_energy_remaining_kWh=120.)))
    payload=dict(scenario=dict(initial_reserve_kWh=150.),m21_initial_storage=dict(ups=40./.95,cold_store=60./.95),
        segments=[dict(m21_storage=dict(ups=dict(initial_kWh=40.),cold_store=dict(initial_kWh=60.)))])
    result=_inventory(env,payload)
    assert result['ups']['initial_kWh']==40.
    assert result['cold_store']['initial_kWh']==60.
    assert result['DG_shaft_budget']['final_kWh']==120.

def test_external_repair_beyond_observed_prefix_remains_unknown():
    from tools.model_correctness_v23.validation import recovery_references
    payload=dict(scenario=dict(faults={'GRID':0.},fault_start_minute=15.,fault_duration_minutes=60.),
        segments=[dict(start_minute=0.,end_minute=30.,action=dict(grid_import_kW=0.))])
    refs,facts=recovery_references(payload)
    assert refs['external_fault_clear_minute'] is None
    assert facts['external_clear_status']=='not_observed'

def test_identity_availability_map_is_not_an_external_fault():
    from tools.model_correctness_v23.validation import recovery_references
    payload=dict(scenario=dict(faults={'GRID':1.},fault_start_minute=15.,fault_duration_minutes=60.),
        segments=[dict(start_minute=0.,end_minute=90.,action=dict(grid_import_kW=100.))])
    refs,_=recovery_references(payload)
    assert refs['external_fault_clear_minute']=='not_applicable'

def test_large_raw_is_written_before_input_iteration_finishes(tmp_path):
    import hashlib
    import gzip
    import json
    from tools.model_correctness_v23.validation import write_trajectory
    path=tmp_path/'raw.json.gz'
    class ObservedRows(list):
        def __iter__(self):
            for row in super().__iter__():yield row
            # An incremental writer has already emitted meaningful body bytes.
            # A monolithic dumps(payload).encode write only emits after here.
            assert path.stat().st_size>10000
    rows=ObservedRows([hashlib.sha256(str(i).encode()).hexdigest()*80 for i in range(1500)])
    write_trajectory(dict(rows=rows,chinese='实际供能',small=[1e-12,None,False]),path)
    with gzip.open(path,'rt',encoding='utf8') as f:result=json.load(f)
    assert result['rows']==list.__getitem__(rows,slice(None))
    assert result['chinese']=='实际供能'

def test_raw_stream_does_not_overwrite_evidence_or_replace_nonfinite(tmp_path):
    from tools.model_correctness_v23.validation import write_trajectory
    path=tmp_path/'existing.gz';path.write_bytes(b'original')
    with pytest.raises(FileExistsError):write_trajectory({},path)
    assert path.read_bytes()==b'original'
    with pytest.raises(ValueError):write_trajectory(dict(value=float('nan')),tmp_path/'bad.gz')

def _inventory_fixture(initial,final,scenario):
    from types import SimpleNamespace as S
    store=lambda:S(config=dict(capacity_kWh=0.,initial_soc=0.),energy_kWh=0.)
    env=S(core=S(ups=store(),cold_store=store(),state=S(diesel_energy_remaining_kWh=final)))
    payload=dict(scenario=scenario,segments=[dict(start_minute=0.,pre_state=dict(diesel_energy_remaining_kWh=initial))])
    return env,payload

def test_default_DG_budget_comes_from_actual_initial_state_not_missing_scenario_zero():
    from tools.model_correctness_v23.validation import _inventory
    env,payload=_inventory_fixture(2400.,2400.,{})
    value=_inventory(env,payload)['DG_shaft_budget']
    assert value['initial_kWh']==2400. and value['scale_kWh']==2400. and value['final_kWh']==2400.
    assert value['initial_source']=='first_actual_pre_state'

def test_conflicting_explicit_DG_budget_is_not_silently_relabelled():
    from tools.model_correctness_v23.validation import _inventory
    env,payload=_inventory_fixture(600.,500.,dict(initial_reserve_kWh=150.))
    with pytest.raises(ValueError,match='budget'):_inventory(env,payload)

def test_real_zero_DG_budget_stays_zero():
    from tools.model_correctness_v23.validation import _inventory
    env,payload=_inventory_fixture(0.,0.,dict(initial_reserve_kWh=0.))
    value=_inventory(env,payload)['DG_shaft_budget']
    assert value['initial_kWh']==value['scale_kWh']==value['final_kWh']==0.
