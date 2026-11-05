"""Annual block proofs use real journal and legacy meter logic, without a solver."""
from copy import deepcopy
import gzip
import importlib
import json
from pathlib import Path

import pytest

from test_engineering_basis_steam_meter import case as legacy_case, sha, jsha
from tools.annual_readiness.journal import BlockJournal, verify_journal
from tools.sci_engineering.design import digest
from reviewed_source_fixtures import reviewed_source_bytes


def api():
    path = Path(__file__).parents[1]/'tools/annual_program/steam.py'
    assert path.is_file(), 'annual actual-block steam adapter is not implemented'
    return importlib.import_module('tools.annual_program.steam')


@pytest.fixture
def annual(legacy_case):
    old = legacy_case
    old.persist()
    producer = old.producer
    original = deepcopy(old.payload)
    source = old.source
    for relative in ('tools/model_correction_v25/reserve.py','tools/essential_fixes/execution.py',
                     'tools/annual_program/witness.py','tools/execution_recovery/numerics.py',
                     'tools/engineering_model/environment.py'):
        target=source/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        # Exercise the current recorder with an exact old14 scientific source;
        # reserve.py must not be copied from the new SF1 scientific checkout.
        raw=((Path(__file__).parents[1]/relative).read_bytes()
            if relative=='tools/annual_program/witness.py' else reviewed_source_bytes(relative))
        target.write_bytes(raw)
        old.bindings['source'][relative]=sha(target)
    scenario = dict(start_hour=1, duration_steps=2, step_minutes=15.)
    case = dict(id='synthetic_annual', scenario=scenario)
    old.bindings['git_commit'] = 'synthetic_fixture_only'
    identity = dict(case_id=case['id'], case_sha256=digest(case),
        source_sha256=digest(old.bindings['source']), input_sha256=digest(old.bindings['inputs']),
        git_commit=old.bindings['git_commit'])

    def persist(*, retained=False, active_cut=False, change=None, result_input_mapping=False, layout48=False,
                include_ambient=True):
        # Each test owns a new fixture directory. Calls to this function occur
        # once so the exclusive original journal writer remains authoritative.
        journal = BlockJournal(producer/'evidence', identity=identity)
        blocks, sidecars, call_count = [], [], 0
        for index in range(2):
            start = index*15.
            local_start = call_count if retained else 0
            segment = deepcopy(original['segments'][0])
            segment.update(start_minute=start, end_minute=start+15.,
                loads_kW=deepcopy(segment['connected_loads_kW']),
                disconnected_loads_kW=[[0.]*3 for _ in range(4)])
            if active_cut:
                segment['loads_kW'][0][1] += 7.
                segment['disconnected_loads_kW'][0][1] = 7.
                segment['unserved_kW'][0][1] = 7.
                segment['service_request_ledger']['original_demand_kW'][0][1] += 7.
                segment['service_request_ledger']['active_cut_kW'][0][1] = 7.
            segment['m24_cold_mode_selection']['candidates'][0]['solver_call_index'] = local_start
            calls = deepcopy(original['solver_calls'])
            for call in calls:
                call['time_minute'] = start
            if layout48:
                segment['m25_reserve']=dict(control_rule=dict(name='sustained_critical',design_minutes=60.))
                for owner in calls+[segment['m23_boiler_domains']['domains'][0]]:
                    for layer in owner['solver_backend_layers']:
                        for attempt in layer['attempts']:
                            attempt['raw_x'].append(.123)
                            attempt['validation']['integer_indices']=[3,4,5,42,44,45]
            basename = 'solver_rows_block_'+format(index, '06d')+'.jsonl.gz'
            calls[1]['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']['file'] = basename
            entries = deepcopy(old.sidecars)
            entries[0]['location'][0] = local_start+1
            if change is not None:
                change(index, segment, calls, entries)
            side_path = producer/basename
            with gzip.open(side_path, 'wt', encoding='utf8') as stream:
                for entry in entries:
                    stream.write(json.dumps(entry)+'\n')
            receipt = dict(path=str(side_path), closed=True, status='completed',
                partial_tail_possible=False, gzip_sha256=sha(side_path), entry_count=len(entries),
                row_count=sum(e['row_count'] for e in entries), durable_and_stream_verified_before_block_ack=True)
            domain = {name:dict(gzip_sha256=row['sha256'], gzip_bytes=row['bytes'],
                rows_sha256=segment['m23_boiler_domains']['domains'][0]['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']['rows_sha256'],
                row_count=1, durable_and_verified_before_block_ack=True)
                for name,row in old.result['domain_evidence']['files'].items()}
            block = dict(start_minute=start, end_minute=start+15., segments=[segment], solver_calls=calls,
                events=[], terminal={}, decision=dict(current_observation=dict(decision_time_min=start,
                    features=dict(outdoor_temperature_C=20.) if include_ambient else {})), solver_index_base=call_count,
                solver_call_local_start_index=local_start,
                solver_index_semantics='global original list index' if retained else 'block local; combine with solver_index_base',
                solver_row_sidecar=receipt, domain_evidence_files=domain)
            journal.append(block)
            blocks.append(block); sidecars.append(receipt)
            call_count+=len(calls)
        journal.close(status='completed', expected_end_minute=30.)
        result = dict(status='completed', requested_end_minute=30., observed_until_minute=30.,
            unknown_tail_minutes=0., solver_row_sidecars=sidecars, **identity)
        if result_input_mapping:
            # Original annual runner.py:481 stores the full input hash mapping;
            # its journal identity at line 488 stores the mapping's digest.
            result['input_sha256']=deepcopy(old.bindings['inputs'])
        for name,value in [('CASE.json',dict(case=case)), ('BINDINGS.json',old.bindings),
                ('RESULT.json',result), ('RESOLVED_CASE.json',dict(model_config=original['model_config']))]:
            (producer/name).write_text(json.dumps(value), encoding='utf8')
        return blocks
    return old, persist


