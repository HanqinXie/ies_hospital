"""Read provided local PDFs; do not open archives or modify source material."""
import json,hashlib,re
from pathlib import Path
import fitz
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/v43_p1b_20261012_235940/references'
OUT.mkdir(exist_ok=True)
records=[];hits=[]
for directory in ('冷热电以及其他指标换算关系','故障模拟'):
    for pdf in sorted((ROOT.parent/directory).glob('*.pdf')):
        doc=fitz.open(pdf);pages=[p.get_text(sort=True) for p in doc]
        digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
        records.append(dict(path=str(pdf),sha256=digest,pages=len(pages),text_chars=sum(map(len,pages))))
        (OUT/(pdf.stem+'.txt')).write_text('\n'.join(f'\n=== PDF PAGE {i+1} ===\n'+p for i,p in enumerate(pages)),encoding='utf-8')
        pattern=r'ramp.{0,25}(rate|limit|up|down)|automatic transfer|\bATS\b|reverse power|no.export|minimum.{0,25}(power|output|load)|start.up.{0,25}(time|delay)|diesel.{0,45}(second|minute)|\b900\b|\b0\.4\b'
        for i,page in enumerate(pages):
            flat=' '.join(page.split())
            for m in re.finditer(pattern,flat,re.I):
                hits.append(dict(file=pdf.name,page=i+1,excerpt=flat[max(0,m.start()-180):m.end()+250]))
(OUT/'manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'keyword_hits.json').write_text(json.dumps(hits,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(pdfs=len(records),pages=sum(r['pages'] for r in records),hits=len(hits))))
for h in hits:print(json.dumps(h,ensure_ascii=False))
