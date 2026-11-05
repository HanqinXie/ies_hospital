"""Concurrent topic rendering for new U and exactly preserved run03 R/N.

Only topic processes draw in parallel. Workbook writing, merged verification,
contact sheets and manifest creation remain in the parent process.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from copy import deepcopy
from datetime import datetime,timezone
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import sys
import time

for _name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[_name]='1'
os.environ.setdefault('MPLBACKEND','Agg')
SOURCE=Path(__file__).resolve().parents[1]
GiB=1024**3
SCHEMA='hospital_ies.source_partition_fix.plot_data.v1'


def load_module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


base=load_module(SOURCE/'release/no_priority_figures_20261105.py','source_partition_legacy_figures')


def utc():return datetime.now(timezone.utc).isoformat()


def execution_roles():
    return dict(normal='unchanged_frozen_original_reference',
        unattended='new_source_partition_arithmetic_execution',response='unchanged_frozen_run03_response')


def validate_data(data):
    if (data.get('schema')!=SCHEMA or data.get('only_unattended_new_execution') is not True
            or data.get('response_new_execution') is not False or data.get('new_fault_trajectories')!=8
            or data.get('all_fault_U_R_newly_executed') is not False):
        raise ValueError('Only explicitly identified U-only source-partition plot data is accepted')
    # Adapt only the schema tag for the unchanged original shape/value checks.
    compatibility=dict(data,schema=base.SCHEMA)
    base.validate_data(compatibility)


def worker_topic(topic,destination,helper_paths):
    import psutil
    if psutil.virtual_memory().available<6*GiB:raise RuntimeError('Available RAM below the 6GiB rendering reserve')
    began=time.perf_counter();destination=Path(destination)
    helpers={name:base.load_helper(Path(path),'source_partition_'+name+'_draw') for name,path in helper_paths.items()}
    records=base.render_one(helpers['total'],base.adapt_topic(topic,None),None,destination)
    for priority in range(4):
        if psutil.virtual_memory().available<6*GiB:raise RuntimeError('Available RAM below the 6GiB rendering reserve')
        records+=base.render_one(helpers['tier'],base.adapt_topic(topic,priority),f'P{priority}',destination)
    info=psutil.Process().memory_info()
    return dict(topic=topic['id'],figures=records,elapsed_seconds=time.perf_counter()-began,
        pid=os.getpid(),RSS_bytes=info.rss,peak_working_set_bytes=getattr(info,'peak_wset',None),
        completed_utc=utc())


def render(data_path,destination,bundle,max_workers=4,worker_budget_GiB=1.5,RAM_reserve_GiB=6.):
    import psutil
    data_path=Path(data_path).resolve(strict=True);destination=Path(destination).resolve();bundle=Path(bundle).resolve(strict=True)
    if destination.exists():raise ValueError('Fresh nonexistent candidate directory required')
    if destination.drive.upper()!='E:':raise ValueError('Render candidates belong in the explicit E evidence workspace')
    if not 1<=max_workers<=4 or worker_budget_GiB<=0 or RAM_reserve_GiB<6.:
        raise ValueError('One to four topic workers and at least 6GiB reserve are required')
    data=json.loads(data_path.read_text(encoding='utf8'));validate_data(data)
    source_sha=base.sha(data_path);root=data_path.parent.parent
    api=load_module(SOURCE/'release/source_partition_rerun_20261105.py','source_partition_render_binding_api')
    api.verify_binding(root)
    acceptance=api.read(root/'ACCEPTANCE.json')
    if (acceptance.get('status')!='passed' or acceptance.get('complete_topic_count')!=8
            or acceptance.get('plot_data_verified') is not True
            or acceptance.get('plot_data')!=api.pin(data_path)):
        raise ValueError('Complete U-only numerical and frozen-reference acceptance required before rendering')
    if psutil.disk_usage(str(destination.parent)).free<5*GiB:raise RuntimeError('Insufficient E space for candidate exports')
    destination.mkdir(parents=True);(destination/'data').mkdir();(destination/'scripts').mkdir();(destination/'logs').mkdir()
    shutil.copyfile(data_path,destination/'data/PLOT_DATA.json')
    for path in (Path(__file__),SOURCE/'release/no_priority_figures_20261105.py'):
        shutil.copyfile(path,destination/'scripts'/path.name)
    helper_paths={};helper_sources={}
    for name,path in base.HELPERS.items():
        copied=destination/'scripts'/(name+'_draw_helper.py');shutil.copyfile(path,copied)
        helper_paths[name]=str(copied)
        helper_sources[name]=dict(path=str(path),sha256=base.sha(path),copied=copied.relative_to(destination).as_posix())
    budget=int(worker_budget_GiB*GiB);reserve=int(RAM_reserve_GiB*GiB)
    queue=list(data['topics']);active={};completed={};records=[];began=utc();failure=None
    context=multiprocessing.get_context('spawn')
    pool=ProcessPoolExecutor(max_workers=max_workers,mp_context=context)
    try:
        with (destination/'logs/RENDER_LEDGER.jsonl').open('x',encoding='utf8') as ledger:
            while queue or active:
                # Reserve the full budget for every submitted job. This is
                # intentionally conservative when other applications use RAM.
                while queue and len(active)<max_workers and psutil.virtual_memory().available>=reserve+(len(active)+1)*budget:
                    topic=queue.pop(0);future=pool.submit(worker_topic,topic,str(destination),helper_paths)
                    active[future]=topic['id']
                    ledger.write(json.dumps(dict(event='submitted',topic=topic['id'],utc=utc(),
                        available_RAM_bytes=psutil.virtual_memory().available,active_topics=len(active),
                        worker_budget_bytes=budget,RAM_reserve_bytes=reserve))+'\n');ledger.flush()
                if not active:
                    raise RuntimeError('Insufficient RAM for one admitted render worker; no more topics submitted')
                done,_=wait(active,timeout=1.,return_when=FIRST_COMPLETED)
                for future in done:
                    topic_id=active.pop(future);value=future.result();completed[topic_id]=value
                    records+=value['figures']
                    receipt={key:item for key,item in value.items() if key!='figures'}
                    ledger.write(json.dumps(dict(event='completed',**receipt))+'\n');ledger.flush()
                    print('Exported '+topic_id+': 15 PNG/SVG pairs',flush=True)
    except BaseException as exc:
        failure=dict(type=type(exc).__name__,message=str(exc),completed_topics=sorted(completed),
            not_submitted_topics=[topic['id'] for topic in queue],pending_topics=list(active.values()),utc=utc())
        for future in active:future.cancel()
        base.write_json(destination/'RENDER_FAILURE.json',failure)
        raise
    finally:
        pool.shutdown(wait=True,cancel_futures=True)
    order={f'T{i:02d}':i for i in range(1,9)}
    records.sort(key=lambda item:(order[item['topic']],-1 if item['priority'] is None else int(item['priority'][1:]),
        {'cooling':0,'heating':1,'electricity':2}[item['carrier']]))
    workbooks=base.update_workbooks(bundle,destination,records)
    # Keep the original values, style and provenance layout while correcting
    # its execution-role wording for this campaign. Only U was recalculated.
    import openpyxl
    for workbook in workbooks:
        book=openpyxl.load_workbook(destination/workbook['path'])
        for sheet in book:
            sheet['H10']='Response reference';sheet['I10']='Frozen run03 response; not recomputed in this campaign'
            sheet['I9']='New arithmetic U calculation with ordinary and dedicated source partitions'
        book.save(destination/workbook['path']);book.close()
        workbook['sha256']=base.sha(destination/workbook['path'])
    checks=base.verify_exports(destination,records,workbooks)
    previews=base.contact_sheets(destination,records)
    if base.sha(data_path)!=source_sha:raise ValueError('Input plot data changed while rendering')
    api.verify_binding(root)
    manifest=dict(schema='hospital_ies.fixed_branch_rerun.figure_exports.v1',backend='Python/matplotlib',
        source_data=dict(path=str(data_path),sha256=source_sha,copied='data/PLOT_DATA.json'),
        renderer=dict(path=str(Path(__file__).resolve()),sha256=base.sha(__file__)),
        reused_renderer=dict(path=str(SOURCE/'release/no_priority_figures_20261105.py'),
            sha256=base.sha(SOURCE/'release/no_priority_figures_20261105.py')),
        helper_sources=helper_sources,**execution_roles(),new_fault_trajectories=8,new_R_trajectories=0,
        normal_reference='frozen_original_reference_not_recomputed',response_reference='frozen_run03_response_not_recomputed',
        source_conditions={topic['id']:{role:topic['conditions'][role]['source'] for role in base.ROLES} for topic in data['topics']},
        PNG_dpi=600,editable_SVG_text=True,figure_width_mm=183,figure_height_mm=145,
        figures=records,workbooks=workbooks,previews=previews,verification=checks,
        parallel_rendering=dict(max_workers=max_workers,worker_budget_bytes=budget,RAM_reserve_bytes=reserve,
            started_utc=began,completed_utc=utc(),topic_receipts={key:{k:v for k,v in value.items() if k!='figures'}
                for key,value in completed.items()},workbooks_written_by_parent_only=True),
        policy='Native held intervals; exact run03 R/N; new U only; no smoothing, interpolation, offsets or quota reassignment',
        published=False)
    base.write_json(destination/'MANIFEST.json',manifest)
    print(json.dumps(checks),flush=True)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('render',))
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--bundle',type=Path,required=True);parser.add_argument('--max-workers',type=int,default=4)
    parser.add_argument('--worker-budget-gib',type=float,default=1.5);parser.add_argument('--ram-reserve-gib',type=float,default=6.)
    args=parser.parse_args();render(args.data,args.destination,args.bundle,args.max_workers,args.worker_budget_gib,args.ram_reserve_gib)
    return 0


if __name__=='__main__':raise SystemExit(main())
