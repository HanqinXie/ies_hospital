"""Rows externalization changes logging only, and restores complete evidence."""
import copy
import importlib.util
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'src')]
def api():
    assert importlib.util.find_spec('tools.model_correctness_v23.evidence') is not None, 'M23 lossless domain evidence module missing'
    return __import__('tools.model_correctness_v23.evidence',fromlist=['DomainEvidenceStore'])

def layers():
    row=dict(name='physical.electric',activity=10.,lower=10.,upper=10.,violation=0.)
    return [dict(rank=0,status=0,attempts=[dict(status=0,accepted=True,validation={'rows':[row]*100,'max_original_violation':0.})])]

def test_complete_success_rows_are_external_once_and_restore_exactly(tmp_path):
    m=api();store=m.DomainEvidenceStore(tmp_path/'domain_evidence');value=layers();before=copy.deepcopy(value)
    with m.evidence_scope(store):
        first=m.successful_backend_evidence(value);second=m.successful_backend_evidence(value)
    assert value==before and first==second
    assert 'rows' not in first[0]['attempts'][0]['validation']
    assert store.receipt()['closed_blobs']==1
    assert m.restore_domain_evidence(first,store.directory)==before

def test_failure_and_rejected_call_rows_stay_complete_inline(tmp_path):
    m=api();value=layers();value[0]['status']=2
    store=m.DomainEvidenceStore(tmp_path/'domain_evidence')
    with m.evidence_scope(store):result=m.successful_backend_evidence(value)
    assert result==value and store.receipt()['closed_blobs']==0

