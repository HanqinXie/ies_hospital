"""Finite v15 metadata; continuous prefixes, sealed support, post-freeze roots.

Only signed historical definitions and input byte inventory are read here.
There is no environment construction, numerical annual load read or outcome
selection. Actual normal histories run inside each registered episode.
"""
from __future__ import annotations

import copy
import json
import math
from collections import Counter
from pathlib import Path

from tools.engineering_model import data_design as old

ROOT=Path(__file__).resolve().parents[2]
digest=old.digest
validate_observation_support=old.validate_observation_support
validate_sealed_support=old.validate_sealed_support
SEALED_DAYS=old.SEALED_DAYS
SCHEMA='hospital_ies.operating_metadata.v15.v1'
ENGINEERING_MODEL_CONTRACT_ID='hospital_ies.operating_response.v15.v1'
ACTION_SCHEMA='operating_response.operating_joint.v15'
PLANNING_INTERFACE='operating_response.ordinary_joint_command.v15'
CORE_CLASSES=('L1','L2','L3','L4b')
COORDINATE_DESIGN_VERSION='operating_response.v15.event_coordinates.v2'
GROWTH_LEVELS=(.25,.75,1.2)
FAULT_DURATIONS_MINUTES=(30.,60.,180.,300.)
SHOCK_DURATIONS_MINUTES=(30.,60.,90.,270.)
RESOURCE_LIMITS=dict(max_actual_attempts=1200,max_experiment_wall_hours=12,
    ceilings_not_quotas=True,internal_provisional_limits=True,
    revision_requires_recorded_evidence=True)
NORMAL_WINDOWS=((10,24),(40,24),(70,48),(100,24),(130,24),(160,48),
                (190,24),(220,24),(250,48),(280,24),(310,24),(340,48))
_FAULTS=('GRID','GT_1','EC_1','HRSG_1','GRID','BOILER_1',
         'ABS','GT_2','GRID','TOWER','HRSG_2','EC_3')
_CLOCKS=(0.,180.,45.,360.,15.,180.,45.,360.,0.,180.,15.,360.)
_CARRIERS=('e','h','c','mixed','c','e','h','mixed','e','c','mixed','h')
_V14=Path('outputs/v14_engineering_model_20261017_run01/experiments')


def _finish(value):
    value=copy.deepcopy(value);value.pop('signature',None)
    value.update(schema=SCHEMA,study_name='hospital_ies_operating_response_v15',
        strict_final_gate='not_verified',original_final12_evaluated=False,
        original_final_release_authorized=False,sealed_final_days=list(SEALED_DAYS),
        new_independent_background_count=0,hospital_count=1,source_year_count=1,
        statistical_iid_claim=False,source_load_values_read=False,
        raw_or_result_files_read=False,physical_episodes_started=0,
        resource_limits=copy.deepcopy(RESOURCE_LIMITS))
    value['signature']=digest(value)
    return value


def _role(scene,confirmation=False):
    scene.update(experiment_role='confirmation' if confirmation else 'development',
        selection_allowed=not confirmation,requires_policy_freeze=confirmation,
        fit_allowed=False,is_final_blind=False,purpose='v15_frozen_evaluation' if confirmation else 'v15_development',
        sampling_cohort='v15_frozen_evaluation' if confirmation else 'v15_development',
        analysis_cohort='v15_frozen_evaluation' if confirmation else 'v15_development',
        sampling_role='post_freeze_new_coordinates' if confirmation else 'registered_engineering_development',
        new_independent_background=False,old_results_read_only=True,
        demand_background='same_hospital_annual_source_no_unseen_background_claim')
    return scene


