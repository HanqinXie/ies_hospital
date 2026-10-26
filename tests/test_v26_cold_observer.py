import importlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]


def module(name):
    try:
        return importlib.import_module('tools.model_correction_v26.'+name)
    except ModuleNotFoundError:
        pytest.fail('V26 complete topology/observer capability is not implemented')


def test_real_idf_maps_all_cold_coils_hx_sides_and_condenser_recovery_boundaries():
    m=module('cold_topology')
    result=m.cold_topology((ROOT/'inputs/energyplus/Hospital_SanDiego.idf').read_text(encoding='utf-8-sig'))
    assert len(result['cold_loops'])==3
    assert len(result['cooling_coils'])==8
    assert len(result['pumps'])==4
    assert set(x['name'] for x in result['heat_exchangers'])=={'Bypass Heat Exchanger','Heat Recovery Chiller Bypass Heat Exchanger'}
    bypass=next(x for x in result['heat_exchangers'] if x['name']=='Bypass Heat Exchanger')
    assert bypass['api_demand_loop']=='CoolSys1_Supply'
    assert bypass['api_supply_loop']=='CoolSys1_Demand'
    assert {x['loop'] for x in result['cross_boundary_components']}=={'TowerWaterSys','Heat Recovery Water Loop'}
    assert any(x['type']=='Humidifier:Steam:Electric' for x in result['or_air_components'])


def test_observer_only_adds_outputs_and_advanced_diagnostic_without_duplicate_diagnostics():
    m=module('cold_observer')
    source='Version,26.2;\nTimestep,6;\nOutput:Diagnostics,DisplayExtraWarnings;\nCoil:Cooling:Water,KEEP,AlwaysOn,1;'
    channels={'one':dict(key='CoolSys1_Demand',variable='Plant Demand Side Lumped Capacitance Heat Storage Rate',unit='W',native_clock='System')}
    out=m.append_observers(source,channels)
    assert 'Coil:Cooling:Water,KEEP,AlwaysOn,1;' in out
    assert 'Timestep,6;' in out
    assert out.lower().count('output:diagnostics,')==1
    assert 'DisplayExtraWarnings' in out
    assert 'DisplayAdvancedReportVariables' in out
    assert m.physics_objects(source)==m.physics_objects(out)


def test_observer_field_set_covers_every_coil_advanced_tank_and_air_path():
    t=module('cold_topology').cold_topology((ROOT/'inputs/energyplus/Hospital_SanDiego.idf').read_text(encoding='utf-8-sig'))
    channels=module('cold_observer').observation_channels(t)
    pairs={(x['key'].lower(),x['variable']) for x in channels.values()}
    for coil in t['cooling_coils']:
        assert (coil['name'].lower(),'Cooling Coil Total Cooling Rate') in pairs
        assert (coil['inlet'].lower(),'System Node Enthalpy') in pairs
        assert (coil['outlet'].lower(),'System Node Enthalpy') in pairs
    for loop in t['cold_loops']:
        for side in ['Demand','Supply']:
            assert (loop['name'].lower(),f'Plant {side} Side Lumped Capacitance Heat Storage Rate') in pairs
    assert ('vav_or humidifier','Humidifier Electricity Rate') in pairs
    assert ('vav_or fan','Fan Heat Gain to Air') in pairs
    assert ('or2_mult5_flr_2','Zone Air Heat Balance System Air Transfer Rate') in pairs


def test_completed_clock_retains_exact_nonuniform_duration_and_rejects_unknown_tail():
    m=module('cold_observer')
    source=pd.DataFrame(dict(bucket_end=pd.to_datetime(['2006-07-20T12:10:00']*2),dt_h=[1/18,1/9],substep_index=[0,1],q=[1.,2.]))
    out=m.completed_clock(source)
    np.testing.assert_array_equal(out.dt_h,source.dt_h)
    assert out.substep_start_h.tolist()==pytest.approx([0.,1/18])
    assert out.substep_end_h.tolist()==pytest.approx([1/18,1/6])
    with pytest.raises(ValueError):
        m.completed_clock(source.iloc[:1])


def test_all_nine_runs_are_registered_and_shared_july_normal_is_explicit():
    m=module('cold_observer')
    plan=m.case_plan(Path('READ_ONLY_V25'))
    assert len(plan)==9
    assert sum(x['normal_for_tolerance'] for x in plan)==2
    july=next(x for x in plan if x['case_id']=='native10_cal_summer_0h')
    assert july['source_idf'].as_posix().endswith('ports/normal/derived.idf')
    assert july['also_port_normal'] is True
    assert len({x['case_id'] for x in plan})==9


def test_no_constrained_case_can_launch_before_immutable_normal_tolerance_gate():
    m=module('cold_observer')
    assert m.require_normal_gate({'normal_for_tolerance':True},None,'contract') is None
    with pytest.raises(ValueError,match='normal'):
        m.require_normal_gate({'normal_for_tolerance':False},None,'contract')
    with pytest.raises(ValueError):
        m.require_normal_gate({'normal_for_tolerance':False},{'channel_contract_sha256':'other'},'contract')


def test_original_output_dictionary_is_not_duplicated_and_internal_mass_is_observed():
    m=module('cold_observer')
    original='Version,26.2;Output:VariableDictionary,IDF;'
    assert m.append_observers(original,{}).lower().count('output:variabledictionary,')==1
    t=module('cold_topology').cold_topology((ROOT/'inputs/energyplus/Hospital_SanDiego.idf').read_text(encoding='utf-8-sig'))
    mass=[x for x in t['or_surfaces'] if x[0].lower()=='internalmass']
    assert len(mass)==4
    channels=m.observation_channels(t)
    for surface in mass:
        assert any(x['key']==surface[1] and x['variable']=='Surface Inside Face Temperature' for x in channels.values())


def test_old_actual_port_flows_and_heat_are_preserved_with_their_original_units():
    m=module('cold_observer')
    new=pd.DataFrame(dict(dt_h=[1/6],q_W=[1000.],mass=[2.]))
    old=pd.DataFrame(dict(dt_h=[1/6],q_kW=[1.],mass_kg_s=[2.]))
    channels={'q_W':dict(key='coil',variable='heat'),'mass':dict(key='coil',variable='mass')}
    previous={'q_kW':('coil','heat',1000.),'mass_kg_s':('coil','mass',1.)}
    assert m.compare_original_ports(new,old,channels,previous)['q_kW']==0.
    old.loc[0,'mass_kg_s']=3.
    with pytest.raises(ValueError):m.compare_original_ports(new,old,channels,previous)


def test_mean_radiant_temperature_has_zone_clock_despite_system_callback_sampling():
    m=module('cold_observer')
    topology=module('cold_topology').cold_topology((ROOT/'inputs/energyplus/Hospital_SanDiego.idf').read_text(encoding='utf-8-sig'))
    channels=m.observation_channels(topology)
    radiant=[c for c in channels.values() if c['variable']=='Zone Mean Radiant Temperature']
    assert len(radiant)==4
    assert all(c['native_clock']=='Zone' for c in radiant)
    assert all('zone-held' in c['scope'] and 'system callback' in c['scope'] for c in radiant)
    legacy={k:dict(c,native_clock='System',scope='representative zone except native system term')
            if c['variable']=='Zone Mean Radiant Temperature' else c.copy() for k,c in channels.items()}
    source='Version,26.2;\nTimestep,6;\n'
    assert m.append_observers(source,channels)==m.append_observers(source,legacy)
