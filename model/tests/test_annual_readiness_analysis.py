"""Synthetic durable blocks exercise offline cross-block native definitions."""
import copy
import csv
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.annual_readiness.journal import BlockJournal
from tools.model_correctness_v23.metrics import build_reliability_metrics
from tools.sci_engineering.design import digest

ROOT=Path(__file__).resolve().parents[1]


def api():
    try:
        return importlib.import_module('tools.annual_readiness.analysis')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_readiness.analysis':
            raise
        pytest.fail('Offline continuous-run analysis is missing',pytrace=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,allow_nan=False),encoding='utf8')


def segment(start,end,*,heat_short=None,cut=.4):
    matrices={k:[[0.,0.,0.] for _ in range(4)] for k in
        ('original_demand_kW','delivered_kW','active_cut_kW','connected_unserved_kW')}
    for key,value in zip(matrices,(10.,9.,cut,1.-cut)):
        matrices[key][0][0]=value
    heat=0.
    if heat_short is not None:
        matrices['original_demand_kW'][0][1]=10.
        heat=0. if heat_short else 10.
        matrices['delivered_kW'][0][1]=heat
        matrices['connected_unserved_kW'][0][1]=10.-heat
    dt=(end-start)/60.
    action=dict(gt_electric_kW=[0.,0.,0.],grid_import_kW=7.,diesel_kW=2.,
        gt_fuel_MMBtu_h=0.,boiler_steam_lbph=heat/(.2*.8),hrsg_steam_lbph=0.,
        absorption_cooling_kW=0.,electric_chiller_cooling_kW=[0.,0.,0.],
        ups_discharge_kW=0.,ups_charge_kW=0.,cold_discharge_kW=0.,cold_charge_kW=0.,
        cold_storage_pump_kW=0.,station_auxiliary_kW=0.,recovery_pump_electric_kW=0.,
        recovery_fan_electric_kW=0.,recovery_cooling_kW=0.)
    return dict(start_minute=start,end_minute=end,hour_of_year=33,
        action=action,service_request_ledger=matrices,
        diesel_local_response=dict(mechanical_work_kWh=3.*dt,electrical_work_kWh=2.*dt,
            loss_work_kWh=dt,rotor_delta_kWh=0.,fuel_budget_debit_kWh_equivalent=3.*dt),
        m21_storage={name:dict(initial_kWh=stock,final_kWh=stock,charged_input_kWh=0.,
            discharged_kWh=0.,standing_loss_kWh=0.,conversion_loss_kWh=0.)
            for name,stock in (('ups',1.),('cold_store',0.))},
        m22_station=dict(boiler_ready=True,diesel_starts_used=1),
        annual_readiness=dict(qualification_before={'unsupported_minutes':0.,'diesel_starts_used':1},
            qualification_after={'unsupported_minutes':0.,'diesel_starts_used':1},
            actual_normal_source_proof={'supported':True,'DC_energy_kWh':None}),
        cost_USD_step=999999.)


