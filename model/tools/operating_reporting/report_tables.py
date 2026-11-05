"""Saved v15 engineering tables only; no raw access, solve or physical episode.

Read producer analysis JSON plus plan/manifest metadata, retain every registered
pair, and keep exact named window bounds/source/configuration separate. Explicit
analysis overlays never alter the original physical status. Costs remain a
separate descriptive account and never offset critical service failures.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from tools.engineering_reporting import aggregate as mature

SCHEMA='hospital_ies.operating_report_tables.v15.v1'
REFERENCE='endurance_restore_p0_ready_v14r6'
POLICIES=(REFERENCE,'readiness_handoff_K1_v15','readiness_handoff_K2_v15')
GROUP=('stage','phase','runtime_source_signature','model_id','model_config_signature',
       'analysis_view','policy_id','policy_spec_signature','analysis_class')
WINDOW=('window','window_start_minute','window_end_minute')
ENS=tuple(f'P{p}_{c}_ENS_kWh' for p in range(4) for c in 'ehc')
FIELDS={
    'registered_n':'Every registered scenario/policy pair, including missing/pending saved analysis; repeated attempts retained.',
    'complete_n':'Producer marks this exact [start,end) window complete; independent of observed P0 failure.',
    'observed_P0_failure_n':'Observed prefix has original P0 failure; incomplete failures still count.',
    'unknown_without_observed_P0_failure_n':'P0 outcome remains unknown and no failure has yet been observed; never a pass.',
    'observed_Px_carrier_ENS_lower_bound_kWh':'Sum of saved nonnegative observed ENS; not a full total when tail is unknown. Missing prefix is null, with known_n.',
    'complete_Px_carrier_ENS_kWh_sum/n':'Only complete windows with a supplied target; explicit n, not the full-denominator total.',
    'longest_material_minutes':'Maximum saved continuous material interruption within this named window; observed prefix only, not clinical threshold.',
    'temperature':'Observed P0-P2 maxima from saved original_19 prefix metadata; complete maxima separate. Uncalibrated thermal proxy. P3 disturbed temperature always null.',
    'new_observed_P0_failure':'Reference E window is complete and clear; candidate has an observed failure, even if its tail is unknown.',
    'avoided_observed_P0_failure':'E has observed failure and candidate exact window is complete and clear. Unknown candidate cannot count as avoided.',
    'pairing':'Same stage/phase/runtime/model/config/analysis view/scenario/window name and exact bounds. Repeated cells remain ambiguous; no latest/winner selection.',
    'operation_scope':'Diesel/shaft/fuel/account summaries are trajectory_observed_prefix only. They are never assigned pro rata to primary/full/event windows.',
    'speed':'all_states extrema include OFF/STARTING zero. positive_Pe_advance extrema filter saved advance receipts with actual_terminal_kW>0; not all connected intervals or proof of frequency safety. Rotor domain and fuel exhaustion flags are separate.',
    'fees':'Original 19th fee, original diesel electric fee and shaft-equivalent fee are separate. No fee/ENS combined score or critical-service compensation.',
    'stage':'exact-normal requires phase targeted_exact_v14_normal or known_case_role starting exact_v14_normal; this explicit identity precedes known. Confirmation/frozen remains frozen; other known cases remain known; ordinary N2 is normal; otherwise development. Original phase remains separate.',
    'missing_analysis':'No zero service/temperature/fuel is invented; original physical status is retained separately from availability of saved analysis.',
}


class SavedReader:
    def __init__(self):self.files={};self.warnings=[]
    def read(self,path):
        path=Path(path)
        if path.suffix!='.json' or path.name.startswith('trajectory'):
            raise ValueError('Only saved analysis/metadata JSON is permitted; no raw access')
        raw=path.read_bytes();self.files[str(path.resolve())]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        return json.loads(raw)


def _stage(scene,phase):
    phase=phase.casefold()
    role=str(scene.get('known_case_role') or '')
    if phase=='targeted_exact_v14_normal' or role.startswith('exact_v14_normal'):return 'exact-normal'
    if scene.get('experiment_role')=='confirmation' or 'frozen' in phase or 'confirmation' in phase:return 'frozen'
    if role or 'known' in phase:return 'known'
    if scene.get('analysis_class')=='N2':return 'normal'
    return 'development'


def _definitions(scene):
    end=float(scene.get('duration_steps',0))*float(scene.get('step_minutes',15.))
    if end<=0:raise ValueError('Positive registered duration required')
    values={'primary':[60.,420.],'full':[0.,600.],'registered_full':[0.,end]}
    if end>=1440.:values.update(long1440=[0.,1440.],extension_600_1440=[600.,1440.])
    first=scene.get('first_event_minute')
    if first is None:
        starts=[]
        if scene.get('faults'):starts.append(float(scene.get('fault_start_minute',0.)))
        if scene.get('shock') or scene.get('shock_by_priority'):starts.append(float(scene.get('shock_start_minute',0.)))
        first=min(starts) if starts else None
    if first is not None:
        first=float(first);values.update(event_first_600=[first,first+600.],event_to_registered_end=[first,end])
        if first>0:values['normal_prefix']=[0.,first]
        for kind,active in (('fault',scene.get('faults')),('shock',scene.get('shock') or scene.get('shock_by_priority'))):
            if active:
                a=float(scene.get(kind+'_start_minute',0.));b=a+float(scene.get(kind+'_duration_minutes',end-a))
                if b>a:values[kind+'_active']=[a,b]
                if b<end:values[kind+'_recovery_to_end']=[b,end]
    return {name:bounds for name,bounds in values.items() if bounds[1]>bounds[0]}


def _records(path,reader,expected,model):
    result={}
    if not path.exists():return result
    for row in reader.read(path).get('records',[]):
        key=row['scenario_id'],row['policy_id']
        if key not in expected or key in result or row.get('model_id',model)!=model:
            raise ValueError('Saved analysis differs from registered pair/model')
        result[key]=row
    return result


def _window(context,name,bounds,window,analysis):
    targets=mature._targets(window);prefix=(window or {}).get('observed_prefix') or {}
    original=(window or {}).get('original_19') or {};complete=bool(window and window.get('complete') is True)
    observed_failure_minutes=prefix.get('P0_failure_minutes',original.get('observed_prefix_failure_minutes'))
    failed=any(mature._finite(v) and float(v)>0. for v in (observed_failure_minutes,targets.get('P0_failure')))
    clear=complete and mature._finite(targets.get('P0_failure')) and float(targets['P0_failure'])==0.
    row=dict(context,window=name,window_start_minute=float(bounds[0]),window_end_minute=float(bounds[1]),
        complete=complete,missing_analysis=window is None,observed_minutes=(window or {}).get('observed_minutes'),
        observed_P0_failure=failed,complete_P0_clear=clear,P0_outcome_unknown=not failed and not clear,
        observed_P0_failure_minutes=observed_failure_minutes,P3_disturbed_temperature_C=None,P3_temperature_violation=None)
    row.update(targets)
    engineering=mature._engineering(window,analysis or {})
    row.update({'observed_'+k:v for k,v in engineering.items()})
    for p in range(3):
        temperatures=original.get('observed_prefix_max_temperature_C')
        row[f'observed_P{p}_max_temperature_C']=(temperatures[p] if temperatures is not None else targets.get(f'P{p}_max_temperature_C'))
    row['observed_secondary_GT_trips']=original.get('observed_prefix_secondary_GT_trips',targets.get('secondary_GT_trips'))
    recovery=prefix.get('service_recovery') or {}
    row.update({'observed_recovery_'+k:v for k,v in recovery.items()})
    for p in range(4):
        for c in 'ehc':row[f'observed_P{p}_{c}_ENS_lower_bound_kWh']=engineering.get(f'P{p}_{c}_total_ENS_kWh')
    return row


def _operation(context,analysis,requested):
    analysis=analysis or {};diesel=analysis.get('diesel_local_response') or {}
    prefix=diesel.get('observed_prefix') or {};windows=analysis.get('windows') or {}
    registered=windows.get('registered_full') or {};original=mature._targets(registered) if registered else {}
    row=dict(context,accounting_scope='trajectory_observed_prefix',requested_end_minute=requested,
        observed_until_minute=analysis.get('observed_until_minute'),registered_full_complete=registered.get('complete') is True,
        missing_analysis=not bool(analysis),P3_disturbed_temperature_C=None,
        original_registered_energy_fee_USD=original.get('actual_energy_fee_USD'))
    row.update({k:v for k,v in prefix.items() if k not in ('speed_min_pu','speed_max_pu')})
    row.update(all_states_speed_min_pu=prefix.get('speed_min_pu'),all_states_speed_max_pu=prefix.get('speed_max_pu'))
    segments=(analysis.get('timelines') or {}).get('diesel_local_response')
    loaded=[s for s in segments or [] if s.get('operation')=='advance' and mature._finite(s.get('actual_terminal_kW')) and s['actual_terminal_kW']>0.]
    for suffix,combine in (('min',min),('max',max)):
        values=[s['speed_'+suffix+'_pu'] for s in loaded if mature._finite(s.get('speed_'+suffix+'_pu'))]
        row['positive_Pe_advance_speed_'+suffix+'_pu']=combine(values) if values else None
    row['positive_Pe_advance_segment_n']=len(loaded) if segments is not None else None
    row['fuel_budget_exhausted_segment_n']=sum(s.get('fuel_budget_domain_exhausted') is True for s in segments) if segments is not None else None
    row['rotor_domain_exceeded_segment_n']=sum(s.get('domain_exceeded') is True for s in segments) if segments is not None else None
    for device,state in ((analysis.get('engineering') or {}).get('gt') or {}).items():
        row.update({device+'_'+k:v for k,v in state.items() if isinstance(v,(int,float,bool))})
    fee=row['original_registered_energy_fee_USD'];wm=prefix.get('mechanical_work_kWh');we=prefix.get('electrical_work_kWh')
    row['shaft_adjusted_registered_fee_USD']=(fee+.30*(wm-we) if row['registered_full_complete'] and all(mature._finite(v) for v in (fee,wm,we)) else None)
    return row


def _stat_into(row,label,values,maximum=False):
    stat=mature._stat(values,maximum=maximum)
    row[label]=stat['known_max'] if maximum else stat['known_sum'];row[label+'_n']=stat['known_count']


def _groups(rows):
    groups=defaultdict(list)
    for row in rows:groups[tuple(row.get(k) for k in (*GROUP,*WINDOW))].append(row)
    result=[]
    for key,values in groups.items():
        row=dict(zip((*GROUP,*WINDOW),key));complete=[v for v in values if v['complete']]
        row.update(registered_n=len(values),complete_n=len(complete),incomplete_n=len(values)-len(complete),
            missing_analysis_n=sum(v['missing_analysis'] for v in values),observed_P0_failure_n=sum(v['observed_P0_failure'] for v in values),
            unknown_without_observed_P0_failure_n=sum(v['P0_outcome_unknown'] for v in values),
            complete_P0_clear_n=sum(v['complete_P0_clear'] for v in values),P3_disturbed_temperature_C=None,P3_temperature_violation=None,
            batches=sorted({v['batch'] for v in values}),status_counts=dict(Counter(v['physical_status'] for v in values)))
        for metric in ENS:
            stem=metric.removesuffix('_ENS_kWh');observed='observed_'+stem+'_ENS_lower_bound_kWh'
            _stat_into(row,observed,[v.get(observed) for v in values])
            _stat_into(row,'complete_'+metric+'_sum',[v.get(metric) for v in complete])
            row['complete_'+metric+'_n']=row.pop('complete_'+metric+'_sum_n')
            for duration in ('material_minutes','equivalent_loss_minutes','longest_material_minutes','trailing_material_minutes'):
                field='observed_'+stem+'_'+duration
                _stat_into(row,field,[v.get(field) for v in values],maximum=duration.startswith(('longest','trailing')))
        for p in range(3):
            _stat_into(row,f'observed_P{p}_max_temperature_C',[v.get(f'observed_P{p}_max_temperature_C') for v in values],True)
            _stat_into(row,f'complete_P{p}_max_temperature_C',[v.get(f'P{p}_max_temperature_C') for v in complete],True)
        for field in sorted({k for v in values for k in v if k.startswith('observed_GT_') or k.startswith('observed_recovery_') or k=='observed_secondary_GT_trips'}):
            if 'recovery' not in field:_stat_into(row,field,[v.get(field) for v in values])
        _stat_into(row,'complete_original_energy_fee_USD_sum',[v.get('actual_energy_fee_USD') for v in complete])
        result.append(row)
    return result


def _pairs(rows,reference):
    fields=tuple(k for k in GROUP if k not in ('policy_id','policy_spec_signature'))+('scenario_id',*WINDOW)
    cells=defaultdict(lambda:defaultdict(list))
    for row in rows:cells[tuple(row.get(k) for k in fields)][row['policy_id']].append(row)
    result=[]
    for key,policies in cells.items():
        refs=policies.get(reference,[])
        for pid,candidates in policies.items():
            if pid==reference:continue
            row=dict(zip(fields,key));row.update(reference_policy_id=reference,candidate_policy_id=pid,
                reference_n=len(refs),candidate_n=len(candidates),reference_batches=[v['batch'] for v in refs],
                candidate_batches=[v['batch'] for v in candidates],comparison_outcome='undetermined',both_complete=False)
            if not refs:row['comparison_outcome']='missing_reference'
            elif len(refs)!=1 or len(candidates)!=1:row['comparison_outcome']='ambiguous_repeated_cells'
            else:
                ref,candidate=refs[0],candidates[0]
                row.update(reference_complete=ref['complete'],candidate_complete=candidate['complete'],
                    reference_observed_P0_failure=ref['observed_P0_failure'],candidate_observed_P0_failure=candidate['observed_P0_failure'],
                    reference_P0_outcome_unknown=ref['P0_outcome_unknown'],candidate_P0_outcome_unknown=candidate['P0_outcome_unknown'],
                    both_complete=ref['complete'] and candidate['complete'])
                if ref['complete_P0_clear'] and candidate['observed_P0_failure']:outcome='new_observed_P0_failure'
                elif ref['observed_P0_failure'] and candidate['complete_P0_clear']:outcome='avoided_observed_P0_failure'
                elif ref['observed_P0_failure'] and candidate['observed_P0_failure']:outcome='both_observed_P0_failure'
                elif ref['complete_P0_clear'] and candidate['complete_P0_clear']:outcome='both_complete_P0_clear'
                else:outcome='undetermined'
                row['comparison_outcome']=outcome
                for metric in ENS:
                    a,b=candidate.get(metric),ref.get(metric)
                    row['complete_delta_'+metric]=a-b if row['both_complete'] and mature._finite(a) and mature._finite(b) else None
            result.append(row)
    return result


def _pair_groups(rows):
    fields=tuple(k for k in GROUP if k not in ('policy_id','policy_spec_signature'))+('candidate_policy_id',*WINDOW)
    grouped=defaultdict(list)
    for row in rows:grouped[tuple(row.get(k) for k in fields)].append(row)
    result=[]
    for key,values in grouped.items():
        row=dict(zip(fields,key));row.update(candidate_scene_window_n=len(values),both_complete_n=sum(v['both_complete'] for v in values))
        row.update({outcome+'_n':sum(v['comparison_outcome']==outcome for v in values) for outcome in
            ('new_observed_P0_failure','avoided_observed_P0_failure','both_observed_P0_failure','both_complete_P0_clear','undetermined','missing_reference','ambiguous_repeated_cells')})
        for metric in ENS:_stat_into(row,'complete_delta_'+metric+'_sum',[v.get('complete_delta_'+metric) for v in values])
        result.append(row)
    return result


def _operation_groups(rows):
    fields=(*GROUP,'requested_end_minute','accounting_scope');grouped=defaultdict(list)
    for row in rows:grouped[tuple(row.get(k) for k in fields)].append(row)
    result=[]
    accounts=('electrical_work_kWh','mechanical_work_kWh','loss_work_kWh','rotor_delta_kWh',
        'fuel_budget_debit_kWh_equivalent','original_electric_fee_USD','shaft_equivalent_fee_USD',
        'original_registered_energy_fee_USD','shaft_adjusted_registered_fee_USD')
    for key,values in grouped.items():
        row=dict(zip(fields,key));row.update(registered_n=len(values),
            registered_full_complete_n=sum(v['registered_full_complete'] for v in values),
            missing_analysis_n=sum(v['missing_analysis'] for v in values),P3_disturbed_temperature_C=None)
        for name in accounts:
            _stat_into(row,'observed_'+name+'_sum',[v.get(name) for v in values])
        for name in sorted({k for v in values for k in v if k.startswith('GT_')}):
            _stat_into(row,'observed_'+name+'_sum',[v.get(name) for v in values])
        for name in ('positive_Pe_advance_segment_n','fuel_budget_exhausted_segment_n','rotor_domain_exceeded_segment_n'):
            _stat_into(row,'observed_'+name+'_sum',[v.get(name) for v in values])
        for name in ('all_states_speed_min_pu','all_states_speed_max_pu','positive_Pe_advance_speed_min_pu','positive_Pe_advance_speed_max_pu'):
            known=[v[name] for v in values if mature._finite(v.get(name))]
            row[name]=(min(known) if '_min_' in name else max(known)) if known else None
            row[name+'_n']=len(known)
        result.append(row)
    return result


def collect(run,*,batches=None,analysis_roots=(),policies=POLICIES,reference=REFERENCE):
    run=Path(run).resolve();reader=SavedReader();rows=[];operations=[];registered=0;batch_count=0
    plans=sorted((run/'experiments').glob('*/plan.json'))
    if batches:plans=[p for p in plans if p.parent.name in set(batches)]
    if not plans:raise ValueError('No selected registered plans; no result manufactured')
    for plan_path in plans:
        folder=plan_path.parent;plan=reader.read(plan_path);manifest=reader.read(folder/'manifest.json');batch_count+=1
        scenes={s['id']:s for s in manifest['scenarios']};specs={s['policy_id']:s for s in plan['policy_specs']}
        expected={(sid,pid) for sid in plan['scenario_ids'] for pid in specs}
        if len(expected)!=plan['registered_pairs'] or len(specs)!=len(plan['policy_specs']):raise ValueError('Invalid registered denominator')
        actual=_records(folder/'analysis/RESULTS.json',reader,expected,plan['model_id'])
        overlays=[]
        for root in map(Path,analysis_roots):
            path=root/folder.name/'analysis/RESULTS.json'
            overlays.append((root.name,_records(path,reader,expected,plan['model_id'])))
        for sid,pid in sorted(expected):
            if policies is not None and pid not in policies:continue
            registered+=1;record=actual.get((sid,pid));saved=folder/'pairs'/(sid+'__'+pid)/'RESULT.json'
            if record is None and saved.exists():record=reader.read(saved)
            if record and (record['scenario_id'],record['policy_id'])!=(sid,pid):raise ValueError('Saved pair identity differs')
            analysis=(record or {}).get('analysis');view='registered_original'
            for name,overlay in overlays:
                if (sid,pid) in overlay:analysis=overlay[sid,pid].get('analysis');view='explicit_overlay:'+name
            scene=scenes[sid];definitions=_definitions(scene);requested=definitions['registered_full'][1]
            context=dict(batch=folder.name,stage=_stage(scene,plan['phase']),phase=plan['phase'],model_id=plan['model_id'],
                model_config_signature=mature._digest(plan.get('model_config',{})),runtime_source_signature=mature._digest(plan.get('source_sha256',{})),
                policy_id=pid,policy_spec_signature=mature._digest(specs[pid]),analysis_class=scene.get('analysis_class'),scenario_id=sid,
                source_day_index=scene.get('source_day_index'),physical_scenario_signature=scene.get('physical_scenario_signature'),
                analysis_view=view,physical_status=(record or {}).get('status','missing_saved_outcome'))
            windows=(analysis or {}).get('windows') or {}
            definitions.update({name:w['window_minutes'] for name,w in windows.items()})
            for name,bounds in definitions.items():rows.append(_window(context,name,bounds,windows.get(name),analysis))
            operations.append(_operation(context,analysis,requested))
    pairs=_pairs(rows,reference)
    return dict(schema=SCHEMA,created_utc=datetime.now(timezone.utc).isoformat(),run_directory=str(run),
        registered_pairs=registered,registered_batches=batch_count,policy_filter=list(policies) if policies else None,reference_policy=reference,
        explicit_analysis_roots=[str(Path(p).resolve()) for p in analysis_roots],
        tables=dict(WINDOW_TRAJECTORIES=rows,WINDOW_GROUPS=_groups(rows),E_PAIRED_WINDOWS=pairs,
            E_PAIRED_GROUPS=_pair_groups(pairs),OPERATION_TRAJECTORIES=operations,
            OPERATION_GROUPS=_operation_groups(operations)),field_dictionary=FIELDS,
        input_bindings=reader.files,read_warnings=reader.warnings,raw_files_opened=0,physical_episodes_started=0,
        final12_gate='not_verified; unopened',fees_do_not_offset_critical_service=True,
        statistic='Descriptive same-hospital-year designed coordinates; no annual failure probability or independent-incident claim')


def export(run,out,**kwargs):
    out=Path(out).resolve()
    if out.drive.lower()=='c:':raise ValueError('Use D/E project output')
    if out.exists():raise FileExistsError('Fresh report-table directory required')
    report=collect(run,**kwargs);out.mkdir(parents=True)
    for name,rows in report['tables'].items():mature._csv(out/(name+'.csv'),rows)
    with (out/'REPORT_TABLES.json').open('x',encoding='utf8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False)
    receipt=dict(schema=SCHEMA+'.receipt',registered_pairs=report['registered_pairs'],registered_batches=report['registered_batches'],
        output_directory=str(out),raw_files_opened=0,physical_episodes_started=0,
        table_row_counts={k:len(v) for k,v in report['tables'].items()},
        output_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()})
    with (out/'REPORT_TABLES_RECEIPT.json').open('x',encoding='utf8') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--batch',action='append')
    parser.add_argument('--analysis-root',type=Path,action='append',default=[])
    parser.add_argument('--policy',action='append',help='Exact policy ID; default E/K1/K2')
    parser.add_argument('--reference-policy',default=REFERENCE);args=parser.parse_args(argv)
    receipt=export(args.run,args.out,batches=args.batch,analysis_roots=args.analysis_root,
        policies=tuple(args.policy) if args.policy else POLICIES,reference=args.reference_policy)
    print(json.dumps(receipt,ensure_ascii=True));return 0


if __name__=='__main__':raise SystemExit(main())