@pytest.mark.parametrize('retained', [False, True])
def test_block_local_and_retained_global_indices_bind_same_native_meter(annual, retained):
    old, persist = annual
    blocks = persist(retained=retained)
    module = api()
    verified = verify_journal(old.producer/'evidence')
    context = module.prepare_steam_context(old.producer, source_root=old.source, verified_journal=verified)
    reports = [module.audit_steam_block(context, block, block_record=record)
        for block,record in zip(blocks,verified['raw_files_sha256'])]
    assert [r['status'] for r in reports] == ['passed', 'passed']
    assert [r['summary']['total_metered_waste_lb'] for r in reports] == [1.25, 1.25]
    assert reports[1]['segments'][0]['binding']['actual_global_solver_call_indices'] == [3]
    assert all(r['imputed_from_energy_remainder'] is False for r in reports)


def test_actual_cut_and_connected_unserved_are_distinct(annual):
    old,persist=annual
    persist(active_cut=True)
    report=api().audit_annual_block(old.producer,source_root=old.source,block_index=1)
    assert report['status']=='passed'
    assert report['segments'][0]['metered_waste_lbph']==5.
    assert report['segments'][0]['ports_lbph']['discard']==5.
    assert report['segments'][0]['ports_lb']['discard']==1.25
    assert report['service_binding_provenance']['source_observer_sha256']=='f3040f97a1331f072837d381615427833aa904f692c634f0d7bfe83d428e9eac'


@pytest.mark.parametrize('change,reason', [
    ('replacement','actual_interval_has_unresolved_replacement_solve'),
    ('start','active_recovery_or_GT_start_context_not_fully_exported')])
def test_unsupported_actual_selection_or_start_stays_unknown(annual,change,reason):
    old,persist=annual
    def mutate(index,segment,calls,entries):
        if change=='replacement':
            calls.append(dict(phase='m23_event_power_probe',time_minute=index*15.,dt_h=.25,status='success'))
        else:
            calls[1]['solver_backend_layers'][0]['attempts'][0]['raw_x'][44]=1.
    persist(change=mutate)
    report=api().audit_annual_block(old.producer,source_root=old.source,block_index=0)
    assert report['status']=='unknown'
    assert reason in report['segments'][0]['reasons']
    assert report['summary']['total_metered_waste_lb'] is None


