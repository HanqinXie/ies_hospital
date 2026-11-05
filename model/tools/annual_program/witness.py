"""Output-only final actual-result receipts for exact reviewed source profiles.

The caller must pass the original final ``sol`` from the actual-interval hook.
No solver, snapshot, current-context query, action-based primal selection or
balance-remainder meter is used. Original successful-row logging owns the new
call's exact validation rows. Existing historical results are never modified.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import inspect
import ast
import dis
import importlib
import builtins
from types import CodeType, FunctionType
from contextlib import contextmanager

from tools.sci_engineering.solver_log import SolverCallLog, normalize_json
from hospital_ies.state_transition import EPS as _TIME_EPS_MINUTES
from . import flow_source_contracts as contracts
from .compiled_flow_input import SOURCE_SCHEMA, MINUTE1_SOURCE_SCHEMA, MINUTE_THERMAL_SOURCE_SCHEMA, validate_compiled_flow_input

ROOT=Path(__file__).resolve().parents[2]
SCHEMA='hospital_ies.annual_program.final_actual_primal_witness.v1'
FIELD='annual_program_actual_witness'
PHASE='annual_program_actual_witness'
NATIVE_SOURCE_CONTRACT={
    'src/hospital_ies/solver_backend.py':'14d3aa2a179cdf00c9e6e43d224bde31f1ed832abeb05c692516029a6406b4d7',
    'tools/model_closeout/numerics.py':'822bf480936cd184b35033eaa631e3b0a244590ef346148432ce409b1be4d3d5',
    'tools/operating_response/physical_balance.py':'fe13fe96a58c0f8816a38419074e4a5c12b65e0207b0d37e85a2d8abf82a76f5',
    'tools/model_correctness_v23/domains.py':'2eda2c44317c563db1e20e8855f4f00437a542664639e9777167bfdf3aa496d8',
    'tools/essential_fixes/execution.py':'0d1bb1161810a49275b03fbd9c8951bb64727ef9b0f12d7658655d3a615d24f4',
    'tools/model_correction_v25/reserve.py':'6754608e317f94fae2f40516dbca033c6be869a01edd8cd257ac1753179f3dba',
    'src/hospital_ies/solver_diagnostics.py':'fcc3bdad7ace8b880f72279eaf4a551ef812977839555435f7e7227ef5584419',
    'tools/model_correctness_v23/numerics.py':'d49eeac8bf099bf50466bbdf6c967f4505d3ea5820b4802209c8d97546933d76',
    'tools/execution_recovery/numerics.py':'3cd9b6b7757866154f7b80c0240803d942c30affbfa7c4792b6bbaaea47641e2',
    'tools/engineering_model/environment.py':'cf0d465bda948159eeffb7a451ddeea701602def597b8f79f11a7473a2a5bdab',
    'tools/model_closure_v22/environment.py':'3b4d6a3cbe59b9bdffb3db039cc12cb8c361f016fecd93c2512199da5fda03ea',
    'tools/model_correctness_v23/environment.py':'6db840b548680f1ba942c23d4ed825d67a52afa7b39bc6ff2894e29af5de0d30',
    'tools/engineering_completion_v24/environment.py':'a45426db0f1c9a205e438a50d209ea6c702ce139a8147b2d787c7fb5dd2916d7',
    'tools/engineering_completion_v24/cold_modes.py':'6add409d192ec8cc6f3b10b043c72055075a138548f6efe30682d5d10b89c34c'}
_SOURCE_CACHE={}
_COMPILED_SOURCE_CACHE={}
_HOOK_CODE_CACHE={}
_FUEL_CODE_CACHE={}
_FUEL_LOADED_MODULES={
    'hospital_ies.model':'src/hospital_ies/model.py',
    'hospital_ies.nonlinear':'src/hospital_ies/nonlinear.py',
    'hospital_ies.solver_backend':'src/hospital_ies/solver_backend.py',
    'hospital_ies.solver_result_cache':'src/hospital_ies/solver_result_cache.py',
    'tools.sci_engineering.planner':'tools/sci_engineering/planner.py',
    'tools.single_fault_model_20261102.storage':'tools/single_fault_model_20261102/storage.py',
    'tools.single_fault_model_20261102.runtime':'tools/single_fault_model_20261102/runtime.py',
    'tools.single_fault_model_20261102.environment':'tools/single_fault_model_20261102/environment.py',
    'tools.single_fault_fuel_20261102.fuel':'tools/single_fault_fuel_20261102/fuel.py',
    'tools.single_fault_fuel_20261102.leaf':'tools/single_fault_fuel_20261102/leaf.py',
    'tools.single_fault_fuel_20261102.runtime':'tools/single_fault_fuel_20261102/runtime.py',
    'tools.single_fault_fuel_20261102.environment':'tools/single_fault_fuel_20261102/environment.py',
    'tools.single_fault_fuel_20261102.nl_store':'tools/single_fault_fuel_20261102/nl_store.py',
    'tools.single_fault_fuel_20261102.runner':'tools/single_fault_fuel_20261102/runner.py',
    'tools.annual_program.source_runtime':'tools/annual_program/source_runtime.py'}
_MINUTE1_LOADED_MODULES={
    'hospital_ies.control_env':'src/hospital_ies/control_env.py',
    'hospital_ies.state_transition':'src/hospital_ies/state_transition.py',
    'hospital_ies.data_contract':'src/hospital_ies/data_contract.py',
    'tools.sci_engineering.environment':'tools/sci_engineering/environment.py',
    'tools.sci_engineering.service':'tools/sci_engineering/service.py',
    'tools.operating_response.execution':'tools/operating_response/execution.py',
    'tools.model_review_v28.demand':'tools/model_review_v28/demand.py',
    'tools.minute1_20261103.profile':'tools/minute1_20261103/profile.py',
    'tools.minute1_20261103.source':'tools/minute1_20261103/source.py',
    'tools.minute1_20261103.registration':'tools/minute1_20261103/registration.py',
    'tools.minute1_20261103.runtime':'tools/minute1_20261103/runtime.py',
    'tools.minute1_20261103.environment':'tools/minute1_20261103/environment.py',
    'tools.minute1_20261103.runner':'tools/minute1_20261103/runner.py'}
_MINUTE_THERMAL_LOADED_MODULES=dict(_MINUTE1_LOADED_MODULES,**{
    'tools.essential_fixes.execution':'tools/essential_fixes/execution.py'})
ACTUAL_HOOK_SOURCE_CONTRACT={'tools/annual_program/source_runtime.py':
    '992d80719dd0728b047b30e847ac6ab86225035a4712c4658efc90fd831e61c2'}
_CONTEXT_WRAPPER_CODE=contextmanager(lambda:None).__code__


def _loaded_capture_function(module,name,value):
    # Only this exact stdlib contextmanager wrapper is part of the release.
    # Other helpers execute their direct code; arbitrary __wrapped__ metadata
    # must never substitute the function the leaf will actually call.
    if not isinstance(value,FunctionType):raise ValueError('Compiled capture loaded helper is not a function')
    if module.__name__=='tools.annual_program.compiled_capture' and name=='capture_scope':
        closure=value.__closure__
        if (value.__code__ is not _CONTEXT_WRAPPER_CODE or value.__globals__ is not contextmanager.__globals__ or
                closure is None or len(closure)!=1 or not isinstance(closure[0].cell_contents,FunctionType) or
                getattr(value,'__wrapped__',None) is not closure[0].cell_contents):
            raise ValueError('Compiled capture loaded helper contextmanager wrapper changed')
        return closure[0].cell_contents
    return value


def _fuel_loaded_functions(root,expected,*,modules=None,generated_runner=True):
    """Verify actual owned functions against pinned source, including classes.

    Source-defined nested bodies are included in their parent code object.
    Generated dataclass methods remain guarded by the SF2 runtime's frozen
    descriptors/code/defaults, rather than being passed off as source functions.
    """
    found=[]
    selected=_FUEL_LOADED_MODULES if modules is None else modules
    for module_name,relative in selected.items():
        module=importlib.import_module(module_name);path=root/relative
        if Path(module.__file__).resolve()!=path:raise ValueError('Compiled SCI_FUEL loaded helper path changed: '+relative)
        key=(str(path),expected[relative])
        if key not in _FUEL_CODE_CACHE:
            raw=path.read_bytes();tree=ast.parse(raw);code=compile(raw,str(path),'exec',dont_inherit=True)
            _FUEL_CODE_CACHE[key]=(tree,code)
        tree,code=_FUEL_CODE_CACHE[key]
        def check(container,nodes,parent_code,prefix=''):
            used={}
            for node in nodes:
                if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):continue
                occurrence=used.get(node.name,0);used[node.name]=occurrence+1
                codes=[value for value in parent_code.co_consts if isinstance(value,CodeType) and value.co_name==node.name]
                if occurrence>=len(codes):raise ValueError('Compiled SCI_FUEL source code shape changed')
                wanted=codes[occurrence];value=vars(container).get(node.name);qualified=prefix+node.name
                if isinstance(node,ast.ClassDef):
                    if not isinstance(value,type) or value.__module__!=module_name:
                        raise ValueError('Compiled SCI_FUEL loaded helper class changed: '+qualified)
                    check(value,node.body,wanted,qualified+'.');continue
                if isinstance(value,(classmethod,staticmethod)):value=value.__func__
                elif isinstance(value,property):
                    decorator=next((d.attr for d in node.decorator_list if isinstance(d,ast.Attribute) and d.attr in ('setter','deleter')),None)
                    value=value.fset if decorator=='setter' else value.fdel if decorator=='deleter' else value.fget
                if isinstance(value,FunctionType) and value.__code__ is _CONTEXT_WRAPPER_CODE:
                    closure=value.__closure__
                    if (value.__globals__ is not contextmanager.__globals__ or closure is None or len(closure)!=1
                            or not isinstance(closure[0].cell_contents,FunctionType)
                            or getattr(value,'__wrapped__',None) is not closure[0].cell_contents):
                        raise ValueError('Compiled SCI_FUEL loaded context helper changed: '+qualified)
                    value=closure[0].cell_contents
                if (not isinstance(value,FunctionType) or value.__globals__ is not module.__dict__
                        or value.__module__!=module_name or value.__name__!=node.name
                        or Path(value.__code__.co_filename).resolve()!=path or value.__code__!=wanted):
                    raise ValueError('Compiled SCI_FUEL loaded helper/hook code/globals changed: '+qualified)
                def source_default(expr):
                    if isinstance(expr,ast.Name):return module.__dict__[expr.id]
                    return ast.literal_eval(expr)
                positional=tuple(source_default(expr) for expr in node.args.defaults) or None
                keyword={arg.arg:source_default(expr) for arg,expr in zip(node.args.kwonlyargs,node.args.kw_defaults) if expr is not None} or None
                if (type(value.__defaults__) is not type(positional) or value.__defaults__!=positional
                        or type(value.__kwdefaults__) is not type(keyword) or value.__kwdefaults__!=keyword):
                    raise ValueError('Compiled SCI_FUEL loaded helper defaults changed: '+qualified)
                found.append((relative,qualified,value.__code__))
        check(module,tree.body,code)
    if generated_runner:found.extend(_fuel_generated_runner_functions(root,expected))
    return tuple(found)


def _minute1_loaded_functions(root,expected):
    """Attest direct minute-owned bodies and their actual imported globals.

    The unchanged SF2 generated-runner guards are called separately. Minute
    runner functions belong to their own reviewed source module, so they never
    borrow the old isolated runner function ownership exemption.
    """
    found=_fuel_loaded_functions(root,expected,modules=_MINUTE1_LOADED_MODULES,generated_runner=False)
    for module_name,relative in _MINUTE1_LOADED_MODULES.items():
        module=importlib.import_module(module_name)
        _minute1_module_globals(module,root/relative)
    return found


def _minute_thermal_loaded_functions(root,expected):
    """Attest the new actual Essential loop without changing old owner maps."""
    found=_fuel_loaded_functions(root,expected,modules=_MINUTE_THERMAL_LOADED_MODULES,generated_runner=False)
    for module_name,relative in _MINUTE_THERMAL_LOADED_MODULES.items():
        module=importlib.import_module(module_name)
        _minute1_module_globals(module,root/relative)
    return found


def _minute1_module_globals(module,path):
    """Check source imports, literal/derived constants and referenced builtins.

    Only a narrow pure-constructor expression subset is evaluated. Reading a
    source table, constructing an environment, calling a solver, or manufacturing
    a generated runner at module scope is not an approved global initializer.
    """
    namespace=dict(__builtins__=builtins.__dict__,__file__=str(path),
        __name__=module.__name__,__package__=module.__package__)
    declared=set(namespace);tree=ast.parse(path.read_bytes())
    constructors={'tuple','list','dict','set','frozenset','str','int','float','bool',
        'range','len','sorted','getattr','vars','Path'}
    def same(left,right):
        if type(left) is not type(right):return False
        if type(left) is dict:return left.keys()==right.keys() and all(same(left[k],right[k]) for k in left)
        if type(left) in (list,tuple):return len(left)==len(right) and all(same(a,b) for a,b in zip(left,right))
        return left==right
    def imported(key,wanted):
        if module.__dict__.get(key) is not wanted:raise ValueError('Minute loaded imported global changed: '+module.__name__+'.'+key)
        namespace[key]=wanted;declared.add(key)
    for node in tree.body:
        if isinstance(node,ast.Import):
            for item in node.names:
                key=item.asname or item.name.split('.')[0]
                imported(key,importlib.import_module(item.name if item.asname else item.name.split('.')[0]))
        elif isinstance(node,ast.ImportFrom) and node.module!='__future__':
            target=importlib.import_module('.'*node.level+(node.module or ''),module.__package__) if node.level else importlib.import_module(node.module)
            for item in node.names:
                if item.name=='*':raise ValueError('Minute loaded wildcard globals are unsupported')
                imported(item.asname or item.name,getattr(target,item.name))
        elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            namespace[node.name]=getattr(module,node.name);declared.add(node.name)
        elif isinstance(node,(ast.Assign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            if node.value is None:continue
            for call in (item for item in ast.walk(node.value) if isinstance(item,ast.Call)):
                constructor=isinstance(call.func,ast.Name) and call.func.id in constructors
                path_resolve=isinstance(call.func,ast.Attribute) and call.func.attr=='resolve' and not call.args and not call.keywords
                if not constructor and not path_resolve:
                    raise ValueError('Minute loaded global initializer is not an approved pure expression: '+module.__name__)
            try:wanted=eval(compile(ast.Expression(node.value),str(path),'eval'),namespace)
            except Exception as exc:raise ValueError('Minute source global cannot be independently resolved: '+module.__name__) from exc
            for target in targets:
                if not isinstance(target,ast.Name):raise ValueError('Minute source global assignment target is unsupported')
                key=target.id
                if key not in module.__dict__ or not same(module.__dict__[key],wanted):
                    raise ValueError('Minute loaded source global changed: '+module.__name__+'.'+key)
                namespace[key]=wanted;declared.add(key)
    if module.__dict__.get('__builtins__') is not builtins.__dict__:
        raise ValueError('Minute loaded module builtins changed: '+module.__name__)
    def functions(owner):
        for value in vars(owner).values():
            if isinstance(value,type) and value.__module__==module.__name__:yield from functions(value)
            elif isinstance(value,(classmethod,staticmethod)):yield value.__func__
            elif isinstance(value,property):
                yield from (fn for fn in (value.fget,value.fset,value.fdel) if fn is not None)
            elif isinstance(value,FunctionType):yield value
    def globals_used(code):
        result={item.argval for item in dis.get_instructions(code) if item.opname=='LOAD_GLOBAL'}
        for item in code.co_consts:
            if isinstance(item,CodeType):result.update(globals_used(item))
        return result
    for fn in functions(module):
        if fn.__module__!=module.__name__ or Path(fn.__code__.co_filename).resolve()!=path.resolve():continue
        if fn.__builtins__ is not builtins.__dict__:raise ValueError('Minute loaded function builtins changed: '+fn.__qualname__)
        for key in globals_used(fn.__code__)-declared:
            if key in module.__dict__ or key not in builtins.__dict__:
                raise ValueError('Minute loaded referenced global/builtin changed: '+module.__name__+'.'+key)


def _fuel_generated_runner_functions(root,expected):
    """Bind the two isolated execution functions to reviewed native code.

    These functions come from assignments, not source FunctionDef nodes. Only
    globals actually loaded by their bytecode (including nested bodies) are
    compared; the two intended SF2 overrides have explicit owned identities.
    """
    from tools.single_fault_fuel_20261102 import runner,environment
    from tools.annual_readiness import runner as native
    relative='tools/annual_readiness/runner.py';path=root/relative
    if (runner.native is not native or Path(native.__file__).resolve()!=path
            or runner.decision_snapshot is not environment.decision_snapshot):
        raise ValueError('Compiled SCI_FUEL loaded runner module/snapshot owner changed')
    key=(str(path),expected[relative])
    if key not in _FUEL_CODE_CACHE:
        raw=path.read_bytes()
        _FUEL_CODE_CACHE[key]=(ast.parse(raw),compile(raw,str(path),'exec',dont_inherit=True))
    tree,module_code=_FUEL_CODE_CACHE[key]
    def global_names(code):
        names={item.argval for item in dis.get_instructions(code) if item.opname=='LOAD_GLOBAL'}
        for item in code.co_consts:
            if isinstance(item,CodeType):names.update(global_names(item))
        return names
    def exact(left,right):
        if type(left) is not type(right):return False
        if isinstance(left,dict):return left.keys()==right.keys() and all(exact(left[k],right[k]) for k in left)
        if isinstance(left,tuple):return len(left)==len(right) and all(exact(a,b) for a,b in zip(left,right))
        return left==right
    found=[]
    for name,native_name,override,owner in (
            ('_execute_loop','_execute_loop','_snapshot',environment.decision_snapshot),
            ('_native_execute_attempt','_execute_attempt','_execute_loop',runner._execute_loop)):
        wanted=next(item for item in module_code.co_consts if isinstance(item,CodeType) and item.co_name==native_name)
        node=next(item for item in tree.body if isinstance(item,ast.FunctionDef) and item.name==native_name)
        defaults=tuple(ast.literal_eval(item) for item in node.args.defaults) or None
        kwdefaults={arg.arg:ast.literal_eval(item) for arg,item in zip(node.args.kwonlyargs,node.args.kw_defaults) if item is not None} or None
        original=getattr(native,native_name);function=getattr(runner,name)
        for value in (original,function):
            if (not isinstance(value,FunctionType) or value.__code__!=wanted
                    or Path(value.__code__.co_filename).resolve()!=path or value.__name__!=native_name
                    or value.__module__!=native.__name__ or value.__closure__ is not None
                    or value.__builtins__ is not native.__dict__['__builtins__']
                    or not exact(value.__defaults__,defaults) or not exact(value.__kwdefaults__,kwdefaults)):
                raise ValueError('Compiled SCI_FUEL loaded isolated runner code/defaults/builtins changed: '+name)
        namespace=function.__globals__
        if (original.__globals__ is not native.__dict__ or namespace is native.__dict__
                or namespace.get('__name__')!=native.__name__ or Path(namespace.get('__file__','')).resolve()!=path
                or namespace.get('__builtins__') is not native.__dict__['__builtins__']
                or namespace.get(override) is not owner):
            raise ValueError('Compiled SCI_FUEL loaded isolated runner globals/owner changed: '+name)
        for key in global_names(wanted)-{override}:
            if ((key in namespace)!=(key in native.__dict__)
                    or key in namespace and namespace[key] is not native.__dict__[key]):
                raise ValueError('Compiled SCI_FUEL loaded isolated runner global binding changed: '+name+'.'+key)
        found.append((relative,name,function.__code__))
    return tuple(found)


def vector_sha(value):
    """Hash every original coordinate, preserving JSON numeric representation."""
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,allow_nan=False,
        separators=(',',':')).encode('utf8')).hexdigest()


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def native_source_binding(source_root,*,source_admission=None,source_hashes=None):
    """Verify the known native return control flow; cache only unchanged files."""
    root=Path(source_root).resolve();observed={}
    if source_admission is not None:
        from tools.minute1_20261103.profile import verify_source_admission
        admission=verify_source_admission(root,source_admission,source_hashes=source_hashes)
        observed=admission['source_files'];recorder=_sha(__file__)
        pair=contracts.classify_recorder_source_pair(recorder,observed,source_admission=admission)
        if not pair['supported']:raise ValueError('Minute recorder/source admission pair is not approved')
        binding=dict(supported=True,source_sha256=deepcopy(observed),source_profile=pair['source_profile'],
            source_admission=deepcopy(source_admission),recorder_source_sha256=recorder,
            control_flow='unchanged SF2 accepted final native result; explicit separately reviewed minute source/runtime',
            source_lines='exact external full source admission; final native-result hook ownership remains required')
        if pair['source_profile']==contracts.MINUTE_THERMAL_CAPTURE_PROFILE:
            binding['native_revision_contract']=deepcopy(admission['native_revision_contract'])
        return binding
    for relative,expected in NATIVE_SOURCE_CONTRACT.items():
        path=root/relative
        if not path.is_file():
            observed[relative]=None;continue
        stat=path.stat();key=(str(path),stat.st_size,stat.st_mtime_ns)
        if key not in _SOURCE_CACHE:_SOURCE_CACHE[key]=_sha(path)
        observed[relative]=_SOURCE_CACHE[key]
    profile=contracts.match_native_source_profile(observed)
    return dict(supported=profile is not None,source_sha256=observed,source_profile=profile,
        control_flow='native backend returns first accepted result; accepted recovery replaces failed result; physical final cost res.x is the executed result primal',
        source_lines=('separate reviewed SF2 fuel leaf; source identity only; final native-result ownership remains required'
            if profile==contracts.SCI_FUEL_CAPTURE_PROFILE else
            'separate reviewed SF1 leaf; native final-result ownership retained; exact source_sha256 above'
            if profile==contracts.SCIENTIFIC_CAPTURE_PROFILE else
            'solver_backend.py:128-151; model_closeout/numerics.py:118-127; physical_balance.py:354-366; essential_fixes/execution.py:420-421'))


def compiled_origin(source_root,source_hashes,*,source_admission=None):
    """Check the approved, already-loaded leaf before enabling output capture.

    This reads source identity only. It never asks for a state/closure/solve.
    Runtime and reader independently check the final-result packet afterwards.
    """
    root=Path(source_root).resolve()
    native={name:source_hashes.get(name) for name in NATIVE_SOURCE_CONTRACT}
    admission=None
    if source_admission is not None:
        from tools.minute1_20261103.profile import verify_source_admission
        admission=verify_source_admission(root,source_admission,source_hashes=source_hashes)
        profile=contracts.match_native_source_profile(source_hashes,source_admission=admission)
        pair=contracts.classify_recorder_source_pair(_sha(__file__),source_hashes,source_admission=admission)
        if not pair['compiled_source_profile_allowed']:raise ValueError('Minute compiled recorder/source pair is not approved')
    else:profile=contracts.match_native_source_profile(native)
    if profile not in (contracts.CAPTURE_PROFILE,contracts.SCIENTIFIC_CAPTURE_PROFILE,contracts.SCI_FUEL_CAPTURE_PROFILE,contracts.MINUTE1_CAPTURE_PROFILE,contracts.MINUTE_THERMAL_CAPTURE_PROFILE):
        raise ValueError('Compiled capture requires the registered capture source profile')
    extras=contracts.CAPTURE_AUXILIARY_SOURCE_CONTRACT
    if not isinstance(extras,dict):raise ValueError('Compiled capture helper release is not approved')
    expected=dict(admission['source_files']) if admission is not None else dict(native,**extras)
    if profile==contracts.SCIENTIFIC_CAPTURE_PROFILE:
        additional=contracts.scientific_capture_source_extra_contract(native)
        if additional is None:raise ValueError('Compiled scientific source extras are not approved')
        expected.update(additional)
    elif profile==contracts.SCI_FUEL_CAPTURE_PROFILE:
        additional=contracts.sci_fuel_capture_source_extra_contract(native)
        if additional is None:raise ValueError('Compiled SCI_FUEL source extras are not approved')
        expected.update(additional)
    if any(source_hashes.get(name)!=pin for name,pin in expected.items()):
        raise ValueError('Compiled capture source files differ from frozen source bindings')
    from tools.operating_response import physical_balance
    from . import compiled_capture,compiled_flow_input
    leaf=physical_balance.solve_physical
    leaf_path=root/'tools/operating_response/physical_balance.py'
    if (Path(physical_balance.__file__).resolve()!=leaf_path or not isinstance(leaf,FunctionType) or
            Path(leaf.__code__.co_filename).resolve()!=leaf_path or leaf.__name__!='solve_physical' or
            leaf.__globals__ is not physical_balance.__dict__):
        raise ValueError('Compiled capture loaded leaf differs from the producer source root')
    if (physical_balance._annual_capture_enabled is not compiled_capture.capture_enabled or
            physical_balance._annual_flow_input_fields is not compiled_capture.flow_input_fields):
        raise ValueError('Compiled capture loaded leaf helper aliases changed')
    if any(getattr(compiled_capture,name) is not getattr(compiled_flow_input,name)
           for name in ('_plain','_source','_index_map','build_compiled_flow_input')):
        raise ValueError('Compiled capture loaded helper aliases changed')
    helper_functions=[]
    for module,relative in ((compiled_capture,'tools/annual_program/compiled_capture.py'),
                            (compiled_flow_input,'tools/annual_program/compiled_flow_input.py')):
        if Path(module.__file__).resolve()!=root/relative:raise ValueError('Compiled capture loaded helper path changed')
        for name,value in module.__dict__.items():
            if isinstance(value,FunctionType) and value.__module__==module.__name__:
                function=_loaded_capture_function(module,name,value)
                if function.__globals__ is not module.__dict__:raise ValueError('Compiled capture loaded helper globals changed')
                helper_functions.append((relative,name,function.__code__))
    stamps=[]
    for name in expected:
        stat=(root/name).stat();stamps.append((name,stat.st_size,stat.st_mtime_ns,stat.st_ino))
    fuel_functions=_fuel_loaded_functions(root,expected) if profile in (contracts.SCI_FUEL_CAPTURE_PROFILE,contracts.MINUTE1_CAPTURE_PROFILE,contracts.MINUTE_THERMAL_CAPTURE_PROFILE) else ()
    minute_functions=(_minute_thermal_loaded_functions(root,expected) if profile==contracts.MINUTE_THERMAL_CAPTURE_PROFILE else
        _minute1_loaded_functions(root,expected) if profile==contracts.MINUTE1_CAPTURE_PROFILE else ())
    key=(str(root),tuple(sorted(expected.items())),tuple(stamps),leaf.__code__,tuple(helper_functions),fuel_functions,minute_functions,
        source_admission['sha256'] if source_admission is not None else None)
    if key in _COMPILED_SOURCE_CACHE:return deepcopy(_COMPILED_SOURCE_CACHE[key])
    for name,pin in expected.items():
        path=root/name
        if not path.is_file() or _sha(path)!=pin:raise ValueError('Compiled capture source bytes changed: '+name)
    migration=contracts.inspect_leaf_migration(leaf_path.read_bytes())
    if migration['runtime_profile_approved'] is not True:
        raise ValueError('Compiled capture leaf migration is not approved')
    compiled=compile(leaf_path.read_bytes(),leaf.__code__.co_filename,'exec',dont_inherit=True)
    expected_code=next(item for item in compiled.co_consts if isinstance(item,CodeType) and item.co_name=='solve_physical')
    if leaf.__code__!=expected_code:raise ValueError('Compiled capture loaded leaf code differs from approved source')
    for module,relative in ((compiled_capture,'tools/annual_program/compiled_capture.py'),
                            (compiled_flow_input,'tools/annual_program/compiled_flow_input.py')):
        code=compile((root/relative).read_bytes(),str(root/relative),'exec',dont_inherit=True)
        for item in code.co_consts:
            if isinstance(item,CodeType):
                function=_loaded_capture_function(module,item.co_name,getattr(module,item.co_name))
                if function.__code__!=item or function.__globals__ is not module.__dict__:
                    raise ValueError('Compiled capture loaded helper code changed')
    schema=MINUTE_THERMAL_SOURCE_SCHEMA if profile==contracts.MINUTE_THERMAL_CAPTURE_PROFILE else MINUTE1_SOURCE_SCHEMA if admission is not None else SOURCE_SCHEMA
    origin=dict(schema=schema,source_root=str(root),source_files=expected,
        leaf_function='tools.operating_response.physical_balance.solve_physical')
    if admission is not None:origin.update(source_profile=profile,source_admission=deepcopy(source_admission))
    if profile==contracts.MINUTE_THERMAL_CAPTURE_PROFILE:origin['native_revision_contract']=deepcopy(admission['native_revision_contract'])
    if len(_COMPILED_SOURCE_CACHE)>=8:_COMPILED_SOURCE_CACHE.pop(next(iter(_COMPILED_SOURCE_CACHE)))
    _COMPILED_SOURCE_CACHE[key]=deepcopy(origin)
    return origin


def _actual_source_freeze(env):
    """Resolve a source freeze without treating the local context as a grant."""
    annual=getattr(env,'annual_program_data_context',None)
    local=getattr(env,'local_actual_witness_source_context',None)
    minute=getattr(env,'minute1_source_admission',None)
    if minute is not None:
        if annual is not None or local is not None:raise ValueError('Minute and historical actual-source contexts cannot be combined')
        from tools.minute1_20261103.profile import resolve_source_freeze
        return resolve_source_freeze(env)
    if annual is not None and local is not None:
        raise ValueError('Annual and local actual-witness source contexts cannot be combined')
    if local is not None:
        from tools.eight_fault_study.runtime import resolve_local_source_freeze
        return resolve_local_source_freeze(env)
    if not isinstance(annual,dict):
        raise ValueError('An explicit actual-witness source freeze is required')
    return annual.get('runtime_freeze_receipt',{}).get('source_sha256',{})


def _compiled_evidence(core,segment,result,x,source_binding,caller):
    """Copy only this final-result argument's packet; no candidate lookup."""
    binding=dict(schema='hospital_ies.annual_program.final_compiled_flow_binding.v1',
        status='unknown',reasons=[],exact_final_result_argument=True,
        payload_content_sha256=None,source_profile=source_binding.get('source_profile'),
        source_root=None,source_files=None,leaf_function=None,actual_hook=None)
    packet=result.get('_compiled_flow_input')
    if packet is None:
        from .compiled_capture import capture_enabled
        if (capture_enabled() and source_binding.get('source_profile') in (contracts.SCI_FUEL_CAPTURE_PROFILE,contracts.MINUTE1_CAPTURE_PROFILE,contracts.MINUTE_THERMAL_CAPTURE_PROFILE)
                and (result.get('_sf2_fuel_receipt') or {}).get('role')=='actual_interval'):
            raise ValueError('SF2 actual result lacks its final compiled flow input')
        binding['reasons']=['missing_final_compiled_flow_input'];return None,binding
    if source_binding.get('source_profile') not in (contracts.CAPTURE_PROFILE,contracts.SCIENTIFIC_CAPTURE_PROFILE,contracts.SCI_FUEL_CAPTURE_PROFILE,contracts.MINUTE1_CAPTURE_PROFILE,contracts.MINUTE_THERMAL_CAPTURE_PROFILE):
        raise ValueError('Compiled packet supplied by an unsupported physical leaf profile')
    payload=validate_compiled_flow_input(packet,raw_x=x)
    origin=payload['source_origin'];root=Path(core.env.root).resolve()
    if origin['source_root']!=str(root):raise ValueError('Compiled packet source root mismatch')
    # Origin is an assertion, not proof. Independently check its source bytes,
    # loaded leaf and complete output-only migration before binding the packet.
    thermal=source_binding.get('source_profile')==contracts.MINUTE_THERMAL_CAPTURE_PROFILE
    minute=source_binding.get('source_profile') in (contracts.MINUTE1_CAPTURE_PROFILE,contracts.MINUTE_THERMAL_CAPTURE_PROFILE)
    if minute:
        reference=getattr(core.env,'minute1_source_admission',None)
        if (origin.get('schema')!=(MINUTE_THERMAL_SOURCE_SCHEMA if thermal else MINUTE1_SOURCE_SCHEMA)
                or origin.get('source_profile')!=source_binding['source_profile']
                or origin.get('source_admission')!=reference or source_binding.get('source_admission')!=reference):
            raise ValueError('Minute packet admission differs from the actual bound runtime')
        if thermal and origin['native_revision_contract']!=source_binding.get('native_revision_contract'):
            raise ValueError('Minute thermal packet native revision differs from the actual bound runtime')
        checked=compiled_origin(root,origin['source_files'],source_admission=reference)
    else:
        if origin.get('schema')!=SOURCE_SCHEMA:raise ValueError('Minute packet supplied without an explicit minute runtime admission')
        checked=compiled_origin(root,origin['source_files'])
    if origin!=checked:raise ValueError('Compiled packet source origin mismatch')
    from . import source_runtime
    AnnualSourceMixin=source_runtime.AnnualSourceMixin
    hook=AnnualSourceMixin._after_actual_segment
    hook_file=root/'tools/annual_program/source_runtime.py'
    if (caller is None or caller.f_code is not hook.__code__ or
            Path(hook.__code__.co_filename).resolve()!=hook_file or
            caller.f_locals.get('self') is not core or caller.f_locals.get('segment') is not segment or
            caller.f_locals.get('result') is not result):
        raise ValueError('Compiled packet lacks the exact final actual-result hook')
    frozen=_actual_source_freeze(core.env)
    hook_sha=_sha(hook_file)
    if (ACTUAL_HOOK_SOURCE_CONTRACT.get('tools/annual_program/source_runtime.py')!=hook_sha or
            frozen.get('tools/annual_program/source_runtime.py')!=hook_sha):
        raise ValueError('Compiled actual hook source differs from the frozen runtime')
    if hook_sha not in _HOOK_CODE_CACHE:
        module_code=compile(hook_file.read_bytes(),hook.__code__.co_filename,'exec',dont_inherit=True)
        class_code=next(item for item in module_code.co_consts if isinstance(item,CodeType) and item.co_name=='AnnualSourceMixin')
        _HOOK_CODE_CACHE[hook_sha]=next(item for item in class_code.co_consts if isinstance(item,CodeType) and item.co_name=='_after_actual_segment')
    if (hook.__code__!=_HOOK_CODE_CACHE[hook_sha] or hook.__globals__ is not source_runtime.__dict__):
        raise ValueError('Compiled actual hook loaded code differs from the approved source')
    if any(frozen.get(name)!=pin for name,pin in origin['source_files'].items()):
        raise ValueError('Compiled packet sources differ from the frozen actual runtime')
    # The native closure returns start + span; subtracting the absolute clock
    # need not recover the bit-identical solve span. Keep that original packet
    # duration and use only the native minute-clock arithmetic budget.
    if abs(payload['dt_h']*60.-(segment['end_minute']-segment['start_minute']))>_TIME_EPS_MINUTES:
        raise ValueError('Compiled packet dt differs from the actual interval')
    binding.update(status='recorded',payload_content_sha256=payload['content_sha256'],
        source_root=str(root),source_files=deepcopy(origin['source_files']),
        leaf_function=origin['leaf_function'],actual_hook=dict(file='tools/annual_program/source_runtime.py',
            function='AnnualSourceMixin._after_actual_segment',sha256=hook_sha))
    if minute:binding['source_admission']=deepcopy(reference)
    if thermal:binding['native_revision_contract']=deepcopy(origin['native_revision_contract'])
    return payload,binding


