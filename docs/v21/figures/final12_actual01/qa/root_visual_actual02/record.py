"""Record root's four actual full-page tool views; not an automated visual judge."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

base = Path('E:/hospital_ies_v21_20261023')
qa = base/'final_blind01/figure_qa02'
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
known = {
    str(qa/'objects02/MATLAB_OBJECT_QA.json'): 'f72070efcc4329f5172d1f76929ba35b57ea2fecb1f7589b9a96964223aa15da',
    str(qa/'structure02/ARTIFACT_STRUCTURE_QA.json'): 'e57bf866f37ccfc5864fb7303c951018584d6a1a00d5aa4f90eb8d2b24a39e1e',
    str(base/'reviews/final12_independent_visual02/VISUAL_REVIEW_RECEIPT.json'): '8bf377b591a3bc46f66be90e45835c647df926560988d9ad96aa33fd824fe5c6',
}
for path, expected in known.items():
    assert sha(path) == expected
obj = json.loads((qa/'objects02/MATLAB_OBJECT_QA.json').read_text(encoding='utf-8'))
assert obj['object_checks_passed'] and obj['all_expected_matched'] and obj['failures'] == []
views, formats = [], []
for detail in obj['details']:
    stem = Path(detail['file']).stem
    for mode, path in [('PDF_FULL_ORIGINAL', qa/'previews02'/f'{stem}_PDF_FULL.png'),
                       ('NATIVE_600DPI_PNG_FULLPAGE_HIGH_DISPLAY', qa/'styled_figures02'/f'{stem}.png')]:
        views.append(dict(name=stem, mode=mode, path=str(path), bytes=path.stat().st_size,
                          sha256=sha(path), actually_viewed_by_root=True))
    for ext, ref in detail['formats'].items():
        assert sha(ref['path']) == ref['sha256']
        formats.append(dict(format=ext, **ref))
assert len(views) == 4 and len(formats) == 8
green_path = base/'reviews/final12_layout_fix01/GREEN_PAGE_BOUNDS01.json'
green = json.loads(green_path.read_text(encoding='utf-8'))
assert green['passed'] and all(row['passed'] for row in green['details'])
receipt = dict(schema='hospital_ies.root_actual_final12_visual_review.v1',
    utc=datetime.now(timezone.utc).isoformat(), visual_checks_passed=True,
    actual_full_pages=views, verified_formats=formats, known_receipts=known,
    boundary_regression=dict(path=str(green_path), sha256=sha(green_path), passed=True),
    observations=[
        'Viewed both final qa02 complete PDF proofs at original proof size and both complete native PNGs.',
        'F1 lower-left P1 electric ENS title is now wholly visible with clear white margin; all6axes readable.',
        'F2 all4axes, legends, labels and category ticks readable; no new clipping or overlap observed.',
        'Five-policy legends and12-block/category labels agree with the audited scope; no independent footnote block exists.',
        'Bars are means; no plotted confidence interval or clinical reliability inference was visually certified.'],
    display_scope='PNG tool displays were resized4252x3307->1792x1393 and4252x3071->1856x1340; full page seen, not pixel-exact comparison.',
    earlier_failed_visual_preserved=str(base/'reviews/final12_independent_visual01/VISUAL_REVIEW_RECEIPT.json'),
    raw_or_source_or_statistical_values_changed=False, scientific_acceptance_issued=False)
target = Path(__file__).parent/'ROOT_VISUAL_REVIEW.json'
with target.open('x', encoding='utf-8') as handle:
    json.dump(receipt, handle, indent=2, ensure_ascii=False)
print(json.dumps(dict(receipt=str(target), sha256=sha(target), actual_full_pages=4,
                     visual_pass=True, not_a_scientific_gate=True)))
