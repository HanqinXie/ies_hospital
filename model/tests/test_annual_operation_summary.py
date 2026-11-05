"""Read-only operations over native synthetic journals; no environment/solver."""
import copy
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.annual_program import MODEL_ID
from tools.annual_program.registration import make_case
from tools.annual_readiness.journal import BlockJournal
from tools.sci_engineering.design import digest
from tools.engineering_model.analysis import _gt_operation
from reviewed_source_fixtures import reviewed_source_bytes

SOURCES=('src/hospital_ies/state_transition.py','tools/engineering_model/execution.py',
    'tools/engineering_model/analysis.py','tools/model_closure_v22/environment.py',
    'tools/model_correctness_v23/environment.py','tools/model_correctness_v23/protection.py',
    'tools/sci_engineering/storage.py','tools/sci_engineering/environment.py',
    'tools/annual_readiness/environment.py','tools/annual_readiness/station.py',
    'tools/execution_recovery/execution.py','tools/annual_readiness/analysis.py',
    'tools/annual_readiness/journal.py')

def implementation():
    try:return importlib.import_module('tools.annual_program.operation_summary')
    except ModuleNotFoundError as exc:
        if exc.name!='tools.annual_program.operation_summary':raise
        pytest.fail('Read-only annual operation summary is missing',pytrace=False)


