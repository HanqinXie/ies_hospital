"""M20 metadata and actual runtime inputs; no unread literature inventory."""
from __future__ import annotations
from collections import Counter
import copy
import hashlib
from pathlib import Path

from hospital_ies.dataset_manifest import digest, runtime_versions
from tools.state_coordinated.data_design import allowed_hours, validate_sealed_support, SEALED_DAYS
from tools.operating_response.data_design import _panel
from .execution import MODEL_ID, DYNAMICS_CONTRACT_ID, LOAD_ACCOUNTING_CONTRACT_ID

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'hospital_ies.load_accounting_metadata.v20.v1'
STUDY_CONTRACT_ID = 'hospital_ies.load_accounting_study.v20.v1'
ACTION_SCHEMA = 'load_accounting.mixed_actions.v20'
PLANNING_INTERFACE = 'load_accounting.mixed_ordinary_joint_command.v20'
RESOURCE_LIMITS = dict(max_actual_attempts=700, max_registered_pairs=700,
    max_experiment_wall_hours=48, max_main_dispatch_configurations=4,
    ceilings_not_quotas=True, internally_predeclared_engineering_bounds=True,
    user_authorized_budget_claim=False, revision_requires_recorded_evidence=True)


def execution_model_contract_id(model_id):
    if model_id != MODEL_ID:
        raise ValueError('M20 explicit model identity required')
    return DYNAMICS_CONTRACT_ID


def runtime_input_inventory(root=ROOT):
    root = Path(root)
    # These are original data/equipment/source records used by, or directly
    # supporting, this execution. Original third-party literature is reference
    # material, not a requirement to run a dispatch episode.
    names = {p.relative_to(root).as_posix() for folder, suffixes in (
        ('inputs/loads', {'.csv'}), ('inputs/energyplus', {'.idf','.epw','.md'}),
        ('inputs/reference', {'.csv','.json'}), ('configs', {'.json'}))
        for p in (root/folder).glob('*') if p.is_file() and p.suffix.lower() in suffixes}
    names.update(p.name for p in root.glob('requirements*.txt'))
    return [dict(path=name, bytes=(root/name).stat().st_size,
                 sha256=hashlib.sha256((root/name).read_bytes()).hexdigest()) for name in sorted(names)]


def panel(name, scenes, *, bindings=None, freeze_signature=None, evaluation_role='seen_regression'):
    if evaluation_role not in ('seen_regression','seen_fixed_background','development'):
        raise ValueError('V20 repeats seen specifications; no new independent confirmation authorized')
    scenes = copy.deepcopy(list(scenes))
    for scene in scenes:
        validate_sealed_support(scene)
        scene.update(v20_source_scene_id=scene['id'], v20_seen_regression=True,
            evaluation_role=evaluation_role, new_independent_background=False,
            experiment_role='development' if evaluation_role=='development' else 'confirmation',
            requires_policy_freeze=evaluation_role!='development', selection_allowed=False,
            fit_allowed=False, is_final_blind=False, original_final12_evaluated=False,
            original_final_release_authorized=False,
            background_status='seen_fixed_background' if evaluation_role=='seen_fixed_background' else 'seen_regression',
            historical_unseen_claim=False)
    result = _panel(name, scenes, bindings=bindings, freeze_signature=freeze_signature)
    result.pop('signature',None)
    result.update(schema=SCHEMA, study_name='hospital_ies_load_accounting_v20',
        study_contract_id=STUDY_CONTRACT_ID, load_accounting_contract_id=LOAD_ACCOUNTING_CONTRACT_ID,
        resource_limits=copy.deepcopy(RESOURCE_LIMITS), evaluation_role=evaluation_role,
        historical_unseen_claim=False, old80_role='seen_regression',
        original_final12_evaluated=False, original_final_release_authorized=False,
        sealed_final_days=list(SEALED_DAYS), confirmation_background_count=0,
        new_independent_background_count=0,
        fixed_background_count=len(result['roots']) if evaluation_role=='seen_fixed_background' else 0,
        canonical_class_counts=dict(Counter(s.get('canonical_class') for s in scenes)),
        confirmation_isolation_registration=None,
        independence='Seen fixed-source specifications rerun after accounting correction; no independent confirmation claim',
        final_archive_hard_limit_bytes=200000000)
    result['signature'] = digest(result)
    return result


