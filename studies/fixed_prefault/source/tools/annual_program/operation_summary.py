"""Unused read-only, selected-field operations projection of a closed journal.

Logging migration: annual_operation_quantities.v1 adds explicitly named native
event/interval quantities; it does not redefine engineering_summary.operations.
No environment, solver, raw input table, producer write or price is involved.
One segment/event is selected at a time. Cumulative token arrays, solver data,
domain catalogues, complete histories and run/spell lists are never captured.

GT hours use the existing engineering 1e-4 kW linear endpoint convention; actual
GT edge events retain their native EPS convention. DG attempted starts are not
state-transition counts. Boiler readiness is electrical/functional, not thermal
standby. Integrated storage receipts crossing an unrecorded reporting boundary
stay unknown monthly; their complete observed-year receipt can remain known.
"""
from __future__ import annotations

from bisect import bisect_right
import gzip
import hashlib
import math
import os
from pathlib import Path
import re

from tools.annual_readiness import journal
from tools.sci_engineering.design import digest
from . import MODEL_ID
from . import engineering_summary as _selector_module
from .engineering_summary import _SelectedJson, _sha, MONTH_BOUNDS

SCHEMA=MODEL_ID+'.operation_summary.v1'
MIGRATION_ID='annual_operation_quantities.v1'
ROOT=Path(__file__).resolve().parents[2]
_OBSERVER_PATHS={'operation_summary.py':Path(__file__).resolve(),
    'engineering_summary.py':Path(_selector_module.__file__).resolve()}
_OBSERVER_LOADED_SHA={name:_sha(path) for name,path in _OBSERVER_PATHS.items()}
GT_POSITIVE_KW=1e-4
NATIVE_EPS=1e-9
SOURCE_SHA256={
 'src/hospital_ies/state_transition.py':'c77ecd4387706ef1468afd6c797308554c25b126cff7bd8481ccb24eb13ca3d6',
 'tools/engineering_model/execution.py':'bb06de1d3b5714e0b407b6ea9138476a1e6cbd21a6e150ea4375997661eae8f9',
 'tools/engineering_model/analysis.py':'9e7fe5f2725a600167d467b8c5e3e069b212c21123a06e6d660b2774672f9b27',
 'tools/model_closure_v22/environment.py':'3b4d6a3cbe59b9bdffb3db039cc12cb8c361f016fecd93c2512199da5fda03ea',
 'tools/model_correctness_v23/environment.py':'6db840b548680f1ba942c23d4ed825d67a52afa7b39bc6ff2894e29af5de0d30',
 'tools/model_correctness_v23/protection.py':'889bced9bcff407e06825836dbb90cd666ef34794b6f8b4b3b092a8e0003a526',
 'tools/sci_engineering/storage.py':'dec80a6e2e668d7d9e6477f39ccbe69baa8b6eeac4c545af5f8a9da646c2d842',
 'tools/sci_engineering/environment.py':'00790240a58195cb56a24c3d495e4799785cf5245eebb79b3b546e0a83bd8a58',
 'tools/annual_readiness/environment.py':'4e4e0d6a52857e976cdf054129215da38379dcfa8f4eb221a4a919f963e95e27',
 'tools/annual_readiness/station.py':'cb75ba9f7f93f3dc6bc6041ddc3d3866701912ca5375ae79c79a3bae2a280d31',
 'tools/execution_recovery/execution.py':'6d1e56d1d46b815fa18b8ba6a3ef23f4726f1c5de6c59ed7185aafd4178f0f7d',
 'tools/annual_readiness/analysis.py':'551ccc8559a26b0af4ee9c3c6cd8db54e927d6552b32ddf4d98d1127014eabca',
 'tools/annual_readiness/journal.py':'4cd6db9234a677c1fc1620ac984adc33bb9fb898d4eb1b7bf318d5ef2a16ddc2'}

def _fields(*names):return {name:True for name in names}

def _observer_sources():
    return {name:dict(path=str(path.resolve()),sha256=_sha(path)) for name,path in _OBSERVER_PATHS.items()}

