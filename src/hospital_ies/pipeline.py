"""Load, validate, dispatch and report the DOE+NMCSD synthetic case."""
from __future__ import annotations

import json
from pathlib import Path
import warnings

import numpy as np
import pandas as pd

from .model import load_plant, solve_hour, RT_TO_KW
from .demand_extensions import attach_demand_extensions, oxygen_profile
from .thermal_inertia import simulate_cooling_interruption

REQUIRED = ['timestamp', 'hour_of_year', 'P_e_nonplant_kW', 'P_e_unresolved_kW',
            'Q_h_demand_kW','Q_c_demand_kW','outdoor_temperature_C']


def read_data(root: Path) -> pd.DataFrame:
    dpath = root / 'inputs/loads'
    primary = pd.read_csv(dpath / 'hospital_ies_demand_8760.csv', parse_dates=['timestamp'])
    if len(primary) != 8760:
        raise ValueError(f'Expected 8760 hours; got {len(primary)}')
    for k in REQUIRED:
        if k not in primary:
            raise ValueError(f'Input missing {k}')
        if primary[k].isna().any():
            raise ValueError(f'Input contains missing values in {k}')
    if primary.hour_of_year.astype(int).tolist() != list(range(1,8761)):
        raise ValueError('hour_of_year is not 1..8760')
    if primary.timestamp.duplicated().any() or (primary.timestamp.diff().iloc[1:] != pd.Timedelta(hours=1)).any():
        raise ValueError('Time index is not consecutive hourly')
    eb = pd.read_csv(dpath / 'electricity_boundary_8760.csv')
    tb = pd.read_csv(dpath / 'thermal_boundary_8760.csv')
    if len(eb)!=8760 or len(tb)!=8760:
        raise ValueError('Boundary source files must have 8760 rows')
    if not (primary.timestamp.values == pd.to_datetime(eb.timestamp).values).all():
        raise ValueError('Electricity table not aligned with main demand')
    if not (primary.timestamp.values == pd.to_datetime(tb.timestamp).values).all():
        raise ValueError('Thermal table not aligned with main demand')
    checks = {
        'electricity_facility':(primary.P_e_facility_kW.to_numpy(),eb.P_e_facility_kW.to_numpy()),
        'nonplant':(primary.P_e_nonplant_kW.to_numpy(),eb.P_e_nonplant_kW.to_numpy()),
        'plant':(primary.P_e_plant_kW.to_numpy(),eb.P_e_plant_kW.to_numpy()),
        'unresolved':(primary.P_e_unresolved_kW.to_numpy(),eb.P_e_unresolved_kW.to_numpy()),
        'cooling':(primary.Q_c_demand_kW.to_numpy(),tb.Q_c_terminal_coils_kW.to_numpy()),
        'space_heat':(primary.Q_h_heating_coils_kW.to_numpy(),tb.Q_h_heating_coils_kW.to_numpy()),
        'DHW':(primary.Q_h_DHW_water_use_kW.to_numpy(),tb.Q_h_DHW_water_use_kW.to_numpy()),
    }
    for name,(a,b) in checks.items():
        if not np.allclose(a,b,rtol=1e-7,atol=1e-5):
            raise ValueError(f'Conflicting inputs: {name}')
    if np.max(np.abs((primary.P_e_nonplant_kW+primary.P_e_plant_kW+
                       primary.P_e_unresolved_kW-primary.P_e_facility_kW).to_numpy()))>1e-5:
        raise ValueError('EnergyPlus electric boundary does not balance')
    if np.max(np.abs((primary.Q_h_heating_coils_kW+primary.Q_h_DHW_water_use_kW-
                       primary.Q_h_demand_kW).to_numpy()))>1e-5:
        raise ValueError('EnergyPlus heat decomposition does not balance')
    z = pd.read_csv(dpath / 'critical_zones_8760.csv',usecols=['zone_name','hour_of_year'])
    if z.zone_name.nunique()!=14 or z.groupby('zone_name').hour_of_year.nunique().min()!=8760:
        raise ValueError('Critical zone coverage incomplete')
    return primary


