"""Pure preregistered development coverage; no hospital response or probability fit."""
from __future__ import annotations
from collections import Counter
import copy,hashlib,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
import numpy as np
from hospital_ies.resilience import validate_scenario
from hospital_ies.dataset_manifest import digest
from tools.p2b_experiment.design import known_development_support,support_interval

VERSION='integrated.crosscoverage.v1'
DISTRIBUTION='q_design.integrated.fixed_and_train_adaptive.v1'
ROLES=('pilot_train','pilot_validation','development_boundary')
ROLE_MEANINGS=dict(pilot_train='training',pilot_validation='development_validation',development_boundary='frozen_development_evaluation_not_final_blind')
CONDITIONS={
    'L4a_grid250_delay45':dict(event_class='L4a',grid_limit_kW=250.,response_delay_minutes=45.,device=None,availability=None,role_counts=(12,6,6)),
    'L4a_grid500_delay30':dict(event_class='L4a',grid_limit_kW=500.,response_delay_minutes=30.,device=None,availability=None,role_counts=(8,2,2)),
    'L4b_EC2_grid350_delay30':dict(event_class='L4b',grid_limit_kW=350.,response_delay_minutes=30.,device='EC_2',availability=.5,role_counts=(8,2,2)),
    'L4b_TOWER3_grid350_delay30':dict(event_class='L4b',grid_limit_kW=350.,response_delay_minutes=30.,device='TOWER_3',availability=.5,role_counts=(8,2,2)),
}
ANCHORS=np.asarray([[0.,0.,.2,0.],[.5,.5,.2,0.],[1.3,1.1,.2,.2],[2.1,1.25,.2,.5]])

def _rng(seed,*key):return np.random.default_rng(int(digest([int(seed),*key])[:32],16))
def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def _overlap(a,b):return a['raw_start_minute']<b['raw_end_minute'] and b['raw_start_minute']<a['raw_end_minute']

def pressure_values(u):
    u=float(u)
    if not math.isfinite(u) or not 0<=u<=1:raise ValueError('Pressure coordinate outside registered [0,1]')
    k=min(int(u*3),2);w=u*3-k
    return (ANCHORS[k]*(1-w)+ANCHORS[k+1]*w).tolist()

def approved_pressure(v):
    if len(v)!=4 or any(not math.isfinite(float(x)) for x in v):return False
    e,c,h,p=map(float,v);eps=1e-12
    return ((0<=e<=.5+eps and 0<=c<=.5+eps and 0<=h<=.25+eps and p==0)
        or (.4-eps<=e<=1.3+eps and .3-eps<=c<=1.1+eps and .1-eps<=h<=.5+eps and 0<=p<=.2+eps)
        or (.8-eps<=e<=2.1+eps and .45-eps<=c<=1.25+eps and .05-eps<=h<=.55+eps and .2-eps<=p<=.9+eps))

def exposure_registry(root):
    """Exclude actual old support and sealed blocks; planned/unrun proposals are disclosed."""
    root=Path(root);oldpath=root/'outputs/v43_p2b_20261013_run01/experiment/global_manifest.json';old=_read(oldpath)
    known=known_development_support(root)
    for b in old['p2b_time_blocks']:
        known.append(dict(raw_start_minute=b['raw_start_minute'],raw_end_minute=b['raw_end_minute'],source=oldpath.relative_to(root).as_posix(),
            source_sha256=_sha(oldpath),purpose='sealed_final' if b['reserved_only'] else 'historical_P2B_exposure',time_block_id=b['time_block_id']))
    # Newer actually attempted registry checkpoints may reuse old support, but
    # include them explicitly rather than assuming every later run is old400.
    patterns=('outputs/v43_p2c_*/replay/global_manifest.json','outputs/v43_p2c_*/probe*/global_manifest.json',
        'outputs/v43_integrated_*/new_data*/global_manifest.json')
    paths=sorted({p for pat in patterns for p in root.glob(pat)})
    for path in paths:
        m=_read(path);attempted=set()
        for journal in path.parent.glob('shards/*/attempts.jsonl'):
            for line in journal.read_text(encoding='utf-8').splitlines():
                row=json.loads(line)
                if row['event']=='started':attempted.add(row['scenario_id'])
        for s in m['scenarios']:
            if s['id'] not in attempted:continue
            support=support_interval(s)
            known.append(dict(raw_start_minute=max(0,support['raw_start_minute']-60),raw_end_minute=min(8760*60,support['raw_end_minute']+60),
                source=path.relative_to(root).as_posix(),source_sha256=_sha(path),purpose='actually_attempted_newer_exposure',scenario_id=s['id']))
    known=sorted({json.dumps(v,sort_keys=True,separators=(',',':')):v for v in known}.values(),key=lambda v:(v['raw_start_minute'],v['raw_end_minute'],v['source']))
    sealed=sorted(int(b['time_block_id'].split('_')[-1]) for b in old['p2b_time_blocks'] if b['reserved_only'])
    return known,dict(sealed_final_days=sealed,old_manifest_path=oldpath.relative_to(root).as_posix(),old_manifest_sha256=_sha(oldpath),
        registry_meaning='Incident/background exposure only; annual raw source values have already been available. No claim of new hospital observations.',
        old_planned_P2C_probe='Unrun prior probe proposal is planning, not incident truth; selection is newly seeded and outcome-free.')