def _event_state(reader):
    # DG state changes use scalar before/after. Other native diagnostics may
    # use arbitrarily large state objects under those same generic key names.
    if reader.peek() in '{[':reader.value(False);return None
    return reader.value(True)

_EVENT_FIELDS=dict(_fields('event_id','kind','time_minute','device','actual_kW','before_kW','after_kW','before','after','initial_GT_online'),
    before=_event_state,after=_event_state,station=_fields('total_diesel_starts_used'))
_STORAGE_FIELDS=_fields('charged_input_kWh','discharged_kWh','standing_loss_kWh','conversion_loss_kWh')
_SEGMENT_FIELDS=dict(_fields('start_minute','end_minute','source_start_gt_kW','source_end_gt_kW'),
    terminal_action=_fields('gt_electric_kW'),pre_state=_fields('gt_prev','diesel_status'),
    action=_fields('gt_electric_kW','diesel_kW','boiler_steam_lbph','gt_start_electric_kW',
        'boiler_restart_electric_kW','ups_charge_kW','ups_discharge_kW','cold_charge_kW','cold_discharge_kW'),
    m22_station=_fields('boiler_ready_before_actual_interval','boiler_execution_status'),
    m23_restoration=_fields('boiler_ready'),arrived_source_permissions=_fields('boiler_steam_lbph'),
    m21_storage=dict(ups=_STORAGE_FIELDS,cold_store=_STORAGE_FIELDS))

class _Reader(_SelectedJson):
    """Extend the bounded existing selector with streaming array callbacks."""
    def select(self,fields):
        self.space()
        if self.take()!='{':raise ValueError('Expected selected JSON object')
        result={};seen=set();self.space()
        if self.peek()=='}':self.take();return result
        while True:
            key=self.value(True)
            if type(key) is not str or key in seen or len(seen)>=1024:raise ValueError('Invalid/duplicate/excessive JSON keys')
            seen.add(key);self.space()
            if self.take()!=':':raise ValueError('Missing JSON colon')
            self.space();spec=fields.get(key)
            if spec is None:self.value(False)
            elif callable(spec):result[key]=spec(self)
            elif isinstance(spec,dict) and self.peek()=='{':result[key]=self.select(spec)
            else:result[key]=self.value(True)
            self.space();char=self.take()
            if char=='}':return result
            if char!=',':raise ValueError('Missing JSON separator')

def _array(reader,fields,consumer):
    reader.space()
    if reader.take()!='[':raise ValueError('Expected streamed JSON array')
    reader.space()
    if reader.peek()==']':reader.take();return True
    while True:
        consumer(reader.select(fields));reader.space();char=reader.take()
        if char==']':return True
        if char!=',':raise ValueError('Missing array separator')

def _read(path,fields,*,compressed=False):
    opener=gzip.open if compressed else Path.open
    with opener(path,'rt',encoding='utf-8-sig') as stream:
        reader=_Reader(stream);value=reader.select(fields);reader.space()
        if reader.peek():raise ValueError('Trailing selected JSON')
        return value

def _number(value):return type(value) in (int,float) and math.isfinite(value)
def _nonnegative(value):return _number(value) and value>=0.
def _vector(value):return type(value) is list and len(value)==3 and all(_number(v) for v in value)
def _get(value,*keys):
    for key in keys:
        if not isinstance(value,dict):return None
        value=value.get(key)
    return value

class _Scalar:
    def __init__(self):self.total=0.;self.correction=0.;self.missing=0;self.known=0
    def add(self,value):
        if not _number(value):self.missing+=1;return
        # Neumaier accumulation without retaining historical summands.
        total=self.total+value
        self.correction+=(self.total-total)+value if abs(self.total)>=abs(value) else (value-total)+self.total
        self.total=total;self.known+=1
    def value(self,present):return None if not present or self.missing else self.total+self.correction
    def subtotal(self):return self.total+self.correction if self.known else None

_GT_COUNTS=('start_requested_count','start_process_begun_count','start_completed_event_count',
    'start_completed_positive_count','actual_started_event_count','actual_stopped_event_count',
    'zero_to_positive_count','positive_to_zero_count')
