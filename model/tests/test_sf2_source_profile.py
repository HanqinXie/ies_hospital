"""SCI_FUEL source/ABI admission, not physical or numeric qualification."""
from pathlib import Path
import hashlib
import pytest
from tools.annual_program import flow_source_contracts as contracts,witness

ROOT=Path(__file__).resolve().parents[1]
PROFILE='single_fault_pnnl_exact_fuel_capture_v1'

def current_native():
    from tools.annual_program import flow_source_contracts as c
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in c.LEGACY_NATIVE_SOURCE_CONTRACT}

def test_current_fuel_source_has_separate_literal_profile():
    from tools.annual_program import flow_source_contracts as c
    assert getattr(c,'SCI_FUEL_CAPTURE_PROFILE',None)==PROFILE
    observed=current_native()
    assert c.SCI_FUEL_NATIVE_SOURCE_CONTRACT==observed
    assert c.match_native_source_profile(observed)==PROFILE
    assert c.recognized_scientific_native_source_contract(observed) is None
    assert c.recognized_capture_native_source_contract(observed) is None
    assert c.recognized_sci_fuel_native_source_contract(observed)==observed

def test_current_fuel_leaf_has_its_own_complete_scientific_ast():
    from tools.annual_program import flow_source_contracts as c
    report=c.inspect_leaf_migration((ROOT/'tools/operating_response/physical_balance.py').read_bytes())
    assert report['shape_valid'] is True
    assert report['runtime_profile_approved'] is True
    assert report['design_equivalent'] is False
    assert report['original_function_ast_equal'] is report['original_module_ast_equal'] is False
    assert report['reviewed_scientific_model_change'] is True
    assert report['source_authentication_performed'] is report['actual_execution_bound'] is False

def test_fuel_recorder_cannot_borrow_any_old_scientific_pair():
    from tools.annual_program import flow_source_contracts as c,witness
    recorder=hashlib.sha256(Path(witness.__file__).read_bytes()).hexdigest()
    assert getattr(c,'SCI_FUEL_CAPTURE_RECORDER_SHA256',None)==recorder
    pair=c.classify_recorder_source_pair(recorder,current_native())
    assert pair['compiled_source_profile_allowed'] is True and pair['source_profile']==PROFILE
    for old in (c.LEGACY_RECORDER_SHA256,c.CAPTURE_RECORDER_SHA256,c.SCIENTIFIC_CAPTURE_RECORDER_SHA256):
        assert c.classify_recorder_source_pair(old,current_native())['supported'] is False
    assert c.classify_recorder_source_pair(recorder,c.SCIENTIFIC_NATIVE_SOURCE_CONTRACT)['supported'] is False

def test_fuel_source_extras_and_origin_bind_actual_new_dependencies():
    from tools.annual_program import flow_source_contracts as c,witness
    resolver=getattr(c,'sci_fuel_capture_source_extra_contract',None)
    assert callable(resolver)
    native=current_native();extra=resolver(native)
    assert {'src/hospital_ies/nonlinear.py','src/hospital_ies/model.py','configs/plant_configs.json',
        'tools/sci_engineering/planner.py','tools/single_fault_fuel_20261102/fuel.py',
        'tools/single_fault_fuel_20261102/runner.py','tools/single_fault_fuel_20261102/nl_store.py'}<=set(extra)
    assert extra=={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in extra}
    frozen=dict(native,**c.CAPTURE_AUXILIARY_SOURCE_CONTRACT,**extra)
    assert witness.compiled_origin(ROOT,frozen)['source_files']==frozen
    for name in extra:
        broken=dict(frozen);broken[name]='0'*64
        with pytest.raises(ValueError):witness.compiled_origin(ROOT,broken)

def test_old_formula_reader_rejects_current_fuel_science():
    from tools.annual_program import flow_source_contracts as c,steam
    native=current_native();resolver=getattr(c,'sci_fuel_capture_source_extra_contract',None)
    assert callable(resolver)
    frozen=dict(native,**c.CAPTURE_AUXILIARY_SOURCE_CONTRACT,**resolver(native))
    with pytest.raises(steam.native._Issue) as error:
        steam._physical_formula_profile(dict(source_root=ROOT,source_hashes=frozen))
    assert error.value.status=='unknown'


EXTRA_FILES=frozenset(contracts.SCI_FUEL_CAPTURE_EXTRA_SOURCE_CONTRACT)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def current_freeze():
    return {name:sha(ROOT/name) for name in (*contracts.LEGACY_NATIVE_SOURCE_CONTRACT,*contracts.CAPTURE_AUXILIARY_SOURCE_CONTRACT,*EXTRA_FILES)}

def test_loaded_current_leaf_cannot_be_replaced_under_correct_source_bindings(monkeypatch):
    from tools.operating_response import physical_balance
    monkeypatch.setattr(physical_balance,'solve_physical',lambda **kwargs:None)
    with pytest.raises(ValueError,match='loaded leaf differs'):
        witness.compiled_origin(ROOT,current_freeze())


