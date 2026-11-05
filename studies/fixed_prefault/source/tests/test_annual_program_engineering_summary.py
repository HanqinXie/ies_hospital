"""Monthly engineering arithmetic over synthetic closed native journals; no solve."""
import copy
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from tools.annual_program import MODEL_ID
from tools.annual_program.registration import make_case
from tools.annual_readiness.journal import BlockJournal
from tools.annual_readiness.runner import _block_evaluation
from tools.sci_engineering.design import digest


def implementation():
    try:
        return importlib.import_module('tools.annual_program.engineering_summary')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_program.engineering_summary':
            raise
        pytest.fail('Streamed monthly engineering summary is missing', pytrace=False)


PARAMETERS = dict(gas_price_usd_per_mmbtu=2., dg_shaft_fee_USD_per_kWh=.3,
    steam_kWh_per_lb=.3, boiler_eff=.8, kWh_per_MMBtu=293.0710702,
    RT_TO_KW=3.51685284, ec_electric_kW_per_rt=.77, cold_pump_kW_per_kW=.02)


def segment(start, end, *, stock_initial=10., stock_final=8., missing=False, dg_work=0.):
    request = [[12., 24., 0.]] + [[0., 0., 0.] for _ in range(3)]
    cut = [[2., 4., 0.]] + [[0., 0., 0.] for _ in range(3)]
    connected = [[1., 2., 0.]] + [[0., 0., 0.] for _ in range(3)]
    delivered = [[9., 18., 0.]] + [[0., 0., 0.] for _ in range(3)]
    row = dict(start_minute=start, end_minute=end,
        service_request_ledger=dict(original_demand_kW=request, active_cut_kW=cut,
            connected_unserved_kW=connected, delivered_kW=delivered),
        action=dict(grid_import_kW=5., gt_fuel_MMBtu_h=1., boiler_steam_lbph=0., diesel_kW=0.),
        grid_tariff_USD_kWh=.1,
        diesel_local_response=dict(energy_receipt=dict(mechanical_work_kWh=dg_work, electrical_work_kWh=0.)),
        pre_state=dict(diesel_energy_remaining_kWh=100.-start/60.),
        post_state=dict(diesel_energy_remaining_kWh=100.-end/60.),
        m21_storage={name:dict(initial_kWh=stock_initial, final_kWh=stock_final,
            charged_input_kWh=0., discharged_kWh=stock_initial-stock_final,
            standing_loss_kWh=0., conversion_loss_kWh=0.) for name in ('ups','cold_store')})
    if missing:row.pop('service_request_ledger')
    return row


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')