@pytest.mark.parametrize('change', ['owner','sidecar_name','candidate_index','cut_identity'])
def test_contradictory_evidence_is_failed_without_zero_filling(annual,change):
    old,persist=annual
    def mutate(index,segment,calls,entries):
        if change=='owner':entries[0]['location'][0]=99
        elif change=='sidecar_name':calls[1]['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']['file']='foreign.jsonl.gz'
        elif change=='candidate_index':segment['m24_cold_mode_selection']['candidates'][0]['solver_call_index']=99
        elif change=='cut_identity':segment['unserved_kW'][0][1]=2.
    persist(change=mutate)
    report=api().audit_annual_block(old.producer,source_root=old.source,block_index=0)
    assert report['status']=='failed'
    assert report['summary']['total_metered_waste_lb'] is None
    assert report['segments'][0]['metered_waste_lbph'] is None


def test_supplied_block_cannot_replace_bound_raw(annual):
    old,persist=annual
    blocks=persist()
    module=api(); context=module.prepare_steam_context(old.producer,source_root=old.source)
    altered=deepcopy(blocks[0]);altered['segments'][0]['action']['boiler_steam_lbph']=9.
    report=module.audit_steam_block(context,altered,block_record=context['raw_files_sha256'][0])
    assert report['status']=='failed'
    assert 'supplied_block_differs_from_bound_raw' in report['reasons']


def test_post_context_source_or_producer_drift_is_failed(annual):
    old,persist=annual
    blocks=persist()
    module=api(); context=module.prepare_steam_context(old.producer,source_root=old.source)
    (old.producer/'BINDINGS.json').write_text('{}',encoding='utf8')
    report=module.audit_steam_block(context,blocks[0],block_record=context['raw_files_sha256'][0])
    assert report['status']=='failed'
    assert report['summary']['total_metered_waste_lb'] is None


def test_small_in_memory_raw_change_cannot_hide_within_arithmetic_tolerance(annual):
    old,persist=annual
    blocks=persist()
    module=api();context=module.prepare_steam_context(old.producer,source_root=old.source)
    altered=deepcopy(blocks[0]);altered['segments'][0]['action']['boiler_steam_lbph']=1e-10
    report=module.audit_steam_block(context,altered,block_record=context['raw_files_sha256'][0])
    assert report['status']=='failed'
    assert 'supplied_block_differs_from_bound_raw' in report['reasons']


def test_empty_substituted_block_is_failed_not_an_unknown_empty_sum(annual):
    old,persist=annual
    blocks=persist()
    module=api();context=module.prepare_steam_context(old.producer,source_root=old.source)
    altered=deepcopy(blocks[0]);altered['segments']=[]
    report=module.audit_steam_block(context,altered,block_record=context['raw_files_sha256'][0])
    assert report['status']=='failed'
    assert report['summary']['total_metered_waste_lb'] is None


def test_sidecar_corruption_after_context_is_a_failed_sha_check(annual):
    old,persist=annual
    blocks=persist()
    module=api();context=module.prepare_steam_context(old.producer,source_root=old.source)
    path=Path(blocks[0]['solver_row_sidecar']['path'])
    path.write_bytes(path.read_bytes()+b'corruption')
    report=module.audit_steam_block(context,blocks[0],block_record=context['raw_files_sha256'][0])
    assert report['status']=='failed'
    assert 'solver_sidecar_gzip_sha_mismatch' in report['reasons']


def test_missing_sidecar_stays_unknown_with_no_discard_integral(annual):
    old,persist=annual
    blocks=persist()
    module=api();context=module.prepare_steam_context(old.producer,source_root=old.source)
    Path(blocks[0]['solver_row_sidecar']['path']).unlink()
    report=module.audit_steam_block(context,blocks[0],block_record=context['raw_files_sha256'][0])
    assert report['status']=='unknown'
    assert 'missing_solver_row_sidecar' in report['reasons']
    assert report['summary']['total_metered_waste_lb'] is None


def test_native_result_input_hash_mapping_binds_to_journal_digest(annual):
    old,persist=annual
    persist(result_input_mapping=True)
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='passed'