_BOILER_FIELDS=('functional_ready_hours','permission_ready_zero_steam_hours','positive_steam_hours','steam_lb',
    'positive_steam_entry_count','station_running_hours','functional_restart_completion_count','restart_electricity_kWh')
_DG_FIELDS=('actual_start_attempt_count','STARTING_entry_count','READY_entry_count','CONNECTED_entry_count',
    'connected_state_hours','positive_settled_output_interval_hours')

class _Period:
    def __init__(self,label,start,end,origin,observed,requested):
        self.label=label;self.start=start;self.end=end
        self.observed=max(0.,min(end,origin+observed)-max(start,origin))
        self.requested=max(0.,min(end,origin+requested)-max(start,origin))
        self.values={};self.first={};self.first_missing=set();self.event_points=0
    def add(self,device,name,value):self.values.setdefault((device,name),_Scalar()).add(value)
    def metric(self,device,name):
        present=self.observed>0. or (name.endswith('_count') and self.event_points>0)
        return self.values.get((device,name),_Scalar()).value(present)
    def export(self,initial_gt,initial_boiler,initial_native_flags):
        answer=dict(label=self.label,observation=dict(observed_duration_minutes=self.observed,
            requested_duration_minutes=self.requested,unanalysed_requested_minutes=self.requested-self.observed,
            has_observed_event_or_state_edge_points=self.event_points>0))
        for i in range(3):
            device=f'GT_{i+1}';answer[device]={name:self.metric(device,name) for name in _GT_COUNTS+('observed_running_hours','starter_electricity_kWh')}
            for field in _GT_COUNTS:
                if answer[device][field] is not None:answer[device][field]=int(answer[device][field])
            answer[device].update(initial_online=initial_gt[i] if initial_gt is not None else None,
                given_initial_online_native_EPS=initial_native_flags[i] if initial_native_flags is not None else None,
                first_positive_settled_output_source_minute=None if device in self.first_missing else self.first.get(device),
                first_output_status=('unobserved_period' if not self.observed else 'unknown_missing_actual_power'
                    if device in self.first_missing else 'observed_positive' if device in self.first else 'no_positive_in_observed_prefix'))
        answer['DG']={name:self.metric('DG',name) for name in _DG_FIELDS}
        for field in _DG_FIELDS:
            if field.endswith('_count') and answer['DG'][field] is not None:answer['DG'][field]=int(answer['DG'][field])
        answer['DG'].update(start_requested_count=None,start_completed_count=None,
            first_positive_settled_output_source_minute=None if 'DG' in self.first_missing else self.first.get('DG'))
        answer['boiler_bank']={name:self.metric('boiler_bank',name) for name in _BOILER_FIELDS}
        for field in _BOILER_FIELDS:
            if field.endswith('_count') and answer['boiler_bank'][field] is not None:answer['boiler_bank'][field]=int(answer['boiler_bank'][field])
        answer['boiler_bank']['initial_producing_left_censored']=initial_boiler
        answer['shared_auxiliaries']=dict(carrier='electric',GT_starter_electricity_kWh=self.metric('shared','GT_starter_electricity_kWh'))
        answer['storage']={}
        for name,carrier in (('ups','electric'),('cold_store','cold')):
            fields=tuple(_STORAGE_FIELDS)+('charge_hours','discharge_hours')
            row={field:self.metric(name,field) for field in fields}
            row.update(carrier=carrier,unit='kWh_'+carrier,turnover_cycle_count=None,
                known_subtotals={field:self.values.get((name,field),_Scalar()).subtotal() for field in _STORAGE_FIELDS})
            answer['storage'][name]=row
        return answer

