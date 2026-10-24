"""Read-only artifact structure inspection; visual review is always separate."""
import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
import fitz
from PIL import Image

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for b in iter(lambda:stream.read(1048576),b''):h.update(b)
    return h.hexdigest()

def scan_svg(path):
    tree=ET.parse(path);images=[];external=[];fonts=set();text=0;paths=0
    for element in tree.iter():
        tag=element.tag.rsplit('}',1)[-1]
        if tag in ['image','feImage','foreignObject']:images.append(dict(tag=tag,attributes=dict(element.attrib)))
        if tag=='text':text+=1
        if tag=='path':paths+=1
        for key,value in element.attrib.items():
            if key.rsplit('}',1)[-1]=='href' and not value.startswith(('#','data:')):external.append(value)
            if key=='font-family':fonts.add(value)
            if key=='style':
                fonts.update(re.findall(r'font-family\s*:\s*([^;]+)',value))
    return dict(image_elements=len(images),external_references=external,font_families=sorted(fonts),text_elements=text,path_elements=paths,all_graphics_are_native_vector=len(images)==0 and not external)

def scan_pdf(path):
    images=0;resources=0;fonts={};sizes=[];spanfonts=set();spans=[]
    with fitz.open(path) as doc:
        for page in doc:
            images+=len(page.get_image_info(xrefs=True));resources+=len(page.get_images(full=True));sizes.append([page.rect.width,page.rect.height])
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines',[]):
                    for span in line.get('spans',[]):
                        spanfonts.add(span['font']);spans.append(dict(page=page.number+1,font=span['font'],text=span['text']))
            for item in page.get_fonts(full=True):
                xref=item[0]
                if xref in fonts:continue
                basename,extension,fonttype,data=doc.extract_font(xref)
                fonts[xref]=dict(xref=xref,resource_basefont=item[3],extracted_name=basename,extension=extension,font_type=fonttype,embedded_bytes=len(data),embedded_sha256=hashlib.sha256(data).hexdigest() if data else None)
        pages=len(doc)
    return dict(pages=pages,page_size_points=sizes,displayed_images=images,image_resources=resources,fonts=list(fonts.values()),text_span_font_names=sorted(spanfonts),text_spans=spans,font_embedding_checked_by_actual_bytes=True)

def scan_png(path,cm=None):
    with Image.open(path) as image:
        size=list(image.size);dpi=image.info.get('dpi')
    theory=None;inferred=None;status='NOT_VERIFIED'
    if cm is not None:
        theory=[float(v)/2.54*600 for v in cm]
        inferred=[size[i]/float(cm[i])*2.54 for i in range(2)]
        if all(abs(size[i]-theory[i])<=2 for i in range(2)):status='passed'
    return dict(pixel_dimensions=size,dpi_metadata=list(dpi) if dpi else None,dpi_metadata_600=bool(dpi and all(abs(float(v)-600)<=.02 for v in dpi)),figure_geometry_theoretical_pixels=theory,inferred_dpi_from_figure_geometry=inferred,theoretical_600dpi_status=status,metadata_modified=False,limit='Absent pHYs is retained. Geometry pass is theoretical FIG-size agreement; cropping mismatch remains NOT_VERIFIED, requiring actual export-extent review.')

def allowed_font(name):
    name=re.sub(r'^[A-Z]{6}\+','',name)
    return name in ['TimesNewRomanPSMT','TimesNewRomanPS-BoldMT','TimesNewRomanPS-ItalicMT','TimesNewRomanPS-BoldItalicMT','Times New Roman','SimSun','SimSun-ExtB','宋体']

def inspect_pdf_fonts(pdf):
    issues=[];math_spans=[];math_resources=[]
    if not pdf.get('fonts') or not pdf.get('text_spans'):issues.append('PDF font/text evidence is empty: NOT_VERIFIED')
    for f in pdf.get('fonts',[]):
        name=re.sub(r'^[A-Z]{6}\+','',f['resource_basefont'])
        if not f['embedded_bytes']:issues.append('PDF font resource has no embedded font bytes: '+name)
        if name=='mwb_cmsy10':math_resources.append(f)
        elif not allowed_font(name):issues.append('Unexpected PDF font resource: '+name)
    for span in pdf.get('text_spans',[]):
        name=span['font']
        if name=='mwb_cmsy10':
            if span['text'] not in ['#','\u00d7']:issues.append('Unapproved math font span characters: '+repr(span['text']))
            math_spans.append(span)
        elif not allowed_font(name):issues.append('Unexpected PDF rendered font: '+name)
    evidence=None
    if math_spans or math_resources:
        if not math_spans or not math_resources:issues.append('Math font resource and actual symbol span must both be present')
        encoding=Path('C:/Program Files/MATLAB/R2024b/sys/fonts/type1/cm/encodings/mwb_cmsy10.enc')
        known='7f052c4c607616c213f141504e2dab6a7524bc499cc1305490a7437555a1ef0a'
        if not encoding.is_file() or sha(encoding)!=known:issues.append('Installed exact math font encoding source differs')
        elif not re.search(r'^35\s+multiply\s*$',encoding.read_text(encoding='ascii'),re.MULTILINE):issues.append('Installed encoding does not map glyph code35 to multiply')
        evidence=dict(font='mwb_cmsy10',encoding_path=str(encoding),encoding_sha256=known,glyph_code=35,glyph_name='multiply',allowed_span_texts=['#','\u00d7'],actual_spans=math_spans,interpretation='PDF extraction U+0023 maps to multiply via the exact installed MATLAB encoding; original PDF text is not rewritten')
    return dict(issues=issues,math_symbol_evidence=evidence)

