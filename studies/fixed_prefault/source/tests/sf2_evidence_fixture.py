"""Exclusive small test-artifact sink, not a scientific producer or admission."""
from copy import deepcopy
from pathlib import Path
import gzip,hashlib,json


def make_domain_sink(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    records=[]
    def sink(payload):
        value=deepcopy(payload);seq=value['scope_sequence'];kind='domain' if value['schema'].endswith('nonlinear_domain.v1') else 'result'
        key='domain_sha256' if kind=='domain' else 'result_sha256'
        body={k:v for k,v in value.items() if k!=key and not (kind=='domain' and k=='scope_sequence')}
        canonical=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf8')
        assert hashlib.sha256(canonical(body)).hexdigest()==value[key]
        name=f'nl_{seq:06d}.{kind}.json.gz';path=directory/name
        with path.open('xb') as stream:stream.write(gzip.compress(canonical(value),mtime=0))
        ref=dict(schema=f'hospital_ies.sf2.nonlinear_{kind}_reference.v1',scope_sequence=seq,
            record_id=f'nl_{seq:06d}',domain_sha256=value['domain_sha256'] if kind=='domain' else value['domain_reference']['domain_sha256'],
            artifacts=[dict(path=f'{directory.name}/{name}',sha256=hashlib.sha256(path.read_bytes()).hexdigest())])
        if kind=='result':ref['result_sha256']=value['result_sha256']
        records.append(dict(event=kind,reference=deepcopy(ref)))
        return ref
    return sink,records
