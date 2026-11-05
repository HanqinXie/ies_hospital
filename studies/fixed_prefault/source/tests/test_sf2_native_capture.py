"""Unregistered native component prefixes: source/capture/NL transport together.

No canonical runner attempt, controller step, annual admission or formal case.
"""
from copy import deepcopy
from pathlib import Path
import gzip,json
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('maximum,configuration,method,expected_layout',[
    (1.,'H2','M_available',47),(.5,'H2','M_available',47),(1.,'H1','M_ready',48)])
def test_native_short_prefix_binds_source_actual_primal_and_closed_nl_store(tmp_path,maximum,configuration,method,expected_layout):
    from tools.annual_readiness.registration import load_registry,make_case,worker_registry
    from tools.annual_program.compiled_capture import capture_scope
    from tools.annual_program.witness import compiled_origin,FIELD
    from tools.single_fault_fuel_20261102 import runner
    from tools.sci_engineering.solver_log import SolverRowSidecar,normalize_json
    from hospital_ies.nonlinear import nonlinear_evidence_scope
    case=make_case(load_registry(),'b07_L4_GT_ER_disturbed_M',method,mode='N',start_hour=33,configuration=configuration)
    original=deepcopy(case);bindings=runner.native._bindings();spec=runner.ModelSpec(physical_max_segment_minutes=maximum)
    out=tmp_path/'unregistered_component';out.mkdir()
    runner.native._write_new(out/'BINDINGS.json',bindings)
    store=runner.NonlinearStore(out,bindings=bindings,case_id=case['id'],physical_model_spec=spec.as_dict(),
        storage_model_spec=runner.StorageSpec(physical_max_segment_minutes=maximum).as_dict())
    origin=compiled_origin(ROOT,bindings['source'])
    writer=SolverRowSidecar(out/'ROWS.jsonl.gz')
    try:
        with nonlinear_evidence_scope(store) as session,capture_scope(origin):
            env,controller,resolved=runner._build_runtime(case,worker_registry(case,load_registry()),out,writer,model_spec=spec)
            observed,_=env.reset(scenario=case['scenario'],seed=20261023)
            assert env.core.time==0. and not env.core.segments
            env.core.advance(maximum)
        evidence=store.finish(session)
        assert session['status']=='closed_completed'
        assert session['call_count']==session['completed_call_count']>0
        assert evidence['index']['event_count']==2*session['call_count']
        final=env.core.segments[-1];receipt=final[FIELD]
        assert env.steps_done==0 and env.core.time==maximum and len(env.core.segments)==1
        assert receipt['status']=='recorded' and receipt['raw_x_length'] in (47,48)
        assert receipt['raw_x_length']==expected_layout
        call=env.core.solver_calls[receipt['solver_call_index']]
        metadata=call['annual_actual_witness'];packet=metadata['compiled_flow_input']
        assert metadata['compiled_flow_binding']['source_profile']=='single_fault_pnnl_exact_fuel_capture_v1'
        assert packet['source_origin']==origin
        assert final['sf2_running_fuel']['accepted_raw_x']==call['solver_backend_layers'][0]['attempts'][0]['raw_x']
        assert final['sf2_running_fuel']['accepted_raw_x_sha256']==receipt['raw_x_sha256']
        assert resolved['single_fault_model']==runner.StorageSpec(physical_max_segment_minutes=maximum).as_dict()
        assert resolved['single_fault_fuel_model']==spec.as_dict()
        assert resolved['model_config']['single_fault_fuel_curve']==env.single_fault_fuel_curve.as_dict()
        assert observed['single_fault_model']['physical_model_id']==runner.MODEL_ID
        assert case==original and resolved['annual_numeric_grant'] is False
        # Reset reference probes are full NL domains, never actual DTOs/energy.
        for record in store.records:
            body=json.loads(gzip.decompress((out/record['domain_reference']['artifacts'][0]['path']).read_bytes()))
            assert 'scope_sequence' not in body
            assert body['source_context']['role']=='free_reference_probe'
        runner.native._write_new(out/'UNREGISTERED_COMPONENT.json',normalize_json(dict(formal_attempt=False,
            case=case,bindings=bindings,resolved=resolved,segment=final,nl=evidence,core_session=session)))
    finally:writer.close()
