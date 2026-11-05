"""Own synthetic processes only: console/window evidence, no signal overrides."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from durable_launch import psutil

def write(path,value):
    with path.open('x',encoding='utf8') as stream:
        json.dump(value,stream,indent=2);stream.flush();os.fsync(stream.fileno())

def console_snapshot():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetConsoleWindow.restype=wintypes.HWND
    window=kernel.GetConsoleWindow()
    kernel.GetConsoleProcessList.argtypes=[ctypes.POINTER(wintypes.DWORD),wintypes.DWORD]
    kernel.GetConsoleProcessList.restype=wintypes.DWORD
    values=(wintypes.DWORD*64)();ctypes.set_last_error(0)
    count=kernel.GetConsoleProcessList(values,64);error=ctypes.get_last_error()
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes=[wintypes.HWND];user.IsWindowVisible.restype=wintypes.BOOL
    visible=[]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    @callback_type
    def visit(hwnd,param):
        pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value==os.getpid() and user.IsWindowVisible(hwnd):visible.append(int(hwnd))
        return True
    user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM];user.EnumWindows.restype=wintypes.BOOL
    if not user.EnumWindows(visit,0):raise ctypes.WinError(ctypes.get_last_error())
    return dict(pid=os.getpid(),create_time=psutil.Process().create_time(),epoch=time.time(),
        console_window_handle=int(window or 0),console_process_count=count,
        console_process_list=list(values[:min(count,64)]),console_process_error=error,
        visible_owned_windows=visible,sigint_handler=str(signal.getsignal(signal.SIGINT)),
        sigint_ignored=signal.getsignal(signal.SIGINT)==signal.SIG_IGN)

p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True)
p.add_argument('--seconds',type=float,required=True);p.add_argument('--stop');p.add_argument('--leaf',action='store_true')
a=p.parse_args();directory=a.directory
write(directory/('LEAF_CONSOLE.json' if a.leaf else 'DRIVER_CONSOLE.json'),console_snapshot())
if not a.leaf:
    command=[sys.executable,'-S',str(Path(__file__).resolve()),'--directory',str(directory),'--seconds','120','--leaf']
    with (directory/'leaf.stdout.log').open('x') as out,(directory/'leaf.stderr.log').open('x') as err:
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW)
    write(directory/'LEAF_START.json',dict(pid=child.pid,create_time=psutil.Process(child.pid).create_time(),creationflags=subprocess.CREATE_NO_WINDOW))
started=time.monotonic()
while time.monotonic()-started<a.seconds:
    with (directory/('LEAF_HEARTBEAT.jsonl' if a.leaf else 'PROBE_HEARTBEAT.jsonl')).open('a') as stream:
        stream.write(json.dumps(dict(pid=os.getpid(),epoch=time.time()))+'\n');stream.flush();os.fsync(stream.fileno())
    if a.stop and Path(a.stop).exists():break
    time.sleep(.25)
write(directory/('LEAF_DONE.json' if a.leaf else 'PROBE_DONE.json'),dict(epoch=time.time(),stop_seen=bool(a.stop and Path(a.stop).exists())))
# The leaf is deliberately left to the supervisor's owned Job cleanup.