def fixture_run(tmp_path, blocks=None, *, start_hour=744, requested=120., status='completed', transform_block=None,include_metric_source=True,fee_source_damage=None):
    # Hour744 begins on Jan31 23:00. The true Feb boundary is local minute60.
    run = tmp_path/'producer'
    run.mkdir(parents=True)
    case = make_case('M_ready', start_hour=start_hour, duration_minutes=requested)
    bindings = dict(git_commit='synthetic-closed-summary-test', source={'synthetic_native_fixture': 'a'*64},
                    inputs={'synthetic_input_fixture': 'b'*64})
    if include_metric_source:
        from tools.model_correctness_v23 import metrics
        bindings['source']['tools/model_correctness_v23/metrics.py']=hashlib.sha256(Path(metrics.__file__).read_bytes()).hexdigest()
    for relative in ('tools/model_review_v30/evaluation.py','tools/operating_rules_study/ledger.py'):
        bindings['source'][relative]=hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
    if fee_source_damage=='missing_evaluation':bindings['source'].pop('tools/model_review_v30/evaluation.py')
    if fee_source_damage=='drift_ledger':bindings['source']['tools/operating_rules_study/ledger.py']='0'*64
    identity = dict(case_id=case['id'], case_sha256=digest(case), model_id=MODEL_ID,
        git_commit=bindings['git_commit'], source_sha256=digest(bindings['source']),
        input_sha256=digest(bindings['inputs']))
    write(run/'CASE.json', {'case':case})
    write(run/'BINDINGS.json', bindings)
    journal = BlockJournal(run/'evidence', identity=identity)
    if blocks is None:
        blocks = [[segment(0.,60.,stock_initial=10.,stock_final=8.)],
                  [segment(60.,120.,stock_initial=8.,stock_final=5.)]]
    for index, segments in enumerate(blocks):
        start, end = segments[0]['start_minute'], segments[-1]['end_minute']
        preparation = {'ups':2.,'cold_store':0.} if index==0 else {'ups':0.,'cold_store':0.}
        try:
            evaluation = _block_evaluation(segments,start,end,preparation=preparation,
                parameters=PARAMETERS,tariffs={})
        except ValueError:
            evaluation = dict(native_material_intervals=None, native_material_definition=None,
                block_native_support_complete=False, economics=None)
        block = dict(start_minute=start,end_minute=end,segments=segments,events=[],solver_calls=[],
            decision={},terminal={},initial_preparation=preparation,error=None,**evaluation)
        if transform_block is not None:transform_block(block)
        journal.append(block)
    error = {'type':'kept_native_failure','message':'actual failed prefix'} if status=='failed' else None
    summary = journal.close(status=status, expected_end_minute=requested, error=error)
    observed = summary['observation']['observed_until_minute']
    result = dict(identity,status=status,observed_until_minute=observed,requested_end_minute=requested,
        unknown_tail_minutes=requested-observed,input_sha256=bindings['inputs'],error=error,
        source_and_inputs_unchanged=True,schema=MODEL_ID+'.runner.v1')
    write(run/'RESULT.json',result)
    return run


@pytest.mark.parametrize('missing',['bindings','native_metric_binding'])
def test_native_monthly_qualification_requires_bound_source_metadata(tmp_path,missing):
    run=fixture_run(tmp_path,include_metric_source=missing!='native_metric_binding')
    if missing=='bindings':(run/'BINDINGS.json').unlink()
    with pytest.raises(ValueError,match='binding|BINDINGS|source|Source'):
        implementation().summarize_run(run)


def test_true_month_boundary_and_yearly_service_sums_without_reset(tmp_path):
    run = fixture_run(tmp_path)
    report = implementation().summarize_run(run)
    jan, feb = report['months'][:2]
    assert jan['label']=='2001-01' and feb['label']=='2001-02'
    assert jan['observation']['observed_duration_minutes']==60.
    assert feb['observation']['observed_duration_minutes']==60.
    for period in (jan,feb):
        row = period['carrier_service']['P0']['e']
        assert {k:row[k] for k in ('requested_kWh','delivered_kWh','active_cut_kWh','connected_unserved_kWh','ENS_kWh')} == {
            'requested_kWh':12.,'delivered_kWh':9.,'active_cut_kWh':2.,'connected_unserved_kWh':1.,'ENS_kWh':3.}
    assert report['year']['carrier_service']['P0']['h']['requested_kWh']==48.
    for field in ('requested_kWh','delivered_kWh','active_cut_kWh','connected_unserved_kWh','ENS_kWh'):
        assert report['year']['carrier_service']['P0']['e'][field] == sum(p['carrier_service']['P0']['e'][field] for p in (jan,feb))
    assert jan['inventory']['ups']['initial_stock_kWh']==10.
    assert jan['inventory']['ups']['final_stock_kWh']==feb['inventory']['ups']['initial_stock_kWh']==8.
    assert feb['inventory']['ups']['final_stock_kWh']==5.
    assert report['year']['inventory']['ups']['minimum_stock_kWh']==5.
    assert report['year']['inventory']['ups']['receipt_chain_max_gap_kWh']==0.
    assert report['months'][2]['carrier_service']['P0']['e']['ENS_kWh'] is None
    assert report['months'][2]['observation']['requested_duration_minutes']==0.


