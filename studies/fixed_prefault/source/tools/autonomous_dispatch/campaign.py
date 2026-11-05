"""Merge audited saved summaries, without reading loads or running a solver.

Each experiment group must contain disjoint scenario IDs and the same policies.
Repeated development/regression/confirmation groups remain separate.
"""
from __future__ import annotations
import argparse
from collections import defaultdict, Counter
import hashlib
import json
from pathlib import Path
from types import FunctionType
from tools.state_dispatch import analysis as original

ROOT=Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contrasts(rows, policy_ids):
    references=('priority_milp','secure_milp','execution_reserve')
    if not set(references)<=set(policy_ids):raise ValueError('All three frozen comparison policies required')
    namespace=dict(original._deltas.__globals__, REFERENCES=references)
    function=FunctionType(original._deltas.__code__, namespace)
    records=function(rows,policy_ids)
    # Descriptive only: the original research reporting resolutions, not new
    # physical constraints or a replacement for the original failure classifier.
    tolerance=[0.,1e-6]+[.01]*12+[.1]*3+[0.,1e-6]
    targets=original.previous.TARGET_NAMES
    for record in records:
        for window in ('primary','full'):
            ds=[record.get(window+'_'+name+('_known_delta' if name=='P0_failure' else '_delta')) for name in targets]
            record[window+'_all_19_nonworse']=all(d<=t for d,t in zip(ds,tolerance)) if all(d is not None for d in ds) else None
    return records


def summarize_contrasts(records):
    groups=defaultdict(list)
    for record in records:
        groups[tuple(record[key] for key in ('experiment_group','analysis_cohort','analysis_class',
                                             'candidate_policy_id','reference_policy_id'))].append(record)
    answer=[]
    for key,group in sorted(groups.items()):
        item=dict(zip(('experiment_group','analysis_cohort','analysis_class',
                       'candidate_policy_id','reference_policy_id'),key),registered_pairs=len(group))
        for window in ('primary','full'):
            binary=[r[window+'_P0_failure_known_delta'] for r in group]
            item[window+'_new_P0_cases']=sum(x==1 for x in binary)
            item[window+'_avoided_P0_cases']=sum(x==-1 for x in binary)
            item[window+'_unknown_binary_pairs']=sum(x is None for x in binary)
            complete=[r for r in group if r[window+'_complete_pair']]
            item[window+'_complete_pairs']=len(complete)
            flags=[r[window+'_all_19_nonworse'] for r in group]
            item[window+'_all_19_nonworse_cases']=sum(x is True for x in flags)
            item[window+'_all_19_unknown_cases']=sum(x is None for x in flags)
            for target in original.previous.TARGET_NAMES[1:]:
                name=window+'_'+target+'_delta'
                values=[r[name] for r in complete]
                item[name+'_observed_worst_complete_pair']=max(values,default=None)
                if 'temperature' in target:
                    item[name+'_worst_all_registered']=max(values,default=None) if len(complete)==len(group) else None
                else:
                    item[name+'_complete_pair_total']=sum(values)
                    item[name+'_all_registered']=sum(values) if len(complete)==len(group) else None
            ranked=sorted(complete,key=lambda r:(r[window+'_P0_failure_minutes_delta'],
                           r[window+'_P0_e_ENS_kWh_delta'],r[window+'_P0_h_ENS_kWh_delta']),reverse=True)
            item[window+'_worst_complete_case_ids']=[r['scenario_id'] for r in ranked[:5]]
        answer.append(item)
    return answer