def run(args):
    rp=Path(args.matlab_receipt).resolve();out=Path(args.output).resolve()
    if out.exists():raise ValueError('Fresh output required')
    if sha(rp)!=args.matlab_sha:raise ValueError('Known MATLAB receipt SHA differs')
    receipt=json.loads(rp.read_text(encoding='utf-8-sig'))
    if receipt.get('schema')!='hospital_ies.matlab_object_audit.v2':raise ValueError('Wrong MATLAB receipt schema')
    details_in=receipt.get('details')
    count=receipt.get('expected_figure_count')
    if not isinstance(details_in,list) or not details_in:raise ValueError('Empty MATLAB artifact details: NOT_VERIFIED')
    if type(count) is not int or count<=0 or count!=len(details_in) or receipt.get('matched_figure_count')!=count:raise ValueError('MATLAB artifact count does not close')
    if receipt.get('object_checks_passed') is not True or receipt.get('all_expected_matched') is not True or receipt.get('failures')!=[]:raise ValueError('Native MATLAB validation is failed, unknown or incomplete')
    if any(d.get('status')!='passed' for d in details_in) or len({d['file'] for d in details_in})!=count:raise ValueError('Failed, unknown or duplicated native FIG detail')
    before={str(rp):sha(rp)};details=[];issues=[]
    if receipt.get('object_checks_passed') is not True:issues.append('MATLAB native object checks did not pass')
    for d in receipt['details']:
        if d.get('status')!='passed':issues.append('Failed native figure '+d['file']);continue
        formats=d['formats']
        for ext in ['fig','pdf','svg','png']:
            p=Path(formats[ext]['path'])
            if p.stat().st_size!=formats[ext]['bytes'] or sha(p)!=formats[ext]['sha256']:raise ValueError('MATLAB-bound artifact drift: '+str(p))
            before[str(p.resolve())]=formats[ext]['sha256']
        svg=scan_svg(formats['svg']['path']);pdf=scan_pdf(formats['pdf']['path']);png=scan_png(formats['png']['path'],d['figure_size_cm'])
        problems=[]
        if svg['image_elements'] or svg['external_references']:problems.append('SVG image or external reference')
        if pdf['displayed_images'] or pdf['image_resources']:problems.append('PDF raster image')
        font_check=inspect_pdf_fonts(pdf);problems.extend(font_check['issues'])
        if font_check['math_symbol_evidence']:
            authority=font_check['math_symbol_evidence'];before[authority['encoding_path']]=authority['encoding_sha256']
        if not (png['dpi_metadata_600'] or png['theoretical_600dpi_status']=='passed'):problems.append('PNG 600 dpi NOT_VERIFIED')
        details.append(dict(file=d['file'],svg=svg,pdf=pdf,pdf_font_check=font_check,png=png,issues=problems,structural_status='passed' if not problems else 'NOT_VERIFIED',visual_review='NOT_RUN'))
        issues.extend(d['file']+': '+problem for problem in problems)
    for p,h in before.items():
        if sha(p)!=h:raise ValueError('Input/artifact changed during scan')
    result=dict(schema='hospital_ies.artifact_structure.v2',matlab_receipt=str(rp),matlab_receipt_sha256=args.matlab_sha,details=details,issues=issues,structure_checks_passed=not issues,input_and_artifact_hashes_unchanged=True,visual_review='NOT_RUN',scientific_gate=False,actual_physics_started=0)
    out.mkdir(parents=True);(out/'ARTIFACT_STRUCTURE_QA.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(receipt=str(out/'ARTIFACT_STRUCTURE_QA.json'),issues=len(issues),structure_checks_passed=not issues,visual_review='NOT_RUN')))
    if issues:raise SystemExit(1)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--matlab-receipt',required=True);p.add_argument('--matlab-sha',required=True);p.add_argument('--output',required=True);run(p.parse_args())
