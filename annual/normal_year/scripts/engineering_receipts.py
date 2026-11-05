"""Pure, explicit consumers for engineering_fast_v1; never imports an optimizer.

Numerical acceptance is separate from clinical/service failure definitions.
Unknown fields, clocks, event/queue order and discrete outcomes stay exact.
"""
from copy import deepcopy
import hashlib
import json
import math
import re


ENGINEERING_MATCH_SCHEMA = 'hospital_ies.annual_dynamic.engineering_recovery_comparison.v1'
_PROFILE = dict(schema='hospital_ies.engineering_numerical_profile.v1', profile='engineering_fast_v1',
    tolerances=dict(physical_rows_bounds=1e-7, normalized_locks=1e-10, integrality=1e-9, nonlinear_equality_MMBtu_h=1e-7),
    native_parameters=dict(FeasibilityTol=1e-9, OptimalityTol=1e-9, IntFeasTol=1e-9),
    native_execution_policy='unchanged_fresh_native_no_warm_start',
    native_acceptance_policy='unchanged_original',
    candidate_gate=dict(physical_rows_bounds=1e-7,normalized_locks=1e-10,integrality=1e-9),
    initialization_policy='unchanged_exact_reference_before_engineering_execution',
    planner_policy='unchanged_exact_command_generation',
    validation_detail_policy='zero_natural_bound_current_domain_maxima_else_full_rows',
    objective_bound_abs_tolerance=1e-10, mip_gap_policy='caller_gap_unchanged_for_every_layer',
    candidate_policy='linear_nonnegative_exact_zero_natural_bound_and_exact_zero_objective_exact_original_gate_only',
    nonlinear_policy='unchanged_exact_nonlinear_fresh_native_no_candidate_reuse',
    state_policy='process_thread_local_volatile_not_checkpointed_no_physical_rounding',
    max_native_models=32, max_candidate_families=256, candidates_per_family=3)
_HASH = re.compile('[0-9a-f]{64}')
_ATTEMPT = re.compile(r'(?:^|\.)solver_backend_layers\[\d+\]\.attempts\[\d+\]$')
_PLANNER_COPY = re.compile(r'^block\.(?:decision\.command_generated|events\[\d+\]\.command|'
    r'terminal\.execution_state\.active_command)\.joint_dispatch\.diagnostic\.m23_boiler_domains(?:\.|$)')
_ZERO_INTERNAL_PHASES=frozenset(('m24_cold_mode_candidate','m24_current_port_selection',
    'm22_current_port_selection','m25_selected_no_storage_witness',
    'm22_non_DG_release_witness','m23_sustained_non_DG_release_witness'))
_FINAL_COST_ANCHOR_PHASES=frozenset(('fixed_execution','after_actual_shedding','local_event_split',
    'm23_event_power_probe','diesel_domain_split','protection_threshold_split','local_down_endpoint'))


def canonical_profile(): return deepcopy(_PROFILE)


def json_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('ascii')).hexdigest()


def _finite(value): return type(value) in (int, float) and math.isfinite(value)


def validated_numerical_profile(case, result=None, resolved=None):
    profile = case.get('numerical_execution')
    if profile is None:
        if ((result is not None and any(k in result for k in ('numerical_profile', 'numerical_execution')))
                or (resolved is not None and 'numerical_execution' in resolved)):
            raise ValueError('Numerical execution metadata has no canonical CASE declaration')
        return None
    if profile != _PROFILE: raise ValueError('Numerical execution profile differs from engineering_fast_v1')
    if result is not None and (result.get('numerical_profile') != profile['profile']
            or result.get('numerical_execution') != profile):
        raise ValueError('Producer numerical profile differs from canonical CASE')
    if resolved is not None and resolved.get('numerical_execution') != profile:
        raise ValueError('Resolved numerical profile differs from canonical CASE')
    return deepcopy(profile)


