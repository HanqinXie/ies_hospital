"""Real small SQLite/journal recovery protocol; no environment/optimizer calls."""
from pathlib import Path
import copy,gzip,hashlib,importlib,json
import pytest

from tools.annual_program import checkpoint,journal
from tools.annual_program.disk_history import DiskHistory
from tools.annual_program.domain_store import DomainStore
from tools.annual_readiness.journal import BlockJournal
from test_annual_program_checkpoint import runtime,plan_for,BINDING
from test_annual_program_journal import block,IDENTITY

def api():return importlib.import_module('tools.annual_program.recovery')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def files(p):return {x.relative_to(p).as_posix():x.read_bytes() for x in p.rglob('*') if x.is_file()}

def fixture(tmp_path,*,empty_domain=False):
 output=tmp_path/'producer';output.mkdir();vault=tmp_path/'vault'
 for name in ('CASE.json','BINDINGS.json','ANNUAL_ACCESS_CONTEXT.json','ORCHESTRATION_FREEZE.json'):
  (output/name).write_text(json.dumps({'fixture':name}),encoding='utf8')
 (output/'ATTEMPTS.jsonl').write_text('{"invocation":0,"recording_mode":"full_v1"}\n',encoding='utf8')
 log=BlockJournal(output/'evidence',identity=IDENTITY)
 history=DiskHistory(output/'histories/events.sqlite',identity={'stream':'fixture'})
 history.extend([{'value':1},{'value':2}]);history.commit(keep_tail=1)
 root=Path(__file__).parents[1];native=root/'tools/model_correctness_v23/evidence.py'
 store=DomainStore.create(output/'domain_evidence',index_directory=output/'domain_index',
  identity={'case_id':'fixture','source_sha256':'a'*64},native_source_binding={'path':str(native),'sha256':sha(native)},cache_limit=2,
  enable_all_validation_rows=True)
 if not empty_domain:store.store([{'name':'row','value':1.}])
 store.commit()
 env,controller=runtime();env.core.time=15.
 first=block();sidecar=output/'solver_rows_block_000000.jsonl.gz';sidecar.write_bytes(gzip.compress(b'[]',mtime=0))
 first['solver_row_sidecar']={'path':str(sidecar),'gzip_sha256':sha(sidecar),'gzip_bytes':sidecar.stat().st_size}
 log.append(first)
 state={'block_count':1,'committed_end_minute':15.,'reset_count':1,'history_states':{'events':history.to_state()},
  'domain_store':store.to_state(),'actual_prefix_parts':[], 'journal_receipt':journal.checkpoint_receipt(log)}
 doc=checkpoint.capture_checkpoint(env,controller,binding=BINDING,field_plan=plan_for(env,controller),runner_state=state)
 return output,vault,log,history,store,doc

def test_advanced_journal_requires_new_branch_with_exact_snapshot_databases(tmp_path):
 output,vault,log,history,store,document=fixture(tmp_path)
 recovery=api();writer=recovery.RecoveryArchive(vault,output,BINDING)
 saved=writer.capture(document,log)
 old=copy.deepcopy(checkpoint._decode(document['runner_state']))
 log.append(block(15.,30.));history.extend([{'value':3},{'value':4}]);history.commit(keep_tail=1)
 store.store([{'name':'row','value':2.}]);store.commit()
 before=files(output)
 with pytest.raises(ValueError):journal.reopen_journal(output/'evidence',old['journal_receipt'],IDENTITY)
 restored=recovery.restore(vault,tmp_path/'branch')
 assert restored['block_count']==1 and restored['observed_until_minute']==15.
 assert files(output)==before
 doc,state=importlib.import_module('tools.annual_program.runner')._load_publication(
  restored['resume_checkpoint'],tmp_path/'branch',BINDING)
 assert state['history_states']['events']['prefix_count']==old['history_states']['events']['prefix_count']
 reopened=DiskHistory.from_state(state['history_states']['events'],identity={'stream':'fixture'})
 assert list(reopened)==[{'value':1},{'value':2}];reopened.close()
 assert state['domain_store']['committed_count']==1
 history.close();store.close()

def test_incomplete_commit_keeps_previous_complete_point(tmp_path):
 output,vault,log,history,store,doc=fixture(tmp_path)
 r=api();writer=r.RecoveryArchive(vault,output,BINDING);first=writer.capture(doc,log)
 partial=vault/'snapshots/incomplete';partial.mkdir();(partial/'manifest.json').write_text('{"partial":true}')
 assert r.latest_snapshot(vault)['snapshot']==first['snapshot']
 result=r.restore(vault,tmp_path/'branch');assert result['block_count']==1
 assert partial.exists();history.close();store.close()

