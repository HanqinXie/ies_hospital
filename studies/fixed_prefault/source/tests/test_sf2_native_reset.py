"""Small local native reset/physical-prefix component, no registered attempt."""
from copy import deepcopy
from pathlib import Path
from contextlib import nullcontext
import hashlib,json,os,subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('segment',[1.,.5])
def test_actual_factory_native_reset_and_one_physical_segment(segment):
    from tools.annual_readiness.registration import load_registry,make_case,worker_registry
    from tools.annual_readiness.runner import _bindings
    from tools.eight_fault_study.runtime import create_source_context,runtime_configuration,with_local_runtime,resolve_local_source_freeze
    from tools.model_correction_v25.environment import MODEL_ID as CONSTRUCTOR_ID
    from tools.sci_engineering.design import SEALED_DAYS
    from tools.single_fault_fuel_20261102.environment import with_single_fault_fuel,decision_snapshot
    from tools.single_fault_fuel_20261102.runtime import ModelSpec,MODEL_ID
    from tools.single_fault_model_20261102.storage import ContinuousLossStore
    from hospital_ies.nonlinear import nonlinear_evidence_scope
    from hospital_ies.solver_diagnostics import json_value
    from sf2_evidence_fixture import make_domain_sink
    registry=load_registry()
    case=make_case(registry,'b07_L4_GT_ER_disturbed_M','M_available',mode='N',start_hour=33,configuration='H2')
    cfg=runtime_configuration(case,worker_registry(case,registry));before=deepcopy(cfg)
    bindings=_bindings();context=create_source_context(ROOT,case=case,configuration=cfg,bindings=bindings)
    original=with_single_fault_fuel('M_available',model_spec=ModelSpec(physical_max_segment_minutes=segment),
        requalification_minutes=None,thermal_profile=case['v28_thermal_profile'])
    adapter=with_local_runtime(original,context)
    env=adapter.OperatingHospitalIESEnv(ROOT,allowed_hours=context['allowed_hours'],sealed_days=SEALED_DAYS,
        model_id=CONSTRUCTOR_ID,model_config=deepcopy(cfg['model_config']),operating_rule=deepcopy(cfg['operating_rule']))
    assert getattr(env,'core',None) is None and cfg==before
    sample_run=os.environ.get('SF2_NATIVE_SAMPLE_RUN')
    destination=None;records=[]
    if sample_run:
        assert sample_run.replace('_','').isalnum()
        destination=Path('E:/hospital_ies_single_fault_model_20261102_run01/epochs/SF2/analysis/fuel_impl/native_samples')/sample_run/('max_segment_'+str(segment).replace('.','p'))
        sink,records=make_domain_sink(destination/'nonlinear_evidence')
    scope=nonlinear_evidence_scope(sink) if destination is not None else nullcontext()
    with scope as session:
        observed,_=env.reset(scenario=case['scenario'],seed=20261023)
        assert env.core.time==0. and env.core.segments==[]
        assert env.model_id==MODEL_ID and type(env.core.ups) is ContinuousLossStore
        assert resolve_local_source_freeze(env)==bindings['source']
        assert observed['single_fault_model']['physical_model_id']==MODEL_ID
        assert env.local_actual_witness_source_context.resolved_model_config['single_fault_fuel_model']==env.single_fault_fuel_spec.as_dict()
        assert decision_snapshot(env)['physical_execution_model_id']==MODEL_ID
        # No actual-witness observer/capture scope is installed; this is an
        # unregistered unit prefix, not scientific producer evidence.
        env.core.advance(segment)
    assert env.steps_done==0 and env.core.time==segment and len(env.core.segments)==1
    final=env.core.segments[-1]
    assert final['sf2_running_fuel']['accounting_committed'] is True
    assert final['sf2_running_fuel']['native_vector_dimension'] in (47,48)
    assert final['single_fault_model']['physical_model_id']==MODEL_ID
    if destination is not None:
        names=subprocess.check_output(['git','-c','core.quotePath=false','ls-files','-z','--cached','--others','--exclude-standard'],cwd=ROOT).decode('utf8').split('\0')
        source_files={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(set(names)) if name and (ROOT/name).is_file()}
        source_sha=hashlib.sha256(json.dumps(source_files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        sample=dict(schema='single_fault.sf2.unregistered_component_native_sample.v1',role='unregistered_component_not_formal',
            formal_attempt=False,source_candidate_sha256=source_sha,source_files=source_files,controller_calls=0,
            command='python -B -m pytest explicit SF2 unit files; run_logged receipt named by SF2_NATIVE_SAMPLE_RUN',
            callcontext=dict(source_root=str(ROOT),bindings=bindings,case=case,spec=env.single_fault_fuel_spec.as_dict(),seed=20261023),
            segment=deepcopy(final),solver_calls=list(env.core.solver_calls),nonlinear_evidence_session=deepcopy(session),
            nonlinear_index=records,profile_admission=False)
        (destination/'COMPONENT_SAMPLE.json').write_text(json.dumps(json_value(sample),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