def annual_summary(results: pd.DataFrame, case: str, plant) -> dict:
    es = results
    sum_fields = ['DOE_fans_existing_kW','fan_incremental_electric_kW','oxygen_production_Nm3_h',
                  'oxygen_incremental_electric_kW','electric_extra_demand_kW',
                  'P_e_nonplant_kW','P_e_unresolved_kW','P_e_service_including_unresolved_kW',
                  'Q_h_demand_kW','Q_c_demand_kW','GT_electric_kW','grid_import_kW',
                  'el_chiller_electric_kW','abs_aux_electric_kW','abs_cooling_kW','el_cooling_kW',
                  'GT_gas_MMBtu_h','boiler_gas_MMBtu_h','HRSG_unused_steam_equivalent_lbph','operating_cost_USD_h']
    return {
        'case':case, 'hours':len(es), 'method':'hourly discrete-PL MILP (not time-coupled, not NMCSD operation)',
        'capacity':plant.as_dict(),
        'annual_integrals':{k:float(es[k].sum()) for k in sum_fields},
        'peak_demand_kW': {k:float(es[k].max()) for k in ['P_e_service_including_unresolved_kW','Q_h_demand_kW','Q_c_demand_kW']},
        'worst_balance_residuals': {
            k:float(es[k].abs().max()) for k in
            ['electricity_balance_error_kW','cooling_balance_error_kW','steam_balance_error_lbph']},
        'GT_operating_hours':int((es.GT_on_units>0).sum()),
        'HRSG_unused_heat_hours':int((es.HRSG_unused_steam_equivalent_lbph>1e-3).sum()),
        'grid_import_hours':int((es.grid_import_kW>1e-3).sum()),
        'load_met_all_hours': bool((es[['electricity_balance_error_kW','cooling_balance_error_kW','steam_balance_error_lbph']].abs().max()<1e-4).all()),
        'limitations':['The DOE demand is synthetic, not NMCSD measured load.',
                       'Scaled unit capacities are hypothetical and not commercial model nameplates.',
                       'Steam enthalpy difference/heat exchanger efficiency are explicit engineering assumptions.',
                       'Representative four-point turbine part load, independent hourly dispatch, no start or ramp constraints.',
                       'Historical 2001 prices, illustrative TOU clock periods; no modern economic conclusions.',
                       'Cooling tower duty approximated from RT and assumed heat rejection.',
                       'Electric unresolved allocation is conservative by including all in building service.',
                       'Emergency diesel is preserved as installed capability, NEVER used for normal grid dispatch.',
                       'No clinical critical-load certification, fault propagation, black start or UPS modeled.',
                       'EnergyPlus cooling and heating boundary differences/warnings remain documented.']
    }


def make_figures(df:pd.DataFrame, dest:Path) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    dest.mkdir(exist_ok=True,parents=True)
    fig,axes=plt.subplots(3,1,figsize=(13,8),sharex=True)
    x=np.arange(1,len(df)+1)
    for ax,(k,label) in zip(axes,[('P_e_service_including_unresolved_kW','Electricity (kW)'),
                                  ('Q_h_demand_kW','Heating demand (kWth)'),
                                  ('Q_c_demand_kW','Cooling demand (kWth)')]):
        ax.plot(x,df[k].values,lw=.45)
        ax.set_ylabel(label)
        ax.grid(alpha=.18)
    axes[-1].set_xlabel('Hour of year')
    fig.tight_layout()
    fig.savefig(dest/'loads_8760.png',dpi=180)
    plt.close(fig)

    fig,axes=plt.subplots(2,1,figsize=(13,7),sharex=True)
    for k,lbl in [('grid_import_kW','Grid'),('GT_electric_kW','Gas turbine'),
                  ('el_chiller_electric_kW','Electric chiller'),('abs_aux_electric_kW','Absorption auxiliaries')]:
        axes[0].plot(x,df[k].values,lw=.55,label=lbl)
    axes[0].legend(ncol=4,fontsize=8)
    axes[0].set_ylabel('Electrical power (kW)')
    for k,lbl in [('abs_cooling_kW','Absorption chiller'),('el_cooling_kW','Electric chillers')]:
        axes[1].plot(x,df[k].values,lw=.55,label=lbl)
    axes[1].legend()
    axes[1].set_ylabel('Cooling supplied (kWth)')
    axes[1].set_xlabel('Hour of year')
    for a in axes: a.grid(alpha=.18)
    fig.tight_layout()
    fig.savefig(dest/'dispatch_8760.png',dpi=180)
    plt.close(fig)


