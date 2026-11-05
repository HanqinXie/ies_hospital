"""Bounded active-EC and conditional active-boiler mechanism evidence.

The boiler pair shares a declared GT-out background. It is not an isolated
boiler-fault annual result. Case construction starts no environment or solver.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from runtime_checks import CheckError, canonical_sha256, read_json, sha256, write_new
from scripts.execution_profile import MODEL_ID, require_result

START, END, HORIZON = 60.25, 120.25, 180.
BASELINE_COMMIT = '60602c5795887a74092b115619d96a32d19fb0b5'
BASELINE_SOURCE = '3ff19036947ad53217694560f6d464827258f122ef36c1291aff43fd63fc6925'
BASELINE_INPUT = 'f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15'


def legacy_mechanism_plan():
    return dict(schema='hospital_ies.busy_tear.mechanism_plan.v1', blind_or_unseen=False,
        original_baseline=dict(commit=BASELINE_COMMIT, source_sha256=BASELINE_SOURCE, input_sha256=BASELINE_INPUT),
        method='M_ready', configuration='H2', horizon_minutes=HORIZON,
        ec=dict(source_hour=4137, target='EC_1', healthy_reference_must_produce_before_and_during_fault=True,
            known_seen_engineering_background=True, capacity_or_policy_changed=False),
        boiler=dict(source_hour=1, target='BOILER_1', common_background='all_GT_unavailable_0_to_150.25min',
            isolated_boiler_annual_result=False, requires_actual_positive_pre_event_steam=True,
            original_total_rating_lbph=3000., fault_available_rating_lbph=2000., capacity_or_policy_changed=False))


def legacy_mechanism_cases():
    sys.path[:0] = [str(PACKAGE / 'source'), str(PACKAGE / 'source' / 'src')]
    from tools.annual_program.registration import make_case
    def build(mode, hour, events=None):
        return make_case('M_ready', mode=mode, start_hour=hour, duration_minutes=HORIZON, calendar=events)
    conditioning = [dict(time_minute=0., availability={name: 0. for name in ('GT_1', 'GT_2', 'GT_3')}),
        dict(time_minute=150.25, availability={name: 1. for name in ('GT_1', 'GT_2', 'GT_3')})]
    boiler_fault = [copy.deepcopy(conditioning[0]), dict(time_minute=START, availability={'BOILER_1': 0.}),
        dict(time_minute=END, availability={'BOILER_1': 1.}), copy.deepcopy(conditioning[1])]
    return dict(ec_healthy=build('N', 4137), ec_fault=build('F', 4137,
        [dict(time_minute=START, availability={'EC_1': 0.}), dict(time_minute=END, availability={'EC_1': 1.})]),
        boiler_healthy=build('F', 1, conditioning), boiler_fault=build('F', 1, boiler_fault))


def mechanism_plan():
    return dict(schema='hospital_ies.annual_dynamic.mechanism_plan.v1',execution_model_id=MODEL_ID,
        blind_or_unseen=False,method='M_ready',configuration='H2',horizon_minutes=HORIZON,
        ec=dict(source_hour=4137,target='EC_1',requires_actual_positive_pre_event_output=True),
        boiler=dict(source_hour=1,target='BOILER_1',common_background='healthy_GT',
            isolated_boiler_fault=True,requires_actual_positive_pre_event_steam=True,
            original_total_rating_lbph=3000.,fault_available_rating_lbph=2000.,isolated_boiler_annual_result=False),
        tower=dict(source_hour=4137,target='TOWER_1',single_of_four_existing_slots=True,
            service_improvement_required=False),
        pump=dict(source_hour=5072,target='EC_1_PRIMARY_PUMP',raw_compressor_healthy=True,
            functional_interlock='EC_1',requires_actual_positive_pre_event_EC1=True),
        capacity_load_threshold_or_policy_changed=False,physical_service_improvement_required=False)


def mechanism_cases():
    sys.path[:0]=[str(PACKAGE/'source'),str(PACKAGE/'source/src')]
    from tools.annual_program.registration import make_case
    cases={}
    for kind,hour,target in (('ec',4137,'EC_1'),('boiler',1,'BOILER_1'),
                             ('tower',4137,'TOWER_1'),('pump',5072,'EC_1_PRIMARY_PUMP')):
        for phase in ('healthy','fault'):
            events=[] if phase=='healthy' else [dict(time_minute=START,availability={target:0.}),
                                               dict(time_minute=END,availability={target:1.})]
            cases[kind+'_'+phase]=make_case('M_ready',mode='N' if phase=='healthy' else 'F',
                start_hour=hour,duration_minutes=HORIZON,calendar=events,execution_revision=MODEL_ID,numerical_profile='engineering_fast_v1')
    return cases


def _number(value, name, *, nonnegative=True):
    if type(value) not in (int, float) or not math.isfinite(value) or (nonnegative and value < 0.):
        raise CheckError('Missing or invalid actual mechanism quantity: ' + name)
    return float(value)


def _vector(value, name):
    if not isinstance(value, list) or len(value) != 3:
        raise CheckError('Exactly three actual converter outputs required: ' + name)
    return [_number(item, name) for item in value]


def _actual_segments(kind, rows, fault):
    if kind not in ('ec', 'boiler','tower','pump') or not isinstance(rows, list) or not rows:
        raise CheckError('Registered actual mechanism segments are required')
    previous = 0.
    target = {'ec':'EC_1','boiler':'BOILER_1','tower':'TOWER_1','pump':'EC_1'}[kind]
    boundaries = {0.}
    for row in rows:
        a, b = (_number(row.get(key), key) for key in ('start_minute', 'end_minute'))
        if a != previous or b <= a or b > HORIZON:
            raise CheckError('Actual mechanism coverage must be contiguous without gaps or overlap')
        boundaries.add(b)
        previous = b
        if fault and any(a < boundary < b for boundary in (START, END)):
            raise CheckError('Fault boundary crosses an unsplit actual physical interval')
        expected = {name: 1. for name in ('GT_1', 'GT_2', 'GT_3', 'EC_1', 'EC_2', 'EC_3',
            'BOILER_1', 'BOILER_2', 'BOILER_3', 'GRID', 'TOWER', 'ABS')}
        if kind=='tower':expected.update({f'TOWER_{i}':1. for i in (1,2,3,4)})
        if fault and START <= a < END:
            expected[target] = 0.
        for label in ('hardware_availability', 'availability'):
            actual = row.get(label)
            if not isinstance(actual, dict):
                raise CheckError('Actual hardware/effective availability is missing')
            for name, value in expected.items():
                observed=_number(actual.get(name),name)
                # Dynamic support readiness reduces executable common support;
                # raw hardware remains exact and independently audited.
                support_factor=label=='availability' and name in ('TOWER','AUX_CHW_PUMP')
                if (observed>value if support_factor else observed!=value):
                    raise CheckError('Actual mechanism availability differs from the registered calendar: ' + name)
        action = row.get('action')
        if not isinstance(action, dict):
            raise CheckError('Actual mechanism action is missing')
        _vector(action.get('electric_chiller_cooling_kW'), 'electric_chiller_cooling_kW')
        _vector(action.get('gt_electric_kW'), 'gt_electric_kW')
        boiler = _number(action.get('boiler_steam_lbph'), 'boiler_steam_lbph')
        capacity = 1000. * sum(row['availability'][name] for name in ('BOILER_1', 'BOILER_2', 'BOILER_3'))
        # The original carrier observer checks exact physics independently;
        # this positive-capacity check never widens its original tolerances.
        if boiler > capacity + 1e-9:
            raise CheckError('Actual boiler steam exceeds its registered available capacity')
    if previous != HORIZON or (fault and not {START, END} <= boundaries):
        raise CheckError('Actual mechanism boundary coverage is incomplete')
    return rows


def _integral(rows, value, start, end):
    return math.fsum(value(row) * max(0., min(end, row['end_minute']) - max(start, row['start_minute'])) / 60.
        for row in rows)


def evaluate_segments(kind, healthy_segments, fault_segments):
    healthy = _actual_segments(kind, healthy_segments, False)
    fault = _actual_segments(kind, fault_segments, True)
    # EPS is the unchanged physical-state zero definition. This gate proves
    # active branches and actual port execution, not a new service threshold.
    sys.path.insert(0, str(PACKAGE / 'source' / 'src'))
    from hospital_ies.state_transition import EPS
    result = dict(status='passed', kind=kind, fault_interval_minutes=[START, END],
        horizon_minutes=HORIZON, initial_state_and_exogenous_background_matched=True,
        exact_recorded_pre_event_state_equality_claimed=False, state_interpolation_performed=False,
        physical_service_improvement_required=False, clinical_or_OEM_qualification=False)
    if kind in ('ec','pump'):
        value = lambda row: row['action']['electric_chiller_cooling_kW'][0]
        healthy_pre = _integral(healthy, value, 0., START)
        fault_pre = _integral(fault, value, 0., START)
        healthy_active = _integral(healthy, value, START, END)
        actual_fault = _integral(fault, value, START, END)
        if min(healthy_pre, fault_pre, healthy_active) <= EPS:
            raise CheckError('Independent EC requires active actual pre-event and healthy fault-window exposure')
        maximum = max(value(row) for row in fault if row['start_minute'] < END and row['end_minute'] > START)
        if maximum > EPS:
            raise CheckError('Unavailable EC_1 has positive actual output during its fault')
        result.update(source_hour=4137 if kind=='ec' else 5072, healthy_pre_event_EC1_kWh_c=healthy_pre,
            fault_pre_event_EC1_kWh_c=fault_pre, healthy_fault_window_EC1_kWh_c=healthy_active,
            fault_window_EC1_kWh_c=actual_fault, fault_window_maximum_EC1_kW_c=maximum,
            isolated_EC_calendar=kind=='ec', single_primary_pump_cause=kind=='pump',known_seen_engineering_background=True)
    elif kind=='boiler':
        value = lambda row: row['action']['boiler_steam_lbph']
        healthy_pre, fault_pre = (_integral(rows, value, 0., START) for rows in (healthy, fault))
        healthy_active = _integral(healthy, value, START, END)
        if min(healthy_pre, fault_pre, healthy_active) <= EPS:
            raise CheckError('Boiler mechanism requires active actual steam exposure before and during the counterfactual fault')
        result.update(source_hour=1, healthy_pre_event_steam_lb=healthy_pre, fault_pre_event_steam_lb=fault_pre,
            healthy_fault_window_steam_lb=healthy_active, fault_window_steam_lb=_integral(fault, value, START, END),
            available_capacity_lbph=dict(before=3000., during=2000., after=3000.),
            common_background='healthy_GT', isolated_boiler_fault=True, isolated_boiler_annual_result=False)
    else:
        value=lambda row:row['action']['tower_rejection_kW']
        healthy_pre,fault_pre=(_integral(rows,value,0.,START) for rows in (healthy,fault))
        if min(healthy_pre,fault_pre,_integral(healthy,value,START,END))<=EPS:
            raise CheckError('Single tower mechanism requires active actual heat rejection exposure')
        result.update(source_hour=4137,healthy_pre_event_rejection_kWh=healthy_pre,
            fault_pre_event_rejection_kWh=fault_pre,single_of_four_slots=True,
            original_total_capacity_unchanged=True,remaining_hardware_fraction=.75,
            zero_service_difference_is_accepted=True)
    return result


def validate_saved_case(saved, name):
    expected = mechanism_cases()[name]
    from tools.annual_program.runner import _configuration
    from tools.annual_readiness.registration import load_registry
    if (not isinstance(saved, dict) or set(saved) != {'case', 'configuration'}
            or saved.get('case') != expected
            or saved.get('configuration') != _configuration(expected, load_registry())):
        raise CheckError('Mechanism producer case/configuration is not its exact registered bounded case')
    return saved['case']


def _read_run(name, output, source_root):
    from tools.annual_readiness.journal import verify_journal
    from tools.sci_engineering.runner import source_binding, input_binding
    import subprocess
    source = Path(source_root).resolve()
    if source != (PACKAGE / 'source').resolve():
        raise CheckError('Mechanism source root differs from this package')
    output = Path(output).resolve()
    case = validate_saved_case(read_json(output / 'CASE.json'), name)
    binding = read_json(output / 'BINDINGS.json')
    head = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True, capture_output=True, check=True).stdout.strip()
    current = dict(source=source_binding(source), inputs=input_binding(source), git_commit=head)
    if binding != current:
        raise CheckError('Mechanism producer source/input/Git binding differs')
    provenance = read_json(PACKAGE / 'config' / 'SOURCE_PROVENANCE.json')
    if (provenance.get('source') != current['source'] or provenance.get('inputs') != current['inputs']
            or canonical_sha256(current['inputs']) != BASELINE_INPUT
            or provenance.get('git_commit') != head):
        raise CheckError('Mechanism provenance is not the current frozen source and unchanged original inputs')
    case_sha = canonical_sha256(case)
    result = read_json(output / 'RESULT.json')
    require_result(result)
    if (result.get('status') != 'completed' or result.get('observed_until_minute') != HORIZON
            or result.get('unknown_tail_minutes') != 0. or type(result.get('reset_count')) is not int
            or result['reset_count'] != 1 or result.get('source_and_inputs_unchanged') is not True
            or result.get('source_sha256') != canonical_sha256(current['source'])
            or result.get('input_sha256') != current['inputs'] or result.get('git_commit') != head
            or result.get('case_sha256') != case_sha):
        raise CheckError('Mechanism producer is incomplete or has a different scientific identity')
    verified = verify_journal(output / 'evidence')
    identity = verified['identity']
    expected_identity = dict(case_id=case['id'], case_sha256=case_sha, git_commit=head,
        input_sha256=canonical_sha256(current['inputs']), source_sha256=canonical_sha256(current['source']),
        model_id=case['annual_program']['model_id'])
    if identity != expected_identity or verified['observed_until_minute'] != HORIZON:
        raise CheckError('Mechanism raw journal identity or coverage differs')
    rows = []
    files = []
    for name in ('CASE.json', 'BINDINGS.json', 'RESULT.json', 'evidence/IDENTITY.json', 'evidence/SUMMARY.json', 'evidence/blocks.jsonl'):
        files.append(dict(path=str(output / name), sha256=sha256(output / name)))
    for record in verified['raw_files_sha256']:
        path = output / 'evidence' / record['file']
        with gzip.open(path, 'rt', encoding='utf8') as stream:
            block = json.load(stream)
        rows.extend(block['segments'])
        files.append(dict(path=str(path), sha256=record['sha256']))
    return rows, dict(producer=str(output), case_sha256=case_sha,
        source_sha256=canonical_sha256(current['source']), input_sha256=canonical_sha256(current['inputs']),
        git_commit=head, files=files)


def verify_mechanism_pair(kind, healthy_output, fault_output, *, source_root=None):
    if kind not in ('ec', 'boiler','tower','pump'):
        raise CheckError('Unknown bounded mechanism')
    source = Path(source_root or PACKAGE / 'source').resolve()
    sys.path[:0] = [str(source), str(source / 'src')]
    healthy, healthy_proof = _read_run(kind + '_healthy', healthy_output, source)
    fault, fault_proof = _read_run(kind + '_fault', fault_output, source)
    report = evaluate_segments(kind, healthy, fault)
    report.update(schema='hospital_ies.busy_tear.active_mechanism.v1',
        validation_script_sha256=sha256(__file__), plan=mechanism_plan(), healthy=healthy_proof, fault=fault_proof)
    report['signature'] = canonical_sha256(report)
    return report


def mechanism_commands(package, folder):
    """Exact registered process commands; no process is created here."""
    from scripts.server_validation import producer_command
    package, base = Path(package).resolve(), Path(folder).resolve() / 'mechanisms'
    result = {}
    for name in mechanism_cases():
        result['mechanism_' + name] = producer_command(base / (name + '_CASE.json'), base / name,
            base / (name + '.stop'), base / (name + '_vault'))
        result['mechanism_account_' + name] = [sys.executable, '-B', str(package / 'scripts/audit_engineering_year.py'),
            '--source', str(package / 'source'), '--producer', str(base / name), '--output', str(base / ('account_' + name))]
    return result


def run_mechanism_checks(package, work, folder, runs):
    """Four serial real bounded producers and their four independent audits."""
    from scripts.server_validation import verify_account
    package, base = Path(package).resolve(), Path(folder).resolve() / 'mechanisms'
    base.mkdir(exist_ok=False)
    write_new(base / 'PLAN.json', mechanism_plan())
    cases = mechanism_cases()
    commands = mechanism_commands(package, folder)
    for name, case in cases.items():
        write_new(base / (name + '_CASE.json'), case)
        runs.command('mechanism_' + name, commands['mechanism_' + name])
        runs.command('mechanism_account_' + name, commands['mechanism_account_' + name])
        account = base / ('account_' + name)
        verify_account(package, account, base / name, read_json(account / 'RESULT.json'))
    for kind in ('ec', 'boiler','tower','pump'):
        proof = verify_mechanism_pair(kind, base / (kind + '_healthy'), base / (kind + '_fault'),
            source_root=package / 'source')
        write_new(base / (kind.upper() + '_PAIR.json'), proof)
    return verify_mechanism_checks(package, folder)


def verify_mechanism_checks(package, folder):
    """Recompute each proof from actual raw journals; summaries cannot qualify."""
    from scripts.server_validation import verify_account
    package, base = Path(package).resolve(), Path(folder).resolve() / 'mechanisms'
    if read_json(base / 'PLAN.json') != mechanism_plan():
        raise CheckError('Active mechanism qualification plan differs')
    cases = mechanism_cases()
    for name, case in cases.items():
        if read_json(base / (name + '_CASE.json')) != case:
            raise CheckError('Active mechanism case file differs')
        account = base / ('account_' + name)
        verify_account(package, account, base / name, read_json(account / 'RESULT.json'))
    for kind in ('ec', 'boiler','tower','pump'):
        expected = verify_mechanism_pair(kind, base / (kind + '_healthy'), base / (kind + '_fault'),
            source_root=package / 'source')
        if read_json(base / (kind.upper() + '_PAIR.json')) != expected:
            raise CheckError('Active mechanism proof does not match actual raw physical evidence')
    return dict(status='passed', execution_model_id=MODEL_ID, active_independent_EC=True, active_independent_boiler=True,
        single_tower_verified=True, single_primary_pump_verified=True,
        eight_actual_bounded_producers=True, eight_independent_carrier_accounts=True,
        boiler_isolated_annual_result=False, scope=mechanism_plan())
