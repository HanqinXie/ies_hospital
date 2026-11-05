"""Bounded sequence lookup for a future packed backend; no new DB writes."""
from copy import deepcopy
import pytest
from test_annual_program_domain_catalogue import catalogue,reference,IDENTITY,implementation


def test_position_sees_pending_rows_on_owner_and_returns_detached_native_reference(tmp_path):
    cat=catalogue(tmp_path,limit=1)
    try:
        a=reference(1);b=reference(2)
        cat[a[0]]=a[1];cat[b[0]]=b[1]
        before=(cat._pending_count,cat._pending_head,cat._epoch,cat._connection.total_changes)
        result=cat.entry_position(b[0])
        assert result==dict(sequence=1,key=b[0],reference=b[1])
        result['reference']['row_count']=900
        assert cat.entry_position(b[0])['reference']==b[1]
        assert cat.entry_position(a[0])['sequence']==0
        assert (cat._pending_count,cat._pending_head,cat._epoch,cat._connection.total_changes)==before
        assert cat.cache_info()['decoded_ref_count']<=1
    finally:cat.close()


def test_position_works_readonly_and_has_no_silent_missing_key_or_closed_fallback(tmp_path):
    cat=catalogue(tmp_path);key,value=reference(3);cat[key]=value;cat.commit();state=cat.to_state();cat.close()
    reader=implementation().DomainCatalogue.from_state(state,identity=IDENTITY,cache_limit=1,read_only=True)
    try:
        assert reader.entry_position(key)==dict(sequence=0,key=key,reference=value)
        with pytest.raises(KeyError):reader.entry_position('a'*64)
        with pytest.raises(ValueError):reader.entry_position('../bad')
    finally:reader.close()
    with pytest.raises(RuntimeError,match='closed'):reader.entry_position(key)
