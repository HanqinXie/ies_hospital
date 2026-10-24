"""Publish exact small external verification records after actual closeout."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json, shutil

HERE = Path(__file__).resolve().parent
D = Path('D:/hospital_ies_v21_20261023')
PROJECT = Path('E:/hospital_ies_v21_20261023/archive_clean_v21_final02/project')
DEST = D/'docs/v21/reproduction/final_archive_actual02'

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()

def read(path): return json.loads(Path(path).read_text('utf-8-sig'))
def ref(path):
    path = Path(path).resolve()
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path))

p = argparse.ArgumentParser()
p.add_argument('--proof', required=True, type=Path)
p.add_argument('--proof-sha256', required=True)
p.add_argument('--audit-output', required=True, type=Path)
a = p.parse_args()
assert not DEST.exists() and not a.audit_output.exists()
assert a.proof.resolve().parent == HERE and sha(a.proof) == a.proof_sha256
proof = read(a.proof)
assert proof['status'] == 'archive_members_fresh_current365_and_default_pytest_actually_passed'
assert proof['full_seen_case_reproduction']['all365_passed'] is True
assert proof['fresh_default_pytest']['failed'] == proof['fresh_default_pytest']['xfailed'] == 0
rows = []

def add(source, relative, expected=None):
    source = Path(source).resolve(); target = Path(relative)
    assert not target.is_absolute() and '..' not in target.parts
    r = ref(source)
    assert r['bytes'] < 10000000 and (expected is None or r['sha256'] == expected)
    assert relative not in {x['relative_path'] for x in rows}
    rows.append(dict(relative_path=relative, original=r))

add(a.proof, 'ACTUAL_FINAL_PROOF.json', a.proof_sha256)
for key, name in [('extraction_receipt','EXTRACTION_RECEIPT.json'), ('global_census','POST_RUN_CENSUS07_01.json')]:
    r = proof[key]; add(r['path'], name, r['sha256'])
for item in proof['guarded_steps']:
    r = item['receipt']; add(r['path'], 'guarded/'+item['step']+'/OUTER_RECEIPT.json', r['sha256'])
full = proof['full_seen_case_reproduction']
for key, name in [('native_proof','INDEPENDENT_REPLAY_PROOF.json'), ('all365_comparisons','ALL365_SCALAR_COMPARISONS.json')]:
    r = full[key]; add(r['path'], name, r['sha256'])
for name in ['guarded_verify01','guarded_prepare01','guarded_run01','fresh_archive_default_pytest01','post_run_census07_01','close_actual_verification01']:
    cp = HERE/'logs'/(name+'.command.json'); c = read(cp)
    assert type(c['exit_code']) is int and c['exit_code'] == 0 and c['finished_utc']
    for suffix in ['command.json','stdout.log','stderr.log']:
        add(HERE/'logs'/(name+'.'+suffix), 'logs/'+name+'.'+suffix)
for name in ['close_actual_verification.py','run_with_scoped_temp.py','publish_external_proof.py']:
    add(HERE/name, 'verification_sources/'+name)
for row in rows:
    r = row['original']; assert ref(r['path']) == r
DEST.mkdir(parents=True)
for row in rows:
    target = DEST/row['relative_path']; target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(row['original']['path'], target)
    assert target.stat().st_size == row['original']['bytes'] and sha(target) == row['original']['sha256']
notice = '''# 本次归档外置验收证据镜像

本目录在实际完整重放和默认测试终态通过后，按原文件字节与已知SHA复制外置证据。对应冻结ZIP及独立解压运行说明见[交付验证](../../DELIVERY_VERIFICATION.md)。PUBLICATION_MANIFEST.json逐项给出原绝对路径、字节数和SHA；原JSON内部绝对路径指向原E盘审计位置，没有改写为镜像路径。

ACTUAL_FINAL_PROOF.json绑定最终ZIP、解压收据、三步守卫、365项逐项比较、真实默认pytest与全局预算。一次C/H0的360 min重放新增独立背景为0；本目录不重新执行2778条科学轨迹，不证明后续Git推送。日志的exit/time来自原run_logged实际终态。

该目录和最终文档更新在已经验收的ZIP之外；ZIP保持原字节及SHA，内部pending是归档时状态。没有把包含ZIP自身SHA的后续证明重新打包形成循环。镜像不包含ZIP、大体积raw、pytest临时目录或凭据。CPython审计不构成OS级native-I/O沙箱。
'''
(DEST/'README.zh-CN.md').write_text(notice, encoding='utf-8', newline='\n')
manifest = dict(schema='hospital_ies.exact_external_verification_publication.v1', created_utc=datetime.now(timezone.utc).isoformat(), proof=ref(a.proof), destination=str(DEST), copied_files=rows, generated_readme=ref(DEST/'README.zh-CN.md'), archive_repacked=False, scientific_values_changed=False, git_push_certified=False)
with (DEST/'PUBLICATION_MANIFEST.json').open('x', encoding='utf-8', newline='\n') as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2, allow_nan=False); f.write('\n')
receipt = dict(status='exact_external_actual_proof_published', destination=str(DEST), files=len(rows)+2, publication_manifest=ref(DEST/'PUBLICATION_MANIFEST.json'), total_bytes=sum(q.stat().st_size for q in DEST.rglob('*') if q.is_file()), source_code_and_physics_changed=False)
with a.audit_output.open('x', encoding='utf-8', newline='\n') as f:
    json.dump(receipt, f, ensure_ascii=False, indent=2); f.write('\n')
print(json.dumps(receipt, ensure_ascii=False))