@pytest.fixture(autouse=True)
def historical_operation_sources(tmp_path,monkeypatch):
    """Bind this legacy observer fixture to its exact historical definitions.

    SF1 changes M23 and the historical store hook. The old summary deliberately
    refuses those current bytes; its producer context must point to a separate
    parse-only source tree instead. Observer implementation identity is separate.
    """
    api=implementation();source=tmp_path/'historical_operation_sources'
    changed={'tools/model_correctness_v23/environment.py','tools/sci_engineering/storage.py'}
    assert set(SOURCES)==set(api.SOURCE_SHA256)
    for name in SOURCES:
        raw=reviewed_source_bytes(name) if name in changed else (ROOT/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==api.SOURCE_SHA256[name]
        target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    monkeypatch.setattr(api,'ROOT',source)

def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')

def segment(a,b,*,gt=(0.,0.,5.),end_gt=None,ready_before=True,ready_after=True,steam=0.,dg=0.):
    storage={name:dict(initial_kWh=10.,final_kWh=8.,charged_input_kWh=0.,discharged_kWh=1.,
        standing_loss_kWh=.5,conversion_loss_kWh=.5,balance_residual_kWh=0.) for name in ('ups','cold_store')}
    return dict(start_minute=a,end_minute=b,source_start_gt_kW=list(gt),
        source_end_gt_kW=list(end_gt if end_gt is not None else gt),
        action=dict(gt_electric_kW=list(gt),diesel_kW=dg,boiler_steam_lbph=steam,
            boiler_auxiliary_kW=.5 if steam else 0.,boiler_restart_electric_kW=.5 if not ready_before else 0.,
            gt_start_electric_kW=0.,ups_charge_kW=0.,ups_discharge_kW=1.,cold_charge_kW=0.,cold_discharge_kW=1.),
        pre_state=dict(gt_prev=list(gt),diesel_status='CONNECTED' if dg else 'OFF'),
        m22_station=dict(boiler_ready_before_actual_interval=ready_before,boiler_ready=ready_after,
            boiler_execution_status='running' if steam else 'held_setting_unexecutable_after_service_locks'),
        m23_restoration=dict(boiler_ready=ready_after),
        # Deliberately post-parent boiler state: not interval-start readiness.
        annual_readiness=dict(qualification_before=dict(boiler_ready=ready_after)),
        arrived_source_permissions=dict(boiler_steam_lbph=3000.),m21_storage=storage)

def fixture_run(tmp_path,blocks=None,events=None,*,requested=120.,status='completed',start_hour=744,damage_source=False):
    output=tmp_path/'producer';output.mkdir(parents=True)
    case=make_case('M_ready',start_hour=start_hour,duration_minutes=requested)
    binding=dict(git_commit='synthetic-operations',source={name:hashlib.sha256((implementation().ROOT/name).read_bytes()).hexdigest()
        for name in SOURCES},inputs={'synthetic_only':'a'*64})
    if damage_source:binding['source'][SOURCES[0]]='0'*64
    identity=dict(case_id=case['id'],case_sha256=digest(case),model_id=MODEL_ID,
        git_commit=binding['git_commit'],source_sha256=digest(binding['source']),input_sha256=digest(binding['inputs']))
    write(output/'CASE.json',dict(case=case));write(output/'BINDINGS.json',binding)
    journal=BlockJournal(output/'evidence',identity=identity)
    blocks=blocks if blocks is not None else [[segment(0.,1.,ready_before=False,ready_after=True),segment(1.,60.)],[segment(60.,120.)]]
    for index,rows in enumerate(blocks):
        journal.append(dict(start_minute=rows[0]['start_minute'],end_minute=rows[-1]['end_minute'],segments=rows,
            solver_calls=[],events=(events or {}).get(index,[]),decision={},terminal={}))
    error=dict(type='native_failure',message='retained error prefix') if status=='failed' else None
    summary=journal.close(status=status,expected_end_minute=requested,error=error)
    observed=summary['observation']['observed_until_minute']
    write(output/'RESULT.json',dict(identity,schema=MODEL_ID+'.runner.v1',input_sha256=binding['inputs'],
        status=status,error=error,observed_until_minute=observed,requested_end_minute=requested,
        unknown_tail_minutes=requested-observed,source_and_inputs_unchanged=True))
    return output


def test_historical_native_source_byte_drift_remains_rejected(tmp_path):
    output=fixture_run(tmp_path)
    api=implementation();path=api.ROOT/'tools/model_correctness_v23/environment.py'
    path.write_bytes(path.read_bytes()+b'\n# drift only in the parse-only E fixture\n')
    with pytest.raises(ValueError,match='Native operation definition source binding changed'):
        api.summarize_run(output)

def event(number,kind,time,**fields):return dict(event_id=number,kind=kind,time_minute=time,**fields)

def test_boiler_interval_start_readiness_and_initial_online_are_not_starts(tmp_path):
    initial=event(1,'m22_station_initial_state',0.,initial_GT_online=[False,False,True])
    output=fixture_run(tmp_path,events={0:[initial]})
    report=implementation().summarize_run(output)
    boiler=report['year']['boiler_bank'];gt=report['year']['GT_3']
    assert boiler['functional_ready_hours']==pytest.approx(119/60)
    assert boiler['permission_ready_zero_steam_hours']==pytest.approx(119/60)
    assert boiler['positive_steam_hours']==0.
    assert boiler['functional_restart_completion_count']==1
    assert boiler['restart_electricity_kWh']==pytest.approx(.5/60)
    assert gt['initial_online'] is True and gt['actual_started_event_count']==0
    assert gt['zero_to_positive_count']==0 and gt['observed_running_hours']==2.
    assert gt['first_positive_settled_output_source_minute']==44580.
    assert report['unsupported']['OEM_startup_gas_MMBtu'] is None
    assert report['unsupported']['hot_standby_hours'] is None
    assert report['unsupported']['standby_fee_USD2001'] is None

def test_gt_events_are_distinct_and_native_linear_running_hours_match(tmp_path):
    rows=[segment(0.,30.,end_gt=(0.,0.,0.)),segment(30.,60.,gt=(2.,0.,0.)),
          segment(60.,90.,gt=(2.,0.,0.)),segment(90.,120.,gt=(2.,0.,0.),end_gt=(0.,0.,0.))]
    events=[event(1,'m22_station_initial_state',0.,initial_GT_online=[False,False,True]),
        event(2,'gt_start_requested',20.,device='GT_1'),event(3,'gt_start_process_begun',25.,device='GT_1'),
        event(4,'gt_start_completed',30.,device='GT_1',actual_kW=2.),
        event(5,'gt_actual_started',30.,device='GT_1',before_kW=0.,after_kW=2.),
        event(6,'gt_start_completed',100.,device='GT_2',actual_kW=0.)]
    output=fixture_run(tmp_path,[rows],{0:events},start_hour=1)
    expected,_=_gt_operation(dict(initial_state={'gt_prev':[0.,0.,5.]},segments=rows,events=events),120.)
    report=implementation().summarize_run(output)
    for device in ('GT_1','GT_2','GT_3'):
        actual=report['year'][device];native=expected[device]
        assert actual['observed_running_hours']==pytest.approx(native['observed_running_minutes']/60.)
        for field in ('zero_to_positive_count','positive_to_zero_count','start_requested_count',
            'start_process_begun_count','start_completed_event_count','start_completed_positive_count'):
            assert actual[field]==native[field]
    assert report['year']['GT_1']['actual_started_event_count']==1
    assert report['year']['GT_2']['start_completed_event_count']==1
    assert report['year']['GT_2']['start_completed_positive_count']==0
    assert report['definitions']['GT_operating_positive_kW']==1e-4
    assert report['definitions']['native_event_EPS_kW']==1e-9

def test_dg_state_and_dc_event_count_one_attempt_and_skip_all_historical_tokens(tmp_path,monkeypatch):
    marker='historical_token_sentinel'
    events=[event(1,'diesel_state_changed',15.,before='OFF',after='STARTING'),
        event(2,'m22_DG_dedicated_DC_start',15.,station=dict(total_diesel_starts_used=1,
            diesel_starts_used=1,claimed_actual_tokens=[marker*50_000])),
        event(3,'diesel_state_changed',30.,before='STARTING',after='READY'),
        event(4,'diesel_state_changed',30.25,before='READY',after='CONNECTED')]
    output=fixture_run(tmp_path,events={0:events})
    original=json.loads
    def bounded(value,*args,**kwargs):
        assert marker not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(json,'loads',bounded)
    report=implementation().summarize_run(output)
    dg=report['year']['DG']
    assert dg['actual_start_attempt_count']==1
    assert dg['STARTING_entry_count']==dg['READY_entry_count']==dg['CONNECTED_entry_count']==1
    assert dg['start_requested_count'] is None and dg['start_completed_count'] is None
    assert 'claimed_actual_tokens' not in json.dumps(report)

def test_month_boundary_preserves_open_gt_and_bank_production_state(tmp_path):
    rows=[[segment(0.,60.,steam=10.)],[segment(60.,120.,steam=10.)]]
    report=implementation().summarize_run(fixture_run(tmp_path,rows))
    jan,feb=report['months'][:2]
    assert jan['GT_3']['observed_running_hours']==feb['GT_3']['observed_running_hours']==1.
    assert report['year']['GT_3']['zero_to_positive_count']==0
    assert report['year']['boiler_bank']['positive_steam_entry_count']==0
    assert report['year']['boiler_bank']['initial_producing_left_censored'] is True
    assert jan['boiler_bank']['steam_lb']+feb['boiler_bank']['steam_lb']==report['year']['boiler_bank']['steam_lb']==20.
    assert report['months'][2]['GT_3']['observed_running_hours'] is None

def test_storage_carriers_and_known_zero_missing_partial_receipts_remain_distinct(tmp_path):
    first=segment(0.,60.);second=segment(60.,120.)
    second['m21_storage']['cold_store'].pop('discharged_kWh')
    report=implementation().summarize_run(fixture_run(tmp_path,[[first],[second]]))
    ups=report['year']['storage']['ups'];cold=report['year']['storage']['cold_store']
    assert ups['carrier']=='electric' and cold['carrier']=='cold'
    assert ups['charged_input_kWh']==0. and ups['discharged_kWh']==2.
    assert ups['standing_loss_kWh']==1. and ups['conversion_loss_kWh']==1.
    assert cold['discharged_kWh'] is None
    assert cold['known_subtotals']['discharged_kWh']==1.

def test_cross_month_integrated_storage_receipts_are_not_prorated(tmp_path):
    report=implementation().summarize_run(fixture_run(tmp_path,[[segment(0.,120.)]]))
    assert report['year']['storage']['cold_store']['standing_loss_kWh']==.5
    assert all(period['storage']['cold_store']['standing_loss_kWh'] is None for period in report['months'][:2])
    assert 'integrated_storage_receipt_crosses_period_boundary' in report['limitations']

@pytest.mark.parametrize('damage',['duplicate_event','nonmonotonic_event','event_after_block','missing_event_id'])
def test_ambiguous_events_reject_instead_of_silent_dedup(tmp_path,damage):
    events=[event(1,'gt_start_requested',1.,device='GT_1'),event(2,'gt_start_requested',2.,device='GT_2')]
    if damage=='duplicate_event':events[1]['event_id']=1
    elif damage=='nonmonotonic_event':events[0]['event_id']=3
    elif damage=='event_after_block':events[1]['time_minute']=121.
    else:events[1].pop('event_id')
    output=fixture_run(tmp_path,events={0:events})
    with pytest.raises(ValueError,match='event|Event'):implementation().summarize_run(output)

@pytest.mark.parametrize('damage',['source_binding','result_identity','raw_change','orphan','open','metadata_change'])
def test_source_identity_integrity_or_live_writer_rejects_readonly(tmp_path,damage,monkeypatch):
    output=fixture_run(tmp_path,damage_source=damage=='source_binding')
    if damage=='result_identity':
        p=output/'RESULT.json';v=json.loads(p.read_text());v['case_id']='wrong';write(p,v)
    elif damage=='raw_change':
        p=output/'evidence/blocks/block_000000.json.gz';p.write_bytes(p.read_bytes()+b'changed')
    elif damage=='orphan':(output/'evidence/blocks/orphan.json.gz').write_bytes(b'orphan')
    elif damage=='open':(output/'evidence/SUMMARY.json').unlink()
    elif damage=='metadata_change':
        api=implementation();original=api._sha;calls=0
        def changed(path):
            nonlocal calls
            if Path(path).name=='CASE.json':
                calls+=1
                if calls>1:return '0'*64
            return original(path)
        monkeypatch.setattr(api,'_sha',changed)
    with pytest.raises(ValueError):implementation().summarize_run(output)

def test_failed_prefix_and_empty_prefix_keep_unknown_tail(tmp_path):
    report=implementation().summarize_run(fixture_run(tmp_path/'partial',[[segment(0.,60.)]],status='failed'))
    assert report['execution']['status']=='failed' and report['execution']['unknown_tail_minutes']==60.
    assert report['year']['GT_3']['observed_running_hours']==1.
    assert report['unobserved_tail_operations'] is None
    empty=implementation().summarize_run(fixture_run(tmp_path/'empty',[],status='failed'))
    assert empty['year']['GT_3']['start_requested_count'] is None
    assert empty['year']['boiler_bank']['functional_ready_hours'] is None

def test_selected_prefix_reports_scope_and_raw_read_bound_without_mutating_producer(tmp_path,monkeypatch):
    output=fixture_run(tmp_path)
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file()}
    original=gzip.open;opened=[]
    def observe(path,*args,**kwargs):opened.append(Path(path).name);return original(path,*args,**kwargs)
    monkeypatch.setattr(gzip,'open',observe)
    report=implementation().summarize_run(output,end_minute=30.)
    assert report['execution']['analysed_until_minute']==30.
    assert report['execution']['producer_observed_until_minute']==120.
    assert report['year']['GT_3']['observed_running_hours']==.5
    assert report['provenance']['raw_content_authentication_scope']=='selected_prefix_blocks_only'
    assert opened==['block_000000.json.gz']
    assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file()}