def fixture_run(tmp_path,blocks=None,*,requested=2.,status='completed',missing_prep=False):
    repo=tmp_path/'repo';run=tmp_path/'run'
    config=dict(engineering_assumptions_not_from_source=dict(kWh_thermal_per_lb_steam=.2,
        steam_to_building_heat_exchanger_efficiency=.8),historical_pnnl_performance=dict(
            existing_absorption_steam_lb_per_RT_hr=20.,existing_absorption_aux_kW_per_RT=.1,
            existing_electric_chiller_all_in_kW_per_RT=1.,boiler_thermal_efficiency=.9),
        historical_2001_prices_USD=dict(gas_per_MMBtu=3.,electricity_offpeak_per_kWh=.1,
            electricity_onpeak_per_kWh=.2,electricity_midpeak_per_kWh=.15))
    write(repo/'configs/plant_configs.json',config)
    hourly=repo/'inputs/loads/hospital_ies_demand_8760.csv';hourly.parent.mkdir(parents=True)
    with hourly.open('w',encoding='utf8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['hour_of_year','timestamp'])
        writer.writeheader();writer.writerow(dict(hour_of_year=33,timestamp='2001-01-01T01:00:00'))
    sources={}
    for name in ('tools/model_correctness_v23/metrics.py','tools/operating_rules_study/ledger.py'):
        path=repo/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((ROOT/name).read_bytes());sources[name]=sha(path)
    sources['configs/plant_configs.json']=sha(repo/'configs/plant_configs.json')
    bindings=dict(source=sources,inputs={'inputs/loads/hospital_ies_demand_8760.csv':sha(hourly)},git_commit='synthetic')
    case=dict(id='synthetic-case',scenario=dict(start_hour=33,duration_steps=requested/15.,step_minutes=15.))
    write(run/'CASE.json',dict(case=case))
    write(run/'BINDINGS.json',bindings)
    model=dict(sci_engineering=dict(cold_pump_kW_per_kW=.02,
        ups=dict(charge_efficiency=.5,discharge_efficiency=1.,loss_per_hour=0.),
        cold_store=dict(charge_efficiency=1.,discharge_efficiency=1.,loss_per_hour=0.)))
    write(run/'RESOLVED_CASE.json',dict(model_config=model))
    identity=dict(case_id=case['id'],model_id='synthetic',git_commit='synthetic',
        source_sha256=digest(bindings['source']),input_sha256=digest(bindings['inputs']),case_sha256=digest(case))
    journal=BlockJournal(run/'evidence',identity=identity)
    blocks=copy.deepcopy(blocks or [[segment(0.,1.)],[segment(1.,2.)]])
    budget=10.
    for i,segments in enumerate(blocks):
        for s in segments:
            s['pre_state']={'diesel_energy_remaining_kWh':budget}
            budget-=s['diesel_local_response']['fuel_budget_debit_kWh_equivalent']
            s['post_state']={'diesel_energy_remaining_kWh':budget}
        end=segments[-1]['end_minute']
        block=dict(start_minute=segments[0]['start_minute'],end_minute=end,segments=segments,
            solver_calls=[],events=[],decision={},terminal=dict(snapshot_time_minute=end,
                DG={'remaining_shaft_budget_kWh':budget},inventories={
                    'ups':{'stock_kWh':1.},'cold_store':{'stock_kWh':0.}}),
            economics={'observed_operating_fee_USD2001':999999.})
        if i==0 and not missing_prep:
            block['initial_preparation']={'ups':2.,'cold_store':0.}
        journal.append(block)
    summary=journal.close(status=status,expected_end_minute=requested,error={'type':'kept_failure'} if status!='completed' else None)
    write(run/'RESULT.json',dict(status=status,case_id=case['id'],model_id='synthetic',
        source_sha256=identity['source_sha256'],case_sha256=identity['case_sha256'],
        observed_until_minute=summary['observation']['observed_until_minute'],requested_end_minute=requested,
        unknown_tail_minutes=requested-summary['observation']['observed_until_minute'],
        source_and_inputs_unchanged=True,error={'type':'kept_failure'} if status!='completed' else None,
        economics={'actual_fee_USD2001':999999.}))
    return run,repo,blocks


def test_original_sixty_second_heat_confirmation_crosses_block_boundary(tmp_path):
    blocks=[[segment(0.,.5,heat_short=True),segment(.5,1.2,heat_short=False)],
        [segment(1.2,2.,heat_short=False)]]
    run,repo,raw=fixture_run(tmp_path,blocks)
    out=api().analyze_run(run,repo=repo)
    native=out['native_reliability'];heat=native['carrier_service']['P0']['h']
    assert heat['candidate_recovery_absolute_minute'] == .5
    assert heat['recovery_confirmation_minute'] == 1.5
    expected=build_reliability_metrics(dict(segments=[dict(start_minute=s['start_minute'],
        end_minute=s['end_minute'],service_request_ledger=s['service_request_ledger'])
        for block in raw for s in block],continuation_start_minute=0.,summary={'requested_end_minute':2.}))
    assert native == expected
    assert out['provenance']['full_raw_trajectory_retained'] is False
    assert out['provenance']['maximum_decoded_blocks'] == 1


def test_nonzero_cuts_and_native_journal_ens_are_reconciled(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    out=api().analyze_run(run,repo=repo)
    row=out['native_reliability']['carrier_service']['P0']['e']
    assert row['ENS_kWh'] == pytest.approx(2./60.)
    assert row['disconnected_ENS_kWh'] == pytest.approx(.4*2./60.)
    assert out['service_reconciliation']['check'] == 'pass'
    assert out['service_reconciliation']['maximum_absolute_ENS_difference_kWh'] < 1e-12


def test_independent_fee_uses_shaft_once_and_prep_only_at_zero_origin(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    out=api().analyze_run(run,repo=repo)
    fee=out['economics']
    assert fee['observed_operating_fee_USD2001'] == pytest.approx((7.*.1+3.*.3)*2./60.)
    assert fee['initial_preparation_fee_USD2001'] == pytest.approx(.2)
    assert fee['actual_fee_USD2001'] == pytest.approx((7.*.1+3.*.3)*2./60.+.2)
    assert fee['DG_electric_fee_USD2001'] == pytest.approx(2.*2./60.*.3)
    assert fee['DG_shaft_difference_USD2001'] == pytest.approx(1.*2./60.*.3)
    assert fee['producer_economics_used_for_calculation'] is False
    assert fee['unobserved_tail_fee_USD2001'] is None


def test_failed_prefix_preserves_error_and_unknown_tail_and_unconfirmed_recovery(tmp_path):
    run,repo,_=fixture_run(tmp_path,[[segment(0.,.5,heat_short=True),segment(.5,1.,heat_short=False)]],
        requested=3.,status='failed')
    out=api().analyze_run(run,repo=repo)
    assert out['observation']['unknown_tail_minutes'] == 2.
    assert out['observation']['complete'] is False
    assert out['execution']['error'] == {'type':'kept_failure'}
    assert out['native_reliability']['carrier_service']['P0']['h']['recovery_confirmation_minute'] is None
    assert out['unobserved_tail_service'] is None


def test_actual_balance_storage_and_dg_stock_chains_are_checked_across_blocks(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    out=api().analyze_run(run,repo=repo)
    assert out['balances']['e']['check'] == 'pass'
    assert out['balances']['c']['check'] == 'pass'
    assert out['storage']['ups']['receipt_chain_max_gap_kWh'] == 0.
    assert out['diesel']['initial_shaft_budget_kWh'] == 10.
    assert out['diesel']['final_shaft_budget_kWh'] == pytest.approx(9.9)
    assert out['diesel']['stock_residual_kWh'] == pytest.approx(0.,abs=1e-14)
    assert out['diesel']['stock_check'] == 'pass'
    assert out['DC_energy_kWh'] is None
    assert out['balances']['steam_full_closure'] == 'unknown'


def test_diesel_work_checks_retain_original_work_units_and_tolerance(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    check=api().analyze_run(run,repo=repo)['diesel']['interval_checks']['energy_balance']
    assert check['tolerance'] == 1e-8
    assert check['tolerance_unit'] == 'kWh'


def test_producer_fee_difference_is_explicit_failed_check_without_using_it_to_price(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    out=api().analyze_run(run,repo=repo)
    assert 'economics.independent_vs_producer_fee' in out['failed_checks']
    assert out['economics']['actual_fee_USD2001'] != 999999.


def test_missing_initial_preparation_stays_unknown_instead_of_zero(tmp_path):
    run,repo,_=fixture_run(tmp_path,missing_prep=True)
    fee=api().analyze_run(run,repo=repo)['economics']
    assert fee['initial_preparation_fee_USD2001'] is None
    assert fee['actual_fee_USD2001'] is None
    assert fee['observed_operating_fee_USD2001'] is not None


def test_actual_source_and_qualification_records_do_not_claim_control_energy(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    out=api().analyze_run(run,repo=repo)
    assert out['sources']['first_positive_output_minute']['DG'] == 0.
    assert out['sources']['first_positive_output_minute']['GT_1'] is None
    first=out['sources']['intervals'][0]
    assert first['actual_GT_kW'] == [0.,0.,0.]
    assert first['qualification_before']['diesel_starts_used'] == 1
    assert first['qualification_after']['diesel_starts_used'] == 1
    assert first['boiler_ready'] is True


def test_source_hash_drift_is_rejected_before_scientific_claims(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    (repo/'configs/plant_configs.json').write_text('{}',encoding='utf8')
    with pytest.raises(ValueError,match='SHA|source|binding'):
        api().analyze_run(run,repo=repo)


def test_raw_block_corruption_is_rejected_by_original_journal_verifier(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    raw=run/'evidence/blocks/block_000000.json.gz'
    raw.write_bytes(raw.read_bytes()+b'corrupt')
    with pytest.raises(ValueError,match='SHA'):
        api().analyze_run(run,repo=repo)


def test_cli_writes_fresh_detached_artifact_and_refuses_overwrite(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    producer=json.loads((run/'RESULT.json').read_text(encoding='utf8'))
    producer['economics']['actual_fee_USD2001']=(7.*.1+3.*.3)*2./60.+.2
    write(run/'RESULT.json',producer)
    api()
    dest=tmp_path/'analysis'
    command=[sys.executable,'-m','tools.annual_readiness.analysis','--run',str(run),'--repo',str(repo),'--output',str(dest)]
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    saved=json.loads((dest/'ANALYSIS.json').read_text(encoding='utf8'))
    assert saved['observation']['observed_until_minute'] == 2.
    assert subprocess.run(command,cwd=ROOT,capture_output=True).returncode != 0


def test_producer_files_are_byte_identical_after_readonly_analysis(tmp_path):
    run,repo,_=fixture_run(tmp_path)
    before={p.relative_to(run).as_posix():sha(p) for p in run.rglob('*') if p.is_file()}
    api().analyze_run(run,repo=repo)
    after={p.relative_to(run).as_posix():sha(p) for p in run.rglob('*') if p.is_file()}
    assert before == after
