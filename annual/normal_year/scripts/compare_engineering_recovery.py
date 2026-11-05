"""Streaming exact recovery comparison for the new engineering result owner.

Retains all physical elapsed fields, dynamic state, inventories and command
queues. Only the previously reviewed path-specific solver wall and durability
projections are reused. Physical accounting remains a separate required gate.
"""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode=True
from audit_engineering_year import _header, read, sha, digest, safe_path, write, MODEL_ID, OWNER_SCHEMA
from execution_profile import MATCH_SCHEMA
from engineering_receipts import (validated_numerical_profile, validate_actual_owner,
    project_engineering, comparison_policy, comparison_statistics, compare_engineering_values,
    require_critical_service_match, ENGINEERING_MATCH_SCHEMA)


def _projection():
    path=Path(__file__).resolve().parent/'engineering_projection_legacy.py'
    spec=importlib.util.spec_from_file_location('annual_engineering_wall_projection',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def _blocks(producer, identity_sha, summary, numerical_profile=None):
    previous=None; cursor=0.; count=0; segments=0; seen=set(); records=summary['raw_files_sha256']
    with (producer/'evidence/blocks.jsonl').open(encoding='utf-8-sig') as stream:
        for line in stream:
            if not line.endswith('\n'): raise ValueError('Incomplete journal index line')
            record=json.loads(line); relative='blocks/block_'+str(count).zfill(6)+'.json.gz'
            if (record.get('index')!=count or record.get('file')!=relative or record.get('previous_digest')!=previous
                    or record.get('identity_sha256')!=identity_sha
                    or record.get('digest')!=digest({k:v for k,v in record.items() if k!='digest'},newline=True)):
                raise ValueError('New-owner journal index hash/identity chain differs')
            path=safe_path(producer/'evidence',relative)
            if sha(path)!=record.get('sha256') or count>=len(records) or records[count]!=dict(file=relative,sha256=record['sha256']):
                raise ValueError('Original new-owner block SHA differs')
            with gzip.open(path,'rt',encoding='utf8') as raw: block=json.load(raw)
            if block['start_minute']!=cursor or block['end_minute']!=cursor+15. or len(block['segments'])!=record['segment_count']:
                raise ValueError('New-owner block coverage differs')
            for row in block['segments']:
                validate_actual_owner(row, numerical_profile)
            cursor=block['end_minute']; previous=record['digest']; count+=1; segments+=len(block['segments']);seen.add(relative)
            yield block,dict(path=str(path),sha256=record['sha256'])
        actual={p.relative_to(producer/'evidence').as_posix() for p in (producer/'evidence/blocks').iterdir()}
        if count!=summary['block_count'] or segments!=summary['segment_count'] or previous!=summary['last_digest'] or seen!=actual:
            raise ValueError('New-owner journal terminal digest/count/file coverage differs')


def compare_runs(source,left,right):
    source,left,right=[Path(p).resolve() for p in (source,left,right)]
    legacy=_projection(); headers=[_header(source,p) for p in (left,right)]
    profiles=[validated_numerical_profile(h[1],h[0],h[3]) for h in headers]
    if profiles[0]!=profiles[1]: raise ValueError('Recovery runs must use the identical canonical numerical profile')
    numerical=profiles[0]
    stats=legacy.statistics() if numerical is None else comparison_statistics()
    compare_values=legacy.compare_values if numerical is None else compare_engineering_values
    ignored=set(); bindings=[]; preparations=[0,0]; segment_count=0
    def project(value,path,side):
        if numerical is not None:value=project_engineering(value,path,numerical,ignored)
        return legacy.project(value,path,ignored,cache_contexts[side],contexts[side])
    for name in ('CASE.json','BINDINGS.json','RESOLVED_CASE.json'):
        legacy.compare_values(read(left/name),read(right/name),'metadata.'+name,stats)
    fields=tuple(legacy.RESULT_FIELDS)+('execution_model_id','annual_dynamic_owner_contract')
    for name in fields:
        a,b=headers[0][0],headers[1][0]
        if (name in a)!=(name in b): raise ValueError('Result scientific field support differs: '+name)
        if name in a: compare_values(a[name],b[name],'result.'+name,stats)
    if headers[0][-1]!=headers[1][-1]: raise ValueError('Requested physical horizons differ')
    contexts=[legacy._recovery_wall_source_context(source,h[2],side=side) for h,side in zip(headers,('left','right'))]
    cache_contexts=[legacy._cache_source_context(source,h[2]) for h in headers]
    # Native verify_journal exposes observed_until_minute from its authenticated
    # block scan. This header already verifies the identical observation clock.
    summaries=[dict(h[5],observed_until_minute=h[5]['observation']['observed_until_minute']) for h in headers]
    for name in legacy.SUMMARY_FIELDS:
        if any(name not in summary for summary in summaries):
            raise ValueError('Required original scientific SUMMARY field is absent: '+name)
        compare_values(project(summaries[0][name],'summary.'+name,0),
            project(summaries[1][name],'summary.'+name,1),'summary.'+name,stats)
    for index,pair in enumerate(__import__('itertools').zip_longest(
            _blocks(left,headers[0][4],headers[0][5],numerical),_blocks(right,headers[1][4],headers[1][5],numerical))):
        if any(item is None for item in pair): raise ValueError('Recovered block support differs')
        blocks=[item[0] for item in pair]; bindings.extend(item[1] for item in pair)
        projected=[]
        for side,block in enumerate(blocks):
            if block.get('initial_preparation') is not None: preparations[side]+=1
            if contexts[side] is not None: contexts[side]['block_index']=index
            projected.append(project(block,'block',side))
        if numerical is not None:
            if len(blocks[0]['segments'])!=len(blocks[1]['segments']):raise ValueError('Engineering actual segment support differs')
            for a,b in zip(blocks[0]['segments'],blocks[1]['segments']):require_critical_service_match(a,b)
        compare_values(projected[0],projected[1],f'block[{index}]',stats)
        for context in cache_contexts:
            if context is not None:legacy._finish_cache_origins(context)
        segment_count+=len(blocks[0]['segments'])
    for context in contexts:
        if context is not None:legacy._verify_recovery_wall_source(context)
    # Revalidate every source/input and protected metadata pin after reading.
    for producer,prior in zip((left,right),headers):
        if _header(source,producer)!=prior: raise ValueError('Source/input/producer metadata changed during comparison')
    result=dict(schema=MATCH_SCHEMA if numerical is None else ENGINEERING_MATCH_SCHEMA,
        status=('exact_match' if numerical is None else 'engineering_match') if stats['difference_count']==0 else 'differences_found',
        execution_model_id=MODEL_ID,annual_dynamic_owner_contract=OWNER_SCHEMA,left=str(left),right=str(right),**stats,
        numerical_tolerance=0. if numerical is None else comparison_policy(),physical_elapsed_fields_retained=True,unknown_elapsed_fields_retained=True,
        dynamic_state_and_queues_compared=True,physical_segment_count=segment_count,file_bindings=bindings,
        original_preparation_counts=preparations,final_reset_counts=[h[0]['reset_count'] for h in headers],
        legacy_native_primal_qualification=False,independent_carrier_balance_qualification=False,
        checkpoint_recovery_qualification=False,physical_solver_calls=0,
        ignored_paths=[dict(path=p,reason=r) for p,r in sorted(ignored)],
        scope='Exact recorded engineering state/ledger comparison only; typed archive authentication and physical accounts remain separate gates')
    if numerical is not None:
        result.update(numerical_profile=numerical['profile'], numerical_execution=numerical,
            comparison_policy=comparison_policy(),critical_service_compared=True,
            event_and_queue_clocks_strict=True,unregistered_numeric_fields_strict=True,
            scope='Registered numerical recovery comparison; actual P0/P1 service, clocks/discrete state and all unregistered fields remain strict; accounts remain separate')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source','--source-root',dest='source',type=Path,required=True)
    for name in ('left','right','output'): parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args(argv); output=args.output.resolve()
    if output.exists() or any(output.is_relative_to(p.resolve()) for p in (args.source,args.left,args.right)):
        raise ValueError('New comparison output must be outside immutable producer/source trees')
    result=compare_runs(args.source,args.left,args.right); output.parent.mkdir(parents=True,exist_ok=True);write(output,result)
    print(json.dumps({k:result[k] for k in ('status','difference_count','maximum_absolute_numeric_difference')}))
    return 0 if result['status'] in ('exact_match','engineering_match') else 1


if __name__=='__main__': raise SystemExit(main())