class _Operations:
    def __init__(self,origin,end,requested):
        self.origin=origin;self.end=end
        self.year=_Period('2001',0.,float(MONTH_BOUNDS[-1]),origin,end,requested)
        self.months=[_Period(f'2001-{i+1:02d}',a,b,origin,end,requested) for i,(a,b) in enumerate(zip(MONTH_BOUNDS,MONTH_BOUNDS[1:]))]
        self.previous_gt=None;self.initial_gt=None;self.initial_native_flags=None;self.previous_steam=None;self.initial_boiler=None
        self.initial_gt_before_event={}
        self.first_segment=True;self.last_event_id=0;self.last_event_time=0.;self.dg_attempt_total=0
        self.limitations=set();self.segment_count=0;self.event_count=0
    def targets(self,a,b):
        a+=self.origin;b+=self.origin
        return [(p,max(a,p.start),min(b,p.end)) for p in [self.year]+self.months if min(b,p.end)>max(a,p.start)]
    def point(self,time):
        absolute=self.origin+time
        if not 0.<=absolute<=MONTH_BOUNDS[-1]:raise ValueError('Event outside source year')
        index=min(11,bisect_right(MONTH_BOUNDS,absolute)-1)
        return [self.year,self.months[index]]
    def count(self,device,name,time,value=1):
        if time>self.end:return
        for period in self.point(time):period.add(device,name,value);period.event_points+=1
    def event(self,value,start,end):
        number=value.get('event_id');time=value.get('time_minute');kind=value.get('kind')
        if (type(number) is not int or number<=self.last_event_id or not _number(time)
                or not start<=time<=end or time<self.last_event_time or type(kind) is not str):raise ValueError('Invalid/nonmonotonic/duplicated event identity or clock')
        self.last_event_id=number;self.last_event_time=time
        if time>self.end:return
        self.event_count+=1
        if kind=='m22_station_initial_state':
            flags=value.get('initial_GT_online')
            if type(flags) is list and len(flags)==3 and all(type(v) is bool for v in flags):self.initial_native_flags=flags
        gt_events={'gt_start_requested':'start_requested_count','gt_start_process_begun':'start_process_begun_count',
            'gt_start_completed':'start_completed_event_count','gt_actual_started':'actual_started_event_count','gt_actual_stopped':'actual_stopped_event_count'}
        if kind in gt_events:
            device=value.get('device')
            if device not in ('GT_1','GT_2','GT_3'):raise ValueError('Native GT event has invalid device')
            self.count(device,gt_events[kind],time)
            if kind in ('gt_actual_started','gt_actual_stopped') and time==0. and _number(value.get('before_kW')):
                self.initial_gt_before_event.setdefault(int(device[-1])-1,value['before_kW'])
            if kind=='gt_start_completed':
                power=value.get('actual_kW')
                self.count(device,'start_completed_positive_count',time,int(power>GT_POSITIVE_KW) if _number(power) else None)
        elif kind=='diesel_state_changed':
            after=value.get('after')
            if after in ('STARTING','READY','CONNECTED'):self.count('DG',after+'_entry_count',time)
        elif kind=='m22_DG_dedicated_DC_start':
            total=_get(value,'station','total_diesel_starts_used')
            if type(total) is not int or total<0:self.count('DG','actual_start_attempt_count',time,None)
            elif total!=self.dg_attempt_total+1:raise ValueError('DG actual attempt cumulative counter is duplicate/discontinuous')
            else:self.count('DG','actual_start_attempt_count',time);self.dg_attempt_total=total
    def edge(self,i,time,before,after):
        if before is None:return
        if (before>GT_POSITIVE_KW)!=(after>GT_POSITIVE_KW):
            self.count(f'GT_{i+1}','zero_to_positive_count' if after>GT_POSITIVE_KW else 'positive_to_zero_count',time)
    def segment(self,row):
        a,b=row['start_minute'],row['end_minute'];finish=min(b,self.end)
        if a>=finish:return
        self.segment_count+=1;parts=self.targets(a,finish)
        start,end=row.get('source_start_gt_kW'),row.get('source_end_gt_kW')
        action=row.get('action') or {}
        if self.first_segment:
            initial=_get(row,'pre_state','gt_prev')
            if _vector(initial):
                initial=list(initial)
                for i,value in self.initial_gt_before_event.items():initial[i]=value
            if _vector(initial):self.initial_gt=[v>GT_POSITIVE_KW for v in initial]
            self.previous_gt=initial if _vector(initial) else None
        for i in range(3):
            device=f'GT_{i+1}'
            if _vector(start) and _vector(end):
                if not self.first_segment and self.previous_gt is None:
                    self.count(device,'zero_to_positive_count',a,None)
                    self.count(device,'positive_to_zero_count',a,None)
                self.edge(i,a,self.previous_gt[i] if self.previous_gt is not None else None,start[i])
                on_a,on_b=start[i]>GT_POSITIVE_KW,end[i]>GT_POSITIVE_KW
                crossing=None
                if on_a!=on_b:
                    crossing=a+(GT_POSITIVE_KW-start[i])/(end[i]-start[i])*(b-a)
                    self.edge(i,crossing,start[i],end[i])
                positive_a=a if on_a else crossing if on_b else b
                positive_b=b if on_b else crossing if on_a else a
                for period,x,y in parts:
                    minutes=max(0.,min(y,self.origin+positive_b)-max(x,self.origin+positive_a))
                    period.add(device,'observed_running_hours',minutes/60.)
            else:
                for period,_,_ in parts:
                    for field in ('observed_running_hours','zero_to_positive_count','positive_to_zero_count'):period.add(device,field,None)
            power=_get(action,'gt_electric_kW')
            for period,x,_ in parts:
                if not _vector(power):
                    if device not in period.first:period.first_missing.add(device)
                elif power[i]>0.:period.first.setdefault(device,x)
        self.previous_gt=end if _vector(end) else None
        terminal=_get(row,'terminal_action','gt_electric_kW')
        if _vector(terminal) and _vector(end) and b<=self.end:
            for i in range(3):self.edge(i,b,end[i],terminal[i])
            self.previous_gt=terminal
        steam=action.get('boiler_steam_lbph');steam_valid=_nonnegative(steam)
        if self.first_segment:self.initial_boiler=steam>NATIVE_EPS if steam_valid else None
        if steam_valid and self.previous_steam is not None and self.previous_steam<=NATIVE_EPS<steam:
            self.count('boiler_bank','positive_steam_entry_count',a)
        if steam_valid and not self.first_segment and self.previous_steam is None:
            self.count('boiler_bank','positive_steam_entry_count',a,None)
        self.previous_steam=steam if steam_valid else None
        ready=_get(row,'m22_station','boiler_ready_before_actual_interval')
        after=_get(row,'m23_restoration','boiler_ready')
        permission=_get(row,'arrived_source_permissions','boiler_steam_lbph')
        if type(ready) is bool and type(after) is bool and not ready and after and b<=self.end:
            self.count('boiler_bank','functional_restart_completion_count',b)
        for period,x,y in parts:
            hours=(y-x)/60.;bank='boiler_bank'
            period.add(bank,'functional_ready_hours',hours*ready if type(ready) is bool else None)
            period.add(bank,'permission_ready_zero_steam_hours',hours*int(ready and permission>NATIVE_EPS and steam<=NATIVE_EPS)
                if type(ready) is bool and _nonnegative(permission) and steam_valid else None)
            period.add(bank,'positive_steam_hours',hours*int(steam>NATIVE_EPS) if steam_valid else None)
            period.add(bank,'steam_lb',hours*steam if steam_valid else None)
            status=_get(row,'m22_station','boiler_execution_status')
            period.add(bank,'station_running_hours',hours*int(status=='running') if type(status) is str else None)
            restart=action.get('boiler_restart_electric_kW')
            period.add(bank,'restart_electricity_kWh',hours*restart if _nonnegative(restart) else None)
            if type(ready) is not bool or type(after) is not bool:period.add(bank,'functional_restart_completion_count',None)
            if not steam_valid:period.add(bank,'positive_steam_entry_count',None)
            dg=action.get('diesel_kW');state=_get(row,'pre_state','diesel_status')
            period.add('DG','connected_state_hours',hours*int(state=='CONNECTED') if type(state) is str else None)
            period.add('DG','positive_settled_output_interval_hours',hours*int(dg>NATIVE_EPS) if _nonnegative(dg) else None)
            if not _number(dg):
                if 'DG' not in period.first:period.first_missing.add('DG')
            elif dg>0.:period.first.setdefault('DG',x)
            starter=action.get('gt_start_electric_kW')
            period.add('shared','GT_starter_electricity_kWh',hours*starter if _nonnegative(starter) else None)
            # Starter is a shared port, not safely allocatable to individual GTs.
            for i in range(3):period.add(f'GT_{i+1}','starter_electricity_kWh',None)
            for name,prefix in (('ups','ups'),('cold_store','cold')):
                receipt=_get(row,'m21_storage',name) or {}
                whole=x==self.origin+a and y==self.origin+b
                for field in _STORAGE_FIELDS:
                    value=receipt.get(field)
                    period.add(name,field,value if whole and _nonnegative(value) else None)
                    if not whole:self.limitations.add('integrated_storage_receipt_crosses_period_boundary')
                for port in ('charge','discharge'):
                    power=action.get(prefix+'_'+port+'_kW')
                    period.add(name,port+'_hours',hours*int(power>NATIVE_EPS) if _nonnegative(power) else None)
        self.first_segment=False