def test_exact_m25_48_coordinate_layout_keeps_native_discard(annual):
    old,persist=annual
    persist(layout48=True,active_cut=True)
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='passed'
    assert report['segments'][0]['metered_waste_lbph']==5.
    assert report['primal_layout_provenance']['M25_48']['appended_coordinate']==47
    assert report['primal_layout_provenance']['M25_48']['appended_coordinate_is_discard'] is False


def test_m25_appended_coordinate_remains_in_full_primal_equivalence(annual):
    old,persist=annual
    def mutate(index,segment,calls,entries):
        calls[0]['solver_backend_layers'][0]['attempts'][0]['raw_x'][47]=.456
    persist(layout48=True,change=mutate)
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='unknown'
    assert 'no_complete_cold_candidate_chain_for_each_actual_primal' in report['segments'][0]['reasons']


def test_shifted_48_coordinate_layout_cannot_borrow_m25_mapping(annual):
    old,persist=annual
    def mutate(index,segment,calls,entries):
        calls[1]['solver_backend_layers'][0]['attempts'][0]['validation']['integer_indices']=[3,4,5,43,45,46]
    persist(layout48=True,change=mutate)
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='unknown'
    assert 'unsupported_48_coordinate_integer_layout' in report['segments'][0]['reasons']


def linked_replacement(index,segment,calls,entries):
    # The producer's final result carries candidate call 2 into both the
    # actual solve record and segment; fixed call 1 retains candidate call 0.
    previous=deepcopy(segment['m24_cold_mode_selection'])
    calls[1]['m24_cold_mode_selection']=previous
    candidate=deepcopy(calls[0]);calls.append(candidate)
    selection=deepcopy(previous)
    selection['candidates'][0]['solver_call_index']=2
    segment['m24_cold_mode_selection']=deepcopy(selection)
    segment['m22_mode_selection']=deepcopy(selection)
    actual=deepcopy(calls[1]);actual.update(phase='m23_event_power_probe',m24_cold_mode_selection=selection)
    row=deepcopy(entries[0]);row['record_id']='rows_000000002';row['location'][0]=3
    entries.append(row)
    actual['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']['record_id']=row['record_id']
    calls.append(actual)


def test_final_result_candidate_id_binds_replacement_without_numerical_guessing(annual):
    old,persist=annual
    persist(layout48=True,change=linked_replacement)
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='passed'
    binding=report['segments'][0]['binding']
    assert binding['actual_solver_call_indices']==[3]
    assert binding['execution_source_selection']['original_phase']=='m23_event_power_probe'
    assert binding['execution_source_selection']['numerical_match_used_to_select_actual_call'] is False


def test_terminal_endpoint_has_no_effect_on_interval_primal_selection(annual):
    old,persist=annual
    def mutate(index,segment,calls,entries):
        selection=deepcopy(segment['m24_cold_mode_selection'])
        segment['m22_mode_selection']=deepcopy(selection)
        calls[1]['m24_cold_mode_selection']=selection
        endpoint=deepcopy(calls[0]);endpoint['phase']='local_down_endpoint';calls.append(endpoint)
    persist(change=mutate)
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='passed'
    assert report['segments'][0]['binding']['actual_solver_call_indices']==[1]


def test_numerically_matching_replacement_without_result_id_link_remains_unknown(annual):
    old,persist=annual
    def mutate(index,segment,calls,entries):
        linked_replacement(index,segment,calls,entries)
        calls[3]['m24_cold_mode_selection']['candidates'][0]['solver_call_index']=0
    persist(change=mutate)
    report=api().audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='unknown'
    assert report['summary']['total_metered_waste_lb'] is None


