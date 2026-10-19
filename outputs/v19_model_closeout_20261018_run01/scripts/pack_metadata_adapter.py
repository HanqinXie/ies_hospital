"""External V19 exact metadata adapter; no compression search or case filter.

Build is explicit and deferred until root authorizes the final data snapshot.
The existing generic metadata decoder and original V19 scientific proof body
are reused. Importing this file never imports a simulation module.
"""
from __future__ import annotations
import argparse
import ast
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import unicodedata
import zipfile

SCRIPT=Path(__file__).resolve();RUN=SCRIPT.parents[1];ROOT=RUN.parents[1]
CODEC=ROOT/'deliverables/v18_packaging_logs/metadata_codec_r1.py'
CODEC_SHA='9a48289edb52d2332258c6a966bbcf6e3a49cd388b30a8875f3f42cacba949ab'
BUILDER=RUN/'scripts/build_delivery_v19.py'
SCHEMA='hospital_ies.v19.exact_metadata_delivery_adapter.v1'
META_SCHEMA='hospital_ies.v18.delivery_metadata_solid.v1'
MAP='delivery_metadata/DELIVERY_METADATA_MAP.json'
BUNDLE='delivery_metadata/METADATA_ALL_JSON_CSV.tar.xz'
DECODER='delivery_metadata/metadata_codec_r1.py'
REPLACEMENT='delivery_metadata/METADATA_REPLACEMENT.json'
CHUNK=1024**2;LIMIT=200_000_000;MAX_ENTRIES=20_000;MAX_MAP=16*1024**2


def utc():return datetime.now(timezone.utc).isoformat()
def encoded(value):return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(CHUNK),b''):h.update(block)
    return h.hexdigest()
def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write_new(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(value if isinstance(value,bytes) else encoded(value))
def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
def output_path(path):
    path=Path(path)
    if not path.is_absolute():raise ValueError('Explicit absolute output path required')
    resolved=path.resolve()
    if os.name=='nt' and (path.drive.lower() not in ('d:','e:') or resolved.drive.lower() not in ('d:','e:')):raise ValueError('Declared and resolved output must use D/E')
    return resolved


def safe(name):
    if not isinstance(name,str) or not name or name.startswith(('/','\\')) or '\\' in name or any(c in name for c in ':<>"|?*') or any(ord(c)<32 for c in name):raise ValueError('Unsafe archive member')
    for part in name.split('/'):
        if part in ('','.','..') or part!=part.rstrip(' .') or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])',part.split('.')[0],re.I):raise ValueError('Unsafe Windows member')
    return name
def unique(names):
    keys=[unicodedata.normalize('NFC',safe(n)).casefold() for n in names]
    if len(keys)!=len(set(keys)):raise ValueError('Aliased or duplicate archive members')
    found=set(keys)
    if any('/'.join(k.split('/')[:i]) in found for k in keys for i in range(1,len(k.split('/')))):raise ValueError('File/parent conflict')
def identity(row):return {k:row[k] for k in ('member','bytes','sha256')}
def validate_identity(row):
    if type(row.get('bytes')) is not int or row['bytes']<0 or not re.fullmatch('[0-9a-f]{64}',str(row.get('sha256',''))):raise ValueError('Invalid byte/SHA identity')
def same_census(rows,wanted):
    unique([r['member'] for r in rows]);unique([r['member'] for r in wanted])
    if {r['member']:(r['bytes'],r['sha256']) for r in rows}!={r['member']:(r['bytes'],r['sha256']) for r in wanted}:raise ValueError('Logical census does not equal original selection')


def verify_outer(archive,entries,*,limit=LIMIT):
    archive=Path(archive);unique([r['member'] for r in entries]);expected={r['member']:r for r in entries}
    if archive.stat().st_size>=limit:raise ValueError('Actual single ZIP violates strict byte cap')
    verified=[]
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();unique([i.filename for i in infos])
        if {i.filename for i in infos}!=set(expected):raise ValueError('Actual outer ZIP census mismatch')
        for info in infos:
            row=expected[info.filename];validate_identity(row)
            kind=stat.S_IFMT(info.external_attr>>16)
            if info.is_dir() or kind not in (0,stat.S_IFREG) or info.flag_bits&1 or info.external_attr&0x400 or info.file_size!=row['bytes']:raise ValueError('Invalid outer ZIP member header')
            h=hashlib.sha256();size=0
            with z.open(info) as stream:
                for block in iter(lambda:stream.read(CHUNK),b''):h.update(block);size+=len(block)
            if size!=row['bytes'] or h.hexdigest()!=row['sha256']:raise ValueError('Outer ZIP member actual SHA/size mismatch')
            verified.append(dict(identity(row),crc32=format(info.CRC,'08x'),compressed_bytes=info.compress_size,crc_verified=True))
    return dict(archive_bytes=archive.stat().st_size,archive_sha256=sha(archive),members=verified,every_member_sha_size_crc_verified=True)


