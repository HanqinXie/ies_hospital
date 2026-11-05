"""Independent Windows supervisor; no physical imports, no automatic job fallback.

Independent lifecycle requires the external known-parent-Job closure probe;
IsProcessInJob(NULL) alone cannot identify caller ancestry. The private Job
owns only the supplied command. A STOP asks for cooperative save first;
forced termination never asserts that the journal is resumable. Not reboot-safe.
"""
import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

# -S avoids machine startup hooks; locate only the executable's installed packages.
def bootstrap_paths(executable, base_prefix):
    executable = Path(executable).resolve()
    roots = [executable.parent, executable.parent.parent]
    paths = [str(root / 'Lib/site-packages') for root in roots if (root / 'pyvenv.cfg').is_file()]
    paths.append(str(Path(base_prefix) / 'Lib/site-packages'))
    return list(dict.fromkeys(paths))

sys.path[:0] = bootstrap_paths(sys.executable, sys.base_prefix)
import psutil

OWNED = (Path(__file__).resolve().parent / 'vendor/owned_process_hidden.py').resolve()
OWNED_SHA = '715fd828b471b34f3187dbebaade9c52126d26cf6c9690c72d0ceac37e468dca'
RSS_LIMIT, RAM_MIN, DISK_MIN = 10_000_000_000, 16_000_000_000, 30_000_000_000

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def command_hash(command):
    return hashlib.sha256(json.dumps(command, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())

def append(path, value):
    with Path(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, allow_nan=False) + '\n')
        stream.flush(); os.fsync(stream.fileno())

def epath(value):
    p = Path(value)
    if not p.is_absolute() or p.drive.upper() != 'E:':
        raise ValueError('Output and stop paths must be absolute E-drive paths')
    if any(part.is_symlink() for part in (p, *p.parents)):
        raise ValueError('Symlink paths are forbidden')
    return p

def validate_spec(s):
    keys = {'schema','command','cwd','run_directory','max_seconds','stop_file',
            'cooperative_stop_files','grace_seconds'}
    if not isinstance(s, dict) or set(s) != keys or s['schema'] != 'hospital_ies.durable_launch.v1':
        raise ValueError('Unexpected launch schema/fields')
    if not isinstance(s['command'], list) or not s['command'] or not all(
            isinstance(x, str) and x and '\0' not in x for x in s['command']):
        raise ValueError('command must be an argument array')
    for key, low, high in [('max_seconds',1,2592000), ('grace_seconds',0,120)]:
        if type(s[key]) is not int or not low <= s[key] <= high:
            raise ValueError(key + ' is outside its registered finite bounds')
    if not Path(s['cwd']).is_absolute() or not Path(s['cwd']).is_dir():
        raise ValueError('cwd must be an existing absolute directory')
    epath(s['run_directory']); epath(s['stop_file'])
    if not isinstance(s['cooperative_stop_files'], list): raise ValueError('stop paths must be a list')
    for p in s['cooperative_stop_files']: epath(p)
    if len(set(s['cooperative_stop_files'])) != len(s['cooperative_stop_files']):
        raise ValueError('duplicate cooperative stop paths')
    return json.loads(json.dumps(s, allow_nan=False))

def launch_flags():
    return 0x01000000 | 0x00000200 | 0x00000008

def in_job():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    result = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(result.value)

def identity(pid, command):
    p = psutil.Process(pid)
    actual = p.cmdline()
    if actual != command: raise ValueError('Owned process command differs from intended argument array')
    return dict(pid=pid, create_time=p.create_time(), command_sha256=command_hash(command),
                observed_command_sha256=command_hash(actual))

def alive(item):
    try:
        p = psutil.Process(item['pid'])
        return p.create_time() == item['create_time'] and p.is_running()
    except psutil.NoSuchProcess: return False

def guard_reason(rss, ram, disk, elapsed, limit, stop):
    if any(type(x) not in (int,float) or not math.isfinite(x) or x < 0
           for x in (rss,ram,disk,elapsed,limit)):
        raise ValueError('Unknown/nonfinite resource sample')
    if stop: return 'explicit_stop'
    if rss > RSS_LIMIT: return 'tree_rss_limit'
    if ram < RAM_MIN: return 'available_ram_limit'
    if disk < DISK_MIN: return 'free_disk_limit'
    if elapsed >= limit: return 'wall_limit'
    return None

def request_stop(paths):
    for name in paths:
        p = epath(name); p.parent.mkdir(parents=True, exist_ok=True)
        try: write_new(p, dict(schema='hospital_ies.cooperative_stop.v1', requested_epoch=time.time()))
        except FileExistsError:
            if not p.is_file() or p.is_symlink(): raise ValueError('Invalid preexisting stop marker')

