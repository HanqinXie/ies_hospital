"""Regression for actual full-page clipping; reads PDF/PNG without changing them."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import fitz
import numpy as np
from PIL import Image

p = argparse.ArgumentParser()
p.add_argument('--directory', required=True, type=Path)
p.add_argument('--output', required=True, type=Path)
a = p.parse_args()
assert not a.output.exists()
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
details = []
for pdf in sorted(a.directory.glob('F[12]_*.pdf')):
    png = pdf.with_suffix('.png')
    before = {str(path): sha(path) for path in (pdf, png)}
    with fitz.open(pdf) as document:
        assert len(document) == 1
        page = document[0]
        boxes = [span['bbox'] for block in page.get_text('dict')['blocks']
                 for line in block.get('lines', []) for span in line.get('spans', [])
                 if span.get('text', '').strip()]
        assert boxes
        text_margins_pt = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                           page.rect.width-max(b[2] for b in boxes),
                           page.rect.height-max(b[3] for b in boxes)]
    with Image.open(png) as original:
        rgba = original.convert('RGBA')
        white = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
        white.alpha_composite(rgba)
        rgb = np.asarray(white.convert('RGB'))
        mask = rgb.min(axis=2) < 245
        yy, xx = np.where(mask)
        assert len(xx)
        ink_margins_px = [int(xx.min()), int(yy.min()), int(rgb.shape[1]-1-xx.max()),
                          int(rgb.shape[0]-1-yy.max())]
        dimensions = [rgb.shape[1], rgb.shape[0]]
    issues = []
    if min(text_margins_pt) < 0:
        issues.append('PDF text metric bounding box extends beyond physical page')
    if min(ink_margins_px) < 2:
        issues.append('PNG nonwhite ink touches/clips a physical page edge')
    assert all(sha(path) == value for path, value in before.items())
    details.append(dict(name=pdf.stem, input_sha256=before,
        pdf_text_margins_left_top_right_bottom_pt=text_margins_pt,
        png_ink_margins_left_top_right_bottom_px=ink_margins_px,
        png_dimensions_px=dimensions, issues=issues, passed=not issues))
assert len(details) == 2
receipt = dict(schema='hospital_ies.final12_fullpage_boundary_regression.v1',
    utc=datetime.now(timezone.utc).isoformat(), directory=str(a.directory),
    guard='PDF text within page and at least2px white raster edge; no scientific tolerance change',
    details=details, passed=all(row['passed'] for row in details),
    input_files_modified=False, figures_generated=False, scientific_plot_backend='MATLAB')
a.output.parent.mkdir(parents=True, exist_ok=True)
with a.output.open('x', encoding='utf-8') as handle:
    json.dump(receipt, handle, indent=2, ensure_ascii=False)
print(json.dumps(receipt, ensure_ascii=True))
sys.exit(0 if receipt['passed'] else 1)