def _stream_file(row,sink):
    path=Path(row['source_path'])
    if path.is_symlink() or not path.is_file() or getattr(path.stat(),'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):raise ValueError('Selected source is missing or a link')
    before=path.stat();h=hashlib.sha256();size=0
    with path.open('rb') as f:
        for block in iter(lambda:f.read(CHUNK),b''):sink.write(block);h.update(block);size+=len(block)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or size!=row['bytes'] or row.get('sha256') not in (None,h.hexdigest()):raise ValueError('Selected source changed or SHA differs')
    row['sha256']=h.hexdigest()


class HashSource:
    def __init__(self,source):self.source=source;self.h=hashlib.sha256();self.size=0
    def read(self,n):
        data=self.source.read(n);self.h.update(data);self.size+=len(data);return data


def encode_metadata(rows,generated,work):
    """Same numeric USTAR and fixed XZ parameters as the verified V18 writer."""
    if not rows or len(rows)>MAX_ENTRIES:raise ValueError('Metadata count exceeds existing finite codec cap')
    bundle=work/Path(BUNDLE).name;mapping=work/Path(MAP).name;entries=[]
    with bundle.open('xb') as output,lzma.LZMAFile(output,'w',format=lzma.FORMAT_XZ,check=lzma.CHECK_CRC64,
            filters=[dict(id=lzma.FILTER_LZMA2,preset=6,dict_size=64*1024**2)]) as compressed:
        with tarfile.open(fileobj=compressed,mode='w|',format=tarfile.USTAR_FORMAT) as tar:
            for index,row in enumerate(rows):
                name='metadata/%08d.bin'%index;info=tarfile.TarInfo(name);info.size=row['bytes'];info.mode=0o644;info.mtime=0
                source=io.BytesIO(generated[row['member']]) if row.get('source_path') is None else Path(row['source_path']).open('rb')
                before=None if row.get('source_path') is None else Path(row['source_path']).stat()
                with source:
                    hashed=HashSource(source);tar.addfile(info,hashed)
                    if source.read(1) or hashed.size!=row['bytes'] or row.get('sha256') not in (None,hashed.h.hexdigest()):raise ValueError('Metadata source identity changed')
                if before is not None:
                    after=Path(row['source_path']).stat()
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Metadata source changed during encoding')
                row['sha256']=hashed.h.hexdigest();entries.append(dict(original_path=row['member'],member=name,bytes=row['bytes'],sha256=row['sha256']))
    value=dict(schema=META_SCHEMA,bundle_path=bundle.name,bundle_bytes=bundle.stat().st_size,bundle_sha256=sha(bundle),
        entries=entries,total_entries=len(entries),decoded_bytes=sum(r['bytes'] for r in entries),
        compression=dict(container='USTAR',XZ_single_stream=True,check='CRC64',preset=6,dictionary_bytes=64*1024**2),
        selection_rule='Every selected/generated JSON or CSV; no scenario/status filter',scientific_content_omitted=False)
    if len(encoded(value))>MAX_MAP:raise ValueError('Metadata map exceeds existing finite codec cap')
    write_new(mapping,value)
    codec=module(CODEC,'unchanged_metadata_codec_build')
    result=codec.verify_metadata_api(mapping,bundle,{r['member']:dict(bytes=r['bytes'],sha256=r['sha256']) for r in rows})
    return mapping,bundle,value,{k:v for k,v in result.items() if k!='captured'}


def finalized_raw_ledger(ledger,logical):
    result=copy.deepcopy(ledger);identities={r['member']:r for r in logical}
    for row in result:
        if row.get('selected'):
            actual=identities.get(row['path'])
            if actual is None:raise ValueError('Selected raw ledger path absent from actual logical census')
            validate_identity(actual)
            if row.get('bytes') is not None and row['bytes']!=actual['bytes']:raise ValueError('Selected raw ledger size differs from actual stream')
            row['sha256']=actual['sha256'];row['sha_basis']='verified while streaming selected whole raw into ZIP'
    return result


def pack_selected(entries,generated,archive,work,*,limit=LIMIT,postcheck=None):
    """Generic synthetic/explicit-selection core; CLI supplies V19 admission."""
    archive=output_path(archive);work=output_path(work);building=archive.with_suffix('.building.zip')
    if archive.exists() or building.exists() or work.exists():raise FileExistsError('Fresh archive/work paths required')
    rows=[copy.deepcopy(r) for r in entries];generated=dict(generated)
    if any(name in generated for name in (MAP,BUNDLE,DECODER,REPLACEMENT)) or any(r['member'] in (MAP,BUNDLE,DECODER,REPLACEMENT) for r in rows):raise ValueError('Reserved metadata wrapper collision')
    unique([r['member'] for r in rows]+list(generated))
    if sha(CODEC)!=CODEC_SHA:raise ValueError('Original generic metadata codec SHA changed')
    rows.append(dict(member=DECODER,source_path=str(CODEC),bytes=CODEC.stat().st_size,sha256=CODEC_SHA,scientific_raw=False,compression='deflate6',reason='exact unchanged storage-only decoder supplement'))
    work.mkdir(parents=True);archive.parent.mkdir(parents=True,exist_ok=True);started=utc()
    try:
        direct=[r for r in rows if Path(r['member']).suffix.lower() not in ('.json','.csv')]
        metadata=[r for r in rows if r not in direct]
        outer=[]
        with zipfile.ZipFile(building,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
            for row in direct:
                info=zipfile.ZipInfo(row['member']);info.compress_type=zipfile.ZIP_STORED if row.get('compression')=='stored' or row.get('scientific_raw') else zipfile.ZIP_DEFLATED;info._compresslevel=6;info.external_attr=(stat.S_IFREG|0o644)<<16
                with z.open(info,'w',force_zip64=True) as sink:_stream_file(row,sink)
                outer.append(copy.deepcopy(row))
                if z.fp.tell()>=limit:raise ValueError('Strict cap reached; unfinished archive retained')
            if 'delivery_metadata/NEW_RAW_LEDGER.json' in generated:
                ledger=json.loads(generated['delivery_metadata/NEW_RAW_LEDGER.json'])
                ledger['entries']=finalized_raw_ledger(ledger['entries'],direct)
                generated['delivery_metadata/NEW_RAW_LEDGER.json']=encoded(ledger)
            for name,data in generated.items():
                if not isinstance(data,bytes):raise TypeError('Generated members must be exact bytes')
                metadata.append(dict(member=name,source_path=None,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),scientific_raw=False))
            if any(Path(r['member']).suffix.lower() not in ('.json','.csv') for r in metadata):raise ValueError('Generated adapter input must be JSON/CSV metadata')
            mapping,bundle,map_value,meta_verification=encode_metadata(metadata,generated,work)
            logical=[identity(r) for r in [*direct,*metadata]]
            replacement=dict(schema=SCHEMA+'.replacement',logical_entries=logical,metadata_map_sha256=sha(mapping),metadata_bundle_sha256=sha(bundle),
                metadata_replaced_entries=len(metadata),no_scientific_case_filtering=True,all_original_generated_members_preserved=True)
            wrapper=[(MAP,mapping.read_bytes()),(BUNDLE,None),(REPLACEMENT,encoded(replacement))]
            for name,data in wrapper:
                if data is None:
                    info=zipfile.ZipInfo(name);info.compress_type=zipfile.ZIP_STORED;info.external_attr=(stat.S_IFREG|0o644)<<16
                    row=dict(member=name,bytes=bundle.stat().st_size,sha256=sha(bundle),source_path=str(bundle),compression='stored')
                    with z.open(info,'w',force_zip64=True) as sink:_stream_file(row,sink)
                else:
                    z.writestr(name,data);row=dict(member=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),source_path=None)
                outer.append(row)
        result=verify_outer(building,outer,limit=limit)
        if postcheck:postcheck()
        selection=dict(schema=SCHEMA+'.selection',entries=outer,logical_entries=logical,metadata_replaced_entries=len(metadata),metadata_decoded_bytes=map_value['decoded_bytes'],
            metadata_verification=meta_verification,adapter_sha256=sha(SCRIPT),codec_sha256=CODEC_SHA,archive_bytes=result['archive_bytes'],archive_sha256=result['archive_sha256'],
            all_selected_nonraw_preserved=True,scientific_case_filtering=False,started_utc=started,finished_utc=utc())
        write_new(archive.with_suffix('.FILE_SELECTION.json'),selection)
        result.update(exhaustive_file_selection_sha256=sha(archive.with_suffix('.FILE_SELECTION.json')),all_selected_nonraw_preserved=True,metadata_verified=True)
        write_new(archive.with_suffix('.VERIFICATION.json'),result)
        building.rename(archive);write_new(archive.with_suffix('.sha256'),(result['archive_sha256']+'  '+archive.name+'\n').encode())
        return dict(status='built_storage_only_not_standalone_certified',archive_bytes=result['archive_bytes'],archive_sha256=result['archive_sha256'],logical_entries=len(logical),metadata_entries=len(metadata))
    except Exception as error:
        path=archive.with_suffix('.FAILED.json')
        if not path.exists():write_new(path,dict(schema=SCHEMA+'.failure',status='failed_unknown',started_utc=started,finished_utc=utc(),error_type=type(error).__name__,message=str(error),partial_archive=str(building),partial_artifact_retained=True))
        raise


