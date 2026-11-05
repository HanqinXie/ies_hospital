"""Actual illustrative fee metric: inputs only; no hospital trajectories."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import sys
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def metrics():return importlib.import_module('tools.integrated_experiment.energy_metrics')

def segment(hour=1,start=0.,end=15.):
    return dict(hour_of_year=hour,start_minute=start,end_minute=end,
        action=dict(gt_fuel_MMBtu_h=0.,boiler_steam_lbph=0.,grid_import_kW=100.,diesel_kW=0.),
        cost_USD_step=99999.,tracking_penalty_USD_step=88888.,security_objective_penalty_USD_step=77777.)

@pytest.mark.parametrize('hour',[31,8760])
def test_actual_rangeindex_hour_selects_original_current_tariff(hour):
    from hospital_ies.pipeline import read_data
    from hospital_ies.model import load_plant,illustrative_tariff
    hourly=read_data(ROOT);plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    assert hourly.index[0]==0 and hourly.index[-1]==8759
    expected=25.*illustrative_tariff(hourly.iloc[hour-1].timestamp,plant)
    assert metrics().segment_energy_fee_USD(segment(hour),plant,hourly)==pytest.approx(expected)

def test_each_real_source_fee_excludes_all_artificial_objective_terms():
    from hospital_ies.model import KWH_PER_MMBTU
    plant=SimpleNamespace(gas_price_usd_per_mmbtu=2.,steam_kWh_per_lb=.1,boiler_eff=.5,
        tariff=dict(electricity_offpeak_per_kWh=.2,electricity_midpeak_per_kWh=.3,electricity_onpeak_per_kWh=.4))
    hourly=pd.DataFrame(dict(timestamp=[pd.Timestamp('2017-01-01')],hour_of_year=[1]))
    s=segment();s['action'].update(gt_fuel_MMBtu_h=1.,boiler_steam_lbph=100.,grid_import_kW=20.,diesel_kW=5.)
    expected=.25*(2.+100*.1/.5/KWH_PER_MMBTU*2.+20*.2+5*.30)
    assert metrics().segment_energy_fee_USD(s,plant,hourly)==pytest.approx(expected)
    s['cost_USD_step']=0.;s['tracking_penalty_USD_step']=0.;s['security_objective_penalty_USD_step']=0.
    assert metrics().segment_energy_fee_USD(s,plant,hourly)==pytest.approx(expected)

def test_partial_overlap_fee_stays_unknown_with_observed_prefix():
    from hospital_ies.pipeline import read_data
    from hospital_ies.model import load_plant
    hourly=read_data(ROOT);plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    s=segment(31,0.,90.);p=dict(segments=[s],summary=dict(status='solver_error',observed_until_minute=90.))
    fee=metrics().window_energy_fee(p,plant,hourly,60.,120.)
    assert fee['actual_energy_fee_USD'] is None and not fee['energy_fee_outcome_valid']
    assert fee['observed_actual_energy_fee_USD']==pytest.approx(metrics().segment_energy_fee_USD(s,plant,hourly)/3)
    assert s['cost_USD_step']==99999.

def test_error_after_fully_observed_window_preserves_known_fee():
    from hospital_ies.pipeline import read_data
    from hospital_ies.model import load_plant
    hourly=read_data(ROOT);plant=load_plant(ROOT/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    s=segment(31,0.,450.);p=dict(segments=[s],summary=dict(status='solver_error',observed_until_minute=450.))
    fee=metrics().window_energy_fee(p,plant,hourly,60.,420.)
    assert fee['energy_fee_outcome_valid'] and fee['actual_energy_fee_USD']==pytest.approx(fee['observed_actual_energy_fee_USD'])

def test_audit_and_training_feedback_record_fee_without_rewriting_raw_cost(tmp_path,monkeypatch):
    from tests.test_integrated_data_audit import make_fixture
    from tools.p2b_experiment.runner import load_payload,save_payload
    path,m,folder=make_fixture(tmp_path,monkeypatch,status='solver_error',first=None)
    for policy in m['policies']:
        p=load_payload(folder,'caseA',policy,m['signature'])
        for s in p['segments']:
            s.update(hour_of_year=31,action=segment()['action'])
        save_payload(folder,'caseA',policy,m['signature'],p)
    audit=importlib.import_module('tools.integrated_experiment.data_audit').audit(path,'fee')
    import json
    rows=json.loads((tmp_path/'audits/fee/trajectory_scores.json').read_text())
    outcomes=json.loads((tmp_path/'audits/fee/training_outcomes.json').read_text())
    assert 'energy_metrics_source_sha256' in audit
    assert all(r['actual_energy_fee_USD'] is None and r['observed_actual_energy_fee_USD']>0 and r['cost_USD'] is None for r in rows)
    assert all(v['actual_energy_fee_USD'] is None and v['observed_actual_energy_fee_USD']>0 for v in outcomes['caseA']['policy_outcomes'].values())
    assert all(s['cost_USD_step']==1. for s in load_payload(folder,'caseA','secure_milp',m['signature'])['segments'])
