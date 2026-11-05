"""Exact git-show historical bytes for parsing only, never module execution.

All 35 entries came from 4a6836f0170653d48d2dcbc20406f11165637bf6.
The fixture is portable: tests need neither Git nor an old external checkout.
"""
import base64
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import zlib


COMMIT='4a6836f0170653d48d2dcbc20406f11165637bf6'
FIXTURE_SHA256='fef9005b87b72d25053601edf148bfbbd2af312fb8a36669ba46dbdd6aaace8f'


@lru_cache(maxsize=1)
def _fixture():
    raw=(Path(__file__).parent/'fixtures/annual_program/reviewed_4a6836f_sci1_source_bytes.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==FIXTURE_SHA256
    value=json.loads(raw)
    assert value['origin_commit']==COMMIT and value['source_executed'] is False
    return value


def reviewed_source_bytes(relative):
    entry=_fixture()['files'][relative]
    raw=zlib.decompress(base64.b64decode(entry['zlib_base64']))
    assert len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256']
    return raw
