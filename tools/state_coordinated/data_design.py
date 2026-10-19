"""V17 finite metadata recipes; no annual values, simulation or old raw reads.

Native legacy event labels remain the simulator's validation contract. The new
canonical labels are explicit study metadata, never an input to the policy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from tools.essential_fixes import data_design as previous

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'hospital_ies.state_coordinated_metadata.v17.v1'
STUDY_CONTRACT_ID = 'hospital_ies.state_coordinated_study.v17.v1'
ACTION_SCHEMA = 'state_coordinated.mixed_actions.v17'
PLANNING_INTERFACE = 'state_coordinated.mixed_ordinary_joint_command.v17'
SEALED_DAYS = previous.SEALED_DAYS
CORE_CLASSES = ('L1', 'L2', 'L3', 'L4')
CANONICAL_TO_LEGACY = dict(L1='L1', L2='L3', L3='L2', L4='L4b', N2='N2', L4a='L4a')
LEGACY_TO_CANONICAL = {v:k for k,v in CANONICAL_TO_LEGACY.items()}
RESOURCE_LIMITS = dict(max_actual_attempts=1200, max_experiment_wall_hours=24,
    max_main_dispatch_configurations=3, ceilings_not_quotas=True,
    internal_provisional_limits=True, revision_requires_recorded_evidence=True)
digest = previous.digest
validate_observation_support = previous.validate_observation_support
execution_model_contract_id = previous.execution_model_contract_id
model_contract_id = execution_model_contract_id

# These are V17 development-isolated coordinates, NOT a global unseen claim.
# All are metadata only until the root freezes and registers a proposal.
MAIN_CALENDAR_DAYS = (5, 8, 32, 35, 64, 66, 104, 107, 145, 148,
                      171, 174, 208, 236, 239, 260, 292, 326, 329, 349)
DEVELOPMENT_CALENDAR_DAYS = (10, 40, 51, 70, 100, 160, 190, 220, 250, 280, 310, 315, 342)
V16 = Path('outputs/v16_essential_engineering_20261017_run01')
_TARGETS = ('GRID','GT_1','HRSG_1','EC_2','ABS','TOWER','GRID','GT_2','HRSG_2','EC_1',
            'GRID','GT_3','HRSG_1','EC_2','ABS','TOWER','GRID','GT_1','HRSG_3','EC_3')


def _validation_view(scene):
    from .environment import validate_scenario
    validate_scenario(scene)  # Full native validation plus the one explicit V17 OFF adapter.
    value=copy.deepcopy(scene)
    if (value.get('v17_matched_propagation_ablation') is True and value.get('stage')==4
            and value.get('event_class')=='L4b' and value.get('enable_propagation') is False):
        value['enable_propagation']=True
    return value


def validate_sealed_support(scene):
    """Validate support on a validation-only copy; preserve actual OFF data."""
    return previous.validate_sealed_support(_validation_view(scene))


def allowed_hours(proposal):
    hours=set()
    for scene in proposal['scenarios']:
        support=validate_sealed_support(scene)
        hours.update(range(int(support['raw_start_minute']//60)+1,int(support['raw_end_minute']//60)+1))
    return hours


def validate_panel_isolation(*proposals):
    """Cross-panel full hourly support plus embargo; shared roots within a panel allowed."""
    intervals=[]
    for index,proposal in enumerate(proposals):
        for scene in proposal['scenarios']:
            support=validate_sealed_support(scene)
            a=support['raw_start_minute']-60.;b=support['raw_end_minute']+60.
            for other,x,y in intervals:
                if other!=index and a<y and x<b:
                    raise ValueError('Cross-panel support isolation/embargo overlap')
            intervals.append((index,a,b))
    return dict(isolated=True,panels=len(proposals),full_support_and_60min_embargo=True,
        global_historical_unseen_claim=False)


def _finish(value):
    result = copy.deepcopy(value)
    result.pop('signature', None)
    result.update(schema=SCHEMA, study_name='hospital_ies_state_coordinated_v17',
        study_contract_id=STUDY_CONTRACT_ID, resource_limits=copy.deepcopy(RESOURCE_LIMITS),
        original_final12_evaluated=False, original_final_release_authorized=False,
        sealed_final_days=list(SEALED_DAYS), statistical_iid_claim=False,
        source_load_values_read=False, raw_or_result_files_read=False,
        physical_episodes_started=0, hospital_count=1, source_year_count=1,
        new_independent_background_count=0)
    result['signature'] = digest(result)
    return result


def _base(day, hours, *, experiment_role='development'):
    scene = previous.old._base(int(day), float(hours), confirmation=experiment_role=='confirmation')
    rid = 'v17_calendar:' + digest([day,'same_hospital_year'])[:24]
    scene.update(root_family_id=rid, family_id=rid, parent_template_id=rid,
        parent_instance_id=rid, design_family=rid, experiment_role=experiment_role,
        canonical_class='N2', legacy_class='N2', analysis_class='N2',
        core_four_class_denominator=False, initialization_seed=20261018,
        base_state_id='v17_initial:'+digest([day,20261018,850.]),
        coordinate_design_version='state_coordinated.v17.explicit_coordinates.v1',
        distribution_id='state_coordinated.v17.finite_mechanism_design',
        background_status='previously_exposed_calendar' if day in DEVELOPMENT_CALENDAR_DAYS else 'held_out_from_v17_development',
        historical_exposure_status='known_v16_exposed' if day in DEVELOPMENT_CALENDAR_DAYS else 'not_fully_audited_no_global_unseen_claim',
        source_outcomes_used_for_selection=experiment_role=='development',
        development_background=experiment_role=='development',
        analysis_cohort='v17_'+experiment_role,sampling_cohort='v17_'+experiment_role)
    return scene


def seal_scene(scene):
    value = copy.deepcopy(scene)
    previous_id = value.get('id')
    if previous_id and not str(previous_id).startswith('v17_'):
        value.setdefault('source_scenario_id', previous_id)
    legacy = value.get('legacy_class', value.get('event_class', value.get('analysis_class')))
    if legacy == 'L1' and not value.get('faults') and not value.get('shock') and not value.get('shock_by_priority'):
        legacy = 'N2'
    canonical = value.get('canonical_class', LEGACY_TO_CANONICAL.get(legacy))
    if canonical not in CANONICAL_TO_LEGACY or CANONICAL_TO_LEGACY[canonical] != legacy:
        raise ValueError('Canonical and legacy event classes disagree')
    value.update(canonical_class=canonical, legacy_class=legacy,
        analysis_class=legacy, sample_schema_version=SCHEMA,
        is_final_blind=False, original_final12_evaluated=False, old_results_read_only=True,
        new_independent_background=False, fit_allowed=False)
    confirmation = value.get('experiment_role') == 'confirmation'
    value.update(selection_allowed=not confirmation, requires_policy_freeze=confirmation)
    value.setdefault('experiment_role','development')
    value.setdefault('analysis_cohort','v17_'+value['experiment_role'])
    value.setdefault('sampling_cohort',value['analysis_cohort'])
    value.setdefault('purpose','v17_state_coordinated')
    value.setdefault('core_four_class_denominator',canonical in CORE_CLASSES)
    value.setdefault('prehistory_mode','same_continuous_episode_no_reset')
    value.setdefault('development_background',not confirmation)
    value.setdefault('historical_exposure_status','not_fully_audited_no_global_unseen_claim')
    if value.get('coordinate_design_version')!='state_coordinated.v17.explicit_coordinates.v1':
        value.setdefault('source_coordinate_design_version',value.get('coordinate_design_version'))
    value['coordinate_design_version']='state_coordinated.v17.explicit_coordinates.v1'
    value['actual_p0_load_multiplier'] = {k:(1.+float(value.get('shock',{}).get(k,0.)))*
        (1.+float(value.get('shock_by_priority',{}).get('P0',{}).get(k,0.))) for k in ('e','h','c')}
    value['full_source_support'] = validate_observation_support(value)
    value['time_block_id'] = 'v17_support:'+digest(value['full_source_support'])[:24]
    value['exogenous_stream_id'] = 'v17_exogenous:'+digest([value['root_family_id'],value.get('exogenous_events',[])])[:24]
    value['physical_scenario_signature'] = digest({k:value.get(k) for k in previous.old.old.PHYSICAL_FIELDS})
    value['id'] = 'v17_'+canonical+'_'+digest([value['physical_scenario_signature'],value['experiment_role'],value.get('design_variant')])[:24]
    value['matched_group_id'] = value['design_cell_id'] = 'v17_cell:'+digest([value['root_family_id'],canonical,value.get('design_variant')])[:24]
    validate_sealed_support(value)
    return value


def panel(name, scenes, *, bindings=None, freeze_signature=None):
    scenes = [seal_scene(s) for s in scenes]
    result = previous.old._panel(name,scenes,bindings=bindings,freeze_signature=freeze_signature)
    result.update(canonical_class_counts=dict(Counter(s['canonical_class'] for s in scenes)),
        legacy_class_counts=dict(Counter(s['legacy_class'] for s in scenes)),
        incident_denominator_classes=list(CORE_CLASSES), separately_reported_classes=['N2','L4a','long_outage'],
        label_mapping=copy.deepcopy(LEGACY_TO_CANONICAL),
        native_labels_preserved=True, permitted_propagation_is_not_observed_propagation=True,
        independence='Shared calendar/root families; no iid, no global historical-unseen claim',
        coordinate_recipe=dict(version='state_coordinated.v17.explicit_coordinates.v1',
            all_coordinates_in_scenarios=True, no_source_values_or_outcomes_read=True,
            support_embargo_minutes=60., response_delay_semantics='event-onset blackout ending at common release boundary; L4 native anchor is shock clock',
            total_costs_only_compared_with_same_spec_and_window=True))
    return _finish(result)


def _events(scene, canonical, *, faults, shock, clock, fault_duration, shock_clock, shock_duration):
    s = copy.deepcopy(scene); legacy = CANONICAL_TO_LEGACY[canonical]
    s.update(canonical_class=canonical,legacy_class=legacy,analysis_class=legacy,event_class=legacy,
        stage={'L1':1,'L3':3,'L2':2,'L4b':4,'L4a':4}[legacy],
        faults=copy.deepcopy(faults),shock=copy.deepcopy(shock),shock_by_priority={},
        enable_propagation=legacy in ('L3','L4b','L4a'),
        fault_start_minute=float(clock),fault_duration_minutes=float(fault_duration),
        shock_start_minute=float(shock_clock),shock_duration_minutes=float(shock_duration))
    events=[]; starts=[]
    if faults:
        events.append(dict(origin='exogenous',kind='device_fault',faults=copy.deepcopy(faults),
            start_minute=float(clock),end_minute=float(clock+fault_duration)))
        starts.append(float(clock))
    if shock:
        events.append(dict(origin='exogenous',kind='demand_shock',shock=copy.deepcopy(shock),shock_by_priority={},
            start_minute=float(shock_clock),end_minute=float(shock_clock+shock_duration)))
        starts.append(float(shock_clock))
    s.update(exogenous_events=events,first_event_minute=min(starts),normal_history_minutes=min(starts),
        core_four_class_denominator=canonical in CORE_CLASSES)
    return s


def build_main_design(days=None, *, experiment_role='confirmation', freeze_signature=None):
    days=tuple(MAIN_CALENDAR_DAYS if days is None else days)
    if len(days)!=20 or len(set(days))!=20:
        raise ValueError('Starting main design requires twenty distinct calendar roots')
    if experiment_role=='confirmation' and set(days)&set(DEVELOPMENT_CALENDAR_DAYS):
        raise ValueError('Final V17 comparison calendars must be separate from V17 development')
    scenes=[]
    for i,day in enumerate(days):
        clock=(67.5,82.5,127.5,187.5,277.5)[i%5]
        fd=(30.,60.,180.,300.)[(i//3)%4]; sd=(30.,90.,180.,300.)[(i+1)%4]
        shock_clock=clock+(-17.5,0.,22.5)[i%3]
        intensity=(.25,.5,.75)[i%3]
        carrier=('e','h','c','mixed')[i%4]
        shock={k:intensity for k in (('e','h','c') if carrier=='mixed' else (carrier,))}
        end=max(600.,max(clock+fd,shock_clock+sd,clock+sd)+300.)
        hours=math.ceil(end/15.)/4.
        for canonical in CORE_CLASSES:
            fault=canonical!='L3'; demand=canonical in ('L3','L4')
            s=_events(_base(day,hours,experiment_role=experiment_role),canonical,
                faults={_TARGETS[i]:0.} if fault else {},shock=shock if demand else {},
                clock=clock,fault_duration=fd,shock_clock=shock_clock if canonical=='L4' else clock,shock_duration=sd)
            s.update(design_variant='main_'+canonical+'_'+str(i),response_delay_minutes=(0.,15.,30.)[i%3],
                source_outcomes_used_for_selection=False,core_four_class_denominator=True,
                target_operating_role=('in_service_candidate' if _TARGETS[i] in ('HRSG_1','EC_2') else
                    'standby_candidate' if _TARGETS[i] in ('HRSG_2','HRSG_3','EC_1','EC_3') else 'classify_from_actual_prefix') if fault else 'not_applicable',
                target_service_attribution='actual pre-event source output and bus-level service; no invented source-to-priority allocation',
                demand_carrier_design=carrier if demand else 'none',
                propagation_pair_id='v17_prop_pair:'+digest([day,clock,fd,_TARGETS[i]])[:24] if canonical in ('L1','L2') else None,
                propagation_design='allowed_observe_actual' if s['enable_propagation'] else 'disabled_reference',
                continuous_recovery_tail_minutes=300.)
            scenes.append(s)
    return panel('main_four_class_start80',scenes,freeze_signature=freeze_signature)


def _source_proposal(root, filename):
    relative=V16/'design'/filename; path=Path(root)/relative
    p=previous.old.old.legacy._signed(path)
    return p,{relative.as_posix():dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),signature=p['signature'])}


def build_development_design(root=ROOT):
    source,bindings=_source_proposal(root,'FOUR_CLASS_PROPOSAL.json'); scenes=[]
    selected=((10,'L4b'),(190,'L1'),(250,'L1'),(310,'L4b'),(70,'L1'),(100,'L1'))
    for day,legacy in selected:
        s=copy.deepcopy(next(s for s in source['scenarios'] if s['source_day_index']==day and s['analysis_class']==legacy))
        s.update(canonical_class=LEGACY_TO_CANONICAL[legacy],legacy_class=legacy,
            experiment_role='development',analysis_cohort='v17_development_known',sampling_cohort='v17_development_known',
            source_outcomes_used_for_selection=True,development_background=True,
            background_status='previously_exposed_calendar',historical_exposure_status='known_v16_exposed',
            design_variant='known_v16_'+str(day)+'_'+legacy,target_operating_role='classify_from_actual_prefix')
        if day in (70,100):
            s['target_operating_role']='standby_candidate'; scenes.append(copy.deepcopy(s))
            old_target='HRSG_2' if day==70 else 'EC_1'; target='HRSG_1' if day==70 else 'EC_2'
            s['faults']={target:0.}; s['design_variant']+='_'+target+'_in_service'
            s['target_operating_role']='in_service_candidate'
            for event in s['exogenous_events']:
                if event['kind']=='device_fault':
                    if event.get('device')==old_target:event['device']=target
                    if 'faults' in event:event['faults']={target:0.}
        scenes.append(s)
    # Fault-only ON/OFF plus delay sweep; no unsupported L4 fault+shock OFF.
    for delay in (0.,15.,30.):
        for canonical in ('L1','L2'):
            s=_events(_base(10,12.),canonical,faults={'GRID':0.},shock={},clock=82.5,
                fault_duration=180.,shock_clock=82.5,shock_duration=0.)
            s.update(design_variant='prop_boundary_delay_'+str(delay),response_delay_minutes=delay,
                target_operating_role='classify_from_actual_prefix',propagation_pair_id='v17_dev_GRID_'+str(delay),
                ablation_scene_candidate=True,source_outcomes_used_for_selection=True)
            scenes.append(s)
    return panel('development_known_in_service_and_propagation',scenes,bindings=bindings)


def build_normal_design(days=(47,113,202,353), *, experiment_role='confirmation', freeze_signature=None):
    scenes=[]
    for day in days:
        for hours in (24.,48.,72.):
            s=_base(day,hours,experiment_role=experiment_role)
            s.update(design_variant='normal_'+str(hours),core_four_class_denominator=False,
                analysis_cohort='v17_normal_'+str(int(hours))+'h',
                normal_duration_hours=hours,source_outcomes_used_for_selection=False)
            scenes.append(s)
    return panel('normal_seasonal_24_48_72h',scenes,freeze_signature=freeze_signature)


def build_long_outage_design(root=ROOT, *, experiment_role='confirmation', freeze_signature=None):
    source,bindings=_source_proposal(root,'DIESEL_PROPOSAL.json'); scenes=[]
    for original in source['scenarios']:
        s=copy.deepcopy(original)
        legacy=original['analysis_class']
        all_gt=all(original['faults'].get('GT_'+str(i))==0 for i in (1,2,3))
        demand=bool(original.get('shock') or original.get('shock_by_priority'))
        s.update(canonical_class=LEGACY_TO_CANONICAL[legacy],legacy_class=legacy,experiment_role=experiment_role,
            design_variant='long_outage_v17_'+original['id'],core_four_class_denominator=False,
            analysis_cohort='v17_long_outage',sampling_cohort='v17_long_outage',
            source_outcomes_used_for_selection=False,development_background=True,
            background_status='previously_exposed_calendar',historical_exposure_status='known_v16_exposed',
            diesel_design='all_GT_lost' if all_gt else 'one_GT_retained',
            long_outage_mechanism='multiple_exogenous_faults_plus_demand_shock' if demand else 'multiple_exogenous_faults_no_demand_shock',
            exogenous_multi_fault_is_endogenous_propagation=False)
        end=max(s['fault_start_minute']+s['fault_duration_minutes'],s['shock_start_minute']+s['shock_duration_minutes'])+240.
        s['duration_steps']=max(s['duration_steps'],math.ceil(end/s['step_minutes']))
        s['continuous_recovery_tail_minutes']=240.
        scenes.append(s)
    return panel('long_outage_separate_seen_background',scenes,bindings=bindings,freeze_signature=freeze_signature)


def build_manifest(proposal, source_sha256, model_id, model_config, root=ROOT, policies=None):
    validation=copy.deepcopy(proposal)
    validation['scenarios']=[_validation_view(s) for s in proposal['scenarios']]
    for s in proposal['scenarios']:validate_sealed_support(s)
    manifest=previous.build_manifest(validation,source_sha256,model_id,model_config,root=root,policies=policies)
    manifest.pop('signature',None)
    # Old builder only inventories input/source bytes and produces role/support
    # splits. Its TRUE validation shadow is never an execution specification.
    manifest['scenarios']=sorted(copy.deepcopy(proposal['scenarios']),key=lambda s:s['id'])
    manifest.update(manifest_version=SCHEMA+'.manifest',study_contract_id=STUDY_CONTRACT_ID,
        canonical_class_counts=copy.deepcopy(proposal.get('canonical_class_counts',{})),
        label_mapping=copy.deepcopy(LEGACY_TO_CANONICAL),source_proposal_signature=proposal['signature'],
        validation_only_propagation_copy_never_executed=True)
    from tools.engineering_model import policy as engineering_policy
    from tools.operating_response import policy as operating_policy
    from . import policy as coordinated_policy
    manifest['legacy_action_schema']=manifest.get('schemas',{}).get('action',previous.ACTION_SCHEMA)
    manifest['legacy_planning_interface_id']=manifest.get('planning_interface_id',previous.PLANNING_INTERFACE)
    manifest['schemas']=dict(manifest.get('schemas',{}),action=ACTION_SCHEMA)
    manifest['planning_interface_id']=PLANNING_INTERFACE
    manifest['action_routes']={route:dict(action_schema=module.ACTION_SCHEMA,planning_interface_id=module.PLANNING_INTERFACE)
        for route,module in (('engineering_joint',engineering_policy),('operating_joint',operating_policy),
                             ('state_coordinated_joint',coordinated_policy))}
    manifest['policy_action_routes']={}
    if policies is not None:
        # Resolve registered identities by their actual immutable spec kind,
        # including named C development variants, rather than name heuristics.
        from .runner import policy_specs
        registered={s['policy_id']:s for s in policy_specs().values()}
        routes=dict(engineering='engineering_joint',operating_response='operating_joint',state_coordinated='state_coordinated_joint')
        for policy_id in policies:
            if policy_id not in registered or registered[policy_id]['kind'] not in routes:
                raise ValueError('Mixed action route is not registered for policy identity')
            manifest['policy_action_routes'][policy_id]=routes[registered[policy_id]['kind']]
    suffix='__v17_'+digest([model_id,model_config,STUDY_CONTRACT_ID])[:16]
    manifest['execution_contract_id']=manifest['legacy_execution_contract_id']+suffix
    manifest['assumption_set_id']=manifest['legacy_assumption_set_id']+suffix
    return _finish(manifest)
