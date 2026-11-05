"""One final-blind M21 release, bound to closed selection and immutable files.

No numeric load row or outcome is read here. The initial grant and execution
receipt are separate: an authentic grant alone cannot open selective inputs.
Hash bindings are local audit controls, not cryptographic account permissions.
"""
from __future__ import annotations

from datetime import datetime,timezone
import copy
from pathlib import Path

from tools.sci_engineering import design,runner

SCHEMA='hospital_ies.sci_final_release.v21.v1'
AUTHORITY=Path('outputs/v21_sci_engineering_20261023_run01/final_blind/RELEASE_GRANT.json')
EXECUTION_AUTHORITY=AUTHORITY.with_name('EXECUTION_RECEIPT.json')
ORIGINAL_AUTHORITY=Path('outputs/v43_p2b_20261013_run01/experiment/global_manifest.json')


def _require(condition,message):
    if not condition:raise ValueError('final release receipt rejected: '+message)


def _sealed(root):
    value=runner.read(Path(root)/ORIGINAL_AUTHORITY)
    days=sorted(int(row['time_block_id'].rsplit('_',1)[1]) for row in value['p2b_time_blocks'] if row['reserved_only'])
    _require(days==list(design.SEALED_DAYS),'original sealed-12 time authority differs')
    return days


def _signature(value):
    return value.get('signature')==design.digest({k:v for k,v in value.items() if k!='signature'})


