"""Immutable post-processing of 123 saved V20 targets; no environment execution."""
from pathlib import Path
from collections import Counter,defaultdict
import argparse
import csv
import hashlib
import json
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path[:0]=[str(HERE),str(ROOT),str(ROOT/'src')]
csv.field_size_limit(16*1024*1024)
from guards import require,finite,saved_equal,unique_index,merge_runs,event_counts,validate_clock_pair,validate_propagation_pair
from publication import release_gate as gate
from scripts import v20_report as report
from scripts.v20_campaign import campaign_jobs
from tools.model_closeout.emissions import evaluate_emissions

RUN=ROOT/gate.RUN_REL
READBACK=RUN/'paper_results/FIXED_SENS_VERIFIED01'
COHORTS={'fixed8':96,'propagation':18,'open1':6,'contact025':3}
P=('P0','P1','P2','P3');C=('e','h','c')
CASE='CAMX2023r2_DG75pct'
CLOCK_PAIRS=(('v20_main_003','v20_open1_000','open1'),('v20_main_067','v20_open1_001','open1'),('v20_long_005','v20_contact025_000','contact025'))
PROP_PAIRS=(('v20_propagation_000','v20_propagation_001'),('v20_propagation_002','v20_propagation_003'),('v20_propagation_004','v20_propagation_005'))
INPUTS={}

def observed(path):
    path=Path(path);require(path.suffix.lower()=='.json','Only compact JSON may be opened by observed()')
    content=path.read_bytes();actual=hashlib.sha256(content).hexdigest()
    INPUTS[str(path.resolve())]=dict(bytes=len(content),sha256=actual)
    return json.loads(content.decode('utf-8-sig'))

def table(name):
    path=READBACK/(name+'.csv');INPUTS[str(path.resolve())]=dict(bytes=path.stat().st_size,sha256=gate.sha(path))
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def primary(job):
    return ('registered_full',[0.,float(job['scene']['duration_steps'])*float(job['scene'].get('step_minutes',15.))]) if job['cohort'] in ('long','contact025') else ('full',[0.,600.])

def selected_jobs():
    all_jobs=campaign_jobs();targets=[j for j in all_jobs if j['cohort'] in COHORTS]
    require(len(targets)==41 and Counter(j['cohort'] for j in targets)==Counter(fixed8=32,propagation=6,open1=2,contact025=1),'Target batch denominator differs')
    require(Counter({c:sum(len(j['policies']) for j in targets if j['cohort']==c) for c in COHORTS})==Counter(COHORTS),'Target pair denominator differs')
    names={j['name'] for j in targets}|{p[0] for p in CLOCK_PAIRS}
    jobs=[j for j in all_jobs if j['name'] in names]
    require(len(jobs)==44,'Missing/ambiguous fixed clock base')
    return targets,jobs,{j['name']:j for j in jobs}

def facts(proof,job):
    return dict(actual_initial_state_sha256=proof['matching']['actual_initial_state_sha256'],
                actual_exogenous_sha256=proof['matching']['registered_stream_sha256'],
                source_binding_sha256=proof['source_binding_sha256'],model_config_sha256=proof['model_config_sha256'],
                runtime_inputs_sha256=proof['runtime_inputs_sha256'],physical_scenario_signature=job['scene']['physical_scenario_signature'])

def context(job,alias,record,path):
    return dict(batch=job['name'],cohort=job['cohort'],scenario_id=job['scene']['id'],alias=alias,
                canonical_class=job['scene']['canonical_class'],source_day_index=job['scene']['source_day_index'],
                policy_id=record['policy_id'],result_sha256=gate.sha(path),result_path=str(path))

def verify_window(metrics,bounds):
    require(metrics['complete'] is True and metrics['metric_identity_valid'] is True and metrics['window_identity_valid'] is True,'Incomplete/wrong saved primary window')
    require([metrics['window_start_minute'],metrics['window_end_minute']]==bounds,'Wrong primary bounds')
    for p in P:
        finite(metrics[p+'_material_union_minutes'],'union')
        for c in C:
            for f in (*gate.SERVICE_FIELDS,'material_minutes'):finite(metrics[p+'_'+c+'_'+f],p+c+f)
    # Partial DG receipts/chemical fuel/economic values remain None where the
    # frozen window evaluator says unknown; no interpolation or zero imputation.