@pytest.mark.parametrize('missing',['ready','GT_curve','boiler_permission','bad_storage_number'])
def test_missing_or_invalid_scientific_fields_are_unknown_not_zero(tmp_path,missing):
    row=segment(0.,120.)
    if missing=='ready':row['m22_station'].pop('boiler_ready_before_actual_interval')
    elif missing=='GT_curve':row.pop('source_end_gt_kW')
    elif missing=='boiler_permission':row.pop('arrived_source_permissions')
    else:row['m21_storage']['ups']['charged_input_kWh']=True
    report=implementation().summarize_run(fixture_run(tmp_path,[[row]]))
    if missing=='ready':assert report['year']['boiler_bank']['functional_ready_hours'] is None
    elif missing=='GT_curve':assert report['year']['GT_3']['observed_running_hours'] is None
    elif missing=='boiler_permission':assert report['year']['boiler_bank']['permission_ready_zero_steam_hours'] is None
    else:assert report['year']['storage']['ups']['charged_input_kWh'] is None


def test_native_eps_initial_flag_does_not_override_engineering_operating_threshold(tmp_path):
    rows=[[segment(0.,120.,gt=(0.,0.,1e-5))]]
    output=fixture_run(tmp_path,rows,{0:[event(1,'m22_station_initial_state',0.,initial_GT_online=[False,False,True])]})
    report=implementation().summarize_run(output)
    assert report['year']['GT_3']['initial_online'] is False
    assert report['year']['GT_3']['given_initial_online_native_EPS'] is True
    assert report['year']['GT_3']['observed_running_hours']==0.


