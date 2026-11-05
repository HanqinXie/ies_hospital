"""Complete global history with durable prefix and mutable current tail."""
import copy
import importlib

import pytest


def implementation():
    try:return importlib.import_module('tools.annual_program.disk_history')
    except ModuleNotFoundError as exc:
        if exc.name != 'tools.annual_program.disk_history':raise
        pytest.fail('Bounded durable annual history is missing',pytrace=False)


def test_mutable_current_tail_global_indices_and_complete_reopen(tmp_path):
    cls=implementation().DiskHistory
    path=tmp_path/'segments.sqlite';seq=cls(path,identity={'case':'same'})
    reference=[]
    for n in range(40):
        row=dict(event_id=n+1,time_minute=.25*n,nested={'array':[n,n+1]})
        reference.append(row);seq.append(copy.deepcopy(row))
        seq[-1]['actual_final_field']=n;reference[-1]['actual_final_field']=n
        assert seq[-1]==reference[-1]
        seq.commit(keep_tail=1)
        assert len(seq)==n+1 and seq.retained_count<=1
        assert seq[:]==reference
    assert isinstance(seq,list)
    assert list(reversed(seq))==list(reversed(reference))
    assert seq[2:10:2]==reference[2:10:2]
    state=seq.to_state();seq.close()
    restored=cls.from_state(state,path=path,identity={'case':'same'})
    assert restored[:]==reference
    restored.append({'event_id':41});assert len(restored)==41
    assert restored[39]==reference[39]
    restored.close()


def test_persisted_prefix_mutation_rejected_but_detached_copy_is_mutable(tmp_path):
    seq=implementation().DiskHistory(tmp_path/'h.sqlite',identity={'a':1})
    seq.append({'nested':[{'x':1}]});seq.append({'nested':[{'x':2}]});seq.commit(keep_tail=1)
    with pytest.raises(TypeError):seq[0]['nested'][0]['x']=9
    value=copy.deepcopy(seq[0]);value['nested'][0]['x']=9
    assert seq[0]['nested'][0]['x']==1
    seq[-1]['nested'][0]['x']=3;assert seq[-1]['nested'][0]['x']==3
    seq.close()


def test_wrong_identity_corruption_or_ambiguous_newer_prefix_cannot_resume(tmp_path):
    cls=implementation().DiskHistory;path=tmp_path/'h.sqlite';seq=cls(path,identity={'a':1})
    seq.append({'x':1});seq.commit(keep_tail=0);state=seq.to_state()
    seq.append({'x':2});seq.commit(keep_tail=0);seq.close()
    with pytest.raises(ValueError):cls.from_state(state,path=path,identity={'a':1})
    with pytest.raises(ValueError):cls.from_state(state,path=path,identity={'a':2})


def test_large_history_keeps_only_requested_tail_and_preserves_all_rows(tmp_path):
    seq=implementation().DiskHistory(tmp_path/'h.sqlite',identity={'a':1})
    for n in range(5000):
        seq.append({'n':n,'value':'x'*20})
        if n%16==15:seq.commit(keep_tail=1)
    seq.commit(keep_tail=1)
    assert seq.retained_count==1 and len(seq)==5000
    assert sum(row['n'] for row in seq)==4999*5000//2
    seq.close()


def test_typed_archived_numpy_nonfinite_values_and_detached_deepcopy(tmp_path):
    import math
    from pathlib import Path
    import numpy as np
    seq=implementation().DiskHistory(tmp_path/'typed.sqlite',identity={'a':1})
    seq.append({'array':np.array([1.,np.nan,np.inf],dtype='float32'),
        'scalar':np.int64(7),'value':float('-inf'),'path':Path('E:/typed'),
        'tuple':(1,{'set':{2,3}})})
    seq.commit(keep_tail=0)
    row=seq[0]
    assert row['array'].dtype==np.dtype('float32') and math.isnan(row['array'][1])
    assert type(row['scalar']) is np.int64 and math.isinf(row['value'])
    assert row['path']==Path('E:/typed') and row['tuple'][1]['set']=={2,3}
    with pytest.raises((TypeError,ValueError)):row['array'][0]=8.
    with pytest.raises((TypeError,ValueError)):row['array'].setflags(write=True)
    with pytest.raises(TypeError):row['tuple'][1]['set'].add(4)
    detached=copy.deepcopy(seq);detached[0]['array'][0]=8.;detached[0]['tuple'][1]['set'].add(4)
    assert seq[0]['array'][0]==1. and seq[0]['tuple'][1]['set']=={2,3}
    state=seq.to_state();seq.close()
    restored=implementation().DiskHistory.from_state(state,identity={'a':1})
    assert restored[0]['array'].dtype==np.dtype('float32')
    restored.close()


