"""Exact closed-run scientific comparison with path-specific wall exclusions.

No physical model/environment is imported or solved. Unknown elapsed_seconds
fields remain scientific data. E/steam/cooling qualification is a separate audit.
"""
from __future__ import annotations
import argparse,copy,gzip,hashlib,importlib,json,math,re,sys
from pathlib import Path
from types import CodeType

CALL=re.compile(r'(?:^|\.)solver_calls\[\d+\]$')
ATTEMPT=re.compile(r'(?:^|\.)solver_backend_layers\[\d+\]\.attempts\[\d+\]$')
PARAMETERS=re.compile(r'(?:^|\.)solver_backend_layers\[\d+\]\.attempts\[\d+\]\.parameters$')
SIDE_CHECKS=['clock','physical_state','inventories','command_and_recovery_queues','solver_call_count']
# These containers describe file addresses/durability, not state or service.
# Their raw block bytes remain authenticated by the original journal verifier.
IO_CONTAINERS={'solver_row_sidecar','domain_evidence_files','domain_store_acknowledgement'}
RESULT_FIELDS=('reset_count','observed_until_minute','requested_end_minute','unknown_tail_minutes',
    'recording_mode','economics','resolved_model_config','configuration','annual_readiness','annual_program')
SUMMARY_FIELDS=('block_count','segment_count','solver_call_count','event_count','carrier_service','storage','terminal','observed_until_minute')
# This independent gate tool admits this reviewed release only.
CACHE_FROZEN16_RELEASE={
    'backend_sha256':'6d6874146d368ace320fe471bd25f5107aa88d4db46495f6a2c6b3d416875576',
    'helper_sha256':'82a1f6ef5bb7c5d7b078f25092bca3306837efe5cbae8f03336df4a09f89002a'}
CACHE_RELEASE={
    'backend_sha256':'5c67375a12d9972862bc0fa24b98fef07da9309e7c4ad01770223d1c7dad8fdd',
    'helper_sha256':'0ecbf955523f8c7a8a2a45ab38931dbe9cf86aac5f17625e329cedd0701725e4'}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def read(path):
    def invalid(value):raise ValueError('Nonfinite JSON constant: '+value)
    return json.loads(Path(path).read_text('utf8'),parse_constant=invalid)