def _events(s):
    events=[]
    if s.get('faults'):
        dev=s['initiating_device'];events.append(dict(origin='exogenous',kind='common_mode_fault' if dev=='TOWER' else 'device_fault',
            start_minute=s['fault_start_minute'],end_minute=s['fault_start_minute']+s['fault_duration_minutes'],device=dev,availability=s['faults'][dev]))
    events.append(dict(origin='exogenous',kind='load_shock',start_minute=s['shock_start_minute'],end_minute=s['shock_start_minute']+s['shock_duration_minutes'],
        shock=copy.deepcopy(s['shock']),shock_by_priority=copy.deepcopy(s['shock_by_priority'])))
    s['exogenous_events']=events;s['actual_p0_load_multiplier']={k:(1+s['shock'].get(k,0))*(1+s['shock_by_priority'].get('P0',{}).get(k,0)) for k in ('e','h','c')}
    s['full_source_support']=support_interval(s);validate_scenario(s);return s

def _spec(block,seed,u,sid,role='fixed_crosscoverage'):
    config=CONDITIONS[block['condition_id']];e,c,h,p=pressure_values(u);assert approved_pressure([e,c,h,p])
    root='integrated_root:'+digest([int(seed),block['time_block_id']]);timing=block['event_timing']
    s=dict(id=sid,stage=4,event_class=config['event_class'],event_schema_version='p2a.events.v1',sample_schema_version=VERSION,
        condition_id=block['condition_id'],pressure_coordinate=float(u),start_hour=block['start_hour'],duration_steps=32,step_minutes=15,
        history_interval_minutes=[0,60],score_interval_minutes=[60,420],tail_interval_minutes=[420,480],label_horizons_minutes=[15,30,60],
        pre_event_warmup_steps=6,initialization_seed=int(seed),automatic_fast_shedding=False,enable_propagation=True,
        response_delay_minutes=config['response_delay_minutes'],grid_limit_kW=config['grid_limit_kW'],response_clock_anchor='shock_start_minute',
        family_id=root,root_family_id=root,parent_instance_id=root,parent_template_id=root,design_family=root,template_type=config['event_class'],
        base_state_id='integrated_initial:'+digest([block['start_hour'],6,config['grid_limit_kW'],int(seed)]),
        exogenous_stream_id='integrated_stream:'+digest([int(seed),block['time_block_id']]),time_block_id=block['time_block_id'],
        experiment_role=block['role'],sampling_role=role,sampling_cohort='main',purpose='development',distribution_id=DISTRIBUTION,
        sampling_method='fixed_pressure_crosscoverage' if role=='fixed_crosscoverage' else 'train_only_registered_adaptive',
        sampling_weight_is_failure_probability=False,p_reference=None,q_design_density=None,importance_weight=None,demand_background='calendar_only_new_incident_support',
        shock={'e':e,'h':h,'c':c},shock_mode='mixed',shock_by_priority={'P0':{'e':p}} if p>0 else {},
        shock_start_minute=timing['shock_start_minute'],shock_duration_minutes=timing['shock_duration_minutes'],
        stratum='ordinary' if u<=1/3 else 'elevated' if u<=2/3 else 'boundary_candidate',
        control_capability_note='Frozen P1B contract; no new automatic shedding, detection, energy path or AI fast channel')
    if config['device']:
        dev=config['device'];s.update(initiating_device=dev,faults={dev:config['availability']},fault_severity_layer='moderate',contingency_class='N-1',
            fault_start_minute=timing['shock_start_minute']+timing['fault_offset_minutes'],fault_duration_minutes=timing['fault_duration_minutes'])
    return _events(s)