def test_first_output_respects_period_boundary_and_unknown_earlier_power(tmp_path):
    report=implementation().summarize_run(fixture_run(tmp_path/'cross',[[segment(0.,120.)]]))
    assert report['months'][1]['GT_3']['first_positive_settled_output_source_minute']==44640.
    first=segment(0.,60.);first['action'].pop('gt_electric_kW')
    report=implementation().summarize_run(fixture_run(tmp_path/'missing',[[first],[segment(60.,120.)]]))
    assert report['year']['GT_3']['first_positive_settled_output_source_minute'] is None
    assert report['year']['GT_3']['first_output_status']=='unknown_missing_actual_power'


def test_shared_starter_energy_is_electrical_and_not_allocated_per_gt(tmp_path):
    row=segment(0.,120.);row['action']['gt_start_electric_kW']=10.
    report=implementation().summarize_run(fixture_run(tmp_path,[[row]]))
    assert report['year']['shared_auxiliaries']['GT_starter_electricity_kWh']==20.
    assert report['year']['GT_1']['starter_electricity_kWh'] is None
    assert report['year']['storage']['cold_store']['unit']=='kWh_cold'
    assert report['year']['storage']['ups']['unit']=='kWh_electric'
    assert type(report['year']['GT_3']['start_requested_count']) is int