def owned_class():
    return owned_module().OwnedProcess

def owned_module():
    if sha(OWNED) != OWNED_SHA: raise ValueError('OwnedProcess source binding changed')
    spec = importlib.util.spec_from_file_location('bound_owned_process', OWNED)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

def job_info():
    result = dict(in_any_job=in_job(), membership_scope='any_job_not_caller_ancestry')
    if not result['in_any_job']: return result
    m = owned_module(); data = m.EXTENDED(); returned = wintypes.DWORD()
    query = m.kernel.QueryInformationJobObject
    query.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    if not query(None,9,ctypes.byref(data),ctypes.sizeof(data),ctypes.byref(returned)):
        raise ctypes.WinError(ctypes.get_last_error())
    flags = data.BasicLimitInformation.LimitFlags
    result.update(limit_flags=flags,kill_on_job_close=bool(flags & 0x2000),
        active_process_limit=data.BasicLimitInformation.ActiveProcessLimit if flags & 0x8 else None,
        process_memory_limit_bytes=data.ProcessMemoryLimit if flags & 0x100 else None,
        job_memory_limit_bytes=data.JobMemoryLimit if flags & 0x200 else None)
    return result

def parent_chain():
    chain = []
    try:
        for p in psutil.Process().parents()[:8]:
            chain.append(dict(pid=p.pid,create_time=p.create_time(),name=p.name()))
    except psutil.Error as exc: chain.append(dict(unavailable=type(exc).__name__))
    return chain

def authenticate_start(start,item,command_sha,source_sha,owned_sha,spec_sha):
    expected=dict(supervisor_identity=item,command_sha256=command_sha,source_sha256=source_sha,
                  owned_process_sha256=owned_sha,specfile_sha256=spec_sha)
    if any(start.get(k)!=v for k,v in expected.items()):
        raise ValueError('START identity/command/source/spec binding does not match this launch')

def job_members(owned):
    """Read the private Job, including descendants whose parent already exited."""
    if owned is None or not owned.job:
        raise ValueError('Private Job handle is unavailable; membership is unknown')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    query = kernel.QueryInformationJobObject
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(1024 * 1024)
    returned = wintypes.DWORD()
    if not query(owned.job, 3, buffer, len(buffer), ctypes.byref(returned)):
        raise ctypes.WinError(ctypes.get_last_error())
    count = ctypes.c_uint32.from_buffer(buffer, 4).value
    if count > (len(buffer) - 8) // ctypes.sizeof(ctypes.c_size_t):
        raise ValueError('Private Job member list is incomplete')
    return list((ctypes.c_size_t * count).from_buffer(buffer, 8))

def finalize_receipt(result,owned,known,directory,started):
    errors=[]; remaining=[]; members=None; empty_seen=False; close_ok=False; samples=[]
    if owned is not None:
        try:
            members=job_members(owned)
            samples.append(dict(epoch=time.time(), pids=members))
            if members:
                result['residual_job_terminated']=True
                owned.terminate_tree()
            until=time.monotonic()+5.
            while members and time.monotonic()<until:
                members=job_members(owned)
                samples.append(dict(epoch=time.time(), pids=members))
                if members: time.sleep(.05)
            empty_seen=members == []
            if not empty_seen: raise ValueError('Private Job still has members before handle close')
        except BaseException as exc:
            errors.append(dict(stage='job_membership_closure',type=type(exc).__name__,message=str(exc)))
            try: owned.terminate_tree()
            except BaseException as cleanup:
                errors.append(dict(stage='job_terminate',type=type(cleanup).__name__,message=str(cleanup)))
        finally:
            try: owned.close(); close_ok=True
            except BaseException as exc: errors.append(dict(stage='job_close',type=type(exc).__name__,message=str(exc)))
    else:
        errors.append(dict(stage='job_membership_closure',type='ValueError',message='No private Job was acquired'))
    try:
        until=time.monotonic()+5.
        remaining=[v for v in known.values() if alive(v)]
        while remaining and time.monotonic()<until:
            time.sleep(.05); remaining=[v for v in known.values() if alive(v)]
    except BaseException as exc: errors.append(dict(stage='identity_closure_check',type=type(exc).__name__,message=str(exc)))
    if errors: result['cleanup_errors']=errors
    closed=empty_seen and close_ok and not remaining and not errors
    if not closed and result.get('status') == 'exited':
        result.update(body_status='exited', status='closure_failed')
    result.update(owned_tree_closed=closed,remaining_owned_identities=remaining,
        job_membership_verified=empty_seen, job_closed_after_empty=empty_seen and close_ok,
        job_remaining_pids=members, job_query_samples=samples,
        elapsed_seconds=time.monotonic()-started,finished_epoch=time.time())
    write_new(directory/'PROCESS_RECEIPT.json',result)

