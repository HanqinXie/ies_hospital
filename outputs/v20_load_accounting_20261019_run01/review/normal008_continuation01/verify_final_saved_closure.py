"""Independent stdlib readback of the saved148/406 chain; no simulation/raw."""
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
import csv
import hashlib
import json
import math

ROOT=Path(__file__).resolve().parents[4]
RUN=ROOT/'outputs/v20_load_accounting_20261019_run01'
HERE=Path(__file__).resolve().parent
S4=HERE/'L72_RUN02'
seen={}

def require(value,message):
    if not value:raise ValueError(message)

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):h.update(block)
    return h.hexdigest()

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def fp(path):
    path=Path(path).resolve(strict=True);require(path.is_relative_to(ROOT),'Evidence escaped root')
    row=dict(path=path.relative_to(ROOT).as_posix(),bytes=path.stat().st_size,sha256=sha(path))
    if row['path'] in seen:require(seen[row['path']]==row,'Evidence changed between repeated observations')
    seen[row['path']]=row;return row

def read(path):fp(path);return json.loads(path.read_text(encoding='utf8'))

def lines(path):fp(path);return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()]

def bind(row):require(fp(ROOT/row['path'])==row,'Fingerprint differs: '+row['path'])

def signed(value):require(value.get('signature')==digest({k:v for k,v in value.items() if k!='signature'}),'Canonical proof signature differs')

def utc(value):return datetime.fromisoformat(value.replace('Z','+00:00'))