def summarize_run(output,*,end_minute=None):
    """Project a closed actual run, optionally only its [0,end_minute] prefix.

    Full index/summary manifest correspondence is streamed. Only selected raw
    prefix blocks are hashed/parsed; the returned authentication scope states
    this explicitly. Unselected physical content is not claimed as verified.
    Unsupported quantities remain None. Existing producer files are never opened
    for writing and their selected metadata hashes must remain unchanged.
    """
    observer_before=_observer_sources()
    if {name:value['sha256'] for name,value in observer_before.items()}!=_OBSERVER_LOADED_SHA:
        raise ValueError('Observer implementation source differs from loaded module/selector')
    output=Path(output).resolve();evidence=output/'evidence'
    names=('CASE.json','BINDINGS.json','RESULT.json','evidence/IDENTITY.json','evidence/SUMMARY.json','evidence/blocks.jsonl')
    if not all((output/name).is_file() and not (output/name).is_symlink() for name in names):
        raise ValueError('Closed actual CASE/BINDINGS/RESULT and journal SUMMARY required')
    before={name:_sha(output/name) for name in names}
    case=_read(output/'CASE.json',dict(case=True)).get('case')
    binding=_read(output/'BINDINGS.json',_fields('source','inputs','git_commit'))
    identity=_read(evidence/'IDENTITY.json',_fields('case_id','case_sha256','source_sha256','input_sha256','git_commit','model_id'))
    result=_read(output/'RESULT.json',dict(_fields('schema','status','case_id','case_sha256','source_sha256','input_sha256',
        'git_commit','model_id','observed_until_minute','requested_end_minute','unknown_tail_minutes','source_and_inputs_unchanged'),
        error=_fields('type','message','category','underlying_category')))
    if (not isinstance(case,dict) or _get(case,'annual_program','model_id')!=MODEL_ID
            or case.get('id')!=identity.get('case_id') or digest(case)!=identity.get('case_sha256')
            or binding.get('git_commit')!=identity.get('git_commit')
            or digest(binding.get('source'))!=identity.get('source_sha256')
            or digest(binding.get('inputs'))!=identity.get('input_sha256')):raise ValueError('Source/input/case identity binding mismatch')
    for path,sha in SOURCE_SHA256.items():
        if binding['source'].get(path)!=sha or _sha(ROOT/path)!=sha:raise ValueError('Native operation definition source binding changed: '+path)
    if (any(result.get(key)!=identity.get(key) for key in ('case_id','case_sha256','source_sha256','git_commit','model_id'))
            or (digest(result.get('input_sha256')) if isinstance(result.get('input_sha256'),dict) else result.get('input_sha256'))!=identity['input_sha256']
            or result.get('schema')!=MODEL_ID+'.runner.v1' or result.get('source_and_inputs_unchanged') is not True):
        raise ValueError('RESULT producer identity/schema/source mismatch')
    manifest=hashlib.sha256();manifest_count=0
    def summary_row(row):
        nonlocal manifest_count
        manifest.update(journal._json(row));manifest_count+=1
    summary=_read(evidence/'SUMMARY.json',dict(_fields('schema','status','identity','identity_sha256','block_count','last_digest',
        'integrity_verified','journal_write_error','uncommitted_files','uncommitted_index_record_count','observation'),
        raw_files_sha256=lambda reader:_array(reader,_fields('file','sha256'),summary_row)))
    observed=result.get('observed_until_minute');requested=result.get('requested_end_minute')
    observation=summary.get('observation') or {}
    if (not _nonnegative(observed) or not _nonnegative(requested) or observed>requested
            or result.get('status') not in ('completed','failed') or summary.get('status')!=result['status']
            or summary.get('schema')!=journal.SCHEMA or summary.get('identity')!=identity
            or summary.get('identity_sha256')!=before['evidence/IDENTITY.json'] or summary.get('integrity_verified') is not True
            or type(summary.get('block_count')) is not int or summary['block_count']<0
            or summary.get('journal_write_error') or summary.get('uncommitted_files') or summary.get('uncommitted_index_record_count')
            or observation.get('observed_until_minute')!=observed or observation.get('requested_end_minute')!=requested
            or observation.get('observed_from_minute')!=0. or observation.get('observed_duration_minutes')!=observed
            or observation.get('unknown_tail_minutes')!=requested-observed or observation.get('contiguous') is not True
            or observation.get('complete') is not bool(result.get('status')=='completed' and observed==requested and summary.get('block_count'))
            or result.get('unknown_tail_minutes')!=requested-observed
            or (result.get('status')=='completed' and observed!=requested)
            or requested!=_get(case,'scenario','duration_steps')*_get(case,'scenario','step_minutes')):
        raise ValueError('Closed journal observed/requested/status/integrity mismatch')
    end=observed if end_minute is None else end_minute
    if not _nonnegative(end) or end>observed:raise ValueError('Selected analysis prefix must lie in the observed actual prefix')
    origin=(_get(case,'scenario','start_hour')-1)*60.
    if origin<0. or origin+requested>MONTH_BOUNDS[-1]:raise ValueError('Case source support outside nonleap2001')
    operations=_Operations(origin,end,requested)
    count=0;last=None;cursor=0.;selected_blocks=0;index_manifest=hashlib.sha256()
    with (evidence/'blocks.jsonl').open('r',encoding='utf8') as stream:
        for line in stream:
            if not line.endswith('\n') or len(line)>65536:raise ValueError('Partial/excessive journal index record')
            import json
            record=json.loads(line)
            filename=f'blocks/block_{count:06d}.json.gz'
            if (record.get('schema')!=journal.SCHEMA or type(record.get('index')) is not int or record['index']!=count
                    or record.get('file')!=filename or record.get('previous_digest')!=last
                    or record.get('digest')!=journal._record_digest(record) or record.get('identity_sha256')!=summary['identity_sha256']
                    or record.get('start_minute')!=cursor or not _number(record.get('end_minute')) or record['end_minute']<=cursor):
                raise ValueError('Journal index identity/chain/order/clock mismatch')
            index_manifest.update(journal._json(dict(file=filename,sha256=record['sha256'])))
            if cursor<end:
                path=evidence/filename
                if path.is_symlink() or not path.is_file() or _sha(path)!=record['sha256']:raise ValueError('Changed/missing selected raw block')
                segment_cursor=cursor;segments=0
                def consume_segment(row):
                    nonlocal segment_cursor,segments
                    a,b=row.get('start_minute'),row.get('end_minute')
                    if not _number(a) or not _number(b) or a!=segment_cursor or b<=a or b>record['end_minute']:
                        raise ValueError('Raw actual segment clocks are nonconsecutive')
                    operations.segment(row);segment_cursor=b;segments+=1
                block=_read(path,dict(_fields('start_minute','end_minute'),
                    events=lambda reader:_array(reader,_EVENT_FIELDS,lambda row:operations.event(row,cursor,record['end_minute'])),
                    segments=lambda reader:_array(reader,_SEGMENT_FIELDS,consume_segment)),compressed=True)
                if (block.get('start_minute')!=cursor or block.get('end_minute')!=record['end_minute'] or block.get('events') is not True
                        or block.get('segments') is not True or segment_cursor!=record['end_minute'] or segments!=record.get('segment_count')):
                    raise ValueError('Selected raw block differs from acknowledged index')
                if _sha(path)!=record['sha256']:raise ValueError('Selected raw block changed while streaming')
                selected_blocks+=1
            cursor=record['end_minute'];last=record['digest'];count+=1
    if (count!=summary.get('block_count') or cursor!=observed or last!=summary.get('last_digest')
            or manifest_count!=count or manifest.digest()!=index_manifest.digest()):raise ValueError('Journal summary manifest/count/endpoint differs from streamed index')
    files=0
    with os.scandir(evidence/'blocks') as entries:
        for entry in entries:
            match=re.fullmatch(r'block_(\d{6})\.json\.gz',entry.name)
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False) or match is None or int(match[1])>=count:
                raise ValueError('Orphan/symlinked raw journal block')
            files+=1
    if files!=count:raise ValueError('Missing raw journal block')
    after={name:_sha(output/name) for name in names}
    if after!=before:raise ValueError('Producer metadata changed during read-only operation analysis')
    observer_after=_observer_sources()
    if observer_after!=observer_before:raise ValueError('Observer implementation source changed during read-only operation analysis')
    return dict(schema=SCHEMA,log_migration_id=MIGRATION_ID,identity=identity,
        definitions=dict(GT_operating_positive_kW=GT_POSITIVE_KW,native_event_EPS_kW=NATIVE_EPS,
            initial_flags_scope='given_run_initial_state_repeated_for_context; not_month_start_state',
            event_count_interval='[0, analysed_end_minute] inclusive',interval_quantities_interval='[start, end)',
            calendar_event_assignment='absolute source minute; endpoint point may have zero-duration interval coverage',
            first_positive_settled_output_kW=0.,GT_hours='native engineering _gt_operation linear endpoint convention',
            DG_hours='positive settled interval output; not instantaneous rotor trajectory',
            boiler_readiness='interval-start electrical/functional qualification; not thermal standby',source_sha256=dict(SOURCE_SHA256)),
        execution=dict(status=result['status'],error=result.get('error'),producer_observed_until_minute=observed,
            analysed_until_minute=end,requested_end_minute=requested,unknown_tail_minutes=requested-observed),
        year=operations.year.export(operations.initial_gt,operations.initial_boiler,operations.initial_native_flags),
        months=[period.export(operations.initial_gt,operations.initial_boiler,operations.initial_native_flags) for period in operations.months],
        unsupported=dict(OEM_startup_gas_MMBtu=None,hot_standby_hours=None,standby_fee_USD2001=None,
            DG_start_requested_count=None,DG_start_completed_count=None,per_boiler_unit_start_counts=None,
            DC_charge_energy_kWh=None,new_prices=None),unobserved_tail_operations=None,
        limitations=sorted(operations.limitations|{'No OEM thermal startup/standby inference','No additional fee charged for already-accounted electrical ports',
            'Per-GT starter electrical energy unavailable: native port is shared','Producer source/input bytes not fully rehashed or data tables parsed'}),
        provenance=dict(metadata_sha256_before=before,metadata_sha256_after=after,producer_metadata_unchanged=True,
            observer_source_binding_before=observer_before,observer_source_binding_after=observer_after,
            observer_source_binding_is_producer_identity=False,
            selected_raw_blocks=selected_blocks,full_index_records=count,selected_segments=operations.segment_count,
            raw_content_authentication_scope='complete_observed_prefix' if end==observed else 'selected_prefix_blocks_only',
            maximum_selected_segments_in_memory=1,maximum_selected_events_in_memory=1,
            full_history_materialized=False,cumulative_token_arrays_captured=False,
            canonical_registration_recomputed=False,
            native_aggregate_service_metrics_recomputed=False,physical_or_clinical_validation_performed=False,
            annual_resource_admission_qualified=False))