def test_loaded_capture_alias_cannot_change_under_correct_source_bindings(monkeypatch):
    from tools.operating_response import physical_balance
    monkeypatch.setattr(physical_balance,'_annual_capture_enabled',lambda:True)
    with pytest.raises(ValueError,match='helper aliases changed'):
        witness.compiled_origin(ROOT,current_freeze())


def test_scientific_source_gate_checks_actual_extra_digest_not_only_declaration(monkeypatch):
    target=(ROOT/'tools/single_fault_model_20261102/storage.py').resolve()
    original=witness._sha
    # Do not mutate the frozen scientific files to emulate a damaged extra.
    monkeypatch.setattr(witness,'_COMPILED_SOURCE_CACHE',{})
    monkeypatch.setattr(witness,'_sha',lambda path:'0'*64 if Path(path).resolve()==target else original(path))
    with pytest.raises(ValueError,match='source bytes changed: tools/single_fault_model_20261102/storage.py'):
        witness.compiled_origin(ROOT,current_freeze())


@pytest.mark.parametrize('relative',sorted(EXTRA_FILES))
def test_scientific_origin_rejects_missing_or_wrong_extra_freeze(relative):
    frozen=current_freeze();frozen[relative]='0'*64
    with pytest.raises(ValueError,match='frozen source bindings'):
        witness.compiled_origin(ROOT,frozen)


def test_new_scientific_bytes_never_gain_legacy_reader_formula_qualification():
    from tools.annual_program import steam
    context=dict(source_root=ROOT,source_hashes=current_freeze())
    with pytest.raises(steam.native._Issue) as error:steam._physical_formula_profile(context)
    assert error.value.status=='unknown'
    assert error.value.reason=='unsupported_annual_physical_source_profile'



@pytest.mark.parametrize('warm',[False,True])
@pytest.mark.parametrize('change',['code','defaults','kwdefaults'])
def test_fuel_loaded_owned_function_cannot_borrow_pinned_source(monkeypatch,warm,change):
    from tools.single_fault_fuel_20261102.fuel import FuelCurve
    if warm:witness.compiled_origin(ROOT,current_freeze())
    else:witness._COMPILED_SOURCE_CACHE.clear()
    fn=FuelCurve.rate
    def replacement(self,power_kW,temperature_C):return 0.
    if change=='code':monkeypatch.setattr(fn,'__code__',replacement.__code__)
    elif change=='defaults':monkeypatch.setattr(fn,'__defaults__',(0.,))
    else:monkeypatch.setattr(fn,'__kwdefaults__',{'temperature_C':0.})
    with pytest.raises(ValueError,match='SCI_FUEL.*loaded'):
        witness.compiled_origin(ROOT,current_freeze())


def test_fuel_recorder_exact_legacy_pair_remains_basic_only():
    recorder=sha(witness.__file__)
    pair=contracts.classify_recorder_source_pair(recorder,contracts.LEGACY_NATIVE_SOURCE_CONTRACT)
    assert pair['source_profile']==contracts.LEGACY_PROFILE
    assert pair['supported'] is pair['basic_witness_allowed'] is True
    assert pair['compiled_source_profile_allowed'] is False
    for old in (contracts.CAPTURE_NATIVE_SOURCE_CONTRACT,contracts.SCIENTIFIC_NATIVE_SOURCE_CONTRACT):
        assert contracts.classify_recorder_source_pair(recorder,old)['supported'] is False


@pytest.mark.parametrize('old_bits',list(__import__('itertools').product((False,True),repeat=3)))
def test_fuel_native_mixed_epochs_never_borrow_current_recorder(old_bits):
    observed=current_native()
    changed=('src/hospital_ies/solver_backend.py','src/hospital_ies/solver_diagnostics.py','tools/operating_response/physical_balance.py')
    for restore,name in zip(old_bits,changed):
        if restore:observed[name]=contracts.SCIENTIFIC_NATIVE_SOURCE_CONTRACT[name]
    pair=contracts.classify_recorder_source_pair(sha(witness.__file__),observed)
    if not any(old_bits):
        assert pair['source_profile']==PROFILE and pair['compiled_source_profile_allowed'] is True
    else:
        assert pair['source_profile']==(contracts.SCIENTIFIC_CAPTURE_PROFILE if all(old_bits) else None)
        assert pair['supported'] is pair['compiled_source_profile_allowed'] is False


@pytest.mark.parametrize('field',['SCI_FUEL_CAPTURE_EXTRA_SOURCE_CONTRACT','SCI_FUEL_PHYSICAL_FUNCTION_AST_SHA256','SCI_FUEL_PHYSICAL_MODULE_AST_SHA256','SCI_FUEL_CAPTURE_LOGGING_EXPRESSION_AST_SHA256','SCI_FUEL_CAPTURE_RECORDER_SHA256'])
def test_incomplete_fuel_literals_cannot_grant_current_capture(monkeypatch,field):
    monkeypatch.setattr(contracts,field,None)
    assert contracts.classify_recorder_source_pair(sha(witness.__file__),current_native())['compiled_source_profile_allowed'] is False


