"""Close an actual local-data MATLAB invocation, without changing any figures."""
from pathlib import Path
import difflib, hashlib, json
from datetime import datetime, timezone
import fitz
from PIL import Image
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
D=Path('D:/hospital_ies_v21_20261023')
E=Path('E:/hospital_ies_v21_20261023')
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()
def read(p):return json.loads(Path(p).read_text('utf-8-sig'))
def write(p,x):
    with Path(p).open('x',encoding='utf-8',newline='\n') as f:json.dump(x,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
published=E/'reviews/final12_publication01/PUBLICATION_RECEIPT.json'
assert sha(published)=='f6331743752a9040af8f6ffa0cdd263ae1e695e03886927e073c61336b4be300'
p=read(published);checked=[]
for kind,root in [('results',D/'docs/v21/results/final12_actual01'),('figures',D/'docs/v21/figures/final12_actual01')]:
    for row in p['outputs'][kind]['files']:
        f=root/row['path'];assert f.stat().st_size==row['bytes'] and sha(f)==row['sha256'],str(f)
        checked.append(dict(path=str(f),sha256=row['sha256'],bytes=row['bytes']))
assert len(checked)==436
failed=read(HERE/'logs/actual_local_data_recipe01.command.json')
actual=read(HERE/'logs/actual_local_data_recipe02.command.json')
assert failed['exit_code']==1 and actual['exit_code']==0
qapath=HERE/'generated02/LOCAL_RECIPE_FUNCTIONAL_QA.json';qa=read(qapath)
assert qa['status']=='actual_recipe_completed_and_exact_native_semantics_checked'
assert qa['axes']==10 and qa['bar_series']==50 and qa['YData_value_count']==200
assert len(qa['figures'])==2 and all(x['exact_snapshot_equal'] for x in qa['figures'])
assert not qa['canonical_original_QA_reissued'] and not qa['new_full_page_visual_review_performed']
formats=[]
for fig in sorted((HERE/'generated02/final02').glob('*.fig')):
    stem=fig.with_suffix('');entry=dict(stem=fig.stem,files=[])
    for ext in ('.fig','.pdf','.svg','.png'):
        f=stem.with_suffix(ext);assert f.is_file() and f.stat().st_size>0
        entry['files'].append(dict(path=str(f),bytes=f.stat().st_size,sha256=sha(f)))
    pdf=fitz.open(stem.with_suffix('.pdf'));assert len(pdf)==1
    assert not pdf[0].get_images(full=True) and pdf[0].get_text().strip()
    entry['PDF_pages']=len(pdf);entry['PDF_embedded_images']=0;pdf.close()
    image=Image.open(stem.with_suffix('.png'));dpi=image.info.get('dpi');assert dpi and all(abs(x-600)<1 for x in dpi)
    entry['PNG_size']=list(image.size);entry['PNG_dpi']=list(dpi);image.close()
    svg=ET.parse(stem.with_suffix('.svg'))
    assert not [n for n in svg.iter() if n.tag.split('}')[-1]=='image']
    entry['SVG_embedded_images']=0;formats.append(entry)
assert len(formats)==2
diff=''.join(difflib.unified_diff((HERE/'run_local_recipe.m').read_text('utf-8').splitlines(True),(HERE/'run_local_recipe02.m').read_text('utf-8').splitlines(True),fromfile='run_local_recipe.m (failed original)',tofile='run_local_recipe02.m (cell receipt collection)'))
(HERE/'WRAPPER_COLLECTION_FIX.diff').write_text(diff,encoding='utf-8')
write(HERE/'INPUT_CURRENT_BYTE_CHECK436.json',dict(reference=dict(path=str(published),sha256=sha(published)),current_files_checked=436,current_files_equal_published_bindings=True,files=checked,not_a_claim_of_an_additional_preexecution_read=True))
write(HERE/'FUNCTIONAL_ENTRY_CLOSURE.json',dict(schema='hospital_ies.final12.local_recipe_closure.v1',created_utc=datetime.now(timezone.utc).isoformat(),status='actual_delivered_local_data_recipe_passed',actual_recipe=actual,retained_failed_wrapper=failed,native_semantic_QA=dict(path=str(qapath),sha256=sha(qapath)),current_input_files_equal_known_published_bindings=436,format_checks=formats,formal_delivered_figures_unchanged=True,new_complete_page_visual_reviews=0,new_physical_attempts=0,new_LP_solves=0,new_statistical_or_cohort_analyses=0,scope='Functional local CSV/MAT recipe, reopened native exact X/Y/CData/RGB/axis UserData/limits/ticks/labels/font checks and output format scan. Original canonical visual QA remains authoritative only for the original delivered figures.'))
print(json.dumps(dict(status='actual_local_recipe_and436_current_inputs_verified',native_figures=2,axes=10,bar_series=50,YData_values=200,exit_code=0),ensure_ascii=False))
