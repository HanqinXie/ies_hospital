from pathlib import Path
import sys,json,copy,time,traceback
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.cooling_dynamics_20261104.run_worker import build_runtime,clean,physical_state,compact_solver
from tools.cooling_dynamics_20261104 import default_config
from tools.sci_engineering.runner import source_binding
case=json.loads(Path('E:/hospital_ies_minute1_20261103_run01/design/cases01/T03_U.json').read_text(encoding='utf-8-sig'))
root=Path('E:/hospital_ies_cooling_dynamics_20261104_run01/component_checks')
label=sys.argv[1] if len(sys.argv)>1 else 'synthetic_boundary01'
anchor=int(sys.argv[2]) if len(sys.argv)>2 else 59
out=root/label;out.mkdir(parents=True,exist_ok=False)
began=time.perf_counter();env=None;history=[];rows=[]
try:
    env,controller,resolved=build_runtime(case,source_binding(ROOT),default_config())
    env.reset(scenario=copy.deepcopy(case['scenario']),seed=20261023)
    # Explicit component fixture: this moves the clock only. It is NOT a
    # continuous pre-fault scientific trajectory or an accepted case result.
    clean(env.episode_snapshot_function(env))
    env.core.time=float(anchor);env.steps_done=anchor;env.core.station.last_supply_end=float(anchor)
    clean(physical_state(env))
    print('SYNTHETIC_ANCHOR',anchor,flush=True)
    with (out/'segments.jsonl').open('x',encoding='utf8') as stream:
        for index in range(3):
            if env.core.time<60:
                from tools.noopt_unattended_20261104.runtime import forbid_optimization
                with forbid_optimization(env.core):segments=env.core.advance(60.)
                env.steps_done=60;info={'segments':segments}
            else:
                _,_,_,_,info=env.step(None)
            clean(physical_state(env))
            for segment in info['segments']:
                value=compact_solver(segment);rows.append(value)
                stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')
            stream.flush()
            print('COMMITTED',env.core.time,'forward',len(env.core.forward_evaluations),flush=True)
    result=dict(status='completed_synthetic_boundary_component',not_continuous_scientific_evidence=True,
        anchor_minute=anchor,end_minute=env.core.time,wall_seconds=time.perf_counter()-began,
        optimization_attempts=env.core._noopt_optimizer_attempts,forward_evaluations=env.core.forward_evaluations,
        maximum_balance_residual_kW=max(max(map(abs,r['energy_balance_residuals'])) for r in rows),
        storage_receipts=[r.get('m21_storage') for r in rows],cooling_receipts=[r.get('cooling_dynamics') for r in rows])
except Exception as exc:
    result=dict(status='failed_synthetic_boundary_component',not_continuous_scientific_evidence=True,
        error=repr(exc),traceback=traceback.format_exc(),anchor_minute=anchor,
        end_minute=None if env is None or env.core is None else env.core.time,wall_seconds=time.perf_counter()-began)
    print(result['traceback'],flush=True)
(out/'RESULT.json').write_text(json.dumps(clean(result),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
print('OUTPUT',out,flush=True)
sys.exit(0 if result['status'].startswith('completed') else 1)



