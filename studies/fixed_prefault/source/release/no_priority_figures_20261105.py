"""Render candidate fixed-branch rerun figures; never publish or run a model.

The three conditions share original requests. Normal is a frozen original
reference, while unattended/response identify the new calculation explicitly.
"""
from __future__ import annotations
import argparse
from copy import copy,deepcopy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import numpy as np

SCHEMA='hospital_ies.fixed_branch_rerun.plot_data.v1'
ROLES=('normal','unattended','response')
CARRIERS=(('cooling',2,'Cooling'),('heating',1,'Heating'),('electricity',0,'Electricity'))
HELPERS={
    'tier':Path('E:/hospital_ies_single_unit_20261104_run01/four_stage01/candidate/scripts/tier_draw_helper.py'),
    'total':Path('E:/hospital_ies_single_unit_20261104_run01/main_replacement01/candidate/scripts/render_figures.py')}
HEADERS=['Start relative (min)','End relative (min)','Demand (kW)','Normal (kW)','No dispatch (kW)','With dispatch (kW)']


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')


def validate_data(data):
    if data.get('schema')!=SCHEMA:raise ValueError('Only fixed-branch rerun plot data is accepted')
    if [t.get('id') for t in data.get('topics',[])]!=[f'T{i:02d}' for i in range(1,9)]:
        raise ValueError('Exactly eight ordered topics required')
    for topic in data['topics']:
        if (float(topic.get('fault_start_minute',-1))!=60. or float(topic.get('fault_end_minute',-1))!=120.
                or not np.isfinite(float(topic['end_minute'])) or float(topic['end_minute']) not in (180.,240.)):
            raise ValueError('Original 60–120 minute fault and complete registered window required')
        conditions=topic.get('conditions',{})
        if set(conditions)!=set(ROLES):raise ValueError('Normal, unattended and response source roles required')
        for role in ROLES:
            condition=conditions[role]
            if not isinstance(condition.get('source'),dict):raise ValueError('Explicit source metadata required')
            segments=condition.get('segments',[])
            previous=0.
            if not segments:raise ValueError('Missing native intervals')
            for segment in segments:
                start,end=float(segment['start_minute']),float(segment['end_minute'])
                demand,served=np.asarray(segment['demand_kW'],float),np.asarray(segment['served_kW'],float)
                if (not np.isfinite([start,end]).all() or end<=start or abs(start-previous)>1e-7
                        or demand.shape!=(4,3) or served.shape!=(4,3)
                        or not np.isfinite(demand).all() or not np.isfinite(served).all()
                        or np.any(demand<0.) or np.any(served < -1e-6) or np.any(served-demand>1e-6)):
                    raise ValueError('Invalid native shape, values, coverage or service bounds')
                previous=end
            if abs(previous-float(topic['end_minute']))>1e-7:raise ValueError('Incomplete topic window')


def adapt_topic(topic,priority):
    result=deepcopy({k:v for k,v in topic.items() if k!='conditions'})
    result.setdefault('start_minute',0.)
    result['base_topic_id']=topic['id']
    if priority is not None:
        result['id']=f"{topic['id']}_P{priority}";result['priority']=f'P{priority}'
    result['conditions']={}
    for role in ROLES:
        rows=[]
        for row in topic['conditions'][role]['segments']:
            demand,served=np.asarray(row['demand_kW'],float),np.asarray(row['served_kW'],float)
            demand=demand.sum(axis=0) if priority is None else demand[priority]
            served=served.sum(axis=0) if priority is None else served[priority]
            rows.append(dict(start_minute=float(row['start_minute']),end_minute=float(row['end_minute']),
                demand_kW=demand.tolist(),delivered_kW=served.tolist()))
        result['conditions'][role]=dict(source=deepcopy(topic['conditions'][role]['source']),segments=rows)
    return result