def attach_actual_witness(old,index,segment,calls,entries,*,guard=True,corrupt=None):
    """Use the real logging sink, including its owned row IDs, in a fixture."""
    from types import SimpleNamespace
    from tools.annual_program.witness import record_actual_witness,FIELD
    from tools.sci_engineering.solver_log import SolverCallLog,SolverRowSidecar,iter_sidecar_entries
    local_start=segment['m24_cold_mode_selection']['candidates'][0]['solver_call_index']
    if guard:
        alternative=deepcopy(calls[0])
        alternative['solver_backend_layers'][-1]['attempts'][0]['raw_x'][13]+=6.821210263296962e-13
        calls.append(alternative)
        candidate=deepcopy(segment['m24_cold_mode_selection']['candidates'][0])
        candidate.update(solver_call_index=local_start+2,cold_mode='guard_witness',guard_probe=True)
        segment['m24_cold_mode_selection']['candidates'].append(candidate)
    segment['m22_mode_selection']=deepcopy(segment['m24_cold_mode_selection'])
    calls[1]['m24_cold_mode_selection']=deepcopy(segment['m24_cold_mode_selection'])
    actual_layers=deepcopy(calls[1]['solver_backend_layers'])
    validation=actual_layers[-1]['attempts'][0]['validation']
    validation.pop('rows_ref');validation['rows']=deepcopy(entries[0]['rows'])
    validation['integer_indices']=[3,4,5,42,44,45]
    calls[1]['solver_backend_layers']=deepcopy(actual_layers)
    result=dict(action=deepcopy(segment['action']),lost=deepcopy(segment['service_request_ledger']['connected_unserved_kW']),
        served=deepcopy(segment['service_request_ledger']['delivered_kW']),solver_backend_layers=actual_layers,
        m22_mode_selection=deepcopy(segment['m24_cold_mode_selection']),m23_boiler_domains=deepcopy(segment['m23_boiler_domains']))
    path=old.producer/('solver_rows_block_'+format(index,'06d')+'.jsonl.gz')
    writer=SolverRowSidecar(path);sink=SolverCallLog(writer=writer)
    list.extend(sink,[{} for _ in range(local_start)])  # Existing retained history, outside this block.
    for call in calls:sink.append(call)
    core=SimpleNamespace(time=segment['end_minute'],solver_calls=sink,env=SimpleNamespace(root=old.source))
    record_actual_witness(core,segment,result)
    writer.close()
    calls[:]=list(sink)[local_start:]
    entries[:]=list(iter_sidecar_entries(path))
    if corrupt is not None:corrupt(segment,calls,entries,FIELD)


@pytest.mark.parametrize('retained',[False,True])
def test_optin_exact_actual_witness_does_not_choose_a_matching_guard_alternative(annual,retained):
    old,persist=annual
    persist(retained=retained,layout48=True,active_cut=True,
        change=lambda i,s,c,e:attach_actual_witness(old,i,s,c,e))
    # Existing exact all-candidate rule and the historical result remain intact.
    legacy=api().audit_annual_block(old.producer,source_root=old.source,block_index=1)
    assert legacy['status']=='unknown'
    assert 'ambiguous_cold_candidate_unused_steam' in legacy['segments'][0]['reasons']
    selected=api().audit_annual_block(old.producer,source_root=old.source,block_index=1,use_actual_witness=True)
    assert selected['status']=='passed'
    assert selected['segments'][0]['metered_waste_lbph']==5.
    assert selected['segments'][0]['binding']['proof_kind']=='logged_final_actual_witness'
    assert selected['segments'][0]['binding']['alternative_candidates_used_to_select_primal'] is False


@pytest.mark.parametrize('change',['scalar','vector_sha','call_index','row_owner','source_contract',
    'boolean_start','boolean_call_time','boolean_layer_index','boolean_attempt_index','remainder_flag'])
def test_optin_witness_contradiction_is_failed_without_legacy_fallback(annual,change):
    old,persist=annual
    def corrupt(segment,calls,entries,field):
        receipt=segment[field];owner=receipt['solver_call_index']
        if change=='scalar':receipt['unused_steam_lbph']=6.
        elif change=='vector_sha':receipt['raw_x_sha256']='0'*64
        elif change=='call_index':receipt['solver_call_index']=1
        elif change=='row_owner':entries[-1]['location'][0]=1
        elif change=='source_contract':calls[owner]['annual_actual_witness']['original_native_source_binding']['source_sha256']['src/hospital_ies/solver_backend.py']='0'*64
        elif change=='boolean_start':receipt['start_minute']=False
        elif change=='boolean_call_time':calls[owner]['time_minute']=False
        elif change=='boolean_layer_index':receipt['logged_layer_index']=False
        elif change=='boolean_attempt_index':receipt['accepted_attempt_index']=False
        elif change=='remainder_flag':receipt['discard_imputed_from_remainder']=True
    persist(layout48=True,change=lambda i,s,c,e:attach_actual_witness(old,i,s,c,e,corrupt=corrupt))
    selected=api().audit_annual_block(old.producer,source_root=old.source,use_actual_witness=True)
    assert selected['status']=='failed'
    assert selected['summary']['total_metered_waste_lb'] is None


