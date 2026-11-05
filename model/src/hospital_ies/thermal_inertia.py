"""IES-supporting 1R1C thermal disturbance surrogate for critical DOE hospital zones.

Uses measured-from-EnergyPlus baseline hourly zone temperature and sensible
cooling, computes only fault-induced temperature deviations by linear
superposition. Baseline forcing is *not* estimated, and the real IDF heat
balance is NOT replaced. C and UA are transparent low-order approximations,
not identified/calibrated parameters. NOT CFD, clinical safety or a verified
building predictive model.
"""
from __future__ import annotations

from pathlib import Path
import math

import numpy as np
import pandas as pd
from .idf_parameters import zone_parameter_table


def simulate_cooling_interruption(zones:pd.DataFrame, hours:pd.DataFrame,
                                  idf_path:Path, event_start_hour:int,
                                  duration_hours:int, loss_fraction:float=1.0,
                                  mass_participation:float=0.5,
                                  effective_U_W_m2K:float=1.5,
                                  dt_h:float=1.0) -> tuple[pd.DataFrame,pd.DataFrame]:
    """Compare fault versus baseline zone temperature for an exogenous cooling loss.

    IDF baseline temp(t) is at each hour's END. Hour h is assumed to have
    `zone_sensible_cooling_kW` delivered in the baseline; during event we
    remove the chosen share of that sensible zone HVAC energy, NOT the central
    plant chilled-water cooling; thus no added demand or plant double count.

    Closed-form 1R1C disturbance response per step:
      dT_next = exp(-UA/C*dt)*dT_prev + (-dQcool/UA)*(1-exp(-UA/C*dt)).
    Here dQcool=-loss*baseline_zone_sensible_cooling (positive thermal shock).
    This model computes a *what-if* effect, not actual co-optimized dispatch.
    """
    if duration_hours<=0 or event_start_hour<1 or not 0<=loss_fraction<=1:
        raise ValueError('Invalid fault interval or cooling loss fraction')
    required={'hour_of_year','zone_name','zone_mean_air_temperature_C','zone_sensible_cooling_kW',
              'thermostat_cooling_setpoint_C','zone_multiplier'}
    if not required.issubset(zones):raise ValueError('Critical zone fields missing')
    selected = zones[zones.hour_of_year.isin(hours.hour_of_year)].copy()
    if selected.empty:raise ValueError('No selected critical zone hours')
    params=pd.DataFrame(zone_parameter_table(idf_path,mass_participation,effective_U_W_m2K))
    selected['zone_name']=selected.zone_name.str.upper()
    if not set(selected.zone_name).issubset(set(params.zone_name)):
        raise ValueError('Critical zone names absent from original source IDF')
    selected=selected.merge(params,on='zone_name',validate='many_to_one',suffixes=('','_IDF'))
    if not np.allclose(selected.zone_multiplier,selected.zone_multiplier_IDF):
        raise ValueError('IDF and critical load zone multipliers differ')
    selected=selected.sort_values(['zone_name','hour_of_year']).reset_index(drop=True)
    allout=[]
    for zone,chunk in selected.groupby('zone_name',sort=False):
        delta=0.0
        for r in chunk.itertuples(index=False):
            ua=float(r.assumed_UA_kW_K)
            C=float(r.effective_C_kWh_K)
            decay=math.exp(-ua/C*dt_h)
            active = event_start_hour <= r.hour_of_year < event_start_hour+duration_hours
            lost_cooling=float(r.zone_sensible_cooling_kW)*loss_fraction if active else 0.0
            delta=decay*delta+(-math.expm1(-ua/C*dt_h))*lost_cooling/ua
            baseline=float(r.zone_mean_air_temperature_C)
            fault=baseline+delta
            allout.append(dict(hour_of_year=int(r.hour_of_year),zone_name=zone,
                zone_multiplier=float(r.zone_multiplier),
                baseline_zone_temp_C=baseline,fault_zone_temp_C=fault,
                delta_temperature_C=delta,baseline_sensible_cooling_kW=float(r.zone_sensible_cooling_kW),
                lost_sensible_cooling_kW=lost_cooling,
                cooling_setpoint_C=float(r.thermostat_cooling_setpoint_C),
                fault_above_cooling_setpoint_C=max(0.0,fault-float(r.thermostat_cooling_setpoint_C)),
                assumed_UA_kW_K=ua,effective_C_kWh_K=C))
    result=pd.DataFrame(allout).sort_values(['hour_of_year','zone_name']).reset_index(drop=True)
    summary=(result.groupby('zone_name').agg(max_delta_T_C=('delta_temperature_C','max'),
          max_fault_temperature_C=('fault_zone_temp_C','max'),
          hours_above_cooling_setpoint=('fault_above_cooling_setpoint_C',lambda s: int((s>0).sum())),
          illustrative_zone_C_kWh_K=('effective_C_kWh_K','first'),
          illustrative_UA_kW_K=('assumed_UA_kW_K','first')).reset_index())
    return result, summary