def make_thermal_figure(thermal_df: pd.DataFrame, dest: Path, event_hour:int, event_duration:int)->None:
    """Reference-vs-fault temperature plot for three representative clinical zones."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    examples=['ICU_FLR_2','OR1_FLR_2','RADIOLOGY_FLR_4']
    fig,axes=plt.subplots(len(examples),1,figsize=(11,8),sharex=True)
    for ax,name in zip(axes,examples):
        view=thermal_df.loc[thermal_df.zone_name.eq(name)]
        if view.empty:continue
        ax.plot(view.hour_of_year,view.baseline_zone_temp_C,label='DOE base zone T',lw=1.1)
        ax.plot(view.hour_of_year,view.fault_zone_temp_C,label='1R1C cooling-loss T',lw=1.2)
        ax.axvspan(event_hour,event_hour+event_duration,alpha=.15,label='Loss interval')
        ax.set_title(name,fontsize=10)
        ax.set_ylabel('Zone air T (C)')
        ax.grid(alpha=.2)
    axes[0].legend(fontsize=8,ncol=3,loc='best')
    axes[-1].set_xlabel('Hour of year')
    fig.suptitle('Illustrative thermal response only — not a validated safety threshold',fontsize=11)
    fig.tight_layout()
    dest.mkdir(parents=True,exist_ok=True)
    fig.savefig(dest/'critical_zone_thermal_demo.png',dpi=220)
    plt.close(fig)


def run(root:Path,case:str,limit_hours:int|None,draw:bool,
        fan_ratio:float=1.0,oxygen_flow_Nm3_h:float|None=None,
        oxygen_profile_csv:Path|None=None,oxygen_specific_kWh_Nm3:float=1.04,
        thermal_demo:bool=False,event_hour:int=24,event_duration:int=4,
        cooling_loss_fraction:float=1.0,mass_participation:float=0.5,
        effective_U_W_m2K:float=1.5, scenario_tag:str|None=None) -> dict:
    root=Path(root)
    plant=load_plant(root/'configs/plant_configs.json',case)
    df=read_data(root)
    e_boundary=pd.read_csv(root/'inputs/loads/electricity_boundary_8760.csv')
    oxygen=None
    if oxygen_flow_Nm3_h is not None or oxygen_profile_csv is not None:
        oxygen=oxygen_profile(df,oxygen_flow_Nm3_h,oxygen_profile_csv,oxygen_specific_kWh_Nm3)
    df=attach_demand_extensions(df,e_boundary,fan_ratio,oxygen)
    if limit_hours is not None:
        if not (1<=limit_hours<=len(df)):
            raise ValueError('Invalid hours limit')
        df=df.head(limit_hours).copy()
    parts=[case if limit_hours is None else f'{case}_first{limit_hours}h']
    if scenario_tag is not None:
        if not scenario_tag or not all(c.isalnum() or c in ('-','_') for c in scenario_tag):
            raise ValueError('Scenario tag must contain only letters, digits, _ or -')
        parts.append(scenario_tag)
    else:
        if fan_ratio!=1:parts.append('fan_scenario')
        if oxygen is not None:parts.append('PSA_scenario')
        if thermal_demo:parts.append('thermal_scenario')
    output_dir=root/'outputs'/('__'.join(parts))
    output_dir.mkdir(parents=True,exist_ok=True)
    if thermal_demo:
        path=root/'inputs/energyplus/Hospital_SanDiego.idf'
        if not path.exists():
            raise FileNotFoundError('Thermal demo requires original EnergyPlus IDF: '+str(path))
        zones=pd.read_csv(root/'inputs/loads/critical_zones_8760.csv')
        t,t_summary=simulate_cooling_interruption(
            zones,df,path,event_hour,event_duration,cooling_loss_fraction,
            mass_participation,effective_U_W_m2K)
        t.to_csv(output_dir/'critical_zone_thermal_response.csv',index=False,float_format='%.9g')
        t_summary.to_csv(output_dir/'critical_zone_thermal_summary.csv',index=False,float_format='%.9g')
        if draw:make_thermal_figure(t,output_dir/'figures',event_hour,event_duration)
        from .idf_parameters import zone_parameter_table
        pd.DataFrame(zone_parameter_table(path,mass_participation,effective_U_W_m2K)).to_csv(
            output_dir/'idf_thermal_parameters_all_zones.csv',index=False,float_format='%.10g')
    records=[]
    for i,row in enumerate(df.itertuples(index=False),start=1):
        try:
            records.append(solve_hour(row,plant))
        except Exception as e:
            (output_dir/'failed_hour.txt').write_text(f'hour {i}: {e}',encoding='utf-8')
            raise
        if i % 1000 == 0:
            print(f'Solved {i}/{len(df)} hours',flush=True)
    result=pd.DataFrame.from_records(records)
    result.to_csv(output_dir/'dispatch_hourly.csv',index=False,float_format='%.10g')
    df[['hour_of_year','DOE_fans_existing_kW','fans_scenario_ratio',
        'fan_incremental_electric_kW','oxygen_production_Nm3_h',
        'oxygen_incremental_electric_kW','electric_extra_demand_kW']].to_csv(
        output_dir/'demand_extensions_hourly.csv',index=False,float_format='%.10g')
    summary=annual_summary(result,case,plant)
    summary['demand_extensions']={
        'fan_ratio':fan_ratio,
        'fan_in_original_DOE_electricity':True,
        'oxygen_added_to_original_DOE_electricity':oxygen is not None,
        'PSA_specific_kWh_per_Nm3':oxygen_specific_kWh_Nm3 if oxygen is not None else None,
        'PSA_flow_origin':'hourly_CSV' if oxygen_profile_csv is not None else
                          ('fixed_illustrative' if oxygen_flow_Nm3_h is not None else 'disabled'),
        'no_oxygen_supply_storage_or_pressure_model':True,
    }
    summary['thermal_inertia']={
        'executed':thermal_demo,'method':'1R1C fault-only superposition on DOE baseline temperatures',
        'event_start_hour':event_hour if thermal_demo else None,
        'duration_hours':event_duration if thermal_demo else None,
        'cooling_loss_fraction':cooling_loss_fraction if thermal_demo else None,
        'mass_participation_assumed':mass_participation if thermal_demo else None,
        'effective_U_W_m2K_assumed':effective_U_W_m2K if thermal_demo else None,
        'dynamic_EnergyPlus_calibrated':False,
        'thermal_demo_is_not_a_feedback_constraint_in_MILP':True,
    }
    (output_dir/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    pd.DataFrame([{'metric':k,'sum_over_hours':v} for k,v in summary['annual_integrals'].items()]).to_csv(
        output_dir/'annual_integrals.csv',index=False)
    if draw:
        make_figures(result,output_dir/'figures')
    return summary
