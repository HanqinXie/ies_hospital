"""New-round fixed incident coverage and complete, causal observation support.

Pure specification/registry work. It creates no environment or hospital outcome.
Historical outputs and the frozen physical/execution core remain read-only.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from collections import Counter
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from hospital_ies.dataset_manifest import digest
from hospital_ies.resilience import validate_scenario
from tools.p2b_experiment.design import known_development_support,support_interval
from tools.integrated_experiment.data_design import approved_pressure

VERSION='control_improvement.fixed_mechanisms.v1'
DISTRIBUTION='q_design.control_improvement.fixed_mechanisms.v1'
ROLES=('pilot_train','pilot_validation','development_boundary')
ROLE_MEANINGS=dict(pilot_train='new training',pilot_validation='new development validation',
    development_boundary='new frozen development evaluation, not final blind')
PRESSURES=((0.,0.,.2,0.),(.5,.5,.25,0.),(1.,.7,.5,.1),(1.5,1.,.4,.4),(2.1,1.25,.55,.9))
CONDITIONS={
    'normal_grid850_delay15':dict(event_class='L4a',devices=(),availability=None,grid_limit_kW=850.,response_delay_minutes=15.),
    'GRID0_grid850_delay15':dict(event_class='L4b',devices=('GRID',),availability=0.,grid_limit_kW=850.,response_delay_minutes=15.),
    'GT50_grid350_delay30':dict(event_class='L4b',devices=('GT_1','GT_2','GT_3'),availability=.5,grid_limit_kW=350.,response_delay_minutes=30.),
    'HRSG0_grid350_delay30':dict(event_class='L4b',devices=('HRSG_1','HRSG_2','HRSG_3'),availability=0.,grid_limit_kW=350.,response_delay_minutes=30.),
    'EC0_grid350_delay30':dict(event_class='L4b',devices=('EC_1','EC_2','EC_3'),availability=0.,grid_limit_kW=350.,response_delay_minutes=30.),
    'TOWERunit0_grid350_delay30':dict(event_class='L4b',devices=('TOWER_1','TOWER_2','TOWER_3','TOWER_4'),availability=0.,grid_limit_kW=350.,response_delay_minutes=30.),
}
DOMAIN_SOURCE=dict(path='scenario_sampler.py',joint_pressure='ordinary/elevated/boundary_candidate only',
    fault_availability='SEVERITY_LAYERS full_outage=0, moderate=.5',response_delays='versioned L4b balanced fault delay {0,15,30,45} minutes',
    grid_limits='L4 versioned grid choices {120,160,250,350,500,850} kW',
    timing='Current absolute event schema; new task section5.4 authorizes extended real-data recovery observation')

def _read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _rng(seed,*keys):return np.random.default_rng(int(digest([int(seed),*keys])[:32],16))
def _overlap(a,b):return a['raw_start_minute']<b['raw_end_minute'] and b['raw_start_minute']<a['raw_end_minute']
def _archive(path):
    return any(x=='before' or x.startswith(('canonical_repro_','portable','source_v','replay_v3_source_snapshot')) or x.endswith('_artifacts') for x in path.parts)

def validate_observation_support(spec):
    """Require the complete trajectory and conservative endpoint source hour."""
    n=float(spec.get('duration_steps',0))*float(spec.get('step_minutes',15))
    start=(int(spec.get('start_hour',0))-1)*60
    if start<0 or n<=0 or start+(math.floor(n/60)+1)*60>8760*60:
        raise ValueError('Complete real annual support and endpoint must stay inside 8760 hours')
    return support_interval(spec)

def _exposure(spec,path,root,purpose='actually_attempted_manifest'):
    a=support_interval(spec)
    return dict(raw_start_minute=max(0,a['raw_start_minute']-60),raw_end_minute=min(8760*60,a['raw_end_minute']+60),
        source=path.relative_to(root).as_posix(),source_sha256=_sha(path),scenario_id=spec['id'],purpose=purpose,expansion_minutes=60)

def exposure_registry(root):
    """All actual manifest attempts (including failures), plus old sealed support.

    The old integrated helper omitted new_fixed_v1/new_adaptive_v1. This new
    namespace reads all actual sharded manifests and explicit control/branch
    reports. Unrun proposals are disclosed, never called observed incidents.
    """
    root=Path(root);oldpath=root/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json'
    old=_read(oldpath);rows=known_development_support(root)
    for b in old['p2b_time_blocks']:
        rows.append(dict(raw_start_minute=b['raw_start_minute'],raw_end_minute=b['raw_end_minute'],source=oldpath.relative_to(root).as_posix(),
            source_sha256=_sha(oldpath),purpose='sealed_final' if b['reserved_only'] else 'historical_P2B_background',time_block_id=b['time_block_id']))
    registered=[];planned=[];attempted_count=0
    paths=sorted(p for p in root.glob('outputs/v43_*/**/global_manifest.json') if not _archive(p.relative_to(root)))
    for path in paths:
        m=_read(path);started=set();journal_refs=[]
        for journal in sorted(path.parent.glob('shards/*/attempts.jsonl')):
            journal_refs.append(dict(path=journal.relative_to(root).as_posix(),sha256=_sha(journal)))
            for line in journal.read_text(encoding='utf-8').splitlines():
                row=json.loads(line)
                if row.get('event')=='started' and row.get('scenario_id') is not None:started.add(row['scenario_id'])
        specs={s['id']:s for s in m.get('scenarios',[])}
        if started-set(specs):raise ValueError('Started journal has an orphan scenario outside its actual manifest')
        reference=dict(path=path.relative_to(root).as_posix(),sha256=_sha(path),attempted_specs=len(started),journals=journal_refs)
        if not started:
            planned.append(reference);continue
        registered.append(reference);attempted_count+=len(started)
        rows.extend(_exposure(specs[sid],path,root) for sid in sorted(started))
    # Learning continuations may reuse a manifest. Record their actual reports
    # and full parent support explicitly rather than giving them new roots.
    continuation_reports=[]
    reports=sorted(set(root.glob('outputs/v43_*/learning/*/CLOSED_LOOP_REPORT.json'))|
                   set(root.glob('outputs/v43_*/learning/*/ACTION_BRANCH_REPORT.json')))
    for report in reports:
        if _archive(report.relative_to(root)):continue
        planpath=report.parent/'registered_plan.json'
        if not planpath.exists():continue
        plan=_read(planpath);mp=plan.get('manifest_path')
        if not mp:continue
        parent=root/mp
        if plan.get('manifest_sha256') and _sha(parent)!=plan['manifest_sha256']:raise ValueError('Historical continuation parent bytes changed')
        m=_read(parent);specs={s['id']:s for s in m['scenarios']};value=_read(report)
        ids={r['scenario_id'] for r in value.get('scores',[])}
        for journal in report.parent.glob('snapshot_*/attempts.jsonl'):
            for line in journal.read_text(encoding='utf-8').splitlines():
                r=json.loads(line)
                if r.get('event')=='started' and r.get('scenario_id'):ids.add(r['scenario_id'])
        if ids-set(specs):raise ValueError('Actual continuation identity absent from parent manifest')
        rows.extend(_exposure(specs[sid],report,root,'actually_attempted_continuation_parent_support') for sid in sorted(ids))
        continuation_reports.append(dict(path=report.relative_to(root).as_posix(),sha256=_sha(report),parent_path=mp,parent_sha256=_sha(parent),actual_specs=len(ids)))
    rows=sorted({json.dumps(r,sort_keys=True,separators=(',',':')):r for r in rows}.values(),key=lambda r:(r['raw_start_minute'],r['raw_end_minute'],r['source']))
    sealed=sorted(int(b['time_block_id'].split('_')[-1]) for b in old['p2b_time_blocks'] if b['reserved_only'])
    return rows,dict(sealed_final_days=sealed,old_manifest_path=oldpath.relative_to(root).as_posix(),old_manifest_sha256=_sha(oldpath),
        actual_manifest_attempted_specs=attempted_count,actual_manifests=registered,actual_continuation_reports=continuation_reports,
        planned_unrun_manifests=planned,registry_meaning='Incident support exposure, not previously unseen annual raw measurements; failed started attempts retained',
        no_outcome_or_success_filter=True,unrun_proposals_are_not_incident_exposure=True)

def _spec(block,seed,index):
    config=CONDITIONS[block['condition_id']];e,c,h,p=PRESSURES[index]
    if not approved_pressure([e,c,h,p]):raise ValueError('Registered pressure outside approved joint boxes')
    root='control_root:'+digest([seed,block['day_index']]);timing=block['event_timing']
    sid='control_case_'+digest([seed,block['day_index'],'fixed',index])[:20]
    s=dict(id=sid,stage=4,event_class=config['event_class'],event_schema_version='p2a.events.v1',sample_schema_version=VERSION,
        condition_id=block['condition_id'],pressure_coordinate=index/4,pressure_point_index=index,start_hour=block['start_hour'],duration_steps=40,step_minutes=15,
        history_interval_minutes=[0,60],score_interval_minutes=[60,420],tail_interval_minutes=[420,600],label_horizons_minutes=[15,30,60,120],
        pre_event_warmup_steps=6,initialization_seed=seed,automatic_fast_shedding=False,enable_propagation=True,
        response_delay_minutes=config['response_delay_minutes'],grid_limit_kW=config['grid_limit_kW'],response_clock_anchor='shock_start_minute',
        family_id=root,root_family_id=root,parent_instance_id=root,parent_template_id=root,design_family=root,template_type=config['event_class'],
        base_state_id='control_initial:'+digest([block['start_hour'],6,config['grid_limit_kW'],seed]),
        exogenous_stream_id='control_stream:'+digest([seed,block['day_index']]),time_block_id=block['time_block_id'],
        experiment_role=block['role'],sampling_role='fixed_crosscoverage',sampling_cohort='main',purpose='development',distribution_id=DISTRIBUTION,
        sampling_method='preregistered_fixed_mechanism_pressure_crosscoverage',sampling_weight_is_failure_probability=False,p_reference=None,q_design_density=None,importance_weight=None,
        demand_background='calendar_only_new_incident_support',shock=dict(e=e,h=h,c=c),shock_mode='mixed',shock_by_priority={'P0':{'e':p}} if p else {},
        shock_start_minute=timing['shock_start_minute'],shock_duration_minutes=timing['shock_duration_minutes'],
        stratum='ordinary' if index<=1 else 'elevated' if index==2 else 'boundary_candidate',
        control_capability_note='Unchanged P1B/Gurobi core. GRID0 uses existing diesel permission/start/fuel chain; no on-grid permission or new event detection')
    events=[]
    if block['device']:
        dev=block['device'];s.update(initiating_device=dev,faults={dev:config['availability']},fault_severity_layer='moderate' if config['availability']==.5 else 'full_outage',
            contingency_class='N-1',fault_start_minute=timing['shock_start_minute']+timing['fault_offset_minutes'],fault_duration_minutes=timing['fault_duration_minutes'])
        events.append(dict(origin='exogenous',kind='device_fault',device=dev,availability=config['availability'],start_minute=s['fault_start_minute'],end_minute=s['fault_start_minute']+s['fault_duration_minutes']))
    events.append(dict(origin='exogenous',kind='load_shock',start_minute=s['shock_start_minute'],end_minute=s['shock_start_minute']+s['shock_duration_minutes'],shock=copy.deepcopy(s['shock']),shock_by_priority=copy.deepcopy(s['shock_by_priority'])))
    s['exogenous_events']=events;s['actual_p0_load_multiplier']={k:(1+s['shock'][k])*(1+s['shock_by_priority'].get('P0',{}).get(k,0)) for k in ('e','h','c')}
    s['full_source_support']=validate_observation_support(s);validate_scenario(s)
    return s

def build_proposal(root,seed=20261015):
    excluded,exposure=exposure_registry(root);available=[]
    for day in range(365):
        block=dict(raw_start_minute=day*1440,raw_end_minute=(day+1)*1440)
        if not any(_overlap(block,r) for r in excluded):available.append(day)
    if len(available)<60:raise ValueError('Insufficient nonoverlapping annual support for60 genuinely new backgrounds')
    days=_rng(seed,'calendar_support_only').permutation(available).tolist();blocks=[];scenarios=[];cursor=0
    for cid,config in CONDITIONS.items():
        for role_index,(role,n) in enumerate(zip(ROLES,(6,2,2))):
            for j in range(n):
                day=int(days[cursor]);cursor+=1;timing=_rng(seed,'absolute_timing_independent_of_case_ids',day)
                device=config['devices'][(j+role_index)%len(config['devices'])] if config['devices'] else None
                b=dict(time_block_id=f'control_day_{day:03d}',day_index=day,condition_id=cid,role=role,device=device,
                    raw_start_minute=day*1440,raw_end_minute=(day+1)*1440,start_hour=day*24+int(_rng(seed,'within_day_hour',day).integers(5,11))+1,
                    event_timing=dict(shock_start_minute=float(timing.integers(90,151)),shock_duration_minutes=float(timing.choice([180,240])),
                        fault_offset_minutes=float(timing.choice([-30,0,30])),fault_duration_minutes=float(timing.choice([180,240]))),
                    reserved_only=False,selection='Calendar-only seeded permutation and absolute clocks fixed before outcomes')
                blocks.append(b);scenarios.extend(_spec(b,int(seed),i) for i in range(5))
    p=dict(version=VERSION,distribution_id=DISTRIBUTION,sampling_seed=int(seed),initialization_seed=int(seed),
        budget=dict(initial_specifications=300,fixed_specifications=300,adaptive_specifications=0,maximum_new_specifications=1200,attempted_not_successful_budget=True,two_baseline_policies=['priority_milp','secure_milp']),
        blocks=blocks,fixed_scenarios=scenarios,adaptive_slots=[],excluded_support=excluded,exposure=exposure,candidate_support_days_available=len(available),
        conditions=copy.deepcopy(CONDITIONS),joint_pressure_points=[list(x) for x in PRESSURES],
        approved_domain_source=dict(**DOMAIN_SOURCE,source_sha256=_sha(Path(root)/DOMAIN_SOURCE['path'])),role_meanings=ROLE_MEANINGS,
        probability_semantics=dict(q_design='Named fixed mechanism/pressure coverage',p_reference=None,q_design_density=None,importance_weight=None,not_hospital_failure_probability=True),
        observation_contract=dict(history_minutes=[0,60],primary_score_minutes=[60,420],tail_minutes=[420,600],full_horizon_minutes=[0,600],
            recovery_snapshot_minute=420,recovery_horizon_minutes=120,endpoint_source_hour_reserved=True,embargo_minutes_each_side=60),
        initial_adaptive_selection=False,positive_and_negative_quotas=None,unknown_attempts_retained=True,
        research_assumptions='Ideal internal observation/PCC interlock, GT residual stress, ATS and thermal proxy remain uncalibrated. Grouping is engineering isolation, not iid proof.')
    p['proposal_sha256']=digest(p);validate_proposal(p)
    return p

def validate_proposal(proposal):
    body=copy.deepcopy(proposal);signature=body.pop('proposal_sha256',None)
    if signature!=digest(body):raise ValueError('Registered proposal content/signature changed')
    if proposal['version']!=VERSION or len(proposal['blocks'])!=60 or len(proposal['fixed_scenarios'])!=300 or proposal['adaptive_slots']:
        raise ValueError('Initial fixed budget/version changed')
    if proposal['conditions']!=json.loads(json.dumps(CONDITIONS)): # tuple/list JSON equivalence
        if json.loads(json.dumps(proposal['conditions']))!=json.loads(json.dumps(CONDITIONS)):raise ValueError('Registered mechanism capabilities changed')
    blocks={b['time_block_id']:b for b in proposal['blocks']}
    if len(blocks)!=60 or len({b['day_index'] for b in blocks.values()})!=60:raise ValueError('Duplicate background support')
    if Counter(b['role'] for b in blocks.values())!=Counter(pilot_train=36,pilot_validation=12,development_boundary=12):raise ValueError('Root roles changed')
    for b in blocks.values():
        if b['day_index'] in proposal['exposure']['sealed_final_days'] or any(_overlap(b,r) for r in proposal['excluded_support']):raise ValueError('Selected support overlaps previous or sealed exposure')
        if b['condition_id'] not in CONDITIONS or b['role'] not in ROLES:raise ValueError('Unregistered condition/role')
    specs=proposal['fixed_scenarios']
    if len({s['id'] for s in specs})!=300:raise ValueError('Duplicate incident specification')
    expected=[_spec(b,proposal['initialization_seed'],i) for b in proposal['blocks'] for i in range(5)]
    if specs!=expected:raise ValueError('Fixed registered specification changed')
    for s in specs:
        a=s['full_source_support'];b=blocks[s['time_block_id']]
        if a['raw_start_minute']-60<b['raw_start_minute'] or a['raw_end_minute']+60>b['raw_end_minute']:raise ValueError('History/endpoint/tail/embargo exceed reserved raw block')
    return True

def manifest_components(proposal,scenarios=None):
    validate_proposal(proposal);scenarios=proposal['fixed_scenarios'] if scenarios is None else scenarios
    fixed={s['id']:s for s in proposal['fixed_scenarios']};splits={}
    if len({s['id'] for s in scenarios})!=len(scenarios):raise ValueError('Duplicate materialization')
    for s in scenarios:
        if s['id'] not in fixed or s!=fixed[s['id']]:raise ValueError('Fixed registered role/specification changed')
        role=s['experiment_role'];a=s['full_source_support'];gid='control_background:'+digest([proposal['sampling_seed'],s['time_block_id']])
        splits[s['id']]=dict(group_id=gid,split=role,fit_allowed=role=='pilot_train',experiment_role=role,
            split_version='control.support_roots.v1',embargo_minutes_each_side=60,raw_start_minute=a['raw_start_minute'],raw_end_minute=a['raw_end_minute'],
            time_block_id=s['time_block_id'],purpose='development',is_final_blind=False)
    ordered=sorted(scenarios,key=lambda s:s['id'])
    return dict(splits=splits,options=dict(seed=proposal['initialization_seed'],security_weight=.35,label_horizons=[15,30,60,120],
        history_minutes=60,score_start_minute=60,score_end_minute=420,observed_end_minute=600),
        p2b_shards=[dict(id=f'shard_{i//25:02d}',scenario_ids=[s['id'] for s in ordered[i:i+25]]) for i in range(0,len(ordered),25)],
        p2b_time_blocks=copy.deepcopy(proposal['blocks']),p2b_support=[dict(scenario_id=s['id'],root_family_id=s['root_family_id'],time_block_id=s['time_block_id'],**s['full_source_support']) for s in ordered],
        control_improvement_data_design=dict(version=VERSION,proposal_sha256=proposal['proposal_sha256'],distribution_id=DISTRIBUTION,role_meanings=ROLE_MEANINGS,
            registered_initial_specifications=300,fixed_fraction=1.,scenarios_in_this_manifest=len(scenarios),same_family_versions_share_roles=True),
        p2b_study_distribution=dict(id=DISTRIBUTION,p_reference=None,importance_weight=None,interpretation='Named fixed development coverage, not hospital probability'))

def snapshot_plan(proposal):
    """Outcome-free fixed clocks, training/validation only, complete H120."""
    validate_proposal(proposal);components=manifest_components(proposal);out=[]
    byblock={s['time_block_id']:s for s in proposal['fixed_scenarios'] if s['pressure_point_index']==3}
    for cid in CONDITIONS:
        for role,n in [('pilot_train',3),('pilot_validation',1)]:
            blocks=sorted((b for b in proposal['blocks'] if b['condition_id']==cid and b['role']==role),key=lambda b:b['time_block_id'])[:n]
            for b in blocks:
                s=byblock[b['time_block_id']]
                for t in (60.,180.,420.):
                    out.append(dict(scenario_id=s['id'],root_family_id=s['root_family_id'],group_id=components['splits'][s['id']]['group_id'],
                        condition_id=cid,experiment_role=role,snapshot_minute=t,snapshot_step=int(t/15),horizons_minutes=[60,120],
                        selection_uses_future_event_clock=False,selection_rule='Fixed absolute clocks and pressure point before outcomes',conditional_futures=1,
                        continuation_policy='priority_milp',maximum_candidates=8,all_actions_share_exogenous_future=True))
    return out

def initial_training_subset(proposal,count=8):
    validate_proposal(proposal)
    if not 1<=int(count)<=180:raise ValueError('Training initialization count outside registered train specs')
    pressure_order={3:0,1:1,4:2,0:3,2:4}
    bycondition={cid:sorted((s for s in proposal['fixed_scenarios'] if s['condition_id']==cid and s['experiment_role']=='pilot_train'),key=lambda s:(s['time_block_id'],pressure_order[s['pressure_point_index']])) for cid in CONDITIONS}
    ordered=[]
    for i in range(30):
        for cid in CONDITIONS:ordered.append(bycondition[cid][i]['id'])
    return ordered[:int(count)]
