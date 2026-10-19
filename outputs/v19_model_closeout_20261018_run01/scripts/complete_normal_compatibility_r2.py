"""One external postprocess continuation; previous unknown evidence is retained."""
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import traceback

B=Path(__file__).resolve().parents[1];ROOT=B.parents[1]
def read(p):return json.loads(Path(p).read_text(encoding='utf8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def module(p,name):
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def main():
    marker=B/'NORMAL_COMPATIBILITY_AUTORUN_R2_RESULT.json'
    if marker.exists():raise FileExistsError(marker)
    record=dict(schema='hospital_ies.v19.normal_compatibility_continuation.v2',utc_started=datetime.now(timezone.utc).isoformat(),
        old_unknown_proof_retained='review/compatibility_normal4_final',physical_episodes_started=0,raw_files_opened=0,
        collector_program_sha256=sha(B/'scripts/summarize_compatibility.py'),commands=[])
    try:
        guard=module(B/'scripts/finish_normal_compatibility.py','normal_compatibility_prior_guard')
        verify=module(B/'scripts/finish_final_expost_r2.py','normal_compatibility_frozen_guard')
        verify.verify_frozen_sources(ROOT,read(B/'MODEL_FREEZE_CLOSEOUT01.json'))
        verify.verify_normal(B)
        argv=guard.comparison_argv(B);argv[argv.index('--label')+1]='normal4_final_r2'
        cmd=[sys.executable,'-B',str(ROOT/'tools/p0_audit/run_logged.py'),'--run-dir',str(B),
            '--name','B19_normal_compatibility_actual_r2','--',*argv]
        result=subprocess.run(cmd,cwd=ROOT,check=False)
        record['commands'].append(dict(command=cmd,exit_code=result.returncode))
        if result.returncode:raise RuntimeError('Normal compatibility CLI failed')
        out=B/'review/compatibility_normal4_final_r2';summary=read(out/'SUMMARY.json')
        verify.verify_output_hashes(out,summary)
        record['strict_registered_full']=guard.verify_proofs(read(out/'MATCH_PROOFS.json'))
        record.update(all_succeeded=True,summary_sha256=sha(out/'SUMMARY.json'),proofs_sha256=sha(out/'MATCH_PROOFS.json'))
        verify.verify_frozen_sources(ROOT,read(B/'MODEL_FREEZE_CLOSEOUT01.json'))
    except Exception as error:
        record.update(all_succeeded=False,error=dict(type=type(error).__name__,message=str(error),traceback=traceback.format_exc()))
    record['utc_finished']=datetime.now(timezone.utc).isoformat()
    with marker.open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(dict(all_succeeded=record['all_succeeded'],marker=str(marker))))
    return 0 if record['all_succeeded'] else 1
if __name__=='__main__':raise SystemExit(main())