def expected_emission(metrics,registry):return evaluate_emissions([metrics],registry['cases'][CASE])[0]

def preflight():
    targets,jobs,byname=selected_jobs();plans={j['name']:observed(RUN/'experiments'/j['name']/'plan.json') for j in jobs}
    f={k:'a'*64 for k in ('actual_initial_state_sha256','actual_exogenous_sha256','source_binding_sha256','model_config_sha256','runtime_inputs_sha256')}
    definitions=[]
    for left,right,variant in CLOCK_PAIRS:
        require(primary(byname[left])==primary(byname[right]),'Clock counterpart primary bounds differ')
        check=validate_clock_pair(left_scene=byname[left]['scene'],right_scene=byname[right]['scene'],left_config=plans[left]['model_config'],right_config=plans[right]['model_config'],variant=variant,left_facts=f,right_facts=f,left_policies=plans[left]['policy_specs'],right_policies=plans[right]['policy_specs'],left_injection='b'*64,right_injection='b'*64)
        definitions.append(dict(base=left,variant=right,primary_window=primary(byname[left]),registered_definition_check=check,actual_matching='pending_terminal'))
    for left,right in PROP_PAIRS:
        other=dict(f,actual_exogenous_sha256='b'*64)
        check=validate_propagation_pair(byname[left]['scene'],byname[right]['scene'],f,other)
        definitions.append(dict(on=left,off=right,registered_definition_check=check,actual_matching='pending_terminal'))
    path=HERE/'PREPARATION_PROOF.json';require(not path.exists(),'Preparation receipt already retained')
    data=dict(status='prepared_definitions_only',target_batches=41,target_pairs=123,clock_and_propagation_definitions=definitions,
              existing_terminals={c:sum((RUN/'experiments'/j['name']/'BATCH_RESULT.json').is_file() for j in targets if j['cohort']==c) for c in COHORTS},
              actual_outcomes_not_evaluated=True,synthetic_definition_fingerprints_are_not_actual_evidence=True,
              script_sha256=gate.sha(Path(__file__)),guards_sha256=gate.sha(HERE/'guards.py'),physical_episodes_started=0,raw_opened=0,annual_values_read=0)
    with path.open('x',encoding='utf-8') as f:json.dump(data,f,indent=2,allow_nan=False)
    print(json.dumps(data));return 0