def _panel_support(panel,days):
    _require(panel.get('model_id')==design.MODEL_ID and panel.get('phase')=='final_blind','panel must be final_blind M21')
    _require(panel==runner.normalize_panel(panel),'final panel must be normalized and fixed before grant')
    scenes=panel.get('scenarios',[])
    _require(bool(scenes) and len({s['id'] for s in scenes})==len(scenes),'nonempty unique final scenes required')
    supports={};represented=set()
    for scene in scenes:
        _require(scene.get('role')=='final_blind' and scene.get('is_final_blind') is True,
                 'scene must explicitly declare final_blind role')
        _require(scene.get('fit_allowed') is False and scene.get('selection_allowed') is False
                 and scene.get('requires_policy_freeze') is True,'final outcomes cannot be fitted or selected')
        support=design.source_support(scene)
        hours=list(range(int(support['raw_start_minute']//60)+1,int(support['raw_end_minute']//60)+1))
        touched={(hour-1)//24 for hour in hours}
        _require(touched<=set(days),'support extends outside specified old sealed-12 days')
        represented.update(touched);supports[scene['id']]=hours
    _require(represented==set(days),'panel must specify all and only the twelve reserved backgrounds')
    return supports


def _selection(freeze,selection):
    specs=freeze.get('policy_specs',[])
    _require(len(specs)==5 and len({s.get('policy_id') for s in specs})==5
        and {s.get('display_alias') for s in specs}=={'R','M','F','C','A'},
        'final freeze must contain exactly the five main policy aliases')
    _require(set(freeze.get('configurations',{}))=={'H0'},'final freeze must contain H0 only; hardware uses another freeze')
    sci=freeze['configurations']['H0'].get('sci_engineering',{})
    _require(all(float(sci.get(name,{}).get('capacity_kWh',0.))==0. for name in ('ups','cold_store')),
        'final H0 cannot contain added storage hardware')
    _require(selection.get('selection_finalized') is True and selection.get('development_completed') is True,
             'development selection must be finalized')
    _require(selection==freeze.get('selection_record'),'selection file differs from immutable freeze selection')
    _require(set(selection.get('selected_policy_ids',[]))=={s['policy_id'] for s in freeze['policy_specs']},
             'selected policies differ from final freeze')
    _require(set(selection.get('selected_configuration_ids',[]))==set(freeze['configurations']),
             'selected configurations differ from final freeze')
    closed=datetime.fromisoformat(selection['finalized_utc'])
    frozen=datetime.fromisoformat(freeze['created_utc'])
    _require(closed.tzinfo is not None and closed<=frozen,'selection was not closed before final freeze')


def _nonexposure(registry,days):
    _require(_signature(registry),'historical metadata registry signature differs')
    _require(registry.get('result_or_raw_files_read') is False,'historical audit must be metadata only')
    _require(not registry.get('unresolved_starts'),'historical actual starts have unresolved support')
    _require(not any(not e.get('synthetic_fixture') for e in registry.get('parse_errors',[])),
             'historical actual-support metadata parse failure')
    overlaps=[(row['scenario_id'],day) for row in registry.get('actual_support',[]) for day in days
        if row['raw_start_minute']<(day+1)*1440 and day*1440<row['raw_end_minute']]
    _require(not overlaps,'historical actual-start support intersects sealed backgrounds')
    return dict(registry_signature=registry['signature'],actual_sealed_support_overlap_count=0,
        actual_scene_ids=len({r['scenario_id'] for r in registry.get('actual_support',[])}),
        actual_support_interval_count=len({(r['raw_start_minute'],r['raw_end_minute']) for r in registry.get('actual_support',[])}),
        synthetic_parse_errors_disclosed=sum(e.get('synthetic_fixture',False) for e in registry.get('parse_errors',[])),
        unresolved_actual_starts=0,no_result_or_raw_reads=True,
        meaning='No recorded started physical incident support overlaps these dates in scanned actual journals; absent or unrecorded historical runs are not certified')


def _historical_authority(selection,registry_path):
    """Bind the audit scope before final freeze, not from an arbitrary caller."""
    value=selection.get('historical_exposure_authority')
    _require(isinstance(value,dict),'frozen historical exposure authority is required')
    for key in ('canonical_root','registry_path','registry_sha256'):
        _require(isinstance(value.get(key),str) and bool(value[key]),'historical authority missing '+key)
    raw_root=Path(value['canonical_root']);raw_registry=Path(value['registry_path'])
    _require(raw_root.is_absolute() and raw_registry.is_absolute(),'historical authority paths must be absolute')
    historical_root=raw_root.resolve();registered_path=raw_registry.resolve()
    _require(historical_root.is_dir(),'canonical historical authority root is unavailable')
    _require(Path(registry_path).resolve()==registered_path,'historical registry path differs from frozen authority')
    _require(registered_path.is_file() and runner.sha(registered_path)==value['registry_sha256'],
        'historical registry SHA differs from frozen authority')
    registry=runner.read(registered_path)
    _require(isinstance(registry.get('root'),str) and Path(registry['root']).is_absolute()
        and Path(registry['root']).resolve()==historical_root,'historical registry root differs from frozen canonical authority')
    return dict(canonical_root=str(historical_root),registry_path=str(registered_path),
        registry_sha256=value['registry_sha256']),registry


def issue_grant(root,freeze_path,selection_path,panel_path,exposure_registry_path):
    root=Path(root).resolve();destination=root/AUTHORITY
    if destination.exists():raise FileExistsError('This research round already has its one final grant')
    paths={name:Path(path).resolve() for name,path in dict(freeze=freeze_path,selection=selection_path,
        panel=panel_path,exposure_registry=exposure_registry_path).items()}
    freeze=runner.read(paths['freeze']);selection=runner.read(paths['selection']);panel=runner.read(paths['panel'])
    runner.verify_freeze(root,freeze);_selection(freeze,selection)
    days=_sealed(root);supports=_panel_support(panel,days)
    historical_authority,old_registry=_historical_authority(selection,paths['exposure_registry'])
    _nonexposure(old_registry,days)
    # Re-scan actual metadata at release time. This cannot use outcomes and does
    # not infer nonexposure merely from the old frozen manifest declaration.
    refreshed=design.scan_historical_support(historical_authority['canonical_root'],sealed_days=days)
    audit=_nonexposure(refreshed,days)
    grant=runner._signed(dict(schema=SCHEMA+'.grant',model_id=design.MODEL_ID,phase='final_blind',
        issued_utc=runner.now(),user_authorization='Hospital_IES_SCI_Codex_Task_20261023 final-freeze once-only release',
        sealed_days=days,scene_support_hours=supports,released_hours=sorted({h for values in supports.values() for h in values}),
        file_bindings={name:dict(path=str(path),sha256=runner.sha(path)) for name,path in paths.items()},
        freeze_signature=freeze['signature'],source_binding_sha256=design.digest(freeze['source_sha256']),
        input_binding_sha256=design.digest(freeze['runtime_inputs']),policy_specs_sha256=design.digest(freeze['policy_specs']),
        configurations_sha256=design.digest(freeze['configurations']),panel_semantic_sha256=design.digest(panel),
        selected_policy_ids=selection['selected_policy_ids'],selected_configuration_ids=selection['selected_configuration_ids'],
        historical_exposure_authority=historical_authority,
        metadata_only_nonexposure_audit=audit,original_final12_evaluated=False,
        development_permanently_closed_for_this_grant=True,grant_count_this_round=1))
    runner.write_new(destination,grant)
    return grant


def validate_grant(root,grant):
    root=Path(root).resolve()
    _require(isinstance(grant,dict) and grant.get('schema')==SCHEMA+'.grant','unknown grant schema')
    path=root/AUTHORITY
    _require(path.is_file() and runner.read(path)==grant and _signature(grant),'grant differs from fixed local authority')
    _require(grant.get('phase')=='final_blind' and grant.get('grant_count_this_round')==1,'grant phase/count differs')
    _require(grant['sealed_days']==_sealed(root),'sealed time authority drift')
    for name,row in grant['file_bindings'].items():
        _require(Path(row['path']).is_file() and runner.sha(row['path'])==row['sha256'],name+' file drift')
    freeze=runner.read(grant['file_bindings']['freeze']['path'])
    runner.verify_freeze(root,freeze)
    selection=runner.read(grant['file_bindings']['selection']['path']);_selection(freeze,selection)
    historical_authority,_=_historical_authority(selection,grant['file_bindings']['exposure_registry']['path'])
    _require(grant.get('historical_exposure_authority')==historical_authority,'grant historical authority binding differs')
    panel=runner.read(grant['file_bindings']['panel']['path'])
    _require(design.digest(panel)==grant['panel_semantic_sha256'],'panel semantic drift')
    _require(_panel_support(panel,grant['sealed_days'])==grant['scene_support_hours'],'registered source support drift')
    return freeze,panel


def validate_final_registration(root,grant,phase,panel,policy_specs,configurations):
    freeze,expected=validate_grant(root,grant)
    _require(phase=='final_blind','other phases cannot register final_blind data')
    _require(panel==expected,'registration panel differs from released panel')
    _require(design.digest(policy_specs)==grant['policy_specs_sha256'],'unselected policy specification')
    _require(design.digest(configurations)==grant['configurations_sha256'],'unselected hardware/model configuration')
    return copy.deepcopy(grant)


def bind_execution(root,grant,plan_path,manifest_path):
    root=Path(root).resolve()
    if (root/EXECUTION_AUTHORITY).exists():raise FileExistsError('Final execution receipt already exists')
    freeze,panel=validate_grant(root,grant)
    plan_path=Path(plan_path).resolve();manifest_path=Path(manifest_path).resolve()
    plan=runner.read(plan_path);manifest=runner.read(manifest_path)
    _require(_signature(plan) and _signature(manifest),'PLAN/MANIFEST signature drift')
    validate_final_registration(root,grant,plan.get('phase'),plan.get('panel'),plan.get('policy_specs'),plan.get('configurations'))
    _require(manifest.get('phase')=='final_blind' and manifest.get('execution_model_id')==design.MODEL_ID,
             'manifest must explicitly be final_blind M21')
    _require(plan.get('manifest_signature')==manifest['signature'],'PLAN/MANIFEST binding differs')
    _require(plan.get('freeze_signature')==manifest.get('freeze_signature')==freeze['signature'],'freeze binding differs')
    _require(manifest.get('scenarios')==panel['scenarios'],'manifest scenario panel differs')
    _require(manifest.get('source_sha256')==freeze['source_sha256'],'manifest source binding differs')
    _require(manifest.get('policy_specs_sha256')==grant['policy_specs_sha256'],'manifest policy binding differs')
    _require(manifest.get('configuration_signatures')=={k:design.digest(v) for k,v in freeze['configurations'].items()},
             'manifest configuration binding differs')
    receipt=runner._signed(dict(schema=SCHEMA+'.execution_receipt',model_id=design.MODEL_ID,phase='final_blind',
        released_utc=runner.now(),grant_issued_utc=grant['issued_utc'],grant_signature=grant['signature'],
        grant_sha256=runner.sha(root/AUTHORITY),freeze_signature=freeze['signature'],
        plan_binding=dict(path=str(plan_path),sha256=runner.sha(plan_path)),
        manifest_binding=dict(path=str(manifest_path),sha256=runner.sha(manifest_path)),
        manifest_signature=manifest['signature'],released_hours=grant['released_hours'],
        selected_policy_ids=grant['selected_policy_ids'],selected_configuration_ids=grant['selected_configuration_ids'],
        metadata_only_nonexposure_audit=grant['metadata_only_nonexposure_audit'],
        source_binding_sha256=grant['source_binding_sha256'],policy_specs_sha256=grant['policy_specs_sha256'],
        configurations_sha256=grant['configurations_sha256'],original_final12_evaluated=False))
    runner.write_new(root/EXECUTION_AUTHORITY,receipt)
    return receipt


def validate_input_release(root,allowed_hours,sealed_days,release_receipt):
    root=Path(root).resolve()
    _require(isinstance(release_receipt,dict) and release_receipt.get('schema')==SCHEMA+'.execution_receipt',
             'unknown final release receipt schema')
    authority=root/EXECUTION_AUTHORITY
    _require(authority.is_file() and runner.read(authority)==release_receipt and _signature(release_receipt),
             'execution receipt differs from fixed local authority')
    grant=runner.read(root/AUTHORITY);freeze,panel=validate_grant(root,grant)
    _require(release_receipt.get('phase')=='final_blind','input phase must be final_blind')
    _require(release_receipt['grant_sha256']==runner.sha(root/AUTHORITY),'grant file drift')
    _require(sorted(sealed_days)==grant['sealed_days'],'sealed day authority mismatch')
    for name in ('plan_binding','manifest_binding'):
        row=release_receipt[name]
        _require(Path(row['path']).is_file() and runner.sha(row['path'])==row['sha256'],name+' drift')
    plan=runner.read(release_receipt['plan_binding']['path']);manifest=runner.read(release_receipt['manifest_binding']['path'])
    _require(plan.get('phase')==manifest.get('phase')=='final_blind','non-final_blind execution context')
    _require(manifest['signature']==release_receipt['manifest_signature'],'manifest signature drift')
    hours=list(allowed_hours)
    _require(bool(hours) and all(isinstance(h,int) and not isinstance(h,bool) and 1<=h<=8760 for h in hours)
        and len(hours)==len(set(hours)),'allowed hours must be distinct valid integers')
    requested=set(hours)
    _require(requested<=set(grant['released_hours']),'hours outside released source support')
    complete=[set(values) for values in grant['scene_support_hours'].values() if set(values)<=requested]
    _require(bool(complete) and set().union(*complete)==requested,'hours are not a union of complete registered scene supports')
    return dict(phase='final_blind',released_hours=sorted(requested),grant_signature=grant['signature'],
        release_signature=release_receipt['signature'],released_utc=release_receipt['released_utc'],
        metadata_only_nonexposure_audit=grant['metadata_only_nonexposure_audit'])


def validate_scene_release(root,scene,allowed_hours,sealed_days,release_receipt):
    """A reset must match a complete scene, not merely reuse its released hour."""
    audit=validate_input_release(root,allowed_hours,sealed_days,release_receipt)
    grant=runner.read(Path(root)/AUTHORITY);_,panel=validate_grant(root,grant)
    expected=[s for s in panel['scenarios'] if s['id']==scene.get('id')]
    _require(len(expected)==1 and scene==expected[0],'reset scene is not the exact registered final scene')
    hours=design.allowed_hours(dict(scenarios=[scene]))
    _require(hours<=set(allowed_hours),'whole registered scene support is not available to reset')
    return dict(audit,scenario_id=scene['id'],registered_scene_support_hours=sorted(hours))
