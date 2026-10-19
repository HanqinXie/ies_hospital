import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from hospital_ies.model import load_plant, solve_hour, RT_TO_KW,gt_level_properties
from hospital_ies.pipeline import read_data

def test_load_integrity():
    d=read_data(ROOT)
    assert len(d)==8760
    assert np.max(np.abs(d.P_e_nonplant_kW+d.P_e_plant_kW+d.P_e_unresolved_kW-d.P_e_facility_kW))<1e-4

def test_solve_two_hours():
    pl=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    df=read_data(ROOT)
    for i in (0,125,4300,7000,8759):
        o=solve_hour(next(df.iloc[[i]].itertuples(index=False)),pl)
        assert abs(o['electricity_balance_error_kW'])<1e-4
        assert abs(o['cooling_balance_error_kW'])<1e-4
        assert abs(o['steam_balance_error_lbph'])<1e-4
        assert o['emergency_diesel_dispatched_kW']==0
        assert o['GT_on_units']<=3
        assert o['GT_on_units']==sum(o[f'GT_units_at_{int(j*100)}pct'] for j in pl.levels)

def test_historical_original_ratings():
    pl=load_plant(ROOT/'configs/plant_configs.json','original_historical_capacity')
    assert pl.gt_rated_kW==800
    assert pl.absorption_rt==800
    assert pl.electric_chillers_rt==(800,800,1200)
    assert pl.boiler_steam_lbph==75000
    assert pl.emergency_diesel_kW==1800
    assert pl.abs_steam_lb_per_rt==19.5
