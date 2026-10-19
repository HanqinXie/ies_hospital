"""Bind offline alarms to actual baseline queue/execution evidence, not rescue claims."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


def relation(time, interval):
    if time is None:return None
    if interval is None:return 'no_formal_failure_observed'
    return 'before_entire_first_interval' if time<interval[0]-1e-10 else 'within_first_interval' if time<interval[1]-1e-10 else 'at_or_after_first_classification'


def command_timing(payload, alert_time, interval):
    decisions=[d for d in payload['decisions'] if abs(d['decision_time_min']-alert_time)<1e-9]
    if len(decisions)>1:raise ValueError('Duplicate actual decision time')
    command=decisions[0].get('command_generated') if decisions else None
    result={'alarm_time_minute':alert_time,'first_failure_interval':interval,
        'alarm_timing_relation':relation(alert_time,interval),'actual_execution_relation':'request_not_observed'}
    if command is None:return result
    cid=command['id'];arrivals=[e for e in payload['events'] if e.get('kind')=='command_arrived' and e.get('command_id')==cid]
    executed=[e for e in payload['events'] if e.get('kind')=='command_executed' and e.get('command_id')==cid]
    if len(arrivals)>1 or len(executed)>1:raise ValueError('Duplicated actual command lifecycle')
    arrived=arrivals[0]['time_minute'] if arrivals else None
    actual=executed[0]['time_minute'] if executed else None
    requested=command['dispatch'].get('gt_electric_kW')
    actual_ports=executed[0]['executed_dispatch'].get('gt_electric_kW') if executed else None
    result.update(command_id=cid,command_generated_minute=command['generated_minute'],
        planned_arrival_minute=command['arrival_minute'],planned_arrival_relation=relation(command['arrival_minute'],interval),
        actual_arrival_minute=arrived,actual_execution_minute=actual,
        actual_execution_relation=relation(actual,interval) if actual is not None else 'arrived_but_not_executed' if arrived is not None else 'not_arrived_in_observed_prefix',
        requested_current_GT_kW=requested,executed_GT_kW=actual_ports,
        GT_arrival_revalidation_delta_kW=None if requested is None or actual_ports is None else [b-a for a,b in zip(requested,actual_ports)],
        evidence_semantics='Actual original baseline command at this decision, not an alarm-driven intervention. Earlier arrival alone does not establish rescue feasibility.')
    return result


def analyze(offline_report_path, out):
    from .dependency import read,sha,reference
    from .runner import verify_manifest
    from tools.p2b_experiment.runner import load_payload
    report_path=Path(offline_report_path).resolve();report=read(report_path);base=report_path.parent
    out=Path(out)
    if out.exists():raise FileExistsError('Fresh execution analysis required')
    parents={}; refs={}
    for cohort in report['cohorts']:
        # Cohort paths are preserved by the actual fitted-data binding, not
        # inferred from scenario names or remembered report counts.
        mp=ROOT/cohort['manifest']['path'];ap=ROOT/cohort['audit']['path']
        if sha(mp)!=cohort['manifest']['sha256'] or sha(ap)!=cohort['audit']['sha256']:
            raise ValueError('Fitted actual cohort bytes changed')
        manifest=read(mp);verify_manifest(manifest);audit=read(ap)
        for entry in audit['checkpoint_digests']:
            key=(entry['scenario_id'],entry['policy_id'])
            if key in refs:raise ValueError('Duplicated source identity across cohorts')
            refs[key]=(mp.parent,manifest['signature'],entry);parents[key]=manifest
    rows=[]
    for policy_report in report['risk']:
        selected=policy_report['selection']['selected']
        if not selected:continue
        trials=[t for t in policy_report['trials'] if t['config_id']==selected['config_id'] and t['seed']==4101 and t['status']=='completed']
        if len(trials)!=1:raise ValueError('Representative seed selection differs from preregistration')
        trial=trials[0]
        for role in ('pilot_validation','development_boundary'):
            metrics=trial['evaluation'][role]['metrics']
            metadata=read(base/trial['trial_directory']/(role+'_alarms.metadata.json'))
            csv_path=base/trial['trial_directory']/(role+'_alarms.csv')
            if sha(csv_path)!=metadata['csv_sha256']:raise ValueError('Actual alarm prediction bytes changed')
            if metadata['role']!=role or metadata['policy']!=policy_report['policy'] or metadata['input_binding_digest']!=report['binding_digest']:
                raise ValueError('Actual alarm role/policy/data binding differs')
            for alarm in metrics.get('positive_first_alerts',[]):
                key=(alarm['scenario_id'],policy_report['policy']);folder,signature,entry=refs[key]
                path=folder/entry['path']
                if sha(path)!=entry['checkpoint_sha256']:raise ValueError('Actual queue checkpoint bytes changed')
                payload=load_payload(path.parent.parent,key[0],key[1],signature)
                timing=command_timing(payload,alarm['decision_time_min'],alarm['first_failure_interval'])
                rows.append(dict(scenario_id=key[0],policy=key[1],role=role,model=selected['config_id'],seed=4101,
                    root_family_id=alarm['root_family_id'],known_abnormal_at_alarm=alarm['known_abnormal_at_alert'],
                    checkpoint_sha256=entry['checkpoint_sha256'],alarm_metadata=reference(base/trial['trial_directory']/(role+'_alarms.metadata.json')),**timing))
    result=dict(offline_report=reference(report_path),cases=len(rows),actual_execution_relation_counts=dict(Counter(r['actual_execution_relation'] for r in rows)),
        planned_arrival_relation_counts=dict(Counter(r.get('planned_arrival_relation') for r in rows)),
        source_sha256=sha(__file__),rows=rows,
        scope='Post-hoc original-baseline queue evidence for selected offline alarms; no new detection capability, fresh delay formula, physical ability or causal rescue claim.',
        no_fresh_full_delay_added=True,learning_control_outcomes_reported_separately=True)
    out.mkdir(parents=True);(out/'ALARM_EXECUTION_REPORT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False));return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('offline_report');p.add_argument('out');a=p.parse_args();analyze(a.offline_report,a.out)