def scientific_stage(builder,stage,receipt,logical):
    """Compile the delivered builder's existing post-extraction proof body."""
    tree=ast.parse(Path(builder.__file__).read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='standalone')
    body=next(n.body for n in function.body if isinstance(n,ast.Try))
    begin=next(i for i,n in enumerate(body) if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='env')
    end=next(i for i,n in enumerate(body[begin:],begin) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='write_new')
    nodes=copy.deepcopy(body[begin:end]);dump=ast.dump(ast.Module(body=nodes,type_ignores=[]),include_attributes=False)
    for required in ('reproduction_proof_spec','verify_reproduction_proof','scientific_reproduction_proof_verified','exit_code','source_binding_exact'):
        if required not in dump:raise ValueError('Delivered builder scientific proof body changed; review required')
    wrapper=ast.parse('def reused_scientific_body(stage, receipt, expected):\n    pass\n').body[0]
    wrapper.body=nodes+[ast.Return(value=ast.Name(id='receipt',ctx=ast.Load()))]
    compiled=ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[]));namespace=dict(vars(builder));exec(compile(compiled,str(builder.__file__),'exec'),namespace)
    result=namespace['reused_scientific_body'](stage,receipt,logical)
    result['scientific_body_AST_sha256']=hashlib.sha256(dump.encode()).hexdigest();result['delivered_builder_sha256']=sha(builder.__file__)
    return result