def test_cross_month_constant_service_integrals_and_native_interval_union(tmp_path):
    run = fixture_run(tmp_path,[[segment(0.,120.,stock_initial=10.,stock_final=5.)]])
    report = implementation().summarize_run(run)
    jan,feb = report['months'][:2]
    assert jan['carrier_service']['P0']['e']['requested_kWh']==12.
    assert feb['carrier_service']['P0']['e']['requested_kWh']==12.
    material = report['year']['material_interruption']
    assert material['by_priority']['P0']['material_minutes']==120.
    assert material['by_priority']['P0']['intervals_source_minutes']==[[44580.,44700.]]
    assert jan['material_interruption']['by_priority']['P0']['intervals_source_minutes']==[[44580.,44640.]]
    assert feb['material_interruption']['by_priority']['P0']['intervals_source_minutes']==[[44640.,44700.]]
    assert material['critical_union']['material_minutes']==120.  # e+h overlap, not240.
    assert report['year']['inventory']['ups']['initial_stock_kWh']==10.
    assert report['year']['inventory']['ups']['final_stock_kWh']==5.
    assert jan['inventory']['ups']['final_stock_kWh'] is None
    assert feb['inventory']['ups']['initial_stock_kWh'] is None
    assert jan['inventory']['ups']['unrecorded_period_boundary_count']==1
    assert report['stock_replenishment_timing'] is None


def test_native_preparation_once_and_monthly_economics_sum(tmp_path):
    run = fixture_run(tmp_path)
    report = implementation().summarize_run(run)
    jan,feb = [row['economics'] for row in report['months'][:2]]
    year = report['year']['economics']
    assert jan['initial_preparation_fee_USD2001']==pytest.approx(.2)
    assert feb['initial_preparation_fee_USD2001']==0.
    assert jan['observed_operating_fee_USD2001']==pytest.approx(2.5)
    assert feb['observed_operating_fee_USD2001']==pytest.approx(2.5)
    assert year['actual_fee_USD2001']==pytest.approx(5.2)
    assert year['actual_fee_USD2001']==pytest.approx(jan['actual_fee_USD2001']+feb['actual_fee_USD2001'])
    assert year['initial_preparation_occurrence_count']==1
    assert year['price_year']==2001 and year['currency']=='USD'


def test_cross_month_fee_uses_native_raw_formula_when_split_receipts_are_exact(tmp_path):
    run = fixture_run(tmp_path,[[segment(0.,120.,stock_initial=10.,stock_final=5.)]])
    report = implementation().summarize_run(run)
    jan,feb = [row['economics'] for row in report['months'][:2]]
    assert jan['actual_fee_USD2001']==pytest.approx(2.7)
    assert feb['actual_fee_USD2001']==pytest.approx(2.5)
    assert report['year']['economics']['actual_fee_USD2001']==pytest.approx(5.2)


@pytest.mark.parametrize('damage',['missing_evaluation','drift_ledger'])
def test_cross_month_fee_recomputation_requires_exact_native_fee_sources(tmp_path,monkeypatch,damage):
    run=fixture_run(tmp_path,[[segment(0.,120.)]],fee_source_damage=damage)
    module=implementation()
    monkeypatch.setattr(module,'_fragment_fee',lambda *a,**k:pytest.fail('Unbound fee formula must not execute'))
    report=module.summarize_run(run)
    assert report['year']['economics']['actual_fee_USD2001']==pytest.approx(5.2)
    assert all(row['economics']['observed_operating_fee_USD2001'] is None for row in report['months'][:2])
    assert 'cross_month_fee_source_binding_unavailable' in report['limitations']


def test_nonzero_integrated_dg_work_is_not_prorated_across_unrecorded_boundary(tmp_path):
    run = fixture_run(tmp_path,[[segment(0.,120.,stock_initial=10.,stock_final=5.,dg_work=4.)]])
    report = implementation().summarize_run(run)
    assert report['year']['economics']['actual_fee_USD2001']==pytest.approx(6.4)
    assert report['months'][0]['economics']['observed_operating_fee_USD2001'] is None
    assert report['months'][1]['economics']['observed_operating_fee_USD2001'] is None
    assert 'integrated_DG_receipt_crosses_unrecorded_month_boundary' in report['limitations']


