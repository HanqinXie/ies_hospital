"""Own one subprocess tree; Windows uses an unnamed kill-on-close Job Object."""
import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
import threading

if os.name=='nt':
    class BASIC(ctypes.Structure):
        _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
            ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
            ('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
    class EXTENDED(ctypes.Structure):
        _fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];kernel.SetInformationJobObject.restype=wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];kernel.AssignProcessToJobObject.restype=wintypes.BOOL
    kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT];kernel.TerminateJobObject.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL

class OwnedProcess:
    def __init__(self,command,*,cwd,env,stdout,stderr):
        self.job=None;self.proc=None;self._lock=threading.RLock()
        if os.name=='nt':
            self.job=kernel.CreateJobObjectW(None,None)
            if not self.job:raise ctypes.WinError(ctypes.get_last_error())
            limits=EXTENDED();limits.BasicLimitInformation.LimitFlags=0x2000
            if not kernel.SetInformationJobObject(self.job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
                error=ctypes.WinError(ctypes.get_last_error());self.close();raise error
        try:
            self.proc=subprocess.Popen(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name=='nt' else 0,
                start_new_session=os.name!='nt')
            if os.name=='nt' and not kernel.AssignProcessToJobObject(self.job,wintypes.HANDLE(int(self.proc._handle))):
                error=ctypes.WinError(ctypes.get_last_error())
                # Assignment failure is fail-closed. Only the Popen-owned root
                # and its observed, creation-time-bound descendants are killed.
                import psutil
                descendants=[]
                try:descendants=[(p.pid,p.create_time()) for p in psutil.Process(self.proc.pid).children(recursive=True)]
                except psutil.Error:pass
                self.proc.kill();self.proc.wait(timeout=5)
                for pid,born in descendants:
                    try:
                        child=psutil.Process(pid)
                        if child.create_time()==born:child.kill()
                    except psutil.Error:pass
                raise error
        except BaseException:
            self.close();raise

    def poll(self):return self.proc.poll()

    def terminate_tree(self):
        with self._lock:
            if os.name=='nt':
                if self.job and not kernel.TerminateJobObject(self.job,1):raise ctypes.WinError(ctypes.get_last_error())
            else:
                try:os.killpg(self.proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            self.proc.wait(timeout=5)

    def close(self):
        with self._lock:
            if os.name=='nt' and self.job:
                kernel.CloseHandle(self.job);self.job=None
            elif os.name!='nt' and self.proc is not None:
                # A finished root must not leave an owned grandchild behind.
                try:os.killpg(self.proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
