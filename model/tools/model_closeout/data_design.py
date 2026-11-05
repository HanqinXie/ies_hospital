"""V19 immutable metadata: seen replays and separately certified confirmation."""
from __future__ import annotations
import copy
from collections import Counter
from pathlib import Path
from tools.state_coordinated import data_design as previous
from tools.operating_response.data_design import _panel

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'hospital_ies.model_closeout_metadata.v19.v1'
STUDY_CONTRACT_ID = 'hospital_ies.model_closeout_study.v19.v1'
ACTION_SCHEMA = 'model_closeout.mixed_actions.v19'
PLANNING_INTERFACE = 'model_closeout.mixed_ordinary_joint_command.v19'
MODEL_ID = 'M19_model_closeout'
SEALED_DAYS = previous.SEALED_DAYS
RESOURCE_LIMITS = dict(max_actual_attempts=700, max_experiment_wall_hours=48,
    max_registered_pairs=700,
    max_main_dispatch_configurations=4, ceilings_not_quotas=True,
    internally_predeclared_engineering_bounds=True, user_authorized_budget_claim=False,
    revision_requires_recorded_evidence=True)
digest = previous.digest
allowed_hours = previous.allowed_hours
validate_sealed_support = previous.validate_sealed_support


def execution_model_contract_id(model_id):
    if model_id != MODEL_ID:
        raise ValueError('V19 requires explicit M19_model_closeout')
    from .execution import DYNAMICS_CONTRACT_ID
    return DYNAMICS_CONTRACT_ID


def _confirmation_isolation(scenes, freeze_signature, registration):
    if not freeze_signature or not isinstance(registration, dict):
        raise ValueError('Independent confirmation requires a freeze and signed support registration')
    unsigned = {k:v for k,v in registration.items() if k!='signature'}
    if (registration.get('signature') != digest(unsigned)
            or registration.get('schema') != 'hospital_ies.v19.confirmation_isolation.v1'
            or registration.get('freeze_signature') != freeze_signature
            or registration.get('historical_training_included') is not True
            or registration.get('v19_development_included') is not True
            or not registration.get('source_inventory_sha256')
            or not isinstance(registration.get('excluded_support'), list)):
        raise ValueError('Unbound or incomplete confirmation isolation registration')
    roots = {}
    for scene in scenes:
        roots.setdefault(scene['root_family_id'], []).append(scene)
    if not 8 <= len(roots) <= 12:
        raise ValueError('Small confirmation requires 8 to 12 complete backgrounds')
    occupied = []
    for members in roots.values():
        if Counter(s.get('canonical_class') for s in members) != Counter(dict.fromkeys(('L1','L2','L3','L4'),1)):
            raise ValueError('Every confirmation background needs exactly four canonical classes')
        supports = [validate_sealed_support(s) for s in members]
        a = min(s['raw_start_minute'] for s in supports)-60.
        b = max(s['raw_end_minute'] for s in supports)+60.
        if any(a<y and x<b for x,y in occupied):
            raise ValueError('Confirmation backgrounds overlap in full source support or embargo')
        if any(a<float(x['raw_end_minute']) and float(x['raw_start_minute'])<b
                for x in registration['excluded_support']):
            raise ValueError('Confirmation overlaps historical training/development support')
        occupied.append((a,b))
    return len(roots)