def test_sqlite_payload_corruption_fails_resume(tmp_path):
    import sqlite3
    cls=implementation().DiskHistory;path=tmp_path/'corrupt.sqlite'
    seq=cls(path,identity={'a':1});seq.append({'x':1});seq.commit(keep_tail=0)
    state=seq.to_state();seq.close()
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE entries SET payload=? WHERE idx=0',(b'corrupted bytes',))
    with pytest.raises(ValueError,match='checksum|corrupt|integrity'):
        cls.from_state(state,path=path,identity={'a':1})


def test_unacknowledged_after_commit_fsync_failure_cannot_reuse_old_state(tmp_path,monkeypatch):
    cls=implementation().DiskHistory;path=tmp_path/'fsync.sqlite'
    seq=cls(path,identity={'a':1});seq.append({'x':1});seq.commit(keep_tail=0)
    state=seq.to_state();seq.append({'x':2})
    def fail():raise OSError('injected durable receipt failure')
    monkeypatch.setattr(seq,'_fsync',fail)
    with pytest.raises(OSError,match='receipt'):seq.commit(keep_tail=0)
    with pytest.raises(RuntimeError,match='write failure'):seq.to_state()
    seq.close()
    with pytest.raises(ValueError,match='newer|version'):
        cls.from_state(state,path=path,identity={'a':1})


def test_list_materialization_operations_do_not_read_empty_base_storage(tmp_path):
    seq=implementation().DiskHistory(tmp_path/'ops.sqlite',identity={'a':1})
    reference=[{'n':0},{'n':1}]
    seq.extend(copy.deepcopy(reference));seq.commit(keep_tail=1)
    assert seq+[{'n':2}]==reference+[{'n':2}]
    assert [{'n':-1}]+seq==[{'n':-1}]+reference
    assert seq*2==reference*2 and 2*seq==reference*2
    with pytest.raises(IndexError):seq[-100]={'x':'outside history'}
    seq.close()


def test_behavior_bearing_rows_are_rejected_before_any_persistence(tmp_path):
    class BehavioralLog(list):pass
    seq=implementation().DiskHistory(tmp_path/'types.sqlite',identity={'a':1})
    with pytest.raises(TypeError):seq.append(BehavioralLog([1]))
    with pytest.raises(TypeError):seq.append({'resource':object()})
    assert len(seq)==0
    seq.close()


def test_archived_verbose_physical_record_is_losslessly_compressed(tmp_path):
    import json
    import sqlite3
    cls=implementation().DiskHistory;path=tmp_path/'compressed.sqlite'
    row={'rows':[{'name':'same original physical witness','value':float(n%3),
        'coefficients':[0.,1.,0.,0.,0.,0.,1.,0.]} for n in range(2000)]}
    seq=cls(path,identity={'a':1});seq.append(row);seq.commit(keep_tail=0)
    assert copy.deepcopy(seq[0])==row
    with sqlite3.connect(path) as connection:
        payload=connection.execute('SELECT payload FROM entries WHERE idx=0').fetchone()[0]
    assert len(payload)<len(json.dumps(row).encode('utf8'))//8
    state=seq.to_state();seq.close()
    restored=cls.from_state(state,path=path,identity={'a':1})
    assert copy.deepcopy(restored[0])==row
    restored.close()