def validate_acceptance(proof, profile, *, optimizer_called, native_optimal_status_claimed):
    if profile != _PROFILE or not isinstance(proof, dict): raise ValueError('Explicit canonical engineering profile/receipt required')
    if (proof.get('schema') != 'hospital_ies.engineering_acceptance.v1'
            or proof.get('profile') != profile['profile'] or proof.get('original_domain_checked') is not True
            or proof.get('physical_state_replaced') is not False
            or proof.get('validation_thresholds') != profile['tolerances']
            or proof.get('effective_validation_thresholds') != (profile['tolerances'] if optimizer_called is True else profile['candidate_gate'])
            or not isinstance(proof.get('current_domain_sha256'), str)
            or _HASH.fullmatch(proof['current_domain_sha256']) is None):
        raise ValueError('Engineering receipt has no current original-domain/profile binding')
    checked = proof.get('validation_summary', {})
    for name, tolerance in (('max_original_violation', profile['tolerances']['physical_rows_bounds']),
            ('max_lock_violation', profile['tolerances']['normalized_locks']),
            ('max_integrality_distance', profile['tolerances']['integrality'])):
        if not _finite(checked.get(name)) or not 0. <= checked[name] <= tolerance:
            raise ValueError('Engineering receipt violates its declared original-domain tolerance: ' + name)
    quality = proof.get('optimality', {})
    if (quality.get('previous_problem_bound_used') is not False
            or type(quality.get('native_optimal_status_claimed')) is not bool
            or quality['native_optimal_status_claimed'] is not native_optimal_status_claimed):
        raise ValueError('Engineering receipt borrows an old bound or misstates native ownership')
    if (any(not _finite(quality.get(k)) for k in ('objective','lower_bound','gap_upper_bound'))
            or quality['gap_upper_bound']<0.
            or abs(quality['gap_upper_bound']-max(0.,quality['objective']-quality['lower_bound']))
                > 1e-12*max(1.,abs(quality['objective']),abs(quality['lower_bound']))
            or quality.get('objective_bound_abs_tolerance')!=profile['objective_bound_abs_tolerance']):
        raise ValueError('Engineering objective/lower-bound/gap certificate is internally inconsistent')
    if optimizer_called is True:
        if proof.get('mode') != 'native_optimization' or native_optimal_status_claimed is not True:
            raise ValueError('Native engineering result requires actual native optimizer ownership')
    elif optimizer_called is False:
        if (native_optimal_status_claimed is not False
                or proof.get('mode') != 'current_domain_natural_bound'
                or quality['objective']!=0. or quality['lower_bound']!=0.
                or abs(quality['objective']-quality['lower_bound'])>profile['objective_bound_abs_tolerance']
                or not _finite(quality.get('gap_upper_bound'))
                or not 0. <= quality['gap_upper_bound'] <= profile['objective_bound_abs_tolerance']):
            raise ValueError('Skipped optimizer lacks its current objective-bound proof')
    else: raise ValueError('Actual optimizer_called boolean required')
    return proof


def validate_attempt(attempt, profile):
    if (attempt.get('accepted') is not True or type(attempt.get('status')) is not int
            or attempt['status'] != 0 or attempt.get('error') is not None):
        raise ValueError('Only a successful accepted current engineering attempt may be projected')
    native = attempt.get('optimizer_called')
    if (native is True and attempt.get('native_status') != 2) or (native is False and attempt.get('native_status') is not None):
        raise ValueError('Cached success must not impersonate native OPTIMAL')
    validate_acceptance(attempt.get('engineering_acceptance'), profile, optimizer_called=native,
        native_optimal_status_claimed=native)
    raw = attempt.get('raw_x')
    if not isinstance(raw, list) or not raw or not all(_finite(v) for v in raw):
        raise ValueError('Engineering accepted raw vector is missing/nonfinite')
    if attempt.get('objective_consistent') is not True: raise ValueError('Engineering objective consistency is missing')
    parameters = attempt.get('parameters')
    if not isinstance(parameters, dict): raise ValueError('Engineering attempt parameters must remain explicit')
    if native:
        allowed = {'OutputFlag','Threads','Method','Seed','DualReductions','FeasibilityTol','IntFeasTol',
            'OptimalityTol','MIPGap','MIPGapAbs','NumericFocus','Presolve','TimeLimit'}
        if set(parameters) - allowed or any(parameters.get(k) != v for k, v in profile['native_parameters'].items()):
            raise ValueError('Engineering native numerical parameters differ from the registered policy')
        if (parameters.get('Threads') != 1 or parameters.get('MIPGapAbs') != 0.
                or not _finite(parameters.get('MIPGap')) or not 0. <= parameters['MIPGap'] <= 1e-6
                or not _finite(parameters.get('TimeLimit')) or not 0. < parameters['TimeLimit'] <= 8.):
            raise ValueError('Engineering layer exceeds its original native thread/gap/time budget')
    elif parameters: raise ValueError('Skipped optimizer cannot claim actual native parameter execution')
    return attempt