def test_month_boundary_stock_discontinuity_is_visible_not_reset_or_tolerance_erased(tmp_path):
    run = fixture_run(tmp_path,[[segment(0.,60.,stock_initial=10.,stock_final=8.)],
                              [segment(60.,120.,stock_initial=7.,stock_final=5.)]])
    report = implementation().summarize_run(run)
    inventory = report['year']['inventory']['ups']
    assert inventory['receipt_chain_max_gap_kWh']==1.
    assert inventory['continuity_gaps']==[{'source_minute':44640.,'previous_final_kWh':8.,'next_initial_kWh':7.,'gap_kWh':1.}]
    assert report['months'][1]['inventory']['ups']['initial_stock_kWh']==7.
    assert report['months'][1]['inventory']['ups']['continuity_gaps']==inventory['continuity_gaps']


def test_failed_prefix_and_unobserved_requested_tail_stay_explicit(tmp_path):
    run = fixture_run(tmp_path,[[segment(0.,60.)]],requested=120.,status='failed')
    report = implementation().summarize_run(run)
    assert report['execution']['status']=='failed'
    assert report['execution']['error']['type']=='kept_native_failure'
    assert report['year']['observation']['unknown_tail_minutes']==60.
    assert report['months'][1]['observation']['requested_duration_minutes']==60.
    assert report['months'][1]['observation']['observed_duration_minutes']==0.
    assert report['months'][1]['carrier_service']['P0']['e']['ENS_kWh'] is None
    assert report['year']['unobserved_tail_service'] is None
    assert report['year']['economics']['unobserved_tail_fee_USD2001'] is None


def test_empty_failed_prefix_is_unknown_not_all_zero(tmp_path):
    run = fixture_run(tmp_path,[],status='failed')
    report = implementation().summarize_run(run)
    assert report['year']['carrier_service']['P0']['e']['requested_kWh'] is None
    assert report['year']['material_interruption']['critical_union']['material_minutes'] is None
    assert report['year']['inventory']['diesel_shaft']['initial_stock_kWh'] is None
    assert report['year']['economics']['initial_preparation_fee_USD2001'] is None
    assert report['year']['economics']['initial_preparation_occurrence_count'] is None


def test_missing_ledger_receipt_and_economics_preserve_unknowns(tmp_path):
    row = segment(0.,60.,missing=True)
    row.pop('m21_storage')
    run = fixture_run(tmp_path,[[row]],requested=60.)
    report = implementation().summarize_run(run)
    assert report['year']['carrier_service']['P0']['e']['ENS_kWh'] is None
    assert report['year']['inventory']['ups']['minimum_stock_kWh'] is None
    assert report['year']['material_interruption']['critical_union']['material_minutes'] is None
    assert report['year']['economics']['actual_fee_USD2001'] is None
    assert report['operations']['starts'] is None
    assert report['operations']['standby_minutes'] is None


@pytest.mark.parametrize('damage',['case','source','inputs','observed','schema','raw','open'])
def test_identity_changed_evidence_or_open_journal_is_rejected(tmp_path,damage):
    run = fixture_run(tmp_path)
    if damage in ('case','source','inputs','observed','schema'):
        result=json.loads((run/'RESULT.json').read_text(encoding='utf8'))
        if damage=='case':result['case_sha256']='0'*64
        elif damage=='source':result['source_sha256']='0'*64
        elif damage=='inputs':result['input_sha256']={'changed':'0'*64}
        elif damage=='schema':result['schema']='unregistered-legacy-result'
        else:result['observed_until_minute']=30.
        write(run/'RESULT.json',result)
    elif damage=='raw':
        path=run/'evidence/blocks/block_000000.json.gz'
        path.write_bytes(path.read_bytes()+b'changed')
    else:(run/'evidence/SUMMARY.json').unlink()
    with pytest.raises(ValueError):implementation().summarize_run(run)