def axis_limits(values):
    values=np.asarray(values,float)
    if not values.size or not np.isfinite(values).all():raise ValueError('Finite actual plot values required')
    low,high=float(values.min()),float(values.max());span=high-low
    minimum_span=max(.2,.01*max(abs(low),abs(high)))
    if span<minimum_span:
        center=(low+high)/2.;lower=max(0.,center-minimum_span*.55);upper=center+minimum_span*.55
    else:lower=max(0.,low-.06*span);upper=high+.06*span
    lower=min(low,lower)
    if upper<=lower:upper=lower+.2
    return lower,upper


def export_paths(topic,priority,carrier):
    if priority is None:
        stem=f'{topic}_{carrier}'
        return f'power/png/{stem}.png',f'power/svg/{stem}.svg',f'data/{stem}.csv'
    stem=f'{topic}_{priority}_{carrier}'
    return f'four_stage/power/png/{priority.lower()}/{stem}.png',f'four_stage/power/svg/{stem}.svg',f'data/{stem}.csv'


def load_helper(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.plt.rcParams.update({'font.family':'Arial','font.size':8,'svg.fonttype':'none',
        'svg.hashsalt':'fixed-branches-20261105','axes.spines.top':False,'axes.spines.right':False,
        'axes.edgecolor':'#858585','axes.linewidth':.8,'legend.frameon':False})
    return module


def save_csv(path,values):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf8',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(['start_relative_min','end_relative_min','demand_kW','normal_kW','unattended_kW','response_kW'])
        writer.writerows(values)


def render_one(helper,topic,priority,destination):
    from matplotlib.ticker import ScalarFormatter
    edges,series=helper.align(topic)
    records=[]
    for carrier,j,name in CARRIERS:
        png_rel,svg_rel,csv_rel=export_paths(topic['base_topic_id'],priority,carrier)
        values=np.column_stack((edges[:-1]-60.,edges[1:]-60.,series['demand'][:,j],
            *[series[role][:,j] for role in ROLES])).tolist()
        save_csv(destination/csv_rel,values)
        low,high=axis_limits(np.concatenate([series[role][:,j] for role in (*ROLES,'demand')]))
        fig=helper.draw(topic,carrier,name,j,edges,series,'power')
        axis=fig.axes[0];axis.set_ylim(low,high)
        formatter=ScalarFormatter(useOffset=False);formatter.set_scientific(False)
        axis.yaxis.set_major_formatter(formatter)
        if np.all(series['demand'][:,j]==0.):
            axis.text(.5,.5,'No demand in this window',transform=axis.transAxes,ha='center',va='center',fontsize=8,color='#666666')
        fig.text(.12,.075,'No optimization: frozen pre-fault shares. Normal: frozen original reference.',fontsize=6.6,color='#555555')
        for rel,kwargs in ((png_rel,dict(dpi=600)),(svg_rel,dict(metadata={'Date':None}))):
            path=destination/rel;path.parent.mkdir(parents=True,exist_ok=True)
            fig.savefig(path,facecolor='white',**kwargs)
        helper.plt.close(fig)
        records.append(dict(topic=topic['base_topic_id'],priority=priority,carrier=carrier,
            png=png_rel,svg=svg_rel,csv=csv_rel,png_sha256=sha(destination/png_rel),
            svg_sha256=sha(destination/svg_rel),csv_sha256=sha(destination/csv_rel),
            power_y_limits_kW=[low,high],native_aligned_rows=len(values)))
    return records


def update_workbooks(bundle,destination,records):
    import openpyxl
    outputs=[]
    for priority in (None,'P0','P1','P2','P3'):
        relative='power/plot_data.xlsx' if priority is None else f'four_stage/power/excel/{priority.lower()}.xlsx'
        template=bundle/relative
        template_sha=sha(template)
        book=openpyxl.load_workbook(template)
        selected=sorted([r for r in records if r['priority']==priority],key=lambda r:Path(r['csv']).stem)
        expected=[Path(record['csv']).stem for record in selected]
        if book.sheetnames!=expected:raise ValueError('Workbook template sheet names differ from export mapping')
        for record in selected:
            sheet=book[Path(record['csv']).stem]
            with (destination/record['csv']).open(encoding='utf8',newline='') as stream:
                values=[[float(v) for v in row.values()] for row in csv.DictReader(stream)]
            old_end=sheet.max_row
            for row in sheet.iter_rows(min_row=2,max_row=max(old_end,len(values)+1),min_col=1,max_col=6):
                for cell in row:cell.value=None
            for r,row in enumerate(values,2):
                for c,value in enumerate(row,1):
                    cell=sheet.cell(r,c)
                    if not cell.has_style:cell._style=copy(sheet.cell(2,c)._style)
                    cell.value=value;cell.number_format='0.000000'
                if sheet.row_dimensions[r].height is None:sheet.row_dimensions[r].height=17
            for c,title in enumerate(HEADERS,1):sheet.cell(1,c).value=title
            sheet.freeze_panes='A2';sheet['I5']=record['power_y_limits_kW'][0];sheet['I6']=record['power_y_limits_kW'][1]
            sheet['I7']=record['csv_sha256']
            sheet['H8']='Normal reference';sheet['I8']='Frozen original reference; not recomputed in this campaign'
            sheet['H9']='No optimization';sheet['I9']='New calculation with frozen pre-fault branch shares'
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);book.save(target);book.close()
        if sha(template)!=template_sha:raise ValueError('Workbook template changed while reading')
        outputs.append(dict(path=relative,sha256=sha(target),sheets=24,template_sha256=template_sha))
    return outputs