def registered_exact_planner_call(value,profile):
    if value.get('phase')!='sci_upper_planning':return False
    if (profile!=_PROFILE or profile.get('planner_policy')!='unchanged_exact_command_generation'
            or value.get('upper_optimizer_called') is not True or value.get('status')!='success'
            or not _finite(value.get('time_minute')) or not isinstance(value.get('solver_backend_layers'),list)):
        raise ValueError('Exact planner ownership needs its registered explicit sci_upper_planning call')
    return True


def validate_exact_planner_attempt(attempt,profile):
    """Only the explicitly registered planner phase may use these exact records."""
    if (profile!=_PROFILE or profile.get('planner_policy')!='unchanged_exact_command_generation'
            or attempt.get('accepted') is not True or type(attempt.get('status')) is not int or attempt['status']!=0
            or type(attempt.get('native_status')) is not int or attempt['native_status']!=2
            or attempt.get('error') is not None or attempt.get('objective_consistent') is not True
            or any(k in attempt for k in ('engineering_acceptance','optimizer_called','engineering_profile'))):
        raise ValueError('Exact planner attempt cannot borrow an engineering success or skip receipt')
    raw=attempt.get('raw_x');p=attempt.get('parameters',{});check=attempt.get('validation',{})
    if (not isinstance(raw,list) or not raw or not all(_finite(v) for v in raw)
            or any(p.get(k)!=1e-9 for k in ('FeasibilityTol','OptimalityTol','IntFeasTol'))
            or p.get('Threads')!=1 or p.get('MIPGapAbs')!=0.
            or not _finite(p.get('MIPGap')) or not 0.<=p['MIPGap']<=1e-6
            or not _finite(p.get('TimeLimit')) or not 0.<p['TimeLimit']<=8.
            or not _finite(attempt.get('mip_gap')) or not 0.<=attempt['mip_gap']<=1e-6):
        raise ValueError('Exact planner vector/native numerical policy is missing or relaxed')
    def strict(validation):
        return all(_finite(validation.get(k)) and 0.<=validation[k]<=limit for k,limit in (
            ('max_original_violation',1e-7),('max_lock_violation',1e-10),('max_integrality_distance',1e-9)))
    if not strict(check):raise ValueError('Exact planner original residual exceeds the exact threshold')
    nl=attempt.get('nonlinear_validation')
    if nl is not None and (nl.get('accepted') is not True or not strict(nl.get('true_validation',{}))
            or not _finite(nl.get('raw_nonlinear_max_residual')) or not 0.<=nl['raw_nonlinear_max_residual']<=1e-7):
        raise ValueError('Exact planner analytic SF2 residual/lock acceptance is missing or relaxed')
    return attempt


def validate_actual_owner(row, profile):
    owner = row.get('annual_dynamic_actual_owner', {})
    if (owner.get('schema') != 'hospital_ies.annual_dynamic.accepted_engineering_owner.v1'
            or owner.get('model_id') != 'annual_responsive_dynamic_bridge_20261104.v1'
            or owner.get('accepted_sf2_path_receipt_present') is not True
            or owner.get('legacy_annual_native_witness_claimed') is not False
            or type(owner.get('dynamic_commit_count')) is not int or owner['dynamic_commit_count'] != 1
            or 'annual_program_actual_witness' in row): raise ValueError('Actual engineering owner is old/mixed/uncommitted')
    if profile is None:
        if (owner.get('result_owner') != 'accepted_native_optimizer_with_dynamic_limits'
                or any(k in owner for k in ('numerical_profile', 'engineering_acceptance', 'optimizer_called'))):
            raise ValueError('Engineering numerical owner cannot qualify an exact CASE')
        return owner
    if (owner.get('result_owner') != 'engineering_solver_with_current_domain_validation'
            or owner.get('numerical_profile') != profile['profile']):
        raise ValueError('Actual engineering owner profile differs from its CASE')
    validate_acceptance(owner.get('engineering_acceptance'), profile, optimizer_called=owner.get('optimizer_called'),
        native_optimal_status_claimed=owner.get('native_optimal_status_claimed'))
    meter = row.get('sf2_running_fuel', {}); native = meter.get('native_result_owner', {})
    raw = meter.get('accepted_raw_x')
    if (meter.get('accounting_committed') is not True or not isinstance(raw, list) or len(raw) not in (47,48)
            or not all(_finite(v) for v in raw) or meter.get('native_vector_dimension') != len(raw)
            or meter.get('accepted_raw_x_sha256') != json_sha(raw)
            or native.get('full_raw_x_sha256') != json_sha(raw)
            or native.get('full_vector_exact_identity') is not True
            or native.get('primal_selected_by_GP_or_action_matching') is not False
            or native.get('layer') != 'cost' or native.get('backend') != 'gurobipy'
            or any(native.get(k) != owner.get(k) for k in ('optimizer_called', 'engineering_acceptance', 'native_optimal_status_claimed'))):
        raise ValueError('Engineering actual owner does not bind the identical complete SF2 vector/receipt')
    return owner


