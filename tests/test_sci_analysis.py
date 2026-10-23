"""Synthetic arithmetic fixtures only; never exported as research figures."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT=Path(__file__).resolve().parents[1]
STAGING=PROJECT if (PROJECT/'analysis.py').is_file() else PROJECT/'tools'/'sci_engineering'

def api(name='analysis'):
    path=STAGING/(name+'.py')
    assert path.is_file(), 'Missing SCI '+name+' implementation'
    spec=importlib.util.spec_from_file_location('sci_test_'+name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def result(policy='C_sci_v21',scene='s1',block='b1',ens=0.,config='H0',window=600.,status='completed',attempt='attempt_0001'):
    service={p:{c:dict(requested_kWh=100.,delivered_kWh=100.,ENS_kWh=0.,disconnected_ENS_kWh=0.,
        connected_unserved_ENS_kWh=0.,material_minutes=0.,longest_material_minutes=0.,
        interruption_count=0,repeat_interruption_count=0) for c in ('e','h','c')} for p in ('P0','P1','P2','P3')}
    service['P0']['c'].update(ENS_kWh=ens,delivered_kWh=100.-ens,connected_unserved_ENS_kWh=ens)
    return dict(scenario_id=scene,source_block_id=block,policy_id=policy,configuration_id=config,
        model_id='M21_sci_engineering',source_binding_sha256='source_one',scenario_sha256=scene,
        phase='formal',canonical_class='L2',season_quarter=3,status=status,attempt_id=attempt,
        metrics=dict(complete=status=='completed',requested_end_minute=window,unknown_tail_minutes=0.,
            carrier_service=service,priority_union={p:dict(material_minutes=0.,longest_material_minutes=0.,interruption_count=0,repeat_interruption_count=0) for p in service},
            critical_union=dict(material_minutes=0.),economics=dict(energy_cost_USD=100.,
                emissions_conditional=dict(total_CO2_kg_conditional=200.,emissions_status='conditional_estimate')),
            actual_energy={},storage={},online_solver_seconds=1.,online_solver_max_seconds=.1),
        _scenario=dict(matched_factor_id='factor_'+scene,fault_duration_minutes=120.,initial_reserve_kWh=2400.),
        _policy=dict(display_alias=policy.split('_')[0],config={}))

def test_carrier_units_are_retained_and_failed_attempt_is_not_zero():
    a=api();good=result(ens=3.);bad=result(scene='failed',status='failed_unknown')
    rows={r['scenario_id']:r for r in a.flatten_records([good,bad])}
    assert rows['s1']['P0_c_ENS_kWh']==3.
    assert rows['s1']['P0_e_ENS_kWh']==0.
    assert rows['failed']['eligible'] is False
    assert rows['failed']['P0_c_ENS_kWh'] is None

def test_pairing_rejects_different_windows_and_source_bytes():
    a=api();one=result();two=result('A_sci_v21',ens=2.,window=720.)
    with pytest.raises(ValueError,match='window'):
        a.paired_differences(a.flatten_records([one,two]),'C_sci_v21')
    two=result('A_sci_v21',ens=2.);two['source_binding_sha256']='different_source'
    with pytest.raises(ValueError,match='source|version'):
        a.flatten_records([one,two])

def test_latest_failure_stays_unknown_without_reusing_earlier_completion():
    a=api();old=result(ens=2.);later=result(ens=0.,status='failed_unknown',attempt='attempt_0002')
    rows=a.flatten_records([old,later])
    assert len(rows)==1 and rows[0]['eligible'] is False
    assert rows[0]['P0_c_ENS_kWh'] is None

def test_cluster_interval_counts_source_blocks_not_policy_rows_or_minutes():
    a=api();pairs=[]
    for block,delta in [('b1',-2.),('b2',4.)]:
        for variant in range(3):
            pairs.append(dict(source_block_id=block,delta=delta,scenario_id=block+str(variant)))
    ci=a.source_block_interval(pairs,seed=1,repetitions=1000)
    assert ci['source_block_count']==2
    assert ci['paired_scenario_count']==6
    assert ci['mean_block_delta']==1.
    assert ci['lower_95']>=-2. and ci['upper_95']<=4.

def test_pair_summary_retains_improved_tied_worsened_and_unknown_denominators():
    a=api();records=[]
    for i,(old,new) in enumerate([(3.,1.),(2.,2.),(1.,4.)]):
        records += [result(scene=str(i),block=str(i),ens=old),result('A_sci_v21',scene=str(i),block=str(i),ens=new)]
    rows=a.paired_differences(a.flatten_records(records),'C_sci_v21')
    summary=a.summarize_pairs(rows,repetitions=100)
    cold=next(x for x in summary if x['metric']=='P0_c_ENS_kWh' and x['stratum_type']=='all')
    assert (cold['improved'],cold['tied'],cold['worsened'])==(1,1,1)
    assert cold['paired_scenarios']==3 and cold['source_blocks']==3

def test_mechanism_four_cells_are_matched_before_effects_and_interaction():
    a=api();records=[]
    for r,p,ens in [(False,False,10.),(True,False,8.),(False,True,7.),(True,True,4.)]:
        item=result(policy='mechanism_'+str(int(r))+str(int(p)),ens=ens)
        item['_policy']['mechanism_comparison']=True
        item['_policy']['config']=dict(reserve_enabled=r,critical_preparation_first=p,legacy_C19_order=False)
        records.append(item)
    rows=a.mechanism_effects(a.flatten_records(records))
    row=next(x for x in rows if x['metric']=='P0_c_ENS_kWh')
    assert row['reserve_only_delta']==-2. and row['preparation_only_delta']==-3.
    assert row['both_delta']==-6. and row['interaction_delta']==-1.

def test_hardware_each_compares_H0_and_retains_conditional_economics():
    a=api();rows=a.hardware_differences(a.flatten_records([
        result(ens=5.),result(ens=3.,config='H1'),result(ens=4.,config='H2')]))
    cold=[x for x in rows if x['metric']=='P0_c_ENS_kWh']
    assert {(x['configuration_id'],x['delta']) for x in cold}=={('H1',-2.),('H2',-1.)}

def test_all_policies_failing_is_not_a_certificate_of_physical_impossibility():
    a=api();rows=a.necessary_conditions([dict(boundary_id='x',all_tested_policies_failed=True)])
    assert rows[0]['physical_impossibility_certified'] is False
    assert rows[0]['status']=='insufficient_optimistic_bound'
    rows=a.necessary_conditions([dict(boundary_id='y',available_shaft_energy_kWh=100.,
        required_shaft_energy_kWh_lower_bound=120.,bound_source='registered independent optimistic energy lower bound')])
    assert rows[0]['physical_impossibility_certified'] is True

def test_unknown_cost_or_emissions_are_never_replaced_with_zero():
    a=api();one=result();one['metrics']['economics']['energy_cost_USD']=None
    one['metrics']['economics']['emissions_conditional']['total_CO2_kg_conditional']=None
    row=a.flatten_records([one])[0]
    assert row['energy_cost_USD'] is None and row['total_CO2_kg_conditional'] is None

def test_export_is_standalone_CSV_MAT_and_json_without_a_plot_backend(tmp_path):
    a=api();tables=a.build_tables([result(ens=1.),result('A_sci_v21',ens=0.)],repetitions=100)
    receipt=a.export_tables(tables,tmp_path/'analysis')
    assert (tmp_path/'analysis'/'PAIRED_DIFFERENCES.csv').is_file()
    assert (tmp_path/'analysis'/'SCI_TABLES.mat').is_file()
    assert receipt['scientific_figures_generated']==0


def test_process_export_preserves_native_intervals_and_checks_raw_hash(tmp_path):
    import gzip,hashlib
    payload=dict(execution_model_id='M21_sci_engineering',scenario={'id':'unit_fixture'},
        summary={'status':'completed','policy_id':'C_sci_v21'},segments=[])
    for start,end in [(0.,.125),(.125,1.5)]:
        payload['segments'].append(dict(start_minute=start,end_minute=end,
            loads_kW=[[1.,2.,3.]]*4,unserved_kW=[[0.,0.,0.]]*4,
            service_request_ledger=dict(original_demand_kW=[[1.,2.,3.]]*4,active_cut_kW=[[0.,0.,0.]]*4,
                connected_unserved_kW=[[0.,0.,0.]]*4,delivered_kW=[[1.,2.,3.]]*4),
            action={'gt_electric_kW':[1.,0.,0.],'electric_chiller_cooling_kW':[0.,0.,0.]},
            m21_storage={'ups':{'initial_kWh':1.,'final_kWh':.9}}))
    raw=tmp_path/'unit.json.gz'
    with gzip.open(raw,'wt',encoding='utf-8') as f:json.dump(payload,f)
    digest=hashlib.sha256(raw.read_bytes()).hexdigest()
    f=api('figures')
    with pytest.raises(ValueError,match='hash|SHA'):
        f.export_process(raw,tmp_path/'bad','0'*64)
    receipt=f.export_process(raw,tmp_path/'actual',digest)
    assert receipt['native_intervals']==2 and receipt['resampled'] is False
    import csv
    rows=list(csv.DictReader((tmp_path/'actual'/'PROCESS.csv').open(encoding='utf-8-sig')))
    assert [float(r['start_minute']) for r in rows]==[0.,.125]
    assert [float(r['end_minute']) for r in rows]==[.125,1.5]


def test_figure_contract_requires_actual_tables_and_never_draws_placeholder(tmp_path):
    f=api('figures')
    with pytest.raises(ValueError,match='actual|table'):
        f.figure_contract(tmp_path)
    (tmp_path/'CASE_METRICS.csv').write_text('scenario_id\n',encoding='utf-8')
    contract=f.figure_contract(tmp_path)
    assert contract['plot_backend']=='MATLAB'
    assert contract['scientific_figures_generated']==0
    assert contract['native_interval_processes_only'] is True


def test_registration_without_terminal_result_remains_in_the_analysis_ledger(tmp_path):
    a=api();campaign=tmp_path/'campaign';campaign.mkdir()
    policies=[dict(policy_id=x,display_alias=x[0],config={}) for x in ('C_sci_v21','A_sci_v21')]
    scene=dict(id='s1',source_block_id='b1',canonical_class='L2',season_quarter=3,duration_steps=40,step_minutes=15.)
    plan=dict(model_id='M21_sci_engineering',phase='formal',panel={'scenarios':[scene]},policy_specs=policies,configurations={'H0':{}})
    (campaign/'PLAN.json').write_text(json.dumps(plan),encoding='utf-8')
    (campaign/'MANIFEST.json').write_text(json.dumps({'simulator_hash':'source_one'}),encoding='utf-8')
    target=campaign/'pairs'/'s1__C_sci_v21__H0'/'attempt_0001';target.mkdir(parents=True)
    completed=result();completed.pop('_scenario');completed.pop('_policy')
    (target/'RESULT.json').write_text(json.dumps(completed),encoding='utf-8')
    rows=a.collect_results([campaign])
    assert len(rows)==2
    pending=next(r for r in rows if r['policy_id']=='A_sci_v21')
    assert pending['status']=='not_started'
    flattened=a.flatten_records(rows)
    assert next(r for r in flattened if r['policy_id']=='A_sci_v21')['eligible'] is False


def test_failed_without_metrics_keeps_known_registered_window_and_unknown_service():
    a=api();row=result(status='failed_unknown');row['metrics']=None
    row['_scenario'].update(duration_steps=40,step_minutes=15.)
    flattened=a.flatten_records([row])[0]
    assert flattened['window_minutes']==600.
    assert flattened['P0_c_ENS_kWh'] is None


def test_ordinary_five_policy_panel_is_not_mistaken_for_a_four_cell_mechanism():
    a=api();rows=[]
    for alias in ('R','M','F','C','A'):
        row=result(alias+'_sci_v21')
        row['_policy']['config']=dict(reserve_enabled=alias in ('F','C','A'),
            critical_preparation_first=alias in ('C','A'),legacy_C19_order=False)
        rows.append(row)
    assert a.mechanism_effects(a.flatten_records(rows))==[]


def test_initial_handoff_is_separated_from_later_endurance_and_recovery_gaps():
    a=api();one=result(window=180.)
    one['_scenario'].update(first_event_minute=15.,initial_handoff_window_minutes=15.,
        recovery_start_minute=150.)
    one['metrics']['critical_union'].update(material_minutes=40.,intervals=[[15.,35.],[90.,100.],[160.,170.]])
    row=a.flatten_records([one])[0]
    assert row['critical_initial_handoff_gap_minutes']==15.
    assert row['critical_post_handoff_gap_minutes']==15.
    assert row['critical_recovery_gap_minutes']==10.
    # The first 15 min are the registered handoff window, not a device state.
    assert row['critical_first_observed_clean_minute']==35.
    assert row['critical_first_gap_after_clean_minute']==90.
    assert row['critical_observed_continuous_minutes']==55.
    assert row['critical_continuity_right_censored'] is False


def test_initial_outage_does_not_turn_unknown_or_unobserved_handoff_into_zero():
    a=api();one=result(window=180.)
    one['_scenario'].update(first_event_minute=15.,initial_handoff_window_minutes=15.,recovery_start_minute=150.)
    one['metrics']['critical_union'].update(material_minutes=165.,intervals=[[15.,180.]])
    row=a.flatten_records([one])[0]
    assert row['critical_post_handoff_gap_minutes']==120.
    assert row['critical_first_observed_clean_minute'] is None
    assert row['critical_observed_continuous_minutes'] is None
    assert row['critical_continuity_status']=='no_observed_clean_boundary_before_recovery'
    one['metrics']['critical_union']=dict(material_minutes=165.)
    row=a.flatten_records([one])[0]
    assert row['critical_initial_handoff_gap_minutes'] is None
    assert row['critical_continuity_status']=='unknown_missing_intervals'


def test_no_later_gap_is_right_censored_at_registered_recovery_and_phase_ENS_keeps_carriers():
    a=api();one=result(window=180.)
    one['_scenario'].update(first_event_minute=15.,initial_handoff_window_minutes=15.,recovery_start_minute=150.)
    one['metrics']['critical_union'].update(material_minutes=15.,intervals=[[15.,30.]])
    one['metrics']['phase_ENS_kWh']={'initial_handoff':{'P0':{'e':1.,'h':2.,'c':3.}},
        'endurance':{'P0':{'e':4.,'h':5.,'c':6.}}}
    row=a.flatten_records([one])[0]
    assert row['critical_first_observed_clean_minute']==30.
    assert row['critical_observed_continuous_minutes']==120.
    assert row['critical_continuity_right_censored'] is True
    assert row['critical_continuity_limit_minute']==150.
    assert row['phase_initial_handoff_P0_c_ENS_kWh']==3.
    assert row['phase_endurance_P0_e_ENS_kWh']==4.


def test_registered_rejection_capacity_is_a_separate_sourced_optimistic_condition():
    a=api();one=result()
    one['_scenario'].update(demand_amplitude=1.25,rejection_capacity_kW=110.,
        critical_cooling_kW_lower_bound=100.,rejection_bound_source='Registered shared EC+tower necessary path')
    row=a.flatten_records([one])[0]
    assert row['rejection_capacity_kW']==110.
    assert row['required_rejection_kW_lower_bound']==pytest.approx(100.*(1.+.77/3.51685))
    tables=a.build_tables([one],repetitions=100)
    bound=tables['NECESSARY_BOUNDARY_CONDITIONS'][0]
    assert bound['physical_impossibility_certified'] is True
    assert bound['certificates']=='rejection_lower_bound_exceeds_capacity'
    assert bound['online_policy_ranking'] is False


def test_interaction_uses_only_registered_same_propagation_four_cells_and_carriers():
    a=api();records=[]
    for cell,ens in [('normal',1.),('fault_only',4.),('shock_only',3.),('fault_and_shock',9.)]:
        row=result(scene=cell,ens=ens)
        row['_scenario'].update(interaction_group_id='independent_factor_1',interaction_cell=cell,enable_propagation=True)
        records.append(row)
    out=a.build_tables(records,repetitions=100)['DISTURBANCE_INTERACTIONS']
    cold=next(r for r in out if r['priority']=='P0' and r['carrier']=='c')
    assert cold['interaction_ENS_kWh']==3.
    assert cold['status']=='complete_matched_four_cells' and cold['source_block_id']=='b1'
    assert len(out)==12
    records[2]['status']='failed_unknown'
    out=a.build_tables(records,repetitions=100)['DISTURBANCE_INTERACTIONS']
    assert all(r['interaction_ENS_kWh'] is None and r['status']=='unknown_or_failed_cell' for r in out)


def test_interaction_rejects_changed_protection_source_or_window_and_keeps_missing_cells():
    a=api();rows=[]
    for cell in ('normal','fault_only','shock_only','fault_and_shock'):
        row=result(scene=cell)
        row['_scenario'].update(interaction_group_id='g1',interaction_cell=cell,enable_propagation=True)
        rows.append(row)
    rows[1]['_scenario']['enable_propagation']=False
    with pytest.raises(ValueError,match='propagation|protection'):
        a.build_tables(rows,repetitions=100)
    rows[1]['_scenario']['enable_propagation']=True
    incomplete=a.build_tables(rows[:3],repetitions=100)['DISTURBANCE_INTERACTIONS']
    assert len(incomplete)==12
    assert all(r['status']=='incomplete_four_cells' and r['interaction_ENS_kWh'] is None for r in incomplete)


def test_process_uses_original_service_ledger_including_active_disconnection(tmp_path):
    import gzip,hashlib,csv
    ledger=dict(original_demand_kW=[[10.,20.,30.]]*4,active_cut_kW=[[3.,4.,5.]]*4,
        connected_unserved_kW=[[1.,2.,3.]]*4,delivered_kW=[[6.,14.,22.]]*4)
    payload=dict(execution_model_id='M21_sci_engineering',scenario={'id':'unit_fixture'},
        summary={'status':'completed'},segments=[dict(start_minute=0.,end_minute=1.,
            loads_kW=[[7.,16.,25.]]*4,unserved_kW=[[1.,2.,3.]]*4,service_request_ledger=ledger)])
    raw=tmp_path/'unit.json.gz'
    with gzip.open(raw,'wt',encoding='utf-8') as stream:json.dump(payload,stream)
    f=api('figures');f.export_process(raw,tmp_path/'process',hashlib.sha256(raw.read_bytes()).hexdigest())
    row=list(csv.DictReader((tmp_path/'process'/'PROCESS.csv').open(encoding='utf-8-sig')))[0]
    assert float(row['P0_c_requested_kW'])==30.
    assert float(row['P0_c_delivered_kW'])==22.
    assert float(row['P0_c_unserved_kW'])==8.
    assert float(row['P0_c_disconnected_kW'])==5.


def sidecar_result(tmp_path):
    """Receipt fixture only: no simulated experiment or plotted data."""
    import gzip,hashlib
    one=result();main=tmp_path/'UNIT_TRAJECTORY.fixture';main.write_bytes(b'Unit main artifact fixture')
    rows=[dict(index=0,name='Unit balance',activity=1.,upper=1.)]
    canonical=json.dumps(rows,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    entry=dict(schema='hospital_ies.solver_validation_rows.sidecar.v1',record_id='rows_000000001',location=[0,'validation'],
        rows=rows,rows_sha256=hashlib.sha256(canonical).hexdigest(),row_count=1)
    path=tmp_path/'solver_rows.jsonl.gz'
    with path.open('wb') as binary:
        with gzip.GzipFile(filename='',fileobj=binary,mode='wb',mtime=0) as stream:
            stream.write((json.dumps(entry,ensure_ascii=False)+'\n').encode('utf-8'))
    receipt=dict(schema=entry['schema'],path=str(path),closed=True,status='completed',entry_count=1,row_count=1,
        attempted_entry_count=1,gzip_bytes=path.stat().st_size,gzip_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        write_error=None,close_error=None,body_exception=None,partial_tail_possible=False)
    one.update(raw_path=str(main),raw_sha256=hashlib.sha256(main.read_bytes()).hexdigest(),raw_bytes=main.stat().st_size,
        raw_total_bytes=main.stat().st_size+path.stat().st_size,solver_row_evidence=receipt,
        resource_measurement=dict(peak_sampled_rss_bytes=123456,peak_sampled_private_bytes=234567))
    return one,path,entry


def test_resource_size_includes_solver_sidecar_and_reports_verified_integrity_counts(tmp_path):
    a=api();one,path,entry=sidecar_result(tmp_path);tables=a.build_tables([one],repetitions=100)
    row=tables['RESOURCE_MEASUREMENTS'][0]
    assert row['raw_bytes']==Path(one['raw_path']).stat().st_size
    assert row['solver_rows_gzip_bytes']==path.stat().st_size
    assert row['raw_total_bytes']==row['raw_bytes']+row['solver_rows_gzip_bytes']
    assert row['raw_size_for_resource_statistics_bytes']==row['raw_total_bytes']
    assert row['resource_peak_sampled_rss_bytes']==123456
    count=tables['EVIDENCE_INTEGRITY_SUMMARY'][0]
    assert count['solver_sidecar_verified_cases']==1 and count['solver_row_entries_verified']==1
    assert count['solver_rows_verified']==1 and count['evidence_drift_cases']==0


@pytest.mark.parametrize('damage',['missing','gzip_sha','entry_sha_with_new_gzip_sha','count','nonobject'])
def test_completed_metrics_become_unknown_when_required_solver_sidecar_drifts(tmp_path,damage):
    import gzip,hashlib
    a=api();one,path,entry=sidecar_result(tmp_path)
    if damage=='missing':path.unlink()
    elif damage=='gzip_sha':one['solver_row_evidence']['gzip_sha256']='0'*64
    elif damage=='count':one['solver_row_evidence']['row_count']=2
    else:
        if damage=='nonobject':entry=['Invalid sidecar entry fixture']
        else:entry['rows'][0]['activity']=99.
        with gzip.open(path,'wt',encoding='utf-8') as stream:stream.write(json.dumps(entry)+'\n')
        one['solver_row_evidence'].update(gzip_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),gzip_bytes=path.stat().st_size)
        one['raw_total_bytes']=one['raw_bytes']+path.stat().st_size
    tables=a.build_tables([one],repetitions=100);row=tables['CASE_METRICS'][0]
    assert row['recorded_status']=='completed' and row['status']=='evidence_drift'
    assert row['eligible'] is False and row['P0_c_ENS_kWh'] is None
    assert row['evidence_complete'] is False
    assert tables['EVIDENCE_INTEGRITY_SUMMARY'][0]['evidence_drift_cases']==1