def build_manifest(proposal, source_sha256, model_id, model_config, root=ROOT, policies=None):
    from .environment import model_contract
    from .runner import policy_specs
    contract, resolved = model_contract(model_id, model_config)
    if digest(resolved) != digest(model_config):
        raise ValueError('Registration requires fully resolved M20 configuration')
    if proposal.get('signature') != digest({k:v for k,v in proposal.items() if k!='signature'}):
        raise ValueError('Proposal signature differs')
    scenes = sorted(copy.deepcopy(proposal['scenarios']), key=lambda s:s['id'])
    splits = {}
    for scene in scenes:
        support = validate_sealed_support(scene)
        if scene.get('new_independent_background') or scene.get('fit_allowed'):
            raise ValueError('V20 is a fixed-specification recheck without training or new-independent claims')
        splits[scene['id']] = dict(group_id=scene['root_family_id'], split=scene['experiment_role'],
            experiment_role=scene['experiment_role'], fit_allowed=False, selection_allowed=False,
            development_only=not scene['requires_policy_freeze'], requires_policy_freeze=scene['requires_policy_freeze'],
            is_final_blind=False, support_independence_claim=False,
            raw_start_minute=support['raw_start_minute'], raw_end_minute=support['raw_end_minute'],
            time_block_id=scene['time_block_id'], embargo_minutes_each_side=60,
            split_version=SCHEMA, purpose='v20_accounting_correction_fixed_recheck')
    pool = {s['policy_id']:s for s in policy_specs().values()}
    routes = dict(engineering='engineering_joint',operating_response='operating_joint',state_coordinated='state_coordinated_joint')
    from tools.engineering_model import policy as ep
    from tools.operating_response import policy as op
    from tools.model_closeout import policy as cp
    ids = list(policies or [])
    if any(i not in pool for i in ids):
        raise ValueError('Policy identity is not registered for M20')
    contract_id = DYNAMICS_CONTRACT_ID+':'+digest(contract)
    manifest = dict(manifest_version=SCHEMA+'.manifest',schema=SCHEMA,
        study_name='hospital_ies_load_accounting_v20',study_contract_id=STUDY_CONTRACT_ID,
        execution_model_id=model_id,engineering_model_contract_id=DYNAMICS_CONTRACT_ID,
        load_accounting_contract_id=LOAD_ACCOUNTING_CONTRACT_ID,
        source_sha256=copy.deepcopy(source_sha256),source_proposal_signature=proposal['signature'],
        source_metadata_bindings=copy.deepcopy(proposal.get('source_metadata_bindings',{})),
        files=runtime_input_inventory(root),runtime=runtime_versions(),scenarios=scenes,
        roots=copy.deepcopy(proposal['roots']),policies=ids,execution_contract=contract,
        model_config=copy.deepcopy(resolved),model_config_signature=digest(resolved),
        planning_interface_id=PLANNING_INTERFACE,schemas=dict(action=ACTION_SCHEMA,
            state='p2a.state.v1',features='p2a.features.v1',event='p2a.events.v1'),
        options=dict(no_future_information=True, no_MPC=True),splits=splits,
        simulator_hash=digest(source_sha256),execution_contract_id=contract_id,
        assumption_set_id='load_accounting.v20:'+digest([resolved,LOAD_ACCOUNTING_CONTRACT_ID]),
        action_routes={route:dict(action_schema=m.ACTION_SCHEMA,planning_interface_id=m.PLANNING_INTERFACE)
            for route,m in (('engineering_joint',ep),('operating_joint',op),('state_coordinated_joint',cp))},
        policy_action_routes={i:routes[pool[i]['kind']] for i in ids},
        resource_limits=copy.deepcopy(RESOURCE_LIMITS),freeze_signature=proposal.get('freeze_signature'),
        evaluation_role=proposal.get('evaluation_role','seen_regression'),
        fixed_background_count=proposal.get('fixed_background_count',0),confirmation_background_count=0,
        new_independent_background_count=0,historical_unseen_claim=False,old80_role='seen_regression',
        original_final12_evaluated=False,original_final_release_authorized=False,
        sealed_final_days=list(SEALED_DAYS),probability_semantics='Designed stress cases; not failure probabilities',
        data_loading_contract='Selective authorized physical rows only; unauthorized hours NaN; sealed12 unreleased',
        runtime_input_scope='Explicit CSV/IDF/EPW/equipment/source-record inventory; unread literature excluded',
        external_literature_is_not_runtime_input=True,
        random_stream_semantics='Registered identical exogenous definitions; no future information to controls')
    if proposal.get('sensitivity_registration'):
        manifest['sensitivity_registration']=copy.deepcopy(proposal['sensitivity_registration'])
    manifest['signature']=digest(manifest)
    return manifest