def run():
    frozen=read(RUN/'MODEL_FREEZE_ACCOUNTING01.json');signed(frozen)
    require(sha(RUN/'MODEL_FREEZE_ACCOUNTING01.json')=='48baed7a128d2f921d36323330ae4d22e2e6dc83214e8a505b6ba89273439b7b','Freeze changed')
    for mapping in (frozen['source_sha256'],frozen['source_bound_test_gate']['test_files_sha256']):
        for name,value in mapping.items():require(fp(ROOT/name)['sha256']==value,'Frozen source/test bytes differ')
    for row in frozen['runtime_inputs']:bind(row)
    require((len(frozen['source_sha256']),len(frozen['source_bound_test_gate']['test_files_sha256']),len(frozen['runtime_inputs']))==(366,241,28),'Frozen closure denominator differs')
    start=read(S4/'START.json');summary=read(S4/'SUMMARY.json');outer=read(RUN/'logs/normal008_lightweight_run02.command.json')
    require(summary['status']=='passed' and summary['exit_code']==outer['exit_code']==0
        and summary['strict_batches']==148 and summary['qualified_pairs']==406,'S4 actual outcome differs')
    require(summary['script_sha256']==start['script_sha256']==sha(ROOT/'publication/continue_normal008.py')
        =='cce231bf254b00a0af0021f04273ad54134005340f5b9706ce4578cec4823ca7','S4 implementation drift')
    require(summary['actual_scientific_children_started']==1 and len(summary['successful_Popen_proofs'])==1
        and summary['last_child']==summary['successful_Popen_proofs'][0],'S4 unique actual Popen differs')
    child=summary['last_child'];require(child['batch']=='v20_normal_008' and child['successful_Popen'] and child['owned_child_drained'] and child['exit_code']==0,'008 child was not uniquely drained successfully')
    for row in summary['predecessor']['evidence_files']:bind(row)
    require(summary['predecessor']==start['predecessor'] and summary['normal009_resource_peak_unknown'] is True
        and summary['old_measured_resource_batches']==10 and summary['new_measured_resource_batches']==1,'Resource continuation roles changed')
    require(read(RUN/'logs/normal03.command.json')['exit_code']==125
        and read(RUN/'review/supervision03/RESULT.json')['status']=='failed_unknown','Old stop was overwritten as success')
    require(read(HERE/'L72_RUN01/IDLE_STOP_OUTCOME.json')['actual_wait_exit_code']==15
        and read(RUN/'logs/normal008_lightweight_run01.command.json')['exit_code']==15,'Original cadence failure exit15 lost')
    require(read(HERE/'L72_PLAN02/SUMMARY.json')['exit_code']==3
        and read(RUN/'logs/normal008_lightweight_probe02.command.json')['exit_code']==3,'Resource-notadmitted probe exit3 lost')
    for key in ('resource_result','preflight','postflight','final_406_proof','admission'):bind(summary[key])
    for row in summary['final_report'].values():bind(row)
    pre=read(S4/'preflight.json');post=read(S4/'postflight.json');final=read(S4/'FINAL_406_PROOF.json')
    for proof in (pre,post,final):
        signed(proof);require(proof['status']=='passed' and proof['freeze_sha256']==sha(RUN/'MODEL_FREEZE_ACCOUNTING01.json'),'Bound qualification invalid')
        require(proof['source_binding_sha256']==digest(frozen['source_sha256'])
            and proof['test_binding_sha256']==digest(frozen['source_bound_test_gate']['test_files_sha256'])
            and proof['runtime_inputs_sha256']==digest(frozen['runtime_inputs']),'Qualification source/test/input identities differ')
        for row in proof['evidence_files']:bind(row)
    require((pre['actual_complete_batches'],pre['actual_complete_pairs'],pre['only_not_started'])==(147,405,'v20_normal_008'),'Pre405 scope differs')
    for proof in (post,final):
        require(proof['actual_complete_batches']==148 and proof['actual_complete_pairs']==406
            and proof['actual_started_attempts']==proof['actual_finished_attempts']==406
            and proof['failed_or_unknown_canonical_pairs']==0
            and proof['evidence_census_sha256']==digest(proof['evidence_files']),'Post406 exact denominator differs')
        require(proof['gate_sha256']==sha(ROOT/'publication/release_gate.py') and proof['physical_episodes_started']==0
            and proof['raw_files_opened']==proof['annual_values_parsed']==0,'Saved qualification scope differs')
    require(post['batch_proofs']==final['batch_proofs'] and post['evidence_files']==final['evidence_files'],'Scientific evidence changed across FINAL01 generation')
    finals={p['batch']:p for p in final['batch_proofs']}
    for p in pre['batch_proofs']:require({k:v for k,v in p.items() if k!='evidence_files'}==finals[p['batch']],'Prior405 evidence changed after008')
    registration=read(RUN/'design/CAMPAIGN_final01.json');jobs=registration['jobs']
    require(len(jobs)==len(finals)==148 and {j['name'] for j in jobs}==set(finals),'Canonical148 registration differs')
    expected={'main':240,'long':24,'normal':12,'compat':7,'fixed8':96,'propagation':18,'open1':6,'contact025':3}
    counts=Counter();actual_pairs=[];starts=finishes=0
    for job in jobs:
        directory=RUN/'experiments'/job['name'];plan=read(directory/'plan.json');manifest=read(directory/'manifest.json');terminal=read(directory/'BATCH_RESULT.json')
        scene=manifest['scenarios'][0];specs=plan['policy_specs'];end=scene['duration_steps']*scene.get('step_minutes',15.)
        require(plan['source_sha256']==manifest['source_sha256']==frozen['source_sha256']
            and manifest['files']==frozen['runtime_inputs'] and plan['model_config']==manifest['model_config'],'Registered physical binding differs')
        require(terminal['plan_sha256']==sha(directory/'plan.json') and terminal['manifest_sha256']==sha(directory/'manifest.json')
            and terminal['registered_pairs']==terminal['actual_attempts']==terminal['complete_full']==len(specs)
            and terminal['unknown_full']==terminal['analysis_failure_or_unavailable_count']==0
            and terminal['status_counts']=={'completed':len(specs)} and terminal['source_unchanged'],'Terminal denominator/status differs')
        attempts=lines(directory/'ATTEMPTS.jsonl');require(len(attempts)==2*len(specs),'Duplicate/partial attempt journal')
        require({(a['scenario_id'],a['policy_id'],a['event']) for a in attempts}=={(scene['id'],s['policy_id'],event) for s in specs for event in ('started','finished')},'Attempt identities differ')
        starts+=sum(a['event']=='started' for a in attempts);finishes+=sum(a['event']=='finished' for a in attempts)
        require(finals[job['name']]['terminal_sha256']==sha(directory/'BATCH_RESULT.json') and finals[job['name']]['qualified'],'Saved proof no longer binds actual terminal')
        for alias,spec in zip(job['policies'],specs):
            path=directory/'pairs'/(scene['id']+'__'+spec['policy_id'])/'RESULT.json';record=read(path)
            require(record['status']=='completed' and record['error'] is None and record['analysis_error'] is None
                and record['model_id']=='M20_load_accounting','Actual RESULT failed/unknown/wrong identity')
            analysis=record['analysis'];audit=analysis['v20']['episode_audit'];window=analysis['v18']['windows']['registered_full'];ledger=analysis['v20']['service_ledgers']['registered_full']
            require(audit['status']=='passed' and not audit['issues'] and analysis['observed_until_minute']==end,'Actual episode audit/coverage differs')
            for value in (window,ledger):require(value['complete'] is True and value['window_minutes']==[0.,end] and value['unknown_tail_minutes'] is None and value['all_registered'] is not None,'Complete full registered accounting missing')
            for priority in ('P0','P1','P2','P3'):
                for carrier in ('e','h','c'):
                    values=ledger['all_registered'][priority][carrier]
                    require(all(type(values[k]) in (int,float) and math.isfinite(values[k]) for k in ('requested_kWh','delivered_kWh','ENS_kWh','disconnected_ENS_kWh','connected_unserved_ENS_kWh')),'Service ledger contains unknown/nonfinite value')
            finish=next(a for a in attempts if a['event']=='finished' and a['policy_id']==spec['policy_id'])
            require(finish['result_sha256']==sha(path) and finish['raw_sha256']==record['raw_sha256'],'Finish does not bind actual RESULT/raw fingerprint')
            actual_pairs.append((job['name'],scene['id'],alias));counts[job['cohort']]+=1
    require(starts==finishes==len(set(actual_pairs))==406 and dict(counts)==expected,'All406 actual unique denominator differs')
    resource=read(S4/'v20_normal_008.result.json');samples=lines(S4/'SAMPLES.jsonl');admission=read(S4/'ADMISSION.json');admission_rows=lines(S4/'ADMISSION_SAMPLES.jsonl')
    require(child['resource_result_sha']==sha(S4/'v20_normal_008.result.json') and resource['passed'] and resource['exit_code']==0,'008 resource result not bound')
    require(len(samples)==resource['resource_samples']==430 and resource['unknown_process_samples']==0
        and all(s['process_sample_status']=='measured' and s['pid']==child['pid'] and s['batch']=='v20_normal_008' for s in samples),'Actual resource sampling denominator differs')
    for field,outfield in [('private_bytes','sampled_private_peak_bytes'),('working_set_bytes','sampled_working_set_peak_bytes'),('OS_lifetime_peak_working_set_bytes','observed_OS_lifetime_peak_working_set_bytes')]:
        require(max(s[field] for s in samples)==resource[outfield],'Resource sampled peak does not match saved samples')
    for field,outfield in [('available_physical_bytes','minimum_observed_available_physical_bytes'),('commit_headroom_bytes','minimum_observed_commit_headroom_bytes')]:
        require(min(s[field] for s in samples)==resource[outfield],'Host minimum does not match saved samples')
    require(resource['admission']==admission,'Resource result admission differs');bind(resource['admission_receipt']);bind(admission['admission_samples'])
    n=admission['observations'];last=admission_rows[-n:];gaps=[b['monotonic']-a['monotonic'] for a,b in zip(last,last[1:])]
    require(n==17 and n>=16 and last[-1]['monotonic']-last[0]['monotonic']==admission['stable_observed_seconds']>=30
        and max(gaps)==admission['maximum_observation_gap_seconds']<=3,'Actual complete resource admission time window differs')
    require(admission['growth_factor']==1.1 and admission['selected_reserve_gib']==admission['reserve_gib']==2
        and admission['host_reserve_bytes']==2*1024**3
        and admission['required_physical_bytes']==math.ceil(1.1*24352612352)+2*1024**3
        and admission['required_commit_headroom_bytes']==math.ceil(1.1*26497581056)+2*1024**3,'Registered empirical resource budget differs')
    for key,limit in [('available_physical_bytes','required_physical_bytes'),('commit_headroom_bytes','required_commit_headroom_bytes')]:
        require(min(s[key] for s in last)==admission['minimum_'+key] and all(s[key]>=admission[limit] for s in last)
            and admission['last_counter_recheck'][key]>=admission[limit],'Actual admission counters did not satisfy both limits')
    require(any(s['commit_headroom_bytes']<admission['required_commit_headroom_bytes']+1024**3 or s['available_physical_bytes']<admission['required_physical_bytes']+1024**3 for s in last),'Preferred3GiB reserve was available but ignored')
    childcommand=read(S4/'v20_normal_008.command.json');require(childcommand['command'][1:]==['-B','-m','tools.load_accounting.runner','run','--directory','outputs/v20_load_accounting_20261019_run01/experiments/v20_normal_008']
        and childcommand['new_registration_created'] is False,'Scientific command changed')
    clocks={name:read(S4/(name+'.command.json')) for name in ('preflight','postflight','final_qualification')}
    reportcommand=read(RUN/'logs/final_readback01.command.json')
    require(all(c['exit_code']==0 and c['error'] is None for c in clocks.values()),'Actual qualification command failed')
    require(utc(clocks['preflight']['finished_utc'])<utc(childcommand['utc'])<utc(clocks['postflight']['started_utc'])
        <utc(clocks['postflight']['finished_utc'])<=utc(reportcommand['started_utc'])
        <utc(reportcommand['finished_utc'])<=utc(clocks['final_qualification']['started_utc'])
        <utc(clocks['final_qualification']['finished_utc'])<=utc(summary['utc'])<=utc(outer['finished_utc']),'Actual handoff phase clocks are not sequential')
    report=RUN/'paper_results/FINAL01';reportreceipt=read(report/'RECEIPT.json');reportsum=read(report/'SUMMARY.json')
    for name,value in reportreceipt['output_files'].items():require(fp(report/name)['sha256']==value,'FINAL01 output receipt differs')
    require(reportcommand['exit_code']==0 and reportreceipt['script_sha256']==frozen['source_sha256']['scripts/v20_report.py']
        and reportreceipt['raw_opened'] is False and reportreceipt['annual_values_read'] is False,'FINAL01 source/scope/command differs')
    require(reportsum['actual_started_pairs']==reportsum['actual_finished_pairs']==reportsum['complete_registered_full_pairs']==reportsum['eligible_terminal_pairs']==406
        and reportsum['unknown_full_pairs']==reportsum['not_started_pairs']==reportsum['numeric_complete_but_disqualified_pairs']==0
        and reportsum['status_counts']=={'completed':406} and reportsum['issues']==[],'FINAL01 strict denominator differs')
    fp(report/'STATUS.csv')
    with (report/'STATUS.csv').open(encoding='utf-8-sig',newline='') as stream:status=list(csv.DictReader(stream))
    require(len(status)==406 and {(s['batch'],s['scenario_id'],s['alias']) for s in status}==set(actual_pairs),'FINAL01 status canonical identity set differs')
    flags=('registration_valid','result_bound','batch_terminal_bound','execution_qualified','comparison_eligible','actual_started','actual_finished','registered_full_complete')
    require(all(s['status']=='completed' and all(s[k]=='True' for k in flags) and s['start_event_count']==s['finish_event_count']=='1' and s['episode_audit_status']=='passed' for s in status),'FINAL01 includes a disqualified pair')
    collect=read(RUN/'review/normal_window_analysis03/ONESHOT_RESULT01.json');collect_command=read(RUN/'logs/normal_analysis_collect03.command.json')
    require(collect['status']=='passed' and collect['normal_qualified_trajectories']==12 and collect['no_physics'] is True
        and collect_command['exit_code']==0,'Actual normal03 collection did not complete')
    for key in ('collection_receipt','document_link_verification','final_document'):bind(collect[key])
    normalreceipt=read(RUN/'review/normal_window_analysis03/COLLECTION_RECEIPT.json');signed(normalreceipt)
    for row in normalreceipt['outputs']:bind({k:row[k] for k in ('path','bytes','sha256')})
    # Rehash only saved compact/input/source evidence. No trajectory is opened.
    for row in list(seen.values()):require((ROOT/row['path']).stat().st_size==row['bytes'] and sha(ROOT/row['path'])==row['sha256'],'Evidence drifted during final readback')
    result=dict(schema='hospital_ies.v20.final_campaign_readonly_closure.v1',status='passed',utc=datetime.now(timezone.utc).isoformat(),
        source_script_sha256=sha(Path(__file__)),scientific_root=str(ROOT),actual_complete_batches=148,actual_complete_pairs=406,
        unique_started_attempts=starts,unique_finished_attempts=finishes,cohort_pairs=dict(counts),eligible_terminal_pairs=406,unknown_registered_full_pairs=0,
        source_test_input_binding_counts=[366,241,28],all_current_hashes_match_frozen=True,source_before_after_and_prior405_unchanged=True,
        actual008=dict(unique_successful_Popen=1,pid=child['pid'],drained=True,exit_code=0,window_minutes=[0.,4320.],
            whole_process_elapsed_seconds=resource['elapsed_seconds'],samples=430,unknown_process_samples=0,
            sampled_private_peak_bytes=resource['sampled_private_peak_bytes'],OS_lifetime_peak_working_set_bytes=resource['observed_OS_lifetime_peak_working_set_bytes']),
        actual_admission=dict(observations=n,span_seconds=admission['stable_observed_seconds'],maximum_gap_seconds=max(gaps),
            growth_factor=1.1,reserve_gib=2,both_counters_passed=True,minimum_commit_headroom_bytes=admission['minimum_commit_headroom_bytes'],
            required_commit_headroom_bytes=admission['required_commit_headroom_bytes'],historical_profile_is_M19_observation_not_M20_upper_bound=True),
        preserved_predecessors=dict(normal03_exit=125,S3_status='failed_unknown',idle_run01_exit=15,resource_probe02_exit=3,normal009_resource_peak='unknown',old_measured10_new_measured1=True),
        normal_collection03_actual12_passed=True,raw_files_opened=0,annual_numeric_values_parsed=0,physical_episodes_started=0,heavy_tests_run=0,
        verified_unique_saved_files=len(seen),final_proof=fp(S4/'FINAL_406_PROOF.json'),S4_summary=fp(S4/'SUMMARY.json'),
        FINAL01_receipt=fp(report/'RECEIPT.json'),normal_collection03=fp(RUN/'review/normal_window_analysis03/ONESHOT_RESULT01.json'),
        no_new_scientific_criteria=True,scope='Saved source/registration/actual terminal/service-ledger/resource and report readback; not another simulation, post-push fresh clone, or clinical/physical certification',
        still_pending=['Final documentation integration and ordinary Git main commit/push','Actual fresh GitHub clone core463/full representative replay without original-root leakage','Final ZIP verification below200000000 bytes'])
    result['signature']=digest(result)
    with (HERE/'FINAL_CAMPAIGN_READONLY_CLOSURE.json').open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':run()