def build_proposal(root,seed=20261014):
    excluded,exposure=exposure_registry(root);candidates=[]
    for day in range(365):
        block=dict(raw_start_minute=day*1440,raw_end_minute=(day+1)*1440)
        if not any(_overlap(block,r) for r in excluded):candidates.append(day)
    if len(candidates)<60:raise ValueError('Insufficient genuinely nonoverlapping new incident support days')
    days=_rng(seed,'calendar_only_support').permutation(candidates).tolist();blocks=[];specs=[];slots=[];offset=0
    for cid,config in CONDITIONS.items():
        for role,n in zip(ROLES,config['role_counts']):
            for _ in range(n):
                day=int(days[offset]);offset+=1;timing=_rng(seed,'timing_independent_of_case_ids',day)
                b=dict(time_block_id=f'integrated_day_{day:03d}',day_index=day,condition_id=cid,role=role,
                    raw_start_minute=day*1440,raw_end_minute=(day+1)*1440,start_hour=day*24+int(_rng(seed,'within_day_hour',day).integers(5,11))+1,
                    event_timing=dict(shock_start_minute=float(timing.integers(75,136)),shock_duration_minutes=float(timing.choice([120,240])),
                        fault_offset_minutes=float(timing.choice([-30,0,30])),fault_duration_minutes=240.),reserved_only=False,
                    selection='Seeded calendar-only permutation and independent timing; no response/value selection')
                blocks.append(b)
                for j,u in enumerate((0.,1/3,2/3,1.)):
                    sid='integrated_case_'+digest([seed,day,'fixed',j])[:20];specs.append(_spec(b,seed,u,sid))
    for cid in CONDITIONS:
        train=[b for b in blocks if b['role']=='pilot_train' and b['condition_id']==cid]
        order=_rng(seed,'adaptive_train_background_rotation',cid).permutation(len(train))
        for j in range(15):
            b=train[int(order[j%len(train)])];slots.append(dict(id='integrated_case_'+digest([seed,b['day_index'],'adaptive',j])[:20],
                condition_id=cid,time_block_id=b['time_block_id'],experiment_role='pilot_train',slot_index=j))
    proposal=dict(version=VERSION,distribution_id=DISTRIBUTION,sampling_seed=int(seed),initialization_seed=int(seed),
        budget=dict(initial_specifications=300,fixed_specifications=240,adaptive_specifications=60,maximum_new_specifications=1200,
            attempted_not_successful_budget=True,two_baseline_policies=['priority_milp','secure_milp']),
        blocks=blocks,fixed_scenarios=specs,adaptive_slots=slots,excluded_support=excluded,exposure=exposure,role_meanings=ROLE_MEANINGS,
        candidate_support_days_available=len(candidates),conditions=copy.deepcopy(CONDITIONS),pressure_path_anchors=ANCHORS.tolist(),
        approved_domain_source='scenario_sampler.py ordinary/elevated/boundary_candidate joint boxes; not a rectangle of marginal ranges',
        probability_semantics='New named fixed/adaptive research coverage; sampling densities and real accident probabilities unknown',
        adaptive_rule=dict(train_only=True,quotas_per_condition=15,feedback='Observed binary positive OR two complete policy negatives from fixed train cases only; unknowns are not negative',
            selection='Prefer adjacent fixed pressure points of opposite known outcome, choose an unused midpoint/submidpoint; otherwise unused seeded pressure-space fill',
            class_target=None,negative_or_positive_quota=None,unknown_attempts_retained=True,feedback_not_from_development_evaluation=True))
    proposal['proposal_sha256']=digest(proposal);return proposal

def validate_proposal(proposal):
    body=copy.deepcopy(proposal);expected=body.pop('proposal_sha256')
    if digest(body)!=expected:raise ValueError('Registered proposal content changed')
    if len(proposal['fixed_scenarios'])!=240 or len(proposal['adaptive_slots'])!=60:raise ValueError('Initial budget changed')

def _binary_feedback(value):
    policies=value.get('policy_outcomes',{})
    if any(v.get('score_P0_failure')==1 for v in policies.values()):return 1
    if set(policies)=={'priority_milp','secure_milp'} and all(v.get('score_outcome_valid') is True and v.get('score_P0_failure')==0 for v in policies.values()):return 0
    return None

