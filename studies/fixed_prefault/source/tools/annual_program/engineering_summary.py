"""Read-only month/year summaries of a closed annual-program actual journal.

Service integrals reuse the journal definitions. Material interruption unions
use the sealed native block intervals and the native joining precision, without
a new materiality threshold or a cross-block recovery/clinical claim. Months use
the true nonleap 2001 source calendar, never a reset of the physical episode.

Whole-block native fees are assigned exactly when the block lies in one month.
Cross-month fees use the native raw formula only for safely divisible receipts;
nonzero integrated DG work is never prorated. Inventories are recorded boundary
observations: no interpolation, replenishment timing, starts or standby inference.
Only one raw block is decoded at a time; compact output/receipt metadata can grow.
"""
from __future__ import annotations

from bisect import bisect_right
import calendar
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import re

from tools.annual_readiness import journal
from tools.model_correctness_v23 import metrics
from tools.model_review_v30.evaluation import independent_energy_audit
from tools.operating_rules_study.ledger import FEE_TOL
from tools.model_review_v30 import evaluation as native_fee_evaluation
from tools.operating_rules_study import ledger as native_fee_ledger
from tools.sci_engineering.design import digest
from . import MODEL_ID
from .registration import validate_case


SCHEMA = MODEL_ID + '.engineering_summary.v1'
PRIORITIES, CARRIERS = journal.PRIORITIES, journal.CARRIERS
SERVICE_FIELDS = journal._SERVICE_FIELDS
YEAR_MINUTES = 365 * 24 * 60
MONTH_BOUNDS = [0]
for _month in range(1,13):
    MONTH_BOUNDS.append(MONTH_BOUNDS[-1] + calendar.monthrange(2001,_month)[1]*1440)

_SKIP_OUTSIDE=re.compile(r'["{}\[\]]')
_SKIP_QUOTED=re.compile(r'["\\]')
_SKIP_PRIMITIVE_END=re.compile(r'[ \t\r\n,}\]]')


def _read(path):
    with Path(path).open('r',encoding='utf-8-sig') as stream:
        return json.load(stream)


class _SelectedJson:
    """Select small JSON metadata without decoding skipped diagnostic/catalogues.

    Skipped values receive structural string/bracket scanning, not a full JSON
    semantic audit. Required selected values are decoded strictly by json.loads.
    The closed producer and its complete file SHA remain the evidence binding.
    """
    def __init__(self,stream):
        self.stream=stream
        self.buffer=''
        self.index=0

    def peek(self):
        if self.index==len(self.buffer):
            self.buffer=self.stream.read(65536)
            self.index=0
        return self.buffer[self.index] if self.buffer else ''

    def take(self):
        value=self.peek()
        if not value:raise ValueError('Incomplete selected producer JSON')
        self.index+=1
        return value

    def space(self):
        while self.peek() and self.peek() in ' \t\r\n':self.take()

    def value(self,capture):
        if not capture:return self._discard_value()
        self.space()
        first=self.take()
        pieces=[first] if capture else None
        stack=[{'{':'}','[':']'}[first]] if first in '{[' else []
        quoted=first=='"'
        string_only=quoted
        escaped=False
        if stack or quoted:
            while stack or quoted:
                char=self.take()
                if capture:
                    pieces.append(char)
                    if len(pieces)>1_000_000:raise ValueError('Selected producer metadata value exceeds bounded reader limit')
                if quoted:
                    if escaped:escaped=False
                    elif char=='\\':escaped=True
                    elif char=='"':
                        quoted=False
                        if string_only:break
                elif char=='"':quoted=True
                elif char in '{[':stack.append({'{':'}','[':']'}[char])
                elif char in '}]':
                    if not stack or char!=stack.pop():raise ValueError('Unbalanced producer JSON value')
        else:
            while self.peek() and self.peek() not in ' \t\r\n,}]':
                char=self.take()
                if capture:
                    pieces.append(char)
                    if len(pieces)>1_000_000:raise ValueError('Selected producer metadata value exceeds bounded reader limit')
        return json.loads(''.join(pieces)) if capture else None

    def _discard_value(self):
        """Same structural skip, using C searches within the bounded buffer.

        Strings/escapes and bracket nesting retain the existing semantics;
        ignored diagnostics still receive no JSON semantic qualification.
        Captured values continue through the unchanged bounded decoder.
        """
        self.space();first=self.take()
        stack=[{'{':'}','[':']'}[first]] if first in '{[' else []
        quoted=first=='"';string_only=quoted
        if not stack and not quoted:
            while self.peek():
                match=_SKIP_PRIMITIVE_END.search(self.buffer,self.index)
                if match is not None:
                    self.index=match.start();break
                self.index=len(self.buffer)
            return None
        while stack or quoted:
            if not self.peek():raise ValueError('Incomplete selected producer JSON')
            match=(_SKIP_QUOTED if quoted else _SKIP_OUTSIDE).search(self.buffer,self.index)
            if match is None:
                self.index=len(self.buffer);continue
            char=match.group();self.index=match.end()
            if quoted:
                if char=='\\':self.take()  # Exactly one escaped character, even across buffers.
                else:
                    quoted=False
                    if string_only:break
            elif char=='"':quoted=True
            elif char in '{[':stack.append({'{':'}','[':']'}[char])
            elif not stack or char!=stack.pop():raise ValueError('Unbalanced producer JSON value')
        return None

    def select(self,fields):
        self.space()
        if self.take()!='{':raise ValueError('Selected producer metadata must be an object')
        result={}
        self.space()
        if self.peek()=='}':self.take();return result
        seen=set()
        while True:
            key=self.value(True)
            if not isinstance(key,str) or key in seen:raise ValueError('Invalid or duplicate producer JSON key')
            seen.add(key)
            self.space()
            if self.take()!=':':raise ValueError('Missing producer JSON colon')
            self.space()
            if key not in fields:self.value(False)
            elif isinstance(fields[key],dict) and self.peek()=='{':result[key]=self.select(fields[key])
            else:result[key]=self.value(True)
            self.space()
            char=self.take()
            if char=='}':break
            if char!=',':raise ValueError('Missing producer JSON field separator')
        return result


