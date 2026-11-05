"""Pure data and export policy tests; no scientific trajectory or full render."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import numpy as np
import pytest


def api():
    path=Path(__file__).with_name('no_priority_figures_20261105.py')
    spec=importlib.util.spec_from_file_location('fixed_figures',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fixture_data():
    values=np.arange(1.,13.).reshape(4,3).tolist()
    return dict(schema='hospital_ies.fixed_branch_rerun.plot_data.v1',topics=[
        dict(id=f'T{i:02d}',title='test',end_minute=180.,fault_start_minute=60.,fault_end_minute=120.,
            conditions={role:dict(source={'role':role},segments=[dict(start_minute=0.,end_minute=180.,
                demand_kW=values,served_kW=values)]) for role in ('normal','unattended','response')})
        for i in range(1,9)])


def test_schema_keeps_original_values_and_adapts_tier_and_total():
    m=api();data=fixture_data();before=deepcopy(data)
    m.validate_data(data)
    tier=m.adapt_topic(data['topics'][0],2)
    total=m.adapt_topic(data['topics'][0],None)
    assert tier['id']=='T01_P2' and tier['priority']=='P2'
    assert tier['conditions']['unattended']['segments'][0]['delivered_kW']==[7.,8.,9.]
    assert total['conditions']['normal']['segments'][0]['delivered_kW']==[22.,26.,30.]
    assert data==before


@pytest.mark.parametrize('bad',['shape','gap','nonfinite','missing_topic'])
def test_schema_rejects_unusable_data(bad):
    m=api();data=fixture_data();segments=data['topics'][0]['conditions']['unattended']['segments']
    if bad=='shape':segments[0]['served_kW']=[1.,2.,3.]
    elif bad=='gap':segments[0]['start_minute']=1.
    elif bad=='nonfinite':segments[0]['served_kW'][0][0]=float('nan')
    else:data['topics'].pop()
    with pytest.raises(ValueError):m.validate_data(data)


def test_axis_limits_include_all_raw_values_without_magnifying_dust():
    m=api()
    lo,hi=m.axis_limits([100.,100.000001])
    assert lo<=100.<=100.000001<=hi
    assert hi-lo>=1.
    assert m.axis_limits([0.,0.])==pytest.approx((0.,.11))
    lo,hi=m.axis_limits([-1e-12,200.])
    assert lo<=-1e-12 and hi>=200.


def test_filename_layout_matches_existing_tree():
    m=api()
    assert m.export_paths('T03','P2','cooling')==(
        'four_stage/power/png/p2/T03_P2_cooling.png',
        'four_stage/power/svg/T03_P2_cooling.svg','data/T03_P2_cooling.csv')
    assert m.export_paths('T03',None,'cooling')==(
        'power/png/T03_cooling.png','power/svg/T03_cooling.svg','data/T03_cooling.csv')


def test_workbooks_replace_values_clear_old_rows_and_keep_template_style(tmp_path):
    import csv
    import openpyxl
    m=api();bundle=tmp_path/'original';destination=tmp_path/'candidate';records=[]
    for priority in (None,'P0','P1','P2','P3'):
        relative='power/plot_data.xlsx' if priority is None else f'four_stage/power/excel/{priority.lower()}.xlsx'
        path=bundle/relative;path.parent.mkdir(parents=True,exist_ok=True)
        book=openpyxl.Workbook();book.remove(book.active)
        for i in range(1,9):
            for carrier in ('cooling','electricity','heating'):
                png,svg,source=m.export_paths(f'T{i:02d}',priority,carrier)
                sheet=book.create_sheet(Path(source).stem)
                sheet['D1'].fill=openpyxl.styles.PatternFill('solid',fgColor='2878B5')
                sheet['A2'].number_format='0.000000';sheet['A99']=999.
                data=destination/source;data.parent.mkdir(parents=True,exist_ok=True)
                with data.open('w',newline='') as stream:
                    writer=csv.writer(stream);writer.writerow(['start','end','demand','normal','unattended','response'])
                    writer.writerow([0.,1.,10.,10.,5.,9.])
                records.append(dict(priority=priority,csv=source,csv_sha256=m.sha(data),power_y_limits_kW=[0.,11.]))
        book.save(path)
    workbooks=m.update_workbooks(bundle,destination,records)
    assert len(workbooks)==5
    book=openpyxl.load_workbook(destination/'four_stage/power/excel/p0.xlsx')
    sheet=book['T01_P0_cooling']
    assert sheet['A2'].value==0. and sheet['E2'].value==5.
    assert sheet['A99'].value is None
    assert sheet['D1'].fill.fgColor.rgb.endswith('2878B5')
    assert sheet['I7'].value==m.sha(destination/'data/T01_P0_cooling.csv')
    assert sheet.freeze_panes=='A2'