def test_event_clock_regression_is_rejected_even_with_increasing_ids(tmp_path):
    output=fixture_run(tmp_path,events={0:[event(1,'gt_start_requested',10.,device='GT_1'),
        event(2,'gt_start_process_begun',5.,device='GT_1')]})
    with pytest.raises(ValueError,match='event|Event'):implementation().summarize_run(output)


def test_completed_status_cannot_hide_requested_unobserved_tail(tmp_path):
    output=fixture_run(tmp_path,[[segment(0.,60.)]])
    with pytest.raises(ValueError,match='complete|observed|status'):implementation().summarize_run(output)


def test_unknown_event_before_after_containers_never_capture_history(tmp_path,monkeypatch):
    marker='opaque_historical_token_sentinel'
    output=fixture_run(tmp_path,events={0:[event(1,'unrelated_native_diagnostic',0.,
        before={'claimed_actual_tokens':[marker*40_000]},after={'claimed_actual_tokens':[marker*40_000]})]})
    original=json.loads
    def selected_only(value,*args,**kwargs):
        assert marker not in value
        return original(value,*args,**kwargs)
    monkeypatch.setattr(json,'loads',selected_only)
    assert implementation().summarize_run(output)['year']['DG']['actual_start_attempt_count']==0


def test_dg_attempt_duplicate_new_event_id_cannot_inflate_attempt_count(tmp_path):
    output=fixture_run(tmp_path,events={0:[event(1,'m22_DG_dedicated_DC_start',10.,station={'total_diesel_starts_used':1}),
        event(2,'m22_DG_dedicated_DC_start',10.,station={'total_diesel_starts_used':1})]})
    with pytest.raises(ValueError,match='DG|attempt'):implementation().summarize_run(output)


def test_event_at_analysis_endpoint_is_counted_without_future_segments(tmp_path):
    output=fixture_run(tmp_path,events={0:[event(1,'gt_start_completed',30.,device='GT_1',actual_kW=0.)]})
    report=implementation().summarize_run(output,end_minute=30.)
    assert report['year']['GT_1']['start_completed_event_count']==1
    assert report['year']['GT_1']['start_completed_positive_count']==0
    assert report['year']['GT_1']['observed_running_hours']==0.


@pytest.mark.parametrize('damage',['boolean_count','unknown_tail','contiguous','complete'])
def test_native_summary_observation_and_integer_types_are_not_silently_accepted(tmp_path,damage):
    output=fixture_run(tmp_path,[[segment(0.,120.)]])
    path=output/'evidence/SUMMARY.json';summary=json.loads(path.read_text())
    if damage=='boolean_count':summary['block_count']=True
    elif damage=='unknown_tail':summary['observation']['unknown_tail_minutes']=99.
    elif damage=='contiguous':summary['observation']['contiguous']=False
    else:summary['observation']['complete']=False
    write(path,summary)
    with pytest.raises(ValueError):implementation().summarize_run(output)


