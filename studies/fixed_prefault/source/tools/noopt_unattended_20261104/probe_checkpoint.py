"""Bounded real-runtime checkpoint integration check, not a full trajectory study."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'
from tools.cooling_dynamics_20261104.run_worker import build_runtime,record_observation,controller_records,physical_state,clean
from tools.sci_engineering.runner import source_binding,input_binding
from tools.cooling_dynamics_20261104 import default_config
from tools.noopt_unattended_20261104.environment import fork_fixed_unattended,finish_common_prefault_step


def run(case_path,output,minutes):
    output.mkdir(parents=True,exist_ok=False)
    os.environ['TEMP']=os.environ['TMP']=str(output)
    case=json.loads(case_path.read_text(encoding='utf8'))
    if case['minute1']['variant']!='responsive':raise ValueError('Common preparation requires a responsive registered case')
    binding=source_binding(ROOT)
    (output/'SOURCE_BINDING.json').write_text(json.dumps(dict(source_root=str(ROOT),source_files=binding,
        runtime_inputs=input_binding(ROOT)),indent=2),encoding='utf8')
    common,controller,resolved=build_runtime(case,binding,default_config())
    common.reset(scenario=deepcopy(case['scenario']),seed=int(case['scenario'].get('initialization_seed',20261023)))
    history=[];began=time.monotonic()
    while common.core.time<60.:
        current=record_observation(common,history)
        action,diagnostic=controller.decide(controller_records(controller,history))
        if common.core.time==59.:finish_common_prefault_step(common,action)
        else:common.step(action)
        if int(common.core.time)%10==0:
            print(json.dumps(dict(phase='separate_common_preparation',minute=common.core.time,elapsed=time.monotonic()-began)),flush=True)
    before=physical_state(common)
    u=fork_fixed_unattended(common)
    first=len(u.core.segments)
    while u.core.time<60.+minutes:
        u.step(None)
        print(json.dumps(dict(phase='fixed_unattended_integration',minute=u.core.time,elapsed=time.monotonic()-began)),flush=True)
    rows=clean(u.core.segments[first:])
    assert physical_state(common)==before,'Common checkpoint mutated by U'
    assert not u.core._noopt_optimizer_attempts
    assert rows and all(row.get('actual_result_owner')=='arithmetic_forward' for row in rows)
    assert not u.core.errors
    (output/'SEGMENTS.json').write_text(json.dumps(rows,ensure_ascii=False,allow_nan=False),encoding='utf8')
    result=dict(status='passed',scope='bounded_real_runtime_integration_not_complete_scientific_trajectory',
        separate_common_preparation_minutes=60.,unattended_minutes=minutes,
        native_segment_count=len(rows),optimizer_attempts=u.core._noopt_optimizer_attempts,
        common_checkpoint_unchanged=True,checkpoint=clean(u.fixed_branch_checkpoint),
        maximum_balance_residual_kW=max(max(map(abs,row['energy_balance_residuals'])) for row in rows),
        elapsed_seconds=time.monotonic()-began)
    (output/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--minutes',type=int,default=3,choices=range(1,6))
    args=parser.parse_args()
    run(args.case,args.output,args.minutes)
