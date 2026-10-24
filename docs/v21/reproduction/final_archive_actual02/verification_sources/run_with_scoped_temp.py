"""Use only the extracted logger and an E-project temporary directory."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,os,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('--project',required=True,type=Path);p.add_argument('--run-dir',required=True,type=Path);p.add_argument('--name',required=True);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
project=a.project.resolve();expected=Path('E:/hospital_ies_v21_20261023/archive_clean_v21_final02/project').resolve()
assert project==expected and Path.cwd().resolve()==project
command=a.command[1:] if a.command[:1]==['--'] else a.command;assert command
tmp=project/'independent/tests/runtime_temp';tmp.mkdir(parents=True,exist_ok=True)
logger=project/'tools/p0_audit/run_logged.py';assert logger.is_file()
env=os.environ.copy();overrides=dict(TEMP=str(tmp),TMP=str(tmp),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1');env.update(overrides)
a.run_dir.mkdir(parents=True,exist_ok=True);record=a.run_dir/(a.name+'.scoped_environment.json')
with record.open('x',encoding='utf-8') as f:json.dump(dict(created_utc=datetime.now(timezone.utc).isoformat(),project=str(project),cwd=str(Path.cwd()),environment_overrides=overrides,scope='This child only; no global environment or power setting changed',command=command,logger=str(logger),logger_sha256=hashlib.sha256(logger.read_bytes()).hexdigest()),f,ensure_ascii=False,indent=2)
result=subprocess.run([sys.executable,'-B',str(logger),'--run-dir',str(a.run_dir),'--name',a.name,'--',*command],cwd=project,env=env,check=False)
sys.exit(result.returncode)