def test_optin_requires_new_witness_and_never_falls_back_on_absence(annual):
    old,persist=annual
    persist()
    legacy=api().audit_annual_block(old.producer,source_root=old.source)
    selected=api().audit_annual_block(old.producer,source_root=old.source,use_actual_witness=True)
    # Earlier optional enhancement behavior accepted a valid old witness when
    # the new field was absent. Explicit new-annual qualification now requires
    # the new receipt on every segment; the default old reader stays unchanged.
    assert legacy['status']=='passed'
    assert selected['status']=='unknown'
    assert selected['summary']['total_metered_waste_lb'] is None
    assert 'missing_final_actual_witness' in selected['segments'][0]['reasons']


def attach_steam_flow_witness(old,index,segment,calls,entries,*,starter=1.,recovery=0.,corrupt=None):
    """Pure electric starter fixture: GT100 = served90 + starter10 kW."""
    raw=calls[1]['solver_backend_layers'][-1]['attempts'][0]['raw_x']
    raw[43]=recovery;raw[44]=starter
    segment['action']['gt_start_electric_kW']=10.*starter
    segment['action']['station_auxiliary_kW']+=10.*starter
    segment['action']['recovery_cooling_kW']=recovery
    for matrix in (segment['loads_kW'],segment['connected_loads_kW'],
                   segment['service_request_ledger']['original_demand_kW'],
                   segment['service_request_ledger']['delivered_kW']):
        matrix[0][0]=90.
    segment['service_dependencies']['direct_electric_delivered_kW'][0]=90.
    attach_actual_witness(old,index,segment,calls,entries,guard=False,corrupt=corrupt)


@pytest.mark.parametrize('layout48',[False,True])
@pytest.mark.parametrize('retained',[False,True])
def test_flow_only_pure_electric_starter_has_owned_direct_discard_and_limited_scope(annual,layout48,retained):
    old,persist=annual
    persist(layout48=layout48,retained=retained,active_cut=True,
        change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e))
    module=api()
    legacy=module.audit_annual_block(old.producer,source_root=old.source,block_index=1)
    strict=module.audit_annual_block(old.producer,source_root=old.source,block_index=1,use_actual_witness=True)
    assert legacy['status']==strict['status']=='unknown'
    assert strict['segments'][0]['reasons']==['active_recovery_or_GT_start_context_not_fully_exported']
    flow=module.audit_annual_block(old.producer,source_root=old.source,block_index=1,steam_flow_only=True)
    assert flow['status']=='passed'
    for report in (flow,flow['segments'][0]):
        assert report['qualification_scope']=='algebraic_steam_flow_only'
        assert report['full_startup_context_validated'] is False
        assert report['electricity_validated'] is False
        assert report['whole_system_validated'] is False
    segment=flow['segments'][0]
    assert segment['metered_waste_lbph']==segment['ports_lbph']['discard']==5.
    assert segment['metered_waste_lb']==1.25
    assert segment['binding']['proof_kind']=='logged_final_actual_witness'
    assert segment['binding']['alternative_candidates_used_to_select_primal'] is False
    assert flow['imputed_from_energy_remainder'] is False
    # Explicit false is byte-for-byte the old strict/default contract.
    assert strict==module.audit_annual_block(old.producer,source_root=old.source,block_index=1,
        use_actual_witness=True,steam_flow_only=False)
    assert legacy==module.audit_annual_block(old.producer,source_root=old.source,block_index=1,steam_flow_only=False)


