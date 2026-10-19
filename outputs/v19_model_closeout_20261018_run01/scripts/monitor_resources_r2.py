"""Read-only PID resource sampler. Never controls processes or loads physics data."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from types import SimpleNamespace
import unittest
import psutil

SCRIPT=Path(__file__).resolve();B=SCRIPT.parents[1]
ERRORS=(psutil.Error,OSError,IndexError)

def utc(epoch=None):return datetime.fromtimestamp(time.time() if epoch is None else epoch,timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_json(path,value):
    with Path(path).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)

class FakeProcess:
    def __init__(self,pid,args,*,name='python.exe',cmd_error=False,memory_error=False,created=1.):
        self.pid=pid;self.args=args;self.process_name=name;self.cmd_error=cmd_error;self.memory_error=memory_error
        self.created=created;self.cmd_reads=0;self.user=2.;self.system=1.;self.directory='.'
    def name(self):return self.process_name
    def create_time(self):return self.created
    def cmdline(self):
        self.cmd_reads+=1
        if self.cmd_error:
            error=OSError('synthetic ReadProcessMemory WinError 87');error.winerror=87;raise error
        if self.process_name!='python.exe':raise AssertionError('cmdline read before Python name filter')
        return self.args
    def cwd(self):return self.directory
    def memory_info(self):
        if self.memory_error:raise OSError('synthetic memory read failed')
        return SimpleNamespace(rss=100,private=80,peak_wset=150)
    def cpu_times(self):return SimpleNamespace(user=self.user,system=self.system)

class FakeAPI:
    def __init__(self,processes,*,enum_error=False):self.processes={p.pid:p for p in processes};self.enum_error=enum_error
    def pids(self):
        if self.enum_error:raise OSError('synthetic PID enumeration failure')
        return list(self.processes)
    def Process(self,pid):return self.processes[pid]
    def virtual_memory(self):return SimpleNamespace(available=1000,total=2000)

class MonitorTests(unittest.TestCase):
    root=None
    def setUp(self):self.path=self.root/self._testMethodName;self.path.mkdir();self.experiments=self.path/'experiments'
    def args(self,batch='case'):return ['python.exe','-B','-m','tools.model_closeout.runner','run','--directory',str(self.experiments/batch)]
    def test_exact_direct_runner_and_wrapper_rejection(self):
        self.assertEqual(parse_runner(self.args(),self.path,self.experiments),'case')
        self.assertIsNone(parse_runner(['python.exe','tools/p0_audit/run_logged.py','--',*self.args()],self.path,self.experiments))
        self.assertIsNone(parse_runner(['python.exe','-m','tools.model_closeout.runner','run','--directory',str(self.experiments/'x')],self.path,self.experiments))
        self.assertIsNone(parse_runner(self.args()[:-1],self.path,self.experiments))
    def test_nonpython_name_never_reads_cmdline(self):
        p=FakeProcess(1,self.args(),name='notepad.exe')
        sampler=Sampler(FakeAPI([p]),self.experiments)
        rows,scan,errors=sampler.sample(0.,100.)
        self.assertEqual(p.cmd_reads,0);self.assertFalse(rows);self.assertEqual(scan['nonpython_skips'],1)
    def test_winerror87_is_contained_per_pid_and_other_worker_survives(self):
        bad=FakeProcess(1,self.args(),cmd_error=True);good=FakeProcess(2,self.args('good'))
        rows,scan,errors=Sampler(FakeAPI([bad,good]),self.experiments).sample(0.,100.)
        self.assertEqual([x['pid'] for x in rows],[2]);self.assertEqual(errors[0]['winerror'],87)
        self.assertEqual(scan['pid_read_errors'],1)
    def test_enum_oserror_is_a_recorded_gap(self):
        rows,scan,errors=Sampler(FakeAPI([],enum_error=True),self.experiments).sample(0.,100.)
        self.assertFalse(rows);self.assertIs(scan['enumeration_complete'],False)
        self.assertEqual(errors[0]['stage'],'pids')
    def test_cmdline_cache_expires_and_pid_reuse_does_not_inherit_skip(self):
        bad=FakeProcess(1,self.args(),cmd_error=True);sampler=Sampler(FakeAPI([bad]),self.experiments)
        sampler.sample(0.,100.);_,scan,_=sampler.sample(5.,105.)
        self.assertEqual(bad.cmd_reads,1);self.assertEqual(scan['cmdline_cache_skips'],1)
        sampler.sample(61.,161.);self.assertEqual(bad.cmd_reads,2)
        bad.created=2.;bad.cmd_error=False
        rows,_,_=sampler.sample(62.,162.);self.assertEqual(bad.cmd_reads,3);self.assertEqual(len(rows),1)
    def test_memory_oserror_does_not_abort_enumeration(self):
        rows,scan,errors=Sampler(FakeAPI([FakeProcess(1,self.args(),memory_error=True),FakeProcess(2,self.args('ok'))]),self.experiments).sample(0.,100.)
        self.assertEqual(len(rows),1);self.assertEqual(scan['pid_read_errors'],1)
        self.assertEqual(errors[0]['stage'],'memory_info')
    def test_cpu_delta_first_unknown_then_measured(self):
        p=FakeProcess(1,self.args());sampler=Sampler(FakeAPI([p]),self.experiments)
        first,_,_=sampler.sample(0.,100.);p.user+=2.
        second,_,_=sampler.sample(4.,104.)
        self.assertIsNone(first[0]['cpu_percent_single_core']);self.assertEqual(second[0]['cpu_percent_single_core'],50.)
        self.assertEqual(second[0]['lifetime_peak_working_set_bytes'],150)
    def test_empty_cmdline_is_unknown_and_recorded_not_a_nonrunner_claim(self):
        rows,scan,errors=Sampler(FakeAPI([FakeProcess(1,[])]),self.experiments).sample(0.,100.)
        self.assertFalse(rows);self.assertEqual(scan['pid_read_errors'],1)
        self.assertEqual(errors[0]['stage'],'cmdline')
    def test_previous_gap_comes_from_saved_csv_not_zero_imputation(self):
        folder=self.path/'old';folder.mkdir()
        (folder/'SAMPLES.csv').write_text('utc_epoch,pid,create_time\n100,1,1\n102,2,1\n',encoding='utf8')
        command=self.path/'command.json';write_json(command,dict(exit_code=1,finished_utc='1970-01-01T00:01:45+00:00'))
        gap=previous_evidence(folder/'SAMPLES.csv',command,self.path/'absent.stderr.log')
        self.assertEqual(gap['last_saved_sample_epoch'],102.)
        self.assertEqual(gap['gap_start_after_last_saved_sample_utc'],utc(102.))
        self.assertIs(gap['unobserved_gap_values_imputed'],False)
    def test_watch_synthetic_csv_and_stop_summary_without_process_control(self):
        p=FakeProcess(1,self.args())
        def stop_after_scan(seconds):write_json(self.path/'MONITOR_STOP.json',dict(synthetic_stop=True))
        code=watch('fixture',api=FakeAPI([p]),run=self.path,clock=lambda:0.,epoch=lambda:100.,sleeper=stop_after_scan)
        self.assertEqual(code,0)
        summary=json.loads((self.path/'review/resources_fixture/SUMMARY.json').read_text(encoding='utf8'))
        self.assertEqual(summary['sample_count'],1);self.assertEqual(summary['process_control_actions'],0)
        self.assertEqual(summary['peaks'][0]['sampled_private_peak_bytes'],80)
        self.assertEqual(summary['peaks'][0]['OS_lifetime_peak_working_set_bytes'],150)
        self.assertIs(summary['missing_sample_values_imputed'],False)

def python_name(value):return isinstance(value,str) and re.fullmatch(r'python(?:w|\d+(?:\.\d+)*)?(?:\.exe)?',value,re.I) is not None

def parse_runner(args,cwd,experiments):
    if (not isinstance(args,(list,tuple)) or len(args)<7 or not all(isinstance(x,str) for x in args)
            or not python_name(Path(args[0]).name)
            or list(args[1:5])!=['-B','-m','tools.model_closeout.runner','run']
            or args.count('--directory')!=1):return None
    index=args.index('--directory')+1
    if index>=len(args) or args[index].startswith('--'):return None
    path=Path(args[index]);path=(Path(cwd)/path).resolve() if not path.is_absolute() else path.resolve()
    return path.name if path.parent==Path(experiments).resolve() else None

def number(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def event(epoch,pid,stage,error,action,**extra):
    return dict(utc_epoch=epoch,utc=utc(epoch),pid=pid,stage=stage,error_type=type(error).__name__,
        error_message=str(error),winerror=getattr(error,'winerror',None),action=action,**extra)

class Sampler:
    def __init__(self,api,experiments):
        self.api=api;self.experiments=Path(experiments);self.cache={};self.cpu_previous={};self.index=0
    def sample(self,now,epoch):
        self.index+=1;rows=[];errors=[]
        scan=dict(scan_index=self.index,utc_epoch=epoch,utc=utc(epoch),enumeration_complete=False,
            pids_seen=0,nonpython_skips=0,python_candidates=0,not_direct_runner_skips=0,
            cmdline_cache_skips=0,pid_read_errors=0,matched_runners=0,samples_written=0,
            available_host_bytes=None,total_host_bytes=None)
        try:
            memory=self.api.virtual_memory();scan['available_host_bytes']=memory.available;scan['total_host_bytes']=memory.total
        except ERRORS as exc:errors.append(event(epoch,None,'host_memory',exc,'host_available_unknown'))
        try:pids=list(self.api.pids());scan['pids_seen']=len(pids);scan['enumeration_complete']=True
        except ERRORS as exc:
            errors.append(event(epoch,None,'pids',exc,'enumeration_failed; no process values imputed'))
            return rows,scan,errors
        present=set(pids)
        for pid in list(self.cache):
            if pid not in present:self.cache.pop(pid,None)
        for pid in pids:
            stage='Process';created=None;name=None
            try:
                process=self.api.Process(pid);stage='name';name=process.name()
                if not python_name(name):scan['nonpython_skips']+=1;continue
                scan['python_candidates']+=1;stage='create_time';created=process.create_time()
                cached=self.cache.get(pid)
                if cached and cached['create_time']==created and now<cached['until']:
                    scan['cmdline_cache_skips']+=1
                    errors.append(dict(utc_epoch=epoch,utc=utc(epoch),pid=pid,create_time=created,name=name,
                        stage='cmdline_cache',error_type=cached['error_type'],error_message=cached['error_message'],
                        winerror=cached['winerror'],action='skip_cached_cmdline',cache_until_monotonic=cached['until']))
                    continue
                self.cache.pop(pid,None);stage='cmdline';args=process.cmdline()
                if not args:raise OSError('Python command line is empty or unavailable; runner identity unknown')
                # Reject wrappers before reading additional per-process counters.
                if (not isinstance(args,(list,tuple)) or len(args)<5
                        or list(args[1:5])!=['-B','-m','tools.model_closeout.runner','run']):
                    scan['not_direct_runner_skips']+=1;continue
                stage='cwd';cwd=process.cwd();stage='parse_cmdline'
                batch=parse_runner(args,cwd,self.experiments)
                if batch is None:scan['not_direct_runner_skips']+=1;continue
                scan['matched_runners']+=1;stage='memory_info';memory=process.memory_info()
                stage='cpu_times';cpu=process.cpu_times();cpu_total=float(cpu.user)+float(cpu.system)
                previous=self.cpu_previous.get((pid,created));delta=None;elapsed=None;percent=None
                if previous:
                    elapsed=now-previous[0];difference=cpu_total-previous[1]
                    if elapsed>0 and difference>=0:delta=difference;percent=100.*delta/elapsed
                    else:errors.append(event(epoch,pid,'cpu_delta',ValueError('counter decreased or elapsed time nonpositive'),
                        'CPU delta unknown; sampled memory retained',create_time=created,name=name))
                self.cpu_previous[(pid,created)]=(now,cpu_total)
                rows.append(dict(scan_index=self.index,utc_epoch=epoch,utc=utc(epoch),pid=pid,create_time=created,
                    batch=batch,runner_cwd=str(cwd),working_set_bytes=getattr(memory,'rss',None),
                    private_bytes=getattr(memory,'private',None),lifetime_peak_working_set_bytes=getattr(memory,'peak_wset',None),
                    available_host_bytes=scan['available_host_bytes'],cpu_user_seconds=float(cpu.user),
                    cpu_system_seconds=float(cpu.system),cpu_total_seconds=cpu_total,cpu_delta_seconds=delta,
                    cpu_elapsed_seconds=elapsed,cpu_percent_single_core=percent))
            except ERRORS as exc:
                scan['pid_read_errors']+=1
                extra=dict(create_time=created,name=name)
                action='skip_pid_this_scan'
                if stage=='cmdline':
                    self.cache[pid]=dict(create_time=created,until=now+60.,error_type=type(exc).__name__,
                        error_message=str(exc),winerror=getattr(exc,'winerror',None))
                    action='skip_pid_cmdline_error_cached_60s';extra['cache_until_monotonic']=now+60.
                errors.append(event(epoch,pid,stage,exc,action,**extra))
        scan['samples_written']=len(rows)
        for row in errors:row.setdefault('scan_index',self.index)
        return rows,scan,errors

def previous_evidence(samples,command,stderr):
    value=dict(previous_monitor='resources_r1',previous_paths={},last_saved_sample_epoch=None,
        gap_start_after_last_saved_sample_utc=None,previous_finished_utc=None,previous_exit_code=None,
        unobserved_gap_values_imputed=False,read_errors=[])
    for kind,path in (('samples',samples),('command',command),('stderr',stderr)):
        path=Path(path)
        if not path.is_file():value['previous_paths'][kind]=dict(path=str(path),status='missing');continue
        try:
            value['previous_paths'][kind]=dict(path=str(path),sha256=sha(path),bytes=path.stat().st_size)
            if kind=='samples':
                with path.open(encoding='utf-8-sig',newline='') as stream:
                    times=[float(row['utc_epoch']) for row in csv.DictReader(stream) if row.get('utc_epoch')]
                if times:
                    value['last_saved_sample_epoch']=max(times)
                    value['gap_start_after_last_saved_sample_utc']=utc(max(times))
            elif kind=='command':
                data=json.loads(path.read_text(encoding='utf8'));value['previous_finished_utc']=data.get('finished_utc');value['previous_exit_code']=data.get('exit_code')
        except (OSError,ValueError,KeyError,IndexError) as exc:value['read_errors'].append(dict(kind=kind,type=type(exc).__name__,message=str(exc)))
    value['gap_semantics']='After the last saved R1 sample until the first successful R2 sample, values are unobserved; R1 exit timestamp is separately preserved. OS lifetime WSS peak may include that interval but has no peak timestamp.'
    return value

def watch(label,*,interval=5.,hours=48.,api=psutil,run=B,clock=time.monotonic,epoch=time.time,sleeper=time.sleep):
    run=Path(run).resolve();out=run/'review'/('resources_'+label);out.mkdir(parents=True,exist_ok=False)
    begun=clock();started=epoch();sampler=Sampler(api,run/'experiments');counts=Counter();error_counts=Counter()
    previous=previous_evidence(run/'review/resources_r1/SAMPLES.csv',run/'logs/resources_monitor_r1.command.json',
        run/'logs/resources_monitor_r1.stderr.log')
    config=run/'M19_MODEL_CONFIG.json';old_script=run/'scripts/monitor_resources.py'
    sources=dict(monitor_program_sha256=sha(SCRIPT),psutil_version=getattr(api,'__version__',getattr(psutil,'__version__',None)),
        model_config_path=str(config),model_config_file_sha256=sha(config) if config.is_file() else None,
        model_config_file_role='Reference metadata only; worker model configurations are not inferred or read.',
        old_monitor_program_sha256=sha(old_script) if old_script.is_file() else None)
    write_json(out/'START.json',dict(schema='hospital_ies.v19.resource_monitor.r2',started_utc=utc(started),
        started_epoch=started,sampling_seconds=interval,maximum_hours=hours,sources=sources,previous_monitor=previous,
        canonical_experiments=str(run/'experiments'),direct_runner_prefix=['python','-B','-m','tools.model_closeout.runner','run'],
        process_control_actions=0,cmdline_error_cache_seconds=60))
    sample_fields=['scan_index','utc_epoch','utc','pid','create_time','batch','runner_cwd','working_set_bytes','private_bytes',
        'lifetime_peak_working_set_bytes','available_host_bytes','cpu_user_seconds','cpu_system_seconds','cpu_total_seconds',
        'cpu_delta_seconds','cpu_elapsed_seconds','cpu_percent_single_core']
    scan_fields=['scan_index','utc_epoch','utc','enumeration_complete','pids_seen','nonpython_skips','python_candidates',
        'not_direct_runner_skips','cmdline_cache_skips','pid_read_errors','matched_runners','samples_written',
        'available_host_bytes','total_host_bytes']
    error_fields=['scan_index','utc_epoch','utc','pid','create_time','name','stage','error_type','error_message','winerror',
        'action','cache_until_monotonic']
    peaks={};minimum_free=None;sample_count=0;first_sample=None;last_sample=None;fatal=None;reason='maximum_duration_elapsed';exit_code=2
    try:
        with (out/'SAMPLES.csv').open('x',encoding='utf8',newline='') as sf,\
                (out/'ENUMERATION.csv').open('x',encoding='utf8',newline='') as ef,\
                (out/'ERRORS.csv').open('x',encoding='utf8',newline='') as xf:
            sw=csv.DictWriter(sf,fieldnames=sample_fields);ew=csv.DictWriter(ef,fieldnames=scan_fields)
            xw=csv.DictWriter(xf,fieldnames=error_fields,extrasaction='ignore')
            sw.writeheader();ew.writeheader();xw.writeheader()
            while clock()-begun<hours*3600.:
                if (run/'MONITOR_STOP.json').is_file():reason='MONITOR_STOP_present';exit_code=0;break
                observed=epoch();rows,scan,errors=sampler.sample(clock(),observed)
                ew.writerow(scan)
                for field in ('pids_seen','nonpython_skips','python_candidates','not_direct_runner_skips',
                        'cmdline_cache_skips','pid_read_errors','matched_runners','samples_written'):
                    counts[field]+=scan[field]
                counts['scans']+=1;counts['enumeration_failures']+=int(not scan['enumeration_complete'])
                available=scan['available_host_bytes']
                if number(available):minimum_free=available if minimum_free is None else min(minimum_free,available)
                for error in errors:
                    error.setdefault('scan_index',scan['scan_index']);xw.writerow(error)
                    error_counts[error['stage']+':'+error['error_type']]+=1
                for row in rows:
                    sw.writerow(row);sample_count+=1
                    first_sample=row['utc_epoch'] if first_sample is None else first_sample;last_sample=row['utc_epoch']
                    key=(row['pid'],row['create_time']);peak=peaks.get(key)
                    if peak is None:
                        peak=dict(pid=row['pid'],create_time=row['create_time'],batch=row['batch'],
                            first_R2_sample_utc=row['utc'],last_R2_sample_utc=row['utc'],samples=0,
                            sampled_working_set_peak_bytes=None,sampled_private_peak_bytes=None,
                            OS_lifetime_peak_working_set_bytes=None,first_sample_cpu_total_seconds=row['cpu_total_seconds'],
                            last_sample_cpu_total_seconds=row['cpu_total_seconds'],cpu_observed_delta_seconds=None)
                        peaks[key]=peak
                    peak['samples']+=1;peak['last_R2_sample_utc']=row['utc'];peak['last_sample_cpu_total_seconds']=row['cpu_total_seconds']
                    peak['cpu_observed_delta_seconds']=row['cpu_total_seconds']-peak['first_sample_cpu_total_seconds'] if peak['samples']>1 else None
                    for src,dst in (('working_set_bytes','sampled_working_set_peak_bytes'),('private_bytes','sampled_private_peak_bytes'),
                            ('lifetime_peak_working_set_bytes','OS_lifetime_peak_working_set_bytes')):
                        if number(row[src]):peak[dst]=row[src] if peak[dst] is None else max(peak[dst],row[src])
                sf.flush();ef.flush();xf.flush()
                remaining=hours*3600.-(clock()-begun)
                if remaining>0:sleeper(min(interval,remaining))
    except KeyboardInterrupt:reason='monitor_keyboard_interrupt';exit_code=130
    except Exception as exc:
        reason='unexpected_monitor_error';exit_code=1;fatal=dict(type=type(exc).__name__,message=str(exc))
    ended=epoch()
    summary=dict(schema='hospital_ies.v19.resource_monitor.r2',sources=sources,started_utc=utc(started),finished_utc=utc(ended),
        observed_seconds=clock()-begun,exit_code=exit_code,termination_reason=reason,fatal_error=fatal,
        sample_count=sample_count,first_R2_sample_utc=utc(first_sample) if first_sample is not None else None,
        last_R2_sample_utc=utc(last_sample) if last_sample is not None else None,
        previous_monitor=previous,minimum_observed_available_host_bytes=minimum_free,peaks=list(peaks.values()),
        sampling_seconds=interval,maximum_hours=hours,scan_counters=dict(counts),error_counts=dict(error_counts),
        MONITOR_STOP_present=(run/'MONITOR_STOP.json').is_file(),process_control_actions=0,
        missing_sample_values_imputed=False,cmdline_error_cache_seconds=60,cache_identity='pid plus create_time; reuse invalidates cache',
        lifetime_peak_working_set_is_OS_reported=True,
        lifetime_peak_timestamp_unknown=True,private_peak_is_sampled_not_absolute=True,
        CPU_percent_denominator='one logical core; may exceed 100 for multithreaded process; first/dropped counter delta is unknown',
        sampling_omission_boundaries=[
            'PID enumeration is a point-in-time snapshot; processes may start or end between scans.',
            'Only exact direct python -B -m tools.model_closeout.runner run commands in canonical experiments are included; wrappers excluded.',
            'Each name/cmdline/cwd/create_time/memory/CPU failure skips that PID for that scan and is recorded.',
            'Cmdline error cache TTL is 60 seconds; retry occurs on the first scan after expiry, with every cache skip recorded. Actual scan delays are visible in ENUMERATION.csv.',
            'OS lifetime WSS peak can include periods before R2 and the R1 gap but has no peak time; private peaks and CPU deltas cover saved samples only.',
            'No sampled maximum is a guarantee of the unsampled instantaneous resource maximum.'],
        no_physical_input_or_model_parameter_changes=True)
    if previous['last_saved_sample_epoch'] is not None:
        summary['R1_to_R2_unobserved_seconds']=first_sample-previous['last_saved_sample_epoch'] if first_sample is not None else None
    summary['output_sha256']={path.name:sha(path) for path in out.iterdir() if path.is_file()}
    write_json(out/'SUMMARY.json',summary)
    print(json.dumps(dict(output=str(out),samples=sample_count,observed_processes=len(peaks),exit_code=exit_code,
        termination_reason=reason),ensure_ascii=False))
    return exit_code

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    test=sub.add_parser('self-test');test.add_argument('--label',required=True)
    monitor=sub.add_parser('watch');monitor.add_argument('--label',default='r2')
    monitor.add_argument('--interval',type=float,default=5.);monitor.add_argument('--hours',type=float,default=48.)
    a=p.parse_args()
    if not re.fullmatch('[A-Za-z0-9_]+',a.label):raise ValueError('Fresh simple label required')
    if a.command=='watch':
        if not number(a.interval) or not .5<=a.interval<=60. or not number(a.hours) or not 0<a.hours<=48.:
            raise ValueError('Sampling interval .5–60 seconds and positive duration no more than 48h required')
        return watch(a.label,interval=a.interval,hours=a.hours)
    MonitorTests.root=B/'review/test_tmp'/('monitor_r2_selftest_'+a.label);MonitorTests.root.mkdir(parents=True,exist_ok=False)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MonitorTests))
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