def _base(day,hours,*,confirmation=False):
    root_id=('v15_final_root:' if confirmation else 'v15_dev_root:')+digest([day,'calendar_support'])[:24]
    scene=dict(start_hour=day*24+1,source_day_index=day,season_quarter=1+min(3,day//91),
        duration_steps=int(hours*4),step_minutes=15.,pre_event_warmup_steps=6,
        initialization_seed=20261017,automatic_fast_shedding=False,
        grid_limit_kW=850.,pre_event_grid_limit_kW=850.,response_delay_minutes=15.,
        root_family_id=root_id,family_id=root_id,parent_template_id=root_id,
        parent_instance_id=root_id,design_family=root_id,
        base_state_id='v15_initial:'+digest([day,20261017,850.]),
        event_schema_version='p2a.events.v1',sample_schema_version=SCHEMA,
        distribution_id='operating_response.v15.finite_mechanism_coverage.q_design',
        sampling_method='calendar_identity_and_disjoint_support_only',
        sampling_weight_is_failure_probability=False,p_reference=None,q_design_density=None,importance_weight=None,
        history_interval_minutes=[0.,60.],score_interval_minutes=[60.,420.],tail_interval_minutes=[420.,600.],
        label_horizons_minutes=[15,30,60,120],
        prehistory_mode='same_continuous_episode_no_reset',
        initialization_semantics='Repeated first-hour algebraic initialization is not actual prehistory',
        state_inheritance='Actual uninterrupted GT ages, fuel, temperatures, source/contact ledgers and command queues',
        stage=1,event_class='L1',analysis_class='N2',enable_propagation=False,
        faults={},shock={},shock_by_priority={},exogenous_events=[],
        first_event_minute=None,normal_history_minutes=hours*60,
        initial_binding_status='not_observed; classify from actual prefix, never from success',
        actual_p0_load_multiplier={'e':1.,'h':1.,'c':1.})
    return _role(scene,confirmation)


def _seal_scene(scene):
    scene['full_source_support']=validate_observation_support(scene)
    scene['time_block_id']='v15_support:'+digest(scene['full_source_support'])[:24]
    scene['exogenous_stream_id']='v15_exogenous:'+digest([scene['root_family_id'],scene['exogenous_events']])
    scene['physical_scenario_signature']=digest({k:scene.get(k) for k in old.PHYSICAL_FIELDS})
    identity=[scene['physical_scenario_signature'],scene['experiment_role']]
    if scene.get('coordinate_design_version'):identity.append(scene['coordinate_design_version'])
    scene['id']='v15_'+scene['analysis_class']+'_'+digest(identity)[:24]
    scene['matched_group_id']=scene['design_cell_id']='v15_cell:'+digest([scene['root_family_id'],scene['analysis_class']])[:24]
    validate_sealed_support(scene)
    return scene


def _panel(name,scenes,*,bindings=None,freeze_signature=None):
    scenes=copy.deepcopy(list(scenes))
    if not scenes or len({s['id'] for s in scenes})!=len(scenes):
        raise ValueError('Nonempty unique scenarios required')
    roots=[]
    for rid in sorted({s['root_family_id'] for s in scenes}):
        members=[s for s in scenes if s['root_family_id']==rid]
        first=min(s['full_source_support']['raw_start_minute'] for s in members)
        last=max(s['full_source_support']['raw_end_minute'] for s in members)
        roots.append(dict(root_family_id=rid,start_hour=members[0]['start_hour'],
            source_day_index=members[0]['source_day_index'],season_quarter=members[0]['season_quarter'],
            raw_start_minute=first,raw_end_minute=last,
            embargo_start_minute=first-60,embargo_end_minute=last+60,
            new_independent_background=False))
    result=dict(panel=name,scenarios=scenes,roots=roots,
        analysis_class_counts=dict(Counter(s['analysis_class'] for s in scenes)),
        source_metadata_bindings=copy.deepcopy(bindings or {}),freeze_signature=freeze_signature,
        incident_denominator_classes=list(CORE_CLASSES),separately_reported_classes=['N2','L4a'],
        scoring_windows={'primary':[60.,420.],'full':[0.,600.]},
        additional_scoring_windows='registered_full [0,actual requested end); prefix and explicitly named event windows',
        independence='New roots/coordinates on one hospital-year; no iid or never-seen demand claim')
    versions={s['coordinate_design_version'] for s in scenes if s.get('coordinate_design_version')}
    if versions:
        if len(versions)!=1:raise ValueError('Do not mix incident coordinate recipes in a proposal')
        result.update(coordinate_design_version=next(iter(versions)),
            coordinate_recipe=dict(growth_cycle=list(GROWTH_LEVELS),fault_duration_cycle_minutes=list(FAULT_DURATIONS_MINUTES),
                shock_duration_cycle_minutes=list(SHOCK_DURATIONS_MINUTES),growth_period=3,duration_period=4,
                growth_is_not_bound_to_quarter=True,fault_and_shock_are_separate_registered_axes=True,
                complete_duration_factorial_claim=False,selection_uses_outcomes=False,
                hold_declarations_are_not_service_guarantees=True,current_static_copy_support_hours=2.))
    return _finish(result)


def normal_proposal(root=ROOT):
    del root
    return _panel('development_normal',[_seal_scene(_base(d,h)) for d,h in NORMAL_WINDOWS])


def _component_relations(intensity,fault,demand,device,fault_duration,shock_duration):
    """Design labels only; they neither solve nor guarantee interval service."""
    demand_relation={};duration_relation={};combined={}
    initial_supported=(not fault or device=='GRID' or device.startswith(('GT_','HRSG_')))
    for profile,multiplier,hold in (('K0',1.,15.),('K1',1.5,30.),('K2',2.,60.)):
        growth_inside=not demand or 1.+intensity<=multiplier+1e-12
        demand_relation[profile]=('baseline' if not demand else 'within_demand_component' if growth_inside else 'outside')
        durations=[v for active,v in ((fault,fault_duration),(demand,shock_duration)) if active]
        maximum=max(durations,default=0.)
        duration_relation[profile]=dict(declared_hold_minutes=hold,
            fault_duration_relation=('not_applicable' if not fault else 'within_declared_hold' if fault_duration<=hold else 'outside_declared_hold'),
            shock_duration_relation=('not_applicable' if not demand else 'within_declared_hold' if shock_duration<=hold else 'outside_declared_hold'),
            maximum_active_event_duration_minutes=maximum,
            combined_active_duration_relation='within_declared_hold' if maximum<=hold else 'outside_declared_hold')
        combined[profile]=('components_within_joint_delivery_not_guaranteed'
            if growth_inside and maximum<=hold and initial_supported else 'outside_at_least_one_declared_component')
    return demand_relation,duration_relation,combined


def _incident_scenes(windows,*,confirmation=False):
    scenes=[]
    for i,(day,_) in enumerate(windows):
        clock=_CLOCKS[i%len(_CLOCKS)]
        if confirmation:clock+=3.75  # Coordinates registered only after explicit freeze.
        carrier=_CARRIERS[i%len(_CARRIERS)]
        intensity=GROWTH_LEVELS[i%len(GROWTH_LEVELS)]
        fault_duration=FAULT_DURATIONS_MINUTES[i%len(FAULT_DURATIONS_MINUTES)]
        shock_duration=SHOCK_DURATIONS_MINUTES[i%len(SHOCK_DURATIONS_MINUTES)]
        shock={k:intensity for k in ('e','h','c')} if carrier=='mixed' else {carrier:intensity}
        device=_FAULTS[i%len(_FAULTS)]
        for cls in CORE_CLASSES:
            s=_base(day,math.ceil((clock+630.)/15.)/4.,confirmation=confirmation)
            fault=cls!='L2';demand=cls in ('L2','L4b')
            # Deliberately rotate simultaneous/offset demand independently of quarter.
            shock_clock=clock+(15. if cls=='L4b' and i%3==1 else 0.)
            first=min(clock if fault else shock_clock,shock_clock if demand else clock)
            demand_relation,duration_relation,combined=_component_relations(
                intensity,fault,demand,device,fault_duration,shock_duration)
            s.update(analysis_class=cls,event_class=cls,stage={'L1':1,'L2':2,'L3':3,'L4b':4}[cls],
                enable_propagation=cls in ('L3','L4b'),faults={device:0.} if fault else {},
                shock=copy.deepcopy(shock) if demand else {},
                fault_start_minute=clock,fault_duration_minutes=fault_duration,
                shock_start_minute=shock_clock,shock_duration_minutes=shock_duration,
                first_event_minute=first,normal_history_minutes=first,
                takeover_role='early_takeover' if first<=45. else 'continuous_normal_history',
                response_delay_minutes=(0.,15.,30.)[(i+1)%3],
                demand_carrier_design=carrier if demand else 'none',
                demand_pressure_design='growth_'+str(round(intensity*100))+'pct' if demand else 'none',
                coordinate_design_version=COORDINATE_DESIGN_VERSION,
                preparation_demand_relation_by_domain=demand_relation,
                duration_relation_by_domain=duration_relation,
                preparation_combined_domain_relation_by_domain=combined,
                preparation_hold_declaration_only=True,implemented_static_copy_support_hours=2.,
                all_interval_service_guarantee=False,
                preparation_full_domain_status='Declared component labels only; joint delivery and all-interval service not_guaranteed',
                binding_design='retain actual bound/unbound cases; EC_3 includes unused-resource negative candidate')
            events=[]
            if fault:events.append(dict(origin='exogenous',kind='device_fault',device=device,availability=0.,
                start_minute=clock,end_minute=clock+s['fault_duration_minutes']))
            if demand:events.append(dict(origin='exogenous',kind='demand_shock',shock=copy.deepcopy(shock),shock_by_priority={},
                start_minute=shock_clock,end_minute=shock_clock+s['shock_duration_minutes']))
            s['exogenous_events']=events
            s['actual_p0_load_multiplier']={k:1.+float(s['shock'].get(k,0.)) for k in ('e','h','c')}
            scenes.append(_seal_scene(s))
    return scenes


def build_development(root=ROOT):
    del root
    return _panel('development_incidents_v2',_incident_scenes(NORMAL_WINDOWS))


def known_counterexamples(root=ROOT):
    root=Path(root);scenes=[];bindings={}
    sources=(('early45','confirmation_r6_M14_engineering_boundary_early45_day51',None),
             ('late85','confirmation_r6_M14_engineering_boundary_late85_day51',None),
             ('day342_recovery','historical_recovery_M0',342),
             ('day47_recovery','historical_recovery_M0',47),
             ('day51_original','revision_r6_M14_engineering_unknown_day51',None))
    for role,name,day in sources:
        relative=_V14/name/'manifest.json';path=root/relative;manifest=old.legacy._signed(path)
        bindings[relative.as_posix()]={'sha256':old._sha(path),'signature':manifest['signature']}
        candidates=[s for s in manifest['scenarios'] if day is None or s['source_day_index']==day]
        if len(candidates)!=1:raise ValueError('Ambiguous immutable known counterexample')
        s=copy.deepcopy(candidates[0]);source_id=s['id'];_role(s)
        first=min([float(s[k]) for k in ('fault_start_minute','shock_start_minute') if k in s],default=0.)
        s.update(id='v15_known_'+role+'_'+digest(source_id)[:16],source_v14_scenario_id=source_id,
            known_case_role=role,previously_exposed_v14=True,source_outcomes_used_for_selection=True,
            analysis_cohort='v15_known_counterexamples',prehistory_mode='same_continuous_episode_no_reset',
            first_event_minute=first,normal_history_minutes=first,
            source_physical_scenario_signature=s.get('physical_scenario_signature'))
        validate_sealed_support(s);scenes.append(s)
    return _panel('known_counterexamples',scenes,bindings=bindings)


def allowed_hours(proposal):
    result=set()
    for scene in proposal['scenarios']:
        support=validate_sealed_support(scene)
        result.update(range(int(support['raw_start_minute']//60)+1,int(support['raw_end_minute']//60)+1))
    return result


def final_registration(freeze,development,*,seed=20261017,root=ROOT):
    del root
    if (not isinstance(freeze,dict) or freeze.get('frozen') is not True
            or not isinstance(freeze.get('source_sha256'),dict) or not freeze['source_sha256']
            or not isinstance(freeze.get('model_configs'),dict) or not freeze['model_configs']
            or not isinstance(freeze.get('policy_specs'),list) or not freeze['policy_specs']):
        raise ValueError('Freeze with source_sha256, model_configs and policy_specs required before final coordinates')
    json.dumps(freeze,allow_nan=False);signature=digest(freeze)
    exclusion=[(r['embargo_start_minute'],r['embargo_end_minute']) for r in normal_proposal()['roots']]
    exclusion.extend((r['embargo_start_minute'],r['embargo_end_minute']) for r in development['roots'])
    selected=[]
    for quarter in range(1,5):
        days=[d for d in range(1,362) if 1+min(3,d//91)==quarter]
        days.sort(key=lambda d:digest([seed,'post_freeze_calendar_coordinates',d]))
        chosen=0
        for day in days:
            hours=24 if chosen==0 else 48
            candidate=_base(day,hours,confirmation=True)
            try:candidate=_seal_scene(candidate)
            except ValueError:continue
            sup=candidate['full_source_support'];a=sup['raw_start_minute']-60;b=sup['raw_end_minute']+60
            if any(a<y and x<b for x,y in exclusion):continue
            selected.append((day,hours));exclusion.append((a,b));chosen+=1
            if chosen==2:break
        if chosen!=2:raise ValueError('Insufficient distinct nonsealed post-freeze annual support')
    normal=_panel('frozen_normal',[_seal_scene(_base(d,h,confirmation=True)) for d,h in selected],freeze_signature=signature)
    incident=_panel('frozen_incidents',_incident_scenes(selected,confirmation=True),freeze_signature=signature)
    return {'normal':normal,'incidents':incident}


def build_manifest(proposal,source_sha256,model_id,model_config,root=ROOT,policies=None):
    if model_id not in ('M0','M14_engineering','M15_local_response'):
        raise ValueError('Unsupported explicitly versioned execution model')
    if not isinstance(model_config,dict):raise ValueError('Explicit model configuration mapping required')
    json.dumps(model_config,allow_nan=False)
    for scene in proposal['scenarios']:validate_sealed_support(scene)
    options={} if policies is None else {'policies':tuple(policies)}
    manifest=old.legacy.build_manifest(proposal['scenarios'],proposal['roots'],source_sha256=source_sha256,
        planning_interface_id=PLANNING_INTERFACE,action_schema=ACTION_SCHEMA,workspace_root=root,
        execution_model_id='M0',**options)
    manifest.pop('signature',None)
    manifest.update(manifest_version=SCHEMA+'.manifest',execution_model_id=model_id,
        engineering_model_contract_id=ENGINEERING_MODEL_CONTRACT_ID,planning_interface_id=PLANNING_INTERFACE,
        schemas=dict(manifest['schemas'],action=ACTION_SCHEMA),model_config=copy.deepcopy(model_config),
        model_config_signature=digest(model_config),source_proposal_signature=proposal['signature'],
        panel=proposal['panel'],source_sha256=copy.deepcopy(source_sha256),freeze_signature=proposal.get('freeze_signature'))
    manifest.update(legacy_execution_contract_id=manifest['execution_contract_id'],
        legacy_assumption_set_id=manifest['assumption_set_id'],legacy_simulator_hash=manifest['simulator_hash'])
    manifest['simulator_hash']=digest(source_sha256)
    suffix='__v15_'+digest([model_id,model_config,ENGINEERING_MODEL_CONTRACT_ID])[:16]
    manifest['execution_contract_id']+=suffix;manifest['assumption_set_id']+=suffix
    return _finish(manifest)