def test_flow_only_block_api_and_explicit_witness_option_are_consistent(annual):
    old,persist=annual
    blocks=persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e))
    module=api();context=module.prepare_steam_context(old.producer,source_root=old.source)
    block=module.audit_steam_block(context,blocks[0],block_record=context['raw_files_sha256'][0],steam_flow_only=True)
    convenience=module.audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True,use_actual_witness=True)
    assert block==convenience


def test_flow_only_requires_final_actual_witness_even_when_legacy_would_pass(annual):
    old,persist=annual
    persist()
    report=api().audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status']=='unknown'
    assert report['segments'][0]['reasons']==['missing_final_actual_witness']
    assert report['segments'][0]['full_startup_context_validated'] is False
    assert report['summary']['total_metered_waste_lb'] is None


@pytest.mark.parametrize('value',[-1.,-1e-12,1e-12,.5,1.])
def test_flow_only_any_positive_recovery_is_unknown_without_original_context(annual,value):
    old,persist=annual
    persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e,recovery=value))
    report=api().audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status']=='unknown'
    assert report['segments'][0]['reasons']==['steam_flow_only_requires_zero_recovery']
    assert report['summary']['total_metered_waste_lb'] is None


@pytest.mark.parametrize('value',[-1.,.5,2.,1.-5e-10])
def test_flow_only_rejects_unsupported_starter_binary_without_widening_tolerance(annual,value):
    old,persist=annual
    persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e,starter=value))
    report=api().audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status'] in ('failed','unknown')
    assert 'steam_flow_only_requires_exact_starter_binary' in report['segments'][0]['reasons']
    assert report['summary']['total_metered_waste_lb'] is None


@pytest.mark.parametrize('change',['missing_rows','wrong_rows','raw_sha','source','row_owner',
    'GT','HRSG','boiler','ABS','heat','cut','auxiliary','ambient'])
def test_flow_only_keeps_original_steam_row_source_vector_action_service_checks(annual,change):
    old,persist=annual
    def corrupt(segment,calls,entries,field):
        receipt=segment[field];owner=receipt['solver_call_index']
        call=calls[owner]
        if change=='missing_rows':call['solver_backend_layers'][0]['attempts'][0]['validation'].pop('rows_ref')
        elif change=='wrong_rows':call['annual_actual_witness']['original_steam_row']['activity']+=1.
        elif change=='raw_sha':receipt['raw_x_sha256']='0'*64
        elif change=='source':call['annual_actual_witness']['original_native_source_binding']['source_sha256']['tools/operating_response/physical_balance.py']='0'*64
        elif change=='row_owner':entries[-1]['location'][0]=1
        elif change=='GT':segment['action']['gt_electric_kW'][0]+=1.
        elif change=='HRSG':segment['action']['hrsg_steam_lbph']+=1.
        elif change=='boiler':segment['action']['boiler_steam_lbph']+=1.
        elif change=='ABS':segment['action']['absorption_cooling_kW']+=1.
        elif change=='heat':segment['service_request_ledger']['delivered_kW'][0][1]-=1.
        elif change=='cut':segment['unserved_kW'][0][1]+=1.
        elif change=='auxiliary':segment['service_dependencies']['direct_electric_delivered_kW'][0]-=1.
        elif change=='ambient':segment['availability']['HRSG_1']=.5
    persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e,corrupt=corrupt))
    report=api().audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status'] in ('failed','unknown')
    assert report['summary']['total_metered_waste_lb'] is None
    assert report['segments'][0]['metered_waste_lbph'] is None


def test_flow_only_native_service_prefix_drift_is_explicit_unknown(annual,monkeypatch):
    old,persist=annual
    persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e))
    module=api()
    assert module.audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)['status']=='passed'
    monkeypatch.setattr(module,'_FLOW_NATIVE_ACTION_AST_SHA','0'*64,raising=False)
    report=module.audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status']=='unknown'
    assert report['segments'][0]['reasons']==['unsupported_native_steam_flow_prefix_version']