def install_scientific_guard(builder,workspace,receipt):
    """Wrap existing child launcher; preserve its command/proof semantics."""
    original=builder._child;workspace=Path(workspace).resolve()
    guard='''import hashlib,json,os,sys
from pathlib import Path
forbidden=Path(os.environ['V19_ORIGINAL_WORKSPACE']).resolve()
def deny_original(event,args):
    if event in ('open','os.listdir','os.scandir') and args and not isinstance(args[0],int):
        path=Path(os.fsdecode(args[0])).resolve()
        if path.is_relative_to(forbidden):
            raise PermissionError('Original workspace access denied: '+str(path))
sys.addaudithook(deny_original)
Path(os.environ['V19_GUARD_MARKER']).write_text(json.dumps({'guard_file':__file__,'guard_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'active':True}),encoding='utf-8')
'''
    data=guard.encode('utf-8');guard_sha=hashlib.sha256(data).hexdigest()
    receipt['scientific_child_guards']=[]
    def guarded(stage,argv,label,env,*,command=None):
        root=builder.command_root(stage,command or {});path=root/'sitecustomize.py'
        if path.exists() and path.read_bytes()!=data:raise ValueError('Staged sitecustomize collision; will not overwrite delivered bytes')
        if not path.exists():write_new(path,data)
        temporary=root/'delivery_verification_temp';temporary.mkdir(exist_ok=True)
        marker=temporary/(label+'.guard.json')
        if marker.exists():raise FileExistsError('Fresh child guard marker required')
        environment=env.copy();environment.update(V19_ORIGINAL_WORKSPACE=str(workspace),V19_GUARD_MARKER=str(marker),PYTHONDONTWRITEBYTECODE='1')
        result=original(stage,argv,label,environment,command=command)
        checked=load(marker) if marker.is_file() else {}
        valid=checked.get('active') is True and checked.get('guard_sha256')==guard_sha and Path(checked.get('guard_file','')).resolve()==path.resolve()
        receipt['scientific_child_guards'].append(dict(label=label,guard_marker_verified=valid,guard_sha256=guard_sha,
            stage_guard_path=str(path),actual_exit_code=result['exit_code'],stdout_sha256=result['stdout_sha256'],stderr_sha256=result['stderr_sha256']))
        if not valid:raise ValueError('Child workspace guard was not actually imported; scientific result uncertified')
        return result
    builder._child=guarded
    receipt['workspace_guard_scope']='Python audit open/listdir/scandir guard, exercised per child; not an adversarial OS sandbox'


