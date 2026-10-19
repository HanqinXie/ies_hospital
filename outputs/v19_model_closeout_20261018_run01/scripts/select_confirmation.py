"""Select the one-shot V19 confirmation only after an exact final freeze.

Metadata and hashes only: no annual numeric rows, raw trajectories, outcome
rankings, solver, physical environment or training are opened or started.
The seed, quota and 600-minute incident recipe are fixed before execution.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve()
RUN = SCRIPT.parent.parent
ROOT = RUN.parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

SEED = 20261018
QUOTAS = {1:1, 2:3, 3:2, 4:2}
MINUTES = 600.
EMBARGO = 60.
YEAR_MINUTES = 8760 * 60
REVIEW = RUN / 'design/CONFIRMATION_POOL_REVIEW.json'
REVIEW_SHA256 = '967f6aeb6ef322c9861d2d5116424574f2c7e71d219a7b6e97db07c40ac4ca8b'
TEMPLATES = ROOT / 'outputs/v18_execution_recovery_20261018_run01/design/MAIN_FINAL_R1_PROPOSAL.json'
DESTINATION = RUN / 'design/confirmation_selection_final01'
RECIPE = dict(id='v19_confirmation_fixed600_v1', seed=SEED,
    quarter_quotas=QUOTAS, source_template_groups=list(range(8)),
    canonical_classes=['L1','L2','L3','L4'], first_event_minute=90.,
    fault_start_minute=90., fault_duration_minutes=180.,
    shock_start_minute=90., shock_duration_minutes=180.,
    full_horizon_minutes=MINUTES, normal_prefix_minutes=90.,
    post_event_recovery_minutes=330., endpoint_source_hour_reserved=True,
    template_fault_targets_and_shock_amplitudes_unchanged=True,
    online_future_event_access=False)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,
        separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def path_in_root(value):
    path=Path(value)
    path=(path if path.is_absolute() else ROOT/path).resolve()
    if not path.is_relative_to(ROOT):raise ValueError('Metadata path leaves the workspace')
    return path


def observe(path, bindings, *, expected=None):
    path=path_in_root(path);actual=sha(path)
    if expected is not None and actual!=expected:
        raise ValueError('Changed source metadata: '+str(path))
    key=path.relative_to(ROOT).as_posix()
    if key in bindings and bindings[key]!=actual:
        raise ValueError('Metadata changed during refresh: '+key)
    bindings[key]=actual
    return json.loads(path.read_text(encoding='utf8'))


def signed(value):
    if value.get('signature')!=digest({k:v for k,v in value.items() if k!='signature'}):
        raise ValueError('Invalid immutable metadata signature')
    return value


def scenes(value):
    if isinstance(value,dict):
        if 'start_hour' in value and 'duration_steps' in value:
            yield value
        else:
            for child in value.values():yield from scenes(child)
    elif isinstance(value,list):
        for child in value:yield from scenes(child)


def support(scene):
    start=(int(scene['start_hour'])-1)*60.
    duration=int(scene['duration_steps'])*float(scene.get('step_minutes',15.))
    end=start+(math.floor(duration/60.)+1)*60.
    if not 0<=start<end<=YEAR_MINUTES:
        raise ValueError('Scenario lacks complete original-year endpoint support')
    registered=scene.get('full_source_support')
    if registered and (registered['raw_start_minute']!=start or registered['raw_end_minute']!=end):
        raise ValueError('Registered support disagrees with its real duration')
    return start,end


def interval(row):
    a,b=float(row['raw_start_minute']),float(row['raw_end_minute'])
    if not (math.isfinite(a) and math.isfinite(b) and 0<=a<b<=YEAR_MINUTES):
        raise ValueError('Invalid historical exclusion interval')
    return a,b


def merged(intervals):
    result=[]
    for a,b in sorted(intervals):
        if not result or a>result[-1][1]:result.append([a,b])
        else:result[-1][1]=max(result[-1][1],b)
    return result


def expand_scene(scene):
    if scene.get('is_final_blind') or scene.get('experiment_role') in ('final','sealed_final','final_blind'):
        raise ValueError('Original sealed-final scene contents are not eligible metadata')
    a,b=support(scene)
    return max(0.,a-EMBARGO),min(float(YEAR_MINUTES),b+EMBARGO)


def validate_freeze(value, bindings):
    # Importing these adapters does not construct a physical environment.
    from tools.model_closeout import runner as r, freeze as f, gate as g
    path=r.freeze_file(value)
    frozen=observe(path,bindings)
    if frozen.get('kind')!='v19_final_shared_model_freeze':
        raise ValueError('A real final shared-model freeze is required before selection')
    current=r.source_binding()
    _,resolved=r.model_contract(r.MODEL_ID,frozen['model_config'])
    if digest(resolved)!=digest(frozen['model_config']):
        raise ValueError('Final model configuration was not fully resolved')
    specs=r.policy_specs()
    if digest(frozen['policies'])!=digest(specs):
        raise ValueError('Current full policy registry differs from the final freeze')
    r.validate_final_freeze(frozen,current,r.MODEL_ID,resolved,[specs[k] for k in ('E','K1','C19')])
    f.validate_test_gate(frozen['source_bound_test_gate'],current,g.test_binding())
    command=frozen['affected_regression_command']
    if command.get('exit_code')!=0:
        raise ValueError('Final freeze has no actually passing regression command')
    name=command.get('name','')
    if not name or not name.replace('_','').isalnum():raise ValueError('Final freeze lacks its real logged gate identity')
    logged=observe(RUN/'logs'/(name+'.command.json'),bindings)
    gate=observe(RUN/'test_gates'/(name+'.json'),bindings)
    if digest(logged)!=digest(command) or digest(gate)!=digest(frozen['source_bound_test_gate']):
        raise ValueError('Actual saved command/test-gate evidence differs from the final freeze')
    config=observe(RUN/'M19_MODEL_CONFIG.json',bindings)
    if digest(r.model_contract(r.MODEL_ID,config)[1])!=digest(resolved):
        raise ValueError('Current model parameter file differs from the final freeze')
    if digest(f.resolved_evaluation_contract(frozen['evaluation_contract']))!=digest(frozen['evaluation_contract']):
        raise ValueError('Evaluation/emissions parameters differ from the final freeze')
    baseline=RUN/'baseline/initial.json'
    initial=observe(baseline,bindings,expected=frozen['original_protected_baseline_sha256'])
    if initial.get('issues'):raise ValueError('Protected input/source baseline has unresolved issues')
    return frozen,path,current


def historical_support(bindings):
    review=observe(REVIEW,bindings,expected=REVIEW_SHA256)
    inventory=review['source_inventory']
    if len(inventory)!=82 or digest(inventory)!=review['source_inventory_digest']:
        raise ValueError('The reviewed 82-file source inventory changed')
    excluded=[];sealed=None
    for row in inventory:
        value=observe(row['path'],bindings,expected=row['sha256'])
        if isinstance(value,dict) and 'signature' in value:signed(value)
        if isinstance(value,dict):
            excluded.extend(interval(x) for x in value.get('excluded_support',[]))
            for block in value.get('blocks',[]):
                if isinstance(block,dict) and 'raw_start_minute' in block:excluded.append(interval(block))
            if 'sealed_final_days' in value:
                current=sorted(value['sealed_final_days'])
                if sealed is not None and current!=sealed:raise ValueError('Sealed exclusion identities disagree')
                sealed=current
        excluded.extend(expand_scene(scene) for scene in scenes(value))
    from tools.model_closeout.data_design import SEALED_DAYS
    if sealed!=sorted(SEALED_DAYS) or len(sealed)!=12 or digest(sealed)!=review['sealed_identity_digest']:
        raise ValueError('The original twelve sealed metadata identities changed')
    excluded.extend((float(day*1440),float((day+1)*1440)) for day in sealed)
    # Recheck the frozen old actual-attempt registry rather than assuming it
    # never grew after the original pool review. No result values are read.
    for old in review['frozen_v43_registry_live_checks']:
        manifest_path=path_in_root(old['path'])
        manifest=observe(manifest_path,bindings,expected=old['sha256'])
        started=set();known={s['id'] for s in manifest.get('scenarios',[])}
        old_journals={x['path']:x['sha256'] for x in old['journal_bindings']}
        actual=sorted(manifest_path.parent.glob('shards/*/attempts.jsonl'))
        if {p.relative_to(ROOT).as_posix() for p in actual}!=set(old_journals):
            raise ValueError('Historical actual-attempt journal inventory changed; refresh review first')
        for path in actual:
            key=path.relative_to(ROOT).as_posix()
            if sha(path)!=old_journals[key]:raise ValueError('Historical attempt journal changed; refresh review first')
            bindings[key]=old_journals[key]
            for line in path.read_text(encoding='utf8').splitlines():
                item=json.loads(line)
                if item.get('event')=='started' and item.get('scenario_id') is not None:started.add(item['scenario_id'])
        if not started<=known or len(started)!=old['current_attempted_specs']:
            raise ValueError('Historical actual-start scope differs from reviewed training/development support')
    return excluded,review


def current_registered_support(bindings):
    directory=RUN/'experiments';batches=set();files=set()
    for name in ('plan.json','manifest.json','ATTEMPTS.jsonl'):
        for path in directory.rglob(name):
            batches.add(path.parent);files.add(path.relative_to(ROOT).as_posix())
    excluded=[];counts=dict(batch_directories=0,registered_pairs=0,actual_started_attempts=0,
        terminal_attempts=0,started_without_terminal_record=0,partial_registrations=0)
    for batch in sorted(batches):
        manifest_path=batch/'manifest.json'
        if not manifest_path.is_file():raise ValueError('Orphan registration/start has no verifiable support manifest')
        manifest=signed(observe(manifest_path,bindings))
        definitions=manifest.get('scenarios')
        if not isinstance(definitions,list) or not definitions:raise ValueError('Registered manifest has no complete support')
        excluded.extend(expand_scene(s) for s in definitions)
        counts['batch_directories']+=1
        plan_path=batch/'plan.json'
        if plan_path.is_file():
            plan=observe(plan_path,bindings)
            if (plan['manifest_sha256']!=bindings[manifest_path.relative_to(ROOT).as_posix()]
                    or plan['manifest_signature']!=manifest['signature']
                    or plan['scenario_ids']!=[s['id'] for s in definitions]
                    or plan['source_sha256']!=manifest['source_sha256']
                    or plan['registered_pairs']!=len(definitions)*len(plan['policy_specs'])):
                raise ValueError('Registered plan/manifest binding disagrees')
            counts['registered_pairs']+=plan['registered_pairs']
        else:
            plan=None;counts['partial_registrations']+=1
        journal=batch/'ATTEMPTS.jsonl';starts=set();ends=set()
        if journal.is_file():
            bindings[journal.relative_to(ROOT).as_posix()]=sha(journal)
            if plan is None:raise ValueError('Actual start has no immutable plan')
            allowed={(s['id'],p['policy_id']) for s in definitions for p in plan['policy_specs']}
            for line in journal.read_text(encoding='utf8').splitlines():
                row=json.loads(line)
                if row.get('event') not in ('started','finished'):continue
                key=(row['scenario_id'],row['policy_id'])
                target=starts if row['event']=='started' else ends
                if key not in allowed or key in target:raise ValueError('Orphan or duplicate actual attempt journal identity')
                target.add(key)
            if not ends<=starts:raise ValueError('Terminal attempt has no real start')
        counts['actual_started_attempts']+=len(starts)
        counts['terminal_attempts']+=len(ends)
        counts['started_without_terminal_record']+=len(starts-ends)
    return excluded,counts,sorted(files)


def candidates(excluded):
    result=[]
    for start_hour in range(1,8761):
        a=(start_hour-1)*60.;b=a+660.
        if a-EMBARGO<0 or b+EMBARGO>YEAR_MINUTES:continue
        if any(a-EMBARGO<y and x<b+EMBARGO for x,y in excluded):continue
        day=(start_hour-1)//24
        result.append(dict(start_hour=start_hour,day_index=day,season_quarter=1+min(3,day//91),
            raw_start_minute=a,raw_end_minute=b,embargo_start_minute=a-EMBARGO,embargo_end_minute=b+EMBARGO))
    return result


def select_eight(pool):
    # Hash ranking is a fixed-seed permutation independent of Python RNG versions.
    ranked=sorted(pool,key=lambda x:digest([SEED,'v19_calendar_support_only',x['start_hour']]))
    visits=0
    def search(selected,remaining,offset):
        nonlocal visits
        visits+=1
        if visits>1000000:raise ValueError('Bounded deterministic metadata search exhausted; no fallback selection')
        if not any(remaining.values()):return selected
        quarter=min(q for q,n in remaining.items() if n)
        for i in range(offset,len(ranked)):
            item=ranked[i]
            if item['season_quarter']!=quarter:continue
            if any(item['day_index']==x['day_index'] or
                    (item['embargo_start_minute']<x['embargo_end_minute'] and
                     x['embargo_start_minute']<item['embargo_end_minute']) for x in selected):continue
            rest=dict(remaining);rest[quarter]-=1
            next_offset=i+1 if rest[quarter] else 0
            answer=search(selected+[item],rest,next_offset)
            if answer is not None:return answer
        return None
    chosen=search([],dict(QUOTAS),0)
    if chosen is None:raise ValueError('No eight complete backgrounds satisfy fixed quarter quotas and support isolation')
    return sorted(chosen,key=lambda x:(x['season_quarter'],x['start_hour'])),visits


def build_scenes(chosen, frozen, templates):
    from tools.control_improvement.data_design import validate_observation_support
    from tools.autonomous_dispatch.data_design import PHYSICAL_FIELDS
    from tools.model_closeout.data_design import validate_sealed_support
    result=[]
    for index,window in enumerate(chosen):
        root_id='v19_confirmation_root:'+digest([SEED,window['start_hour'],frozen['signature']])[:24]
        for class_index,canonical in enumerate(RECIPE['canonical_classes']):
            source=templates['scenarios'][index*4+class_index]
            if source['canonical_class']!=canonical:raise ValueError('SOURCE05 template ordering changed')
            scene=copy.deepcopy(source)
            for key in list(scene):
                if key.startswith(('v17_','v18_','v19_')):scene.pop(key)
            scene.update(start_hour=window['start_hour'],source_day_index=window['day_index'],
                season_quarter=window['season_quarter'],duration_steps=40,step_minutes=15.,
                initialization_seed=SEED,root_family_id=root_id,family_id=root_id,
                parent_template_id=root_id,parent_instance_id=root_id,design_family=root_id,
                base_state_id='v19_confirmation_initial:'+digest([window['start_hour'],SEED,scene['pre_event_grid_limit_kW']]),
                source_v18_scenario_id=source['id'],source_v18_template_index=index*4+class_index,
                experiment_role='confirmation',requires_policy_freeze=True,fit_allowed=False,
                selection_allowed=False,is_final_blind=False,original_final12_evaluated=False,
                new_independent_background=True,development_background=False,
                historical_exposure_status='full_support_isolated_from_registered_history_and_v19',
                background_status='post_freeze_support_isolated_incident',source_outcomes_used_for_selection=False,
                sampling_cohort='v19_one_shot_confirmation',analysis_cohort='v19_one_shot_confirmation',
                purpose='post_freeze_independent_incident_confirmation',
                demand_background='same_hospital_year_unused_complete_source_support',
                sampling_role='post_freeze_confirmation',sampling_method='fixed_seed_calendar_metadata_only',
                coordinate_design_version='model_closeout.v19.confirmation_fixed600.v1',
                sample_schema_version='hospital_ies.model_closeout_confirmation.v19.v1',
                distribution_id='model_closeout.v19.finite_confirmation_mechanisms',
                design_variant=RECIPE['id']+'_'+str(index)+'_'+canonical,
                fault_start_minute=90.,fault_duration_minutes=180.,
                shock_start_minute=90.,shock_duration_minutes=180.,first_event_minute=90.,
                normal_history_minutes=90.,history_interval_minutes=[0.,90.],
                score_interval_minutes=[90.,270.],tail_interval_minutes=[270.,600.],
                continuous_recovery_tail_minutes=330.,core_four_class_denominator=True)
            events=[]
            if scene['faults']:
                events.append(dict(origin='exogenous',kind='device_fault',faults=copy.deepcopy(scene['faults']),
                    start_minute=90.,end_minute=270.))
            if scene['shock'] or scene.get('shock_by_priority'):
                events.append(dict(origin='exogenous',kind='demand_shock',shock=copy.deepcopy(scene['shock']),
                    shock_by_priority=copy.deepcopy(scene.get('shock_by_priority',{})),start_minute=90.,end_minute=270.))
            scene['exogenous_events']=events
            scene['propagation_pair_id']='v19_confirmation_prop:'+digest([root_id,scene['faults'],90.,180.])[:24] if canonical in ('L1','L2') else None
            scene['actual_p0_load_multiplier']={k:(1.+float(scene['shock'].get(k,0.)))*
                (1.+float(scene.get('shock_by_priority',{}).get('P0',{}).get(k,0.))) for k in ('e','h','c')}
            scene['full_source_support']=validate_observation_support(scene)
            scene['time_block_id']='v19_confirmation_support:'+digest(scene['full_source_support'])[:24]
            scene['exogenous_stream_id']='v19_confirmation_exogenous:'+digest([root_id,events])[:24]
            scene['physical_scenario_signature']=digest({k:scene.get(k) for k in PHYSICAL_FIELDS})
            scene['id']='v19_confirm_'+canonical+'_'+digest([root_id,scene['physical_scenario_signature'],RECIPE['id']])[:24]
            scene['matched_group_id']=scene['design_cell_id']='v19_confirmation_cell:'+digest([root_id,canonical])[:24]
            validate_sealed_support(scene)
            if support(scene)!=(window['raw_start_minute'],window['raw_end_minute']):
                raise ValueError('Constructed scenario left selected full support')
            result.append(scene)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze',required=True,help='Direct final freeze JSON filename in canonical V19 run')
    args=parser.parse_args(argv)
    if RUN!=ROOT/'outputs/v19_model_closeout_20261018_run01':raise ValueError('Use the canonical reviewed selector path')
    if DESTINATION.exists():raise FileExistsError('One-shot confirmation selection already has evidence; do not overwrite or reseed')
    bindings={SCRIPT.relative_to(ROOT).as_posix():sha(SCRIPT)}
    frozen,freeze_path,source=validate_freeze(args.freeze,bindings)
    historical,review=historical_support(bindings)
    recent,counts,batch_files=current_registered_support(bindings)
    excluded=merged(historical+recent)
    pool=candidates(excluded)
    chosen,visits=select_eight(pool)
    templates=signed(observe(TEMPLATES,bindings))
    definitions=build_scenes(chosen,frozen,templates)
    from tools.model_closeout import data_design as d, runner as r
    registration=dict(schema='hospital_ies.v19.confirmation_isolation.v1',
        freeze_signature=frozen['signature'],historical_training_included=True,v19_development_included=True,
        source_inventory_sha256=copy.deepcopy(bindings),
        excluded_support=[dict(raw_start_minute=a,raw_end_minute=b) for a,b in excluded],
        prior_and_candidate_embargo_minutes_each_side=EMBARGO,
        prior_unstarted_registered_and_partial_support_conservatively_excluded=True,
        old_training_and_development_review_sha256=REVIEW_SHA256,
        current_v19_attempt_accounting=counts,selector_sha256=sha(SCRIPT),
        selection_seed=SEED,recipe=RECIPE)
    registration['signature']=digest(registration)
    proposal=d.panel('v19_independent_confirmation8',definitions,
        bindings={name:dict(sha256=value) for name,value in bindings.items()},
        freeze_signature=frozen['signature'],evaluation_role='independent_confirmation',
        isolation_registration=registration)
    proposal.pop('signature')
    proposal.update(coordinate_recipe=RECIPE,primary_comparison_window_minutes=[0.,600.],
        incident_score_and_recovery_intervals_are_not_the_auxiliary_legacy_primary_window=True,
        selected_background_count=8,planned_matched_policy_keys=['E','K1','C19'],planned_trajectories=96,
        selector_sha256=sha(SCRIPT),no_population_or_independent_hospital_claim=True)
    proposal['signature']=digest(proposal)
    # Refuse moving files/source/registration inventories, even if a completed
    # old attempt merely acquired another journal line while refreshing.
    for name,expected in bindings.items():
        if sha(path_in_root(name))!=expected:raise ValueError('Selection input changed before output: '+name)
    now_files=set()
    for name in ('plan.json','manifest.json','ATTEMPTS.jsonl'):
        now_files.update(p.relative_to(ROOT).as_posix() for p in (RUN/'experiments').rglob(name))
    if sorted(now_files)!=batch_files:raise ValueError('New V19 registration or attempt journal appeared during selection')
    if r.source_binding()!=source:raise ValueError('Production source changed before selection output')
    validate_freeze(args.freeze,{})
    if Counter(x['season_quarter'] for x in chosen)!=Counter(QUOTAS):raise ValueError('Fixed quarter quota was not met')
    receipt=dict(schema='hospital_ies.v19.one_shot_confirmation_selection.v1',
        utc=datetime.now(timezone.utc).isoformat(),freeze_path=str(freeze_path),freeze_sha256=sha(freeze_path),
        freeze_signature=frozen['signature'],selector_sha256=sha(SCRIPT),seed=SEED,recipe=RECIPE,
        reviewed_source_file_count=len(review['source_inventory']),eligible_hour_start_count=len(pool),
        eligible_calendar_day_count=len({x['day_index'] for x in pool}),
        selected_background_count=8,selected_backgrounds=chosen,deterministic_search_nodes=visits,
        actual_current_v19_scope=counts,proposal_signature=proposal['signature'],
        isolation_registration_signature=registration['signature'],source_inventory_sha256=bindings,
        annual_numeric_values_read=False,original_final12_trajectory_or_fault_script_opened=False,
        physical_episodes_started=0,training_started=0,outcome_ranking_read=False,
        interpretation='Single-hospital/year support-isolated incident confirmation; eight backgrounds, not 32 independent backgrounds')
    # The first mutation occurs only after every precondition and pure metadata
    # construction has passed. A failed partial write is retained, never rerun.
    DESTINATION.mkdir(parents=False,exist_ok=False)
    for name,value in [('ISOLATION_REGISTRATION.json',registration),('CONFIRMATION_PROPOSAL.json',proposal)]:
        with (DESTINATION/name).open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
    receipt['proposal_sha256']=sha(DESTINATION/'CONFIRMATION_PROPOSAL.json')
    receipt['isolation_registration_sha256']=sha(DESTINATION/'ISOLATION_REGISTRATION.json')
    receipt['signature']=digest(receipt)
    with (DESTINATION/'SELECTION_RECEIPT.json').open('x',encoding='utf8') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(dict(status='selected_metadata_only',backgrounds=8,specifications=32,
        planned_trajectories=96,proposal=str(DESTINATION/'CONFIRMATION_PROPOSAL.json'),
        selector_sha256=receipt['selector_sha256'],proposal_sha256=receipt['proposal_sha256']),ensure_ascii=True))
    return 0


if __name__=='__main__':raise SystemExit(main())
