"""Read-only source/artifact checks and PDF previews; no scientific plotting."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import xml.etree.ElementTree as ET
import fitz
from PIL import Image

BASE = Path(__file__).resolve().parent
DATA = BASE / 'data01'
FIGS = BASE / 'figures01'
OUT = BASE / 'artifact_qa01'
TRUSTED_EXPORT = 'fe50c8484958a2cf5c2ce94796df5af4dd473ad5693aefa5668dec2a87ee7956'

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def check_inputs():
    assert sha(BASE / 'EXPORT_RECEIPT.json') == TRUSTED_EXPORT
    r = read(BASE / 'EXPORT_RECEIPT.json')
    for item in r['files']:
        target = (DATA / item['path']).resolve()
        target.relative_to(DATA.resolve())
        assert sha(target) == item['sha256'], str(target)
    assert sha(r['gate_path']) == r['gate_sha256']
    return r

assert not OUT.exists(), 'Create-only artifact QA output required'
export = check_inputs()
original = read(FIGS / 'MATLAB_FIGURE_QA.json')
objects = read(BASE / 'ROOT_OBJECT_QA01.json')
assert original['status'] == objects['status'] == 'passed'
assert {r['configuration_id'] for r in original['cases']} == {'H1', 'H2'}
assert {r['configuration_id'] for r in objects['cases']} == {'H1', 'H2'}
OUT.mkdir()
before = {p.name: sha(p) for p in FIGS.iterdir() if p.is_file()}
checks = []
for cid in ('H1', 'H2'):
    stem = cid + '_functional_process'
    files = {ext: FIGS / (stem + '.' + ext) for ext in ('fig', 'svg', 'pdf', 'png')}
    assert all(p.is_file() and p.stat().st_size > 0 for p in files.values())
    meta = next(r for r in export['cases'] if r['configuration_id'] == cid)
    q = next(r for r in original['cases'] if r['configuration_id'] == cid)
    o = next(r for r in objects['cases'] if r['configuration_id'] == cid)
    assert q['raw_sha256'] == meta['process']['raw_sha256']
    assert q['csv_mat_max_absolute_difference'] == 0
    assert q['reopened_native_lines_checked'] == 14 and o['all_source_lines_exact']
    assert len(o['line_checks']) == 14 and all(x['source_csv_x_exact'] and x['source_csv_y_exact'] for x in o['line_checks'])
    assert q['functional_check_only'] is True and q['paired_H0'] is False
    assert q['request_delivery_resampled'] is False and q['inventory_interpolated'] is False
    svg = ET.parse(files['svg']).getroot()
    assert not any(e.tag.rsplit('}', 1)[-1].lower() == 'image' for e in svg.iter()), 'SVG raster element'
    with fitz.open(files['pdf']) as doc:
        assert len(doc) == 1
        page = doc[0]
        assert not page.get_images(full=True), 'PDF image object'
        fonts = sorted({f[3] for f in page.get_fonts(full=True)})
        assert any('TimesNewRoman' in f.replace(' ', '') for f in fonts), fonts
        assert any('SimSun' in f or 'Song' in f for f in fonts), fonts
        assert len(page.get_drawings()) > 0, 'Missing vector drawing'
        assert len(page.get_text().strip()) > 0, 'Missing text layer'
        page_size = [page.rect.width, page.rect.height]
        preview = OUT / (stem + '_pdf_preview.png')
        page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False).save(preview)
        vector_drawings = len(page.get_drawings())
    with Image.open(files['png']) as im:
        im.load()
        width, height = im.size
        dpi = im.info.get('dpi')
        assert width > 4000 and height > 4000, 'Unexpectedly small 600dpi export'
        if dpi is not None:
            assert all(abs(float(v) - 600) < 1 for v in dpi), dpi
            dpi_basis = 'PNG stored pixels-per-metre converted to dpi'
        else:
            assert abs(width - page_size[0] * 600 / 72) < 8
            assert abs(height - page_size[1] * 600 / 72) < 8
            dpi_basis = 'PNG pixels compared with corresponding MATLAB PDF physical dimensions'
    checks.append(dict(configuration_id=cid, source_native_lines_exact=14,
        csv_mat_error=0, raster_objects_svg=0, raster_objects_pdf=0,
        pdf_fonts=fonts, pdf_vector_drawings=vector_drawings, pdf_points=page_size,
        png_pixels=[width, height], png_dpi=dpi, dpi_basis=dpi_basis,
        preview_path=str(preview), preview_sha256=sha(preview),
        files=[dict(path=str(p), sha256=sha(p), bytes=p.stat().st_size) for p in files.values()],
        visual_review='PENDING_ROOT_VIEW_IMAGE', functional_check_only=True, paired_H0=False))
check_inputs()
after = {p.name: sha(p) for p in FIGS.iterdir() if p.is_file()}
assert before == after, 'Figure bytes changed during read-only QA'
result = dict(schema='hospital_ies.storage_functional_artifact_qa.v1',
    created_utc=datetime.now(timezone.utc).isoformat(),
    status='structural_and_source_checks_passed_visual_review_pending',
    input_export_receipt_sha256=TRUSTED_EXPORT,
    matlab_source_object_qa_sha256=sha(BASE / 'ROOT_OBJECT_QA01.json'),
    original_matlab_qa_sha256=sha(FIGS / 'MATLAB_FIGURE_QA.json'),
    all_figure_hashes_unchanged=True, pdf_preview_backend='PyMuPDF read-only rendering',
    scientific_plotting_backend='MATLAB R2024b', physics_attempts=0, cases=checks)
(OUT / 'ARTIFACT_RECEIPT.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(dict(status=result['status'], cases=len(checks), receipt_sha256=sha(OUT / 'ARTIFACT_RECEIPT.json'))))
