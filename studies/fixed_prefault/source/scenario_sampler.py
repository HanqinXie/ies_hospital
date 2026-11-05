"""Literature-inspired, deterministic four-stage contingency design for a DOE hospital IES.

IISE/N-1 coverage motivates complete unit enumeration (Lei et al., Applied Energy 2018).
Cross-system event-tree *branches* motivate multiple L3 initiating events (Liu et al., 2023).
L4 stratification and Sobol low-discrepancy coverage are OUR experiment design,
not claims that either reference prescribed these precise samples.

No sampling weights represent actual component failure frequencies. A state-dependent
cascade may or may not occur; L3/L4 are propagation-ENABLED, not trip-guaranteed.
"""
from __future__ import annotations
import math
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/"src"))
import numpy as np
import pandas as pd
from scipy.stats import qmc
from hospital_ies.resilience import empty_faults, validate_scenario

# All modeled individually faultable devices. TOWER is the shared common-mode failure
# and TOWER_i is a 25% bank derating (not a validated pipe connection graph).
SINGLE_DEVICES=tuple(k for k in empty_faults() if k!='TOWER')
COMMON_MODE_DEVICES=('TOWER',)
INITIATORS={
    'electric':('GT_1','GT_2','GT_3','GRID'),
    'steam':('HRSG_1','HRSG_2','HRSG_3','BOILER_1','BOILER_2','BOILER_3'),
    'cooling':('EC_1','EC_2','EC_3','ABS','TOWER_1','TOWER_2','TOWER_3','TOWER_4','TOWER'),
    'backup':('DIESEL',),
}
# The current protective trip model acts on GTs only: non-GT initiators may
# cause lost service or coupled output derating, but not fabricated trips.
L4_STRATA=('ordinary','elevated','boundary_candidate','extreme_ood')
L4_WEIGHTS=(0.35,0.30,0.25,0.10)
SAMPLE_SCHEMA_VERSION='p2a.sampling.v1'
EVENT_SCHEMA_VERSION='p2a.events.v1'
DISTRIBUTION_ID='q_design.independent_stratified.v1'
EVENT_CLASSES=('L1','L2','L3','L4a','L4b')
SEVERITY_LAYERS={'full_outage':0.,'severe':.25,'moderate':.5,'mild':.75}


def _digest(value)->str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _rng(seed:int,*keys)->np.random.Generator:
    """Stable, purpose-specific streams; class order and policies consume no shared RNG."""
    return np.random.default_rng(int(_digest([int(seed),*keys])[:32],16))


def _balanced_choices(n:int,choices,rng:np.random.Generator)->list:
    """Randomize each complete block independently, retaining finite coverage."""
    out=[]
    while len(out)<n:
        order=list(choices)
        rng.shuffle(order)
        out.extend(order)
    return out[:n]


def _sobol(n:int,seed:int,*keys)->np.ndarray:
    if n==0:return np.empty((0,8))
    q=qmc.Sobol(d=8,scramble=True,seed=int(_rng(seed,*keys).integers(0,2**32)))
    return q.random_base2(m=int(math.ceil(math.log2(n))))[:n]


