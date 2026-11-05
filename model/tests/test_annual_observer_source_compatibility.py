"""Read-only real old producers plus pure declared-profile negative cases."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
BASE=Path(r'E:\hospital_ies_annual_program_20261030_run01')
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
sys.path.insert(0,str(ROOT/'tests'))
from tools.annual_program import steam,flow_source_contracts as contracts,witness
from test_annual_program_carrier_flow import carrier
from test_annual_program_steam import annual,legacy_case
from reviewed_source_fixtures import reviewed_source_bytes


@pytest.fixture(scope='module',params=['pilot14_epoch09_indexed_split','pilot16_epoch09_pending30_split'])
def old_actual_block(request):
    directory=BASE/'runs'/request.param
    context=steam.prepare_steam_context(directory,source_root=Path(r'D:\hospital_ies_annual_runtime_epoch09_20261030'))
    record=context['records']['blocks/block_000000.json.gz']
    block=steam.native._read(directory/'evidence'/record['file'],zipped=True)
    assert context['source_hashes']['tools/annual_program/witness.py']==contracts.LEGACY_RECORDER_SHA256
    assert all(segment[witness.FIELD]['recorder_source_sha256']==contracts.LEGACY_RECORDER_SHA256 for segment in block['segments'])
    return context,block,record


def test_old_registered_receipt_survives_a_different_current_observer_recorder(old_actual_block):
    context,block,record=old_actual_block
    assert steam.native._digest(witness.__file__)!=contracts.LEGACY_RECORDER_SHA256
    result=steam.audit_steam_block(context,block,block_record=record,use_actual_witness=True,steam_flow_only=True)
    assert result['status']=='passed',result['summary']
    assert result['summary']['passed_segments']==len(block['segments'])
    assert result['summary']['unknown_segments']==result['summary']['failed_segments']==0
    assert all(segment['binding']['alternative_candidates_used_to_select_primal'] is False for segment in result['segments'])


def test_unregistered_source_stays_disabled_after_explicit_release_registration():
    # The old reviewed capture source remains exact; current science belongs to
    # a separate profile. Preserve these old assertions using parse-only bytes.
    assert contracts.CAPTURE_NATIVE_SOURCE_CONTRACT['tools/operating_response/physical_balance.py']==hashlib.sha256(reviewed_source_bytes('tools/operating_response/physical_balance.py')).hexdigest()
    assert contracts.CAPTURE_RECORDER_SHA256==hashlib.sha256(reviewed_source_bytes('tools/annual_program/witness.py')).hexdigest()
    observed=dict(contracts.CAPTURE_NATIVE_SOURCE_CONTRACT)
    assert contracts.classify_recorder_source_pair(contracts.LEGACY_RECORDER_SHA256,observed)['supported'] is False
    observed['tools/operating_response/physical_balance.py']='0'*64
    assert contracts.match_native_source_profile(observed) is None
    assert contracts.classify_recorder_source_pair(steam.native._digest(witness.__file__),observed)['supported'] is False


def test_basic_new_recorder_old_leaf_does_not_treat_explicit_absence_as_future_schema(carrier):
    from tools.annual_program import carrier_flow
    old,_=carrier()
    report=carrier_flow.audit_annual_block(old.producer,source_root=old.source)
    assert report['status']=='passed'
    assert all(report['segments'][0]['raw_flow'][key]['status']=='passed' for key in ('e','c'))
    assert report['whole_system_validated'] is False
