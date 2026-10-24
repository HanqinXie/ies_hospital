"""Run generated PS5 with fake native commands only; never call sci_campaign."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

HERE=Path(__file__).resolve().parent
ROOT=Path('D:/hospital_ies_v21_20261023')
PLAN=Path('E:/hospital_ies_v21_20261023/main02/experiments/002_main_m21_L2_b691f2fe78052e5574ca_H0/PLAN.json')
FREEZE=Path('E:/hospital_ies_v21_20261023/main02/FINAL_MAIN_FREEZE.json')
PSHA='ed3c2e5a5e224ec413a9edac41d5bfe805c22a554a59eac4939ac66734e617b2'
FSHA='a868f610fa593b4e8b0184ef72f35e1ca76b16693da44cfd516ab9c8e0f22e43'
POWERSHELL=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'


def quote(value):return "'"+str(value).replace("'","''")+"'"


@pytest.fixture
def fixture(tmp_path):
    spec=importlib.util.spec_from_file_location('future_script_fixture',HERE/'export_reproduction_inputs.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    output=tmp_path/'inputs';future=tmp_path/'future'
    m.export(ROOT,PLAN,PSHA,FREEZE,FSHA,output,future,4.)
    fake=tmp_path/'fake_native.py';log=tmp_path/'fake_calls.jsonl'
    fake.write_text('''import json,os,sys
from pathlib import Path
assert os.environ['FAKE_NATIVE_ONLY']=='1'
args=sys.argv[1:]
stage=args[1] if args[0]=='scripts/sci_campaign.py' else 'run'
assert stage in ('freeze','register','run')
with Path(os.environ['FAKE_CALL_LOG']).open('a') as f:f.write(json.dumps(stage)+'\\n')
if stage==os.environ.get('FAKE_FAIL_STAGE'):sys.exit(17)
if stage=='freeze':Path(os.environ['FAKE_FUTURE']).mkdir()
sys.exit(0)
''',encoding='utf8')
    script=tmp_path/'future_recipe.ps1'
    script.write_bytes((output/'FUTURE_COMMANDS.ps1.txt').read_bytes())
    driver=tmp_path/'fixture_driver.ps1'
    driver.write_text('function python { & '+quote(sys.executable)+' '+quote(fake)+' @args; $global:LASTEXITCODE = $LASTEXITCODE }\n. '+quote(script)+'\n',encoding='utf8')
    env=dict(os.environ,FAKE_NATIVE_ONLY='1',FAKE_CALL_LOG=str(log),FAKE_FUTURE=str(future))
    def run(fail=''):
        result=subprocess.run([str(POWERSHELL),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(driver)],
            capture_output=True,env=dict(env,FAKE_FAIL_STAGE=fail),timeout=30)
        index=len(list(tmp_path.glob('exit_*.json')))
        (tmp_path/f'exit_{index}.stdout.bin').write_bytes(result.stdout)
        (tmp_path/f'exit_{index}.stderr.bin').write_bytes(result.stderr)
        (tmp_path/f'exit_{index}.json').write_text(json.dumps(dict(returncode=result.returncode,
            stdout=result.stdout.decode('utf8','backslashreplace'),stderr=result.stderr.decode('utf8','backslashreplace'))),encoding='utf8')
        return result
    def calls():return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return output,future,run,calls


@pytest.mark.parametrize('stage,expected',[('freeze',['freeze']),('register',['freeze','register']),('run',['freeze','register','run'])])
def test_native_nonzero_stops_script(fixture,stage,expected):
    output,future,run,calls=fixture
    result=run(stage)
    assert result.returncode!=0, result.stdout+result.stderr
    assert b'exit 17' in result.stderr
    assert calls()==expected


def test_existing_future_root_refuses_all_native_calls(fixture):
    output,future,run,calls=fixture;future.mkdir()
    result=run()
    assert result.returncode!=0 and calls()==[]


@pytest.mark.parametrize('changed',['PANEL.json','SOURCE_MANIFEST.json'])
def test_input_drift_refuses_all_native_calls(fixture,changed):
    output,future,run,calls=fixture
    with (output/changed).open('ab') as stream:stream.write(b'\n')
    result=run()
    assert result.returncode!=0 and calls()==[]


def test_success_is_three_fake_calls_and_repeat_is_refused(fixture):
    output,future,run,calls=fixture
    first=run();assert first.returncode==0 and calls()==['freeze','register','run']
    second=run()
    assert second.returncode!=0 and calls()==['freeze','register','run']