_ERROR_FIELDS={key:True for key in ('type','message','underlying_category','category')}


def _selected(path,fields):
    with Path(path).open('r',encoding='utf-8-sig') as stream:
        reader=_SelectedJson(stream)
        result=reader.select(fields)
        reader.space()
        if reader.peek():raise ValueError('Trailing producer JSON content')
    return result


def _sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            result.update(block)
    return result.hexdigest()


def _overlap(a,b,c,d):
    return max(0.,min(b,d)-max(a,c))


def _known(total):
    return total.known_total if total.known_count else None


def _merged_append(target, interval):
    if target:
        tail=metrics._merge([target[-1],interval])
        target[-1:]=tail
    else:
        target.append(list(interval))


def _interval_report(intervals,complete):
    merged=metrics._merge(intervals)
    return dict(intervals_source_minutes=merged if complete else None,
        known_intervals_source_minutes=merged,
        material_minutes=math.fsum(b-a for a,b in merged) if complete else None,
        longest_material_minutes=max((b-a for a,b in merged),default=0.) if complete else None,
        interruption_count=len(merged) if complete else None,
        complete_observed_support=complete)


class _Stock:
    def __init__(self):
        self.count=0
        self.initial=self.final=self.minimum=None
        self.boundaries_complete=True
        self.chain_complete=True
        self.max_gap=0.
        self.gaps=[]
        self.unrecorded=0

    def add(self,initial,final,unrecorded):
        if not self.count:self.initial=initial if journal._number(initial) else None
        for value in (initial,final):
            if journal._number(value):
                self.minimum=value if self.minimum is None else min(self.minimum,value)
            else:self.boundaries_complete=False
        self.final=final if journal._number(final) else None
        self.count+=1
        self.unrecorded+=unrecorded

    def gap(self,source_minute,previous,initial):
        if all(journal._number(v) for v in (previous,initial)):
            value=abs(previous-initial)
            self.max_gap=max(self.max_gap,value)
            if value==0.:return
        else:
            value=None
            self.chain_complete=False
        self.gaps.append(dict(source_minute=source_minute,previous_final_kWh=previous,
            next_initial_kWh=initial,gap_kWh=value))

    def export(self):
        return dict(initial_stock_kWh=self.initial,final_stock_kWh=self.final,
            minimum_stock_kWh=self.minimum if self.count and self.boundaries_complete else None,
            known_recorded_boundary_minimum_kWh=self.minimum,
            minimum_scope='recorded actual segment boundaries only; no intrasegment interpolation',
            intrasegment_minimum_stock_kWh=None,
            receipt_chain_max_gap_kWh=self.max_gap if self.count and self.boundaries_complete and self.chain_complete else None,
            continuity_gaps=self.gaps,unrecorded_period_boundary_count=self.unrecorded)


