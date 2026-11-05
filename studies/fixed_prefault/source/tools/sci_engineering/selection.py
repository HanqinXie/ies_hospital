"""Preregistered conservative F/A selection, development data only.

No confirmation RESULT is opened. No fit, weighted carrier sum or policy
optimization is performed. A selected representative need not be a winner.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()


def _sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def _read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2,ensure_ascii=False,allow_nan=False)


def default_rule():
    tiers=[dict(name='critical_reliability',metrics=[f'{p}_{c}_ENS_kWh' for p in ('P0','P1') for c in ('e','h','c')]
        +[f'{p}_union_material_minutes' for p in ('P0','P1')]),
        dict(name='general_service',metrics=[f'{p}_{c}_ENS_kWh' for p in ('P2','P3') for c in ('e','h','c')]
        +[f'{p}_union_material_minutes' for p in ('P2','P3')]),
        dict(name='same_window_cost',metrics=['energy_cost_USD']),
        dict(name='conditional_CO2',metrics=['total_CO2_kg_conditional'])]
    return dict(schema='hospital_ies.sci_development_selection.rule.v21.v1',
        allowed_phase='development',required_scene_role='development',configuration_id='H0',
        candidates=dict(F=dict(parameter='fixed_reserve_hours',values=[.5,1.,2.],default=1.),
            A=dict(parameter='release_after_minutes',values=[120.,240.],default=120.)),
        tiers=tiers,comparison_numerical_zero=1e-8,
        criterion='Per-scenario, per-metric Pareto over all registered matched scenarios; smaller is better',
        tier_transition='Only when every survivor is equivalent at the current and previous tiers',
        tradeoff_rule='Use default if undominated; else minimize absolute distance from default, then numerical parameter value',
        unknown_rule='Refuse finalization for failed, pending, missing, unmatched or nonfinite candidate inputs',
        independent_unit='Source block; variants and minutes are not new independent observations',
        no_weighted_carrier_sum=True,no_confirmation_results_read=True,no_universal_winner_required=True)


def required_metrics():return [metric for tier in default_rule()['tiers'] for metric in tier['metrics']]


def _candidate_specs(specifications,rule):
    candidates={};by_alias={}
    for alias,definition in rule['candidates'].items():
        group=[copy.deepcopy(p) for p in specifications if p.get('display_alias')==alias and p.get('selection_role')=='development_only']
        parameter=definition['parameter'];values=[];base=[]
        for policy in group:
            if policy.get('kind')!='sci_engineering':raise ValueError('Candidate kind changed')
            config=copy.deepcopy(policy.get('config') or {})
            if parameter not in config:raise ValueError('Missing candidate selection parameter')
            value=float(config.pop(parameter));values.append(value);base.append(_digest(config))
            if policy['policy_id'] in candidates:raise ValueError('Duplicate candidate policy identity')
            candidates[policy['policy_id']]=policy
        if sorted(values)!=definition['values']:raise ValueError('Candidate grid is not the preregistered '+alias+' grid')
        if len(set(base))!=1:raise ValueError('Candidate nonselection configuration changed')
        by_alias[alias]=group
    return candidates,by_alias


def select_rows(rows,specifications,rule):
    if rule!=default_rule():raise ValueError('Selection rule differs from preregistered conservative rule')
    specs,by_alias=_candidate_specs(specifications,rule)
    rows=[r for r in rows if r['policy_id'] in specs]
    if not rows:raise ValueError('No actual development candidate rows')
    if any(r.get('phase')!='development' for r in rows):raise ValueError('Only development outcomes are authorized for selection')
    if {r.get('configuration_id') for r in rows}!={rule['configuration_id']}:
        raise ValueError('Selection configuration must remain H0')
    for field in ('model_id','source_binding_sha256'):
        if len({r.get(field) for r in rows})!=1:raise ValueError('Selection mixed source/model identities')
    cases={};metrics=required_metrics()
    for row in rows:
        if row.get('eligible') is not True:raise ValueError('Failed, pending or incomplete candidate; cannot finalize')
        if any(row.get(m) is None or isinstance(row.get(m),bool) or not isinstance(row.get(m),(int,float))
            or not math.isfinite(row[m]) for m in metrics):raise ValueError('Unknown or nonfinite candidate metric; cannot finalize')
        key=(row['scenario_id'],row['configuration_id']);group=cases.setdefault(key,{})
        if row['policy_id'] in group:raise ValueError('Duplicate paired candidate row')
        if group:
            reference=next(iter(group.values()))
            for field in ('source_block_id','scenario_sha256','configuration_sha256','window_minutes'):
                if reference.get(field)!=row.get(field):raise ValueError('Paired candidate changed '+field)
        group[row['policy_id']]=row
    for group in cases.values():
        if set(group)!=set(specs):raise ValueError('Missing paired registered candidate')
    eps=rule['comparison_numerical_zero'];ordered_cases=sorted(cases)
    selected={}
    for alias,policies in by_alias.items():
        definition=rule['candidates'][alias];parameter=definition['parameter']
        survivors=[p['policy_id'] for p in policies];trace=[];decided=False
        for tier in rule['tiers']:
            vectors={pid:[float(cases[case][pid][metric]) for case in ordered_cases for metric in tier['metrics']] for pid in survivors}
            def dominates(left,right):
                return all(x<=y+eps for x,y in zip(vectors[left],vectors[right])) and any(x<y-eps for x,y in zip(vectors[left],vectors[right]))
            survivors=[pid for pid in survivors if not any(dominates(other,pid) for other in survivors if other!=pid)]
            equal=all(all(abs(x-y)<=eps for x,y in zip(vectors[survivors[0]],vectors[pid])) for pid in survivors[1:])
            trace.append(dict(tier=tier['name'],remaining_policy_ids=survivors.copy(),survivors_equivalent=equal))
            if len(survivors)==1:
                chosen=survivors[0];status=('unique_reliability_weak_dominator' if tier['name']=='critical_reliability' else 'unique_secondary_weak_dominator')
                decided=True;break
            if not equal:
                chosen=min(survivors,key=lambda pid:(abs(specs[pid]['config'][parameter]-definition['default']),specs[pid]['config'][parameter]))
                status='representative_of_tradeoff_frontier';decided=True;break
        if not decided:
            chosen=min(survivors,key=lambda pid:(abs(specs[pid]['config'][parameter]-definition['default']),specs[pid]['config'][parameter]))
            status='predeclared_representative_of_equal_candidates'
        selected[alias]=dict(selected_policy_id=chosen,selected_parameter_name=parameter,
            selected_parameter_value=specs[chosen]['config'][parameter],selected_policy_spec=copy.deepcopy(specs[chosen]),
            selected_policy_spec_sha256=_digest(specs[chosen]),decision_status=status,decision_tier=trace[-1]['tier'],
            remaining_candidate_values=[specs[p]['config'][parameter] for p in survivors],tier_trace=trace,
            unified_winner_claimed=status=='unique_reliability_weak_dominator',matched_scenarios=len(cases),
            matched_source_blocks=len({r['source_block_id'] for r in rows}),
            evidence_vector_sha256=_digest({pid:{str(k):{m:cases[k][pid][m] for m in metrics} for k in ordered_cases} for pid in specs}))
    return selected


def collect_development(campaigns):
    """Gate every PLAN before opening any RESULT; confirmation inputs are refused."""
    plans=[];all_specs={}
    for directory in map(Path,campaigns):
        plan=_read(directory/'PLAN.json')
        if plan.get('phase')!='development':raise ValueError('Only development PLAN is permitted; confirmation is sealed')
        scenes=plan.get('panel',{}).get('scenarios',[])
        if not scenes or any(s.get('role')!='development' for s in scenes):
            raise ValueError('Every scene must have development role; confirmation is sealed')
        plans.append((directory,plan))
        for policy in plan['policy_specs']:
            if policy['policy_id'] in all_specs and _digest(all_specs[policy['policy_id']])!=_digest(policy):raise ValueError('Candidate identity changed between development registrations')
            all_specs[policy['policy_id']]=policy
    path=Path(__file__).with_name('analysis.py');spec=importlib.util.spec_from_file_location('sci_selection_analysis',path)
    analysis=importlib.util.module_from_spec(spec);spec.loader.exec_module(analysis)
    records=analysis.collect_results([directory for directory,plan in plans])
    registrations={str(directory.resolve()).casefold():(directory,plan) for directory,plan in plans}
    terminals={}
    for directory,plan in plans:
        journal=directory/'ATTEMPTS.jsonl'
        if not journal.is_file():continue
        for line in journal.read_text(encoding='utf-8-sig').splitlines():
            item=json.loads(line)
            if item.get('event')=='finished':
                key=str(Path(item['result_path']).resolve()).casefold()
                terminals.setdefault(key,[]).append(item)
    for record in records:
        directory,plan=registrations[record['_campaign'].casefold()]
        manifest=_read(directory/'MANIFEST.json')
        expected=dict(model_id=plan['model_id'],phase='development',source_block_id=record['_scenario']['source_block_id'],
            source_binding_sha256=manifest['simulator_hash'],
            scenario_sha256=_digest(record['_scenario']),policy_sha256=_digest(record['_policy']),
            configuration_sha256=_digest(plan['configurations'][record['configuration_id']]))
        for field,value in expected.items():
            if record.get(field)!=value:raise ValueError('Development registered evidence differs: '+field)
        if record.get('status')=='completed' and record.get('source_unchanged') is not True:
            raise ValueError('Completed development candidate lacks unchanged source evidence')
        path=record.get('_result_path')
        if path:
            finished=terminals.get(str(Path(path).resolve()).casefold(),[])
            if len(finished)!=1 or finished[0].get('result_sha256')!=record['_result_sha256']:
                raise ValueError('Development terminal journal or RESULT SHA drift')
    rows=analysis.flatten_records(records)
    candidate_ids={p['policy_id'] for p in all_specs.values() if p.get('selection_role')=='development_only' and p.get('display_alias') in ('F','A')}
    for row in rows:
        if row['policy_id'] not in candidate_ids:continue
        if row.get('evidence_status')=='evidence_drift':raise ValueError('Development evidence drift: '+str(row.get('evidence_error')))
        if not row['eligible']:continue
        path=row.get('raw_path')
        if not path or not Path(path).is_file() or not row.get('raw_sha256') or _sha(path)!=row['raw_sha256']:
            raise ValueError('Completed development raw evidence SHA drift or missing raw')
        row['raw_sha256_verified_for_selection']=True
        if row.get('solver_sidecar_required') and not row.get('solver_sidecar_verified'):
            raise ValueError('Required development solver sidecar evidence is incomplete')
        row['solver_sidecar_verified_for_selection']=bool(row.get('solver_sidecar_verified'))
    return rows,list(all_specs.values()),records


def register_rule(output):
    value=dict(registered_utc=datetime.now(timezone.utc).isoformat(),rule=default_rule(),
        selector_source_sha256=_sha(__file__),registration_scope='Before reading development or confirmation outcomes')
    value['rule_sha256']=_digest(value['rule']);value['registration_sha256']=_digest(value)
    _write_new(output,value);return value


def finalize(campaigns,rule_path,output):
    registration=_read(rule_path);rule=registration['rule']
    copy_of_registration=copy.deepcopy(registration);copy_of_registration.pop('registration_sha256',None)
    if _digest(copy_of_registration)!=registration['registration_sha256'] or _digest(rule)!=registration['rule_sha256']:
        raise ValueError('Selection preregistration digest differs')
    if registration['selector_source_sha256']!=_sha(__file__):raise ValueError('Selector source changed after preregistration')
    rows,specifications,records=collect_development(campaigns)
    selected=select_rows(rows,specifications,rule)
    inventory=[]
    for campaign in map(Path,campaigns):
        for name in ('PLAN.json','MANIFEST.json','ATTEMPTS.jsonl'):
            path=campaign/name
            if path.is_file():inventory.append(dict(path=str(path.resolve()),sha256=_sha(path)))
    for record in records:
        if record.get('_result_path'):inventory.append(dict(path=record['_result_path'],sha256=record['_result_sha256'],
            raw_sha256=record.get('raw_sha256'),solver_row_evidence=record.get('solver_row_evidence'),
            status=record.get('status'),attempt_id=record.get('attempt_id')))
    candidate_ids={p['policy_id'] for p in specifications if p.get('selection_role')=='development_only' and p.get('display_alias') in ('F','A')}
    value=dict(schema='hospital_ies.sci_development_selection.closed.v21.v1',selection_closed=True,
        closed_utc=datetime.now(timezone.utc).isoformat(),rule_registration_path=str(Path(rule_path).resolve()),
        rule_registration_sha256=_sha(rule_path),rule_sha256=registration['rule_sha256'],
        selector_source_sha256=_sha(__file__),analysis_source_sha256=_sha(Path(__file__).with_name('analysis.py')),
        selected=selected,input_inventory=inventory,confirmation_results_read=0,
        candidate_omitted_due_to_failure_or_unknown=False,statistical_independence_not_increased=True,
        all_latest_candidate_raw_sha256_verified=True,
        latest_candidate_evidence=[{k:r.get(k) for k in ('scenario_id','source_block_id','policy_id','configuration_id',
            'window_minutes','source_binding_sha256','scenario_sha256','policy_sha256','configuration_sha256',
            'result_path','result_sha256','raw_path','raw_sha256','raw_sha256_verified_for_selection',
            'solver_rows_path','solver_rows_gzip_sha256','solver_rows_gzip_bytes','solver_rows_entry_count_observed',
            'solver_rows_count_observed','solver_sidecar_required','solver_sidecar_verified_for_selection')} for r in rows
            if r['policy_id'] in candidate_ids],
        prior_failed_attempts_retained=sum(r.get('status')!='completed' for r in records))
    value['selection_signature']=_digest(value)
    output=Path(output)
    if output.exists():raise FileExistsError('Use a fresh immutable selection directory')
    output.mkdir(parents=True)
    _write_new(output/'SELECTION.json',value)
    formal_specs=[]
    for alias in ('F','A'):
        policy=copy.deepcopy(selected[alias]['selected_policy_spec'])
        policy.update(policy_id=alias+'_sci_v21',selection_role='selected_after_development',
            selected_candidate_policy_id=selected[alias]['selected_policy_id'],selection_signature=value['selection_signature'])
        formal_specs.append(policy)
    _write_new(output/'SELECTED_FA_POLICIES.json',formal_specs)
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    registered=sub.add_parser('register');registered.add_argument('--output',type=Path,required=True)
    closed=sub.add_parser('finalize');closed.add_argument('--rule',type=Path,required=True)
    closed.add_argument('--campaign',type=Path,action='append',required=True);closed.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    value=(register_rule(args.output) if args.command=='register' else finalize(args.campaign,args.rule,args.output))
    print(json.dumps(value,ensure_ascii=True));return 0


if __name__=='__main__':raise SystemExit(main())
