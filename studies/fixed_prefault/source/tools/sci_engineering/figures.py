"""Traceable CSV/MAT inputs for MATLAB; no Python rendering or interpolation."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def _sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def export_process(raw_path,output,expected_sha256):
    """One row per actual native interval; actual receipt inventories are kept."""
    from scipy.io import savemat
    raw_path=Path(raw_path)
    if _sha(raw_path)!=expected_sha256:raise ValueError('Raw trajectory SHA256 differs from its actual RESULT binding')
    with gzip.open(raw_path,'rt',encoding='utf-8') as stream:payload=json.load(stream)
    segments=payload.get('segments',[])
    if not segments:raise ValueError('No actual intervals to plot')
    if payload.get('summary',{}).get('status')!='completed':raise ValueError('Main process figure needs a complete actual trajectory')
    rows=[];previous=0.
    for segment in segments:
        a,b=float(segment['start_minute']),float(segment['end_minute'])
        if not math.isfinite(a+b) or b<=a or abs(a-previous)>1e-7:
            raise ValueError('Actual native process intervals overlap or have an unknown gap')
        previous=b;row=dict(start_minute=a,end_minute=b)
        ledger=segment.get('service_request_ledger')
        if not isinstance(ledger,dict):raise ValueError('Missing actual M21 original service request ledger')
        request,cut,connected,delivered=(np.asarray(ledger.get(key),float) for key in
            ('original_demand_kW','active_cut_kW','connected_unserved_kW','delivered_kW'))
        arrays=(request,cut,connected,delivered)
        if any(array.shape!=(4,3) or not np.isfinite(array).all() or np.any(array < -1e-8) for array in arrays):
            raise ValueError('Missing or invalid actual carrier service ledger matrices')
        if not np.allclose(request,cut+connected+delivered,atol=1e-7,rtol=0.):
            raise ValueError('Actual original request does not equal delivered plus both shortages')
        unmet=cut+connected
        for ip,p in enumerate(('P0','P1','P2','P3')):
            for ic,c in enumerate(('e','h','c')):
                for label,value in (('requested',request[ip,ic]),('delivered',delivered[ip,ic]),('unserved',unmet[ip,ic]),
                    ('disconnected',cut[ip,ic]),('connected_unserved',connected[ip,ic])):
                    row[f'{p}_{c}_{label}_kW']=float(value)
        for key,value in segment.get('action',{}).items():
            if isinstance(value,list):
                for i,v in enumerate(value):row[key+'_'+str(i+1)]=float(v)
            elif isinstance(value,(int,float)):row[key]=float(value)
        for store,receipt in segment.get('m21_storage',{}).items():
            for key in ('initial_kWh','final_kWh','charged_input_kWh','discharged_kWh','standing_loss_kWh','conversion_loss_kWh'):
                if key in receipt:row[store+'_'+key]=float(receipt[key])
        state=segment.get('pre_state',{})
        for key in ('diesel_energy_remaining_kWh',):
            if isinstance(state.get(key),(int,float)):row['pre_'+key]=float(state[key])
        state=segment.get('post_state',{})
        for key in ('diesel_energy_remaining_kWh',):
            if isinstance(state.get(key),(int,float)):row['post_'+key]=float(state[key])
        for key,value in segment.get('availability',{}).items():row['availability_'+key]=float(value)
        for key,value in segment.get('temperature',{}).items():
            if isinstance(value,(int,float)):row['exploratory_temperature_'+key]=float(value)
        if any(not math.isfinite(v) for v in row.values()):raise ValueError('Nonfinite native process values')
        rows.append(row)
    output=Path(output)
    if output.exists():raise FileExistsError('Use a fresh process-figure source directory')
    output.mkdir(parents=True)
    fields=sorted(set.union(*(set(r) for r in rows)))
    with (output/'PROCESS.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    savemat(output/'PROCESS.mat',dict(columns=np.array(fields,dtype=object),
        values=np.array([[r.get(k,np.nan) for k in fields] for r in rows]),
        interval_semantics='Native half-open actual execution intervals; no resampling',
        raw_sha256=expected_sha256),do_compression=True)
    receipt=dict(raw_path=str(raw_path.resolve()),raw_sha256=expected_sha256,
        execution_model_id=payload.get('execution_model_id'),scenario_id=payload.get('scenario',{}).get('id'),
        policy_id=payload.get('summary',{}).get('policy_id'),native_intervals=len(rows),resampled=False,
        observed_start_minute=rows[0]['start_minute'],observed_end_minute=rows[-1]['end_minute'],
        process_csv_sha256=_sha(output/'PROCESS.csv'),scientific_figures_generated=0)
    (output/'PROCESS_RECEIPT.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False),encoding='utf-8')
    return receipt


def figure_contract(directory):
    directory=Path(directory)
    if not (directory/'CASE_METRICS.csv').is_file():raise ValueError('Actual analysis table CASE_METRICS.csv is required')
    definitions=[
        dict(id='F1_service_comparison',source='CASE_METRICS.csv',
            question='What critical carrier services are lost in the five matched upper arrangements?',
            plot='Six independent ENS panels for P0/P1 x e/h/c; balanced complete five-policy cases only',
            guard='No addition across carriers; class counts and excluded failed/unknown cases must be disclosed'),
        dict(id='F2_service_cost',source='CASE_METRICS.csv',
            question='What general-service and same-window conditional operating costs accompany critical service?',
            plot='P2/P3 ENS and conditional fee/CO2 panels; retain negative and unchanged results',
            guard='Initial inventory and actual recharge are accounted once; CO2 remains a conditional post-run estimate'),
        dict(id='F3_mechanism',source='MECHANISM_EFFECTS.csv',
            question='What are the separate reserve, critical-preparation and interaction effects?',
            plot='Four signed effects from complete matched 2x2 cells, source blocks as independent units',
            guard='Do not use C18 minus C19 as a single mechanism estimate'),
        dict(id='F4_hardware',source='HARDWARE_DIFFERENCES.csv',
            question='Which bridge or cold-path bottlenecks do H1 and H2 remove and at what cost?',
            plot='H1 versus H0 and H2 versus H0 separately for C and A',
            guard='Do not confound new policy and new hardware or compare unmatched windows'),
        dict(id='F5_boundary',source='CASE_METRICS.csv',
            question='Where does a first critical service gap appear across registered resource axes?',
            plot='Duration x initial shaft reserve and demand_amplitude x rejection_capacity_kW registered maps; post-handoff gaps, initial loss and censoring kept separate',
            guard='NECESSARY_BOUNDARY_CONDITIONS.csv is a separate optimistic diagnostic, never an online policy'),
        dict(id='F6_process',source='PROCESS.csv',
            question='Who loses service, which real source takes over, how long does it last and how does it return?',
            plot='Actual interval stairs of requested/delivered e/h/c, actual source ports and native inventory endpoints',
            guard='No new synthetic samples; never turn administrative end into an observed endurance limit')]
    for item in definitions:
        source=directory/item['source'];item['source_present']=source.is_file()
        if source.is_file():item['source_sha256']=_sha(source)
    return dict(schema='hospital_ies.sci_figure_contract.v21.v1',plot_backend='MATLAB',
        native_interval_processes_only=True,scientific_figures_generated=0,
        font_chinese='\u5b8b\u4f53',font_latin_numeric='Times New Roman',font_points=9,
        editable_outputs=['FIG','SVG','PDF'],proof_output='PNG 600 dpi',figures=definitions,
        actual_data_directory=str(directory.resolve()),no_placeholder_or_mock_figure=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',type=Path)
    parser.add_argument('--raw',type=Path);parser.add_argument('--raw-sha256')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.raw:
        if not args.raw_sha256:parser.error('--raw-sha256 is required')
        result=export_process(args.raw,args.output,args.raw_sha256)
    elif args.analysis:
        result=figure_contract(args.analysis)
        if args.output.exists():raise FileExistsError('Figure contract output already exists')
        args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    else:parser.error('Use --analysis or --raw')
    print(json.dumps(result,ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