def materialize_adaptive(proposal,training_outcomes):
    validate_proposal(proposal);fixed={s['id']:s for s in proposal['fixed_scenarios']};blocks={b['time_block_id']:b for b in proposal['blocks']}
    for sid,v in training_outcomes.items():
        if sid not in fixed or fixed[sid]['experiment_role']!='pilot_train' or v.get('experiment_role')!='pilot_train':raise ValueError('Adaptive feedback must be registered fixed train outcomes only')
    used={b['time_block_id']:{0.,1/3,2/3,1.} for b in proposal['blocks']};out=[]
    for slot in proposal['adaptive_slots']:
        b=blocks[slot['time_block_id']];cases=sorted((s for s in fixed.values() if s['time_block_id']==b['time_block_id']),key=lambda s:s['pressure_coordinate'])
        brackets=[]
        for left,right in zip(cases,cases[1:]):
            a=_binary_feedback(training_outcomes.get(left['id'],{}));z=_binary_feedback(training_outcomes.get(right['id'],{}))
            if a is not None and z is not None and a!=z:brackets.append((left,right))
        u=None;ids=[];reason='no_known_bracket_space_fill'
        for left,right in brackets:
            low,high=left['pressure_coordinate'],right['pressure_coordinate']
            for frac in (.5,.25,.75,.125,.875):
                candidate=low+(high-low)*frac
                if all(abs(candidate-v)>1e-12 for v in used[b['time_block_id']]):u=candidate;ids=[left['id'],right['id']];reason='observed_train_opposite_outcome_bracket';break
            if u is not None:break
        if u is None:
            pool=[(j+.5)/24 for j in range(24)];_rng(proposal['sampling_seed'],'adaptive_space_fill',slot['id']).shuffle(pool)
            u=next(x for x in pool if all(abs(x-v)>1e-12 for v in used[b['time_block_id']]))
        used[b['time_block_id']].add(u);s=_spec(b,proposal['sampling_seed'],u,slot['id'],'train_only_adaptive')
        s.update(adaptive_reason=reason,adaptive_feedback_scenario_ids=ids,adaptive_slot=copy.deepcopy(slot),
            adaptive_feedback_evidence={sid:copy.deepcopy(training_outcomes[sid]) for sid in ids})
        out.append(s)
    return out

def manifest_components(proposal,scenarios):
    validate_proposal(proposal);blocks={b['time_block_id']:b for b in proposal['blocks']};fixed={s['id']:s for s in proposal['fixed_scenarios']};slots={s['id']:s for s in proposal['adaptive_slots']}
    if len({s['id'] for s in scenarios})!=len(scenarios):raise ValueError('Duplicate registered specification')
    splits={};roots={}
    for s in scenarios:
        b=blocks.get(s['time_block_id'])
        if b is None or s['experiment_role']!=b['role']:raise ValueError('Family/support role changed')
        if s['id'] in fixed:
            if s!=fixed[s['id']]:raise ValueError('Fixed preregistered specification changed')
        elif s['id'] in slots:
            if s['experiment_role']!='pilot_train' or s['adaptive_slot']!=slots[s['id']]:raise ValueError('Adaptive slot must retain train-only role')
        else:raise ValueError('Unregistered specification identity')
        if not approved_pressure([s['shock']['e'],s['shock']['c'],s['shock']['h'],s['shock_by_priority'].get('P0',{}).get('e',0)]):raise ValueError('Shock outside approved joint support')
        validate_scenario(s);support=support_interval(s);role=b['role'];root=s['root_family_id']
        if root in roots and roots[root]!=role:raise ValueError('Root family crossed roles')
        roots[root]=role;gid='integrated_background:'+digest([proposal['sampling_seed'],b['time_block_id']])
        splits[s['id']]=dict(group_id=gid,split=role,fit_allowed=role=='pilot_train',experiment_role=role,split_version='integrated.support_roots.v1',
            embargo_minutes_each_side=60,raw_start_minute=support['raw_start_minute'],raw_end_minute=support['raw_end_minute'],time_block_id=b['time_block_id'],purpose='development',is_final_blind=False)
    values=list(splits.values())
    for i,a in enumerate(values):
        for b in values[:i]:
            near=a['raw_start_minute']-60<b['raw_end_minute']+60 and b['raw_start_minute']-60<a['raw_end_minute']+60
            if near and a['group_id']!=b['group_id']:raise ValueError('Overlapping support must be the same background group')
    ordered=sorted(scenarios,key=lambda s:s['id'])
    return dict(splits=splits,options=dict(seed=proposal['initialization_seed'],security_weight=.35,label_horizons=[15,30,60],history_minutes=60,score_start_minute=60,score_end_minute=420,observed_end_minute=480),
        p2b_shards=[dict(id=f'shard_{i//25:02d}',scenario_ids=[s['id'] for s in ordered[i:i+25]]) for i in range(0,len(ordered),25)],
        integrated_data_design=dict(version=VERSION,proposal_sha256=proposal['proposal_sha256'],distribution_id=DISTRIBUTION,role_meanings=ROLE_MEANINGS,
            registered_initial_specifications=300,fixed_fraction=.8,scenarios_in_this_manifest=len(scenarios),same_family_versions_share_roles=True),
        p2b_time_blocks=copy.deepcopy(proposal['blocks']),p2b_support=[dict(scenario_id=s['id'],root_family_id=s['root_family_id'],time_block_id=s['time_block_id'],**support_interval(s)) for s in scenarios],
        p2b_study_distribution=dict(id=DISTRIBUTION,p_reference=None,importance_weight=None,interpretation='Named development coverage, not hospital probability'))
