"""Read-only exact selection/index and bounded credential scan before publication."""
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
import collections,hashlib,json,re,subprocess,sys

root=Path(sys.argv[1]).resolve(strict=True)
out=root/'publication_checks/prepush_review01'
out.mkdir(parents=True,exist_ok=True)
def git(*args):return subprocess.check_output(['git','-c','core.quotepath=false',*args],cwd=root)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
selection=root/'publication/V20_FILE_SELECTION.json'
manifest=json.loads(selection.read_text(encoding='utf8'))
marker=json.loads((root/'publication/V20_RELEASE_ROOT.json').read_text(encoding='utf8'))
if manifest.get('schema')!='hospital_ies.v20.repository_selection.v2' or manifest.get('candidate') is not False or marker.get('status')!='final':raise ValueError('Only the actual final release selection may be audited')
expected={r['path']:r for r in manifest['files']}
if len(expected)!=len(manifest['files']):raise ValueError('Duplicate selected path')
expected['publication/V20_FILE_SELECTION.json']=dict(path='publication/V20_FILE_SELECTION.json',bytes=selection.stat().st_size,sha256=sha(selection))
tracked={n.decode('utf8') for n in git('ls-files','-z').split(b'\0') if n}
errors=[];hits=[]
if set(expected)!=tracked:errors.append(dict(kind='index_census',missing=sorted(set(expected)-tracked),extra=sorted(tracked-set(expected))))
index={}
for row in git('ls-files','--stage','-z').split(b'\0'):
    if not row:continue
    header,name=row.split(b'\t',1);mode,blob,stage=header.decode().split()
    if stage!='0':errors.append(dict(kind='unmerged_index',path=name.decode('utf8')))
    if mode not in ('100644','100755'):errors.append(dict(kind='unsupported_index_mode',path=name.decode('utf8'),mode=mode))
    index[name.decode('utf8')]=blob
attribute_input=b''.join(name.encode('utf8')+b'\0' for name in sorted(expected))
attributes=subprocess.check_output(['git','check-attr','--cached','-z','--stdin','text'],input=attribute_input,cwd=root).split(b'\0')
if attributes and attributes[-1]==b'':attributes.pop()
if len(attributes)!=3*len(expected):errors.append(dict(kind='text_attribute_census'))
for i in range(0,len(attributes)-2,3):
    name,attribute,value=attributes[i:i+3]
    if attribute!=b'text' or value!=b'unset':errors.append(dict(kind='checkout_text_conversion_not_disabled',path=name.decode('utf8'),attribute=value.decode('utf8')))
patterns={
    'private_key_header':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github_token':re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}'),
    'aws_access_id':re.compile(rb'AKIA[0-9A-Z]{16}'),
    'literal_credential_assignment':re.compile(rb'''(?im)^\s*(?:password|api_key|access_token|client_secret)\s*[:=]\s*["'][^"'\r\n]{12,}["']\s*[,;]?\s*$'''),
}
for name,row in expected.items():
    relative=PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or ':' in name or '\\' in name:errors.append(dict(kind='unsafe_path',path=name));continue
    raw=root.joinpath(*relative.parts)
    chain=[raw]+[p for p in raw.parents if p.is_relative_to(root)]
    if any(p.is_symlink() or (getattr(p.lstat(),'st_file_attributes',0)&0x400) for p in chain):errors.append(dict(kind='linked_or_reparse_path',path=name));continue
    p=raw.resolve(strict=True)
    if not p.is_relative_to(root):errors.append(dict(kind='unsafe_path',path=name));continue
    content=p.read_bytes()
    if len(content)!=row['bytes'] or hashlib.sha256(content).hexdigest()!=row['sha256']:errors.append(dict(kind='selection_bytes',path=name))
    blob=hashlib.sha1(('blob '+str(len(content))+'\0').encode()+content).hexdigest()
    if index.get(name)!=blob:errors.append(dict(kind='index_blob',path=name))
    if len(content)>=104857600 or p.suffix.lower() in ('.zip','.gz','.7z','.sql','.sqlite','.lic','.pdf','.xlsx'):errors.append(dict(kind='excluded_or_oversized',path=name))
    if p.suffix.lower()!='.npz':
        for kind,pattern in patterns.items():
            for match in pattern.finditer(content):hits.append(dict(kind=kind,path=name,line=content.count(b'\n',0,match.start())+1))
name_status=git('diff','--cached','--no-renames','--name-status').decode('utf8')
numstat=git('diff','--cached','--no-renames','--numstat').decode('utf8')
for filename,content in [('CACHED_NAME_STATUS.txt',name_status),('CACHED_NUMSTAT.txt',numstat)]:
    with (out/filename).open('x',encoding='utf8') as f:f.write(content)
counts=collections.Counter(line.split('\t',1)[0] for line in name_status.splitlines())
value=dict(schema='hospital_ies.v20.prepush_exact_tree_review.v1',utc=datetime.now(timezone.utc).isoformat(),
    status='passed' if not errors and not hits else 'failed_review_required',root=str(root),
    baseline_commit=git('rev-parse','HEAD').decode().strip(),selection_sha256=sha(selection),
    audit_script_sha256=sha(Path(__file__)),release_marker_sha256=sha(root/'publication/V20_RELEASE_ROOT.json'),
    expected_files=len(expected),indexed_files=len(tracked),total_uncompressed_bytes=sum(r['bytes'] for r in expected.values()),
    change_counts=dict(counts),errors=errors,credential_pattern_hits=hits,credential_scan_is_bounded_not_universal=True,
    index_and_worktree_exact_bytes=True if not errors else False,
    full_name_status_sha256=sha(out/'CACHED_NAME_STATUS.txt'),full_numstat_sha256=sha(out/'CACHED_NUMSTAT.txt'))
with (out/'PREPUSH_AUDIT01.json').open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
print(json.dumps({k:v for k,v in value.items() if k not in ('errors','credential_pattern_hits')},ensure_ascii=True))
if errors or hits:raise SystemExit(2)
