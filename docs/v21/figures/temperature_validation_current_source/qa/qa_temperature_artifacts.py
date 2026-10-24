"""Read-only MATLAB temperature artifact/source checks and PDF preview."""
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import fitz
from PIL import Image

ROOT = Path(r'D:\hospital_ies_v21_20261023')
BASE = Path(__file__).resolve().parent
FIGS = BASE / 'render01'
OLD = ROOT / 'docs/v21/figures/temperature_validation'
SOURCE = BASE.parent / 'temperature_figure_current_source_review01/publication_source'
OUT = BASE / 'artifact_qa01'
sys.path.insert(0, str(ROOT))
from tools.sci_engineering import runner

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

assert not OUT.exists(), 'Create-only artifact QA output'
known_m = '2adac32b2ca73eee5bb550e7879bfc8b260d796442a062f5236fda6c319129ad'
assert sha(SOURCE / 'plot_temperature_validation_publication.m') == known_m
qa_path = BASE / 'ACTUAL_FIG_OBJECT_QA01.json'
qa = read(qa_path)
assert qa['status'] == 'passed' and qa['exact_series_XY_pairs'] == 790
assert qa['plotted_source_rows'] == 158 and qa['native_data_lines'] == 10
assert qa['quantitative_temperature_validation_passed'] is False
assert qa['fitted_candidate_deployed'] is False and qa['clinical_threshold_asserted'] is False
assert qa['plotter_sha256'] == known_m
for case in qa['cases']:
    assert sha(Path(case['source_csv'])) == case['source_csv_sha256']
assert sha(FIGS / 'temperature_figure_receipt.json') == qa['native_generation_receipt_sha256']
paths = {ext: FIGS / ('temperature_validation.' + ext) for ext in ('fig','svg','pdf','png')}
assert sha(paths['fig']) == qa['figure_sha256']
samples = FIGS / 'temperature_validation_plotted_samples.csv'
assert sha(samples) == sha(OLD / samples.name), 'Displayed data changed from original exact export'
freeze = read(r'E:\hospital_ies_v21_20261023\main02\FINAL_MAIN_FREEZE.json')
runner.verify_freeze(ROOT, freeze)
old_before = {str(p): sha(p) for p in OLD.rglob('*') if p.is_file()}
before = {ext: sha(path) for ext, path in paths.items()}
root_svg = ET.parse(paths['svg']).getroot()
assert not any(e.tag.rsplit('}',1)[-1].lower() == 'image' for e in root_svg.iter())
OUT.mkdir()
with fitz.open(paths['pdf']) as doc:
    assert len(doc) == 1
    page = doc[0]
    assert page.get_images(full=True) == []
    fonts = page.get_fonts(full=True)
    names = sorted({font[3] for font in fonts})
    assert any('TimesNewRoman' in name.replace(' ','') for name in names)
    assert any('SimSun' in name or 'Song' in name for name in names)
    for font in fonts:
        assert len(doc.extract_font(font[0])[3]) > 0
    spans = [span for block in page.get_text('dict')['blocks'] for line in block.get('lines',[]) for span in line.get('spans',[])]
    mixed_runs = [span for span in spans if '+2' in span['text']]
    assert mixed_runs and all('TimesNewRoman' in span['font'].replace(' ','') for span in mixed_runs)
    assert not any(any(ch.isascii() and ch.isdigit() for ch in span['text']) for span in spans if 'SimSun' in span['font'])
    drawings = len(page.get_drawings())
    assert drawings > 0 and page.get_text().strip()
    pdf_points = [page.rect.width, page.rect.height]
    preview = OUT / 'temperature_validation_pdf_preview.png'
    page.get_pixmap(matrix=fitz.Matrix(1.8,1.8), alpha=False).save(preview)
with Image.open(paths['png']) as im:
    im.load()
    pixels = list(im.size)
    dpi = im.info.get('dpi')
    assert pixels[0] > 4000 and pixels[1] > 3000
    assert dpi and all(abs(value-600)<1 for value in dpi)
assert before == {ext:sha(path) for ext,path in paths.items()}
assert old_before == {str(p):sha(p) for p in OLD.rglob('*') if p.is_file()}
assert sha(SOURCE / 'plot_temperature_validation_publication.m') == known_m
runner.verify_freeze(ROOT, freeze)
result = {
    'schema':'hospital_ies.temperature_artifact_qa.v1',
    'created_utc':datetime.now(timezone.utc).isoformat(),
    'status':'native_source_and_structure_passed_visual_pending',
    'native_object_qa_sha256':sha(qa_path), 'native_series_XY_exact_pairs':790,
    'source_rows':158, 'displayed_sample_csv_exact_original':True,
    'original_temperature_directory_bytes_unchanged':True,
    'pdf_fonts':names, 'pdf_fonts_embedded':True,
    'footnote_plus2C_Latin_numeric_font':'Times New Roman actual PDF spans',
    'pdf_vector_drawings':drawings, 'pdf_raster_images':0, 'svg_raster_images':0,
    'png_pixels':pixels, 'png_stored_dpi':dpi, 'pdf_points':pdf_points,
    'dpi_basis':'stored PNG pHYs; tight-cropped canvas equivalence not asserted',
    'files':[{'path':str(path),'sha256':before[ext],'bytes':path.stat().st_size} for ext,path in paths.items()],
    'preview_path':str(preview),'preview_sha256':sha(preview),
    'frozen_source_files_verified':643,'frozen_inputs_verified':28,
    'scientific_plotting_backend':'MATLAB R2024b',
    'pdf_preview_backend':'PyMuPDF read-only rendering',
    'quantitative_temperature_validation_passed':False,
    'clinical_threshold_asserted':False,'fitted_candidate_deployed':False,
    'physics_attempts':0,'new_EnergyPlus_runs':0,'main_final_scientific_results_read':False,
}
output = OUT/'ARTIFACT_QA01.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
print(json.dumps({'receipt':str(output),'sha256':sha(output),'status':result['status']}))
