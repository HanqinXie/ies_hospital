"""Opt-in incremental, immutable post-ACK recovery into a new producer.

Only the two existing append-only SQLite schemas are supported. Snapshot
publication reads new rows/raw members, never the previously archived prefix.
Restore performs the expensive complete authentication once. Original attempts
are never truncated, repaired, overwritten, or claimed to be the new branch.
"""
from __future__ import annotations
import argparse,base64,copy,gzip,hashlib,json,math,os,re,sqlite3,time,uuid
from pathlib import Path,PurePosixPath
from . import checkpoint
from . import journal as resume_journal
from . import disk_history,domain_catalogue
from tools.annual_readiness import journal as native_journal

SCHEMA='hospital_ies.annual_program.incremental_recovery.v1'
COMPRESSION_LEVEL=1
METADATA=('CASE.json','BINDINGS.json','ANNUAL_ACCESS_CONTEXT.json','ORCHESTRATION_FREEZE.json',
          'ATTEMPTS.jsonl','RESOLVED_CASE.json','RECOVERY_ORIGIN.json')
DISK_SQL=(
 'CREATE TABLE header (id INTEGER PRIMARY KEY CHECK(id=1), schema TEXT NOT NULL, identity BLOB NOT NULL, identity_sha TEXT NOT NULL, prefix_count INTEGER NOT NULL, version INTEGER NOT NULL, last_digest TEXT)',
 'CREATE TABLE entries (idx INTEGER PRIMARY KEY, payload BLOB NOT NULL, payload_sha TEXT NOT NULL, previous_digest TEXT, digest TEXT NOT NULL)')
CAT_SQL=(domain_catalogue._HEADER_SQL,domain_catalogue._ENTRIES_SQL)

