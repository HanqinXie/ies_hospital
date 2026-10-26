"""Bound constructor/resource factory; it never resets or advances a model."""
from __future__ import annotations
from dataclasses import dataclass,field
from pathlib import Path
import copy,hashlib,json
from .validation import ROOT,preflight_case,resolve_case,policy_identity
from . import STUDY_ID

def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data:=stream.read(1048576):h.update(data)
    return h.hexdigest()

def _write(path,value):
    with Path(path).open('x',encoding='utf8') as stream:json.dump(value,stream,indent=2,ensure_ascii=False,allow_nan=False)

@dataclass
class ExperimentContext:
    env:object
    controller:object
    manifest:dict
    binding:dict
    scene:dict
    config:dict
    output:Path
    case:dict
    io_resources:dict
    _prefix_stream:object
    _closed:bool=False
    _receipt:dict=field(default=None)

    def close(self,body_exception=None):
        if self._closed:return copy.deepcopy(self._receipt)
        writer=self.io_resources['_solver_row_sidecar'];failure=None
        if body_exception is not None:writer.body_exception=dict(type=type(body_exception).__name__,message=str(body_exception))
        try:self._prefix_stream.close()
        except Exception as exc:failure=exc
        try:writer.close()
        except Exception as exc:failure=failure or exc
        domain=self.io_resources['domain_evidence']
        prefix=self.output/'ACTUAL_PREFIX.jsonl'
        receipt=dict(solver_sidecar=writer.receipt(),domain_evidence=domain.receipt(),
            domain_files={p.name:dict(bytes=p.stat().st_size,sha256=_sha(p)) for p in sorted(domain.directory.glob('*')) if p.is_file()},
            actual_prefix=dict(path=str(prefix),bytes=prefix.stat().st_size,sha256=_sha(prefix)),
            actual_reset_or_episode_calls_by_factory=0,closed=True,error=None if failure is None else dict(type=type(failure).__name__,message=str(failure)))
        self._receipt=receipt;self._closed=True;_write(self.output/'FACTORY_IO_RECEIPT.json',receipt)
        if failure is not None:raise failure
        return copy.deepcopy(receipt)

    def __enter__(self):return self
    def __exit__(self,kind,error,tb):
        try:self.close(error)
        except Exception:
            if error is None:raise
        return False