@pytest.mark.parametrize('change',['bound','activity','unit','missing'])
def test_flow_only_rehashed_owned_rows_still_need_original_steam_physics(annual,change):
    old,persist=annual
    def corrupt(segment,calls,entries,field):
        receipt=segment[field];call=calls[receipt['solver_call_index']]
        attempt=call['solver_backend_layers'][0]['attempts'][receipt['accepted_attempt_index']]
        ref=attempt['validation']['rows_ref']
        entry=next(row for row in entries if row['record_id']==ref['record_id'])
        row=next(row for row in entry['rows'] if row['name']=='balance.steam')
        if change=='bound':row['lower']+=1.
        elif change=='activity':row['activity']+=1.
        elif change=='unit':row['unit']='kW'
        elif change=='missing':row['name']='unrelated.row'
        entry['rows_sha256']=jsha(entry['rows'])
        ref['rows_sha256']=entry['rows_sha256']
        receipt['rows_ref']=deepcopy(ref)
        call['annual_actual_witness']['original_steam_row']=deepcopy(row)
    persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e,corrupt=corrupt))
    report=api().audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status'] in ('failed','unknown')
    assert report['summary']['total_metered_waste_lb'] is None


def test_flow_only_missing_original_ambient_is_unknown(annual):
    old,persist=annual
    persist(layout48=True,include_ambient=False,
        change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e))
    report=api().audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status']=='unknown'
    assert report['segments'][0]['reasons']==['missing_current_ambient_observation']


def test_flow_only_no_legacy_selection_no_global_patch_and_input_unchanged(annual,monkeypatch):
    old,persist=annual
    blocks=persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e))
    module=api();context=module.prepare_steam_context(old.producer,source_root=old.source)
    original=deepcopy(blocks[0]);native_function=module.native._action_service
    def forbidden(*args,**kwargs):raise AssertionError('flow-only called legacy action matching')
    monkeypatch.setattr(module,'_execution_selection',forbidden)
    monkeypatch.setattr(module,'_segment_auditor',forbidden)
    report=module.audit_steam_block(context,blocks[0],block_record=context['raw_files_sha256'][0],steam_flow_only=True)
    assert report['status']=='passed'
    assert blocks[0]==original
    assert module.native._action_service is native_function


def test_flow_only_unavailable_actual_receipt_does_not_fall_back(annual):
    old,persist=annual
    def corrupt(segment,calls,entries,field):
        segment[field].update(status='unknown',reasons=['no_unique_final_attempt'])
    persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e,corrupt=corrupt))
    report=api().audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status']=='unknown'
    assert report['segments'][0]['reasons']==['actual_witness_unavailable:no_unique_final_attempt']


def test_flow_only_self_consistent_waste_hash_and_row_cannot_replace_port_closure(annual):
    old,persist=annual
    def corrupt(segment,calls,entries,field):
        from tools.annual_program.witness import vector_sha
        receipt=segment[field];call=calls[receipt['solver_call_index']]
        attempt=call['solver_backend_layers'][0]['attempts'][receipt['accepted_attempt_index']]
        attempt['raw_x'][13]+=1.
        metadata=call['annual_actual_witness']
        metadata['raw_x_sha256']=receipt['raw_x_sha256']=vector_sha(attempt['raw_x'])
        metadata['unused_steam_lbph']=receipt['unused_steam_lbph']=attempt['raw_x'][13]
        ref=attempt['validation']['rows_ref']
        entry=next(row for row in entries if row['record_id']==ref['record_id'])
        row=next(row for row in entry['rows'] if row['name']=='balance.steam')
        row['activity']-=1.
        entry['rows_sha256']=jsha(entry['rows'])
        ref['rows_sha256']=entry['rows_sha256']
        receipt['rows_ref']=deepcopy(ref)
        metadata['original_steam_row']=deepcopy(row)
    persist(layout48=True,change=lambda i,s,c,e:attach_steam_flow_witness(old,i,s,c,e,corrupt=corrupt))
    report=api().audit_annual_block(old.producer,source_root=old.source,steam_flow_only=True)
    assert report['status']=='failed'
    assert report['segments'][0]['reasons']==['observable_full_steam_balance_failure']
    assert report['summary']['total_metered_waste_lb'] is None
