"""Standalone stdlib archive/member verification; no input data or solver needed."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile
p=Path(sys.argv[1])
with zipfile.ZipFile(p) as z:
    assert z.testzip() is None,'CRC failed'
    items=json.loads(z.read('PACKAGE_MEMBERS.json'))
    assert len(z.namelist())==len(items)+1
    for item in items:
        raw=z.read(item['path'])
        assert len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256'],item['path']
print(json.dumps(dict(package=str(p),members=len(items)+1,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),verified=True)))
