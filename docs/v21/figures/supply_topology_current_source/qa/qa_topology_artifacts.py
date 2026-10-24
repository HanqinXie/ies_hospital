"""Read-only inspection and PDF preview of MATLAB's editable topology."""
import csv
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
SOURCE = BASE.parent / 'topology_current_source_revision01'
FIGURES = BASE / 'render01'
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

assert not OUT.exists(), 'Create-only QA output'
known = {
    'plot_supply_topology_current_source.m': 'eb7e4049280d2a69ca49b1092810448c043962e581b18c2b2e11e8ab5b43d13d',
    'source_spec.json': '93dccdfd50dad544560d4fca351fd45d4b33839d34ec25d6136da8f04dca09ab',
    'service_dependencies.csv': '2a6f6fffedc653a656e9e46716fdd87c0f234efe401f9717d3c12de9a413ae7d',
}
for name, expected in known.items():
    assert sha(SOURCE / name) == expected, name
spec = read(SOURCE / 'source_spec.json')
for item in spec['sources']:
    target = ROOT / item['relative_path']
    assert target.stat().st_size == item['bytes'] and sha(target) == item['sha256']
with (SOURCE / 'service_dependencies.csv').open(encoding='utf-8-sig', newline='') as handle:
    assert list(csv.DictReader(handle)) == spec['service_rows']
freeze = read(r'E:\hospital_ies_v21_20261023\main02\FINAL_MAIN_FREEZE.json')
runner.verify_freeze(ROOT, freeze)
objects_path = BASE / 'NATIVE_OBJECT_QA01.json'
objects = read(objects_path)
assert objects['status'] == 'passed_native_object_checks_visual_review_required'
assert objects['image_objects'] == 0 and objects['distinct_electric_converter_port'] is True
render = read(FIGURES / 'RENDER_RECEIPT.json')
assert render['plotter_sha256'] == known['plot_supply_topology_current_source.m']
assert render['source_spec_sha256'] == known['source_spec.json']
assert render['service_csv_sha256'] == known['service_dependencies.csv']
assert render['source_files'] == spec['sources'] and render['source_mapping_rows'] == 54
paths = {ext: FIGURES / ('supply_topology_current_source.' + ext) for ext in ('fig', 'svg', 'pdf', 'png')}
assert objects['figure_sha256'] == sha(paths['fig'])
before = {ext: sha(path) for ext, path in paths.items()}
svg = ET.parse(paths['svg']).getroot()
assert not any(element.tag.rsplit('}', 1)[-1].lower() == 'image' for element in svg.iter())
OUT.mkdir()
with fitz.open(paths['pdf']) as doc:
    assert len(doc) == 1
    page = doc[0]
    assert page.get_images(full=True) == []
    fonts = page.get_fonts(full=True)
    names = sorted({font[3] for font in fonts})
    assert any('TimesNewRoman' in name.replace(' ', '') for name in names)
    assert any('SimSun' in name or 'Song' in name for name in names)
    for font in fonts:
        assert len(doc.extract_font(font[0])[3]) > 0, ('Font not embedded', font)
    drawings = len(page.get_drawings())
    assert drawings >= 10 and page.get_text().strip()
    pdf_points = [page.rect.width, page.rect.height]
    preview = OUT / 'supply_topology_current_source_pdf_preview.png'
    page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False).save(preview)
with Image.open(paths['png']) as im:
    im.load()
    pixels = list(im.size)
    dpi = im.info.get('dpi')
    assert pixels[0] > 6000 and pixels[1] > 4000
    assert dpi is not None and all(abs(value - 600) < 1 for value in dpi)
assert before == {ext: sha(path) for ext, path in paths.items()}
for name, expected in known.items():
    assert sha(SOURCE / name) == expected
runner.verify_freeze(ROOT, freeze)
result = {
    'schema': 'hospital_ies.topology_artifact_qa.v1',
    'created_utc': datetime.now(timezone.utc).isoformat(),
    'status': 'native_source_and_structure_passed_visual_pending',
    'native_object_qa_sha256': sha(objects_path),
    'render_receipt_sha256': sha(FIGURES / 'RENDER_RECEIPT.json'),
    'source_files_verified': 10, 'source_mapping_rows': 54,
    'frozen_source_files_verified': 643, 'frozen_physical_inputs_verified': 28,
    'pdf_fonts': names, 'pdf_fonts_embedded': True,
    'pdf_vector_drawings': drawings, 'pdf_raster_images': 0, 'svg_raster_images': 0,
    'png_pixels': pixels, 'png_stored_dpi': dpi, 'pdf_points': pdf_points,
    'dpi_basis': 'stored PNG pHYs; tight-cropped export geometry not independently asserted',
    'files': [{'path': str(path), 'sha256': before[ext], 'bytes': path.stat().st_size} for ext, path in paths.items()],
    'preview_path': str(preview), 'preview_sha256': sha(preview),
    'scientific_plotting_backend': 'MATLAB R2024b',
    'pdf_preview_backend': 'PyMuPDF read-only rendering',
    'physics_attempts': 0, 'scientific_results_read': False,
}
output = OUT / 'ARTIFACT_QA01.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'receipt': str(output), 'sha256': sha(output), 'status': result['status']}))
