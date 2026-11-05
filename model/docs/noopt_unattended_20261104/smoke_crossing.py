from pathlib import Path
import sys,json,copy,time,traceback,math
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.cooling_dynamics_20261104.run_worker import build_runtime,clean
from tools.cooling_dynamics_20261104 import default_config
from tools.sci_engineering.runner import source_binding
case=json.loads(Path('E:/hospital_ies_minute1_20261103_run01/design/cases01/T03_U.json').read_text(encoding='utf-8-sig'))
out=Path('E:/hospital_ies_cooling_dynamics_20261104_run01/component_checks/forward_crossing04');out.mkdir(parents=True,exist_ok=False)
start=time.perf_counter();env=None;history=[]
try:
    env,controller,resolved=build_runtime(case,source_binding(ROOT),default_config())
    env.reset(scenario=copy.deepcopy(case['scenario']),seed=20261023)
    print('RESET_OK',flush=True)
    with (out/'segments.jsonl').open('w',encoding='utf8') as stream:
        for minute in range(62):
            snapshot=clean(env.episode_snapshot_function(env));history.append(copy.deepcopy(snapshot))
            request=None if minute>=60 else controller.decide(copy.deepcopy(history))[0]
            _,_,_,_,info=env.step(request)
            for segment in info['segments']:
                def scan(value,path=''):
                    if isinstance(value,dict):
                        return [x for k,v in value.items() for x in scan(v,path+'.'+str(k))]
                    if isinstance(value,(list,tuple)):
                        return [x for i,v in enumerate(value) for x in scan(v,path+'['+str(i)+']')]
                    return [(path,repr(value))] if isinstance(value,float) and not math.isfinite(value) else []
                bad=scan(segment)
                if bad:
                    (out/'NONFINITE_METADATA_PATHS.json').write_text(json.dumps(bad,ensure_ascii=False),encoding='utf8')
                from tools.sci_engineering.solver_log import normalize_json
                segment=normalize_json(segment)
                stream.write(json.dumps(clean(segment),ensure_ascii=False,allow_nan=False)+'\n')
            stream.flush()
            if minute%10==0 or minute>=58:print('COMMITTED',env.core.time,'calls',len(env.core.solver_calls),'forward',len(env.core.forward_evaluations),flush=True)
    result=dict(status='completed_short_component_prefix',end_minute=env.core.time,wall_seconds=time.perf_counter()-start,
        optimization_attempts=env.core._noopt_optimizer_attempts,forward_evaluations=env.core.forward_evaluations)
except Exception as exc:
    result=dict(status='failed_component_prefix',error=repr(exc),traceback=traceback.format_exc(),
        end_minute=None if env is None or env.core is None else env.core.time,wall_seconds=time.perf_counter()-start)
    print(result['traceback'],flush=True)
(out/'RESULT.json').write_text(json.dumps(clean(result),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
print('OUTPUT',out,flush=True)
sys.exit(0 if result['status'].startswith('completed') else 1)