def restore_archive(archive,stage,*,original_workspace=ROOT,storage_only=False):
    archive=Path(archive).resolve();stage=output_path(stage);workspace=Path(original_workspace).resolve()
    if stage.exists():raise FileExistsError('Fresh standalone stage required')
    if stage.is_relative_to(workspace) or workspace.is_relative_to(stage):raise ValueError('Standalone stage must be outside original workspace')
    selection_path=archive.with_suffix('.FILE_SELECTION.json');verification_path=archive.with_suffix('.VERIFICATION.json')
    selection=load(selection_path);prior=load(verification_path)
    if prior.get('exhaustive_file_selection_sha256')!=sha(selection_path) or prior.get('archive_sha256')!=sha(archive) or selection.get('archive_sha256')!=prior['archive_sha256']:raise ValueError('Archive/selection/verification binding mismatch')
    actual=verify_outer(archive,selection['entries'])
    outer={r['member']:r for r in selection['entries']}
    if actual['members']!=prior['members']:raise ValueError('Outer actual verification ledger differs')
    if any(name not in outer for name in (MAP,BUNDLE,DECODER,REPLACEMENT)):raise ValueError('Metadata wrapper incomplete')
    if outer[DECODER]['sha256']!=CODEC_SHA:raise ValueError('Delivered generic decoder identity differs')
    parent=next(p for p in stage.parents if p.exists())
    if shutil.disk_usage(parent).free<sum(r['bytes'] for r in selection['entries'])+512*1024**2:raise OSError('Insufficient fresh extraction capacity')
    stage.mkdir(parents=True);receipt=dict(schema=SCHEMA+'.standalone',status='started',archive_sha256=actual['archive_sha256'],stage=str(stage),
        all_outer_members_sha_size_crc_verified=True,actual_scientific_reproduction_verified=False,original_workspace_imports_or_scientific_reads=0)
    receipt_path=archive.with_suffix('.STANDALONE_VERIFICATION.json')
    if receipt_path.exists():raise FileExistsError('Standalone attempt receipt already exists')
    try:
        with zipfile.ZipFile(archive) as z:
            for row in selection['entries']:
                target=stage.joinpath(*safe(row['member']).split('/'));target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(row['member']) as source,target.open('xb') as output:shutil.copyfileobj(source,output,CHUNK)
                if target.stat().st_size!=row['bytes'] or sha(target)!=row['sha256']:raise ValueError('Extracted outer identity mismatch')
        codec=module(stage/DECODER,'delivered_generic_metadata_decoder')
        if Path(codec.__file__).resolve()!=(stage/DECODER).resolve() or sha(codec.__file__)!=outer[DECODER]['sha256']:raise ValueError('Actual decoder import escaped staged delivered identity')
        receipt.update(decoder_import_file=str(Path(codec.__file__).resolve()),decoder_import_sha256=sha(codec.__file__))
        mapping=codec.load_map(stage/MAP,stage/BUNDLE)
        replacement=load(stage/REPLACEMENT);metadata={r['original_path']:dict(bytes=r['bytes'],sha256=r['sha256']) for r in mapping['entries']}
        logical=[identity(r) for r in selection['entries'] if r['member'] not in (MAP,BUNDLE,REPLACEMENT)]
        logical += [dict(member=name,**row) for name,row in metadata.items()]
        same_census(logical,selection['logical_entries']);same_census(logical,replacement['logical_entries'])
        if replacement.get('metadata_map_sha256')!=sha(stage/MAP) or replacement.get('metadata_bundle_sha256')!=sha(stage/BUNDLE):raise ValueError('Replacement wrapper binding differs')
        if shutil.disk_usage(stage).free<mapping['decoded_bytes']+512*1024**2:raise OSError('Insufficient exact metadata restoration capacity')
        result=codec.verify_metadata_api(stage/MAP,stage/BUNDLE,metadata,destination=stage)
        for row in logical:
            target=stage.joinpath(*row['member'].split('/'))
            if not target.is_file() or target.stat().st_size!=row['bytes'] or sha(target)!=row['sha256']:raise ValueError('Restored logical file SHA/size mismatch')
        receipt.update(all_logical_files_sha_size_verified=True,logical_file_count=len(logical),metadata_restoration={k:v for k,v in result.items() if k!='captured'})
        if storage_only:receipt.update(status='storage_only_scientific_reproduction_unexecuted')
        else:
            delivered=stage/BUILDER.relative_to(ROOT)
            expected=next((r for r in logical if r['member']==BUILDER.relative_to(ROOT).as_posix()),None)
            if not expected or sha(delivered)!=expected['sha256']:raise ValueError('Original V19 builder not exactly delivered')
            builder=module(delivered,'delivered_v19_builder_scientific_proofs')
            if Path(builder.__file__).resolve()!=delivered.resolve() or sha(builder.__file__)!=expected['sha256']:raise ValueError('Actual builder import escaped staged delivered identity')
            # Retain the original _child prohibition on original-workspace argv.
            builder.ROOT=workspace
            receipt.update(builder_import_file=str(Path(builder.__file__).resolve()),builder_import_sha256=sha(builder.__file__))
            install_scientific_guard(builder,workspace,receipt)
            receipt=scientific_stage(builder,stage,receipt,logical)
        write_new(receipt_path,receipt);return receipt
    except Exception as error:
        receipt.update(status='failed_unknown',error_type=type(error).__name__,message=str(error),stage_retained=True)
        if not receipt_path.exists():write_new(receipt_path,receipt)
        raise