def supervise(specfile):
    s = validate_spec(json.loads(Path(specfile).read_text(encoding='utf-8-sig')))
    directory = Path(s['run_directory']); started = time.monotonic()
    # WMI creates the independent process without inheriting caller std handles.
    if not (directory/'supervisor.stdout.log').exists():
        sys.stdout = (directory/'supervisor.stdout.log').open('x',encoding='utf-8')
        sys.stderr = (directory/'supervisor.stderr.log').open('x',encoding='utf-8')
    result = dict(schema='hospital_ies.durable_process_receipt.v1', status='starting',
        supervisor_identity=identity(os.getpid(), [sys.executable, '-S', str(Path(__file__).resolve()), 'supervise', '--spec', str(specfile)]),
        command_sha256=command_hash(s['command']), started_epoch=time.time(),
        source_sha256=sha(__file__), owned_process_sha256=sha(OWNED),specfile_sha256=sha(specfile),owned_tree_closed=False,
        force_killed=False, stop_reason=None, peak_tree_rss_bytes=0)
    owned = None; known = {}; next_sample = 0.; stop_at = None
    try:
        result['supervisor_job_info'] = job_info()
        result['parent_chain'] = parent_chain()
        cls = owned_class()
        write_new(directory / 'START.json', result)
        env = dict(os.environ, TEMP=str(directory), TMP=str(directory), PYTHONDONTWRITEBYTECODE='1')
        with (directory/'driver.stdout.log').open('x',encoding='utf-8') as out, \
                (directory/'driver.stderr.log').open('x',encoding='utf-8') as err:
            owned = cls(s['command'], cwd=s['cwd'], env=env, stdout=out, stderr=err)
            root = psutil.Process(owned.proc.pid)
            result['driver_identity'] = identity(root.pid, s['command'])
            write_new(directory/'DRIVER_START.json', result['driver_identity'])
            while owned.poll() is None:
                members = [psutil.Process(os.getpid())]
                for pid in job_members(owned):
                    try: members.append(psutil.Process(pid))
                    except psutil.NoSuchProcess: pass
                rss = 0
                for p in members:
                    try:
                        born = p.create_time(); rss += p.memory_info().rss
                        if p.pid != os.getpid(): known[p.pid] = dict(pid=p.pid, create_time=born)
                    except psutil.NoSuchProcess: pass
                elapsed = time.monotonic()-started
                ram, disk = psutil.virtual_memory().available, psutil.disk_usage('E:/').free
                result['peak_tree_rss_bytes'] = max(result['peak_tree_rss_bytes'], rss)
                reason = guard_reason(rss,ram,disk,elapsed,s['max_seconds'],Path(s['stop_file']).exists())
                if reason and stop_at is None:
                    result['stop_reason'] = reason; stop_at = time.monotonic()
                    request_stop([s['stop_file'], *s['cooperative_stop_files']])
                    write_new(directory/'STOP_REQUEST.json', dict(reason=reason,epoch=time.time(),grace_seconds=s['grace_seconds']))
                if time.monotonic() >= next_sample:
                    append(directory/'HEARTBEAT.jsonl', dict(epoch=time.time(), elapsed_seconds=elapsed,
                        tree_rss_bytes=rss, available_ram_bytes=ram, free_disk_bytes=disk,
                        owned_identities=list(known.values()), stop_reason=result['stop_reason']))
                    next_sample = time.monotonic()+1.
                if stop_at is not None and time.monotonic()-stop_at >= s['grace_seconds']:
                    if owned.poll() is None: owned.terminate_tree(); result['force_killed'] = True
                    break
                time.sleep(.25)
            result.update(status='exited', exit_code=owned.proc.wait(timeout=5))
    except BaseException as exc:
        result.update(status='controller_error', error=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc()))
        if owned is not None and owned.poll() is None: owned.terminate_tree()
    finally:
        finalize_receipt(result,owned,known,directory,started)
    return 0 if result['status']=='exited' and result['exit_code']==0 and result['owned_tree_closed'] else 1

def wmi_script(command,cwd):
    payload = base64.b64encode(json.dumps(dict(command_line=subprocess.list2cmdline(command),cwd=cwd)).encode()).decode()
    return ("$ErrorActionPreference='Stop'; $x=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+payload+"'))); "
        "$s=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0;CreateFlags=[uint32]16777736}; "
        "$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$x.command_line;CurrentDirectory=$x.cwd;ProcessStartupInformation=$s}; "
        "$r | Select-Object ReturnValue,ProcessId | ConvertTo-Json -Compress")