def test_real_time_zero_activation_is_not_confused_with_given_initial_online(tmp_path):
    events=[event(1,'m22_station_initial_state',0.,initial_GT_online=[False,False,False]),
        event(2,'gt_actual_started',0.,device='GT_1',before_kW=0.,after_kW=2.)]
    output=fixture_run(tmp_path,[[segment(0.,120.,gt=(2.,0.,0.))]],{0:events})
    report=implementation().summarize_run(output)
    assert report['year']['GT_1']['initial_online'] is False
    assert report['year']['GT_1']['actual_started_event_count']==1
    assert report['year']['GT_1']['zero_to_positive_count']==1


def test_unknown_preceding_output_cannot_turn_cross_month_entry_into_known_zero(tmp_path):
    first=segment(0.,60.,gt=(0.,0.,0.));first.pop('source_end_gt_kW');first['action'].pop('boiler_steam_lbph')
    second=segment(60.,120.,gt=(2.,0.,0.),steam=10.)
    report=implementation().summarize_run(fixture_run(tmp_path,[[first],[second]]))
    feb=report['months'][1]
    assert feb['GT_1']['observed_running_hours']==1.
    assert feb['GT_1']['zero_to_positive_count'] is None
    assert feb['boiler_bank']['positive_steam_entry_count'] is None


def test_initial_flag_scope_and_inclusive_endpoint_do_not_imply_month_interval_coverage(tmp_path):
    events={0:[event(1,'gt_start_completed',60.,device='GT_1',actual_kW=0.)]}
    report=implementation().summarize_run(fixture_run(tmp_path,events=events),end_minute=60.)
    assert report['definitions']['initial_flags_scope']=='given_run_initial_state_repeated_for_context; not_month_start_state'
    assert report['definitions']['event_count_interval']=='[0, analysed_end_minute] inclusive'
    assert report['definitions']['interval_quantities_interval']=='[start, end)'
    feb=report['months'][1]
    assert feb['observation']['observed_duration_minutes']==0.
    assert feb['observation']['has_observed_event_or_state_edge_points'] is True
    assert feb['GT_1']['start_completed_event_count']==1
    assert feb['GT_3']['observed_running_hours'] is None
    assert feb['storage']['ups']['charged_input_kWh'] is None


@pytest.mark.parametrize('changed',['operation_summary.py','engineering_summary.py'])
def test_actual_observer_source_byte_drift_is_rejected_without_touching_producer_sources(tmp_path,monkeypatch,changed):
    output=fixture_run(tmp_path/'producer_fixture')
    api=implementation();copies=tmp_path/'observer_copies';copies.mkdir()
    copied={name:copies/name for name in api._OBSERVER_PATHS}
    for name,path in api._OBSERVER_PATHS.items():copied[name].write_bytes(path.read_bytes())
    monkeypatch.setattr(api,'_OBSERVER_PATHS',copied)
    original=api._read;mutated=False
    def change_real_bytes(*args,**kwargs):
        nonlocal mutated
        value=original(*args,**kwargs)
        if not mutated:
            with copied[changed].open('ab') as stream:stream.write(b'\n# actual source byte drift in E test copy\n')
            mutated=True
        return value
    monkeypatch.setattr(api,'_read',change_real_bytes)
    with pytest.raises(ValueError,match='[Oo]bserver.*source'):api.summarize_run(output)
    assert mutated


def test_observer_and_reused_selector_before_after_bindings_are_independent_of_producer(tmp_path):
    report=implementation().summarize_run(fixture_run(tmp_path))
    provenance=report['provenance']
    before=provenance['observer_source_binding_before'];after=provenance['observer_source_binding_after']
    assert set(before)=={'operation_summary.py','engineering_summary.py'} and before==after
    for value in before.values():
        assert Path(value['path']).is_absolute()
        assert hashlib.sha256(Path(value['path']).read_bytes()).hexdigest()==value['sha256']
    assert provenance['observer_source_binding_is_producer_identity'] is False
