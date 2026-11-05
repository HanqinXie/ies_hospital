"""Independent loaded-code negative probes; no actual physical model or LP."""
import hashlib
from pathlib import Path
import sys
from types import FunctionType
from functools import update_wrapper
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from test_annual_compiled_witness import captured,actual,legacy_case

def ready():
 from tools.annual_program import witness,flow_source_contracts as c
 from tools.operating_response import physical_balance
 pins={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
       for name in (*c.LEGACY_NATIVE_SOURCE_CONTRACT,*c.CAPTURE_AUXILIARY_SOURCE_CONTRACT)}
 # These adversarial probes target the actually loaded current leaf. Its
 # independent SF1 profile requires its complete scientific dependency closure.
 native={name:pins[name] for name in c.LEGACY_NATIVE_SOURCE_CONTRACT}
 additional=c.sci_fuel_capture_source_extra_contract(native)
 assert additional is not None
 pins.update({name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in additional})
 assert c.match_native_source_profile(native)==c.SCI_FUEL_CAPTURE_PROFILE
 witness._COMPILED_SOURCE_CACHE.clear()
 witness.compiled_origin(ROOT,pins)
 return witness,physical_balance,pins

def test_historical_scientific_capture_declaration_keeps_original_assertions(tmp_path):
 from tools.annual_program import flow_source_contracts as c
 from reviewed_sci1_source_fixtures import _fixture,reviewed_source_bytes
 from types import SimpleNamespace
 historical=tmp_path/'parse_only'
 for name in _fixture()['files']:
  path=historical/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(reviewed_source_bytes(name))
 pins={name:hashlib.sha256((historical/name).read_bytes()).hexdigest() for name in (*c.SCIENTIFIC_NATIVE_SOURCE_CONTRACT,*c.CAPTURE_AUXILIARY_SOURCE_CONTRACT,*c.SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT)}
 origin={'source_files':pins}
 leaf_module=SimpleNamespace(__file__=historical/'tools/operating_response/physical_balance.py')
 assert origin['source_files']==pins
 assert len(origin['source_files'])==27
 report=c.inspect_leaf_migration(Path(leaf_module.__file__).read_bytes())
 assert report['design_equivalent'] is False
 assert report['reviewed_scientific_model_change'] is report['runtime_profile_approved'] is True

@pytest.mark.parametrize('warm',[False,True])
@pytest.mark.parametrize('change',['module_file','function_name'])
def test_loaded_leaf_metadata_cannot_bypass_source_gate(monkeypatch,warm,change):
 witness,leaf_module,pins=ready()
 if not warm:witness._COMPILED_SOURCE_CACHE.clear()
 if change=='module_file':monkeypatch.setattr(leaf_module,'__file__',str(ROOT/'not_the_compiler.py'))
 else:monkeypatch.setattr(leaf_module.solve_physical,'__name__','not_the_compiler')
 try:
  with pytest.raises(ValueError,match='loaded leaf'):
   witness.compiled_origin(ROOT,pins)
 finally:witness._COMPILED_SOURCE_CACHE.clear()

def test_same_leaf_code_cannot_use_foreign_runtime_globals(monkeypatch):
 witness,leaf_module,pins=ready();witness._COMPILED_SOURCE_CACHE.clear()
 original=leaf_module.solve_physical
 namespace=dict(original.__globals__);namespace['__file__']=str(ROOT/'not_the_loaded_compiler.py')
 namespace['_annual_flow_input_fields']=lambda **kwargs:{'_compiled_flow_input':{'foreign':True}}
 substitute=FunctionType(original.__code__,namespace,'solve_physical',original.__defaults__,original.__closure__)
 monkeypatch.setattr(leaf_module,'solve_physical',substitute)
 try:
  with pytest.raises(ValueError,match='loaded leaf'):
   witness.compiled_origin(ROOT,pins)
 finally:witness._COMPILED_SOURCE_CACHE.clear()

def test_unapproved_loaded_hook_cannot_claim_pinned_actual_hook(monkeypatch,captured):
 from tools.annual_program import witness
 from tools.annual_program.source_runtime import AnnualSourceMixin
 original=AnnualSourceMixin._after_actual_segment
 namespace={'recorder':witness.record_actual_witness}
 exec(compile('def _after_actual_segment(self,segment,result):\n    return recorder(self,segment,result)\n',
              original.__code__.co_filename,'exec'),namespace)
 substitute=namespace['_after_actual_segment']
 monkeypatch.setattr(AnnualSourceMixin,'_after_actual_segment',substitute)
 with pytest.raises(ValueError,match='hook'):
  captured.core._after_actual_segment(captured.segment,captured.result)
 assert witness.FIELD not in captured.segment


@pytest.mark.parametrize('name',['_plain','_source','_index_map','build_compiled_flow_input'])
def test_loaded_capture_aliases_cannot_borrow_a_warm_source_receipt(monkeypatch,name):
 from tools.annual_program import compiled_capture
 witness,leaf_module,pins=ready()
 monkeypatch.setattr(compiled_capture,name,lambda *a,**k:None)
 try:
  with pytest.raises(ValueError,match='helper aliases'):
   witness.compiled_origin(ROOT,pins)
 finally:witness._COMPILED_SOURCE_CACHE.clear()


@pytest.mark.parametrize('warm',[False,True])
def test_capture_callable_cannot_borrow_code_via_wrapped_metadata(monkeypatch,warm):
 from tools.annual_program import compiled_capture
 witness,leaf_module,pins=ready()
 if not warm:witness._COMPILED_SOURCE_CACHE.clear()
 original=compiled_capture.flow_input_fields
 namespace={}
 exec('def replacement(**kwargs):\n    return {"foreign_capture_marker": True}\n',namespace)
 wrapper=FunctionType(namespace['replacement'].__code__,compiled_capture.__dict__,'flow_input_fields')
 update_wrapper(wrapper,original)
 monkeypatch.setattr(compiled_capture,'flow_input_fields',wrapper)
 monkeypatch.setattr(leaf_module,'_annual_flow_input_fields',wrapper)
 assert leaf_module._annual_flow_input_fields()=={'foreign_capture_marker':True}
 try:
  with pytest.raises(ValueError,match='helper'):witness.compiled_origin(ROOT,pins)
 finally:witness._COMPILED_SOURCE_CACHE.clear()


def test_current_loaded_fuel_capture_has_independent_scientific_identity():
 from tools.annual_program import flow_source_contracts as c
 witness,leaf,pins=ready()
 assert witness.compiled_origin(ROOT,pins)['source_files']==pins
 assert len(pins)==43
 assert witness.native_source_binding(ROOT)['source_profile']==c.SCI_FUEL_CAPTURE_PROFILE


def test_historical_ready_assertions_keep_exact_scientific_identity():
 from tools.annual_program import flow_source_contracts as c
 from reviewed_sci1_source_fixtures import reviewed_source_bytes
 native={name:hashlib.sha256(reviewed_source_bytes(name)).hexdigest() for name in c.LEGACY_NATIVE_SOURCE_CONTRACT}
 additional=c.scientific_capture_source_extra_contract(native)
 assert additional is not None
 assert c.match_native_source_profile(native)==c.SCIENTIFIC_CAPTURE_PROFILE
