"""Observe only V19 runner resource use; never control a process or a model."""
from pathlib import Path
import csv
import hashlib
import json
import time
import psutil

B=Path(__file__).resolve().parent.parent

def main():
    out=B/'review'/'resources_r1';out.mkdir(parents=True,exist_ok=False)
    begun=time.monotonic();peaks={};minimum_free=None;samples=0
    fields=['utc_epoch','pid','create_time','batch','working_set_bytes','private_bytes','lifetime_peak_working_set_bytes','available_host_bytes']
    with (out/'SAMPLES.csv').open('x',encoding='utf8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        while time.monotonic()-begun<48*3600:
            free=psutil.virtual_memory().available;minimum_free=free if minimum_free is None else min(minimum_free,free)
            for p in psutil.process_iter(['pid','cmdline','create_time']):
                try:
                    args=p.info['cmdline'] or []
                    if 'tools.model_closeout.runner' not in args or '--directory' not in args:continue
                    if args[args.index('tools.model_closeout.runner')+1]!='run':continue
                    directory=Path(args[args.index('--directory')+1]).resolve()
                    if directory.parent!=(B/'experiments').resolve():continue
                    memory=p.memory_info();row=dict(utc_epoch=time.time(),pid=p.pid,create_time=p.info['create_time'],
                        batch=directory.name,working_set_bytes=memory.rss,private_bytes=memory.private,
                        lifetime_peak_working_set_bytes=memory.peak_wset,available_host_bytes=free)
                    writer.writerow(row);samples+=1
                    key=(p.pid,p.info['create_time']);previous=peaks.get(key)
                    if previous is None:peaks[key]=row.copy()
                    else:
                        for field in ('working_set_bytes','private_bytes','lifetime_peak_working_set_bytes'):
                            previous[field]=max(previous[field],row[field])
                except (psutil.NoSuchProcess,psutil.AccessDenied,IndexError,OSError):continue
            stream.flush()
            # The original queue's terminal record proves only its own children have exited.
            if (B/'MONITOR_STOP.json').is_file():break
            time.sleep(5.)
    value=dict(helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        model_config_file_sha256=hashlib.sha256((B/'M19_MODEL_CONFIG.json').read_bytes()).hexdigest(),
        observed_seconds=time.monotonic()-begun,sample_count=samples,minimum_observed_free_bytes=minimum_free,
        peaks=list(peaks.values()),sampling_seconds=5,process_control_actions=0,
        lifetime_peak_working_set_is_OS_reported=True,private_peak_is_sampled_not_an_absolute_guarantee=True,
        original_queue_terminal=(B/'MONITOR_STOP.json').is_file(),
        no_physical_input_or_model_parameter_changes=True)
    with (out/'SUMMARY.json').open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(samples=samples,observed_processes=len(peaks),original_queue_terminal=value['original_queue_terminal'])))
    return 0 if value['original_queue_terminal'] else 2

if __name__=='__main__':raise SystemExit(main())