@pytest.mark.parametrize('name',['_execute_loop','_native_execute_attempt'])
@pytest.mark.parametrize('warm',[False,True])
@pytest.mark.parametrize('change',['code','name','file','module','defaults','kwdefaults','injected_global','referenced_global','globals_file','globals_name','builtins','shadow_builtin','native_code'])
def test_generated_runner_function_requires_reviewed_native_owner(monkeypatch,name,warm,change):
    from tools.single_fault_fuel_20261102 import runner
    if warm:witness.compiled_origin(ROOT,current_freeze())
    else:witness._COMPILED_SOURCE_CACHE.clear()
    fn=getattr(runner,name)
    def unreviewed(*args,**kwargs):return {'status':'completed','unreviewed_generated_callable':True}
    if change=='code':monkeypatch.setattr(fn,'__code__',unreviewed.__code__)
    elif change=='name':monkeypatch.setattr(fn,'__name__','unreviewed')
    elif change=='file':monkeypatch.setattr(fn,'__code__',fn.__code__.replace(co_filename=str(ROOT/'unreviewed.py')))
    elif change=='module':monkeypatch.setattr(fn,'__module__','unreviewed')
    elif change=='defaults':monkeypatch.setattr(fn,'__defaults__',(False,))
    elif change=='kwdefaults':monkeypatch.setattr(fn,'__kwdefaults__',None)
    elif change=='native_code':
        native=getattr(runner.native,'_execute_loop' if name=='_execute_loop' else '_execute_attempt')
        monkeypatch.setattr(native,'__code__',unreviewed.__code__)
    else:
        namespace=fn.__globals__
        if change=='injected_global':key='_snapshot' if name=='_execute_loop' else '_execute_loop';value=unreviewed
        elif change=='referenced_global':key='normalize_json' if name=='_execute_loop' else '_read';value=unreviewed
        elif change=='globals_file':key='__file__';value=str(ROOT/'unreviewed.py')
        elif change=='globals_name':key='__name__';value='unreviewed'
        elif change=='builtins':key='__builtins__';value={}
        else:key='str';value=unreviewed
        monkeypatch.setitem(namespace,key,value)
    # The altered callable is never executed; this is source/owner protocol.
    with pytest.raises(ValueError,match='loaded|helper|runtime|runner'):
        witness.compiled_origin(ROOT,current_freeze())


@pytest.mark.parametrize('warm',[False,True])
def test_generated_guard_ignores_unreferenced_globals_and_attribute_names(monkeypatch,warm):
    from tools.single_fault_fuel_20261102 import runner
    if warm:witness.compiled_origin(ROOT,current_freeze())
    else:witness._COMPILED_SOURCE_CACHE.clear()
    for name in ('_execute_loop','_native_execute_attempt'):
        # deepcopy occurs as an attribute of imported copy, not LOAD_GLOBAL.
        monkeypatch.setitem(getattr(runner,name).__globals__,'deepcopy',object())
        monkeypatch.setitem(getattr(runner,name).__globals__,'_unused_quality_note',object())
    assert witness.compiled_origin(ROOT,current_freeze())['source_files']==current_freeze()


@pytest.mark.parametrize('name',['_execute_loop','_native_execute_attempt'])
@pytest.mark.parametrize('warm',[False,True])
def test_generated_callable_cannot_hide_captured_foreign_builtins(monkeypatch,name,warm):
    from types import FunctionType
    from tools.single_fault_fuel_20261102 import runner
    if warm:witness.compiled_origin(ROOT,current_freeze())
    else:witness._COMPILED_SOURCE_CACHE.clear()
    original=getattr(runner,name);namespace=dict(original.__globals__)
    namespace['__builtins__']={'str':lambda *a:'unreviewed_builtin'}
    foreign=FunctionType(original.__code__,namespace,original.__name__,original.__defaults__,original.__closure__)
    foreign.__kwdefaults__=original.__kwdefaults__
    namespace['__builtins__']=runner.native.__dict__['__builtins__']
    assert foreign.__builtins__ is not original.__builtins__
    assert namespace['__builtins__'] is runner.native.__dict__['__builtins__']
    monkeypatch.setattr(runner,name,foreign)
    if name=='_execute_loop':monkeypatch.setitem(runner._native_execute_attempt.__globals__,'_execute_loop',foreign)
    # No execution of the foreign callable or its captured builtin.
    with pytest.raises(ValueError,match='loaded|helper|runtime|runner'):
        witness.compiled_origin(ROOT,current_freeze())


@pytest.mark.parametrize('warm',[False,True])
def test_generated_callable_accepts_original_captured_builtins_and_empty_closure(warm):
    from tools.single_fault_fuel_20261102 import runner
    if warm:witness.compiled_origin(ROOT,current_freeze())
    else:witness._COMPILED_SOURCE_CACHE.clear()
    for name,native_name in (('_execute_loop','_execute_loop'),('_native_execute_attempt','_execute_attempt')):
        fn=getattr(runner,name);original=getattr(runner.native,native_name)
        assert fn.__builtins__ is original.__builtins__ is runner.native.__dict__['__builtins__']
        assert fn.__closure__ is original.__closure__ is None
    assert witness.compiled_origin(ROOT,current_freeze())['source_files']==current_freeze()
