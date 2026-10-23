"""V21 source audit, preregistration, freeze and serial evidence-producing runs."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.sci_engineering import design,runner


def pilot_proposal(start_hour=4001,*,duration_minutes=360.,warmup_minutes=60.,
                   fault_duration_minutes=120.,seed=20261023):
    support=design.source_support(dict(start_hour=start_hour,duration_steps=int(duration_minutes/15.),step_minutes=15.))
    block=dict(source_block_id='m21_seen_pilot_'+design.digest([start_hour,duration_minutes,seed])[:16],
        role='development',start_hour=start_hour,source_day_index=(start_hour-1)//24,
        season_quarter=1+next(q for q in range(4) if (start_hour-1)//24<design.QUARTER_DAYS[q+1]),
        episode_minutes=duration_minutes,**support,globally_historical_unseen=False,
        historical_exposure_status='known_seen_pilot_not_independent_confirmation',
        selection_uses_outcomes=False)
    return design.scenario_panel([block],variants_per_block=1,seed=seed,name='resource_and_engineering_pilot',
        warmup_minutes=warmup_minutes,recovery_minutes=duration_minutes-warmup_minutes-fault_duration_minutes,
        duration_levels=(fault_duration_minutes,),fault_targets=('GRID',),delay_levels=(15.,),amplitude_levels=(.5,))


def _policy_subset(pool,names):
    names=[name.strip() for name in names.split(',') if name.strip()]
    result=[spec for spec in pool if spec['policy_id'] in names or spec.get('display_alias') in names]
    unmatched=set(names)-{name for spec in result for name in (spec['policy_id'],spec.get('display_alias'))}
    if unmatched:raise ValueError('Unknown registered policy aliases: '+','.join(sorted(unmatched)))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    normalized=sub.add_parser('normalize-panel',help='Fix exporter identities before the final grant')
    normalized.add_argument('--panel',type=Path,required=True);normalized.add_argument('--output',type=Path,required=True)
    grant=sub.add_parser('grant-final',help='Issue the once-only final-blind grant after development closes')
    grant.add_argument('--freeze',type=Path,required=True);grant.add_argument('--selection-record',type=Path,required=True)
    grant.add_argument('--panel',type=Path,required=True);grant.add_argument('--exposure-registry',type=Path,required=True)
    scan=sub.add_parser('scan')
    scan.add_argument('--historical-root',type=Path,required=True);scan.add_argument('--output',type=Path,required=True)
    planned=sub.add_parser('design')
    planned.add_argument('--registry',type=Path,required=True);planned.add_argument('--output',type=Path,required=True)
    planned.add_argument('--confirmation-blocks',type=int,default=24);planned.add_argument('--development-blocks',type=int,default=8)
    planned.add_argument('--confirmation-variants',type=int,default=4);planned.add_argument('--development-variants',type=int,default=2)
    planned.add_argument('--episode-minutes',type=float,default=720.);planned.add_argument('--seed',type=int,default=20261023)
    planned.add_argument('--historical-mode',choices=('strict','current_round_isolated'),default='strict')
    pilot=sub.add_parser('pilot-proposal')
    pilot.add_argument('--output',type=Path,required=True);pilot.add_argument('--start-hour',type=int,default=4001)
    pilot.add_argument('--duration-minutes',type=float,default=360.);pilot.add_argument('--warmup-minutes',type=float,default=60.)
    pilot.add_argument('--fault-duration-minutes',type=float,default=120.);pilot.add_argument('--seed',type=int,default=20261023)
    frozen=sub.add_parser('freeze')
    frozen.add_argument('--output',type=Path,required=True);frozen.add_argument('--policies',default='R,M,F,C,A')
    frozen.add_argument('--policy-specs',type=Path);frozen.add_argument('--configurations',type=Path)
    frozen.add_argument('--attempt-limit',type=int,default=5000);frozen.add_argument('--wall-hours',type=float,default=96.)
    frozen.add_argument('--selection-record',type=Path)
    registered=sub.add_parser('register')
    registered.add_argument('--panel',type=Path,required=True);registered.add_argument('--freeze',type=Path,required=True)
    registered.add_argument('--directory',type=Path,required=True);registered.add_argument('--raw-root',type=Path,required=True)
    registered.add_argument('--policies',default='R,M,F,C,A');registered.add_argument('--configurations',default='H0')
    registered.add_argument('--phase',default='pilot');registered.add_argument('--role',choices=('development','confirmation'))
    registered.add_argument('--pilot-proof',type=Path)
    registered.add_argument('--final-release-grant',type=Path)
    run=sub.add_parser('run')
    run.add_argument('--directory',type=Path,required=True);run.add_argument('--retry-failed',action='store_true')
    run.add_argument('--max-pairs',type=int)
    args=parser.parse_args(argv)
    if args.command=='normalize-panel':
        value=runner.normalize_panel(runner.read(args.panel));runner.write_new(args.output,value)
        print(json.dumps(dict(signature=value['signature'],scenarios=len(value['scenarios']),physical_episodes_started=0)))
    elif args.command=='grant-final':
        from tools.sci_engineering.release import issue_grant
        value=issue_grant(ROOT,args.freeze,args.selection_record,args.panel,args.exposure_registry)
        print(json.dumps(dict(signature=value['signature'],phase=value['phase'],grant_count_this_round=1,
            metadata_only_nonexposure_audit=value['metadata_only_nonexposure_audit'],physical_episodes_started=0)))
    elif args.command=='scan':
        value=design.scan_historical_support(args.historical_root)
        runner.write_new(args.output,value)
        print(json.dumps(dict(metadata=value['candidate_metadata_files'],actual_scene_ids=len({r['scenario_id'] for r in value['actual_support']}),
            unresolved_starts=len(value['unresolved_starts']),parse_errors=len(value['parse_errors']),signature=value['signature'])))
    elif args.command=='design':
        registry=runner.read(args.registry)
        blocks=design.select_source_blocks(registry,n_confirmation=args.confirmation_blocks,n_development=args.development_blocks,
            episode_minutes=args.episode_minutes,seed=args.seed,historical_mode=args.historical_mode)
        value=design.scenario_panel(blocks,seed=args.seed,
            variants_per_block=dict(confirmation=args.confirmation_variants,development=args.development_variants))
        runner.write_new(args.output,value)
        print(json.dumps(dict(source_blocks=len(blocks),scenarios=len(value['scenarios']),class_counts=value['class_counts'])))
    elif args.command=='pilot-proposal':
        value=pilot_proposal(args.start_hour,duration_minutes=args.duration_minutes,warmup_minutes=args.warmup_minutes,
            fault_duration_minutes=args.fault_duration_minutes,seed=args.seed)
        runner.write_new(args.output,value)
        print(json.dumps(dict(scenarios=4,expected_pairs_with_five_policies=20,full_window_minutes=args.duration_minutes,
            actual_preparation_minutes=args.warmup_minutes,static_initialization_repetitions=1,physical_episodes_started=0)))
    elif args.command=='freeze':
        from tools.sci_engineering.policy import policy_specs
        from tools.sci_engineering.environment import model_contract
        pool=runner.read(args.policy_specs) if args.policy_specs else list(policy_specs().values())
        if isinstance(pool,dict):pool=list(pool.values())
        selected=_policy_subset(pool,args.policies)
        configs=(runner.read(args.configurations) if args.configurations else
                 dict(H0=model_contract(design.MODEL_ID,dict(sci_engineering=dict(configuration_id='H0')))[1]))
        value=runner.create_freeze(ROOT,args.output,selected,configs,
            dict(max_actual_attempts=args.attempt_limit,max_registered_pairs=args.attempt_limit,
                 max_experiment_wall_hours=args.wall_hours),
            selection_record=runner.read(args.selection_record) if args.selection_record else None)
        print(json.dumps(dict(signature=value['signature'],policies=len(selected),configurations=list(configs),physical_episodes_started=0)))
    elif args.command=='register':
        freeze=runner.read(args.freeze);panel=runner.read(args.panel)
        if args.role:
            panel=copy.deepcopy(panel);panel.pop('signature',None)
            panel['scenarios']=[s for s in panel['scenarios'] if s.get('role')==args.role]
            used={s['source_block_id'] for s in panel['scenarios']}
            panel['source_blocks']=[b for b in panel.get('source_blocks',[]) if b['source_block_id'] in used]
            panel['signature']=design.digest(panel)
        specs=_policy_subset(freeze['policy_specs'],args.policies)
        configs={key:freeze['configurations'][key] for key in args.configurations.split(',')}
        value=runner.register_campaign(ROOT,args.directory,panel,specs,configs,args.freeze,phase=args.phase,
            raw_root=args.raw_root,pilot_proof=args.pilot_proof,
            final_release_grant=runner.read(args.final_release_grant) if args.final_release_grant else None)
        print(json.dumps(dict(registered_pairs=value['registered_pairs'],phase=args.phase,physical_episodes_started=0)))
    else:
        value=runner.run_registered(ROOT,args.directory,retry_failed=args.retry_failed,max_pairs=args.max_pairs)
        print(json.dumps(value,ensure_ascii=True))
        return 0 if value['failed']==0 and value['pending']==0 else 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
