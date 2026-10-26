import importlib
import importlib.util
import numpy as np
import pandas as pd
import pytest


def module():
    name='tools.model_correction_v25.thermal_ports'
    assert importlib.util.find_spec(name) is not None, 'V25 physical thermal-port checks are missing'
    return importlib.import_module(name)


ORIGINAL='''Version,26.2;
Timestep,6;
AvailabilityManagerAssignmentList,VAV_OR Availability Manager List,AvailabilityManager:NightCycle,VAV_OR Availability Manager;
Fan:VariableVolume,VAV_OR Fan,HVACOperationSchd,.61165,1314.72,AUTOSIZE;
Chiller:Electric:ReformulatedEIR,CoolSys1 Chiller1,721302.75;
Chiller:Electric:ReformulatedEIR,CoolSys1 Chiller2,721302.75;
EnergyManagementSystem:Actuator,Chiller2Status,COOLSYS1 CHILLER2,Plant Component Chiller:Electric:ReformulatedEIR,On/Off Supervisory;
PlantEquipmentOperationSchemes,CoolSys1 Loop Operation Scheme List,PlantEquipmentOperation:CoolingLoad,CoolSys1 Operation Scheme,PlantOnSched;
PlantEquipmentOperationSchemes,Heat Recovery Chiller Bypass Loop Operation,PlantEquipmentOperation:CoolingLoad,Heat Recovery Chiller,PlantOnSched;
PlantEquipmentList,CoolSys1 Equipment List 1,Chiller:Electric:ReformulatedEIR,CoolSys1 Chiller1;
'''


def test_partial_source_limit_uses_real_dispatch_eligibility_and_no_fraction_or_derating():
    # The earlier test assumed an appended EMS0 guaranteed off. Actual EP
    # qualification falsified that assumption; keep its failed evidence and
    # require explicit equipment eligibility plus the unchanged actual0 gate.
    out=module().port_idf(ORIGINAL,'one_main_source_off')
    assert 'Chiller:Electric:ReformulatedEIR,CoolSys1 Chiller1,721302.75;' in out
    assert 'Chiller:Electric:ReformulatedEIR,CoolSys1 Chiller2,721302.75;' in out
    assert 'V25_LIMITED_OPERATION' in out
    assert 'V25_PORT_LIMIT_ACTIVE' in out
    assert 'CoolSys1 Equipment List 1' in out
    assert 'SET Chiller2Status = 0.5' not in out
    assert 'Coil:Cooling:Water' not in out


def test_fan_intervention_preserves_original_fan_parameters_and_adds_forceoff_manager():
    out=module().port_idf(ORIGINAL,'or_fan_path_off')
    assert 'Fan:VariableVolume,VAV_OR Fan,HVACOperationSchd,.61165,1314.72,AUTOSIZE;' in out
    assert 'AvailabilityManager:NightCycle' in out
    assert 'AvailabilityManager:ScheduledOff' in out
    assert 'V25_OR_ForcedOff' in out


def test_thermal_port_uses_net_HminusC_and_multiplier_exactly_once():
    frame=pd.DataFrame(dict(dt_h=[.05,.1,1/60],heating_kW=[1.,0.,2.],cooling_kW=[3.,4.,0.]))
    result=module().port_integrals(frame,multiplier=5.)
    assert result['representative_net_sensible_kWh']==pytest.approx(.05+2/60-.15-.4)
    assert result['physical_group_net_sensible_kWh']==pytest.approx(5*result['representative_net_sensible_kWh'])
    assert result['representative_cooling_kWh']==pytest.approx(.55)
    assert result['representative_heating_kWh']==pytest.approx(.05+2/60)


@pytest.mark.parametrize('multiplier',[0.,-1.,np.inf])
def test_invalid_physical_multiplier_rejected(multiplier):
    with pytest.raises(ValueError):
        module().port_integrals(pd.DataFrame(dict(dt_h=[1/6],heating_kW=[0.],cooling_kW=[2.])),multiplier=multiplier)


def test_actual_off_command_without_actual_zero_or_restoration_cannot_qualify():
    m=module()
    assert not m.port_qualification('one_main_source_off',{'source2':644.2},1.,{'OR1':1.},1.,100.,1e-13)
    assert not m.port_qualification('or_fan_path_off',{},0.,{'OR1':0.},0.,100.,1e-13)
    assert not m.port_qualification('all_cold_sources_off',{'source1':0.,'source2':0.,'hrc':0.},1.,{'OR1':1.},1.,0.,1e-13)
    assert m.port_qualification('all_cold_sources_off',{'source1':0.,'source2':0.,'hrc':0.},1.,{'OR1':1.},1.,100.,1e-13)


def test_fan_restore_requires_actual_positive_flow_to_every_or():
    m=module()
    zones={z:0. for z in ('OR1_FLR_2','OR2_MULT5_FLR_2','OR3_FLR_2','OR4_FLR_2')}
    restored={z:1. for z in zones}
    assert m.port_qualification('or_fan_path_off',{},0.,zones,4.,100.,1e-13,restored_zoneflows=restored)
    restored['OR4_FLR_2']=0.
    assert not m.port_qualification('or_fan_path_off',{},0.,zones,4.,100.,1e-13,restored_zoneflows=restored)
