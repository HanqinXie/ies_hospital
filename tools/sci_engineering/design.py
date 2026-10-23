"""V21 incident metadata, actual historical exposure and disjoint source blocks.

Only manifests, proposals, design JSON and attempt identity journals are read.
Annual input inventories and unexecuted proposals are never called incident
exposure. No raw trajectory or RESULT payload is opened by this module.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess

MODEL_ID = 'M21_sci_engineering'
SCHEMA = 'hospital_ies.sci_design.v21.v1'
YEAR_MINUTES = 8760 * 60
SEALED_DAYS = (21, 26, 59, 77, 92, 127, 140, 196, 264, 284, 344, 359)
CANONICAL_TO_NATIVE = dict(L1='L1', L2='L3', L3='L2', L4='L4b')
FAULT_TARGETS = ('GT_1','GT_2','GT_3','HRSG_1','HRSG_2','HRSG_3',
    'BOILER_1','EC_1','EC_2','EC_3','ABS','TOWER','GRID','DIESEL',
    'AUX_FAN_OR','AUX_FAN_ER','AUX_FAN_ICU','AUX_CHW_PUMP','AUX_HEAT_PUMP')
AUXILIARY_TARGETS = frozenset(('AUX_FAN_OR','AUX_FAN_ER','AUX_FAN_ICU','AUX_CHW_PUMP','AUX_HEAT_PUMP'))
QUARTER_DAYS = (0, 90, 181, 273, 365)
BUSINESS_ZONES = dict(
    emergency_department=('ER_TRAUMA1_FLR_1','ER_TRAUMA2_FLR_1','ER_EXAM1_MULT4_FLR_1','ER_EXAM3_MULT4_FLR_1'),
    observation_rooms=('IC_PATROOM1_MULT5_FLR_2','IC_PATROOM2_FLR_2','IC_PATROOM3_MULT6_FLR_2'),
    operating_rooms=('OR1_FLR_2','OR2_MULT5_FLR_2','OR3_FLR_2','OR4_FLR_2'))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _decode(content):
    return content.decode('utf-16' if content.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')


def source_support(scene):
    """Episode includes its real warmup/tail; reserve its endpoint source hour."""
    start = (int(scene['start_hour']) - 1) * 60.
    duration = float(scene['duration_steps']) * float(scene.get('step_minutes', 15.))
    end = start + (math.floor(duration / 60.) + 1) * 60.
    declared = scene.get('full_source_support', {})
    if isinstance(declared, dict):
        start = min(start, float(declared.get('raw_start_minute', start)))
        end = max(end, float(declared.get('raw_end_minute', end)))
    hours = scene.get('initialization_source_hours', [])
    if isinstance(hours, list) and hours and all(isinstance(h, (int, float)) for h in hours):
        start = min(start, (min(hours) - 1) * 60.)
        end = max(end, max(hours) * 60.)
    if not (math.isfinite(start+end) and 0 <= start < end <= YEAR_MINUTES):
        raise ValueError('Complete initialization/warmup/event/tail support must stay inside source year')
    return dict(raw_start_minute=start, raw_end_minute=end)


def _synthetic(path):
    return any(part.lower() in ('test_tmp', '.pytest_cache', 'temp', 'tests')
        or part.lower().startswith('test_') or part.lower().endswith('_tests')
        for part in path.parts)


def _enumerate_json(root):
    command = ['rg','--files','--hidden','--no-ignore','-g','*.json','-g','*.jsonl',
        '-g','!**/.git/**','-g','!**/node_modules/**','-g','!**/.venv/**', str(root)]
    try:
        done = subprocess.run(command, capture_output=True, check=False, encoding='utf-8')
        if done.returncode not in (0, 1):
            raise OSError(done.stderr)
        return [Path(value) for value in done.stdout.splitlines()]
    except FileNotFoundError:
        return [p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.json','.jsonl')]


def _scene_nodes(value):
    if isinstance(value, dict):
        if all(key in value for key in ('start_hour','duration_steps')):
            yield value
        for child in value.values():
            yield from _scene_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _scene_nodes(child)


def _referenced_scene_ids(value):
    if isinstance(value,dict):
        if isinstance(value.get('scenario_id'),(str,int)):
            yield str(value['scenario_id'])
        for child in value.values():yield from _referenced_scene_ids(child)
    elif isinstance(value,list):
        for child in value:yield from _referenced_scene_ids(child)


def scan_historical_support(root, *, sealed_days=SEALED_DAYS):
    """Catalog planned supports; join actual starts without filtering success.

    Duplicate metadata copies remain in the inventory. Synthetic test paths are
    disclosed but cannot prove scientific exposure. Started IDs without a
    resolvable source interval make a strict historical-unseen claim unavailable.
    """
    root = Path(root).resolve()
    paths = _enumerate_json(root)
    inventory, planned, actual, errors, unresolved = [], [], [], [], []
    scenarios = defaultdict(list)
    plan_context = {}
    cache = {}
    metadata = [p for p in paths if p.suffix.lower()=='.json'
        and any(token in p.name.lower() for token in ('manifest','proposal','design','plan'))
        or p.suffix.lower()=='.json' and 'design' in {x.lower() for x in p.parts}]
    metadata = [p for p in metadata if not any(token in p.name.lower() for token in ('result','trajectory'))
        and 'raw' not in {x.lower() for x in p.parts}]
    for path in sorted(metadata):
        relative = path.relative_to(root).as_posix()
        synthetic = _synthetic(path.relative_to(root))
        try:
            content = path.read_bytes()
            sha = hashlib.sha256(content).hexdigest()
            if sha not in cache:
                value = json.loads(_decode(content))
                nodes = []
                for scene in _scene_nodes(value):
                    sid = scene.get('id', scene.get('scenario_id'))
                    if sid is not None:
                        nodes.append((str(sid), source_support(scene)))
                cache[sha] = (nodes,set(_referenced_scene_ids(value)))
            nodes,referenced = cache[sha]
            if 'plan' in path.name.lower() and referenced and not synthetic:
                plan_context.setdefault(path.parent.resolve(),set()).update(referenced)
            inventory.append(dict(path=relative, sha256=sha, bytes=len(content),
                kind='design_metadata', synthetic_fixture=synthetic, scene_count=len(nodes)))
            for sid, support in nodes:
                row = dict(scenario_id=sid, source=relative, source_sha256=sha,
                    synthetic_fixture=synthetic, **support)
                planned.append(row)
                if not synthetic:
                    scenarios[sid].append(row)
        except Exception as exc:
            errors.append(dict(path=relative, kind='design_metadata', synthetic_fixture=synthetic,
                type=type(exc).__name__, message=str(exc)))
    for path in sorted(p for p in paths if p.suffix.lower()=='.jsonl' and 'attempt' in p.name.lower()):
        relative = path.relative_to(root).as_posix()
        if _synthetic(path.relative_to(root)):
            inventory.append(dict(path=relative, kind='attempt_journal', synthetic_fixture=True,
                                  excluded_from_incident_exposure=True))
            continue
        try:
            content = path.read_bytes()
            sha = hashlib.sha256(content).hexdigest()
            rows = [json.loads(line) for line in _decode(content).splitlines() if line.strip()]
            terminal = {(str(row.get('scenario_id')),str(row.get('policy_id')))
                for row in rows if row.get('event') in ('finished','completed','failed')}
            starts = [row for row in rows if row.get('event') in ('started','start','attempt_started')]
            journal_parents={str(row.get('scenario_id',row.get('scenario'))) for row in rows
                if isinstance(row.get('scenario_id',row.get('scenario')),(str,int))}
            if not journal_parents:
                journal_parents.update(plan_context.get(path.parent.resolve(),()))
            inventory.append(dict(path=relative,sha256=sha,bytes=len(content),kind='attempt_journal',
                synthetic_fixture=False,actual_started_records=len(starts)))
            for row in starts:
                sid = row.get('scenario_id', row.get('scenario'))
                if not isinstance(sid, (str,int)):
                    # Snapshot/action-branch journals record the parent at
                    # capture time, then omit it from sibling branch attempts.
                    # All parent supports are already retained regardless of
                    # branch success. These starts do not invent new roots.
                    parents=set(journal_parents)
                    if isinstance(row.get('attempt_directory'),str):
                        parents.update(plan_context.get(Path(row['attempt_directory']).resolve(),()))
                    if parents:
                        for parent_sid in parents:
                            for candidate in {tuple(r[k] for k in ('raw_start_minute','raw_end_minute')):r
                                    for r in scenarios.get(parent_sid,[])}.values():
                                actual.append(dict(candidate,journal=relative,journal_sha256=sha,
                                    policy_id=row.get('policy_id'),actual_start_utc=row.get('utc',row.get('time_unix')),
                                    terminal_observed=False,exposure_basis='actual_branch_start_bound_to_parent_plan'))
                        continue
                    if ('fit' in path.name.lower() or (not row.get('attempt_directory')
                            and (row.get('command') is not None or row.get('step') is not None))):
                        # Fit/workflow starts are orchestration, not hospital
                        # trajectory identities. Their actual child episode
                        # journals determine source exposure independently.
                        continue
                    unresolved.append(dict(journal=relative,scenario_id=sid,reason='Started record has no scalar scene identity or actual snapshot parent'))
                    continue
                sid = str(sid)
                # Metadata copied into portable stages are multiple byte
                # receipts for one interval, never independent source blocks.
                candidate_copies=scenarios.get(sid, [])
                candidates=list({(r['raw_start_minute'],r['raw_end_minute']):r for r in candidate_copies}.values())
                if not candidates:
                    unresolved.append(dict(journal=relative,scenario_id=sid,reason='Actual start has no source-bound scenario metadata'))
                for candidate in candidates:
                    actual.append(dict(candidate, journal=relative,journal_sha256=sha,
                        policy_id=row.get('policy_id'),actual_start_utc=row.get('utc',row.get('time_unix')),
                        terminal_observed=(sid,str(row.get('policy_id'))) in terminal,
                        exposure_basis='actual_started_attempt_including_failure_or_unknown_tail'))
        except Exception as exc:
            errors.append(dict(path=relative,kind='attempt_journal',synthetic_fixture=False,
                type=type(exc).__name__,message=str(exc)))
    # Exact duplicate starts copied into packages do not create independent incidents.
    actual = list({digest(row):row for row in actual}.values())
    registry = dict(schema=SCHEMA,root=str(root),metadata_inventory=inventory,
        candidate_metadata_files=len(metadata),unique_metadata_contents=len(cache),
        planned_support=planned,actual_support=actual,parse_errors=errors,
        unresolved_starts=unresolved,sealed_days=sorted(set(map(int,sealed_days))),
        result_or_raw_files_read=False,annual_input_inventory_is_incident_exposure=False,
        unexecuted_proposals_are_incident_exposure=False,statistical_iid_claim=False)
    registry['signature'] = digest(registry)
    return registry


def _overlap(a, b, embargo=0.):
    return (float(a['raw_start_minute'])-embargo < float(b['raw_end_minute'])+embargo
        and float(b['raw_start_minute'])-embargo < float(a['raw_end_minute'])+embargo)


def validate_block_isolation(blocks, *, embargo_minutes=60.):
    for index, first in enumerate(blocks):
        for second in blocks[index+1:]:
            if first['source_block_id'] != second['source_block_id'] and _overlap(first,second,embargo_minutes):
                raise ValueError('Source-block full warmup/event/recovery/endpoint support and embargo overlap')
            if set(first.get('source_calendar_days',[]))&set(second.get('source_calendar_days',[])):
                raise ValueError('Source-block calendar-day overlap; same-day time shifts are related backgrounds')
    return dict(isolated=True,block_count=len(blocks),embargo_minutes_each_side=embargo_minutes,
                full_episode_and_endpoint_support=True)


def select_source_blocks(registry, *, n_confirmation=24, n_development=8,
        episode_minutes=720., embargo_minutes=60., seed=20261023, historical_mode='strict'):
    if historical_mode not in ('strict','current_round_isolated'):
        raise ValueError('Explicit historical selection mode required')
    unresolved = registry.get('unresolved_starts', [])
    real_errors = [x for x in registry.get('parse_errors', []) if not x.get('synthetic_fixture')]
    if historical_mode=='strict' and (unresolved or real_errors):
        raise ValueError('Historical exposure has unresolved starts or metadata parse errors')
    if min(n_confirmation,n_development)<0 or episode_minutes<=0:
        raise ValueError('Positive episode duration and nonnegative block counts required')
    blocked = [dict(raw_start_minute=d*1440.,raw_end_minute=(d+1)*1440.) for d in registry.get('sealed_days',SEALED_DAYS)]
    historical = []
    historical_days=set()
    for start,end in sorted({(row['raw_start_minute'],row['raw_end_minute']) for row in registry.get('actual_support',[])}):
        historical_days.update(range(int(start//1440),int((end-1e-7)//1440)+1))
        if historical and start<=historical[-1]['raw_end_minute']:
            historical[-1]['raw_end_minute']=max(historical[-1]['raw_end_minute'],end)
        else:
            historical.append(dict(raw_start_minute=start,raw_end_minute=end))
    blocks = []
    for quarter in range(1,5):
        candidates = list(range(QUARTER_DAYS[quarter-1]*24+1,
                                QUARTER_DAYS[quarter]*24-int(episode_minutes//60)-1))
        random.Random(f'{seed}:quarter:{quarter}').shuffle(candidates)
        for role, total in (('confirmation',n_confirmation),('development',n_development)):
            needed = total//4 + int(quarter<=total%4)
            selected = 0
            for start_hour in candidates:
                support = source_support(dict(start_hour=start_hour,
                    duration_steps=int(episode_minutes/15.),step_minutes=15.))
                if any(_overlap(support, other, embargo_minutes) for other in blocked+blocks):
                    continue
                calendar_days=list(range(int(support['raw_start_minute']//1440),int((support['raw_end_minute']-1e-7)//1440)+1))
                if any(set(calendar_days)&set(other.get('source_calendar_days',[])) for other in blocks):
                    continue
                exposed = any(_overlap(support, other, embargo_minutes) for other in historical)
                if historical_mode=='strict' and exposed:
                    continue
                identity = 'm21_block_'+digest([support,role,seed])[:16]
                same_calendar=bool(set(calendar_days)&historical_days)
                blocks.append(dict(source_block_id=identity,role=role,start_hour=start_hour,
                    source_day_index=(start_hour-1)//24,season_quarter=quarter,
                    episode_minutes=episode_minutes,**support,
                    source_calendar_days=calendar_days,calendar_background_id='m21_calendar_'+digest(calendar_days)[:16],
                    fresh_source_hours=not exposed,historical_same_calendar_day_exposure=same_calendar,
                    new_calendar_background=not same_calendar,
                    globally_historical_unseen=historical_mode=='strict' and not same_calendar and not unresolved and not real_errors,
                    historical_exposure_status=('historically_exposed_current_round_isolated' if exposed
                        else 'new_source_hours_on_historically_exposed_calendar_background' if same_calendar
                        else 'no_recorded_actual_incident_overlap'),
                    exposure_registry_signature=registry.get('signature'),selection_uses_outcomes=False))
                selected += 1
                if selected==needed:
                    break
            if selected!=needed:
                raise ValueError(f'Insufficient isolated source support in quarter {quarter}, role {role}: {selected}/{needed}')
    validate_block_isolation(blocks,embargo_minutes=embargo_minutes)
    return blocks


def scenario_panel(blocks, *, variants_per_block=4, seed=20261023,
                   warmup_minutes=120., recovery_minutes=240., name='main',
                   duration_levels=(60.,120.,180.,360.),fault_targets=FAULT_TARGETS,
                   amplitude_levels=(.25,.5,.75),delay_levels=(0.,15.,30.)):
    validate_block_isolation(blocks)
    rng = random.Random(seed)
    scenes = []
    factor_index = 0
    # Each axis receives its own permutation. The same index never fixes the
    # delay-amplitude-severity tuple; L1/L2 retain paired physical fault clocks.
    variants = sum((variants_per_block.get(b['role'],4) if isinstance(variants_per_block,dict)
                    else int(variants_per_block)) for b in blocks)
    axes = dict(target=[fault_targets[i%len(fault_targets)] for i in range(variants)],
        availability=[(0.,.5)[i%2] for i in range(variants)],
        amplitude=[amplitude_levels[i%len(amplitude_levels)] for i in range(variants)],
        delay=[delay_levels[i%len(delay_levels)] for i in range(variants)],
        duration=[duration_levels[i%len(duration_levels)] for i in range(variants)])
    for values in axes.values():
        rng.shuffle(values)
    for block in blocks:
        count = variants_per_block.get(block['role'],4) if isinstance(variants_per_block,dict) else int(variants_per_block)
        for variant in range(count):
            i = factor_index; factor_index += 1
            target = axes['target'][i]; duration = axes['duration'][i]
            if warmup_minutes+duration+recovery_minutes > block['episode_minutes']:
                raise ValueError('Registered source block cannot contain warmup, full incident and recovery tail')
            factor_id = 'm21_factor_'+digest([block['source_block_id'],variant,seed])[:16]
            for canonical,native in CANONICAL_TO_NATIVE.items():
                has_fault = canonical!='L3'
                has_shock = canonical in ('L3','L4')
                faults = {target:axes['availability'][i]} if has_fault and target not in AUXILIARY_TARGETS else {}
                auxiliary_faults = {target:axes['availability'][i]} if has_fault and target in AUXILIARY_TARGETS else {}
                amplitude = axes['amplitude'][i]
                # Three declared research business templates, with carrier
                # increments on existing priority demand and unchanged classes.
                template = tuple(BUSINESS_ZONES)[i%3]
                zone_shock={zone:dict(e=amplitude,h=amplitude,c=amplitude)
                            for zone in BUSINESS_ZONES[template]} if has_shock else {}
                scene = dict(source_block_id=block['source_block_id'],source_day_index=block['source_day_index'],
                    season_quarter=block['season_quarter'],role=block['role'],experiment_role=block['role'],
                    start_hour=block['start_hour'],duration_steps=int(block['episode_minutes']/15.),step_minutes=15.,
                    pre_event_warmup_steps=1,initialization_seed=seed,
                    canonical_class=canonical,event_class=native,analysis_class=native,
                    stage={'L1':1,'L3':3,'L2':2,'L4b':4}[native],
                    enable_propagation=canonical in ('L2','L4'),event_schema_version='p2a.events.v1',
                    faults=faults,auxiliary_faults=auxiliary_faults,shock={},shock_by_priority={},shock_by_zone=zone_shock,
                    fault_target=target,fault_availability=axes['availability'][i],demand_amplitude=amplitude,
                    clinical_template_id=template,clinical_coefficients_are_research_assumptions=True,
                    response_delay_minutes=axes['delay'][i],fault_start_minute=warmup_minutes,
                    fault_duration_minutes=duration,shock_start_minute=warmup_minutes,shock_duration_minutes=duration,
                    first_event_minute=warmup_minutes,recovery_start_minute=warmup_minutes+duration,
                    history_interval_minutes=[0.,warmup_minutes],tail_interval_minutes=[warmup_minutes+duration,block['episode_minutes']],
                    matched_factor_id=factor_id,root_family_id=block['source_block_id'],family_id=block['source_block_id'],
                    design_family=block['source_block_id'],sampling_cohort='sci_v21_'+block['role'],
                    sampling_method='preregistered_disjoint_source_blocks_and_independent_factor_permutations',
                    grid_limit_kW=850.,pre_event_grid_limit_kW=850.,automatic_fast_shedding=False,
                    requires_policy_freeze=block['role']=='confirmation',selection_allowed=block['role']=='development',
                    fit_allowed=False,is_final_blind=False,original_final12_evaluated=False,
                    historical_exposure_status=block['historical_exposure_status'],
                    new_independent_background=bool(block['globally_historical_unseen']),
                    background_independence='Full source-block isolation within V21; historical exposure separately reported',
                    prehistory_mode='same_continuous_episode_no_reset',exogenous_events=[])
                scene['full_source_support'] = source_support(scene)
                scene['id'] = 'm21_'+canonical+'_'+digest(scene)[:20]
                scenes.append(scene)
    panel = dict(schema=SCHEMA,name=name,model_id=MODEL_ID,source_blocks=copy.deepcopy(blocks),
        scenarios=scenes,label_mapping=CANONICAL_TO_NATIVE,
        class_counts=dict(Counter(s['canonical_class'] for s in scenes)),
        actual_independent_source_blocks=len(blocks),hospital_count=1,weather_year_count=1,
        globally_new_calendar_backgrounds=sum(bool(b.get('new_calendar_background')) for b in blocks),
        fresh_source_hour_blocks=sum(bool(b.get('fresh_source_hours')) for b in blocks),
        original_final12_evaluated=False,selection_uses_outcomes=False,
        factor_axes_independently_permuted=True,seed=seed)
    panel['signature'] = digest(panel)
    return panel


def allowed_hours(panel):
    hours = set()
    for scene in panel['scenarios']:
        support = source_support(scene)
        hours.update(range(int(support['raw_start_minute']//60)+1,
                           int(support['raw_end_minute']//60)+1))
    return hours
