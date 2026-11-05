"""Independent closed E/steam/C algebra and exact original-file preservation."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
p.add_argument('--producer',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();root=a.source.resolve();producer=a.producer.resolve();out=a.output.resolve()
out.mkdir(parents=True,exist_ok=False)
sys.path[:0]=[str(root),str(root/'src')]
from tools.annual_program import carrier_flow,steam

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for raw in iter(lambda:stream.read(1048576),b''):h.update(raw)
 return h.hexdigest()
def inventory(directory):
 result={}
 for path in sorted(directory.rglob('*')):
  if path.is_symlink():raise ValueError('Symlink not admitted')
  if path.is_file():result[path.relative_to(directory).as_posix()]={'bytes':path.stat().st_size,'sha256':sha(path)}
 return result
def write(name,value):
 with (out/name).open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2)

start=time.monotonic();before=inventory(producer);source_before=inventory(root)
write('BEFORE.json',dict(producer=before,source=source_before))
context=carrier_flow.prepare_carrier_context(producer,source_root=root)
counts={k:Counter() for k in ('e','c','steam')};reasons={k:Counter() for k in counts}
blocks=[];packet_status=Counter()
for index,record in enumerate(context['raw_files_sha256']):
 block=json.loads(gzip.decompress((producer/'evidence'/record['file']).read_bytes()))
 e=carrier_flow.audit_carrier_block(context,block,block_record=record)
 s=steam.audit_steam_block(context,block,block_record=record,use_actual_witness=True,steam_flow_only=True)
 for row in e['segments']:
  packet_status[row.get('compiled_context_status','absent')]+=1
  for k in ('e','c'):
   counts[k][row['raw_flow'][k]['status']]+=1;reasons[k].update(row['raw_flow'][k]['reasons'])
 for row in s['segments']:counts['steam'][row['status']]+=1;reasons['steam'].update(row.get('reasons',[]))
 assert e['whole_system_validated'] is s['whole_system_validated'] is False
 write(f'E_C_{index:06d}.json',e);write(f'STEAM_{index:06d}.json',s)
 blocks.append(dict(index=index,e_c_status=e['status'],steam_status=s['status'],
     e_c_summary=e['summary'],steam_summary=s['summary'],
     action_ledger=e['action_ledger'],storage=e['storage'],diesel=e['diesel']))
 print(json.dumps(dict(block=index,counts={k:dict(v) for k,v in counts.items()})),flush=True)
carrier_flow.verify_carrier_context(context,full=True)
after=inventory(producer);source_after=inventory(root)
write('AFTER.json',dict(producer=after,source=source_after))
assert before==after and source_before==source_after
complete=all(v['unknown']==v['failed']==0 and v['passed']>0 for v in counts.values())
result=dict(status='passed' if complete else 'incomplete_or_failed',
 producer=str(producer),source=str(root),counts={k:dict(v) for k,v in counts.items()},
 reasons={k:dict(v) for k,v in reasons.items()},compiled_context_status=dict(packet_status),
 blocks=blocks,producer_file_count=len(before),original_bytes_unchanged=True,
 source_bytes_unchanged=True,elapsed_seconds=time.monotonic()-start,
 script_sha256=sha(Path(__file__)),physical_solver_calls=0,whole_system_validated=False,
 OEM_startup_qualified=False,DC_energy_qualified=False,annual_run_qualified=False)
write('RESULT.json',result)
print(json.dumps({k:result[k] for k in ('status','counts','reasons','compiled_context_status','elapsed_seconds')}),flush=True)
raise SystemExit(0 if complete else 1)