class _Period:
    def __init__(self,label,start,end,origin,requested,observed):
        self.label,self.start,self.end=label,start,end
        self.origin=origin
        self.requested=_overlap(start,end,origin,origin+requested)
        self.observed=_overlap(start,end,origin,origin+observed)
        self.services={p:{c:{field:journal._Total() for field in SERVICE_FIELDS}
            for c in CARRIERS} for p in PRIORITIES}
        self.inventory={name:_Stock() for name in ('ups','cold_store','diesel_shaft')}
        self.intervals={p:{c:[] for c in CARRIERS} for p in PRIORITIES}
        self.material_complete=True
        self.operating=journal._Total()
        self.preparation=None
        self.preparation_assigned=False
        self.preparation_count=None

    def add_service(self,segment,a,b):
        dt=(b-a)/60.
        ledger=segment.get('service_request_ledger') or {}
        if not isinstance(ledger,dict):ledger={}
        for ip,p in enumerate(PRIORITIES):
            for ic,c in enumerate(CARRIERS):
                values=[journal._cell(ledger.get(key),ip,ic) for key in
                    ('original_demand_kW','delivered_kW','active_cut_kW','connected_unserved_kW')]
                request,delivered,_,_=values
                ens=max(request-delivered,0.) if all(journal._number(v) for v in (request,delivered)) else None
                for field,value in zip(SERVICE_FIELDS,values+[ens]):
                    self.services[p][c][field].add(value*dt if journal._number(value) else None)

    def material(self,intervals,complete):
        self.material_complete &= complete
        if intervals is None:return
        for p in PRIORITIES:
            for c in CARRIERS:
                for a,b in intervals[p][c]:
                    a,b=max(self.start,a),min(self.end,b)
                    if b>a:_merged_append(self.intervals[p][c],[a,b])

    def export(self):
        services={p:{c:dict(**{field:total.value() for field,total in totals.items()},
            known_subtotals={field:_known(total) for field,total in totals.items()},
            missing_numeric_inputs={field:total.count-total.known_count for field,total in totals.items()},
            unit='kWh_'+c,source='native journal service_request_ledger power integral')
            for c,totals in carriers.items()} for p,carriers in self.services.items()}
        complete=bool(self.observed and self.material_complete)
        cell={p:{c:_interval_report(self.intervals[p][c],complete) for c in CARRIERS} for p in PRIORITIES}
        unions={p:_interval_report([v for c in CARRIERS for v in self.intervals[p][c]],complete) for p in PRIORITIES}
        critical=_interval_report([v for p in PRIORITIES[:2] for c in CARRIERS for v in self.intervals[p][c]],complete)
        preparation=(self.preparation if self.preparation_assigned else
                     0. if self.observed and not self.start<=self.origin<self.end else None)
        count=(self.preparation_count if self.preparation_assigned else
               0 if self.observed and not self.start<=self.origin<self.end else None)
        operating=self.operating.value()
        total=math.fsum((operating,preparation)) if all(journal._number(v) for v in (operating,preparation)) else None
        return dict(label=self.label,source_period_start_minute=self.start,source_period_end_minute=self.end,
            observation=dict(requested_duration_minutes=self.requested,observed_duration_minutes=self.observed,
                unknown_tail_minutes=self.requested-self.observed,complete=bool(self.requested and self.requested==self.observed),
                status='not_requested' if not self.requested else 'fully_observed' if self.requested==self.observed else 'unknown_requested_tail'),
            carrier_service=services,material_interruption=dict(by_cell=cell,by_priority=unions,
                critical_union=critical,scope='native material interval union clipped to period; no recovery inference'),
            inventory={name:value.export() for name,value in self.inventory.items()},
            economics=dict(observed_operating_fee_USD2001=operating,
                known_observed_operating_fee_USD2001=_known(self.operating),
                missing_operating_fee_contributions=self.operating.count-self.operating.known_count,
                initial_preparation_fee_USD2001=preparation,initial_preparation_occurrence_count=count,
                actual_fee_USD2001=total,unobserved_tail_fee_USD2001=None,currency='USD',price_year=2001),
            operations=dict(starts=None,standby_minutes=None),unobserved_tail_service=None)