def verify_exports(destination,records,workbooks):
    from PIL import Image
    import openpyxl
    checked_cells=0
    if len(records)!=120:raise ValueError('Expected 96 tier and 24 total figures')
    for record in records:
        for ext in ('png','svg','csv'):
            if sha(destination/record[ext])!=record[ext+'_sha256']:raise ValueError('Export hash mismatch')
        with Image.open(destination/record['png']) as image:
            image.verify()
        with Image.open(destination/record['png']) as image:
            dpi=image.info.get('dpi',())
            if image.width<4000 or image.height<3000 or len(dpi)!=2 or any(abs(v-600.)>.1 for v in dpi):
                raise ValueError('Unexpected paper-size raster geometry or DPI')
        svg=(destination/record['svg']).read_text(encoding='utf8')
        if '<text' not in svg or '<image' in svg:raise ValueError('Expected editable vector SVG with no embedded raster')
    for item in workbooks:
        book=openpyxl.load_workbook(destination/item['path'],read_only=False,data_only=False)
        for sheet in book:
            csv_path=destination/'data'/(sheet.title+'.csv')
            with csv_path.open(encoding='utf8',newline='') as stream:
                rows=[[float(value) for value in row.values()] for row in csv.DictReader(stream)]
            if [sheet.cell(1,c).value for c in range(1,7)]!=HEADERS or sheet.freeze_panes!='A2':
                raise ValueError('Workbook header or frozen-row policy drift')
            observed=list(sheet.iter_rows(min_row=2,max_row=len(rows)+1,min_col=1,max_col=6,values_only=True))
            for actual,expected in zip(observed,rows):
                for a,b in zip(actual,expected):
                    if not isinstance(a,(int,float)) or abs(a-b)>1e-10:raise ValueError('Workbook numeric values differ from CSV')
                    checked_cells+=1
            if sheet['I7'].value!=sha(csv_path):raise ValueError('Workbook CSV provenance hash differs')
        book.close()
    return dict(status='passed',figures=120,PNG=120,SVG=120,workbooks=5,worksheets=120,
        numeric_cells_checked=checked_cells,editable_SVG_text=True,embedded_SVG_rasters=0)