def _nonactual_zero_service_summary(attempt,profile):
    proof=attempt['engineering_acceptance'];q=proof['optimality'];v=attempt.get('validation',{})
    if (q['objective']!=0. or q['lower_bound']!=0. or attempt.get('raw_objective')!=0.
            or v.get('schema')!='hospital_ies.engineering_current_domain_validation_summary.v1'
            or v.get('all_current_rows_checked') is not True or v.get('raw_rows_retained') is not False
            or v.get('raw_rows_recoverable') is not False or v.get('objective_coefficients_nonnegative') is not True
            or v.get('nonlinear_domain') is not False or v.get('current_objective')!=0.
            or v.get('current_natural_lower_bound')!=0. or v.get('current_domain_sha256')!=proof['current_domain_sha256']
            or v.get('validation_thresholds')!=profile['tolerances'] or v.get('effective_validation_thresholds')!=profile['candidate_gate']):return False
    for count,checked in (('row_count','original_rows_checked'),('variable_count','original_variables_checked')):
        if type(v.get(count)) is not int or v[count]<0 or v.get(checked)!=v[count]:return False
    if v['variable_count']!=len(attempt['raw_x']):return False
    return all(v.get(key)==value for key,value in proof['validation_summary'].items())


def _has_fresh_final_cost_anchor(call,profile):
    """Admit only intermediate service vectors; the final cost vector stays."""
    if call.get('phase') not in _FINAL_COST_ANCHOR_PHASES or call.get('status')!='success':return False
    layers=call.get('solver_backend_layers')
    if not isinstance(layers,list) or not layers:return False
    if [i for i,layer in enumerate(layers) if isinstance(layer,dict) and layer.get('layer')=='cost']!=[len(layers)-1]:return False
    for layer in layers:
        if (not isinstance(layer,dict) or type(layer.get('status')) is not int or layer['status']!=0
                or layer.get('backend')!='gurobipy' or not isinstance(layer.get('attempts'),list) or not layer['attempts']
                or any(not isinstance(a,dict) or a.get('accepted') is not True or type(a.get('status')) is not int
                    or a['status']!=0 or a.get('error') is not None for a in layer['attempts'])):return False
    attempts=layers[-1]['attempts']
    if len(attempts)!=1:return False
    final=attempts[0]
    if (final.get('optimizer_called') is not True or type(final.get('native_status')) is not int or final['native_status']!=2
            or final.get('engineering_model_reused',False) is not False
            or final.get('engineering_acceptance',{}).get('mode')!='native_optimization'):return False
    try:validate_attempt(final,profile)
    except (ValueError,TypeError,KeyError):return False
    return True


