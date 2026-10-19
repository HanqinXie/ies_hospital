"""Read saved compact V20 long/compat evidence; never import an environment."""
from pathlib import Path
import csv
import json
import sys
from collections import defaultdict

# Saved command/event JSON cells exceed Python's 128 KiB CSV default. This is
# an input parser bound for this read-only process, not a numerical tolerance.
csv.field_size_limit(16*1024*1024)

ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from publication import release_gate as gate
from scripts import v20_report as report
from scripts.v20_campaign import campaign_jobs

OUT=Path(__file__).resolve().parent
RUN=ROOT/gate.RUN_REL
READBACK=RUN/'paper_results/LONG_COMPAT_VERIFIED01'
CASE='CAMX2023r2_DG75pct'
P=('P0','P1','P2','P3');C=('e','h','c')
SERVICE=('requested_kWh','delivered_kWh','ENS_kWh','disconnected_ENS_kWh','connected_unserved_ENS_kWh')
INPUTS={}


def observed(path):
    path=Path(path);INPUTS[str(path)]=gate.sha(path)
    return gate.read(path)


def table(name):
    path=READBACK/(name+'.csv');INPUTS[str(path)]=gate.sha(path)
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def number(row,key):
    value=row.get(key)
    return None if value in ('',None) else float(value)


def saved_equal(cell,value):
    if value is None:return cell in ('',None)
    if isinstance(value,bool):return cell==str(value)
    if isinstance(value,(int,float)):return cell not in ('',None) and float(cell)==value
    if isinstance(value,(dict,list)):return json.loads(cell)==value
    return cell==value


def window_key(row):return row['batch'],row['alias'],row['window']


def context(job,alias,record):
    return dict(batch=job['name'],cohort=job['cohort'],alias=alias,scenario_id=job['scene']['id'],
        source_day_index=job['scene']['source_day_index'],diesel_design=job['scene'].get('diesel_design'),
        policy_id=record['policy_id'],result_model_id=record['model_id'])