def aggregate(config_path, out_dir):
    config=read(config_path); rows=[]; deltas=[]; evidence=[]; expected=0; seen=set()
    by_group=defaultdict(list); specifications={}; contexts={}
    for batch in config['batches']:
        folder=ROOT/batch['directory']; group=batch['group']
        batch_name=batch.get('name',folder.name)
        plan_path=folder/'registered_plan.json'; audit_path=folder/'AUDIT.json'
        summary_path=folder/'analysis/SUMMARY.json'
        plan,audit,summary=map(read,(plan_path,audit_path,summary_path))
        manifest=read(plan['manifest_path'])
        count=plan['registered_pair_count']; expected+=count
        if (audit.get('passed') is not True or audit.get('terminal_pair_count')!=count
                or audit.get('plan_sha256')!=sha(plan_path)
                or plan.get('manifest_sha256')!=sha(plan['manifest_path'])
                or audit.get('manifest_sha256')!=plan['manifest_sha256']
                or summary.get('plan_sha256')!=sha(plan_path)
                or summary.get('audit_sha256')!=sha(audit_path)
                or summary['registered_trajectory_count']!=count):
            raise ValueError('Incomplete or changed audited batch: '+folder.name)
        policy_ids=[s['policy_id'] for s in plan['policy_specs']]
        if group in specifications and specifications[group]!=plan['policy_specs']:
            raise ValueError('Policy identity/config differs inside experiment group')
        specifications[group]=plan['policy_specs']
        context=dict(execution_model_id=manifest.get('execution_model_id','M0'),
                     execution_contract=manifest.get('execution_contract'),score_windows=summary['score_windows'],
                     runtime_sources=plan['source_sha256'],planning_interface=manifest.get('planning_interface_id'),
                     standby_contract_id=manifest.get('standby_contract_id'),
                     numerical_contract_id=manifest.get('numerical_contract_id','inherited_scaled_rows'))
        if group in contexts and contexts[group]!=context:raise ValueError('Model/contract/window differs in experiment group')
        contexts[group]=context
        registered={(sid,pid) for sid in plan['scenario_ids'] for pid in policy_ids}
        actual={(r['scenario_id'],r['policy_id']) for r in summary['trajectory_results']}
        if len(summary['trajectory_results'])!=count or actual!=registered:
            raise ValueError('Saved summary denominator mismatch')
        for r in summary['trajectory_results']:
            key=(group,r['scenario_id'],r['policy_id'])
            if key in seen:raise ValueError('Repeated scene/policy in one experiment group')
            seen.add(key); copied=dict(r,experiment_group=group,batch_name=batch_name,
                execution_model_id=context['execution_model_id'],numerical_contract_id=context['numerical_contract_id'],
                standby_contract_id=context['standby_contract_id'])
            rows.append(copied);by_group[group].append(copied)
        evidence.append(dict(name=batch_name,group=group,registered=count,plan_sha256=sha(plan_path),
            audit_sha256=sha(audit_path),summary_sha256=sha(summary_path),
            phase=plan['phase'],execution_model_id=context['execution_model_id'],
            standby_contract_id=context['standby_contract_id'],
            numerical_contract_id=context['numerical_contract_id']))
    for group, members in by_group.items():
        policy_ids=[s['policy_id'] for s in specifications[group]]
        for d in contrasts(members,policy_ids):deltas.append(dict(d,experiment_group=group))
    summaries=original._group_summary(rows,('experiment_group','analysis_cohort','analysis_class','policy_id'))
    contrast_summary=summarize_contrasts(deltas)
    report=dict(schema='autonomous_dispatch.audited_campaign.v1',registered_trajectories=expected,
        aggregation_source_sha256=sha(__file__),configuration_sha256=sha(config_path),
        rows=len(rows),unique_scenario_ids=len({r['scenario_id'] for r in rows}),
        background_root_count=len({r['root_family_id'] for r in rows}),
        complete_full_trajectories=sum(r['full_full_horizon'] for r in rows),
        unknown_full_trajectories=sum(not r['full_full_horizon'] for r in rows),
        status_counts=dict(Counter(r['status'] for r in rows)),
        physical_episodes_started=0,reads_extra_load_content=False,
        pooling_rule='Repeated experiment groups stay separate; design frequencies are not probabilities',
        all_19_reporting_tolerance=[0.,1e-6]+[.01]*12+[.1]*3+[0.,1e-6],
        worst_case_ranking='Descending P0 failure minutes delta, electric ENS delta, heat ENS delta; not an all-objective order',
        evidence=evidence,policy_specs_by_group=specifications,group_summary=summaries,
        contrast_summary=contrast_summary,trajectory_results=rows,policy_deltas=deltas)
    if len(rows)!=expected:raise ValueError('Campaign denominator mismatch')
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=False)
    (out/'SUMMARY.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    for name,table in [('ABSOLUTE_CLASS_POLICY',summaries),('MATCHED_CLASS_POLICY',contrast_summary),
                       ('ALL_TRAJECTORIES',rows),('ALL_MATCHED_DELTAS',deltas)]:
        original._write_table(out/(name+'.csv'),table)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=aggregate(args.config,args.output)
    print(json.dumps({k:result[k] for k in ('registered_trajectories','unique_scenario_ids',
        'complete_full_trajectories','unknown_full_trajectories')}))


if __name__=='__main__':main()
