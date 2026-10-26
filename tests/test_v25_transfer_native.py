"""Bounded native-input transfer, with actual paid DG startup and boiler work."""
import copy
import json
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


@pytest.mark.parametrize('enabled',[True,False])
def test_explicit_finite_source_return_configuration_constructs(enabled):
    from tools.model_correction_v25.environment import OperatingHospitalIESEnv
    from tools.operating_response.data_design import normal_proposal,SEALED_DAYS
    from tools.sci_engineering.design import allowed_hours
    scene=copy.deepcopy(normal_proposal()['scenarios'][0])
    env=OperatingHospitalIESEnv(ROOT,allowed_hours=allowed_hours(dict(scenarios=[scene])),
        sealed_days=SEALED_DAYS,model_config={'model_correction_v25':{'finite_source_return':enabled}})
    assert env.model_correction_config['finite_source_return'] is enabled
    assert env.operating_model_config['model_correction_v25']['finite_source_return'] is enabled


def test_native_finite_return_keeps_open_gap_and_paid_restart(tmp_path):
    from tools.model_correction_v25.environment import OperatingHospitalIESEnv
    from tools.operating_response.data_design import normal_proposal,SEALED_DAYS
    from tools.sci_engineering.design import allowed_hours
    from test_execution_recovery_core import arrive
    scene=copy.deepcopy(normal_proposal()['scenarios'][0])
    scene.update(duration_steps=6,stage=1,faults={'GRID':0.,'GT_1':0.,'GT_2':0.,'GT_3':0.},
        fault_start_minute=0.,fault_duration_minutes=40.,response_delay_minutes=0.,enable_propagation=False)
    first=scene['start_hour']
    env=OperatingHospitalIESEnv(ROOT,allowed_hours=allowed_hours(dict(scenarios=[scene])),sealed_days=SEALED_DAYS)
    env.reset(scenario=scene,seed=20261026);core=env.core
    env.state.diesel_energy_remaining_kWh=300.
    arrive(core,grid=400.,diesel=500.)
    core.pending_commands[-1]['dispatch']['boiler_steam_lbph']=300.
    core.advance(75.)
    segments=core.segments
    report=dict(time_minute=core.time,events=core.events,segments=segments,
        errors=core.errors,final_diesel_energy_kWh=env.state.diesel_energy_remaining_kWh)
    from tools.model_correctness_v23.export import json_clean
    (tmp_path/'native_transfer.json').write_text(json.dumps(json_clean(report),ensure_ascii=False,indent=2),encoding='utf-8')
    assert core.time==75. and not env.solver_errors
    assert all(s['action']['grid_import_kW']<1e-8 or s['action']['diesel_kW']<1e-8 for s in segments)
    prepared=[e for e in core.events if e['kind']=='grid_bus_state_changed' and e['after']=='RETURN_PREP']
    opened=[e for e in core.events if e['kind']=='grid_bus_state_changed' and e['after']=='TRANSFER_OPEN']
    closed=[e for e in core.events if e['kind']=='grid_bus_state_changed' and e['after']=='GRID']
    assert prepared and opened and closed
    assert opened[0]['time_minute']-prepared[0]['time_minute']==pytest.approx(.25)
    assert closed[0]['time_minute']-opened[0]['time_minute']==pytest.approx(.25)
    open_start=opened[0]['time_minute'];close_time=closed[0]['time_minute']
    gap=[s for s in segments if s['start_minute']>=open_start-1e-9 and s['end_minute']<=close_time+1e-9]
    assert sum(s['end_minute']-s['start_minute'] for s in gap)==pytest.approx(.25)
    assert all(s['action']['grid_import_kW']==s['action']['diesel_kW']==0. for s in gap)
    prep=[s for s in segments if s['start_minute']>=prepared[0]['time_minute']-1e-9 and s['end_minute']<=open_start+1e-9]
    assert prep and all(s['action']['diesel_kW']>0. for s in prep)
    assert not any(e['kind']=='diesel_fuel_exhausted' and e['time_minute']<=open_start for e in core.events)
    after=[s for s in segments if s['start_minute']>=close_time-1e-9 and s['action'].get('boiler_restart_electric_kW',0.)>0.]
    assert after
    assert after[0]['action']['boiler_restart_electric_kW']==pytest.approx(.5)
    assert sum(s['end_minute']-s['start_minute'] for s in after)==pytest.approx(1.)
    assert sum(s['diesel_local_response']['mechanical_work_kWh'] for s in segments)==pytest.approx(300.-env.state.diesel_energy_remaining_kWh,abs=1e-7)
    assert all(max(map(abs,s['energy_balance_residuals']))<1e-4 for s in segments)
