"""Read-only deflate budget estimate; not a delivery archive or final inventory."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib, json, sys, zlib

ROOT = Path(r'D:\hospital_ies_v20_20261019')
sys.path.insert(0, str(ROOT))
from publication.build_v20_release import select

names, _ = select()
rows = []
for name in names:
    path = ROOT / name
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    count = compressed = 0
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            count += len(chunk)
            digest.update(chunk)
            compressed += len(compressor.compress(chunk))
    compressed += len(compressor.flush())
    rows.append(dict(path=name, observed_bytes=count, sha256=digest.hexdigest(),
                     raw_deflate_bytes=compressed,
                     estimated_zip_entry_bytes=compressed + 76 + 2 * len(('Hospital_IES_V20/' + name).encode('utf8'))))
value = dict(schema='hospital_ies.v20.preliminary_compression_estimate.v1',
    utc=datetime.now(timezone.utc).isoformat(), status='preliminary_not_final_delivery_proof',
    source_root=str(ROOT), selected_files=len(rows),
    observed_uncompressed_bytes=sum(r['observed_bytes'] for r in rows),
    estimated_zip_bytes=22 + sum(r['estimated_zip_entry_bytes'] for r in rows),
    live_source_may_grow=True, post_push_proofs_not_yet_included=True,
    no_archive_created=True, final_cap_must_be_checked_on_actual_zip=True, files=rows)
destination=Path(__file__).with_name('COMPRESSION_ESTIMATE02.json')
with destination.open('x', encoding='utf8') as stream:
    json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
print(json.dumps({k:v for k,v in value.items() if k!='files'}, ensure_ascii=True))