def test_zero_blob_domain_restores_its_registered_empty_directory(tmp_path):
 output,vault,log,history,store,doc=fixture(tmp_path,empty_domain=True)
 r=api();writer=r.RecoveryArchive(vault,output,BINDING);writer.capture(doc,log)
 history.close();store.close();writer.close();output.rename(tmp_path/'original_absent')
 result=r.restore(vault,tmp_path/'branch')
 runner=importlib.import_module('tools.annual_program.runner');_,state=runner._load_publication(
  result['resume_checkpoint'],tmp_path/'branch',BINDING)
 d=state['domain_store']
 assert d['committed_count']==d['catalogue_state']['count']==d['proof_history_state']['prefix_count']==0
 assert (tmp_path/'branch/domain_evidence').is_dir()
 assert list((tmp_path/'branch/domain_evidence').iterdir())==[]
 checked=DomainStore.from_state(d,identity=d['identity'],native_source_binding=d['native_source_binding'],read_only=True,full=True)
 assert checked.verify_blobs(full=True)['verified_blob_count']==0;checked.close()

def test_metadata_append_cannot_mutate_a_committed_snapshot(tmp_path):
 output,vault,log,history,store,doc=fixture(tmp_path);r=api();w=r.RecoveryArchive(vault,output,BINDING)
 w.capture(doc,log)
 with (output/'ATTEMPTS.jsonl').open('a') as f:f.write('{"invocation":1}\n')
 result=r.restore(vault,tmp_path/'branch')
 assert result['block_count']==1
 assert (tmp_path/'branch/ATTEMPTS.jsonl').read_text()== '{"invocation":0,"recording_mode":"full_v1"}\n'
 history.close();store.close();w.close()

def test_changed_database_identity_is_rejected_before_commit(tmp_path):
 import sqlite3
 output,vault,log,history,store,doc=fixture(tmp_path);r=api();w=r.RecoveryArchive(vault,output,BINDING)
 with sqlite3.connect(history.path) as c:c.execute("UPDATE header SET identity_sha=?",('b'*64,));c.commit()
 with pytest.raises(ValueError):w.capture(doc,log)
 assert not (vault/'LATEST.json').exists()
 history.close();store.close();w.close()

def test_restore_is_self_contained_when_original_attempt_is_absent(tmp_path):
 output,vault,log,history,store,doc=fixture(tmp_path);r=api();w=r.RecoveryArchive(vault,output,BINDING)
 w.capture(doc,log);history.close();store.close();w.close()
 output.rename(tmp_path/'quarantined_original')
 result=r.restore(vault,tmp_path/'branch');assert result['block_count']==1
 assert not output.exists()

@pytest.mark.parametrize('member',['runtime','delta','raw'])
def test_corrupt_or_missing_snapshot_member_is_not_a_recoverable_point(tmp_path,member):
 output,vault,log,history,store,doc=fixture(tmp_path);r=api();w=r.RecoveryArchive(vault,output,BINDING)
 w.capture(doc,log);m=r.latest_snapshot(vault)['manifest']
 ref=m['runtime'] if member=='runtime' else m['databases'][0]['delta'] if member=='delta' else m['raw'][0]['object']
 p=vault/ref['file']
 if member=='runtime':
  changed=json.loads(gzip.decompress(p.read_bytes()));changed['minute']+=1.
  p.write_bytes(gzip.compress(json.dumps(changed).encode(),mtime=0))
 elif member=='delta':p.unlink()
 else:p.write_bytes(b'corrupt archived raw')
 with pytest.raises((ValueError,FileNotFoundError)):r.restore(vault,tmp_path/'branch')
 assert not (tmp_path/'branch').exists()
 history.close();store.close();w.close()

def reseal_manifest(vault,change):
 r=api();value=r.latest_snapshot(vault);commit=value['commit'];m=value['manifest'];change(m)
 p=vault/commit['manifest']['file'];p.write_bytes(r._json(m))
 commit['manifest']=r._ref(vault,p);commit=r._sealed({k:v for k,v in commit.items() if k!='digest'})
 c=vault/value['snapshot']/'COMMIT.json';c.write_bytes(r._json(commit))
 pointer=r._sealed({'schema':r.SCHEMA+'.latest','snapshot':value['snapshot'],'commit_sha256':sha(c)})
 (vault/'LATEST.json').write_bytes(r._json(pointer))

def test_nonzero_domain_missing_archived_blob_is_still_rejected(tmp_path):
 output,vault,log,history,store,doc=fixture(tmp_path);r=api();writer=r.RecoveryArchive(vault,output,BINDING)
 writer.capture(doc,log)
 reseal_manifest(vault,lambda m:m.update(raw=[x for x in m['raw'] if not x['path'].startswith('domain_evidence/')]))
 with pytest.raises(ValueError):r.restore(vault,tmp_path/'branch')
 assert not (tmp_path/'branch').exists()
 history.close();store.close();writer.close()

@pytest.mark.parametrize('kind',['negative_cursor','traversal'])
def test_semantically_invalid_resealed_archive_is_rejected(tmp_path,kind):
 output,vault,log,history,store,doc=fixture(tmp_path);r=api();w=r.RecoveryArchive(vault,output,BINDING);w.capture(doc,log)
 def change(m):
  if kind=='negative_cursor':m['databases'][0]['start']=-1
  else:m['raw'][0]['path']='../escaped.gz'
 reseal_manifest(vault,change)
 with pytest.raises(ValueError):r.restore(vault,tmp_path/'branch')
 assert not (tmp_path/'escaped.gz').exists() and not (tmp_path/'branch').exists()
 history.close();store.close();w.close()