def contact_sheets(destination,records):
    from PIL import Image,ImageDraw
    output=destination/'previews';output.mkdir()
    paths=[]
    for topic in (f'T{i:02d}' for i in range(1,9)):
        items=sorted([r for r in records if r['topic']==topic],key=lambda r:('total' if r['priority'] is None else r['priority'],r['carrier']))
        sheet=Image.new('RGB',(1800,5*480),'#dddddd');draw=ImageDraw.Draw(sheet)
        for index,item in enumerate(items):
            with Image.open(destination/item['png']) as image:
                thumb=image.convert('RGB');thumb.thumbnail((590,445))
            x=(index%3)*600;y=(index//3)*480
            sheet.paste(thumb,(x+(600-thumb.width)//2,y+15))
            draw.text((x+8,y+460),(item['priority'] or 'total')+' '+item['carrier'],fill='black')
        target=output/(topic+'.png');sheet.save(target);paths.append(target.relative_to(destination).as_posix())
    return paths


def render(data_path,destination,bundle):
    data_path,destination,bundle=Path(data_path),Path(destination),Path(bundle)
    if destination.exists():raise ValueError('A fresh, nonexistent candidate directory is required')
    data=json.loads(data_path.read_text(encoding='utf8'));validate_data(data)
    source_sha=sha(data_path)
    for path in HELPERS.values():
        if not path.is_file():raise FileNotFoundError('Required original Python helper: '+str(path))
    destination.mkdir(parents=True)
    (destination/'data').mkdir();(destination/'scripts').mkdir()
    shutil.copyfile(data_path,destination/'data/PLOT_DATA.json')
    shutil.copyfile(__file__,destination/'scripts'/Path(__file__).name)
    helpers={};helper_sources={}
    for name,path in HELPERS.items():
        copied=destination/'scripts'/(name+'_draw_helper.py');shutil.copyfile(path,copied)
        helpers[name]=load_helper(copied,'fixed_'+name+'_draw')
        helper_sources[name]=dict(path=str(path),sha256=sha(path),copied=copied.relative_to(destination).as_posix())
    records=[]
    for topic in data['topics']:
        records+=render_one(helpers['total'],adapt_topic(topic,None),None,destination)
        for priority in range(4):
            records+=render_one(helpers['tier'],adapt_topic(topic,priority),f'P{priority}',destination)
        print('Exported '+topic['id']+': 15 PNG/SVG pairs',flush=True)
    workbooks=update_workbooks(bundle,destination,records)
    checks=verify_exports(destination,records,workbooks)
    previews=contact_sheets(destination,records)
    if sha(data_path)!=source_sha:raise ValueError('Input plot data changed while rendering')
    manifest=dict(schema='hospital_ies.fixed_branch_rerun.figure_exports.v1',backend='Python/matplotlib',
        source_data=dict(path=str(data_path),sha256=source_sha,copied='data/PLOT_DATA.json'),
        renderer=dict(path=str(Path(__file__).resolve()),sha256=sha(__file__)),helper_sources=helper_sources,
        normal_reference='frozen_original_reference_not_recomputed',
        unattended='new_fixed_prefault_branch_calculation',response='new_response_calculation',
        source_conditions={t['id']:{role:t['conditions'][role]['source'] for role in ROLES} for t in data['topics']},
        PNG_dpi=600,editable_SVG_text=True,figure_width_mm=183,figure_height_mm=145,
        figures=records,workbooks=workbooks,previews=previews,verification=checks,
        policy='Native held intervals; no smoothing, interpolation, offsets or quota reassignment; tight original axis policy',
        published=False)
    write_json(destination/'MANIFEST.json',manifest)
    print(json.dumps(checks),flush=True)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    command=sub.add_parser('render')
    command.add_argument('--data',type=Path,required=True)
    command.add_argument('--destination',type=Path,required=True)
    command.add_argument('--bundle',type=Path,required=True)
    args=parser.parse_args()
    render(args.data,args.destination,args.bundle)
