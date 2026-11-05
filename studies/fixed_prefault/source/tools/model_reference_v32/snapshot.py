"""Independent common reset only, never a historical trajectory or C/M decision."""
import copy,json
from pathlib import Path

def load_frozen_case(root,branch):
    if branch not in ('normal','grid'):raise ValueError('Only registered b01 normal/grid branches')
    root=Path(root);registry=json.loads((root/'configs/model_review_v30/cases.json').read_text(encoding='utf8'))
    cid='b01_L1_grid_'+('normal' if branch=='normal' else 'disturbed')+'_M'
    raw=copy.deepcopy(next(c for c in registry['cases'] if c['id']==cid))
    from tools.model_review_v30.validation import resolve_case
    case=resolve_case(raw,registry)
    return case,registry

def physical_configuration(case,registry):
    from tools.model_correction_v25.validation import resolve_operating_rule
    config=copy.deepcopy(registry['hardware_profiles'][case['configuration']])
    for name in ('model_closure_v22','model_correctness_v23','engineering_completion_v24','model_correction_v25'):
        config[name]=copy.deepcopy(case.get(name,registry[name]))
    _,rule=resolve_operating_rule(registry,case)
    return config,rule

def initialize(root,branch,output):
    """Original t0 reset may solve initialization; zero policy/episode calls."""
    from tools.model_review_v30.environment import with_terminal
    from tools.model_review_v30.terminal import _plain
    from tools.sci_engineering.design import allowed_hours,SEALED_DAYS
    from tools.model_correction_v25 import MODEL_ID
    from tools.sci_engineering.runner import input_binding,source_binding,digest
    root=Path(root).resolve();output=Path(output).resolve()
    if output.exists():raise FileExistsError('Fresh independent initialization output required')
    if output.drive.upper() not in ('D:','E:'):raise ValueError('Initialization evidence stays D/E')
    output.mkdir(parents=True);case,registry=load_frozen_case(root,branch);config,rule=physical_configuration(case,registry)
    adapter=with_terminal(case.get('v27_load_overlay'),thermal_profile=case['v28_thermal_profile'])
    env=adapter.OperatingHospitalIESEnv(root,allowed_hours=allowed_hours({'scenarios':[case['scenario']]}),sealed_days=SEALED_DAYS,
        model_id=MODEL_ID,model_config=config,operating_rule=rule,solver_evidence_directory=output/'domain_evidence',enable_all_validation_rows=True)
    env.reset(scenario=copy.deepcopy(case['scenario']),seed=int(case['scenario'].get('initialization_seed',20261023)))
    if env.core.time!=0.:raise ValueError('Initialization unexpectedly advanced actual time')
    result=dict(schema='hospital_ies.v32.independent_common_initialization.v1',branch=branch,time_minute=0.,
        initial_stocks={k:float(getattr(env.core,k).energy_kWh) for k in ('ups','cold_store')},
        diesel_shaft_budget_kWh=float(env.state.diesel_energy_remaining_kWh),initial_state=_plain(vars(env.state)),
        initial_GT_kW=env.state.gt_prev.tolist(),stores={k:copy.deepcopy(getattr(env.core,k).config) for k in ('ups','cold_store')},
        plant=env.plant.as_dict(),physical_initial_receipt=_plain(env.v28_demand_receipt['full_initial_state_and_core']),
        resolved_model_config=copy.deepcopy(env.operating_model_config),execution_contract=copy.deepcopy(env.execution_contract),
        operating_execution_contract=copy.deepcopy(env.operating_execution_contract),
        initialization_solver_calls=_plain(list(env.core.solver_calls)),
        provenance=dict(source='original common environment constructor/reset, no controller constructed or episode executed',
            source_files=source_binding(root),input_files=input_binding(root),root=str(root),base_case_id=case['id'],
            scenario=copy.deepcopy(case['scenario']),initialization_seed=case['scenario'].get('initialization_seed',20261023),
            completed_policy_results_read=False,new_hospital_episodes=0,C_M_decision_calls=0))
    result['physical_initial_receipt_sha256']=digest(result['physical_initial_receipt'])
    (output/'INITIALIZATION.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result