def _native_intervals(block,offset):
    raw=block.get('native_material_intervals')
    if (block.get('block_native_support_complete') is not True or not isinstance(raw,dict)
            or not isinstance(block.get('native_material_definition'),dict)):
        return None,False
    value={p:{} for p in PRIORITIES}
    for p in PRIORITIES:
        for c in CARRIERS:
            intervals=raw.get(p,{}).get(c) if isinstance(raw.get(p),dict) else None
            if not isinstance(intervals,list):raise ValueError('Invalid native material interval cell')
            for pair in intervals:
                if (not isinstance(pair,list) or len(pair)!=2 or not all(journal._number(v) for v in pair)
                        or not block['start_minute']<=pair[0]<pair[1]<=block['end_minute']):
                    raise ValueError('Native material interval exceeds its actual block')
            if metrics._merge(intervals)!=intervals:raise ValueError('Native material intervals are not canonical unions')
            value[p][c]=[[a+offset,b+offset] for a,b in intervals]
    return value,True


def _validate_native_service(block,material_complete):
    """Use the unchanged native numeric/closure checks where raw support exists."""
    fields=('original_demand_kW','active_cut_kW','connected_unserved_kW','delivered_kW')
    supported=True
    for segment in block['segments']:
        ledger=segment.get('service_request_ledger')
        if not isinstance(ledger,dict):supported=False;break
        for name in fields:
            matrix=ledger.get(name)
            if (not isinstance(matrix,list) or len(matrix)!=4 or any(not isinstance(row,list)
                    or len(row)!=3 or not all(journal._number(v) for v in row) for row in matrix)):
                supported=False;break
        if not supported:break
    if not supported:
        if material_complete:raise ValueError('Native material claims complete support without an actual service ledger')
        return
    native=metrics.build_reliability_metrics(dict(segments=block['segments'],
        continuation_start_minute=block['start_minute'],summary={'requested_end_minute':block['end_minute']}))
    if material_complete:
        expected={p:{c:native['carrier_service'][p][c]['intervals'] for c in CARRIERS} for p in PRIORITIES}
        if expected!=block['native_material_intervals'] or native['definition']!=block['native_material_definition']:
            raise ValueError('Sealed native material intervals/definition contradict actual service ledger')


def _economics(block):
    value=block.get('economics')
    if (not isinstance(value,dict) or value.get('schema')!='hospital_ies.v30.independent_raw_energy_audit.v1'
            or value.get('accounting_objective_cost_used') is not False
            or value.get('producer_economic_aggregation_used') is not False):
        return {}
    return value


def _fragment_fee(segment,a,b,economics,limitations):
    # Constant raw action rates are divisible. The integrated mechanical receipt
    # is not a constant rate; only an explicitly zero receipt is safely divisible.
    receipt=segment.get('diesel_local_response') or {}
    receipt=receipt.get('energy_receipt',receipt) if isinstance(receipt,dict) else {}
    shaft=receipt.get('mechanical_work_kWh')
    electrical=receipt.get('electrical_work_kWh')
    split=a!=segment['start_minute'] or b!=segment['end_minute']
    if split and shaft!=0.:
        shaft=None
        limitations.add('integrated_DG_receipt_crosses_unrecorded_month_boundary')
    if split and electrical!=0.:electrical=None
    minimal=dict(start_minute=a,end_minute=b,action=segment.get('action',{}),
        diesel_local_response=dict(energy_receipt=dict(mechanical_work_kWh=shaft,electrical_work_kWh=electrical)))
    for key in ('hour_of_year','grid_tariff_USD_kWh'):
        if key in segment:minimal[key]=segment[key]
    audit=independent_energy_audit(dict(segments=[minimal],continuation_start_minute=a,
        summary={'requested_end_minute':b},m21_initial_storage={'ups':0.,'cold_store':0.}),
        accounting_parameters=economics.get('accounting_parameters',{}),tariffs_by_hour={})
    return audit['observed_operating_fee_USD2001']


def _stocks(segment):
    storage=segment.get('m21_storage') or {}
    values={}
    for name in ('ups','cold_store'):
        receipt=storage.get(name,{}) if isinstance(storage,dict) else {}
        receipt=receipt if isinstance(receipt,dict) else {}
        values[name]=(receipt.get('initial_kWh'),receipt.get('final_kWh'))
    before,after=segment.get('pre_state') or {},segment.get('post_state') or {}
    values['diesel_shaft']=(before.get('diesel_energy_remaining_kWh'),after.get('diesel_energy_remaining_kWh'))
    return values