def build_context(case,registry,output,*,order='legacy_before_cost',sidecar_basename='prefix_solver_rows.jsonl.gz',domain_basename='prefix_domains',overlay_spec=None):
    from tools.model_correction_v25 import environment as base_environment,MODEL_ID,CONTRACT_ID
    from tools.model_correction_v25.validation import resolve_operating_rule
    from tools.model_correctness_v23 import policy
    from tools.model_closure_v22.validation import digest,validate_exporter_metadata
    from tools.sci_engineering.runner import source_binding,input_binding
    from tools.sci_engineering.design import allowed_hours,SEALED_DAYS
    from tools.sci_engineering.solver_log import SolverRowSidecar
    from tools.model_correctness_v23.validation import record_actual_prefix
    for name in [sidecar_basename,domain_basename]:
        if not isinstance(name,str) or name in ('','.','..') or Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('Evidence names must be single relative basenames')
    resolved=resolve_case(case,registry,order=order);preflight_case(resolved,registry)
    if resolved['configuration'] not in registry['hardware_profiles']:raise ValueError('Unregistered physical configuration')
    scene=copy.deepcopy(resolved['scenario']);validate_exporter_metadata(scene)
    environment=base_environment
    extra={}
    if overlay_spec is not None:
        from .environment import with_overlay
        environment=with_overlay(overlay_spec)
        for key,value in overlay_spec['files'].items():
            path=Path(value['path']);path=path if path.is_absolute() else ROOT/path
            if not path.is_file() or _sha(path)!=value['sha256']:raise ValueError('Registered conditional input missing or changed: '+key)
            extra[key]=dict(path=str(path.resolve()),sha256=value['sha256'])
    schema_scene=copy.deepcopy(scene);schema_scene.pop('model_closure_events',None)
    environment.validate_scenario(schema_scene);environment.validate_events(scene.get('model_closure_events',[]))
    config=copy.deepcopy(registry['hardware_profiles'][resolved['configuration']])
    for layer in ['model_closure_v22','model_correctness_v23','engineering_completion_v24','model_correction_v25']:
        config[layer]=copy.deepcopy(resolved.get(layer,registry[layer]))
    rule_id,rule=resolve_operating_rule(registry,resolved)
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    source=source_binding(ROOT);inputs=input_binding(ROOT)
    writer=SolverRowSidecar(output/sidecar_basename);stream=None;env=None
    try:
        env=environment.OperatingHospitalIESEnv(ROOT,allowed_hours=allowed_hours(dict(scenarios=[scene])),sealed_days=SEALED_DAYS,model_id=MODEL_ID,
            model_config=config,solver_row_sidecar=writer,solver_evidence_directory=output/domain_basename,enable_all_validation_rows=True,operating_rule=rule)
        if env.control_rule_id!=rule_id:raise ValueError('Factory and actual operating-rule identities differ')
        stream=(output/'ACTUAL_PREFIX.jsonl').open('x',encoding='utf8')
        env.actual_segment_observer=lambda segment:record_actual_prefix(segment,stream)
        controller=policy.SciPolicy(resolved['policy']['alias'],config=resolved['policy']['config'],plant=env.plant,policy_id=policy_identity(resolved))
        signature=digest(env.operating_model_config)
        manifest=dict(execution_contract_id=CONTRACT_ID,assumption_set_id=CONTRACT_ID,engineering_model_contract_id=CONTRACT_ID,study_contract_id=STUDY_ID,
            model_config_signature=signature,configuration_id=resolved['configuration'],execution_model_id=MODEL_ID,simulator_hash=digest(source),schemas=copy.deepcopy(registry['schemas']),
            splits={scene['id']:dict(group_id=scene['family_id'],split='development',fit_allowed=False,embargo_minutes=60.,grouping_basis='full original source support')},
            control_rule_id=rule_id,control_rule=copy.deepcopy(env.control_rule))
        manifest['schemas']['model_correction_v25']=CONTRACT_ID;manifest['schemas']['model_review_v27']=STUDY_ID;manifest['signature']=digest(manifest)
        binding=dict(manifest_signature=manifest['signature'],simulator_hash=digest(source),source_sha256=digest(source),input_sha256=digest(inputs),
            execution_contract_id=CONTRACT_ID,scenario_id=scene['id'],scenario_sha256=digest(scene),case_sha256=digest(resolved),model_config_sha256=signature,extra_input_bindings=extra)
        resources=dict(_solver_row_sidecar=writer,domain_evidence=env.domain_evidence,actual_segment_observer=env.actual_segment_observer)
        _write(output/'FACTORY_CONTEXT.json',dict(case=resolved,scene=scene,model_config=config,resolved_model_config=env.operating_model_config,manifest=manifest,binding=binding,
            physical_model_id=MODEL_ID,study_id=STUDY_ID,policy_id=controller.policy_id,operating_rule_id=rule_id,base_environment_type=type(env).__module__+'.'+type(env).__qualname__,
            source_binding=source,input_binding=inputs,conditional_overlay=copy.deepcopy(overlay_spec),construction_did_not_reset=True))
        return ExperimentContext(env,controller,manifest,binding,scene,config,output,resolved,resources,stream)
    except Exception as exc:
        if stream is not None:stream.close()
        writer.body_exception=dict(type=type(exc).__name__,message=str(exc))
        try:writer.close()
        finally:
            _write(output/'FACTORY_CONSTRUCTION_FAILURE.json',dict(error=dict(type=type(exc).__name__,message=str(exc)),solver_sidecar=writer.receipt(),actual_reset_or_episode_calls_by_factory=0))
        raise
