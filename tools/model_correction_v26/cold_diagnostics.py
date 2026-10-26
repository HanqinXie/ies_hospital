"""Three preregistered cold diagnostics; no actuation or physical parameter changes."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd

from tools.model_correction_v26 import cold_observer as observer
from tools.model_correctness_v23.thermal_data import read_native,sha256
from tools.model_correction_v25.thermal_observation import align_completed_api
from tools.engineering_completion_v24.thermal_nodes import sizing


COUNTERS=('HVAC System Solver Iteration Count','Air System Solver Iteration Count',
          'Plant Solver Sub Iteration Count','Plant Solver Half Loop Calls Count')
RUN_IDS=('H1_NORMAL_STAGE_TRACE','H2_NORMAL_MIN8_MAX8','H2_ALL_COLD_OFF_MIN8_MAX8')
SPEC_SHA='1aedde415fce17b241338d08d40a85b8f50d7099fa14bc7c3549b40168171e45'
TRACE_BUCKET=pd.Timestamp('2006-07-20 16:50')


def iteration_channels():
    return {'simhvac__'+re.sub('[^a-z0-9]+','_',name.lower()).strip('_'):
            dict(key='SimHVAC',variable=name,unit='1',native_clock='System',
                 scope='live cumulative source pointer within SimHVAC; never sum callback samples',
                 purpose='limited numerical diagnostic') for name in COUNTERS}


def non_numeric_physics(text):
    return [f for f in observer.physics_objects(text) if f[0]!='convergencelimits']


def derive_numeric_idf(text,min8):
    objects=[f for f in observer.idf_objects(text) if f[0].lower()=='convergencelimits']
    if len(objects)!=1 or objects[0][1:]!=['1','20']:
        raise ValueError('Expected original ConvergenceLimits literal 1,20 and default plant2/8')
    if not min8:return text
    out,count=re.subn(r'(?im)^\s*ConvergenceLimits\s*,[^;]*;', 'ConvergenceLimits,1,20,8,8;',text)
    if count!=1 or non_numeric_physics(out)!=non_numeric_physics(text):
        raise ValueError('Numerical diagnostic changed an unauthorized physical/control object')
    return out


def qualify_counter_metadata(metadata,handles):
    fields=list(csv.reader(io.StringIO(metadata)))
    result=[]
    for label,c in iteration_channels().items():
        matching=[f for f in fields if len(f)>=4 and f[0]=='OutputVariable' and f[1]==c['variable'] and f[2].upper()=='SIMHVAC' and f[3].replace(' ','') in ('','[]')]
        if len(matching)!=1 or handles.get(label,-1)<0:raise ValueError('Missing/mismatched live iteration counter '+c['variable'])
        result.append(dict(label=label,variable=c['variable'],actual_handle=handles[label],actual_metadata=matching[0]))
    return result


def resolve_trace_group(rows,clock):
    result=[]
    for row in rows:
        stage=row['stage']
        result.append(dict(row,**clock,integration_weight_h=0.,
            trial_dt_matches_accepted=bool(abs(row['api_dt_h']-clock['dt_h'])<1e-12),
            accepted_completed_sample=stage=='after',
            stage_semantics={'inside':'PRE_CURRENT_EMS_PRE_CURRENT_SOLVE','before':'END_SYSTEM_BEFORE_HVAC_REPORTING',
                             'after':'END_SYSTEM_AFTER_HVAC_REPORTING'}[stage],
            time_semantics='accepted interval association; Inside rows are candidate states, may include discarded zone trial'))
    return result


class StageTrace:
    """Trace one preregistered physical bucket; candidate samples have zero time weight."""
    def __init__(self,topology):
        nodes={c[k].lower() for c in topology['cold_components'] for k in ['inlet','outlet']}
        components={c['name'].lower() for c in topology['cold_components']}
        self.keys=nodes|components|{c['name'].lower() for c in topology['cold_loops']}
        self.group=[];self.groups=[];self.rows=[];self.ordinal=0;self.accepted=0;self.counter_metadata=None
        self.counts=dict(inside=0,before=0,after=0)

    def attach(self,api,state,ready,handles,channels,destination,failures):
        self.api=api;self.ready=ready;self.handles=handles;self.destination=destination;self.failures=failures
        self.labels=[label for label,c in channels.items() if c['key'].lower() in self.keys or c['key']=='SimHVAC']
        def sample(stage):
            def callback(s):
                try:self.capture(s,stage)
                except Exception as exc:failures.append('diagnostic '+repr(exc));api.runtime.stop_simulation(s)
            return callback
        api.runtime.callback_inside_system_iteration_loop(state,sample('inside'))
        api.runtime.callback_end_system_timestep_before_hvac_reporting(state,sample('before'))

    def capture(self,s,stage):
        if not self.ready(s):return
        if self.counter_metadata is None:
            self.counter_handles={k:self.handles['port',k] for k in iteration_channels()}
            if any(h<0 for h in self.counter_handles.values()):raise ValueError('Missing actual integer counter handle')
            self.counter_metadata='pending actual SQL dictionary validation after simulation closes'
        self.ordinal+=1;self.counts[stage]+=1
        ex=self.api.exchange
        self.group.append(dict(callback_ordinal=self.ordinal,stage=stage,
            api_dt_h=float(ex.system_time_step(s)),api_current_time_h=float(ex.current_time(s)),
            api_month=ex.month(s),api_day=ex.day_of_month(s),api_hour=ex.hour(s),
            api_zone_step=ex.zone_time_step_number(s),api_dst=ex.daylight_savings_time_indicator(s),
            **{label:ex.get_variable_value(s,self.handles['port',label]) for label in self.labels}))

    def after_system(self,s):
        self.capture(s,'after');self.accepted+=1
        self.groups.append([dict(r,accepted_system_sequence=self.accepted) for r in self.group]);self.group=[]

    def resolve_bucket(self,clocks):
        if len(self.groups)!=len(clocks):raise ValueError('Trace/accepted system interval association failed')
        for rows,clock in zip(self.groups,clocks):
            if clock['bucket_end']==TRACE_BUCKET:self.rows.extend(resolve_trace_group(rows,clock))
        self.groups=[]

    def finish(self):
        pd.DataFrame(self.rows).to_csv(self.destination/'STAGE_TRACE.csv',index=False)
        con=sqlite3.connect(f'file:{(self.destination/"eplusout.sql").as_posix()}?mode=ro',uri=True)
        actual=con.execute('select Name,KeyValue,Units from ReportDataDictionary where Name in (?,?,?,?)',COUNTERS).fetchall();con.close()
        metadata='\n'.join('OutputVariable,'+name+','+key+','+(unit or '') for name,key,unit in actual)
        self.counter_metadata=qualify_counter_metadata(metadata,getattr(self,'counter_handles',{}))
        report=dict(trace_bucket_end=str(TRACE_BUCKET),physical_window='2006-07-20 (16:40,16:50]',
            callback_counts_full_run=self.counts,saved_trace_rows=len(self.rows),trace_columns=len(self.labels),
            counter_metadata=self.counter_metadata,unresolved_candidate_rows=len(self.group),unresolved_groups=len(self.groups),
            integration_forbidden=True,inside_stage='PRE_CURRENT_EMS_PRE_CURRENT_SOLVE',
            first_inside_air_count_may_be_previous_SimHVAC=True,
            metadata_source='actual SQL dictionary plus actual API handle; API inventory deliberately omits Integer outputs in api_datatransfer.cc:205',
            callback_registration_only_no_actuator_or_state_setters=True)
        (self.destination/'TRACE_RECEIPT.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
        if not self.rows or self.group or self.groups or self.counter_metadata is None:raise RuntimeError('Incomplete/unknown diagnostic trace retained')


def prepare(base_registration,spec_path,tolerance_path,destination):
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=False)
    base=json.loads(Path(base_registration).read_text(encoding='utf8'))
    spec=json.loads(Path(spec_path).read_text(encoding='utf8'))
    if sha256(spec_path)!=SPEC_SHA or spec['run_order']!=list(RUN_IDS):raise ValueError('Unapproved diagnostic specification')
    tolerance=json.loads(Path(tolerance_path).read_text(encoding='utf8'))
    observer.require_normal_gate(dict(normal_for_tolerance=False),tolerance,base['channel_contract_sha256'])
    cases=[]
    for number,run in enumerate(spec['runs']):
        old=next(c for c in base['cases'] if c['case_id']==run['base_case'])
        case=dict(old);case.pop('retained_normal_observation',None)
        channels=dict(old['channels'],**iteration_channels())
        original=Path(case['source_idf']).read_text(encoding='utf-8-sig')
        derived=observer.append_observers(derive_numeric_idf(original,number>0),channels)
        case.update(case_id=run['id'],base_case_id=old['case_id'],normal_for_tolerance=False,
            numerical_solver_diagnostic=number>0,raw_observation_component_identity='frozen_A_Diagnostic_03',
            channels=channels,observer_idf_sha256=hashlib.sha256(derived.encode()).hexdigest(),
            physical_control_ems_preserved=True,numeric_change=None if number==0 else {'minimum_plant':{'before':2,'after':8},'maximum_plant':8},
            inherited_observation_directory=str(Path(base['output_root'])/old['case_id']))
        cases.append(case)
    source_files={p:sha256(p) for p in base['source_files']}
    source_files[str(Path(__file__).resolve())]=sha256(__file__)
    reg=dict(base,version='V26_limited_diagnostic_03',created_utc=datetime.now(timezone.utc).isoformat(),
        source_files=source_files,output_root=str(destination/'raw'),cases=cases,
        base_registration=str(Path(base_registration).resolve()),base_registration_sha256=sha256(base_registration),
        fixed_tolerance=str(Path(tolerance_path).resolve()),fixed_tolerance_sha256=sha256(tolerance_path),
        diagnostic_specification=str(Path(spec_path).resolve()),diagnostic_specification_sha256=sha256(spec_path),
        run_order=list(RUN_IDS),extra_diagnostic_hypotheses=2,extra_diagnostic_runs=3,
        unused_fourth_not_authorized_automatically=True,original_nine_never_replaced=True,
        trace_window='2006-07-20 (16:40,16:50] resolved original physical clock',
        ledger_analysis_parser='pandas format=mixed, preserve nanoseconds',
        diagnostic_counter_contract=iteration_channels())
    reg['integer_counter_metadata_contract']='API list/getAPIData omits Integer; actual handles + live values + own SQL Name/Key/Units validated; None unit empty (DataGlobalConstants unitNames), RDD renders []'
    path=destination/'DIAGNOSTIC_REGISTRATION.json';path.write_text(json.dumps(reg,indent=2)+'\n',encoding='utf8')
    freeze=dict(registration_sha256=sha256(path),registration=str(path),source_files=source_files,
                fixed_tolerance_sha256=reg['fixed_tolerance_sha256'],specification_sha256=SPEC_SHA)
    (destination/'FROZEN_A_DIAGNOSTIC_03.json').write_text(json.dumps(freeze,indent=2)+'\n',encoding='utf8')
    return path


def align_numeric(case,destination,receipt):
    """Qualify a deliberate numerical derivative without claiming baseline equality."""
    expected_sizing=sizing(Path(case['original_api_dir'])/'eplusout.eio')
    actual_sizing=sizing(destination/'eplusout.eio')
    if expected_sizing!=actual_sizing:raise ValueError('Numerical diagnostic changed equipment sizing')
    native=read_native(destination/'eplusout.sql',interval_minutes=10)
    raw=pd.read_csv(destination/'API_DETAIL.csv',parse_dates=['bucket_end','timestamp'])
    details=[];differences=[]
    for zone in observer.ZONES:
        old=pd.read_csv(str(case['expected_native_prefix'])+'_'+zone+'.csv',index_col=0,parse_dates=True)
        if not native[zone].index.equals(old.index):raise ValueError('Original native zone clock changed')
        shared=[f for f in native[zone] if f in old and pd.api.types.is_numeric_dtype(native[zone][f])]
        differences.append(dict(zone=zone,max_abs_difference=float((native[zone][shared]-old[shared]).abs().to_numpy().max())))
        details.append(observer.completed_clock(align_completed_api(raw[raw.zone==zone].reset_index(drop=True),native[zone].index)))
    pd.concat(details,ignore_index=True).to_csv(destination/'API_ALIGNED_DETAIL.csv',index=False)
    port=pd.read_csv(destination/'API_PORT.csv',parse_dates=['bucket_end','timestamp'])
    aligned=align_completed_api(port.assign(temperature_C=0.),native[observer.ZONES[0]].index).drop(columns=['temperature_C'])
    aligned=observer.completed_clock(aligned);aligned.to_csv(destination/'OBSERVED_DETAIL.csv',index=False)
    event_gate=None
    if case['base_case_id']=='all_cold_sources_off':
        marker=aligned['v25_port_availability__schedule_value']<.5
        checks={}
        for c in case['channels'].values():
            if c['variable'] in ['Chiller Evaporator Cooling Rate','Chiller Electricity Rate']:
                label=next(k for k,v in case['channels'].items() if v==c)
                checks[label]=float(aligned.loc[marker,label].abs().max())
        if len(checks)!=6 or not marker.any() or max(checks.values())>1e-6:raise ValueError('Original all-source-off actual intervention failed')
        event_gate=dict(accepted_system_rows=int(marker.sum()),actual_evaporator_and_electric_max_abs_W=checks,
                        actual_duration_h=float(aligned.loc[marker,'dt_h'].sum()))
        if abs(event_gate['actual_duration_h']-6)>1e-10:raise ValueError('Original event duration changed')
    result=dict(case_id=case['case_id'],output_only_qualified=False,numerical_diagnostic_qualified=True,
        original_reference_unchanged_claim=False,identical_sizing_fields=len(actual_sizing),
        original_native_zone_clock_preserved=True,own_exact_api_durations_qualified=True,
        native_differences_from_original=differences,all_requested_api_handles_resolved=receipt['all_handles_found'],
        original_event_actual_intervention=event_gate,interface_qualified=False,
        status='numerical solver derivative; not replacement physical benchmark or certified repair')
    (destination/'OBSERVATION_QUALIFICATION.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    return result


def run(registration,case_id):
    reg=json.loads(Path(registration).read_text(encoding='utf8'))
    for p,h in reg['source_files'].items():
        if sha256(p)!=h:raise ValueError('Frozen diagnostic component changed '+p)
    for key in ['fixed_tolerance','diagnostic_specification','weather']:
        if sha256(reg[key])!=reg[key+'_sha256']:raise ValueError('Registered input changed '+key)
    case=next(c for c in reg['cases'] if c['case_id']==case_id)
    if sha256(case['source_idf'])!=case['source_idf_sha256']:raise ValueError('Read-only IDF changed')
    installation=Path(reg['installation'])
    if sha256(installation/'energyplus.exe')!=reg['executable_sha256'] or sha256(installation/'energyplusapi.dll')!=reg['api_dll_sha256']:raise ValueError('Engine identity changed')
    target=Path(reg['output_root'])/case_id;target.mkdir(parents=True,exist_ok=False)
    temp=Path(reg['output_root'])/'temp';temp.mkdir(exist_ok=True);os.environ.update(TEMP=str(temp),TMP=str(temp))
    idf=target/'observer.idf'
    original=Path(case['source_idf']).read_text(encoding='utf-8-sig')
    idf.write_text(observer.append_observers(derive_numeric_idf(original,case['numerical_solver_diagnostic']),case['channels']),encoding='utf8',newline='\n')
    if sha256(idf)!=case['observer_idf_sha256']:raise ValueError('Derived diagnostic IDF not preregistered')
    receipt=observer.collect_observed(idf,Path(reg['weather']),target,installation,case['channels'],trace=StageTrace(reg['topology']))
    if case['numerical_solver_diagnostic']:result=align_numeric(case,target,receipt)
    else:result=observer.qualify_observation(case,target,receipt)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    a=sub.add_parser('prepare');a.add_argument('--base-registration',type=Path,required=True);a.add_argument('--specification',type=Path,required=True)
    a.add_argument('--tolerance',type=Path,required=True);a.add_argument('--destination',type=Path,required=True)
    b=sub.add_parser('run');b.add_argument('--registration',type=Path,required=True);b.add_argument('--case',choices=RUN_IDS,required=True)
    args=p.parse_args()
    if args.mode=='prepare':print(prepare(args.base_registration,args.specification,args.tolerance,args.destination))
    else:print(json.dumps(run(args.registration,args.case),indent=2))


if __name__=='__main__':main()
