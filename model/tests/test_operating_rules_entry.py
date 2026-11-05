"""Public study entry validates before creating any actual attempt."""
import importlib, json, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

def api():
    try:return importlib.import_module('tools.operating_rules_study.validation')
    except ModuleNotFoundError as exc:pytest.fail('Named study entry is missing: '+str(exc))

def test_registered_case_can_be_preflighted_without_solving():
    from tools.operating_rules_study.registration import load_registry,make_case
    r=load_registry();c=make_case(r,'b07_L4_GT_ER_disturbed_M','M_both',mode='FD',clock_contract='event_triggered')
    out=api().preflight(c,r)
    assert out['operating_rules_study']['method']=='M_both'
    assert out['policy']['config']['time_energy_preparation'] is False

def test_missing_registration_rejected_before_output_created(tmp_path):
    path=tmp_path/'bad.json';path.write_text('{}',encoding='utf8');out=tmp_path/'not_created'
    with pytest.raises((ValueError,KeyError)):
        api().main(['--case-file',str(path),'--output',str(out)])
    assert not out.exists()
