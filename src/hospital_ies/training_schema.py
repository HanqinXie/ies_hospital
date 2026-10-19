"""Strict pre-action observation schema shared by non-MPC ML baselines.

Do not add stage, stratum, future shock, post-dispatch ENS, trip outcomes or
complete scenario definitions here. Model learns CONDITIONAL synthetic risk.
"""
from __future__ import annotations
import pandas as pd
DEVICES=[f'{name}_{i}' for name in ('GT','HRSG','EC','BOILER') for i in (1,2,3)]+[
    'GRID','ABS','TOWER','DIESEL',*[f'TOWER_{i}' for i in range(1,5)]]
FEATURES=['step','hour_of_year','grid_availability','grid_limit_kW',
    'pre_GT_1_kW','pre_GT_2_kW','pre_GT_3_kW',
    'pre_diesel_energy_kWh','pre_P0_thermal_delta_max_C',
    'demand_shock_e','demand_shock_h','demand_shock_c',
    *[f'{g}_{k}_demand_kW' for g in ('P0','P1','P2','P3') for k in ('e','h','c')],
    *[f'fault_{name}' for name in DEVICES]]
LEGACY_OUTPUTS=['gt_electric_kW_1','gt_electric_kW_2','gt_electric_kW_3',
         'grid_import_kW','diesel_kW','absorption_cooling_kW',
         'electric_chiller_cooling_kW_1','electric_chiller_cooling_kW_2','electric_chiller_cooling_kW_3',
         'boiler_steam_lbph']
# P2A: only these six solver targets are accepted by the frozen environment.
# Boiler (lb/h) and three electric chiller powers remain outcome diagnostics.
from .data_contract import ACTION_SCHEMA,SCHEMA_VERSIONS,validate_action,require_single_contract
ACTIONS=['gt_electric_kW_1','gt_electric_kW_2','gt_electric_kW_3',
         'grid_import_kW','diesel_kW','absorption_cooling_kW']

def action_targets(values):
    if len(values)!=len(ACTIONS):raise ValueError('Expected exactly six supported kW targets')
    return validate_action({'targets':dict(zip(ACTION_SCHEMA,map(float,values)))})

def build_decision_features(records):
    """Use only the explicit observable whitelist, never flatten post outcomes."""
    if not records:return pd.DataFrame()
    if any('execution_contract_id' in r for r in records):
        if not all('execution_contract_id' in r for r in records):raise ValueError('Missing execution contract identity')
        require_single_contract(records)
    keys=set(records[0]['features'])
    for row in records:
        if set(row['features'])!=keys:raise ValueError('Inconsistent observation schema')
        if set(row['feature_metadata'])!=keys:raise ValueError('Missing feature provenance')
        if any(v['available_time_min']>row['decision_time_min'] for v in row['feature_metadata'].values()):
            raise ValueError('Future feature availability')
    return pd.DataFrame([r['features'] for r in records])

def build_features(df:pd.DataFrame)->pd.DataFrame:
    x=df.copy()
    faults=df.active_faults.fillna('').astype(str).map(lambda s:set(s.split(';')))
    for dev in DEVICES:x[f'fault_{dev}']=faults.map(lambda state:int(dev in state))
    missing=sorted(set(FEATURES)-set(x.columns))
    if missing:raise ValueError(f'Missing pre-action observable features: {missing}')
    return x[FEATURES].fillna(0.).astype(float)