def project_engineering(value, path, profile, ignored, _exact_planner=False,_zero_internal=False,_zero_service_layer=False,_zero_anchor=False):
    """Only authenticated performance receipts are excluded; raw primal stays."""
    if profile != _PROFILE: raise ValueError('Unknown engineering projection profile')
    if (_PLANNER_COPY.search(path) and profile.get('planner_policy')=='unchanged_exact_command_generation'):
        # Independent command diagnostic copies are not admitted as new solver
        # evidence. Retain their full contents for the unchanged legacy timing
        # projection/comparison; main explicit planner calls are checked below.
        return deepcopy(value)
    if isinstance(value, list):
        return [project_engineering(v, path + f'[{i}]', profile, ignored,_exact_planner,_zero_internal,_zero_service_layer,_zero_anchor) for i, v in enumerate(value)]
    if not isinstance(value, dict): return value
    if isinstance(value.get('solver_backend_layers'),list):
        _zero_anchor=_has_fresh_final_cost_anchor(value,profile)
        _zero_internal=(value.get('phase') in _ZERO_INTERNAL_PHASES and value.get('status')=='success'
            and value.get('inventory_committed') is False) or _zero_anchor
    if isinstance(value.get('attempts'),list):
        _zero_service_layer=type(value.get('layer')) is int and value['layer'] in (0,1,2,3)
    if registered_exact_planner_call(value,profile):_exact_planner=True
    if 'annual_dynamic_actual_owner' in value: validate_actual_owner(value, profile)
    attempt = bool(_ATTEMPT.search(path))
    excluded = set()
    projected_zero=False
    if attempt and value.get('accepted') is True:
        if _exact_planner:validate_exact_planner_attempt(value,profile)
        else:
            validate_attempt(value, profile)
            excluded = {'optimizer_called','engineering_acceptance','engineering_profile','engineering_model_reused',
                'engineering_candidate_search','native_status','mip_dual_bound','mip_gap'}
            projected_zero=(_zero_internal and _zero_service_layer and _nonactual_zero_service_summary(value,profile))
            if projected_zero:excluded.add('raw_x')
    elif path.endswith(('.annual_dynamic_actual_owner', '.sf2_running_fuel.native_result_owner')):
        validate_acceptance(value.get('engineering_acceptance'), profile, optimizer_called=value.get('optimizer_called'),
            native_optimal_status_claimed=value.get('native_optimal_status_claimed'))
        excluded = {'optimizer_called','engineering_acceptance','native_optimal_status_claimed'}
        if path.endswith('.native_result_owner'): excluded.add('full_raw_x_sha256')
    elif path.endswith('.sf2_running_fuel'):
        if value.get('accepted_raw_x_sha256') != json_sha(value.get('accepted_raw_x')):
            raise ValueError('SF2 raw vector SHA differs before engineering comparison')
        excluded.add('accepted_raw_x_sha256')
    copied = {}
    if projected_zero:copied['verified_zero_service_domain_sha256']=value['engineering_acceptance']['current_domain_sha256']
    for key, item in value.items():
        if key in excluded:
            ignored.add((re.sub(r'\[\d+\]', '[]', path + '.' + key),
                ('authenticated_zero_service_primal_with_fresh_final_cost_anchor' if _zero_anchor else
                 'authenticated_nonactual_exact_zero_service_primal') if key=='raw_x' and projected_zero
                else 'authenticated_engineering_optimizer_evidence'))
            continue
        if attempt and not _exact_planner and value.get('accepted') is True and key == 'parameters':
            copied[key] = {}
            ignored.add((re.sub(r'\[\d+\]', '[]', path + '.parameters'), 'validated_engineering_native_execution_parameters'))
        else: copied[key] = project_engineering(item, path + '.' + key, profile, ignored,_exact_planner,_zero_internal,_zero_service_layer,_zero_anchor)
    return copied


def comparison_policy():
    return dict(schema='hospital_ies.engineering_recovery_numeric_policy.v1', profile='engineering_fast_v1',
        raw_primal_absolute=1e-6, instantaneous_rate_absolute=1e-6, normalized_lock_absolute=1e-9,
        inventory_absolute_kWh=.01, temperature_absolute_C=.01, critical_service_absolute_kW=1e-6,
        event_queue_clock_tolerance_minutes=0., unknown_numeric_tolerance=0.,
        intermediate_zero_primal_projection='registered_nonactual_or_fresh_final_cost_anchored_P0_P3_exact_zero_summary_identical_domain_SHA',
        source_case_inputs_and_discrete_outcomes='exact', per_segment_energy_absolute_kWh=1e-6)


def comparison_statistics():
    return dict(difference_count=0, differences=[], checked_numeric_values=0,
        maximum_absolute_numeric_difference=0., allowed_numeric_difference_count=0,
        maximum_allowed_difference_by_budget={})