def test_restore_capture_new_generation_then_restore_again_is_self_contained(tmp_path):
 output,vault,log,history,store,doc=fixture(tmp_path);r=api();writer=r.RecoveryArchive(vault,output,BINDING)
 writer.capture(doc,log);history.close();store.close();writer.close()
 first=r.restore(vault,tmp_path/'branch1')
 runner=importlib.import_module('tools.annual_program.runner')
 restored,state=runner._load_publication(first['resume_checkpoint'],tmp_path/'branch1',BINDING)
 branch=tmp_path/'branch1';log=journal.reopen_journal(branch/'evidence',state['journal_receipt'],IDENTITY)
 h=DiskHistory.from_state(state['history_states']['events'],identity={'stream':'fixture'})
 ds=state['domain_store'];s=DomainStore.from_state(ds,identity=ds['identity'],native_source_binding=ds['native_source_binding'])
 h.extend([{'value':3},{'value':4}]);h.commit(keep_tail=1);s.store([{'name':'new','value':3.}]);s.commit()
 log.append(block(15.,30.));state.update(block_count=2,committed_end_minute=30.,
  history_states={'events':h.to_state()},domain_store=s.to_state(),journal_receipt=journal.checkpoint_receipt(log))
 env,controller=runtime();env.core.time=30.
 doc=checkpoint.capture_checkpoint(env,controller,binding=BINDING,field_plan=plan_for(env,controller),runner_state=state)
 writer=r.RecoveryArchive(vault,branch,BINDING);writer.capture(doc,log);writer.close();h.close();s.close()
 output.rename(tmp_path/'original_absent');branch.rename(tmp_path/'branch1_absent')
 second=r.restore(vault,tmp_path/'branch2')
 assert second['block_count']==2 and second['observed_until_minute']==30.
 assert (tmp_path/'branch2/RECOVERY_ORIGIN.json').is_file()

def test_second_snapshot_does_not_hash_previously_archived_raw_members(tmp_path,monkeypatch):
 output,vault,log,history,store,doc=fixture(tmp_path);r=api();writer=r.RecoveryArchive(vault,output,BINDING)
 writer.capture(doc,log)
 history.extend([{'value':3},{'value':4}]);history.commit(keep_tail=1)
 store.store([{'name':'new','value':3.}]);store.commit();log.append(block(15.,30.))
 state=checkpoint._decode(doc['runner_state']);state.update(block_count=2,committed_end_minute=30.,
  history_states={'events':history.to_state()},domain_store=store.to_state(),journal_receipt=journal.checkpoint_receipt(log))
 env,controller=runtime();env.core.time=30.
 doc=checkpoint.capture_checkpoint(env,controller,binding=BINDING,field_plan=plan_for(env,controller),runner_state=state)
 original_sha=r._sha
 def bounded(path):
  assert Path(path) not in (output/'evidence/blocks/block_000000.json.gz',output/'solver_rows_block_000000.jsonl.gz')
  return original_sha(path)
 monkeypatch.setattr(r,'_sha',bounded)
 result=writer.capture(doc,log);latest=r.latest_snapshot(vault)['manifest']
 assert len(latest['records'])==1 and all(d['start']>0 for d in latest['databases'])
 assert result['archive_statistics']['new_sql_rows']>0
 writer.close();history.close();store.close()

def test_real_sidecar_history_materializes_exact_relocated_receipt_for_reader_membership(tmp_path):
 output,vault,log,history,store,doc=fixture(tmp_path);r=api()
 first=json.loads(gzip.decompress((output/'evidence/blocks/block_000000.json.gz').read_bytes()))
 side=DiskHistory(output/'histories/sidecars.sqlite',identity={'stream':'sidecars'})
 side.append(first['solver_row_sidecar']);side.commit(keep_tail=0)
 state=checkpoint._decode(doc['runner_state']);state['history_states']['sidecars']=side.to_state()
 doc['runner_state']=checkpoint._encode(state);doc['content_sha256']=r._digest({k:v for k,v in doc.items() if k!='content_sha256'})
 w=r.RecoveryArchive(vault,output,BINDING);w.capture(doc,log);w.close();history.close();store.close();side.close()
 output.rename(tmp_path/'original_absent');result=r.restore(vault,tmp_path/'branch')
 runner=importlib.import_module('tools.annual_program.runner');_,state=runner._load_publication(result['resume_checkpoint'],tmp_path/'branch',BINDING)
 restored=DiskHistory.from_state(state['history_states']['sidecars'],identity={'stream':'sidecars'})
 assert list(restored)==[first['solver_row_sidecar']]  # Original SQL row/chain is unchanged.
 materialized=runner._materialize_sidecars(restored,tmp_path/'branch')
 block_receipt=json.loads(gzip.decompress((tmp_path/'branch/evidence/blocks/block_000000.json.gz').read_bytes()))['solver_row_sidecar']
 assert materialized==[block_receipt]
 assert {k:v for k,v in materialized[0].items() if k!='path'}=={k:v for k,v in first['solver_row_sidecar'].items() if k!='path'}
 restored.close()
