"""Keep the same selected metadata while skipping large diagnostics in chunks."""
import io
import json
from pathlib import Path
import random
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from tools.annual_program.engineering_summary import _SelectedJson


class SmallChunks(io.StringIO):
    def __init__(self,value,size):super().__init__(value);self.chunk=size
    def read(self,size=-1):return super().read(min(size,self.chunk) if size>=0 else self.chunk)


class Counted(_SelectedJson):
    def __init__(self,stream):super().__init__(stream);self.takes=0
    def take(self):self.takes+=1;return super().take()


def test_large_skipped_diagnostic_does_not_visit_every_character_in_python():
    text=json.dumps(dict(first=7,diagnostic='a'*2_000_000,last=dict(value=9)))
    reader=Counted(io.StringIO(text))
    assert reader.select({'first':True,'last':{'value':True}})==dict(first=7,last=dict(value=9))
    assert reader.takes<1000


@pytest.mark.parametrize('chunk',[1,2,3,7,31,65536])
@pytest.mark.parametrize('ignored',[
    'brackets { [ ] } inside a string', 'quotes " slash \\ and escaped \\" quote',
    [{'x':'line\n中\\文"[]'},[True,False,None,1.2,-9]],[],{},None,False,123.5,
])
def test_skipped_string_bracket_escape_and_buffer_boundaries_keep_selected_values(chunk,ignored):
    text=json.dumps(dict(first=[1,2],ignored=ignored,last={'v':'完\\"成'}),ensure_ascii=False)
    reader=_SelectedJson(SmallChunks(text,chunk))
    assert reader.select({'first':True,'last':{'v':True}})==dict(first=[1,2],last={'v':'完\\"成'})
    reader.space();assert reader.peek()==''


def test_random_nested_ignored_json_leaves_selected_metadata_exact():
    rng=random.Random(741)
    strings=['','[]{}','"','\\','a\\"b','中\n文','\\\\"[]']
    def value(depth=0):
        if depth>=4:return rng.choice(strings+[None,True,False,1.5])
        kind=rng.randrange(3)
        if kind==0:return rng.choice(strings+[None,True,False,1.5])
        if kind==1:return [value(depth+1) for _ in range(rng.randrange(4))]
        return {str(i):value(depth+1) for i in range(rng.randrange(4))}
    for _ in range(150):
        text=json.dumps({'ignored':value(),'kept':{'answer':42}},ensure_ascii=False)
        assert _SelectedJson(SmallChunks(text,rng.randrange(1,98))).select({'kept':{'answer':True}})=={'kept':{'answer':42}}


@pytest.mark.parametrize('text',[
    '{"ignored":[1,2}', '{"ignored":{"nested":[}}',
    '{"ignored":"unfinished\\', '{"ignored":["unfinished',
])
def test_skipped_structural_corruption_still_fails(text):
    with pytest.raises(ValueError):_SelectedJson(SmallChunks(text,3)).select({})


def test_skipping_remains_structural_and_does_not_claim_semantic_validation():
    # This was already allowed in skipped native diagnostics; selected values
    # still go through json.loads. No new whole-file semantic claim is made.
    reader=_SelectedJson(SmallChunks('{"skip":[not_json],"keep":3}',2))
    assert reader.select({'keep':True})=={'keep':3}