def _finite(value,name,*,nonnegative=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('Invalid actual finite number: '+name)
    if nonnegative and value<0.:raise ValueError('Negative actual number: '+name)
    return value


def _tree_finite(value):
    if isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite actual native evidence')
    if isinstance(value,dict):
        for item in value.values():_tree_finite(item)
    elif isinstance(value,(list,tuple)):
        for item in value:_tree_finite(item)
    elif hasattr(value,'tolist'):
        # Inspect numeric arrays/scalars before sink normalization can turn an
        # invalid nonfinite coefficient into a missing JSON value.
        _tree_finite(value.tolist())


def _native_row_bound_absence(rows):
    """Permit exactly validate_vector's absent-side endpoint/residual pairs.

    Native detailed rows use lower=-inf / lower_residual=-inf, or upper=+inf /
    upper_residual=-inf for absent inequality sides. Activity and violations,
    finite-side residuals, coefficients and every other field stay finite.
    The original sink maps these infinities to null; the returned descriptors
    retain their unbounded-side meaning without changing the incoming rows.
    """
    if not isinstance(rows,list):raise ValueError('Invalid native validation row list')
    absent=[]
    endpoint_keys={'lower','upper','lower_residual','upper_residual'}
    for position,row in enumerate(rows):
        if not isinstance(row,dict):raise ValueError('Invalid native validation row')
        _finite(row.get('activity'),'native_row_activity')
        for key in ('violation','scaled_violation'):
            if key in row:_finite(row[key],'native_row_'+key,nonnegative=True)
        flags={}
        for side,allowed_sign in (('lower',-math.inf),('upper',math.inf)):
            value=row.get(side)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or math.isnan(value):
                raise ValueError('Invalid native row endpoint: '+side)
            unbounded=math.isinf(value)
            residual_name=side+'_residual'
            if unbounded:
                if value!=allowed_sign:raise ValueError('Wrong-signed infinite native endpoint: '+side)
                residual=row.get(residual_name)
                if (isinstance(residual,bool) or not isinstance(residual,(int,float)) or
                        residual!=-math.inf):
                    raise ValueError('Unbounded native endpoint lacks matching negative infinite residual: '+side)
            elif residual_name in row:
                _finite(row[residual_name],'native_row_'+residual_name)
            flags[side+'_unbounded']=unbounded
        _tree_finite({key:value for key,value in row.items() if key not in endpoint_keys})
        if any(flags.values()):
            absent.append(dict(row_position=position,original_row_index=row.get('index'),
                row_name=row.get('name'),**flags))
    return absent


def _matrix(value,name,*,nonnegative=True):
    value=normalize_json(value)
    if not isinstance(value,list) or len(value)!=4 or any(not isinstance(row,list) or len(row)!=3 for row in value):
        raise ValueError('Invalid actual service matrix: '+name)
    for row in value:
        for cell in row:_finite(cell,name,nonnegative=nonnegative)
    return value


def _same_matrix(a,b):
    return all(abs(a[g][k]-b[g][k])<=1e-9 for g in range(4) for k in range(3))


def _service_basis(segment,result,x):
    connected=_matrix(segment['connected_loads_kW'],'connected')
    cuts=_matrix(segment['disconnected_loads_kW'],'cuts')
    total=_matrix(segment['unserved_kW'],'total_unserved')
    lost=_matrix(result['lost'],'result_connected_unserved')
    served=_matrix(result['served'],'result_served',nonnegative=False)
    primal_lost=[[max(0.,x[14+3*g+k]) for k in range(3)] for g in range(4)]
    if (not _same_matrix(lost,primal_lost) or
            not _same_matrix(total,[[lost[g][k]+cuts[g][k] for k in range(3)] for g in range(4)]) or
            not _same_matrix(served,[[connected[g][k]-lost[g][k] for k in range(3)] for g in range(4)])):
        raise ValueError('Actual connected/cut/total service identity mismatch')
    if normalize_json(segment['action'])!=normalize_json(result['action']):
        raise ValueError('Supplied final result action differs from actual segment')
    return dict(connected_loads_kW=connected,active_cut_kW=cuts,total_unserved_kW=total,
        connected_unserved_kW=lost,delivered_kW=served,
        service_request_ledger_not_required_at_this_hook=True)


def _accepted(layer):
    return [(i,a) for i,a in enumerate(layer.get('attempts',[])) if
        a.get('accepted') is True and not isinstance(a.get('status'),bool) and a.get('status')==0]


def _domain_witnesses(result,x):
    out=[]
    for index,domain in enumerate((result.get('m23_boiler_domains') or {}).get('domains',[])):
        layers=domain.get('solver_backend_layers') or []
        if domain.get('status')!='legal' or not layers:continue
        for attempt_index,attempt in _accepted(layers[-1]):
            if attempt.get('raw_x')==x:
                out.append(dict(domain_index=index,domain=domain.get('domain'),
                    layer_index=len(layers)-1,attempt_index=attempt_index,raw_x_sha256=vector_sha(x),
                    rows_ref=deepcopy(attempt.get('validation',{}).get('rows_ref'))))
    return out


def record_actual_witness(core,segment,result):
    """Append one original-sink proof of the supplied final actual ``sol``.

    Attach and return ``segment[FIELD]`` only after a successful sink append.
    Unsupported attribution attaches an explicit unknown receipt without a
    success call. Malformed actual data and sink I/O errors propagate before a
    recorded flag is attached. Only metadata and the existing log are changed.
    """
    if FIELD in segment:raise ValueError('Actual segment already has a witness receipt')
    start=_finite(segment['start_minute'],'segment_start',nonnegative=True)
    end=_finite(segment['end_minute'],'segment_end',nonnegative=True)
    if end<=start:raise ValueError('Nonpositive actual witness interval')
    base=dict(schema=SCHEMA,status='unknown',reasons=[],start_minute=start,end_minute=end,
        solver_call_index=None,raw_x_sha256=None,raw_x_length=None,unused_steam_lbph=None,
        rows_ref=None,primal_selected_by_action_matching=False,discard_imputed_from_remainder=False,
        physical_dynamics_modified=False)
    def unknown(reason):
        receipt=dict(base,reasons=[reason]);segment[FIELD]=deepcopy(receipt);return receipt
    source_root=getattr(getattr(core,'env',None),'root',ROOT)
    minute=getattr(getattr(core,'env',None),'minute1_source_admission',None)
    if minute is not None:
        source_binding=native_source_binding(source_root,source_admission=minute,
            source_hashes=_actual_source_freeze(core.env))
    else:source_binding=native_source_binding(source_root)
    if not source_binding['supported']:return unknown('native_source_contract_not_supported')
    layers=result.get('solver_backend_layers') or []
    if (not layers or layers[-1].get('layer')!='cost' or
            isinstance(layers[-1].get('status'),bool) or layers[-1].get('status')!=0):
        return unknown('missing_successful_final_cost_layer')
    final=layers[-1]
    if final.get('backend')!='gurobipy':return unknown('unsupported_final_native_backend')
    accepted=_accepted(final)
    if len(accepted)!=1:return unknown('nonunique_accepted_final_cost_attempt' if accepted else 'no_unique_accepted_final_cost_attempt')
    attempt_index,attempt=accepted[0]
    if (attempt.get('native_status')!=2 or attempt.get('objective_consistent') is not True or attempt.get('error') is not None):
        return unknown('incomplete_native_acceptance')
    x=attempt.get('raw_x')
    if x is None:return unknown('missing_accepted_raw_x')
    if not isinstance(x,list) or len(x) not in (47,48):return unknown('unsupported_final_actual_primal_layout')
    for value in x:_finite(value,'raw_x')
    _finite(x[13],'unused_steam',nonnegative=True)
    validation=attempt.get('validation') or {}
    if validation.get('integer_indices')!=[3,4,5,42,44,45]:return unknown('unsupported_final_actual_integer_layout')
    for name,limit in (('max_original_violation',1e-7),('max_lock_violation',1e-10),('max_integrality_distance',1e-9)):
        if name not in validation:return unknown('missing_original_native_validation:'+name)
        if _finite(validation[name],name,nonnegative=True)>limit:
            raise ValueError('Actual accepted primal exceeds original validation: '+name)
    rows=validation.get('rows')
    if not isinstance(rows,list):return unknown('missing_inline_final_native_rows')
    row_absence=_native_row_bound_absence(rows)
    worst_absence=_native_row_bound_absence(validation['worst_rows']) if 'worst_rows' in validation else []
    _tree_finite({key:value for key,value in validation.items() if key not in ('rows','worst_rows')})
    steam_rows=[row for row in rows if row.get('name')=='balance.steam' and row.get('kind')=='physical' and row.get('unit')=='lb/h']
    if len(steam_rows)!=1:return unknown('missing_unique_original_steam_row')
    if not all(key in result for key in ('lost','served','action')):return unknown('missing_actual_result_service_basis')
    service=_service_basis(segment,result,x)
    frame=inspect.currentframe()
    try:compiled,compiled_binding=_compiled_evidence(core,segment,result,x,source_binding,frame.f_back)
    finally:del frame
    sink=getattr(core,'solver_calls',None)
    if not isinstance(sink,SolverCallLog):return unknown('missing_original_externalizing_solver_sink')
    original_layer_index=len(layers)-1
    domains=_domain_witnesses(result,x)
    selection=deepcopy(result.get('m22_mode_selection'))
    metadata=dict(schema=SCHEMA,scope='final_actual_interval_result',
        original_result_layer_index=original_layer_index,logged_layer_index=0,accepted_attempt_index=attempt_index,
        raw_x_sha256=vector_sha(x),raw_x_length=len(x),unused_steam_coordinate=13,unused_steam_lbph=x[13],
        original_native_source_binding=source_binding,recorder_source_sha256=_sha(__file__),
        backend_selected_location_was_originally_emitted=False,
        accepted_location_attribution='unique accepted final-cost attempt from source-pinned native return control flow',
        final_result_selection=selection,candidate_solver_call_indices=[row.get('solver_call_index') for row in
            (selection or {}).get('candidates',[])],exact_result_domain_witnesses=domains,
        selected_domain=deepcopy(domains[0]) if len(domains)==1 else None,
        selected_domain_attribution='unique_exact_result_witness' if len(domains)==1 else 'no_explicit_selection_pointer',
        service_basis=service,original_steam_row=deepcopy(steam_rows[0]),
        compiled_flow_input=compiled,compiled_flow_binding=compiled_binding,
        native_row_bound_encoding=dict(schema='hospital_ies.native_row_bound_absence.v1',
            source='source-pinned solver_diagnostics.validate_vector:94-110',
            original_encoding='lower=-Infinity or upper=+Infinity; corresponding residual=-Infinity',
            original_sink_encoding='normalize_json maps only these validated infinite fields to JSON null',
            normalized_null_means_unbounded_endpoint_not_zero=True,
            all_other_actual_numbers_stay_finite=True,rows=row_absence,worst_rows=worst_absence))
    call_index=len(sink)
    call=dict(time_minute=start,dt_h=(end-start)/60.,phase=PHASE,status='success',
        evidence_copy_only=True,additional_native_solver_calls=0,
        annual_actual_witness=metadata,solver_backend_layers=[deepcopy(final)])
    sink.append(call)  # Existing writer owns/externalizes exact copied rows.
    logged=sink[call_index]
    logged_attempt=logged['solver_backend_layers'][0]['attempts'][attempt_index]
    ref=logged_attempt.get('validation',{}).get('rows_ref')
    if ref is None or logged_attempt['raw_x']!=x:raise ValueError('Original sink did not retain exact actual native proof')
    receipt=dict(base,status='recorded',solver_call_index=call_index,
        raw_x_sha256=metadata['raw_x_sha256'],raw_x_length=len(x),unused_steam_lbph=x[13],
        original_result_layer_index=original_layer_index,logged_layer_index=0,accepted_attempt_index=attempt_index,
        rows_ref=deepcopy(ref),recorder_source_sha256=metadata['recorder_source_sha256'],
        compiled_flow_input_sha256=compiled_binding['payload_content_sha256'])
    segment[FIELD]=deepcopy(receipt)
    return receipt
