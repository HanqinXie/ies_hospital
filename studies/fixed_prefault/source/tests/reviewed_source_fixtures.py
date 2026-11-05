"""Exact git-show historical bytes for parsing only, never module execution.

All 30 entries came from d8d056d6c4c08e7ae2b33d13e98c6fdbe7f259dc.
The fixture is portable: tests need neither Git nor an old external checkout.
"""
import base64
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import zlib


COMMIT='d8d056d6c4c08e7ae2b33d13e98c6fdbe7f259dc'
FIXTURE_SHA256='6c1138c4ef7396c7f9b55c7d8c78e2af82723dd14df58cb0c7ada04196f8878b'


@lru_cache(maxsize=1)
def _fixture():
    raw=(Path(__file__).parent/'fixtures/annual_program/reviewed_d8d056d_source_bytes.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==FIXTURE_SHA256
    value=json.loads(raw)
    assert value['origin_commit']==COMMIT and value['source_executed'] is False
    return value


def reviewed_source_bytes(relative):
    entry=_fixture()['files'][relative]
    raw=zlib.decompress(base64.b64decode(entry['zlib_base64']))
    assert len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256']
    return raw