def _budget(path, unit):
    name = path.rsplit('.', 1)[-1].split('[')[0].lower(); lower = path.lower()
    if any(word in name for word in ('minute','timestamp','elapsed','duration','delay','clock','hour','count','index','rank')):
        return 0., 'strict_clock_or_counter'
    if name.endswith('_c') and ('temp' in name or 'temperature' in lower): return .01, 'temperature_C'
    if 'temperature' in name: return .01, 'temperature_C'
    if ('stock_kwh' in name or name in ('stock_kwh','diesel_energy_remaining_kwh')
            or (name in ('initial_kwh','final_kwh','energy_kwh') and any(x in lower for x in ('m21_storage','ups','cold_store')))):
        return .01, 'inventory_kWh'
    if 'raw_x[' in lower or 'native_raw_x[' in lower or 'accepted_raw_x[' in lower: return 1e-6, 'raw_primal'
    if 'normalized' in unit.lower() or unit == 'dimensionless' or name == 'max_lock_violation': return 1e-9, 'normalized_lock'
    if (name.endswith(('_kw','_lbph','_rt','_mmbtu_h')) or name in ('raw_objective','res_fun','objective_value',
            'final_objective_value','lock_upper','incumbent_raw_objective','max_original_violation',
            'max_integrality_distance','max_bound_violation','max_physical_row_violation')):
        return 1e-6, 'instantaneous_or_solver_value'
    if name.endswith(('_kwh','_mmbtu','_usd_step')): return 1e-6, 'per_segment_energy_or_fee'
    return 0., 'unregistered_exact'


def compare_engineering_values(a, b, path, stats, unit=''):
    def fail(kind, **details):
        stats['difference_count'] += 1
        if len(stats['differences']) < 200: stats['differences'].append(dict(path=path, kind=kind, **details))
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b): fail('different_keys', left_only=sorted(set(a)-set(b)), right_only=sorted(set(b)-set(a)))
        local_unit = a.get('unit', unit) if a.get('unit') == b.get('unit') else unit
        for key in a.keys() & b.keys(): compare_engineering_values(a[key], b[key], path+'.'+key, stats, str(local_unit))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b): fail('different_lengths', left=len(a), right=len(b))
        for i, (x, y) in enumerate(zip(a,b)): compare_engineering_values(x,y,path+f'[{i}]',stats,unit)
    elif _finite(a) and _finite(b):
        stats['checked_numeric_values'] += 1
        delta = abs(a-b); stats['maximum_absolute_numeric_difference'] = max(stats['maximum_absolute_numeric_difference'], delta)
        tolerance, label = _budget(path, unit)
        if delta > tolerance: fail('numeric_budget_exceeded', difference=delta, tolerance=tolerance, budget=label, left=a, right=b)
        elif delta:
            stats['allowed_numeric_difference_count'] += 1
            prior = stats['maximum_allowed_difference_by_budget'].get(label, 0.)
            stats['maximum_allowed_difference_by_budget'][label] = max(prior, delta)
    elif type(a) is not type(b) or a != b: fail('different_value', left=str(a)[:160], right=str(b)[:160])


def require_critical_service_match(left, right):
    for row in (left,right):
        ledger = row.get('service_request_ledger', {})
        for field in ('original_demand_kW','delivered_kW'):
            value=ledger.get(field)
            if not isinstance(value,list) or len(value)!=4 or any(not isinstance(v,list) or len(v)!=3 for v in value):
                raise ValueError('Critical recovery comparison needs the complete actual4x3 service ledger')
    for group in (0,1):
        for carrier in range(3):
            demand=[row['service_request_ledger']['original_demand_kW'][group][carrier] for row in (left,right)]
            delivered=[row['service_request_ledger']['delivered_kW'][group][carrier] for row in (left,right)]
            if not all(_finite(x) for x in demand+delivered) or demand[0]!=demand[1]:
                raise ValueError('Critical original demand changed or is unknown')
            lost=[max(0.,d-s) for d,s in zip(demand,delivered)]
            if abs(lost[0]-lost[1])>1e-6: raise ValueError('P0/P1 actual interruption differs beyond the numerical rate allowance')
    return True
