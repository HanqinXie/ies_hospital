"""Sequential evidence-gated regression and exact400 replay; stops on any error."""
import json
from pathlib import Path
import subprocess
import sys
sys.path[:0]=[str(Path(__file__).resolve().parents[2])]
from tools.p2c_experiment.release import ROOT,source_snapshot,create

B=ROOT/'outputs/v43_p2c_20261013_run01'


def logged(name,argv):
    result=subprocess.run([sys.executable,str(ROOT/'tools/p0_audit/run_logged.py'),
        '--run-dir',str(B),'--name',name,'--',*argv],cwd=ROOT,check=False)
    if result.returncode:
        raise RuntimeError(f'{name} exited {result.returncode}; no dependent stage released')


def main():
    snapshot=B/'release_source_before_02.json'
    source_snapshot(snapshot)
    logged('06_full_release_regression',['python','-m','pytest','-q','-rx','--tb=short',
        '--junitxml=outputs/v43_p2c_20261013_run01/release_pytest_02.xml'])
    gate=B/'release_gate_v3.json'
    actual=create(B/'logs/06_full_release_regression.command.json',B/'release_pytest_02.xml',
        B/'baseline_pytest.xml',B/'review/corrected_solver_evidence_review_v3.json',snapshot,gate)
    print(json.dumps(dict(stage='full_release',**actual['actual'])),flush=True)
    numerical=B/'solver_diagnostics/S_gate_v3.json'
    logged('07_freeze_uniform_replay',['python','tools/p2c_experiment/replay.py','freeze',
        str(B/'replay'),str(numerical),str(gate)])
    manifest=B/'replay/global_manifest.json'
    for index in range(8):
        logged(f'replay_shard_{index:02d}',['python','tools/p2c_experiment/replay.py','run',str(manifest),str(index)])
        name=f'check_{index:02d}'
        logged(f'replay_audit_{index:02d}',['python','tools/p2b_experiment/audit.py',str(manifest),name])
        audit=json.loads((manifest.parent/'audits'/name/'audit.json').read_text(encoding='utf-8'))
        print(json.dumps(dict(stage='replay_internal_check',shard=index,
            attempts=audit['attempted_trajectories'],status_counts=audit['status_counts'],
            score_unknown=audit['score_unknown'],manifest_signature=audit['manifest_signature'])),flush=True)
        if any(k != 'completed' and v for k,v in audit['status_counts'].items()):
            raise RuntimeError('New replay exception: inspect complete preserved contexts before dependent stages')
    logged('replay_final_audit',['python','tools/p2b_experiment/audit.py',str(manifest),'final'])
    print(json.dumps(dict(stage='uniform_replay_complete',new_specs=0,attempted_trajectories=800)),flush=True)


if __name__=='__main__':
    main()