def summarize_run(output):
    """Return a detached dict; authenticate producer records without source CSVs."""
    output=Path(output).resolve()
    evidence=output/'evidence'
    verified=journal.verify_journal(evidence)
    names=('CASE.json','RESULT.json','BINDINGS.json','evidence/IDENTITY.json','evidence/SUMMARY.json','evidence/blocks.jsonl')
    before={name:_sha(output/name) for name in names if (output/name).is_file()}
    if not all(name in before for name in names):
        raise ValueError('A closed actual CASE, RESULT, BINDINGS and journal are required')
    case=validate_case(_read(output/'CASE.json')['case'])
    result_fields={key:True for key in ('schema','status','case_id','case_sha256','model_id','source_sha256',
        'input_sha256','git_commit','observed_until_minute','requested_end_minute','unknown_tail_minutes',
        'source_and_inputs_unchanged')}
    result_fields['error']=_ERROR_FIELDS
    result=_selected(output/'RESULT.json',result_fields)
    summary=_selected(evidence/'SUMMARY.json',dict(status=True,observation=True,error=_ERROR_FIELDS))
    identity=verified['identity']
    expected=dict(case_id=case['id'],case_sha256=digest(case),model_id=MODEL_ID)
    if any(identity.get(key)!=value for key,value in expected.items()):
        raise ValueError('Journal differs from canonical annual case identity')
    for key in ('case_id','case_sha256','model_id','source_sha256','git_commit'):
        if result.get(key)!=identity.get(key) or result.get(key) is None:
            raise ValueError('Producer RESULT identity mismatch: '+key)
    inputs=result.get('input_sha256')
    if (digest(inputs) if isinstance(inputs,dict) else inputs)!=identity.get('input_sha256'):
        raise ValueError('Producer RESULT input identity mismatch')
    metric_sha=_sha(metrics.__file__)
    fee_sources={
        'tools/model_review_v30/evaluation.py':_sha(native_fee_evaluation.__file__),
        'tools/operating_rules_study/ledger.py':_sha(native_fee_ledger.__file__)}
    fee_sources_bound=False
    if 'BINDINGS.json' in before:
        bound=_read(output/'BINDINGS.json')
        if (bound.get('git_commit')!=identity['git_commit'] or digest(bound.get('source'))!=identity['source_sha256']
                or digest(bound.get('inputs'))!=identity['input_sha256']):
            raise ValueError('Frozen source/data binding metadata differs from journal')
        expected_metric=bound.get('source',{}).get('tools/model_correctness_v23/metrics.py')
        if expected_metric!=metric_sha:
            raise ValueError('Native service validation implementation differs from frozen source identity')
        fee_sources_bound=all(bound['source'].get(path)==sha for path,sha in fee_sources.items())
    observation=summary['observation']
    observed=observation['observed_until_minute']
    requested=case['scenario']['duration_steps']*case['scenario']['step_minutes']
    if (result.get('schema')!=MODEL_ID+'.runner.v1'
            or result.get('status') not in ('completed','failed') or result['status']!=summary['status']
            or result.get('observed_until_minute')!=observed or result.get('requested_end_minute')!=requested
            or result.get('unknown_tail_minutes')!=requested-observed
            or observation['requested_end_minute']!=requested or result.get('source_and_inputs_unchanged') is not True):
        raise ValueError('Producer execution, source status or observed prefix disagrees with closed journal')
    offset=(case['scenario']['start_hour']-1)*60.
    months=[_Period(f'2001-{n+1:02d}',MONTH_BOUNDS[n],MONTH_BOUNDS[n+1],offset,requested,observed) for n in range(12)]
    year=_Period('2001',0.,float(YEAR_MINUTES),offset,requested,observed)
    limitations={'inventory_minima_are_recorded_boundaries_only','starts_standby_and_replenishment_timing_unsupported',
        'source_and_input_bytes_not_reparsed_or_rechecked','no_cross_block_recovery_or_clinical_qualification'}
    definitions=[]
    previous_stocks={}
    for index,record in enumerate(verified['raw_files_sha256']):
        path=evidence/record['file']
        if _sha(path)!=record['sha256']:raise ValueError('Raw block changed after authentication')
        with gzip.open(path,'rt',encoding='utf8') as stream:block=json.load(stream)
        first_month=bisect_right(MONTH_BOUNDS,offset+block['start_minute'])-1
        last_month=bisect_right(MONTH_BOUNDS,offset+block['end_minute'])-1
        # Half-open endpoint at a month boundary belongs to the preceding block.
        if offset+block['end_minute'] in MONTH_BOUNDS:last_month-=1
        selected=months[first_month:last_month+1]
        economics=_economics(block)
        fee=economics.get('observed_operating_fee_USD2001')
        year.operating.add(fee)
        if len(selected)==1:selected[0].operating.add(fee)
        if index==0:
            for period in (year,months[first_month]):
                period.preparation_assigned=True
                period.preparation=economics.get('initial_preparation_fee_USD2001')
                period.preparation_count=1 if isinstance(block.get('initial_preparation'),dict) else None
        intervals,complete=_native_intervals(block,offset)
        _validate_native_service(block,complete)
        definition=block.get('native_material_definition')
        if isinstance(definition,dict):
            if definitions and definitions[0]!=definition:
                raise ValueError('Native material definition changed between blocks')
            if not definitions:definitions.append(copy.deepcopy(definition))
        for period in [year]+selected:period.material(intervals,complete)
        monthly_fees=[]
        for segment in block['segments']:
            source_start=offset+segment['start_minute']
            source_end=offset+segment['end_minute']
            stock=_stocks(segment)
            for name,(initial,final) in stock.items():
                if name in previous_stocks:
                    for period in [year]+[p for p in selected if p.start<=source_start<p.end]:
                        period.inventory[name].gap(source_start,previous_stocks[name],initial)
                previous_stocks[name]=final
            for period in [year]+selected:
                a,b=max(source_start,period.start),min(source_end,period.end)
                if b<=a:continue
                period.add_service(segment,a,b)
                for name,(initial,final) in stock.items():
                    period.inventory[name].add(initial if a==source_start else None,
                        final if b==source_end else None,int(a!=source_start)+int(b!=source_end))
                if period is not year and len(selected)>1:
                    if fee_sources_bound:
                        part=_fragment_fee(segment,a-offset,b-offset,economics,limitations)
                    else:
                        part=None;limitations.add('cross_month_fee_source_binding_unavailable')
                    period.operating.add(part)
                    monthly_fees.append(part)
        if monthly_fees and all(journal._number(v) for v in monthly_fees) and journal._number(fee):
            if abs(math.fsum(monthly_fees)-fee)>FEE_TOL:
                raise ValueError('Native monthly economic split differs from acknowledged block fee')
        del block
    after={name:_sha(output/name) for name in before}
    if before!=after:raise ValueError('Producer metadata changed during engineering summary')
    return dict(schema=SCHEMA,source_year=2001,time_clock='absolute source minute since 2001-01-01T00:00',
        months=[p.export() for p in months],year=year.export(),
        execution=dict(status=result['status'],error=copy.deepcopy(result.get('error')),
            error_reference=dict(producer_file='RESULT.json',sha256=before['RESULT.json'],json_pointer='/error',
                complete_error_and_diagnostics_preserved_in_producer=True),
            journal_error=copy.deepcopy(summary.get('error')),observed_until_minute=observed,
            requested_end_minute=requested,unknown_tail_minutes=requested-observed),
        native_material_definition=definitions[0] if definitions else None,
        operations=dict(starts=None,standby_minutes=None,reason='No approved unambiguous actual operation predicate in this summary'),
        stock_replenishment_timing=None,limitations=sorted(limitations),
        provenance=dict(producer_directory=str(output),journal_identity=copy.deepcopy(identity),
            producer_files_sha256=before,producer_files_unchanged=True,source_input_bytes_rechecked=False,
            native_recorded_material_intervals_used=True,materiality_threshold_redefined=False,
            native_service_validation_sha256=metric_sha,selected_result_metadata_read_in_bounded_chunks=True,
            cross_month_fee_sources_sha256=fee_sources,cross_month_fee_sources_bound=fee_sources_bound,
            maximum_decoded_blocks=1,full_raw_trajectory_retained=False,
            metadata_and_compact_output_size_not_bounded=True,simulation_rerun=False))