def _wall_number(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0.:raise ValueError('Invalid solver wall metadata')
def _side_effect(value):
    before=value.get('before_sha256')
    if (type(before) is not str or re.fullmatch('[0-9a-f]{64}',before) is None or value.get('after_sha256')!=before
        or value.get('checks')!=SIDE_CHECKS or value.get('unchanged') is not True
        or any(value.get(k) is not False for k in ('additional_solve_called','decision_snapshot_called','observe_called'))):
        raise ValueError('Invalid native side_effect_check; derived hashes cannot be projected')

def _json_sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')).hexdigest()

def _cache_release_for(bindings):
    hashes=bindings.get('source',{})
    for release in (CACHE_FROZEN16_RELEASE,CACHE_RELEASE):
        if hashes.get('src/hospital_ies/solver_backend.py')==release['backend_sha256']:
            if hashes.get('src/hospital_ies/solver_result_cache.py')!=release['helper_sha256']:
                raise ValueError('cache backend/helper release pair is mixed or incomplete')
            return dict(release)
    return None

def _cache_source_context(source_root,bindings):
    """Use existing descriptor/code metadata after exact file authentication.

    Importing this reviewed adapter creates no model or solver environment;
    descriptor() reads only version/constants. No optimize function is called.
    """
    if source_root is None:return None
    expected={'backend_sha256':'src/hospital_ies/solver_backend.py','helper_sha256':'src/hospital_ies/solver_result_cache.py'}
    hashes=bindings.get('source',{})
    release=_cache_release_for(bindings)
    if release is None:return None
    root=Path(source_root).resolve()
    for key,relative in expected.items():
        if hashes.get(relative)!=release[key] or sha(root/relative)!=release[key]:
            raise ValueError('cache source bytes/BINDINGS differ from the reviewed release')
    backend=importlib.import_module('hospital_ies.solver_backend')
    helper=importlib.import_module('hospital_ies.solver_result_cache')
    for module,relative in ((backend,expected['backend_sha256']),(helper,expected['helper_sha256'])):
        if Path(module.__file__).resolve()!=(root/relative).resolve():raise ValueError('cache descriptor module loaded from a different source')
    compiled=compile((root/expected['backend_sha256']).read_bytes(),str(root/expected['backend_sha256']),'exec',dont_inherit=True)
    codes=[item for item in compiled.co_consts if isinstance(item,CodeType) and item.co_name=='_gurobi_attempt']
    if len(codes)!=1:raise ValueError('cache native function missing from reviewed source')
    native_code=helper.code_sha(codes[0])
    if native_code!=helper.code_sha(backend._gurobi_attempt.__code__):raise ValueError('cache descriptor loaded native function differs from reviewed source')
    source=dict(release,native_code_sha256=native_code)
    return dict(source=source,numeric_policy_sha256=_json_sha(backend.descriptor()),_origins={},_pending_origins=[])

def _finish_cache_origins(context):
    if context is None:return
    for identity,certificate in context['_pending_origins']:
        if context['_origins'].get(identity)!=certificate:raise ValueError('cache hit has no matching original native accepted certificate in this trajectory')
    context['_pending_origins'].clear()

def _cache_execution(attempt,context):
    """Validate only the explicitly registered cache execution certificate.

    This authenticates its recorded shape/source/vector binding. It never
    replaces the current original rows, objective or physical-state comparison.
    """
    proof=attempt.get('result_cache_provenance')
    if proof is None:
        if 'native_optimize_performed' in attempt:raise ValueError('cache execution flag without provenance')
        return False
    fields={'schema','mode','native_optimize_performed','scope_id','process_id','call_id',
        'origin_scope_id','origin_process_id','origin_call_id','origin_attempt','origin_accepted',
        'problem_sha256','raw_x_sha256','source','numeric_policy_sha256','fresh_original_validation','certificate_sha256'}
    hashed=lambda value:type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None
    source_fields={'backend_sha256','helper_sha256','native_code_sha256'}
    if (not isinstance(context,dict) or not isinstance(proof,dict) or set(proof)!=fields
            or proof['schema']!='hospital_ies.accepted_solver_result_cache.v1'
            or proof['mode'] not in ('native_solve','exact_verified_reuse')
            or type(proof['native_optimize_performed']) is not bool
            or type(attempt.get('native_optimize_performed')) is not bool
            or attempt['native_optimize_performed'] is not proof['native_optimize_performed']
            or proof['native_optimize_performed'] is not (proof['mode']=='native_solve')
            or not isinstance(proof['source'],dict) or set(proof['source'])!=source_fields
            or any(not hashed(v) for v in proof['source'].values())
            or proof['source']!=context.get('source')
            or not hashed(proof['numeric_policy_sha256'])
            or proof['numeric_policy_sha256']!=context.get('numeric_policy_sha256')
            or not hashed(proof['problem_sha256']) or not hashed(proof['certificate_sha256'])
            or type(proof['fresh_original_validation']) is not bool
            or type(proof['origin_accepted']) is not bool):raise ValueError('cache execution certificate shape/source/flags mismatch')
    for key in ('process_id','call_id','origin_process_id','origin_call_id','origin_attempt'):
        if type(proof[key]) is not int or proof[key]<1:raise ValueError('cache execution identity is invalid')
    if (type(proof['scope_id']) is not str or not proof['scope_id']
            or proof['origin_scope_id']!=proof['scope_id'] or proof['origin_process_id']!=proof['process_id']):
        raise ValueError('cache execution origin outside its process scope')
    raw=attempt.get('raw_x')
    if raw is not None and (not isinstance(raw,list) or any(type(v) not in (int,float) or not math.isfinite(v) for v in raw)):
        raise ValueError('cache execution raw vector is invalid')
    if proof['raw_x_sha256']!=(None if raw is None else _json_sha(raw)):
        raise ValueError('cache execution vector binding mismatch')
    body={k:v for k,v in proof.items() if k not in ('mode','native_optimize_performed','call_id','fresh_original_validation','certificate_sha256')}
    if proof['certificate_sha256']!=_json_sha(body):raise ValueError('cache execution certificate SHA mismatch')
    if proof['mode']=='native_solve':
        if (proof['origin_call_id']!=proof['call_id'] or proof['origin_attempt']!=attempt.get('attempt')
                or proof['origin_accepted'] is not attempt.get('accepted')):raise ValueError('cache native origin is not its actual attempt')
    elif (proof['origin_call_id']>=proof['call_id'] or proof['origin_attempt']!=1 or attempt.get('attempt')!=1
            or proof['origin_accepted'] is not True or attempt.get('accepted') is not True
            or attempt.get('status')!=0 or proof['fresh_original_validation'] is not True):
        raise ValueError('cache reuse is not a freshly validated first accepted result')
    if attempt.get('accepted') is True:
        checked=attempt.get('validation')
        if (not isinstance(checked,dict) or attempt.get('status')!=0 or attempt.get('objective_consistent') is not True
                or type(attempt.get('mip_gap')) not in (int,float) or not math.isfinite(attempt['mip_gap'])
                or not 0.<=attempt['mip_gap']<=1e-6):raise ValueError('cache accepted result lacks original acceptance evidence')
        for key,limit in (('max_original_violation',1e-7),('max_lock_violation',1e-10),('max_integrality_distance',1e-9)):
            value=checked.get(key)
            if type(value) not in (int,float) or not math.isfinite(value) or not 0.<=value<=limit:
                raise ValueError('cache original acceptance validation failed')
    if '_origins' in context:
        identity=(proof['origin_scope_id'],proof['origin_process_id'],proof['origin_call_id'],proof['origin_attempt'])
        if proof['mode']=='native_solve' and proof['origin_accepted'] and proof['origin_attempt']==1:
            previous=context['_origins'].get(identity)
            if previous is not None and previous!=proof['certificate_sha256']:raise ValueError('cache native origin has contradictory certificates')
            if previous is None and len(context['_origins'])>=100000:raise ValueError('cache short-gate origin limit exceeded')
            context['_origins'][identity]=proof['certificate_sha256']
        elif proof['mode']=='exact_verified_reuse':context['_pending_origins'].append((identity,proof['certificate_sha256']))
    return True

def project(value,path='block',ignored=None,cache_context=None):
    """Copy all scientific values; remove only observed source14 wall paths."""
    if isinstance(value,list):return [project(v,path+f'[{i}]',ignored,cache_context) for i,v in enumerate(value)]
    if not isinstance(value,dict):return value
    side=path in ('block.terminal.side_effect_check','summary.terminal.side_effect_check')
    if side:_side_effect(value)
    call=bool(CALL.search(path)) and isinstance(value.get('phase'),str) and isinstance(value.get('status'),str) and isinstance(value.get('solver_backend_layers'),list)
    attempt=bool(ATTEMPT.search(path)) and type(value.get('accepted')) is bool and isinstance(value.get('parameters'),dict) and 'raw_x' in value
    cache=attempt and _cache_execution(value,cache_context)
    copied={}
    for key,item in value.items():
        wall=(key=='elapsed_seconds' and (call or attempt)) or (key=='TimeLimit' and bool(PARAMETERS.search(path)))
        io=path=='block' and key in IO_CONTAINERS
        derived=side and key in ('before_sha256','after_sha256')
        cached=cache and key in ('result_cache_provenance','native_optimize_performed')
        if wall or io or derived or cached:
            if wall:_wall_number(item)
            if ignored is not None:ignored.add((re.sub(r'\[\d+\]','[]',path+'.'+key),'solver_wall' if wall else 'file_durability_container' if io else 'authenticated_cache_execution_metadata' if cached else 'validated_side_effect_derived_hash'))
            continue
        copied[key]=project(item,path+'.'+key,ignored,cache_context)
    return copied

def compare_values(a,b,path,stats):
    def fail(kind,**detail):
        stats['difference_count']+=1
        if len(stats['differences'])<200:stats['differences'].append(dict(path=path,kind=kind,**detail))
    if isinstance(a,dict) and isinstance(b,dict):
        if set(a)!=set(b):fail('different_keys',left_only=sorted(set(a)-set(b)),right_only=sorted(set(b)-set(a)))
        for key in a.keys()&b.keys():compare_values(a[key],b[key],path+'.'+key,stats)
    elif isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b):fail('different_lengths',left=len(a),right=len(b))
        for i,(x,y) in enumerate(zip(a,b)):compare_values(x,y,path+f'[{i}]',stats)
    elif type(a) in (int,float) and type(b) in (int,float):
        stats['checked_numeric_values']+=1
        if not math.isfinite(a) or not math.isfinite(b):fail('nonfinite_scientific_number');return
        delta=abs(a-b);stats['maximum_absolute_numeric_difference']=max(stats['maximum_absolute_numeric_difference'],delta)
        if delta!=0.:fail('numeric',difference=delta,left=a,right=b)
    elif type(a) is not type(b) or a!=b:fail('different_value',left=str(a)[:160],right=str(b)[:160])

