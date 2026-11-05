"""Additive IES demand extensions with strict EnergyPlus electric-boundary accounting.

Normal baseline: P_service = P_nonplant + P_unresolved.
DOE `fans_kW` is ALREADY contained in P_nonplant and is only allocated.
On-site PSA electricity is *optional and incremental*; never silently assumed.
"""
from __future__ import annotations

from pathlib import Path
import math
import pandas as pd
import numpy as np


def oxygen_profile(loads: pd.DataFrame, hourly_flow_Nm3: float | None = None,
                   profile_csv: Path | None = None,
                   specific_kWh_Nm3: float = 1.04) -> pd.DataFrame:
    """Return 8760-aligned production and incremental electric demand.

    1.04 kWh/Nm3 is a particular 4-bed-6-step PSA point reported in Si et al.
    CEJ 509 (2025) 161273, Table 7. NOT universal and not medical certification.
    This does not simulate oxygen storage, pressure, purity or medical demand.
    """
    if specific_kWh_Nm3 <= 0 or not np.isfinite(specific_kWh_Nm3):
        raise ValueError('Specific oxygen electricity must be finite positive')
    if (hourly_flow_Nm3 is None) == (profile_csv is None):
        raise ValueError('Specify exactly one of fixed hourly PSA flow or a profile CSV')
    if profile_csv is not None:
        raw = pd.read_csv(profile_csv)
        if not {'hour_of_year','oxygen_production_Nm3_h'}.issubset(raw.columns):
            raise ValueError('PSA profile must have hour_of_year and oxygen_production_Nm3_h')
        if len(raw)!=len(loads) or raw.hour_of_year.duplicated().any():
            raise ValueError('PSA profile length/hours not aligned')
        flows=loads[['hour_of_year']].merge(raw[['hour_of_year','oxygen_production_Nm3_h']],
                                           on='hour_of_year',how='left',validate='one_to_one')['oxygen_production_Nm3_h'].to_numpy()
    else:
        flows = np.full(len(loads),float(hourly_flow_Nm3))
    if not np.isfinite(flows).all() or (flows < 0).any():
        raise ValueError('PSA production flow must be finite and nonnegative')
    return pd.DataFrame({'oxygen_production_Nm3_h':flows,
                         'oxygen_incremental_electric_kW':flows*specific_kWh_Nm3})


def attach_demand_extensions(loads: pd.DataFrame, electricity_boundary: pd.DataFrame,
                             fan_ratio:float=1.0, oxygen:pd.DataFrame|None=None) -> pd.DataFrame:
    """Set accounting columns; fan ratio is scenario-only, no pressure validation."""
    if not np.isfinite(fan_ratio) or fan_ratio<0:
        raise ValueError('Fan ratio must be finite nonnegative')
    df=loads.copy().reset_index(drop=True)
    b=electricity_boundary.reset_index(drop=True)
    if len(df)!=len(b) or not np.array_equal(df.hour_of_year.to_numpy(),b.hour_of_year.to_numpy()):
        raise ValueError('Electricity boundary not aligned')
    if not {'fans_kW','P_e_nonplant_kW'}.issubset(b.columns):
        raise ValueError('Missing DOE fan/boundary columns')
    fans=b.fans_kW.to_numpy(dtype=float)
    nonplant=df.P_e_nonplant_kW.to_numpy(dtype=float)
    if not (np.isfinite(fans).all() and (fans>=0).all() and np.all(fans <= nonplant + 1e-5)):
        raise ValueError('DOE fan electricity must be contained in nonplant boundary')
    df['DOE_fans_existing_kW']=fans
    df['fans_scenario_ratio']=fan_ratio
    df['fan_incremental_electric_kW']=(fan_ratio-1)*fans
    if oxygen is None:
        df['oxygen_production_Nm3_h']=0.0
        df['oxygen_incremental_electric_kW']=0.0
    else:
        if len(oxygen)!=len(df):raise ValueError('Oxygen data not aligned')
        for k in ('oxygen_production_Nm3_h','oxygen_incremental_electric_kW'):
            df[k]=oxygen[k].to_numpy(dtype=float)
    df['electric_extra_demand_kW']=df.fan_incremental_electric_kW+df.oxygen_incremental_electric_kW
    return df
