"""Descriptive engineering event outcomes from already audited case tables.

No plots, controller changes, pooling of windows or new accident probabilities.
"""
from pathlib import Path
from collections import defaultdict
from statistics import mean
import argparse,csv,hashlib,json,math

FIELDS=('P0_e_ENS_kWh','P0_h_ENS_kWh','P0_c_ENS_kWh',
    'P1_e_ENS_kWh','P1_h_ENS_kWh','P1_c_ENS_kWh',
    'critical_union_minutes','critical_initial_handoff_gap_minutes',
    'critical_post_handoff_gap_minutes','critical_recovery_gap_minutes',
    'P2_union_material_minutes','P3_union_material_minutes','energy_cost_USD',
    'total_CO2_kg_conditional')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def mechanism(row):
    if row['canonical_class']=='L3':return 'DEMAND_ONLY'
    if row['canonical_class']=='X_GRID_ALL_GT0':return 'COMPOUND_GRID_ALL_GT'
    value=row['fault_target']
    if value.startswith('AUX_FAN'):return 'AIR_DISTRIBUTION'
    if value=='AUX_CHW_PUMP':return 'CHW_DISTRIBUTION'
    if value=='AUX_HEAT_PUMP':return 'HEAT_DISTRIBUTION'
    if value=='TOWER':return 'SHARED_REJECTION'
    if value.startswith(('GT','HRSG')):return 'COGENERATION'
    if value.startswith('EC'):return 'ELECTRIC_CHILLER'
    return value or 'NO_EXTERNAL_FAULT'

def case_outcome(row):
    result_path=Path(row['result_path'])
    if sha(result_path)!=row['result_sha256']:raise ValueError('RESULT SHA drift')
    result=json.loads(result_path.read_text(encoding='utf8'))
    for name in ('scenario_id','policy_id','configuration_id','source_block_id','source_binding_sha256'):
        if result[name]!=row[name]:raise ValueError('Case identity differs: '+name)
    if row['eligible'] not in (True,'True','true'):raise ValueError('Ineligible input remains unknown; not summarized')
    metrics=result.get('metrics')
    if not isinstance(metrics,dict):raise ValueError('Missing actual event counter and metrics')
    events=metrics.get('event_counts')
    if not isinstance(events,dict) or any(type(v) is not int or v<0 for v in events.values()):
        raise ValueError('Missing/invalid actual event counter')
    for name in ('strategy_fallback_steps','projection_fallback_steps','projection_changed_steps'):
        if type(metrics.get(name)) is not int or metrics[name]<0:raise ValueError('Missing/invalid actual counter: '+name)
    output={key:row[key] for key in ('phase','window_minutes','scenario_id','canonical_class',
        'season_quarter','source_block_id','policy_id','configuration_id','fault_target')}
    output.update(failure_path=mechanism(row),protection_trip_events=events.get('protection_trip',0),
        no_export_interlocks=events.get('no_export_interlock',0),
        minimum_output_interlocks=events.get('minimum_output_interlock',0),
        strategy_fallback_steps=metrics['strategy_fallback_steps'],
        projection_fallback_steps=metrics['projection_fallback_steps'],
        projection_changed_steps=metrics['projection_changed_steps'],
        result_path=str(result_path),result_sha256=row['result_sha256'])
    for key in FIELDS:
        value=None if row[key] in ('',None) else float(row[key])
        if value is not None and not math.isfinite(value):raise ValueError('Nonfinite scientific value: '+key)
        output[key]=value
    return output,events

def summarize(cases):
    groups=defaultdict(list)
    strata=('phase','window_minutes','canonical_class','season_quarter','policy_id','configuration_id','failure_path')
    for row in cases:
        key=tuple(row[k] for k in strata)
        groups[key].append(row)
    output=[]
    for key,rows in sorted(groups.items()):
        item=dict(zip(strata,key))
        item.update(scenarios=len(rows),source_backgrounds=len({r['source_block_id'] for r in rows}),
            cases_with_protection_trip=sum(r['protection_trip_events']>0 for r in rows),
            actual_protection_trip_events=sum(r['protection_trip_events'] for r in rows),
            descriptive_only=True,accident_probability_not_estimated=True)
        for field in FIELDS:
            blocks=defaultdict(list)
            for row in rows:
                if row[field] is not None:blocks[row['source_block_id']].append(row[field])
            item[field+'_equal_background_mean']=mean(mean(v) for v in blocks.values()) if blocks else None
            item[field+'_known_cases']=sum(map(len,blocks.values()))
        output.append(item)
    return output

def write_csv(path,rows):
    fields=sorted({k for row in rows for k in row})
    with path.open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)

def execute(case_csv,output,analysis_receipt):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    case_csv=Path(case_csv);analysis_receipt=Path(analysis_receipt)
    native_receipt_sha=sha(analysis_receipt)
    native=json.loads(analysis_receipt.read_text(encoding='utf8'))
    bindings=[r for r in native['export_receipt']['files'] if Path(r['path']).name==case_csv.name]
    if len(bindings)!=1 or bindings[0]['sha256']!=sha(case_csv):raise ValueError('CASE CSV differs from native analysis export binding')
    rows=list(csv.DictReader(Path(case_csv).open(encoding='utf-8-sig',newline='')))
    if not rows:raise ValueError('No actual case evidence')
    sources={r['source_binding_sha256'] for r in rows}
    if len(sources)!=1 or '' in sources:raise ValueError('Mixed or missing source identity')
    identities=[tuple(r[k] for k in ('phase','scenario_id','policy_id','configuration_id')) for r in rows]
    if len(set(identities))!=len(identities):raise ValueError('Duplicate scientific case identity')
    cases=[];events=[]
    for row in rows:
        outcome,counters=case_outcome(row);cases.append(outcome)
        for kind,count in sorted(counters.items()):
            events.append({**{k:outcome[k] for k in ('phase','scenario_id','policy_id','configuration_id','source_block_id')},
                'kind':kind,'actual_count':count})
    if sha(case_csv)!=bindings[0]['sha256']:raise ValueError('CASE CSV drift during analysis')
    if sha(analysis_receipt)!=native_receipt_sha:raise ValueError('Native analysis receipt drift during analysis')
    for name,values in [('CASE_PROCESS_OUTCOMES',cases),('ACTUAL_EVENT_COUNTS',events),
                        ('FAILURE_PATH_DESCRIPTIVE_SUMMARY',summarize(cases))]:
        write_csv(output/(name+'.csv'),values)
    receipt=dict(input_path=str(Path(case_csv).resolve()),input_sha256=sha(case_csv),cases=len(cases),
        native_analysis_receipt_path=str(analysis_receipt.resolve()),native_analysis_receipt_sha256=native_receipt_sha,
        actual_event_rows=len(events),source_bindings=sorted({r['source_binding_sha256'] for r in rows}),
        model_changes=False,missing_case_not_imputed=True,
        failure_path_assignment='Preregistered fault path; L3 demand-only. Actual protection trip is counted separately.',
        statistical_scope='Descriptive equal-background means, separate class/season/phase/window/configuration. Original paired source-block bootstrap remains in audited analysis tables',
        outputs={p.name:sha(p) for p in output.glob('*.csv')})
    (output/'RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--analysis-receipt',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(execute(args.cases,args.output,args.analysis_receipt)))