def statistics():return dict(difference_count=0,differences=[],checked_numeric_values=0,maximum_absolute_numeric_difference=0.)
def differences(a,b):
    stats=statistics();compare_values(a,b,'root',stats);return stats['differences']

def _actual_final_primal(block):
    """Read the recorded actual owner, never an equal-valued candidate."""
    calls=block.get('solver_calls',[]);base=block.get('solver_call_local_start_index',0);proofs=[]
    for segment in block['segments']:
        witness=segment.get('annual_program_actual_witness')
        if not isinstance(witness,dict) or witness.get('status')!='recorded' or witness.get('reasons')!=[]:raise ValueError('Missing/unknown actual final witness')
        owner=witness.get('solver_call_index')
        if type(owner) is not int or not base<=owner<base+len(calls):raise ValueError('Actual witness owner outside block')
        call=calls[owner-base];layers=call.get('solver_backend_layers',[])
        if call.get('phase')!='annual_program_actual_witness' or call.get('status')!='success' or len(layers)!=1 or layers[0].get('layer')!='cost':raise ValueError('Actual final owner is not the original cost record')
        index=witness.get('accepted_attempt_index');attempts=layers[0].get('attempts',[])
        if type(index) is not int or not 0<=index<len(attempts):raise ValueError('Actual accepted attempt owner missing')
        attempt=attempts[index];raw=attempt.get('raw_x');ref=attempt.get('validation',{}).get('rows_ref')
        if attempt.get('status')!=0 or attempt.get('accepted') is not True or not isinstance(raw,list) or not isinstance(ref,dict):raise ValueError('Original actual primal/rows reference missing')
        if ref!=witness.get('rows_ref'):raise ValueError('Actual segment/call row ownership differs')
        raw_sha=hashlib.sha256(json.dumps(raw,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf8')).hexdigest()
        if witness.get('raw_x_sha256')!=raw_sha:raise ValueError('Actual raw vector hash differs')
        proofs.append(dict(owner=owner,accepted_attempt_index=index,raw_x=raw,rows_sha256=ref.get('rows_sha256'),row_count=ref.get('row_count')))
    return proofs

def _load_run(root,verify_journal):
    root=Path(root).resolve();result=read(root/'RESULT.json')
    if (result.get('status')!='completed' or result.get('error') is not None or result.get('source_and_inputs_unchanged') is not True
        or result.get('reset_count')!=1 or result.get('unknown_tail_minutes')!=0):raise ValueError('Only complete unchanged trajectories with one reset may compare')
    metadata={name:read(root/name) for name in ('CASE.json','BINDINGS.json')}
    protected={str(root/name):sha(root/name) for name in ('CASE.json','BINDINGS.json','RESULT.json','evidence/blocks.jsonl','evidence/SUMMARY.json')}
    summary=verify_journal(root/'evidence')
    # Native verify_journal returns the authenticated scan (without status).
    # Its separately verified SUMMARY owns the completion classification.
    sealed=read(root/'evidence/SUMMARY.json')
    if (summary.get('integrity_verified') is not True or sealed.get('status')!='completed'
        or sealed.get('observation',{}).get('complete') is not True
        or summary['observed_until_minute']!=result['observed_until_minute']):raise ValueError('Journal is not completely closed')
    return dict(root=root,result=result,metadata=metadata,protected=protected,summary=summary)

def compare_runs(left,right,verify_journal,source_root=None):
    runs=[_load_run(p,verify_journal) for p in (left,right)];a,b=runs;stats=statistics();ignored=set();bindings=[];segment_count=0;proof_count=0
    if a['metadata']!=b['metadata']:raise ValueError('Source/input/HEAD/case/resolved registration differs')
    cache_contexts=[_cache_source_context(source_root,run['metadata']['BINDINGS.json']) for run in runs]
    for name in RESULT_FIELDS:
        if (name in a['result'])!=(name in b['result']):raise ValueError('Result scientific metadata support differs: '+name)
        if name in a['result']:compare_values(a['result'][name],b['result'][name],'result.'+name,stats)
    for name in SUMMARY_FIELDS:
        compare_values(project(a['summary'][name],'summary.'+name,ignored,cache_contexts[0]),project(b['summary'][name],'summary.'+name,ignored,cache_contexts[1]),'summary.'+name,stats)
    records=[r['summary']['raw_files_sha256'] for r in runs]
    if len(records[0])!=len(records[1]):raise ValueError('Actual block support differs')
    preparation_counts=[0,0]
    for index,pair in enumerate(zip(*records)):
        blocks=[]
        for side,(run,record) in enumerate(zip(runs,pair)):
            path=run['root']/'evidence'/record['file'];before=sha(path)
            if before!=record['sha256']:raise ValueError('Original raw block SHA differs')
            with gzip.open(path,'rt',encoding='utf8') as stream:block=json.load(stream)
            if sha(path)!=before:raise ValueError('Raw block changed during independent comparison')
            bindings.append(dict(path=str(path),sha256=before));blocks.append(block)
            if block.get('initial_preparation') is not None:preparation_counts[side]+=1
        actual=[_actual_final_primal(block) for block in blocks]
        compare_values(actual[0],actual[1],f'block[{index}].actual_final_primal',stats)
        compare_values(project(blocks[0],ignored=ignored,cache_context=cache_contexts[0]),project(blocks[1],ignored=ignored,cache_context=cache_contexts[1]),f'block[{index}]',stats)
        for context in cache_contexts:
            if context is not None:_finish_cache_origins(context)
        segment_count+=len(blocks[0]['segments']);proof_count+=len(actual[0])
    if preparation_counts!=[1,1]:raise ValueError('Preparation must occur once in each complete trajectory')
    for run in runs:
        for path,expected in run['protected'].items():
            if sha(path)!=expected:raise ValueError('Producer metadata changed during comparison')
    return dict(schema='hospital_ies.strict_recovery_physical_comparison.v1',status='exact_match' if stats['difference_count']==0 else 'differences_found',
        left=str(a['root']),right=str(b['root']),**stats,physical_segment_count=segment_count,actual_final_primal_count=proof_count,
        numerical_tolerance=0.,physical_elapsed_fields_retained=True,unknown_elapsed_fields_retained=True,
        ignored_paths=[dict(path=p,reason=r) for p,r in sorted(ignored)],original_preparation_counts=preparation_counts,
        final_reset_counts=[r['result']['reset_count'] for r in runs],file_bindings=bindings,
        physical_solver_calls=0,independent_carrier_balance_qualification=False,checkpoint_recovery_qualification=False,
        scope='Scientific exact comparison only; separate E/steam/cooling and archive/restore authentication remain required')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('left','right','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--source-root','--source',dest='source_root',type=Path,required=True)
    args=parser.parse_args(argv);output=args.output.resolve()
    if any(output.is_relative_to(p.resolve()) for p in (args.left,args.right,args.source_root)):raise ValueError('Comparison output must be outside producers/source')
    if output.exists():raise FileExistsError('Never overwrite a comparison report')
    sys.path[:0]=[str(args.source_root),str(args.source_root/'src')]
    from tools.annual_readiness.journal import verify_journal
    result=compare_runs(args.left,args.right,verify_journal,source_root=args.source_root)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,allow_nan=False,indent=2)
    print(json.dumps({k:result[k] for k in ('status','physical_segment_count','checked_numeric_values','maximum_absolute_numeric_difference','difference_count')}))
    return 0 if result['status']=='exact_match' else 1

if __name__=='__main__':raise SystemExit(main())