def _json(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf8')
def _digest(v):return hashlib.sha256(_json(v)).hexdigest()
def _sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def _read(p):return json.loads(Path(p).read_text('utf8'))
def _sealed(v):return dict(v,digest=_digest(v))
def _check(v):
 if type(v) is not dict or v.get('digest')!=_digest({k:x for k,x in v.items() if k!='digest'}):raise ValueError('Recovery receipt integrity mismatch')
 return v
def _root(p):
 original=Path(p).absolute();result=original.resolve()
 if original.is_symlink() or original!=result:raise ValueError('Recovery path has a symlink or noncanonical alias')
 return result
def _under(root,name):
 rel=PurePosixPath(name) if type(name) is str else None
 if rel is None or rel.is_absolute() or '..' in rel.parts or str(rel)!=name or '\\' in name:raise ValueError('Invalid recovery member path')
 p=Path(root)/name
 if any(part.is_symlink() for part in (p,*p.parents) if part!=Path(root).parent):raise ValueError('Symlinked recovery member')
 if not p.resolve().is_relative_to(Path(root).resolve()):raise ValueError('Recovery member escaped its root')
 return p
def _write(p,data):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def _atomic(p,data):
 p=Path(p);temporary=p.with_name(p.name+'.prepare-'+uuid.uuid4().hex)
 _write(temporary,data);os.replace(temporary,p)
def _ref(vault,p):return {'file':Path(p).relative_to(vault).as_posix(),'sha256':_sha(p),'bytes':Path(p).stat().st_size}
def _member(vault,ref):
 if type(ref) is not dict or set(ref)!={'file','sha256','bytes'} or type(ref['bytes']) is not int or ref['bytes']<0:raise ValueError('Invalid recovery member receipt')
 p=_under(vault,ref['file'])
 if not p.is_file() or p.stat().st_size!=ref['bytes'] or _sha(p)!=ref['sha256']:raise ValueError('Missing or corrupt recovery member')
 return p
def _sql_pack(row):
 return [{'bytes':base64.b64encode(x).decode('ascii')} if type(x) is bytes else x for x in row]
def _sql_unpack(row):
 if type(row) is not list:raise ValueError('Invalid SQL archive row')
 result=[]
 for x in row:
  if type(x) is dict:
   if set(x)!={'bytes'}:raise ValueError('Unknown SQL archive value')
   x=base64.b64decode(x['bytes'],validate=True)
  elif type(x) not in (str,int,float) and x is not None:raise ValueError('Unknown SQL archive scalar')
  if type(x) is float and not math.isfinite(x):raise ValueError('Nonfinite SQL archive scalar')
  result.append(x)
 return result
def _schema(connection):
 tables=dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
 if set(tables)!={'header','entries'} or connection.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:raise ValueError('Unsupported recovery database schema')
 actual=(tables['header'],tables['entries'])
 if actual==DISK_SQL:return 'history','idx',DISK_SQL
 if actual==CAT_SQL:return 'catalogue','seq',CAT_SQL
 raise ValueError('Only the fixed history/catalogue schemas can be archived')
def _header_count(kind,header):
 if len(header)!=7 or header[0]!=1:raise ValueError('Invalid recovery SQL header')
 count=header[4] if kind=='history' else header[5]
 if type(count) is not int or count<0:raise ValueError('Invalid recovery cursor')
 return count
def _expected_header(kind,state):
 if kind=='history':return (1,disk_history.SCHEMA,disk_history._json(state['identity']),state['identity_sha256'],state['prefix_count'],state['version'],state['last_digest'])
 return (1,domain_catalogue.SCHEMA,domain_catalogue._json(state['identity']),state['identity_sha256'],state['version'],state['count'],state['head_digest'])
def _database_states(state):
 result={}
 for name,saved in state.get('history_states',{}).items():result[saved['path']]=saved
 domain=state.get('domain_store')
 if domain is not None:
  result[domain['catalogue_state']['path']]=domain['catalogue_state']
  result[domain['proof_history_state']['path']]=domain['proof_history_state']
 return result

def latest_snapshot(vault,snapshot='latest'):
 vault=_root(vault)
 registration=_check(_read(vault/'VAULT.json'))
 if snapshot=='latest':
  pointer=_check(_read(vault/'LATEST.json'));snapshot=pointer['snapshot']
 else:pointer=None
 directory=_under(vault,snapshot);commit_path=directory/'COMMIT.json'
 commit=_check(_read(commit_path))
 if commit.get('schema')!=SCHEMA+'.commit' or commit.get('snapshot')!=snapshot:raise ValueError('Incomplete recovery COMMIT')
 if pointer is not None and pointer['commit_sha256']!=_sha(commit_path):raise ValueError('LATEST points to another recovery COMMIT')
 manifest=_read(_member(vault,commit['manifest']))
 if manifest.get('schema')!=SCHEMA+'.manifest' or manifest.get('binding_sha256')!=registration['binding_sha256']:raise ValueError('Recovery manifest binding differs')
 return {'snapshot':snapshot,'manifest':manifest,'commit':commit,'commit_sha256':_sha(commit_path)}

class RecoveryArchive:
 def __init__(self,vault,output,binding):
  self.vault=_root(vault);self.output=_root(output);self.binding=copy.deepcopy(binding)
  if self.vault.is_relative_to(self.output) or self.output.is_relative_to(self.vault):raise ValueError('Recovery vault and producer must be separate trees')
  self.vault.mkdir(parents=True,exist_ok=True)
  registration=self.vault/'VAULT.json'
  if registration.exists():
   value=_check(_read(registration))
   if value['binding_sha256']!=_digest(binding):raise ValueError('Recovery vault belongs to another source/input/configuration')
  else:_write(registration,_json(_sealed({'schema':SCHEMA+'.vault','binding_sha256':_digest(binding),'binding':binding})))
  self._lock=(self.vault/'WRITER.lock').open('a+b')
  if not self._lock.tell():self._lock.write(b'0');self._lock.flush()
  self._lock.seek(0)
  try:
   if os.name=='nt':
    import msvcrt;msvcrt.locking(self._lock.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl;fcntl.flock(self._lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BaseException:self._lock.close();raise
  self.previous=None
  if (self.vault/'LATEST.json').exists():
   last=latest_snapshot(self.vault)
   if last['manifest']['source_output']==str(self.output):self.previous=last
   else:
    origin=_check(_read(self.output/'RECOVERY_ORIGIN.json'))
    if origin['snapshot']!=last['snapshot'] or origin['commit_sha256']!=last['commit_sha256']:raise ValueError('New recovery generation has no exact original snapshot')
  self.generation=(self.previous['manifest']['generation'] if self.previous else uuid.uuid4().hex)
 def close(self):
  if not self._lock.closed:self._lock.close()
 def __del__(self):
  if hasattr(self,'_lock'):self.close()
 def _object(self,source,expected=None,*,mutable=False):
  source=_root(source)
  if not source.is_file():raise ValueError('Missing recovery source member')
  hashed=_sha(source)
  if expected is not None and hashed!=expected:raise ValueError('Acknowledged raw bytes changed before archive')
  target=self.vault/'objects'/hashed;target.parent.mkdir(exist_ok=True)
  if not target.exists():
   if mutable:_write(target,source.read_bytes())
   else:os.link(source,target)
   if _sha(target)!=hashed:raise ValueError('Archived hardlink differs from source')
  if target.stat().st_size!=source.stat().st_size or target.is_symlink():raise ValueError('Corrupt immutable recovery object')
  return {'file':target.relative_to(self.vault).as_posix(),'sha256':hashed,'bytes':source.stat().st_size}
 def capture(self,document,journal):
  started=time.perf_counter()
  checkpoint._validate(document,self.binding)
  live=resume_journal.recovery_receipt(journal)
  state=checkpoint._decode(document['runner_state'])
  receipt=state['journal_receipt']
  if any(receipt[k]!=live[k] for k in ('block_count','observed_until_minute','last_digest','index_bytes','identity_sha256')) or document['minute']!=live['observed_until_minute']:raise ValueError('Runtime checkpoint differs from its acknowledged journal')
  previous=self.previous['manifest'] if self.previous else None
  start=previous['block_count'] if previous else 0;offset=previous['journal_receipt']['index_bytes'] if previous else 0
  if type(start) is not int or start<0 or live['block_count']<=start:raise ValueError('Recovery snapshots must advance exact nonnegative cursors')
  relative='snapshots/'+self.generation+'_'+format(live['block_count'],'09d')
  directory=self.vault/relative;directory.mkdir(parents=True,exist_ok=False)
  raw=[];records=[];prior=previous['journal_receipt']['last_digest'] if previous else None
  with (self.output/'evidence/blocks.jsonl').open('rb') as index:
   index.seek(offset)
   for line in index:
    record=json.loads(line)
    if record['index']!=start+len(records) or record['previous_digest']!=prior or record['digest']!=native_journal._record_digest(record):raise ValueError('New recovery journal interval is not consecutive')
    prior=record['digest'];records.append(record)
    path=_under(self.output,'evidence/'+record['file']);raw.append({'path':'evidence/'+record['file'],'object':self._object(path,record['sha256'])})
    block=json.loads(gzip.decompress(path.read_bytes()));sidecar=block.get('solver_row_sidecar')
    if sidecar:
     p=_root(sidecar['path'])
     if p.parent!=self.output:raise ValueError('Acknowledged sidecar belongs to another producer')
     raw.append({'path':p.name,'object':self._object(p,sidecar['gzip_sha256'])})
   if index.tell()!=live['index_bytes']:raise ValueError('Journal advanced during recovery snapshot')
  if len(records)!=live['block_count']-start or prior!=live['last_digest']:raise ValueError('Incomplete recovery journal interval')
  old_dbs={d['path']:d for d in previous['databases']} if previous else {}
  databases=[]
  for number,(name,saved) in enumerate(_database_states(state).items()):
   source=_root(name)
   if not source.is_relative_to(self.output):raise ValueError('Database escaped its producer')
   relative_db=source.relative_to(self.output).as_posix();old=old_dbs.get(relative_db);cursor=old['end'] if old else 0
   if type(cursor) is not int or cursor<0:raise ValueError('Invalid previous recovery SQL cursor')
   destination=directory/f'database_{number:03d}.jsonl.gz'
   with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,isolation_level=None) as connection:
    connection.execute('BEGIN');kind,key,sql=_schema(connection);headers=connection.execute('SELECT * FROM header').fetchall()
    if len(headers)!=1:raise ValueError('Invalid recovery SQL header count')
    header=headers[0];end=_header_count(kind,header)
    if header!=_expected_header(kind,saved):raise ValueError('Database identity/version/header differs from complete runtime snapshot')
    if end<cursor or end!=saved.get('prefix_count',saved.get('count')):raise ValueError('Checkpoint/database committed count differs')
    last=connection.execute(f'SELECT {key} FROM entries ORDER BY {key} DESC LIMIT 1').fetchone()
    if last!=(end-1,) and not (end==0 and last is None):raise ValueError('Extra or missing committed SQLite row')
    with destination.open('xb') as out:
     with gzip.GzipFile(filename='',fileobj=out,mode='wb',mtime=0,compresslevel=COMPRESSION_LEVEL) as zipped:
      zipped.write(_json({'schema':SCHEMA+'.sql','kind':kind,'start':cursor,'end':end,'header':_sql_pack(header)})+b'\n')
      count=cursor
      for row in connection.execute(f'SELECT * FROM entries WHERE {key}>=? ORDER BY {key}',(cursor,)):
       if row[0]!=count:raise ValueError('Missing recovery database delta row')
       zipped.write(_json(_sql_pack(row))+b'\n');count+=1
       if relative_db=='domain_index/blob_proofs.sqlite':
        proof=disk_history._decode_row(row[1]);blob=self.output/'domain_evidence'/(proof['key']+'.json.gz')
        raw.append({'path':blob.relative_to(self.output).as_posix(),'object':self._object(blob,proof['gzip_sha256'])})
      if count!=end:raise ValueError('Incomplete recovery SQL range')
     out.flush();os.fsync(out.fileno())
    connection.rollback()
   databases.append({'path':relative_db,'kind':kind,'start':cursor,'end':end,'delta':_ref(self.vault,destination)})
  parts=state.get('actual_prefix_parts',[]);old_parts=previous['part_count'] if previous else 0
  for part in parts[old_parts:]:raw.append({'path':part['file'],'object':self._object(_under(self.output,part['file']),part['sha256'])})
  # Invocation ledgers append on future resumes: a hardlink would mutate old
  # snapshot bytes. Copy these small metadata preimages instead.
  metadata={name:self._object(self.output/name,mutable=True) for name in METADATA if (self.output/name).is_file()}
  encoded_document=_json(document)
  docfile=directory/'runtime.json.gz';_write(docfile,gzip.compress(encoded_document,mtime=0,compresslevel=COMPRESSION_LEVEL))
  stats={'new_sql_rows':sum(d['end']-d['start'] for d in databases),'new_raw_members':len(raw),
   'new_raw_bytes':sum(r['object']['bytes'] for r in raw),'runtime_json_bytes':len(encoded_document),
   'sql_delta_gzip_bytes':sum(d['delta']['bytes'] for d in databases),'runtime_gzip_bytes':docfile.stat().st_size,
   'compression_level':COMPRESSION_LEVEL,'elapsed_before_commit_seconds':time.perf_counter()-started}
  manifest={'schema':SCHEMA+'.manifest','generation':self.generation,'source_output':str(self.output),'binding_sha256':_digest(self.binding),
   'parent':self.previous['snapshot'] if self.previous else None,'parent_commit_sha256':self.previous['commit_sha256'] if self.previous else None,
   'block_count':live['block_count'],'part_count':len(parts),'journal_receipt':live,'records':records,
   'identity':journal._identity,'runtime':_ref(self.vault,docfile),'databases':databases,'raw':raw,'metadata':metadata,'statistics':stats}
  mfile=directory/'manifest.json';_write(mfile,_json(manifest))
  commit=_sealed({'schema':SCHEMA+'.commit','snapshot':relative,'manifest':_ref(self.vault,mfile)})
  staging=directory/'COMMIT.prepare';_write(staging,_json(commit));os.rename(staging,directory/'COMMIT.json')
  pointer=_sealed({'schema':SCHEMA+'.latest','snapshot':relative,'commit_sha256':_sha(directory/'COMMIT.json')})
  _atomic(self.vault/'LATEST.json',_json(pointer));self.previous=latest_snapshot(self.vault)
  return {'schema':SCHEMA+'.publication','publication_path':str(directory/'COMMIT.json'),'snapshot':relative,
          'block_count':live['block_count'],'observed_until_minute':live['observed_until_minute'],
          'archive_statistics':dict(stats,elapsed_seconds=time.perf_counter()-started)}

def _rebind_state(state,old,new):
 result=copy.deepcopy(state)
 for saved in result.get('history_states',{}).values():
  path=_root(saved['path']);relative=path.relative_to(old);saved['path']=str(new/relative)
  saved['content_sha256']=_digest({k:v for k,v in saved.items() if k!='content_sha256'})
 domain=result.get('domain_store')
 if domain is not None:
  domain['directory']=str(new/'domain_evidence');domain['index_directory']=str(new/'domain_index')
  for key in ('catalogue_state','proof_history_state'):
   saved=domain[key];saved['path']=str(new/Path(saved['path']).relative_to(old))
   saved['content_sha256']=_digest({k:v for k,v in saved.items() if k!='content_sha256'})
  domain['content_sha256']=_digest({k:v for k,v in domain.items() if k!='content_sha256'})
 return result

def restore(vault,output,*,snapshot='latest'):
 vault=_root(vault);output=_root(output)
 if output.exists() or output.is_relative_to(vault) or vault.is_relative_to(output):raise ValueError('Restore requires a new independent output directory')
 chosen=latest_snapshot(vault,snapshot);chain=[];current=chosen;seen=set()
 while True:
  if current['snapshot'] in seen:raise ValueError('Cyclic recovery chain')
  seen.add(current['snapshot']);chain.append(current)
  parent=current['manifest']['parent']
  if parent is None:break
  previous=latest_snapshot(vault,parent)
  if previous['commit_sha256']!=current['manifest']['parent_commit_sha256']:raise ValueError('Recovery parent COMMIT differs')
  current=previous
 chain.reverse();stage=output.with_name(output.name+'.prepare-'+uuid.uuid4().hex);stage.mkdir(parents=True,exist_ok=False)
 connections={};cursors={};migration=[];raw_seen=set()
 try:
  target=chosen['manifest'];document=json.loads(gzip.decompress(_member(vault,target['runtime']).read_bytes()))
  binding=_check(_read(vault/'VAULT.json'))['binding'];checkpoint._validate(document,binding)
  for item in chain:
   manifest=item['manifest'];old=Path(manifest['source_output'])
   for raw in manifest['raw']:
    source=_member(vault,raw['object']);destination=_under(stage,raw['path'])
    if raw['path'] in raw_seen:
     if _sha(destination)!=raw['object']['sha256']:raise ValueError('Conflicting recovery raw member')
     continue
    destination.parent.mkdir(parents=True,exist_ok=True);os.link(source,destination);raw_seen.add(raw['path'])
   for db in manifest['databases']:
    path=_under(stage,db['path']);source=_member(vault,db['delta'])
    if (type(db['start']) is not int or db['start']<0 or db['start']!=cursors.get(db['path'],0)
        or type(db['end']) is not int or db['end']<db['start']):raise ValueError('Invalid recovery SQL range cursor')
    if db['path'] not in connections:
     if db['start']!=0:raise ValueError('Recovery database has no complete base')
     path.parent.mkdir(parents=True,exist_ok=True);c=sqlite3.connect(path);connections[db['path']]=c
     sql=DISK_SQL if db['kind']=='history' else CAT_SQL if db['kind']=='catalogue' else None
     if sql is None:raise ValueError('Unknown recovery SQL schema')
     for command in sql:c.execute(command)
    c=connections[db['path']]
    with gzip.open(source,'rt',encoding='utf8') as stream:
     header=json.loads(next(stream));row_header=_sql_unpack(header['header'])
     if header!={'schema':SCHEMA+'.sql','kind':db['kind'],'start':db['start'],'end':db['end'],'header':header['header']} or _header_count(db['kind'],row_header)!=db['end']:raise ValueError('SQL delta header differs from its manifest')
     count=db['start']
     for line in stream:
      row=_sql_unpack(json.loads(line))
      if row[0]!=count:raise ValueError('SQL delta rows are missing, repeated or out of order')
      c.execute('INSERT INTO entries VALUES ('+','.join('?' for _ in row)+')',row);count+=1
     if count!=db['end']:raise ValueError('SQL delta row count differs')
     c.execute('DELETE FROM header');c.execute('INSERT INTO header VALUES ('+','.join('?' for _ in row_header)+')',row_header);c.commit()
     cursors[db['path']]=count
  for c in connections.values():c.close()
  connections.clear()
  inherited_origin=None;inherited_origin_ref=None
  for name,ref in target['metadata'].items():
   if name not in METADATA:raise ValueError('Unregistered recovery metadata file')
   source=_member(vault,ref);destination=stage/name
   if name=='RECOVERY_ORIGIN.json':
    inherited_origin=_check(_read(source));inherited_origin_ref=ref
    if inherited_origin.get('output')!=target['source_output']:raise ValueError('Inherited recovery origin belongs to another branch')
    destination=stage/'RECOVERY_INHERITED_ORIGIN.json'
   _write(destination,source.read_bytes())
  state=checkpoint._decode(document['runner_state']);old=Path(target['source_output'])
  staged_state=_rebind_state(state,old,stage)
  for saved in staged_state.get('history_states',{}).values():
   h=disk_history.DiskHistory.from_state(saved,identity=saved['identity']);h.close()
  if 'domain_store' in staged_state:
   from .domain_store import DomainStore
   d=staged_state['domain_store']
   # A healthy engineering run may emit no native CAS blobs. Files alone do
   # not preserve empty directories; recreate only this registered empty store.
   if d['committed_count']==0:
    if (d['catalogue_state']['count']!=0 or d['proof_history_state']['prefix_count']!=0
        or d['proof_history_state']['global_count']!=0):raise ValueError('Empty domain-store descriptors disagree')
    blob_directory=_under(stage,'domain_evidence')
    if d['directory']!=str(blob_directory):raise ValueError('Empty domain-store directory is not canonical')
    blob_directory.mkdir(exist_ok=True)
   store=DomainStore.from_state(d,identity=d['identity'],native_source_binding=d['native_source_binding'],read_only=True,full=True);store.close()
  # The branch preserves original raw objects in the vault. Only this exact
  # sidecar path is reserialized; every other raw field remains original.
  native=native_journal.BlockJournal(stage/'rebuilt-evidence',identity=target['identity'])
  for item in chain:
   for record in item['manifest']['records']:
    original=_under(stage,'evidence/'+record['file'])
    if (_sha(original)!=record['sha256'] or record['digest']!=native_journal._record_digest(record)
        or type(record['index']) is not int or record['index']!=native._count):raise ValueError('Recovery raw/index preimage differs')
    block=json.loads(gzip.decompress(original.read_bytes()));patches=[]
    if block.get('solver_row_sidecar'):
     value=block['solver_row_sidecar']['path'];expected=str(Path(item['manifest']['source_output'])/Path(value).name)
     if value!=expected:raise ValueError('Unregistered sidecar path relocation')
     new=str(output/Path(value).name);block['solver_row_sidecar']['path']=new
     patches.append({'path':'solver_row_sidecar.path','before':value,'after':new})
    new_record=native.append(block)
    migration.append({'original_record':record,'branch_record':new_record,'io_patches':patches})
  original_evidence=_under(stage,'evidence');rebuilt=_under(stage,'rebuilt-evidence')
  retained=_under(stage,'recovery_original_evidence')
  if not original_evidence.resolve().is_relative_to(stage.resolve()) or not rebuilt.resolve().is_relative_to(stage.resolve()):raise ValueError('Recovery evidence move escaped the registered stage')
  os.rename(original_evidence,retained);os.rename(rebuilt,stage/'evidence');native.output=stage/'evidence'
  receipt=resume_journal.checkpoint_receipt(native)
  receipt['journal_path']=str(output/'evidence');receipt['digest']=native_journal._record_digest(receipt)
  new_state=_rebind_state(state,old,output);new_state['journal_receipt']=receipt
  document['runner_state']=checkpoint._encode(new_state);document['content_sha256']=_digest({k:v for k,v in document.items() if k!='content_sha256'})
  checkpoint._validate(document,binding)
  directory=stage/'checkpoints';directory.mkdir();saved=directory/'recovered_state.json';checkpoint.save_checkpoint(document,saved)
  marker_path=output/'checkpoints/RECOVERY_CHECKPOINT.json'
  marker={'schema':resume_journal.SCHEMA+'.publication','publication_path':str(marker_path),
   'checkpoint_path':str(output/'checkpoints/recovered_state.json'),'checkpoint_bytes':saved.stat().st_size,
   'checkpoint_sha256':_sha(saved),'checkpoint_content_sha256':document['content_sha256'],
   'journal_receipt':receipt,'journal_receipt_sha256':hashlib.sha256(native_journal._json(receipt)).hexdigest(),
   'cryptographic_identity_claim':False}
  marker['digest']=native_journal._record_digest(marker);_write(directory/'RECOVERY_CHECKPOINT.json',native_journal._json(marker))
  origin=_sealed({'schema':SCHEMA+'.restore','snapshot':chosen['snapshot'],'commit_sha256':chosen['commit_sha256'],
   'source_output':str(old),'output':str(output),'binding_sha256':_digest(binding),'migration':migration,
   'origin_outputs':sorted(set((inherited_origin.get('origin_outputs',[]) if inherited_origin else [])+[str(old)])),
   'inherited_origin':inherited_origin_ref,
   'physical_fields_modified':False,'original_row_gzip_bytes_modified':False,'reset_called':False})
  _write(stage/'RECOVERY_ORIGIN.json',_json(origin));os.rename(stage,output)
  return {'resume_checkpoint':str(marker_path),'snapshot':chosen['snapshot'],'block_count':target['block_count'],
          'observed_until_minute':document['minute'],'output':str(output),'source_output_unchanged':True}
 finally:
  for c in connections.values():c.close()

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
 p=sub.add_parser('restore');p.add_argument('--vault',type=Path,required=True);p.add_argument('--snapshot',default='latest');p.add_argument('--output',type=Path,required=True)
 args=parser.parse_args(argv);print(json.dumps(restore(args.vault,args.output,snapshot=args.snapshot),ensure_ascii=False))
if __name__=='__main__':main()