def _fault_design(n:int,steps:int,seed:int,event_class:str)->list[dict]:
    """Enumerate real devices; severity/duration/background/delay have separate streams."""
    devices=list(SINGLE_DEVICES+COMMON_MODE_DEVICES)
    _rng(seed,event_class,'device_order').shuffle(devices)
    visits=Counter(devices[i%len(devices)] for i in range(n))
    designs={}
    for dev,count in visits.items():
        minimum=max(2,steps//2) if event_class=='L3' else 2
        designs[dev]={
            'severity':_balanced_choices(count,tuple(SEVERITY_LAYERS),_rng(seed,event_class,dev,'severity')),
            'duration':_balanced_choices(count,range(minimum,steps+1),_rng(seed,event_class,dev,'duration')),
            'delay':_balanced_choices(count,range(4),_rng(seed,event_class,dev,'delay')),
            'background':_balanced_choices(count,('peak','annual','annual'),_rng(seed,event_class,dev,'background')),
            'start':_balanced_choices(count,(0,2) if steps>=4 else (0,),_rng(seed,event_class,dev,'start')),
        }
    occurrence=Counter();out=[]
    for i in range(n):
        dev=devices[i%len(devices)];j=occurrence[dev];occurrence[dev]+=1
        out.append(dict(device=dev,**{k:v[j] for k,v in designs[dev].items()}))
    return out


def _weighted_strata(n:int,rng:np.random.Generator)->list[str]:
    # Exactly approximately target proportions, randomized in ordering.
    raw=np.asarray(L4_WEIGHTS)*n
    counts=np.floor(raw).astype(int)
    for j in np.argsort(-(raw-counts))[:n-sum(counts)]:counts[j]+=1
    out=[s for s,k in zip(L4_STRATA,counts) for _ in range(k)]
    rng.shuffle(out)
    return out


def _hour_candidates(hours:pd.DataFrame,steps:int):
    latest=min(int(hours.hour_of_year.max()),8760-int(math.ceil(steps/4))-1)
    if latest<1:raise ValueError('Scenario too long for 8760 input')
    subset=hours.loc[hours.hour_of_year<=latest]
    # Intentional seasonal/load diversity: annual high demand + unconstrained hours.
    combined=subset.P_e_nonplant_kW+subset.P_e_unresolved_kW+subset.Q_c_demand_kW/4
    peaks=subset.loc[combined>=combined.quantile(0.85),'hour_of_year'].to_numpy()
    return subset.hour_of_year.to_numpy(),peaks


def sample_scenarios(hours:pd.DataFrame,count_per_stage:int,steps:int,seed:int,
                     suite_id:str='design',*,event_classes:tuple[str,...]|None=None)->list[dict]:
    """Make stress specifications only. Default remains four stages with n rows each.

    Explicit event_classes selects versioned L1/L2/L3/L4a/L4b, with n rows per
    class. Counts and stratification describe q_design, never hospital frequencies.
    """
    if count_per_stage<1 or steps<2:raise ValueError('count_per_stage >=1, steps >=2 required')
    versioned=event_classes is not None
    classes=tuple(event_classes) if versioned else ('L1','L2','L3','L4')
    if not classes or len(set(classes))!=len(classes) or (versioned and any(c not in EVENT_CLASSES for c in classes)):
        raise ValueError('event_classes must contain distinct supported event classes')
    all_hours,peak_hours=_hour_candidates(hours,steps)
    results=[]
    for event_class in classes:
        stage=int(event_class[1])
        faults=_fault_design(count_per_stage,steps,seed,event_class) if event_class in ('L1','L3','L4b') else None
        strata=_weighted_strata(count_per_stage,_rng(seed,event_class,'strata'))
        modes=_balanced_choices(count_per_stage,('e','h','c','mixed'),_rng(seed,event_class,'modes'))
        groups=modes if stage==2 else strata
        points={group:iter(_sobol(groups.count(group),seed,event_class,'amplitudes',group)) for group in set(groups)}
        backgrounds=_balanced_choices(count_per_stage,('peak','annual','annual'),_rng(seed,event_class,'background'))
        for i in range(count_per_stage):
            design=faults[i] if faults else None
            background=design['background'] if design else backgrounds[i]
            h=int(_rng(seed,event_class,i,'hour').choice(peak_hours if background=='peak' else all_hours))
            e={'id':f'{suite_id}_{event_class}_{i:04d}_h{h}',
               'stage':stage,'event_class':event_class,
               'event_schema_version':EVENT_SCHEMA_VERSION if versioned else 'legacy.events.v1',
               'sample_schema_version':SAMPLE_SCHEMA_VERSION,
               'start_hour':h,'duration_steps':steps,'step_minutes':15,
               'enable_propagation':stage>=3,'response_delay_steps':0,
               'design_family':f'{suite_id}_{event_class}_{i:04d}',
               'sampling_method':'independent_conditional_sobol_and_device_strata',
               'sampling_weight_is_failure_probability':False,
               'sampling_role':'development_stress_design','distribution_id':DISTRIBUTION_ID,
               'p_reference':None,'q_design_density':None,'importance_weight':None,
               'demand_background':background,
               'parent_template_id':f'p2a:{int(seed)}:{event_class}:{i:04d}',
               'exogenous_stream_id':_digest([DISTRIBUTION_ID,int(seed),event_class,i]),
               'base_state_id':_digest({'hour':h,'steps':steps,'step_minutes':15}),
               'exogenous_events':[]}
            if design:
                dev=design['device'];severity=design['severity']
                e.update(faults={dev:SEVERITY_LAYERS[severity]},initiating_device=dev,
                    fault_severity_layer=severity,fault_duration_steps=design['duration'],
                    fault_start_step=design['start'] if stage>=3 else 0,
                    contingency_class='common_mode' if dev=='TOWER' else 'N-1')
                if stage>=3:
                    e.update(pre_event_warmup_steps=6,response_delay_steps=design['delay'],
                        grid_limit_kW=float(_rng(seed,event_class,i,'grid').choice([160,250,350,500,850])),
                        modeled_secondary_protection='GT_overload_only')
            if stage==2:
                mode=modes[i];u=next(points[mode])
                shock={'e':round(float(1.8*u[0]),4),'h':round(float(u[1]),4),'c':round(float(1.4*u[2]),4)}
                if mode!='mixed':shock={k:(v if k==mode else 0.) for k,v in shock.items()}
                e.update(shock=shock,shock_mode=mode)
            elif stage==4:
                stratum=strata[i];u=next(points[stratum])
                if stratum=='ordinary':
                    el,heat,cool,p0=0.5*u[0],0.25*u[1],0.5*u[2],0.
                elif stratum=='elevated':
                    el,heat,cool,p0=0.4+0.9*u[0],0.1+0.4*u[1],0.3+0.8*u[2],0.2*u[3]
                elif stratum=='boundary_candidate':
                    el,heat,cool,p0=0.8+1.3*u[0],0.05+0.5*u[1],0.45+0.8*u[2],0.2+0.7*u[3]
                else:
                    el,heat,cool,p0=1.4+1.4*u[0],0.2+0.65*u[1],0.6+0.8*u[2],0.4+1.1*u[3]
                e.update(shock={'e':round(float(el),4),'h':round(float(heat),4),'c':round(float(cool),4)},
                    response_delay_steps=design['delay'] if design else min(3,int(u[4]*4)),
                    grid_limit_kW=float([120.,160.,250.,350.,500.,850.][min(5,int(u[5]*6))]),
                    pre_event_warmup_steps=6,
                    shock_start_step=2 if steps>=4 and _rng(seed,event_class,i,'shock_start').random()<.35 else 0,
                    stratum=stratum,sampling_cohort='stress_ood' if stratum=='extreme_ood' else 'main',
                    shock_by_priority={'P0':{'e':round(float(p0),4)}} if p0>0 else {})
            if stage>=3:
                auto=_rng(seed,event_class,i,'automatic_response')
                e['automatic_fast_shedding']=bool(auto.choice([True,False,False]))
                e['precontrol_sheddable_fraction_by_priority']={
                    'P3':float(auto.choice([.1,.25,.4,.6])),
                    'P2':float(auto.choice([0.,.1,.15])),
                    'P1':float(auto.choice([0.,.03,.05]))}
            if versioned:
                e['response_delay_minutes']=15*e.pop('response_delay_steps')
                for kind in ('fault','shock'):
                    if not e.get('faults' if kind=='fault' else 'shock'):continue
                    e[kind+'_start_minute']=15*e.pop(kind+'_start_step',0)
                    if kind=='shock':
                        duration=int(_rng(seed,event_class,i,'shock_duration').integers(2,steps+1))
                    else:
                        duration=e.pop('fault_duration_steps')
                    e[kind+'_duration_minutes']=15*duration
                    event={'origin':'exogenous','kind':'load_shock' if kind=='shock' else
                           ('common_mode_fault' if e['initiating_device']=='TOWER' else 'device_fault'),
                           'start_minute':e[kind+'_start_minute'],
                           'end_minute':e[kind+'_start_minute']+e[kind+'_duration_minutes']}
                    if kind=='fault':event.update(device=e['initiating_device'],availability=next(iter(e['faults'].values())))
                    else:event.update(shock=dict(e['shock']),shock_by_priority=e.get('shock_by_priority',{}))
                    e['exogenous_events'].append(event)
            e['actual_p0_load_multiplier']={k:(1+e.get('shock',{}).get(k,0.))*(1+e.get('shock_by_priority',{}).get('P0',{}).get(k,0.))
                for k in ('e','h','c')}
            # A family groups policy/parameter variants of the same exogenous parent.
            e['family_id']=_digest([e['parent_template_id'],e['base_state_id'],e.get('faults'),
                e.get('shock'),e.get('shock_by_priority'),e.get('exogenous_events'),e.get('stratum')])
            validate_scenario(e)
            results.append(e)
    return results


def coverage_summary(scenarios:list[dict])->dict:
    stages=Counter(int(s['stage']) for s in scenarios)
    l1=Counter(next(iter(s.get('faults',{})),None) for s in scenarios if s['stage']==1)
    l3=Counter(next(iter(s.get('faults',{})),None) for s in scenarios if s['stage']==3)
    l4=Counter(s.get('stratum','') for s in scenarios if s['stage']==4)
    templates=Counter((round(s.get('shock',{}).get('e',0),3),round(s.get('shock',{}).get('c',0),3),
                      round(s.get('shock_by_priority',{}).get('P0',{}).get('e',0),3),
                      s.get('grid_limit_kW')) for s in scenarios if s['stage']==4)
    class_of=lambda s:s.get('event_class',f"L{s['stage']}")
    classes=EVENT_CLASSES+(('L4',) if any(class_of(s)=='L4' for s in scenarios) else ())
    coverage=[];conditions=[]
    for event_class in classes:
        for dev in SINGLE_DEVICES+COMMON_MODE_DEVICES:
            group=[s for s in scenarios if class_of(s)==event_class and dev in s.get('faults',{})]
            supported=event_class in ('L1','L3','L4b')
            for severity,availability in SEVERITY_LAYERS.items():
                count=sum(float(s['faults'][dev])==availability for s in group)
                coverage.append(dict(device=dev,severity_layer=severity,availability=availability,
                    event_class=event_class,supported=supported,count=count,
                    status=('covered' if count else 'unsampled') if supported else 'unsupported'))
            conditions.append(dict(device=dev,event_class=event_class,supported=supported,count=len(group),
                fault_duration_counts=dict(Counter(float(s.get('fault_duration_minutes',
                    s.get('fault_duration_steps',s['duration_steps'])*s.get('step_minutes',15))) for s in group)),
                background_hour_counts=dict(Counter(s['start_hour'] for s in group)),
                demand_background_counts=dict(Counter(s.get('demand_background','unrecorded') for s in group)),
                response_delay_minutes_counts=dict(Counter(float(s.get('response_delay_minutes',
                    s.get('response_delay_steps',0)*s.get('step_minutes',15))) for s in group))))
    carrier_coverage=[]
    for mode in ('e','h','c','mixed'):
        for carrier,upper in (('e',1.8),('h',1.),('c',1.4)):
            if mode not in (carrier,'mixed'):continue
            values=[s['shock'][carrier] for s in scenarios if s['stage']==2 and s.get('shock_mode')==mode]
            carrier_coverage.append(dict(mode=mode,carrier=carrier,interval=[0.,upper],count=len(values),
                minimum=min(values,default=None),maximum=max(values,default=None),
                quartile_counts={str(q):sum(min(3,int(4*v/upper))==q for v in values) for q in range(4)}))
    return {'stage_counts':dict(stages),'model_devices':list(SINGLE_DEVICES+COMMON_MODE_DEVICES),
            'L1_covered_devices':dict(l1),'L1_missing_devices':sorted(set(SINGLE_DEVICES+COMMON_MODE_DEVICES)-set(l1)),
            'L3_initiators':dict(l3),'L3_missing_initiators':sorted(set(l3_order_all())-set(l3)),
            'L4_strata':dict(l4),'L4_max_exact_template_count':max(templates.values(),default=0),
            'sample_schema_version':SAMPLE_SCHEMA_VERSION,'distribution_id':DISTRIBUTION_ID,
            'event_class_counts':dict(Counter(class_of(s) for s in scenarios)),
            'device_severity_event_class':coverage,'device_event_condition_coverage':conditions,
            'conditional_carrier_coverage':carrier_coverage,
            'q_design':{'kind':'fixed_stress_counts','event_class_counts':dict(Counter(class_of(s) for s in scenarios)),
                        'L4_stratum_counts':dict(l4),'L4_target_design_fractions':dict(zip(L4_STRATA,L4_WEIGHTS))},
            'p_reference':None,'q_design_density':None,'importance_weight':None,
            'sampling_design_not_failure_probabilities':True,
            'propagation_model_limit':'Only GT overload protection can trigger secondary physical trips; other energy losses are coupled effects.'}

def l3_order_all()->tuple[str,...]:
    return tuple(d for ds in INITIATORS.values() for d in ds)