def build(args):
    producer=module(BUILDER,'v19_ordinary_selection_authority')
    archive=producer.destination(args.destination,archive=True)
    value=producer.assemble(args.representatives,args.freeze,final=True)
    generated=dict(value['generated']);commands=producer.read(producer.local(args.repro_commands)) if args.repro_commands else dict(commands=[],actual_reproduction_not_yet_verified=True)
    generated['delivery_metadata/REPRO_COMMANDS.json']=producer.encoded(commands)
    source=dict(value['source_binding']);builder_sha=sha(BUILDER);adapter_sha=sha(SCRIPT)
    def postcheck():
        if producer.live_binding()!=source or sha(BUILDER)!=builder_sha or sha(SCRIPT)!=adapter_sha:raise ValueError('Source/builder/adapter changed during build')
    result=pack_selected(list(value['entries'].values()),generated,archive,args.work,postcheck=postcheck)
    selection=load(archive.with_suffix('.FILE_SELECTION.json'))
    finalized=finalized_raw_ledger(value['raw_ledger'],selection['logical_entries'])
    producer.write_new(archive.with_suffix('.OMITTED.json'),dict(entries=value['omissions'],raw_ledger=finalized,outcomes_not_filtered=True))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--destination',type=Path,required=True);b.add_argument('--work',type=Path,required=True)
    b.add_argument('--representatives',type=Path,required=True);b.add_argument('--freeze',type=Path,required=True);b.add_argument('--repro-commands',type=Path)
    v=sub.add_parser('verify');v.add_argument('--archive',type=Path,required=True);v.add_argument('--destination',type=Path,required=True);v.add_argument('--storage-only',action='store_true')
    a=p.parse_args()
    if os.name=='nt':
        for target in ([a.destination,a.work] if a.command=='build' else [a.destination]):
            output_path(target)
    if a.command=='build':
        expected=(RUN/'delivery_builds').resolve()
        if a.work.resolve()==expected or not a.work.resolve().is_relative_to(expected):raise ValueError('Work must be a fresh B19/delivery_builds child, excluded from future census')
        result=build(a)
    else:result=restore_archive(a.archive,a.destination,storage_only=a.storage_only)
    print(json.dumps(result,ensure_ascii=True));return 0


if __name__=='__main__':raise SystemExit(main())
