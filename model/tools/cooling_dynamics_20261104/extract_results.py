"""Small finite engineering ledger audit and exact segment plot inputs.

No simulator, optimization, interpolation, unknown-tail filling, or historical
outcome reuse. This audit does not reconstruct native primal certificates.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

PRIORITIES=('P0','P1','P2','P3')
CARRIERS=('e','h','c')
LEDGER=('original_demand_kW','delivered_kW','active_cut_kW','connected_unserved_kW')
SCHEMA='hospital_ies.cooling_dynamics.finite_engineering_audit.v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite JSON: '+x)))


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def write(path,value):
    with Path(path).open('x',encoding='utf8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')


def _number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Missing/nonfinite actual quantity')
    return float(value)


def checked_segments(rows,expected_end):
    end=_number(expected_end);cursor=0.
    if not rows or end<=0:raise ValueError('Missing actual coverage')
    for row in rows:
        a,b=_number(row['start_minute']),_number(row['end_minute'])
        if abs(a-cursor)>1e-8 or not a<b or b>end+1e-8 or b-a>1.+1e-8:raise ValueError('Actual segment coverage gap/overlap/overrun or nonminute interval')
        ledger=row.get('service_request_ledger',{})
        for field in LEDGER:
            value=ledger.get(field)
            if not isinstance(value,list) or len(value)!=4 or any(not isinstance(cell,list) or len(cell)!=3 for cell in value):raise ValueError('Missing actual 4x3 service ledger')
        for g in range(4):
            for k in range(3):
                d,s,cut,u=[_number(ledger[field][g][k]) for field in LEDGER]
                if min(d,s,cut,u)<-1e-8 or s>d+1e-7 or abs(d-s-cut-u)>1e-7:raise ValueError('Original service ledger closure/bounds failed')
        cursor=b
    if abs(cursor-end)>1e-8:raise ValueError('Actual coverage has an unknown tail')
    return rows


def integrate(rows,start,end):
    out={name:[[0.]*3 for _ in range(4)] for name in ('demand_kWh','served_kWh','ENS_kWh','active_cut_kWh','connected_unserved_kWh')}
    for row in rows:
        dt=max(0.,min(end,row['end_minute'])-max(start,row['start_minute']))/60.
        if not dt:continue
        ledger=row['service_request_ledger']
        for g in range(4):
            for k in range(3):
                d,s,cut,u=[float(ledger[field][g][k]) for field in LEDGER]
                for name,value in zip(out,(d,s,max(0.,d-s),cut,u)):out[name][g][k]+=value*dt
    return out


def audit_segments(rows,expected_end):
    checked_segments(rows,expected_end)
    totals=integrate(rows,0.,expected_end)
    phases=[]
    for phase,a,b in [('pre_fault',0.,60.),('fault',60.,120.),('post_repair',120.,expected_end)]:
        b=min(b,expected_end)
        if a<b:phases.append(dict(phase=phase,start_minute=a,end_minute=b,**integrate(rows,a,b)))
    return dict(schema=SCHEMA,status='passed',expected_end_minute=expected_end,segment_count=len(rows),
        actual_coverage_complete=True,ledger_closure_tolerance_kW=1e-7,**totals,phases=phases,
        scope='Recorded actual service ledger and interval arithmetic only; no primal, OEM, hydraulic or clinical qualification')


def minute_rates(rows,expected_end):
    """Duration weighted physical rates, with no smoothing or missing support."""
    checked_segments(rows,expected_end)
    if expected_end!=int(expected_end):raise ValueError('Integer minute support required')
    result=[]
    for minute in range(int(expected_end)):
        values=integrate(rows,minute,minute+1.)
        record=dict(start_minute=float(minute),end_minute=float(minute+1))
        for k,c in enumerate(CARRIERS):
            for field in ('demand','served','ENS'):
                for g,p in enumerate(PRIORITIES):record[f'{p}_{c}_{field}_kW']=values[field+'_kWh'][g][k]*60.
                record[f'total_{c}_{field}_kW']=math.fsum(values[field+'_kWh'][g][k]*60. for g in range(4))
        result.append(record)
    return result


def load_rows(path):
    with Path(path).open(encoding='utf8') as stream:return [json.loads(line) for line in stream if line.strip()]


def extract(root):
    root=Path(root).resolve();registration=read(root/'REGISTRATION.json')
    source_ref=dict(path=str(root/'SOURCE_BINDING.json'),sha256=sha(root/'SOURCE_BINDING.json'))
    binding=read(source_ref['path']);producers={};initial={};selection=read(root/'SELECTION.json')
    independent_path=root/'audit01/CAMPAIGN_AUDIT.json';independent=read(independent_path)
    if independent.get('status')!='passed' or independent.get('source_binding')!=source_ref or independent.get('unique_main_producers')!=19:raise ValueError('Independent actual energy/stock audit must pass before plotting')
    for label,selected in selection['producers'].items():
        if independent.get('producer_results',{}).get(label,{}).get('selected_result_sha256')!=selected['result_sha256']:raise ValueError('Independent energy audit uses a different selected producer')
    out=root/'data';out.mkdir(exist_ok=False)
    for item in registration['producers']:
        p=Path(selection['producers'][item['label']]['producer']);result=read(p/'RESULT.json')
        if sha(p/'RESULT.json')!=selection['producers'][item['label']]['result_sha256']:raise ValueError('Selected producer result changed')
        if result.get('schema')!='hospital_ies.cooling_dynamics.producer.v1' or result.get('status')!='completed' or result.get('source_binding')!=source_ref:raise ValueError('Incomplete or mixed-source producer: '+item['label'])
        for pin in result.get('files',[]):
            actual=Path(pin['path'])
            if not actual.resolve().is_relative_to(p.resolve()) or actual.stat().st_size!=pin['bytes'] or sha(actual)!=pin['sha256']:raise ValueError('Actual producer evidence changed: '+str(actual))
        case=read(item['case_file'])
        if result.get('case_sha256')!=sha(item['case_file']):raise ValueError('Case identity changed')
        if item['variant']=='unattended' and (result.get('strict_noopt_check',{}).get('all_post60_segments_arithmetic') is not True or result['strict_noopt_check'].get('optimizer_entry_attempts')!=[]):raise ValueError('Unattended source is not an accepted strict arithmetic U')
        rows=load_rows(p/'SEGMENTS.jsonl');audit=audit_segments(rows,float(item['duration_minutes']))
        if audit!=read(p/'ENGINEERING_AUDIT.json'):raise ValueError('Recomputed actual ledger audit differs')
        compact=[dict(start_minute=r['start_minute'],end_minute=r['end_minute'],
            demand_kW=r['service_request_ledger']['original_demand_kW'],served_kW=r['service_request_ledger']['delivered_kW'],
            active_cut_kW=r['service_request_ledger']['active_cut_kW'],connected_unserved_kW=r['service_request_ledger']['connected_unserved_kW'],
            cooling_dynamics=r.get('cooling_dynamics'),strict_unattended_forward=r.get('strict_unattended_forward')) for r in rows]
        producers[item['label']]=dict(case_id=case['id'],variant=item['variant'],duration_minutes=item['duration_minutes'],
            segments=compact,engineering_audit=audit,source=dict(segments_path=str(p/'SEGMENTS.jsonl'),segments_sha256=sha(p/'SEGMENTS.jsonl'),
                result_path=str(p/'RESULT.json'),result_sha256=sha(p/'RESULT.json')))
        first=load_rows(p/'STATES.jsonl')[0];initial[item['label']]=first
        rates=minute_rates(rows,float(item['duration_minutes']))
        with (out/(item['label']+'_minute_rates.csv')).open('x',encoding='utf8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rates[0]));writer.writeheader();writer.writerows(rates)
    for topic in registration['topics']:
        labels=topic['producers'];normal=producers[labels['normal']]
        for label in labels.values():
            candidate=producers[label]
            # Compare exactly the actual exogenous minute demand in all views.
            def demand_minute(prod):
                rows=[dict(start_minute=s['start_minute'],end_minute=s['end_minute'],service_request_ledger={
                    'original_demand_kW':s['demand_kW'],'delivered_kW':s['demand_kW'],
                    'active_cut_kW':[[0.]*3 for _ in range(4)],'connected_unserved_kW':[[0.]*3 for _ in range(4)]}) for s in prod['segments']]
                return minute_rates(rows,float(prod['duration_minutes']))
            nr,cr=demand_minute(normal),demand_minute(candidate)
            if len(nr)!=len(cr) or any(abs(a[k]-b[k])>1e-7 for a,b in zip(nr,cr) for k in a):raise ValueError('Matched conditions use different actual demand: '+topic['id'])
        physical_initial=[initial[label] for label in labels.values()]
        if any(x!=physical_initial[0] for x in physical_initial[1:]):raise ValueError('Matched conditions use different initial actual state: '+topic['id'])
    if len(producers)!=19 or registration['topics'][7]['producers']!=registration['topics'][4]['producers']:raise ValueError('19 unique producers / T08=T05 contract changed')
    topics=[]
    for item in registration['topics']:
        conditions={}
        for variant,label in item['producers'].items():
            producer=producers[label]
            conditions['response' if variant=='responsive' else variant]=dict(label=label,producer=producer['source'],
                totals=producer['engineering_audit'],segments=[dict(start_minute=s['start_minute'],end_minute=s['end_minute'],
                    demand_kW=[math.fsum(row[k] for row in s['demand_kW']) for k in range(3)],
                    delivered_kW=[math.fsum(row[k] for row in s['served_kW']) for k in range(3)]) for s in producer['segments']])
        topics.append(dict(id=item['id'],title=item['title'],start_minute=0.,end_minute=float(item['duration_minutes']),
            fault_start_minute=60.,fault_end_minute=120.,conditions=conditions,
            data_reuse=dict(exact_T05_continuous_alias=item['id']=='T08',independent_fault_sample=item['id']!='T08')))
    value=dict(schema='hospital_ies.cooling_dynamics.plot_data.v1',status='accepted_for_finite_engineering_plot',source_binding=source_ref,
        registration=dict(path=str(root/'REGISTRATION.json'),sha256=sha(root/'REGISTRATION.json')),
        independent_energy_audit=dict(path=str(independent_path),sha256=sha(independent_path)),
        topics=topics,producers=producers,unique_main_producers=19,logical_condition_rows=24,
        normalization='100*(scenario_delivered-normal_delivered)/normal_delivered; zero normal delivered power is undefined. Absolute figures use actual delivered kW.',
        physical_qualification=False,native_primal_certificate_reconstructed=False)
    write(out/'PLOT_DATA.json',value);return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args()
    try:
        result=extract(args.root);print(json.dumps(dict(status=result['status'],producers=len(result['producers']))));return 0
    except Exception as exc:
        print(json.dumps(dict(status='failed',type=type(exc).__name__,message=str(exc))));return 1


if __name__=='__main__':raise SystemExit(main())
