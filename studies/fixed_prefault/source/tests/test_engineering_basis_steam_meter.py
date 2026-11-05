"""Adversarial original-primal metering contracts; no simulator is run."""
from copy import deepcopy
import gzip
import hashlib
import importlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from reviewed_source_fixtures import reviewed_source_bytes


SOURCE_FILES = (
    'tools/operating_response/physical_balance.py', 'src/hospital_ies/solver_backend.py',
    'tools/model_correctness_v23/domains.py', 'tools/model_closure_v22/environment.py',
    'tools/engineering_completion_v24/cold_modes.py', 'tools/engineering_completion_v24/environment.py',
    'tools/model_correctness_v23/environment.py', 'src/hospital_ies/state_transition.py',
    'tools/sci_engineering/solver_log.py', 'tools/model_correctness_v23/evidence.py',
    'src/hospital_ies/model.py', 'src/hospital_ies/control_env.py',
    'tools/model_closure_v22/physical.py', 'configs/plant_configs.json',
    'src/hospital_ies/solver_diagnostics.py', 'tools/model_correctness_v23/numerics.py',
    'tools/model_closeout/numerics.py')


def api():
    path = Path(__file__).parents[1] / 'tools/engineering_basis_study/steam_meter.py'
    assert path.is_file(), 'accepted-primal steam meter is not implemented'
    return importlib.import_module('tools.engineering_basis_study.steam_meter')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsha(value, sorted_keys=False):
    return hashlib.sha256(json.dumps(value, sort_keys=sorted_keys, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode()).hexdigest()


@pytest.fixture
def case(tmp_path):
    repo = Path(__file__).parents[1]
    source = tmp_path / 'source'
    producer = tmp_path / 'producer'
    producer.mkdir()
    bindings = {'source': {}, 'inputs': {}}
    for relative in SOURCE_FILES:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # This is a legacy producer fixture. The output-only annual capture
        # release changes the leaf/backend hashes; copying a current release
        # would silently turn the old-source regression into a mixed producer.
        frozen={
            'tools/operating_response/physical_balance.py':('legacy_physical_balance.py.txt','fe13fe96a58c0f8816a38419074e4a5c12b65e0207b0d37e85a2d8abf82a76f5'),
            'src/hospital_ies/solver_backend.py':('legacy_solver_backend.py.txt','14d3aa2a179cdf00c9e6e43d224bde31f1ed832abeb05c692516029a6406b4d7')}
        if relative in frozen:
            original=repo/'tests/fixtures/annual_program'/frozen[relative][0]
            assert sha(original)==frozen[relative][1]
            raw=original.read_bytes()
        else:
            # M23 environment is a new scientific revision in the current tree;
            # an old producer must retain exact historical bytes throughout.
            raw=reviewed_source_bytes(relative)
        target.write_bytes(raw)
        bindings['source'][relative] = sha(target)
    cfg = json.loads((source/'configs/plant_configs.json').read_text(encoding='utf8'))
    perf = cfg['historical_pnnl_performance']
    original = cfg['source_equipment']['gas_turbines']['rated_electric_kW_each']
    temp = 20.
    fahrenheit = temp*1.8+32.
    rated = original*.4*(1.23836-.00404*fahrenheit)
    steam_ratio = perf['hrsg_steam_exponential_constant_lb_per_hr']*math.exp(
        perf['hrsg_steam_exponential_exponent_per_kW']*original)*.4/rated
    hrsg = 100.*steam_ratio
    fuel = 100.*(29477.-1954.9*math.log(original))*.9851*(.910320+.00152*fahrenheit)/1e6
    x = [0.]*47
    x[0], x[3], x[13] = 100., 1., 5.
    action = dict(gt_electric_kW=[100., 0., 0.], grid_import_kW=0., diesel_kW=0.,
        absorption_cooling_kW=0., electric_chiller_cooling_kW=[0., 0., 0.],
        boiler_steam_lbph=0., hrsg_steam_lbph=hrsg, tower_rejection_kW=0., gt_fuel_MMBtu_h=fuel,
        ups_discharge_kW=0., ups_charge_kW=0., cold_discharge_kW=0., cold_charge_kW=0.,
        cold_storage_pump_kW=0., boiler_auxiliary_kW=0., gt_start_electric_kW=0.,
        boiler_restart_electric_kW=0., station_auxiliary_kW=0., recovery_cooling_kW=0.,
        recovery_pump_electric_kW=0., recovery_fan_electric_kW=0., boiler_station_running=False)
    loads = [[100., (hrsg-5.)*.30*.95, 0.]] + [[0.]*3 for _ in range(3)]
    lost = [[0.]*3 for _ in range(4)]
    rows = [dict(index=1, name='balance.steam', unit='lb/h', kind='physical',
                 activity=hrsg-5., lower=hrsg-5., upper=hrsg-5., violation=0.)]
    cas_sha = jsha(rows, True)
    domain_path = producer/'domain_evidence'/f'{cas_sha}.json.gz'
    domain_path.parent.mkdir()
    with gzip.open(domain_path, 'wt', encoding='utf8') as f:
        json.dump(rows, f)
    cas_ref = dict(schema='hospital_ies.m23.validation_rows.reference.v1', directory='domain_evidence',
        file=domain_path.name, rows_sha256=cas_sha, row_count=1)
    def attempt(ref):
        return dict(accepted=True, status=0, native_status=2, raw_x=deepcopy(x),
            objective_consistent=True, error=None, validation=dict(rows_ref=deepcopy(ref),
                max_original_violation=0., max_lock_violation=0., max_integrality_distance=0.))
    def layer(ref):
        return [dict(layer='cost', rank=0, backend='gurobipy', status=0, attempts=[attempt(ref)])]
    side_ref = dict(schema='hospital_ies.solver_validation_rows.reference.v1', file='solver_rows.jsonl.gz',
        record_id='rows_000000001', rows_sha256=jsha(rows), row_count=1)
    candidate = dict(status='legal', action=deepcopy(action), unserved_kW=deepcopy(lost), solver_call_index=0)
    segment = dict(start_minute=0., end_minute=15., command_id=1,
        action=deepcopy(action), connected_loads_kW=deepcopy(loads), unserved_kW=deepcopy(lost),
        availability={f'HRSG_{i}': 1. for i in (1,2,3)},
        m22_station=dict(boiler_ready_before_actual_interval=True),
        service_request_ledger=dict(original_demand_kW=deepcopy(loads), delivered_kW=deepcopy(loads),
            active_cut_kW=deepcopy(lost), connected_unserved_kW=deepcopy(lost)),
        service_dependencies=dict(auxiliary_required_kW=deepcopy(lost), auxiliary_delivered_kW=deepcopy(lost),
            direct_electric_delivered_kW=[100.,0.,0.,0.]),
        m24_cold_mode_selection=dict(candidates=[candidate]),
        m23_boiler_domains=dict(domains=[dict(domain='off', status='legal', solver_backend_layers=layer(cas_ref))]),
        steam_balance_residual_lbph=0.)
    payload = dict(segments=[segment], solver_calls=[
        dict(time_minute=0., dt_h=.25, phase='m24_cold_mode_candidate', status='success', solver_backend_layers=layer(cas_ref)),
        dict(time_minute=0., dt_h=.25, phase='fixed_execution', status='success', solver_backend_layers=layer(side_ref))],
        decisions=[dict(decision_time_min=0., features=dict(outdoor_temperature_C=temp))],
        model_config=dict(sci_engineering=dict(cold_pump_kW_per_kW=.02),
            model_closure_v22=dict(station=dict(boiler_fixed_kW=.5, boiler_kW_per_kWsteam=.012, gt_starter_kW_per_unit=10.))),
        operating_execution_contract={}, summary=dict(requested_end_minute=15.))
    sidecars = [dict(schema='hospital_ies.solver_validation_rows.sidecar.v1', record_id='rows_000000001',
        location=[1,'solver_backend_layers',0,'attempts',0,'validation'], rows=rows,
        rows_sha256=jsha(rows), row_count=1)]
    # Producer completion has an explicit requested/observed endpoint contract;
    # prior fixture omitted it although the real native RESULT always has it.
    result = dict(status='completed', unknown_tail_minutes=0., requested_end_minute=15.,
        observed_until_minute=15., domain_evidence=dict(files={
        domain_path.relative_to(producer).as_posix(): dict(sha256=sha(domain_path), bytes=domain_path.stat().st_size)}))
    def persist():
        with gzip.open(producer/'trajectory.json.gz', 'wt', encoding='utf8') as f:
            json.dump(payload, f)
        with gzip.open(producer/'solver_rows.jsonl.gz', 'wt', encoding='utf8') as f:
            for entry in sidecars:
                f.write(json.dumps(entry, ensure_ascii=False)+'\n')
        result['raw_sha256'] = sha(producer/'trajectory.json.gz')
        result['solver_row_sidecar'] = dict(closed=True,status='completed',partial_tail_possible=False,
            gzip_sha256=sha(producer/'solver_rows.jsonl.gz'),entry_count=len(sidecars),
            row_count=sum(e['row_count'] for e in sidecars))
        (producer/'RESULT.json').write_text(json.dumps(result), encoding='utf8')
        (producer/'BINDINGS.json').write_text(json.dumps(bindings), encoding='utf8')
    def audit():
        persist()
        return api().audit_steam_meter(payload, bindings=bindings, producer_directory=producer, source_root=source)
    return SimpleNamespace(payload=payload, producer=producer, source=source, bindings=bindings,
        result=result, sidecars=sidecars, audit=audit, persist=persist)


def test_direct_native_waste_is_read_then_closure_checked(case):
    out=case.audit()
    assert out['status']=='passed'
    assert out['segments'][0]['metered_waste_lbph']==5.
    assert out['summary']['total_metered_waste_lb']==1.25
    assert out['segments'][0]['steam_residual_lbph']==pytest.approx(0.,abs=1e-12)
    assert out['imputed_from_energy_remainder'] is False


@pytest.mark.parametrize('change,expected',[
    ('missing_x','unknown'), ('unaccepted','unknown'), ('wrong_time','unknown'),
    ('wrong_action','failed'), ('wrong_service','failed'), ('wrong_sha','failed'),
    ('sidecar_location','failed'), ('source_sha','failed'), ('missing_source','unknown'),
    ('new_storage','failed'), ('replacement','unknown'), ('candidate_only','unknown'),
    ('candidate_mismatch','unknown'), ('missing_domain','unknown'), ('nonfinite','failed'),
    ('nonfinite_action','failed'), ('bool_waste','failed'), ('negative_waste','failed')])
def test_missing_or_contradictory_proof_never_becomes_a_zero_meter(case,change,expected):
    p=case.payload;s=p['segments'][0];a=p['solver_calls'][1]['solver_backend_layers'][0]['attempts'][0]
    if change=='missing_x':del a['raw_x']
    elif change=='unaccepted':a['accepted']=False
    elif change=='wrong_time':p['solver_calls'][1]['time_minute']=1.
    elif change=='wrong_action':s['action']['grid_import_kW']=1.
    elif change=='wrong_service':s['unserved_kW'][0][1]=1.
    elif change=='wrong_sha':a['validation']['rows_ref']['rows_sha256']='0'*64
    elif change=='sidecar_location':case.sidecars[0]['location'][0]=99
    elif change=='source_sha':case.bindings['source']['src/hospital_ies/model.py']='0'*64
    elif change=='missing_source':(case.source/'src/hospital_ies/model.py').unlink()
    elif change=='new_storage':
        s['action']['cold_discharge_kW']=1.
        s['m24_cold_mode_selection']['candidates'][0]['action']['cold_discharge_kW']=1.
    elif change=='replacement':p['solver_calls'].append(dict(time_minute=0.,dt_h=.25,phase='m23_event_power_probe',status='success'))
    elif change=='candidate_only':p['solver_calls'][1]['phase']='m24_cold_mode_candidate'
    elif change=='candidate_mismatch':s['m24_cold_mode_selection']['candidates'][0]['action']['grid_import_kW']=1.
    elif change=='missing_domain':s['m23_boiler_domains']['domains']=[]
    elif change=='nonfinite':a['raw_x'][13]=float('inf')
    elif change=='nonfinite_action':s['action']['grid_import_kW']=float('nan')
    elif change=='bool_waste':a['raw_x'][13]=True
    elif change=='negative_waste':a['raw_x'][13]=-1.
    out=case.audit()
    assert out['status']==expected
    assert out['segments'][0]['metered_waste_lbph'] is None
    assert out['summary']['total_metered_waste_lb'] is None
    assert out['segments'][0]['reasons']


def test_source_bindings_required_even_if_native_numbers_look_valid(case):
    case.persist()
    out=api().audit_steam_meter(case.payload,bindings={},producer_directory=case.producer,source_root=case.source)
    assert out['status']=='unknown'
    assert out['summary']['total_metered_waste_lb'] is None


def test_raw_binding_cannot_be_bypassed_with_an_in_memory_payload(case):
    case.persist()
    altered=deepcopy(case.payload);altered['segments'][0]['action']['boiler_steam_lbph']=9.
    out=api().audit_steam_meter(altered,bindings=case.bindings,producer_directory=case.producer,source_root=case.source)
    assert out['status']=='failed'


def test_equivalent_complete_actual_evidence_lists_every_index(case):
    p=case.payload
    extra=deepcopy(p['solver_calls'][1]);ref=extra['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']
    ref['record_id']='rows_000000002';entry=deepcopy(case.sidecars[0]);entry['record_id']=ref['record_id'];entry['location'][0]=2
    case.sidecars.append(entry);p['solver_calls'].append(extra)
    candidate=deepcopy(p['segments'][0]['m24_cold_mode_selection']['candidates'][0])
    p['segments'][0]['m24_cold_mode_selection']['candidates'].append(candidate)
    out=case.audit();b=out['segments'][0]['binding']
    assert out['status']=='passed'
    assert b['mode']=='equivalent'
    assert b['actual_solver_call_indices']==[1,2]
    assert b['cold_candidate_indices']==[0,1]
    assert b['selected_unique'] is False


def test_different_waste_in_duplicate_actual_evidence_is_unknown(case):
    extra=deepcopy(case.payload['solver_calls'][1]);extra['solver_backend_layers'][0]['attempts'][0]['raw_x'][13]=6.
    case.payload['solver_calls'].append(extra)
    out=case.audit()
    assert out['status']=='unknown'
    assert out['segments'][0]['metered_waste_lbph'] is None


def test_non_equivalent_matching_cold_candidates_cannot_hide_a_choice(case):
    p=case.payload;s=p['segments'][0]
    extra=deepcopy(p['solver_calls'][0]);extra['solver_backend_layers'][0]['attempts'][0]['raw_x'][13]=6.
    p['solver_calls'].append(extra)
    candidate=deepcopy(s['m24_cold_mode_selection']['candidates'][0]);candidate['solver_call_index']=2
    s['m24_cold_mode_selection']['candidates'].append(candidate)
    assert case.audit()['status']=='unknown'


def test_empty_or_invalid_raw_never_passes(case):
    p=case.payload;p['segments']=[]
    assert case.audit()['status']=='unknown'


def test_sidecar_tampering_is_failed_not_missing(case):
    case.sidecars[0]['rows'][0]['activity']+=1.
    assert case.audit()['status']=='failed'


def test_accepted_wrong_steam_balance_is_failed_not_a_new_waste(case):
    a=case.payload['solver_calls'][1]['solver_backend_layers'][0]['attempts'][0]
    a['raw_x'][13]=6.
    # Keep candidate and legal domain coherent; source known consumption is
    # unchanged, so this must be an observable closure failure, not imputation.
    case.payload['solver_calls'][0]['solver_backend_layers'][0]['attempts'][0]['raw_x'][13]=6.
    case.payload['segments'][0]['m23_boiler_domains']['domains'][0]['solver_backend_layers'][0]['attempts'][0]['raw_x'][13]=6.
    out=case.audit()
    assert out['status']=='failed'
    assert out['segments'][0]['metered_waste_lbph'] is None


def test_duplicate_actual_interval_cannot_double_the_integrated_meter(case):
    case.payload['segments'].append(deepcopy(case.payload['segments'][0]))
    out=case.audit()
    assert out['status']=='failed'
    assert out['summary']['total_metered_waste_lb'] is None


def test_unproven_domain_for_one_equivalent_actual_primal_stays_unknown(case):
    # A zero-required auxiliary fraction can differ without changing exported
    # action or service. Both actual primals still need their domain witness.
    p=case.payload;extra=deepcopy(p['solver_calls'][1])
    extra['solver_backend_layers'][0]['attempts'][0]['raw_x'][26]=.5
    ref=extra['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']
    ref['record_id']='rows_000000002';entry=deepcopy(case.sidecars[0])
    entry['record_id']=ref['record_id'];entry['location'][0]=2
    case.sidecars.append(entry);p['solver_calls'].append(extra)
    out=case.audit()
    assert out['status']=='unknown'
    assert out['summary']['total_metered_waste_lb'] is None


def test_selected_primal_requires_its_complete_cold_candidate_chain(case):
    p=case.payload;extra=deepcopy(p['solver_calls'][1])
    extra['solver_backend_layers'][0]['attempts'][0]['raw_x'][26]=.5
    ref=extra['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']
    ref['record_id']='rows_000000002';entry=deepcopy(case.sidecars[0]);entry['record_id']=ref['record_id'];entry['location'][0]=2
    p['solver_calls'].append(extra);case.sidecars.append(entry)
    domain=deepcopy(p['segments'][0]['m23_boiler_domains']['domains'][0])
    domain['solver_backend_layers'][0]['attempts'][0]['raw_x'][26]=.5
    p['segments'][0]['m23_boiler_domains']['domains'].append(domain)
    assert case.audit()['status']=='unknown'


def test_boolean_actual_clock_is_not_a_numeric_zero(case):
    case.payload['solver_calls'][1]['time_minute']=False
    assert case.audit()['status']=='failed'


def test_missing_original_solver_validation_source_is_unknown(case):
    (case.source/'src/hospital_ies/solver_diagnostics.py').unlink()
    assert case.audit()['status']=='unknown'


@pytest.mark.parametrize('change,expected',[
    ('payload_requested','failed'),('result_requested','failed'),('result_observed','failed'),
    ('truncated_complete','failed'),('missing_payload_requested','unknown'),
    ('missing_result_requested','unknown'),('missing_result_observed','unknown'),
    ('boolean_requested','failed')])
def test_actual_tail_must_match_every_required_producer_endpoint(case,change,expected):
    if change=='payload_requested':case.payload['summary']['requested_end_minute']=30.
    elif change=='result_requested':case.result['requested_end_minute']=30.
    elif change=='result_observed':case.result['observed_until_minute']=30.
    elif change=='truncated_complete':
        case.payload['summary']['requested_end_minute']=30.
        case.result.update(requested_end_minute=30.,observed_until_minute=30.)
    elif change=='missing_payload_requested':del case.payload['summary']['requested_end_minute']
    elif change=='missing_result_requested':del case.result['requested_end_minute']
    elif change=='missing_result_observed':del case.result['observed_until_minute']
    elif change=='boolean_requested':case.result['requested_end_minute']=True
    out=case.audit()
    assert out['status']==expected
    assert out['summary']['total_metered_waste_lb'] is None


@pytest.mark.parametrize('clock,expected',[(False,'failed'),(-1.,'failed'),('0','failed'),(None,'unknown')])
def test_current_ambient_decision_clock_is_validated_before_selection(case,clock,expected):
    if clock is None:del case.payload['decisions'][0]['decision_time_min']
    else:case.payload['decisions'][0]['decision_time_min']=clock
    out=case.audit()
    assert out['status']==expected
    assert out['segments'][0]['metered_waste_lbph'] is None
    assert out['summary']['total_metered_waste_lb'] is None


def candidate_ordered_row(case,*,owner):
    # Reuse the real row bytes, but give the cold-candidate attempt its own
    # ordered record. Identical row content does not make ownership optional.
    proof=case.payload['solver_calls'][0]['solver_backend_layers'][0]['attempts'][0]
    entry=deepcopy(case.sidecars[0]);entry['record_id']='rows_000000002'
    if owner is None:del entry['location']
    else:entry['location'][0]=owner
    proof['validation']['rows_ref']=deepcopy(case.payload['solver_calls'][1]['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref'])
    proof['validation']['rows_ref']['record_id']=entry['record_id']
    case.sidecars.append(entry)


@pytest.mark.parametrize('duplicates',[False,True])
def test_cold_candidate_cannot_borrow_an_actual_calls_ordered_rows(case,duplicates):
    candidate_ordered_row(case,owner=1)  # Candidate owner must be call 0.
    if duplicates:
        records=case.payload['segments'][0]['m24_cold_mode_selection']['candidates']
        records.append(deepcopy(records[0]))
    out=case.audit()
    assert out['status']=='failed'
    assert out['summary']['total_metered_waste_lb'] is None


def test_cold_candidate_missing_ordered_owner_is_unknown(case):
    candidate_ordered_row(case,owner=None)
    out=case.audit()
    assert out['status']=='unknown'
    assert out['summary']['total_metered_waste_lb'] is None


def test_cold_candidate_correct_ordered_owner_remains_supported(case):
    candidate_ordered_row(case,owner=0)
    out=case.audit()
    assert out['status']=='passed'
    assert out['segments'][0]['metered_waste_lbph']==5.


def test_domain_without_call_owner_cannot_borrow_ordered_actual_rows(case):
    # Domain CAS is content-addressed and has no ordered solver-call owner.
    # An ordered reference cannot replace it without a verifiable location.
    domain=case.payload['segments'][0]['m23_boiler_domains']['domains'][0]
    domain['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref']=deepcopy(
        case.payload['solver_calls'][1]['solver_backend_layers'][0]['attempts'][0]['validation']['rows_ref'])
    out=case.audit()
    assert out['status']=='unknown'
    assert out['summary']['total_metered_waste_lb'] is None