def test_exact_producer_identity_and_readonly_one_raw_block_stream(tmp_path,monkeypatch):
    run = fixture_run(tmp_path)
    before={str(p.relative_to(run)):p.read_bytes() for p in run.rglob('*') if p.is_file()}
    original=gzip.open
    live=maximum=0
    class OpenBlock:
        def __init__(self,*args,**kwargs):self.stream=original(*args,**kwargs)
        def __enter__(self):
            nonlocal live,maximum
            live+=1;maximum=max(maximum,live)
            return self.stream.__enter__()
        def __exit__(self,*args):
            nonlocal live
            try:return self.stream.__exit__(*args)
            finally:live-=1
    monkeypatch.setattr(gzip,'open',OpenBlock)
    report=implementation().summarize_run(run)
    assert live==0 and maximum==1
    assert report['provenance']['maximum_decoded_blocks']==1
    assert report['provenance']['full_raw_trajectory_retained'] is False
    assert report['provenance']['journal_identity']==json.loads((run/'evidence/IDENTITY.json').read_text())
    assert report['provenance']['source_input_bytes_rechecked'] is False
    assert report['provenance']['producer_files_unchanged'] is True
    assert {str(p.relative_to(run)):p.read_bytes() for p in run.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('start_hour,requested,observed_months',[
    (1416,120.,['2001-02','2001-03']), (8760,60.,['2001-12']),
])
def test_nonleap_february_and_source_year_endpoint(start_hour,requested,observed_months,tmp_path):
    blocks=[[segment(0.,60.,stock_initial=10.,stock_final=8.)]]
    if requested==120.:blocks.append([segment(60.,120.,stock_initial=8.,stock_final=5.)])
    run=fixture_run(tmp_path,blocks,start_hour=start_hour,requested=requested)
    report=implementation().summarize_run(run)
    assert len([m for m in report['months'] if m['observation']['observed_duration_minutes']])>0
    assert [m['label'] for m in report['months'] if m['observation']['observed_duration_minutes']]==observed_months
    assert report['months'][1]['source_period_end_minute']==59*1440.
    assert report['months'][-1]['source_period_end_minute']==365*1440.


@pytest.mark.parametrize('damage',['negative_service','inconsistent_service','wrong_recorded_interval'])
def test_native_semantic_validation_rejects_contradictory_numeric_service_or_intervals(tmp_path,damage):
    row=segment(0.,60.)
    transform=None
    if damage=='negative_service':row['service_request_ledger']['active_cut_kW'][0][0]=-3.
    elif damage=='inconsistent_service':row['service_request_ledger']['delivered_kW'][0][0]=99.
    else:
        def transform(block):block['native_material_intervals']['P0']['e']=[]
    run=fixture_run(tmp_path,[[row]],requested=60.,transform_block=transform)
    with pytest.raises(ValueError):implementation().summarize_run(run)


def test_old_large_error_diagnostic_is_referenced_not_loaded_or_embedded_again(tmp_path,monkeypatch):
    run=fixture_run(tmp_path,[[segment(0.,60.)]],requested=120.,status='failed')
    path=run/'RESULT.json'
    result=json.loads(path.read_text(encoding='utf8'))
    result['error']['diagnostic']={'completed_segments':['preserved original raw history'*100000]}
    write(path,result)
    original=Path.open
    read_sizes=[]
    class BoundedResultText:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def read(self,size=-1):
            assert 0<size<=65536, 'RESULT metadata must be selected in bounded chunks'
            read_sizes.append(size)
            return self.stream.read(size)
    def bounded_open(self,*args,**kwargs):
        stream=original(self,*args,**kwargs)
        mode=args[0] if args else kwargs.get('mode','r')
        return BoundedResultText(stream) if self==path and mode=='r' else stream
    monkeypatch.setattr(Path,'open',bounded_open)
    report=implementation().summarize_run(run)
    assert read_sizes
    assert 'diagnostic' not in report['execution']['error']
    assert report['execution']['error']['type']=='kept_native_failure'
    assert report['execution']['error_reference']['json_pointer']=='/error'
    assert report['execution']['error_reference']['producer_file']=='RESULT.json'
    assert len(json.dumps(report))<200000