def main():
    require(not (HERE/'PROOF.json').exists(),'Existing formal output must not be overwritten')
    targets,jobs,byname=selected_jobs();target_names={j['name'] for j in targets}
    require(all((RUN/'experiments'/j['name']/'BATCH_RESULT.json').is_file() for j in targets),'All 41 target terminal records are required before collection')
    api=gate.load_api(ROOT);freeze_path=RUN/gate.FREEZE_NAME;freeze=observed(freeze_path);freeze_sha=gate.sha(freeze_path)
    require(api.runner.source_binding()==freeze['source_sha256'],'Frozen source changed')
    require(api.gate.test_binding()==freeze['source_bound_test_gate']['test_files_sha256'],'Frozen tests changed')
    require(api.design.runtime_input_inventory(ROOT)==freeze['runtime_inputs'],'Runtime input inventory changed')
    proofs=[gate.qualify_registered_batch(ROOT,j,freeze,freeze_sha,api=api) for j in jobs];proofmap={p['batch']:p for p in proofs}
    require(sum(p['pairs'] for p in proofs if p['batch'] in target_names)==123,'Actual target qualification denominator differs')
    receipt=observed(READBACK/'RECEIPT.json');require(receipt['script_sha256']==gate.sha(ROOT/'scripts/v20_report.py'),'Frozen report writer identity differs')
    for name,expected in receipt['output_files'].items():require(gate.sha(READBACK/name)==expected,'Readback output changed: '+name)
    report_inputs=observed(READBACK/'INPUT_INDEX.json');summary=observed(READBACK/'SUMMARY.json')
    status=table('STATUS');windows=table('WINDOWS');emissions=table('EMISSIONS');oldnew=table('V19_V20_COMPARISON')
    expected_keys={(j['name'],al) for j in jobs for al in j['policies']}
    statusidx=unique_index([r for r in status if r['batch'] in byname],lambda r:(r['batch'],r['alias']),expected_keys,'qualified status')
    expected_windows={(*k,primary(byname[k[0]])[0]) for k in expected_keys}
    wi=unique_index([r for r in windows if (r['batch'],r['alias'],r['window']) in expected_windows],lambda r:(r['batch'],r['alias'],r['window']),expected_windows,'same primary window')
    ei=unique_index([r for r in emissions if (r['batch'],r['alias'],r['window']) in expected_windows and r['emissions_factor_case_id']==CASE],lambda r:(r['batch'],r['alias'],r['window']),expected_windows,'primary emissions')
    oi=unique_index([r for r in oldnew if r['batch'] in target_names and (r['batch'],r['alias'],r['window']) in expected_windows],lambda r:(r['batch'],r['alias'],r['window']),{k for k in expected_windows if k[0] in target_names},'old/new primary comparison')
    registry_path=RUN/'references/EMISSIONS_FACTOR_REGISTRY.json';registry=observed(registry_path)
    require(summary['emissions_registry_sha256']==gate.sha(registry_path),'Registered emission factors differ')
    plans={};records={};contexts={};metrics={};critical={};service=[];union_rows=[];energy=[];version=[];source_refs=[]
    for j in jobs:
        name=j['name'];d=RUN/'experiments'/name;plan=observed(d/'plan.json');plans[name]=plan
        for spec in plan['policy_specs']:
            alias=spec['display_alias'];key=name,alias;path=d/'pairs'/(j['scene']['id']+'__'+spec['policy_id'])/'RESULT.json'
            record=observed(path);records[key]=record;ctx=context(j,alias,record,path);contexts[key]=ctx
            st=statusidx[key];fp=facts(proofmap[name],j)
            require(all(st[k]==v for k,v in dict(result_sha256=ctx['result_sha256'],**fp).items()),'Readback actual status/source fingerprints differ')
            require(st['comparison_eligible']=='True' and st['execution_qualified']=='True' and st['episode_audit_status']=='passed','Target/base readback ineligible')
            wn,bounds=primary(j);m=report.window_metrics(record,wn,bounds);verify_window(m,bounds);metrics[key]=m
            require(all(saved_equal(wi[(*key,wn)].get(k),v) for k,v in m.items()),'Readback primary metrics differ from actual compact RESULT')
            e=expected_emission(m,registry)
            for f in ('grid_CO2_kg','natural_gas_CO2_kg','grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'):
                require(saved_equal(ei[(*key,wn)].get(f),e[f]),'Conditional CO2 differs from frozen formula: '+f)
            emission_fields={k:v for k,v in e.items() if k not in m}
            source_refs.append(dict(ctx,window=wn,window_minutes=bounds))
            saved=record['analysis']['v18']['windows'][wn]['all_registered']['carrier_service'];critical[key]={p:{c:saved[p][c] for c in C} for p in ('P0','P1')}
            for p in P:
                spans=merge_runs([v for c in C for v in saved[p][c]['loss_runs']],bounds)
                require(sum(b-a for a,b in spans)==m[p+'_material_union_minutes'],'Recomputed carrier union differs')
                if name in target_names:
                    union_rows.append(dict(ctx,window=wn,window_minutes=bounds,priority=p,material_union_minutes=sum(b-a for a,b in spans),longest_union_minutes=max((b-a for a,b in spans),default=0.),loss_run_count=len(spans),repeated_segments=len(spans)>1,loss_runs=spans))
                for c in C:
                    detail=saved[p][c]
                    if name in target_names:
                        service.append(dict(ctx,window=wn,window_minutes=bounds,priority=p,carrier=c,unit='kWh_'+c,
                            **{f:m[p+'_'+c+'_'+f] for f in (*gate.SERVICE_FIELDS,'material_minutes')},
                            **{f:detail.get(f) for f in ('longest_material_minutes','first_loss_minute','first_clean_minute','confirmed_30min_interval','recurrence_after_30min_count','recovery_status','loss_runs')}))
            if name in target_names:
                energy.append(dict(ctx,window=wn,window_minutes=bounds,**{f:m[f] for f in gate.ECON_FIELDS},
                    DG_budget_start_kWh_equivalent=m['DG_budget_start_kWh_equivalent'],DG_budget_finish_kWh_equivalent=m['DG_budget_finish_kWh_equivalent'],
                    saved_observed_DG_exhaustion_minutes=m['observed_DG_exhaustion_minutes'],partial_DG_receipt_not_interpolated=m['partial_DG_receipt_not_interpolated'],
                    actual_source_integrals=m['actual_source_integrals'],**emission_fields))
                comp=oi[(*key,wn)]
                if comp['comparison_valid']!='True':
                    version.append(dict(ctx,comparison_valid=False,reason='Frozen old/new comparison did not qualify; old values remain unknown'));continue
                oldpath=Path(comp['old_result_path']);require(gate.sha(oldpath)==comp['old_result_sha256'],'Old RESULT SHA differs')
                old=observed(oldpath);before=report.window_metrics(old,wn,bounds,expected_model='M19_model_closeout')
                for f in (*[p+'_material_union_minutes' for p in P],*[p+'_'+c+'_'+s for p in P for c in C for s in gate.SERVICE_FIELDS],*report.ECON_FIELDS):
                    require(saved_equal(comp['old_'+f],before[f]) and saved_equal(comp['new_'+f],m[f]),'Old/new readback values differ')
                    delta=m[f]-before[f] if m[f] is not None and before[f] is not None else None
                    require(saved_equal(comp['delta_'+f],delta),'Old/new delta differs')
                prior=old['analysis']['v18']['windows'][wn]['all_registered']['carrier_service']
                for p in ('P0','P1'):
                    for c in C:
                        version.append(dict(ctx,window=wn,window_minutes=bounds,comparison_valid=True,old_result_path=str(oldpath),old_result_sha256=comp['old_result_sha256'],priority=p,carrier=c,
                            old_material_minutes=prior[p][c]['material_minutes'],new_material_minutes=saved[p][c]['material_minutes'],
                            delta_material_minutes=saved[p][c]['material_minutes']-prior[p][c]['material_minutes'],old_ENS_kWh=prior[p][c]['ENS_kWh'],new_ENS_kWh=saved[p][c]['ENS_kWh'],old_request_delivery_unknown=True))
    stable_dirs=[(RUN/'experiments'/j['name']).resolve() for j in jobs]
    stable_dirs.extend(Path(r['old_result_path']).parents[2].resolve() for r in version if r.get('old_result_path'))
    stable_inputs=[]
    for row in report_inputs:
        path=Path(row['path']).resolve()
        if any(path.is_relative_to(d) for d in stable_dirs):
            require(path.suffix.lower() in ('.json','.jsonl','.csv'),'Unexpected non-metadata report input; do not open raw')
            require(gate.sha(path)==row['sha256'],'Stable report input changed');stable_inputs.append(row)
    require(all(path in {str(Path(r['path']).resolve()) for r in stable_inputs} for path in INPUTS if Path(path).name=='RESULT.json'),'Actual RESULT absent from bound report input index')
    events={};eventrows=[];event_summary=[]
    for j in jobs:
        d=RUN/'experiments'/j['name'];terminal=observed(d/'BATCH_RESULT.json');export=terminal['analysis_receipt']['compatibility_export_receipt']
        path=d/'analysis/EVENT_TIMELINE.csv';expected=export['output_sha256']['EVENT_TIMELINE.csv'];require(gate.sha(path)==expected,'Actual event CSV terminal SHA differs')
        INPUTS[str(path.resolve())]=dict(bytes=path.stat().st_size,sha256=expected)
        own=defaultdict(list)
        with path.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                k=row.get('kind','')
                if k not in ('protection_trip','no_export_interlock','minimum_output_interlock','diesel_fuel_exhausted','gt_actual_started','gt_actual_stopped','exogenous_fault_start','exogenous_fault_end') and not k.startswith('grid_') and 'contact' not in k:continue
                own[row['policy_id']].append({n:row.get(n) for n in ('event_id','kind','time_minute','device','origin','cause_class','reason','trigger','hold_minutes','stress_ratio','protection_reference_kW','start_minute','trip_time_minute','before','after','actual_kW','remaining_budget_kWh','grid_bus_state','grid_close_minute','command_id')})
        for alias in j['policies']:
            key=j['name'],alias;ctx=contexts[key];wn,bounds=primary(j);saved=own[records[key]['policy_id']];events[key]=saved
            counted=event_counts(saved,bounds);prop=records[key]['analysis']['v18'].get('propagation')
            cause_lists={'residual_stress_protection':'residual_stress_protection_events','no_export_isolation':'no_export_interlock_events','minimum_output_interlock':'minimum_output_interlock_events'}
            for cause,field in cause_lists.items():
                v=prop.get(field) if isinstance(prop,dict) else None
                if isinstance(v,list):require(event_counts(v,bounds)[cause]==counted[cause],'Actual trip export and compact cause list differ')
                else:counted[cause]=None
            if j['name'] in target_names:
                event_summary.append(dict(ctx,window=wn,window_minutes=bounds,**counted,functional_loss_count=None,functional_loss_status='not_directly_logged; zero source output is not failure'))
            for e in saved:
                t=e.get('time_minute')
                if t not in ('',None) and bounds[0]<=float(t)<bounds[1]:eventrows.append(dict(ctx,window=wn,**e))
    pair_fields=[*[p+'_material_union_minutes' for p in P],*[p+'_'+c+'_'+f for p in P for c in C for f in (*gate.SERVICE_FIELDS,'material_minutes')],*report.ECON_FIELDS]
    def pair_values(lk,rk):
        left,right=metrics[lk],metrics[rk]
        require([left['window_start_minute'],left['window_end_minute']]==[right['window_start_minute'],right['window_end_minute']],'Paired bounds differ')
        row={}
        row['requested_integral_max_abs_difference_kWh']=max(abs(left[p+'_'+c+'_requested_kWh']-right[p+'_'+c+'_requested_kWh']) for p in P for c in C)
        for f in pair_fields:
            x,y=left[f],right[f];row['base_'+f]=x;row['variant_'+f]=y;row['delta_'+f]=y-x if x is not None and y is not None else None
        for f in ('grid_plus_gas_CO2_kg','DG_CO2_kg_conditional','total_CO2_kg_conditional'):
            x,y=expected_emission(left,registry)[f],expected_emission(right,registry)[f];row['base_'+f]=x;row['variant_'+f]=y;row['delta_'+f]=y-x if x is not None and y is not None else None
        row.update(base_critical=critical[lk],variant_critical=critical[rk],base_actual_protection=event_counts(events[lk],[left['window_start_minute'],left['window_end_minute']]),variant_actual_protection=event_counts(events[rk],[right['window_start_minute'],right['window_end_minute']]))
        return row
    clock=[];propagation=[]
    for left,right,variant in CLOCK_PAIRS:
        for index,alias in enumerate(byname[left]['policies']):
            lk=left,alias;rk=right,alias
            match=validate_clock_pair(left_scene=byname[left]['scene'],right_scene=byname[right]['scene'],left_config=plans[left]['model_config'],right_config=plans[right]['model_config'],variant=variant,left_facts=facts(proofmap[left],byname[left]),right_facts=facts(proofmap[right],byname[right]),left_policies=plans[left]['policy_specs'],right_policies=plans[right]['policy_specs'],left_injection=proofmap[left]['matching']['injection_state_sha256'][index],right_injection=proofmap[right]['matching']['injection_state_sha256'][index])
            clock.append(dict(base_batch=left,variant_batch=right,variant=variant,alias=alias,window_minutes=[metrics[lk]['window_start_minute'],metrics[lk]['window_end_minute']],matching=match,**pair_values(lk,rk)))
    for left,right in PROP_PAIRS:
        match=validate_propagation_pair(byname[left]['scene'],byname[right]['scene'],facts(proofmap[left],byname[left]),facts(proofmap[right],byname[right]))
        for alias in byname[left]['policies']:
            propagation.append(dict(on_batch=left,off_batch=right,propagation_pair_id=byname[left]['scene']['propagation_pair_id'],alias=alias,window_minutes=[0.,600.],delta_direction='OFF minus ON',matching=match,**pair_values((left,alias),(right,alias))))
    aggregates=[]
    for cls in ('L1','L2','L3','L4'):
        for alias in ('E','K1','C19'):
            for p in P:
                for c in C:
                    own=[r for r in service if r['cohort']=='fixed8' and r['canonical_class']==cls and r['alias']==alias and r['priority']==p and r['carrier']==c]
                    require(len(own)==8,'Fixed-eight class/strategy denominator differs')
                    aggregates.append(dict(cohort='fixed8',canonical_class=cls,alias=alias,priority=p,carrier=c,n=8,**{f:sum(r[f] for r in own) for f in (*gate.SERVICE_FIELDS,'material_minutes')},material_cases=sum(r['material_minutes']>0 for r in own),max_longest_material_minutes=max(r['longest_material_minutes'] for r in own),repeated_segment_cases=sum(len(r['loss_runs'])>1 for r in own)))
    require(len(service)==123*12 and len(clock)==len(propagation)==9 and len(energy)==123,'Final target service/pair denominator differs')
    tables=dict(PRIMARY_SERVICE=service,PRIMARY_UNION=union_rows,PRIMARY_ENERGY=energy,FIXED8_AGGREGATE_SERVICE=aggregates,V19_V20_CRITICAL=version,CLOCK_PAIRS=clock,PROPAGATION_PAIRS=propagation,ACTUAL_EVENT_COUNTS=event_summary,SELECTED_EVENTS=eventrows)
    require(api.runner.source_binding()==freeze['source_sha256'],'Source changed during read-only analysis')
    for name,rows in tables.items():report.csv_new(HERE/(name+'.csv'),rows)
    analysis=dict(schema='hospital_ies.v20.fixed_background_and_clock_analysis.v1',scope='123 target trajectories only; fixed eight backgrounds are already seen; not all406',target_pairs=COHORTS,
                  primary_windows={c:'registered_full[0,1065)' if c=='contact025' else 'full[0,600)' for c in COHORTS},source_freeze_sha256=freeze_sha,batch_proofs=proofs,
                  stable_report_inputs=stable_inputs,source_refs=source_refs,clock_pairs=clock,propagation_pairs=propagation,table_rows={k:len(v) for k,v in tables.items()},
                  source_priority_allocation='unidentified common bus; do not allocate DG energy to priorities',old_request_delivery_unknown=True,raw_opened=0,annual_values_read=0,physical_episodes_started=0,other_cohorts_not_claimed_complete=True)
    with (HERE/'ANALYSIS.json').open('x',encoding='utf-8') as f:json.dump(analysis,f,ensure_ascii=False,indent=2,allow_nan=False)
    with (HERE/'INPUT_INDEX.json').open('x',encoding='utf-8') as f:json.dump(INPUTS,f,ensure_ascii=False,indent=2,allow_nan=False)
    proof=dict(status='passed',target_batches=41,target_pairs=123,target_cohort_pairs=COHORTS,clock_pairs=9,propagation_pairs=9,
               script_sha256=gate.sha(Path(__file__)),guards_sha256=gate.sha(HERE/'guards.py'),analysis_sha256=gate.sha(HERE/'ANALYSIS.json'),input_index_sha256=gate.sha(HERE/'INPUT_INDEX.json'),
               output_files={name+'.csv':gate.sha(HERE/(name+'.csv')) for name in tables},report_receipt_sha256=gate.sha(READBACK/'RECEIPT.json'),
               raw_opened=0,annual_values_read=0,physical_episodes_started=0,partial_DG_unknown_preserved=True)
    with (HERE/'PROOF.json').open('x',encoding='utf-8') as f:json.dump(proof,f,indent=2,allow_nan=False)
    print(json.dumps(proof));return 0

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--preflight-only',action='store_true');args=parser.parse_args()
    raise SystemExit(preflight() if args.preflight_only else main())