def main():
    jobs=campaign_jobs();targets=[j for j in jobs if j['cohort'] in ('long','compat')]
    assert len(targets)==15 and sum(len(j['policies']) for j in targets)==31
    assert all((RUN/'experiments'/j['name']/'BATCH_RESULT.json').is_file() for j in targets)
    api=gate.load_api(ROOT);freeze_path=RUN/gate.FREEZE_NAME;freeze=observed(freeze_path);freeze_sha=gate.sha(freeze_path)
    assert api.runner.source_binding()==freeze['source_sha256']
    assert api.gate.test_binding()==freeze['source_bound_test_gate']['test_files_sha256']
    assert api.design.runtime_input_inventory(ROOT)==freeze['runtime_inputs']
    receipt=observed(READBACK/'RECEIPT.json')
    assert receipt['script_sha256']==gate.sha(ROOT/'scripts/v20_report.py')
    for name,expected in receipt['output_files'].items():assert gate.sha(READBACK/name)==expected
    report_inputs=observed(READBACK/'INPUT_INDEX.json')
    summary=observed(READBACK/'SUMMARY.json');status=table('STATUS');windows=table('WINDOWS')
    oldnew=table('V19_V20_COMPARISON');oldnewco2=table('V19_V20_EMISSIONS');emissions=table('EMISSIONS');strategy=table('STRATEGY_COMPARISONS')
    selected=[r for r in status if r['cohort'] in ('long','compat')]
    assert len(selected)==31
    flags=('registration_valid','result_bound','batch_terminal_bound','execution_qualified','comparison_eligible','actual_started','actual_finished','registered_full_complete')
    assert all(r['status']=='completed' and all(r[k]=='True' for k in flags) and r['start_event_count']==r['finish_event_count']=='1' and r['episode_audit_status']=='passed' and not r['error'] and not r['analysis_error'] for r in selected)
    full={window_key(r):r for r in windows if r['window']=='registered_full'}
    compat=[r for r in strategy if r['cohort']=='compat' and r['window']=='registered_full']
    assert len(compat)==7 and all(r['comparison_valid']=='True' and r['actual_reset_exogenous_source_config_match']=='True' and r['left_alias']=='C19' and r['right_alias']=='C18_M20' for r in compat)
    counterpart_names={r['left_batch'] for r in compat}
    qualified_jobs=[j for j in jobs if j['name'] in ({j['name'] for j in targets}|counterpart_names)]
    proofs=[gate.qualify_registered_batch(ROOT,j,freeze,freeze_sha,api=api) for j in qualified_jobs]
    proofmap={p['batch']:p for p in proofs}
    assert sum(p['pairs'] for p in proofs if p['cohort'] in ('long','compat'))==31
    em={window_key(r):r for r in emissions if r['window']=='registered_full' and r['emissions_factor_case_id']==CASE}
    registry_path=RUN/'references/EMISSIONS_FACTOR_REGISTRY.json';registry=observed(registry_path)
    assert summary['emissions_registry_sha256']==gate.sha(registry_path)
    from tools.model_closeout.emissions import evaluate_emissions
    records={};contexts={};results=[];services=[];diesel=[];recoveries=[];expected_full={}
    statusindex={(r['batch'],r['alias']):r for r in status}
    needed={(j['name'],a) for j in targets for a in j['policies']}|{(r['left_batch'],'C19') for r in compat}
    for j in qualified_jobs:
        d=RUN/'experiments'/j['name'];plan=observed(d/'plan.json')
        for spec in plan['policy_specs']:
            alias=spec['display_alias'];key=j['name'],alias
            if key not in needed:continue
            record=observed(d/'pairs'/(j['scene']['id']+'__'+spec['policy_id'])/'RESULT.json')
            records[key]=record;ctx=context(j,alias,record);contexts[key]=ctx;a=record['analysis']
            proof=proofmap[j['name']];st=statusindex[key]
            expected_status=dict(result_sha256=gate.sha(d/'pairs'/(j['scene']['id']+'__'+spec['policy_id'])/'RESULT.json'),
                actual_initial_state_sha256=proof['matching']['actual_initial_state_sha256'],
                actual_exogenous_sha256=proof['matching']['registered_stream_sha256'],
                source_binding_sha256=proof['source_binding_sha256'],model_config_sha256=proof['model_config_sha256'],
                runtime_inputs_sha256=proof['runtime_inputs_sha256'],physical_scenario_signature=j['scene']['physical_scenario_signature'])
            assert all(st[k]==v for k,v in expected_status.items())
            for name in ('registered_full','full','fault_active','fault_recovery_to_end'):
                window=a['v18']['windows'].get(name)
                if not window:continue
                assert window['complete'] and a['v20']['service_ledgers'][name]['complete']
                metrics=report.window_metrics(record,name,window['window_minutes']);results.append(dict(ctx,**metrics))
                if name=='registered_full':
                    expected_full[key]=metrics
                    assert all(saved_equal(full[(*key,name)].get(k),v) for k,v in metrics.items())
                for p in P:
                    for c in C:
                        detail=window['all_registered']['carrier_service'][p][c]
                        services.append(dict(ctx,window=name,window_start_minute=window['window_minutes'][0],window_end_minute=window['window_minutes'][1],priority=p,carrier=c,
                            **{f:metrics[p+'_'+c+'_'+f] for f in (*SERVICE,'material_minutes')},
                            **{f:detail.get(f) for f in ('first_loss_minute','first_clean_minute','confirmed_30min_interval','longest_material_minutes','recurrence_after_30min_count','recovery_status','loss_runs')}))
            m=full[(j['name'],alias,'registered_full')];e=em[(j['name'],alias,'registered_full')]
            expected_emission=evaluate_emissions([expected_full[key]],registry['cases'][CASE])[0]
            assert all(saved_equal(e.get(k),expected_emission[k]) for k in ('grid_CO2_kg','natural_gas_CO2_kg','grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'))
            diesel.append(dict(ctx,window_minutes=a['v20']['service_ledgers']['registered_full']['window_minutes'],
                **{f:number(m,f) for f in ('DG_budget_start_kWh_equivalent','DG_budget_finish_kWh_equivalent','DG_electric_work_kWh','DG_shaft_work_kWh','DG_loss_work_kWh','DG_rotor_delta_kWh','DG_fuel_budget_debit_kWh_equivalent','actual_energy_fee_USD','accounting_cost_USD')},
                observed_DG_exhaustion_minutes=json.loads(m['observed_DG_exhaustion_minutes']),
                factor_case_id=CASE,conditional_CO2_kg=number(e,'total_CO2_kg_conditional'),grid_plus_gas_CO2_kg=number(e,'grid_plus_gas_CO2_kg'),conditional_DG_CO2_kg=number(e,'DG_CO2_kg_conditional'),exact_DG_chemical_CO2='unknown',
                saved_target_device_operation=a['v18']['recovery_stages']['target_device_operation'],
                saved_hardware_effective_state=a['v18']['recovery_stages']['hardware_effective_state'],
                saved_GT_summary=a['engineering']['gt']))
            if j['cohort']=='long':
                w=a['v18']['windows']['fault_recovery_to_end'];metric=report.window_metrics(record,'fault_recovery_to_end',w['window_minutes'])
                recoveries.append(dict(ctx,**metric))
    # Exact same-window C19/C18 comparisons, including main registered windows
    # longer than 600. Never substitute main primary/full600 for registered_full.
    pairs=[]
    fields=['actual_energy_fee_USD','accounting_cost_USD','DG_electric_work_kWh','DG_shaft_work_kWh','DG_loss_work_kWh','DG_fuel_budget_debit_kWh_equivalent',*[p+'_material_union_minutes' for p in P],*[p+'_'+c+'_'+s for p in P for c in C for s in SERVICE]]
    match_fields=('actual_initial_state_sha256','actual_exogenous_sha256','source_binding_sha256','model_config_sha256','runtime_inputs_sha256','physical_scenario_signature')
    for match in compat:
        lk=match['left_batch'],'C19';rk=match['right_batch'],'C18_M20';left=full[(*lk,'registered_full')];right=full[(*rk,'registered_full')]
        assert (left['window_start_minute'],left['window_end_minute'])==(right['window_start_minute'],right['window_end_minute'])
        facts={k:statusindex[lk][k]==statusindex[rk][k] and bool(statusindex[lk][k]) for k in match_fields};assert all(facts.values())
        row=dict(scenario_id=match['scenario_id'],C19_batch=lk[0],C18_batch=rk[0],window='registered_full',window_minutes=[number(left,'window_start_minute'),number(left,'window_end_minute')],matching=facts,
            C19_policy_panel_sha256=statusindex[lk]['policy_panel_sha256'],C18_policy_panel_sha256=statusindex[rk]['policy_panel_sha256'],policy_panels_expected_distinct=True)
        for f in fields:
            x,y=number(left,f),number(right,f);row['C19_'+f]=x;row['C18_'+f]=y;row['C18_minus_C19_'+f]=y-x if x is not None and y is not None else None
        for f in ('total_CO2_kg_conditional','grid_plus_gas_CO2_kg','DG_CO2_kg_conditional'):
            x,y=number(em[(*lk,'registered_full')],f),number(em[(*rk,'registered_full')],f)
            row['C19_'+f]=x;row['C18_'+f]=y;row['C18_minus_C19_'+f]=y-x
        pairs.append(row)
    selected_oldnew=[r for r in oldnew if r['cohort'] in ('long','compat') and r['window']=='registered_full']
    assert len(selected_oldnew)==31 and all(r['comparison_valid']=='True' for r in selected_oldnew)
    selected_oldco2=[r for r in oldnewco2 if r['cohort'] in ('long','compat') and r['window']=='registered_full' and r['factor_case_id']==CASE]
    assert len(selected_oldco2)==31 and all(r['comparison_valid']=='True' for r in selected_oldco2)
    oldnewgt=[]
    for r in selected_oldnew:
        path=Path(r['old_result_path']);assert gate.sha(path)==r['old_result_sha256']
        old=observed(path);new=records[(r['batch'],r['alias'])]
        bounds=[number(r,'window_start_minute'),number(r,'window_end_minute')]
        prior=report.window_metrics(old,'registered_full',bounds,expected_model='M19_model_closeout')
        present=expected_full[(r['batch'],r['alias'])]
        compare_fields=[*[p+'_material_union_minutes' for p in P],*[p+'_'+c+'_'+f for p in P for c in C for f in SERVICE],*report.ECON_FIELDS]
        for f in compare_fields:
            assert saved_equal(r['old_'+f],prior[f]) and saved_equal(r['new_'+f],present[f])
            delta=present[f]-prior[f] if present[f] is not None and prior[f] is not None else None
            assert saved_equal(r['delta_'+f],delta)
        old_em=evaluate_emissions([prior],registry['cases'][CASE])[0];new_em=evaluate_emissions([present],registry['cases'][CASE])[0]
        co2=next(x for x in selected_oldco2 if x['scenario_id']==r['scenario_id'] and x['alias']==r['alias'])
        for f in ('grid_CO2_kg','natural_gas_CO2_kg','grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'):
            assert saved_equal(co2['old_'+f],old_em[f]) and saved_equal(co2['new_'+f],new_em[f])
            assert saved_equal(co2['delta_'+f],new_em[f]-old_em[f])
        for device in ('GT_1','GT_2','GT_3'):
            x=old['analysis']['engineering']['gt'][device];y=new['analysis']['engineering']['gt'][device]
            oldnewgt.append(dict(batch=r['batch'],alias=r['alias'],device=device,
                old_model_id=old['model_id'],new_model_id=new['model_id'],old_result_sha256=r['old_result_sha256'],
                old_observed_running_minutes=x['observed_running_minutes'],new_observed_running_minutes=y['observed_running_minutes'],
                old_zero_to_positive_count=x['zero_to_positive_count'],new_zero_to_positive_count=y['zero_to_positive_count'],
                old_positive_to_zero_count=x['positive_to_zero_count'],new_positive_to_zero_count=y['positive_to_zero_count']))
    stable_dirs=[(RUN/'experiments'/j['name']).resolve() for j in qualified_jobs]
    stable_dirs.extend(Path(r['old_result_path']).parents[2].resolve() for r in selected_oldnew)
    checked_report_inputs=[]
    for row in report_inputs:
        path=Path(row['path']).resolve()
        if any(path.is_relative_to(d) for d in stable_dirs):
            assert gate.sha(path)==row['sha256'];checked_report_inputs.append(row)
    assert all(str(Path(p).resolve()) in {str(Path(r['path']).resolve()) for r in checked_report_inputs} for p in INPUTS if Path(p).name=='RESULT.json')
    # Source operation telemetry is an existing compact CSV; no trajectory or
    # annual input is opened. Classify positive output separately from health.
    source_groups={'GT':['GT_1','GT_2','GT_3'],'GRID':['GRID'],'DG':['DIESEL'],'HRSG':['HRSG_TOTAL_STEAM_lbph'],'BOILER':['BOILERS_STEAM_lbph'],'EC':['EC_1_COOLING_kW','EC_2_COOLING_kW','EC_3_COOLING_kW'],'ABS':['ABS_COOLING_kW'],'TOWER':['TOWER_REJECTION_kW']}
    source_rows=[];event_rows=[]
    for j in qualified_jobs:
        terminal=observed(RUN/'experiments'/j['name']/'BATCH_RESULT.json')
        export=terminal['analysis_receipt']['compatibility_export_receipt']
        assert terminal['analysis_receipt']['emissions_registry_sha256']==gate.sha(registry_path)
        path=RUN/'experiments'/j['name']/'analysis/V18_RECOVERY_STAGES.csv';INPUTS[str(path)]=gate.sha(path)
        assert INPUTS[str(path)]==export['v18_output_sha256']['V18_RECOVERY_STAGES.csv']
        with path.open(encoding='utf-8-sig',newline='') as stream:
            segments=defaultdict(list)
            for row in csv.DictReader(stream):
                if row['stage']=='actual_source_interval' and row['actual_sources']:segments[row['policy_id']].append((float(row['start_minute']),float(row['end_minute']),json.loads(row['actual_sources'])))
        for alias in j['policies']:
            key=j['name'],alias
            if key not in needed:continue
            a=records[key]['analysis'];pid=records[key]['policy_id']
            for phase in ('fault_active','fault_recovery_to_end'):
                w=a['v18']['windows'].get(phase)
                if not w:continue
                lo,hi=w['window_minutes']
                for source,keys in source_groups.items():
                    relevant=[(x,y,sum(v[k] for k in keys)) for x,y,v in segments[pid] if x>=lo and y<=hi and all(k in v for k in keys)]
                    covered=sum(y-x for x,y,z in relevant);positive=[(x,y,z) for x,y,z in relevant if z>(0. if source in ('HRSG','BOILER') else 1e-4)]
                    source_rows.append(dict(contexts[key],phase=phase,source=source,window_minutes=[lo,hi],covered_minutes=covered,
                        complete_whole_interval_coverage=abs(covered-(hi-lo))<1e-8,
                        first_observed_positive_interval_start=min((x for x,y,z in positive),default=None),
                        observed_positive_minutes=sum(y-x for x,y,z in positive),
                        integral_native_unit_hours=sum((y-x)/60*z for x,y,z in relevant) if abs(covered-(hi-lo))<1e-8 else None,
                        unit='lb steam' if source in ('HRSG','BOILER') else 'kWh',
                        source_positive_output_is_priority_recovery=False,source_positive_output_is_physical_repair=False))
        path=RUN/'experiments'/j['name']/'analysis/EVENT_TIMELINE.csv';INPUTS[str(path)]=gate.sha(path)
        assert INPUTS[str(path)]==export['output_sha256']['EVENT_TIMELINE.csv']
        wanted={records[(j['name'],alias)]['policy_id'] for alias in j['policies'] if (j['name'],alias) in needed}
        with path.open(encoding='utf-8-sig',newline='') as stream:
            for r in csv.DictReader(stream):
                if r['policy_id'] not in wanted:continue
                kind=r.get('kind','')
                if kind in ('protection_trip','diesel_fuel_exhausted','gt_actual_stopped','gt_actual_started') or kind.startswith('grid_'):
                    fields=('policy_id','event_id','time_minute','kind','device','origin','cause_class','reason','trigger','stress_ratio','protection_reference_kW','hold_minutes','trip_time_minute','actual_kW','remaining_budget_kWh','grid_bus_state','grid_close_minute')
                    event_rows.append(dict(batch=j['name'],**{k:r.get(k) for k in fields}))
    targetnames={j['name'] for j in targets}
    tables=dict(FULL_WINDOW_STATUS=selected,WINDOW_DETAILS=[r for r in results if r['batch'] in targetnames],SERVICE_DETAIL=[r for r in services if r['batch'] in targetnames],
        DG_AND_RECOVERY=diesel,RECOVERY_TAIL=recoveries,COMPAT_EXACT_PAIRS=pairs,V19_V20_FULL=selected_oldnew,V19_V20_CO2_FULL=selected_oldco2,EQUIPMENT_SOURCE_PHASES=source_rows,GT_RUNTIME_OLD_NEW=oldnewgt,SELECTED_EVENTS=event_rows)
    for name,rows in tables.items():report.csv_new(OUT/(name+'.csv'),rows)
    data=dict(schema='hospital_ies.v20.long_compat_analysis.v1',qualified_target_pairs=31,long_pairs=24,compat_pairs=7,normal_or_other_pairs_not_completed_claimed=True,
        source_freeze_sha256=freeze_sha,report_summary=summary,batch_proofs=proofs,compat_comparisons=pairs,checked_stable_readback_inputs=checked_report_inputs,
        long_full=[r for r in results if r['cohort']=='long' and r['window']=='registered_full'],recovery_tails=recoveries,
        diesel_and_recovery=diesel,source_phases=source_rows,old_new_full=selected_oldnew,old_new_co2=selected_oldco2,GT_runtime_old_new=oldnewgt,selected_events=event_rows,
        table_rows={k:len(v) for k,v in tables.items()},old_request_delivery_unknown=True,no_raw_opened=True,no_annual_values_read=True,no_new_physical_episode=True,original_final12_unopened=True)
    assert len(data['long_full'])==24 and len(recoveries)==24 and len(pairs)==7
    data['analysis_script_sha256']=gate.sha(Path(__file__))
    gate.require(api.runner.source_binding()==freeze['source_sha256'],'Source changed during analysis')
    with (OUT/'ANALYSIS.json').open('x',encoding='utf8') as f:json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
    with (OUT/'INPUT_INDEX.json').open('x',encoding='utf8') as f:json.dump(INPUTS,f,ensure_ascii=False,indent=2)
    proof=dict(status='passed',qualified_target_pairs=31,long_pairs=24,compat_pairs=7,compat_exact_registered_full_pairs=7,full_long_minutes=1065.,raw_files_opened=0,annual_values_read=0,physical_episodes_started=0,
        analysis_script_sha256=gate.sha(Path(__file__)),analysis_sha256=gate.sha(OUT/'ANALYSIS.json'),input_index_sha256=gate.sha(OUT/'INPUT_INDEX.json'),table_rows=data['table_rows'],
        report_readback_receipt_sha256=gate.sha(READBACK/'RECEIPT.json') if (READBACK/'RECEIPT.json').exists() else None)
    with (OUT/'PROOF.json').open('x',encoding='utf8') as f:json.dump(proof,f,indent=2)
    print(json.dumps(proof))


if __name__=='__main__':main()
