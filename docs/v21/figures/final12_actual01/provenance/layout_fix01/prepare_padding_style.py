"""Derive one F1 layout-property fix from the byte-pinned page-only helper."""
from pathlib import Path
import hashlib
import difflib
import json

here = Path(__file__).resolve().parent
source = Path('E:/hospital_ies_v21_20261023/q1_supplement01/figure_qa01/style_q1_fullpage.m')
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert sha(source) == 'd60c0ba1c88a6c08796f9724deebc3b5e52bd12065f905578a3e3c001759b32c'
original = source.read_text(encoding='utf-8-sig')
text = original
replacements = [
    ('function receipt=style_q1_fullpage(sourceDir,outputDir)',
     'function receipt=style_final12_f1_padding(sourceDir,outputDir)'),
    ('    oldPaper=struct(',
     "    fontObjects=findall(f,'-property','FontName');fontBefore=fontSnapshot(fontObjects);\n"
     "    layouts=findall(f,'-isa','matlab.graphics.layout.TiledChartLayout');assert(numel(layouts)==1);\n"
     "    paddingBefore=string(layouts.Padding);\n"
     "    if startsWith(names(i),'F1_'),assert(paddingBefore==\"compact\");layouts.Padding='loose';end\n"
     "    paddingAfter=string(layouts.Padding);\n"
     '    oldPaper=struct('),
    ("    savefig(f,stem+'.fig');print(f,stem+'.svg','-dsvg');",
     "    assert(isequaln(fontBefore,fontSnapshot(fontObjects)),'Layout changed original font properties.');\n"
     "    savefig(f,stem+'.fig');print(f,stem+'.svg','-dsvg');"),
    ("    formats=struct;", "    assert(isequaln(fontBefore,fontSnapshot(fontObjects)),'Export changed original font properties.');\n    formats=struct;"),
    ("    details{i}=struct('name',names(i),'scientific_X_Y_Z_CData_UserData_axislimits_exact',true,",
     "    details{i}=struct('name',names(i),'padding_before',paddingBefore,'padding_after',paddingAfter, ...\n"
     "        'font_properties_exact',true,'scientific_X_Y_Z_CData_UserData_axislimits_exact',true,"),
    ("'hospital_ies.Q1_fullpage_style.v1'", "'hospital_ies.final12_padding_fullpage_style.v1'"),
    ("    'changes','Page/export geometry only: fixed physical paper and unfilled white vector canvas; no series/font/statistical change', ...",
     "    'base_fullpage_helper_sha256','d60c0ba1c88a6c08796f9724deebc3b5e52bd12065f905578a3e3c001759b32c', ...\n"
     "    'changes','Only F1 tiledlayout.Padding compact->loose; original page-only exports and exact data/fonts/axis limits retained', ..."),
    ("'Q1_FULLPAGE_STYLE_RECEIPT.json'", "'FINAL12_PADDING_FULLPAGE_STYLE_RECEIPT.json'"),
]
for before, after in replacements:
    assert text.count(before) == 1, before
    text = text.replace(before, after)
text += "\nfunction state=fontSnapshot(handles)\nstate=cell(numel(handles),1);\nfor k=1:numel(handles)\n h=handles(k);assert(isvalid(h));v=struct('class',class(h),'FontName',h.FontName);\n for field=[\"FontSize\",\"FontUnits\"],if isprop(h,field),v.(field)=h.(field);end,end\n state{k}=v;\nend\nend\n"
target = here/'style_final12_f1_padding.m'
with target.open('x', encoding='utf-8', newline='\n') as handle:
    handle.write(text)
diff = ''.join(difflib.unified_diff(original.splitlines(True), text.splitlines(True),
                                  fromfile=str(source), tofile=str(target)))
with (here/'PAGE_HELPER_MINIMAL_DIFF.patch').open('x', encoding='utf-8') as handle:
    handle.write(diff)
assert sha(source) == 'd60c0ba1c88a6c08796f9724deebc3b5e52bd12065f905578a3e3c001759b32c'
receipt = dict(status='style_source_prepared_not_executed', source=str(source), source_sha256=sha(source),
    target=str(target), target_sha256=sha(target), production_physics_or_statistics_changed=False,
    intended_change='F1 compact tiled padding underestimates decimal-axis label inset; switch only F1 Padding to loose',
    failing_test='RED_PAGE_BOUNDS01.json: F1 PDF minX=-3.172615pt and PNG left ink=0px; F2 passed',
    scientific_X_Y_Z_CData_UserData_axislimits_and_original_fonts_must_remain_exact=True)
with (here/'PREPARATION_RECEIPT.json').open('x', encoding='utf-8') as handle:
    json.dump(receipt, handle, indent=2)
print(json.dumps(receipt))