def panel(name, scenes, *, bindings=None, freeze_signature=None,
          evaluation_role='seen_regression', isolation_registration=None):
    scenes = copy.deepcopy(list(scenes))
    if evaluation_role not in ('seen_regression','development','independent_confirmation'):
        raise ValueError('Unknown V19 evaluation role')
    for scene in scenes:
        validate_sealed_support(scene)
    backgrounds = (_confirmation_isolation(scenes,freeze_signature,isolation_registration)
                   if evaluation_role=='independent_confirmation' else 0)
    result = _panel(name, scenes, bindings=bindings, freeze_signature=freeze_signature)
    # The caller's physical/exogenous identity is immutable; no legacy reseal.
    result['scenarios'] = scenes
    for scene in scenes:
        scene['v19_seen_regression'] = evaluation_role=='seen_regression'
        if evaluation_role=='independent_confirmation':
            scene.update(experiment_role='confirmation',requires_policy_freeze=True,
                selection_allowed=False,fit_allowed=False,new_independent_background=True)
    result.pop('signature',None)
    result.update(schema=SCHEMA,study_name='hospital_ies_model_closeout_v19',
        study_contract_id=STUDY_CONTRACT_ID,resource_limits=copy.deepcopy(RESOURCE_LIMITS),
        evaluation_role=evaluation_role, historical_unseen_claim=False,
        old80_role='seen_regression',original_final12_evaluated=False,
        original_final_release_authorized=False,confirmation_background_count=backgrounds,
        new_independent_background_count=backgrounds,
        canonical_class_counts=dict(Counter(s.get('canonical_class') for s in scenes)),
        confirmation_isolation_registration=copy.deepcopy(isolation_registration),
        final_archive_hard_limit_bytes=200000000)
    for root in result['roots']:
        root['new_independent_background']=evaluation_role=='independent_confirmation'
    result['signature']=digest(result)
    return result


def build_manifest(proposal, source_sha256, model_id, model_config, root=ROOT, policies=None):
    actual_contract = execution_model_contract_id(model_id)
    from .environment import model_contract
    _, resolved = model_contract(model_id,model_config)
    if digest(resolved)!=digest(model_config):
        raise ValueError('Registration requires fully resolved M19 configuration')
    # Legacy helper inventories original source/input bytes and support only.
    # It never constructs an M16 simulator. Replace all model bindings before signing.
    manifest=previous.build_manifest(proposal,source_sha256,'M16_essential',model_config,
        root=root,policies=None)
    manifest.pop('signature',None)
    from .runner import policy_specs
    specs={s['policy_id']:s for s in policy_specs().values()}
    routes=dict(engineering='engineering_joint',operating_response='operating_joint',
                state_coordinated='state_coordinated_joint')
    manifest.update(manifest_version=SCHEMA+'.manifest',execution_model_id=model_id,
        engineering_model_contract_id=actual_contract,study_contract_id=STUDY_CONTRACT_ID,
        study_name='hospital_ies_model_closeout_v19',planning_interface_id=PLANNING_INTERFACE,
        model_config=copy.deepcopy(resolved),model_config_signature=digest(resolved),
        resource_limits=copy.deepcopy(RESOURCE_LIMITS),historical_unseen_claim=False,
        old80_role='seen_regression',evaluation_role=proposal.get('evaluation_role','seen_regression'),
        confirmation_background_count=proposal.get('confirmation_background_count',0),
        new_independent_background_count=proposal.get('confirmation_background_count',0),
        confirmation_isolation_registration=copy.deepcopy(proposal.get('confirmation_isolation_registration')),
        metadata_inventory_adapter='V17 original byte inventory only; no legacy physical execution')
    manifest['schemas']['action']=ACTION_SCHEMA
    if 'sensitivity_registration' in proposal:
        manifest['sensitivity_registration']=copy.deepcopy(proposal['sensitivity_registration'])
    from . import policy as coordinated_policy
    manifest['action_routes']['state_coordinated_joint']=dict(
        action_schema=coordinated_policy.ACTION_SCHEMA,planning_interface_id=coordinated_policy.PLANNING_INTERFACE)
    manifest['policy_action_routes']={p:routes[specs[p]['kind']] for p in (policies or [])}
    suffix='__v19_'+digest([model_id,resolved,actual_contract,STUDY_CONTRACT_ID])[:16]
    manifest['execution_contract_id']=manifest['legacy_execution_contract_id']+suffix
    manifest['assumption_set_id']=manifest['legacy_assumption_set_id']+suffix
    manifest['signature']=digest(manifest)
    return manifest