def shell_script(command,cwd):
    payload = base64.b64encode(json.dumps(dict(executable=command[0],arguments=subprocess.list2cmdline(command[1:]),cwd=cwd)).encode()).decode()
    return ("$ErrorActionPreference='Stop'; $x=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+payload+"'))); "
        "$s=New-Object -ComObject Shell.Application; $s.ShellExecute($x.executable,$x.arguments,$x.cwd,'open',0)")

def launch(specfile,method='breakaway'):
    if os.name != 'nt': raise RuntimeError('Windows only')
    s = validate_spec(json.loads(Path(specfile).read_text(encoding='utf-8-sig')))
    directory = epath(s['run_directory']); directory.mkdir(parents=True,exist_ok=False)
    copied = directory/'SPEC.json'; write_new(copied,s)
    command = [sys.executable,'-S',str(Path(__file__).resolve()),'supervise','--spec',str(copied)]
    startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = 0
    item=None; process=None
    try:
        if method=='breakaway':
            with (directory/'supervisor.stdout.log').open('x') as out, (directory/'supervisor.stderr.log').open('x') as err:
                p = subprocess.Popen(command,cwd=s['cwd'],stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                    close_fds=True,creationflags=launch_flags(),startupinfo=startup)
            pid = p.pid
        elif method=='wmi':
            encoded = base64.b64encode(wmi_script(command,s['cwd']).encode('utf-16le')).decode()
            response = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                capture_output=True,text=True,timeout=20,creationflags=0x08000000)
            write_new(directory/'WMI_RESPONSE.json',dict(exit_code=response.returncode,stdout=response.stdout,stderr=response.stderr))
            if response.returncode: raise RuntimeError('Same-user WMI launch rejected; inspect response')
            value = json.loads(response.stdout)
            if value['ReturnValue'] != 0: raise RuntimeError('Win32_Process.Create rejected: '+str(value['ReturnValue']))
            pid = value['ProcessId']
        elif method=='shell':
            encoded = base64.b64encode(shell_script(command,s['cwd']).encode('utf-16le')).decode()
            response = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                capture_output=True,text=True,timeout=20,creationflags=0x08000000)
            write_new(directory/'SHELL_RESPONSE.json',dict(exit_code=response.returncode,stdout=response.stdout,stderr=response.stderr))
            if response.returncode: raise RuntimeError('Same-user Shell.Application launch rejected')
            until = time.monotonic()+10.
            while not any((directory/n).exists() for n in ('START.json','PROCESS_RECEIPT.json')) and time.monotonic()<until: time.sleep(.05)
            path = directory/('START.json' if (directory/'START.json').exists() else 'PROCESS_RECEIPT.json')
            if not path.exists(): raise RuntimeError('Shell broker produced no identity-bound start/exit receipt')
            pid = json.loads(path.read_text())['supervisor_identity']['pid']
        else: raise ValueError('Unknown explicit launch method')
        process = psutil.Process(pid)
        if process.username() != psutil.Process(os.getpid()).username():
            raise ValueError('Supervisor does not belong to the invoking Windows user')
        item = identity(pid,command)
        receipt = dict(schema='hospital_ies.durable_launch_receipt.v1', supervisor_identity=item,
            command=command,method=method,creationflags=None if method=='shell' else launch_flags(),
            launcher_in_job=in_job(),same_windows_user=True,run_directory=str(directory))
        write_new(directory/'LAUNCH.json',receipt)
        until = time.monotonic()+10.
        start=None
        while alive(item) and time.monotonic()<until:
            try: start=json.loads((directory/'START.json').read_text(encoding='utf-8'))
            except (FileNotFoundError,json.JSONDecodeError): time.sleep(.05);continue
            break
        if start is None: raise RuntimeError('Independent supervisor did not authenticate START; inspect preserved receipts')
        authenticate_start(start,item,command_hash(s['command']),sha(__file__),OWNED_SHA,sha(copied))
        receipt['authenticated_start'] = True
        print(json.dumps(receipt)); return 0
    except BaseException as exc:
        failure=dict(type=type(exc).__name__,message=str(exc),epoch=time.time(),automatic_fallback=False)
        if item is not None:
            try:
                if alive(item): process.terminate();process.wait(timeout=5)
                failure['remaining_supervisor_identity']=item if alive(item) else None
            except BaseException as cleanup: failure['cleanup_error']=dict(type=type(cleanup).__name__,message=str(cleanup))
        write_new(directory/'LAUNCH_FAILURE.json',failure)
        raise

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['launch','supervise']); p.add_argument('--spec',required=True)
    p.add_argument('--method',choices=['breakaway','wmi','shell'],default='breakaway',help='No automatic fallback; alternative must be selected explicitly')
    args = p.parse_args()
    return launch(args.spec,args.method) if args.action=='launch' else supervise(args.spec)

if __name__=='__main__': raise SystemExit(main())
