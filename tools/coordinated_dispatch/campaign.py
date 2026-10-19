"""Reuse audited campaign accounting with the actually registered references."""
from __future__ import annotations
from collections import defaultdict
import json
from pathlib import Path
from types import FunctionType
from tools.autonomous_dispatch import campaign as inherited
from tools.state_dispatch import analysis as original
from .analysis import references_for

def contrasts(rows, policy_ids, references=None):
    refs = tuple(references or [p for p in policy_ids if p in
        ('execution_reserve','joint_standby_1p25_h2','priority_milp','secure_milp')
        or p.startswith('fixed') and 'no_budget' not in p and 'no_handoff' not in p])
    if not refs or not set(refs) <= set(policy_ids):
        raise ValueError('Matched references must really exist in the panel')
    # Preserve the inherited reporting tolerances, with a local delta function.
    delta = FunctionType(original._deltas.__code__, dict(original._deltas.__globals__, REFERENCES=refs))
    # The inherited three-reference guard is replaced here, not bypassed by aliases.
    records = delta(rows, policy_ids)
    tolerance = [0.,1e-6]+[.01]*12+[.1]*3+[0.,1e-6]
    for record in records:
        for window in ('primary','full'):
            ds = [record.get(window+'_'+name+('_known_delta' if name=='P0_failure' else '_delta'))
                  for name in original.previous.TARGET_NAMES]
            record[window+'_all_19_nonworse'] = (all(d <= t for d,t in zip(ds,tolerance))
                if all(d is not None for d in ds) else None)
    return records

def _extra_group(items):
    answer = []
    groups = defaultdict(list)
    for item in items:
        groups[tuple(item[k] for k in ('experiment_group','analysis_class','policy_id'))].append(item)
    for key, group in sorted(groups.items()):
        row = dict(zip(('experiment_group','analysis_class','policy_id'),key),registered=len(group))
        for window in ('primary','full'):
            complete = [x[window]['all_registered'] for x in group if x[window]['complete']]
            prefixes = [x[window]['observed_prefix'] for x in group]
            row[window+'_complete'] = len(complete)
            row[window+'_unknown'] = len(group)-len(complete)
            for p in ('P2','P3'):
                for carrier in 'ehc':
                    for metric in ('ENS_kWh','total_minutes','longest_minutes'):
                        name = f'{window}_{p}_{carrier}_{metric}'
                        vals = [x['service'][p][carrier][metric] for x in prefixes]
                        row[name+'_observed_prefix_'+('max' if metric=='longest_minutes' else 'sum')] = (
                            max(vals,default=None) if metric=='longest_minutes' else sum(vals))
                        row[name+'_all_registered'] = (max(vals,default=None) if metric=='longest_minutes'
                            else sum(vals)) if len(complete)==len(group) else None
            for gap in ('zero_electric_supply','P0_material_unserved','P0_guarantee_failure'):
                vals = [x['gaps'][gap]['total_minutes'] for x in prefixes]
                row[f'{window}_{gap}_observed_prefix_minutes'] = sum(vals)
                row[f'{window}_{gap}_all_registered_minutes'] = sum(vals) if len(complete)==len(group) else None
            for p in ('P0','P1','P2','P3'):
                observed = [x['temperature'][p] for x in prefixes if x['temperature'][p] is not None]
                row[f'{window}_{p}_temperature_max_C'] = max((x['maximum_C'] for x in observed),default=None)
                row[f'{window}_{p}_temperature_violation_observed_minutes'] = sum(x['violation_minutes'] for x in observed) if observed else None
            recovery = [x['recovery'] for x in prefixes]
            row[window+'_confirmed_30min_recovery_cases'] = sum(x['confirmed_30min'] is True for x in recovery)
            row[window+'_recurrences_after_30min_observed'] = sum(x['recurrence_after_30min_count'] for x in recovery)
            row[window+'_terminal_observed_P0_failure_cases'] = sum(x['terminal_P0_failure'] is True for x in prefixes)
        answer.append(row)
    return answer

def aggregate(config_path, out_dir):
    config = inherited.read(config_path)
    panels = {}
    extras = []
    for batch in config['batches']:
        folder = inherited.ROOT / batch['directory']
        plan = inherited.read(folder/'registered_plan.json')
        specs = plan['policy_specs']
        panels[tuple(s['policy_id'] for s in specs)] = references_for(specs)
        extra_path = folder/'analysis/SERVICE_SWITCH_SUMMARY.json'
        binding = inherited.read(folder/'analysis/COORDINATED_SOURCE_BINDING.json')
        if binding['extra_report_sha256'] != inherited.sha(extra_path):
            raise ValueError('Changed saved extra metrics')
        extra = inherited.read(extra_path)
        if extra['registered_trajectory_count'] != plan['registered_pair_count']:
            raise ValueError('Extra metric denominator differs from the registered panel')
        extras.extend(dict(x,experiment_group=batch['group']) for x in extra['trajectories'])
    namespace = dict(inherited.aggregate.__globals__, __file__=__file__)
    namespace['contrasts'] = lambda rows,pids: contrasts(rows,pids,panels[tuple(pids)])
    function = FunctionType(inherited.aggregate.__code__,namespace)
    report = function(config_path,out_dir)
    if len(extras) != report['registered_trajectories']:
        raise ValueError('Campaign extra metrics lost registered outcomes')
    value = dict(schema='coordinated_dispatch.saved_extra_campaign.v1',
        registered_trajectories=len(extras),physical_episodes_started=0,
        repeated_experiment_groups_kept_separate=True,
        clinical_recovery_threshold=False,planned_budget_is_actual_ENS_guarantee=False,
        group_summary=_extra_group(extras),trajectory_results=extras)
    destination = Path(out_dir)
    with (destination/'SERVICE_SWITCH_CAMPAIGN.json').open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
    original._write_table(destination/'SERVICE_SWITCH_CLASS_POLICY.csv',value['group_summary'])
    return report

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    report=aggregate(args.config,args.output)
    print(json.dumps({k:report[k] for k in ('registered_trajectories','complete_full_trajectories','unknown_full_trajectories')}))

if __name__=='__main__':main()
