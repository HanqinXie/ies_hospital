"""V18 registration adapter: unchanged seen exogenous specifications, new model binding."""
from __future__ import annotations
import copy
from pathlib import Path
from tools.state_coordinated import data_design as previous

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'hospital_ies.execution_recovery_metadata.v18.v1'
STUDY_CONTRACT_ID = 'hospital_ies.execution_recovery_study.v18.v1'
ACTION_SCHEMA = 'execution_recovery.mixed_actions.v18'
PLANNING_INTERFACE = 'execution_recovery.mixed_ordinary_joint_command.v18'
SEALED_DAYS = previous.SEALED_DAYS
RESOURCE_LIMITS = dict(max_actual_attempts=1200, max_experiment_wall_hours=24,
    max_main_dispatch_configurations=4, ceilings_not_quotas=True,
    internal_provisional_limits=True, revision_requires_recorded_evidence=True)
digest = previous.digest
allowed_hours = previous.allowed_hours
validate_sealed_support = previous.validate_sealed_support


def execution_model_contract_id(model_id):
    if model_id != 'M18_execution_recovery':
        raise ValueError('V18 requires its explicit shared M18 model')
    from .execution import DYNAMICS_CONTRACT_ID
    return DYNAMICS_CONTRACT_ID


def panel(name, scenes, *, bindings=None, freeze_signature=None):
    # Do not reseal scene IDs or change exogenous/physical fields: these are
    # exact seen regression specifications, not a new unseen sample.
    source = previous.panel(name, scenes, bindings=bindings, freeze_signature=freeze_signature)
    # The legacy panel can recompute IDs from metadata. Retain every caller
    # physical and exogenous field verbatim, not just its display identity.
    source['scenarios'] = copy.deepcopy(list(scenes))
    for s in source['scenarios']:
        s['v18_seen_regression'] = True
    source.pop('signature', None)
    source.update(schema=SCHEMA, study_name='hospital_ies_execution_recovery_v18',
        study_contract_id=STUDY_CONTRACT_ID, resource_limits=copy.deepcopy(RESOURCE_LIMITS),
        historical_unseen_claim=False, old80_role='seen_regression', final_archive_hard_limit_bytes=200000000)
    source['signature'] = digest(source)
    return source


def build_manifest(proposal, source_sha256, model_id, model_config, root=ROOT, policies=None):
    actual_contract = execution_model_contract_id(model_id)
    from .environment import model_contract
    _, resolved = model_contract(model_id, model_config)
    if digest(resolved) != digest(model_config):
        raise ValueError('Registration requires fully resolved M18 configuration')
    # V17's builder inventories unchanged original input/support bytes only.
    # The M16 identifier is confined to that metadata compatibility call;
    # overwrite every execution/model/assumption binding before signing.
    manifest = previous.build_manifest(proposal, source_sha256, 'M16_essential',
        model_config, root=root, policies=None)
    manifest.pop('signature', None)
    from .runner import policy_specs
    specs = {s['policy_id']:s for s in policy_specs().values()}
    routes = dict(engineering='engineering_joint', operating_response='operating_joint',
                  state_coordinated='state_coordinated_joint')
    manifest.update(manifest_version=SCHEMA+'.manifest', execution_model_id=model_id,
        engineering_model_contract_id=actual_contract, study_contract_id=STUDY_CONTRACT_ID,
        study_name='hospital_ies_execution_recovery_v18',
        planning_interface_id=PLANNING_INTERFACE, model_config=copy.deepcopy(resolved),
        model_config_signature=digest(resolved), resource_limits=copy.deepcopy(RESOURCE_LIMITS),
        historical_unseen_claim=False, old80_role='seen_regression',
        metadata_inventory_adapter='legacy V17 inventory only; no M16 execution')
    manifest['schemas']['action'] = ACTION_SCHEMA
    if 'sensitivity_registration' in proposal:
        manifest['sensitivity_registration']=copy.deepcopy(proposal['sensitivity_registration'])
    from . import policy as coordinated_policy
    manifest['action_routes']['state_coordinated_joint'] = dict(
        action_schema=coordinated_policy.ACTION_SCHEMA,
        planning_interface_id=coordinated_policy.PLANNING_INTERFACE)
    manifest['policy_action_routes'] = {p:routes[specs[p]['kind']] for p in (policies or [])}
    suffix = '__v18_' + digest([model_id, resolved, actual_contract, STUDY_CONTRACT_ID])[:16]
    manifest['execution_contract_id'] = manifest['legacy_execution_contract_id'] + suffix
    manifest['assumption_set_id'] = manifest['legacy_assumption_set_id'] + suffix
    manifest['signature'] = digest(manifest)
    return manifest
