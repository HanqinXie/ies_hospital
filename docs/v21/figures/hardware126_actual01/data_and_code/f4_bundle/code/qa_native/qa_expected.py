"""Read-only expected values for the frozen MATLAB plotter; no simulations."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from scipy.io import loadmat

SOURCE='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
PLOTTER_SHA='069489e31952f39b8b29d8db71823e0a3eb4ec982bc2d1955ebf1c9f8bceb5a9'
APPROVED_PLOTTERS={PLOTTER_SHA:'original_numeric_plotter_legacy_labels','e4d95dfef43b1b5b9bb19446098a1cc6e05f6e1a4df5af55bae2320e9deeb0c7':'publication_single_line_class_labels_and_default_bar_fonts'}
COLORS=[[.45,.45,.45],[.10,.35,.62],[.56,.37,.67],[.13,.52,.39],[.82,.37,.12]]
ALIASES=list('RMFCA')
CRITICAL=[f'{p}_{c}_ENS_kWh' for p in ['P0','P1'] for c in 'ehc']
COST=['P2_union_material_minutes','P3_union_material_minutes','energy_cost_USD','total_CO2_kg_conditional']
HARDWARE=['critical_initial_handoff_gap_minutes','critical_e_ENS_kWh','critical_c_ENS_kWh','P3_e_ENS_kWh','energy_cost_USD','total_CO2_kg_conditional']
BOUNDARY=['critical_initial_handoff_gap_minutes','critical_post_handoff_gap_minutes','critical_observed_continuous_minutes']

def finite(v):
    return isinstance(v,(int,float,np.number)) and math.isfinite(v)

def present(v):
    return v is not None and str(v) not in ['', 'NaN'] and not (isinstance(v,float) and math.isnan(v))

def eligible(r):
    return str(r.get('eligible','')).lower() in ['true','1','1.0']

def block_mean(rows,field):
    groups=defaultdict(list)
    for r in rows:
        if finite(r.get(field)): groups[str(r['source_block_id'])].append(float(r[field]))
    return float(np.mean([np.mean(v) for v in groups.values()])) if groups else None

def balanced_five(rows):
    accepted=[];excluded=[]
    for scene in sorted({str(r['scenario_id']) for r in rows}):
        group=[r for r in rows if str(r['scenario_id'])==scene]
        if len(group)==5 and sorted(r['policy_alias'] for r in group)==sorted(ALIASES) and all(eligible(r) and all(finite(r.get(f)) for f in CRITICAL) for r in group): accepted.extend(group)
        else: excluded.append(scene)
    return accepted,excluded

def four_cell_effects(values):
    if set(values)!=set(['00','10','01','11']) or not all(finite(v) for v in values.values()): raise ValueError('Four complete finite actual cells required')
    a,b,c,d=[values[k] for k in ['00','10','01','11']]
    return [b-a,c-a,d-a,d-b-c+a]

def hardware_deltas(rows):
    identity=['source_block_id','model_id','source_binding_sha256','scenario_sha256','policy_id','policy_sha256','fault_target']
    raw=['critical_initial_handoff_gap_minutes','P0_e_ENS_kWh','P1_e_ENS_kWh','P0_c_ENS_kWh','P1_c_ENS_kWh','P3_e_ENS_kWh','energy_cost_USD','total_CO2_kg_conditional']
    required=identity+raw+['scenario_id','configuration_id','configuration_sha256','window_minutes','eligible','status','unknown_tail_minutes','policy_alias']
    if not rows or any(any(f not in r for f in required) for r in rows): return [],['Required CASE identities or hardware metrics absent']
    def uniform(v,fields):
        return bool(v) and all(all(present(r[f]) for r in v) and len({str(r[f]) for r in v})==1 for f in fields)
    if not uniform(rows,['model_id','source_binding_sha256']): return [],['Mixed/unknown hardware model or source']
    for a in 'CA':
        if not uniform([r for r in rows if r['policy_alias']==a],['policy_id','policy_sha256']): return [],['Missing or mixed policy '+a]
    for c in ['H0','H1','H2']:
        if not uniform([r for r in rows if r['configuration_id']==c],['configuration_sha256']): return [],['Missing or mixed config '+c]
    out=[];unknown=[]
    for scene in sorted({r['scenario_id'] for r in rows}):
        for a in 'CA':
            v=[r for r in rows if r['scenario_id']==scene and r['policy_alias']==a];reason=None
            if len(v)!=3 or {r['configuration_id'] for r in v}!=set(['H0','H1','H2']): reason='Missing or duplicate triple'
            elif not all(eligible(r) and r['status']=='completed' and finite(r['unknown_tail_minutes']) and r['unknown_tail_minutes']==0 for r in v): reason='Failed/ineligible/unknown member'
            elif not all(finite(r['window_minutes']) for r in v) or not uniform(v,['window_minutes']): reason='Unknown/different windows'
            elif not uniform(v,identity): reason='Unmatched identity'
            elif not all(all(finite(r[f]) for f in raw) for r in v): reason='Unknown metric'
            if reason: unknown.append(scene+' / '+a+': '+reason);continue
            def values(r):
                z=[r[f] for f in raw];return np.array([z[0],z[1]+z[2],z[3]+z[4],*z[5:]],dtype=float)
            baseline=values(next(r for r in v if r['configuration_id']=='H0'))
            for c in ['H1','H2']:
                r=next(r for r in v if r['configuration_id']==c)
                d={f:r[f] for f in ['source_block_id','scenario_id','policy_id','policy_alias','policy_sha256','configuration_id','window_minutes','fault_target','source_binding_sha256','scenario_sha256']}
                d.update(dict(zip(HARDWARE,(values(r)-baseline).tolist())));out.append(d)
    return out,unknown

def boundary_cell(rows,metric,censor=False):
    result={'value':None,'right_censored':False}
    if not rows or any(not finite(r.get(metric)) for r in rows): return result
    if censor:
        vals=[r.get('critical_continuity_right_censored') for r in rows]
        if any(not finite(v) or v not in [0,1] for v in vals): return result
        result['right_censored']=any(v==1 for v in vals)
    result['value']=block_mean(rows,metric);return result

def process_vectors(rows):
    if not rows: raise ValueError('Empty native process')
    starts=np.array([r['start_minute'] for r in rows],dtype=float);ends=np.array([r['end_minute'] for r in rows],dtype=float)
    if not np.all(np.isfinite(starts)) or not np.all(np.isfinite(ends)) or np.any(ends<=starts) or not np.allclose(starts[1:],ends[:-1],rtol=0,atol=1e-7): raise ValueError('Native intervals must be positive and contiguous')
    x=np.append(starts,ends[-1])/60;series=[]
    def add(tile,name,values,color,style='-',kind='stair'):
        values=list(values);series.append(dict(tile=tile,name=name,kind=kind,x=x.tolist(),y=values+[values[-1]],color=color,line_style=style))
    for tile,c in enumerate('ehc',1):
        for mode,color,style in [('requested',[.2,.2,.2],'--'),('delivered',COLORS[3],'-')]: add(tile,c+'_'+mode,[r[f'P0_{c}_{mode}_kW']+r[f'P1_{c}_{mode}_kW'] for r in rows],color,style)
    columns=set(rows[0])
    if all(f'gt_electric_kW_{i}' in columns for i in [1,2,3]): add(4,'GT',[sum(r[f'gt_electric_kW_{i}'] for i in [1,2,3]) for r in rows],COLORS[0])
    for f,name,color in [('grid_import_kW','GRID',COLORS[1]),('diesel_kW','DG',COLORS[4])]:
        if f in columns: add(4,name,[r[f] for r in rows],color)
    stores=[('pre_diesel_energy_remaining_kWh','post_diesel_energy_remaining_kWh'),('ups_initial_kWh','ups_final_kWh'),('cold_store_initial_kWh','cold_store_final_kWh')];tile=4
    for k,(initial,final) in enumerate(stores):
        if initial not in columns: continue
        if final not in columns: raise ValueError('Missing native final inventory')
        tile+=1;series.append(dict(tile=tile,name=initial,kind='line',x=x.tolist(),y=[r[initial] for r in rows]+[rows[-1][final]],color=COLORS[k+2],line_style='none',marker='.'))
    return dict(x_hours=x.tolist(),series=series,axes_count=tile,interval_semantics='native half-open; last step value repeated only for plotting; inventory actual pre endpoints plus final endpoint')

def parse_number(v):
    if v is None or v=='': return float('nan')
    if str(v).lower()=='true': return 1.
    if str(v).lower()=='false': return 0.
    return float(v)

def compare_numeric(csv_values,mat_values):
    try: a=np.array([parse_number(v) for v in csv_values],dtype=float)
    except (ValueError,TypeError) as exc: raise ValueError('CSV numeric parsing failed') from exc
    b=np.asarray(mat_values,dtype=float).reshape(-1)
    if a.shape!=b.shape or not np.array_equal(np.isnan(a),np.isnan(b)) or not np.allclose(a,b,equal_nan=True,rtol=1e-12,atol=1e-10): raise ValueError('CSV/MAT numeric values or unknown masks differ')

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''): h.update(part)
    return h.hexdigest()

def checked(path,expected,bound):
    p=Path(path).resolve()
    if not re.fullmatch('[0-9a-f]{64}',expected) or sha(p)!=expected: raise ValueError('SHA mismatch: '+str(p))
    bound[str(p)]={'sha256':expected,'bytes':p.stat().st_size};return p

def load_table(directory,name,mat=None,process=False):
    with (Path(directory)/(name+'.csv')).open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
    if mat is None: mat=loadmat(Path(directory)/('PROCESS.mat' if process else 'SCI_TABLES.mat'))
    prefix='' if process else name+'_numeric';values=np.asarray(mat['values' if process else prefix],dtype=float)
    columns=mat['columns' if process else prefix+'_columns'].reshape(-1);columns=[str(np.asarray(c).reshape(-1)[0]) for c in columns]
    if values.ndim!=2 or values.shape!=(len(rows),len(columns)): raise ValueError('CSV/MAT dimension mismatch: '+name)
    for j,f in enumerate(columns):
        if rows and f not in rows[0]: raise ValueError('Numeric MAT column absent in CSV: '+f)
        compare_numeric([r[f] for r in rows],values[:,j])
        for i,r in enumerate(rows): r[f]=float(values[i,j])
    for i,r in enumerate(rows,1): r['_source_row']=i
    return rows,len(columns)

def number_tag(v): return format(float(v),'.15g')
def tag(v): return re.sub('[^A-Za-z0-9_]','_',str(v))
def unique(rows,field): return sorted({r[field] for r in rows},key=lambda v:str(v))

def expected_class_panels(rows):
    figures=[];skips=[];phases=unique(rows,'phase');windows=sorted({r['window_minutes'] for r in rows if finite(r.get('window_minutes'))})
    for phase in phases:
        if phase in ['mechanism','boundary','hardware']: continue
        for w in windows:
            base=[r for r in rows if r['phase']==phase and r['window_minutes']==w and r['configuration_id']=='H0' and not present(r.get('interaction_group_id'))]
            for normal in [False,True]:
                group=[r for r in base if (r['canonical_class']=='NORMAL')==normal]
                if not group: continue
                s,excluded=balanced_five(group);name_tag=tag(phase)+'_w'+number_tag(w)+('_NORMAL' if normal else '')
                if excluded: skips.append(dict(family='F1/F2',group=name_tag,excluded_scenes=excluded))
                if not s: skips.append(dict(family='F1/F2',group=name_tag,reason='No complete matched five-policy H0 set'));continue
                classes=[c for c in ['L1','L2','L3','L4','NORMAL'] if c in unique(s,'canonical_class')]
                if set(unique(s,'canonical_class'))-set(classes): raise ValueError('Unsupported plotted class')
                for family,fields,prefix in [('F1',CRITICAL,'critical_service'),('F2',COST,'service_cost')]:
                    if family=='F2' and not all(all(finite(r.get(f)) for f in fields) for r in s): skips.append(dict(family='F2',group=name_tag,reason='Unknown economics/general-service values; no zero fill'));continue
                    axes=[]
                    for k,f in enumerate(fields,1):
                        y=[[block_mean([r for r in s if r['canonical_class']==c and r['policy_alias']==a],f) for a in ALIASES] for c in classes]
                        counts=[len(unique([r for r in s if r['canonical_class']==c],'source_block_id')) for c in classes]
                        labels=ALIASES if len(classes)==1 else [f'{c} (b={n})' for c,n in zip(classes,counts)]
                        axes.append(dict(tile=k,metric=f,values=y,classes=classes,aliases=ALIASES,colors=COLORS,class_source_block_counts=counts,expected_xtick_labels=labels))
                    figures.append(dict(name=family+'_'+prefix+'_'+name_tag,family=family,axes=axes,axes_count=len(axes),source_rows=sorted(r['_source_row'] for r in s),source_blocks=unique(s,'source_block_id'),normal=normal))
    return figures,skips

def verify_mechanism_cases(case,effects):
    fields=['reserve_only_delta','preparation_only_delta','both_delta','interaction_delta']
    for effect in effects:
        if effect.get('status')!='complete_matched_four_cells': continue
        matches=[r for r in case if str(r.get('mechanism_comparison','')).lower() in ['true','1','1.0'] and str(r.get('legacy_C19_order','')).lower() in ['false','0','0.0'] and all(r.get(f)==effect.get(f) for f in ['matched_factor_id','canonical_class','configuration_id','source_block_id','window_minutes'])]
        cells={}
        for r in matches:
            key=('1' if parse_number(r.get('reserve_enabled'))==1 else '0')+('1' if parse_number(r.get('critical_preparation_first'))==1 else '0')
            if key in cells: raise ValueError('Ambiguous duplicate mechanism CASE cell')
            cells[key]=r.get(effect['metric'])
        compare_numeric(four_cell_effects(cells),[effect[n] for n in fields])

def expected_mechanism(case,effects):
    figures=[];skips=[];verify_mechanism_cases(case,effects)
    metrics=['P0_c_ENS_kWh','P1_c_ENS_kWh','P3_e_ENS_kWh','energy_cost_USD'];fields=['reserve_only_delta','preparation_only_delta','both_delta','interaction_delta']
    for c in unique(effects,'configuration_id'):
        for w in sorted({r['window_minutes'] for r in effects if finite(r.get('window_minutes'))}):
            s=[r for r in effects if r['configuration_id']==c and r['window_minutes']==w and r['status']=='complete_matched_four_cells']
            axes=[dict(tile=k+1,metric=m,values=[block_mean([r for r in s if r['metric']==m],f) for f in fields],color=COLORS[4]) for k,m in enumerate(metrics)];name='F3_mechanism_'+str(c)+'_w'+number_tag(w)
            if any(any(v is None for v in a['values']) for a in axes): skips.append(dict(family='F3',group=name,reason='Incomplete mechanism cells'));continue
            figures.append(dict(name=name,family='F3',axes=axes,axes_count=4,configuration_id=c,window_minutes=w,matching='Layout.Tile + exact configuration/window filename; title must declare matched four cells'))
    return figures,skips

def expected_hardware(case):
    rows=[r for r in case if r['phase']=='hardware' and r['policy_alias'] in ['C','A']];figures=[];skips=[]
    for target in sorted({r['fault_target'] for r in rows if present(r.get('fault_target'))}):
        for w in sorted({r['window_minutes'] for r in rows if finite(r.get('window_minutes'))}):
            s=[r for r in rows if r['fault_target']==target and r['window_minutes']==w]
            if not s: continue
            d,unknown=hardware_deltas(s);name='F4_hardware_'+tag(target)+'_w'+number_tag(w)
            if unknown or not d: skips.append(dict(family='F4',group=name,reason='Entire group skipped',unknown=unknown));continue
            axes=[dict(tile=k+1,metric=f,values=[[block_mean([r for r in d if r['configuration_id']==c and r['policy_alias']==a],f) for a in 'CA'] for c in ['H1','H2']],colors=[COLORS[3],COLORS[4]]) for k,f in enumerate(HARDWARE)]
            if any(any(v is None for row in a['values'] for v in row) for a in axes): skips.append(dict(family='F4',group=name,reason='Unknown paired metric'));continue
            figures.append(dict(name=name,family='F4',axes=axes,axes_count=6,fault_target=target,window_minutes=w,source_blocks=unique(d,'source_block_id'),paired_scenarios=unique(d,'scenario_id'),paired_values=d,paired_values_file=name+'_PAIRED_VALUES.csv'))
    if not rows: skips.append(dict(family='F4',reason='No hardware C/A rows; actual formal hardware not tested'))
    return figures,skips

def expected_boundary(case):
    rows=[r for r in case if r['phase']=='boundary' and eligible(r)];figures=[];skips=[]
    factors=['policy_id','configuration_id','window_minutes','fault_target','fault_duration_minutes','initial_reserve_kWh','demand_amplitude','rejection_capacity_kW','response_delay_minutes']
    for xf,yf,mode in [('fault_duration_minutes','initial_reserve_kWh','duration_reserve'),('demand_amplitude','rejection_capacity_kW','demand_rejection')]:
        if not rows or any(xf not in r or yf not in r for r in rows): continue
        keep=[f for f in factors if f not in [xf,yf] and f in rows[0]];groups=defaultdict(list)
        for r in rows: groups[json.dumps([clean(r.get(f)) for f in keep],sort_keys=True,allow_nan=False)].append(r)
        for key,s in groups.items():
            x=sorted({r[xf] for r in s if finite(r[xf])});y=sorted({r[yf] for r in s if finite(r[yf])})
            if len(x)<2 or len(y)<2: continue
            axes=[]
            for k,metric in enumerate(BOUNDARY):
                z=[];flags=[]
                for yy in y:
                    zr=[];fr=[]
                    for xx in x:
                        cell=boundary_cell([r for r in s if r[xf]==xx and r[yf]==yy],metric,censor=k==2);zr.append(cell['value']);fr.append(cell['right_censored'])
                    z.append(zr);flags.append(fr)
                axes.append(dict(tile=k+1,metric=metric,x_field=xf,y_field=yf,x=x,y=y,values=z,right_censored=flags,missing=[[v is None for v in zr] for zr in z]))
            if all(all(v is None for zr in a['values'] for v in zr) for a in axes): continue
            figures.append(dict(family='F5',mode=mode,axes=axes,axes_count=3,source_rows=sorted(r['_source_row'] for r in s),group_fields=keep,group_values=json.loads(key),fault_target=s[0]['fault_target'],policy_id=s[0]['policy_id'],configuration_id=s[0]['configuration_id'],window_minutes=s[0]['window_minutes'],matching='MATLAB native boundaryGroups ordinal + exact source rows; never Python-guessed slice'))
    if not rows: skips.append(dict(family='F5',reason='No eligible boundary rows; actual formal boundary not tested'))
    return figures,skips

def clean(obj):
    if isinstance(obj,dict): return {k:clean(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple)): return [clean(v) for v in obj]
    if isinstance(obj,np.ndarray): return clean(obj.tolist())
    if isinstance(obj,np.generic): return clean(obj.item())
    if isinstance(obj,float) and not math.isfinite(obj): return None
    return obj

def prepare(args):
    native=getattr(args,'parallel_receipt',None);derived=getattr(args,'cohort_receipt',None)
    if bool(native)==bool(derived):raise ValueError('Exactly one native or derived authority is required')
    if native and (not getattr(args,'parallel_sha',None) or getattr(args,'cohort_sha',None)):raise ValueError('Native authority needs only its external SHA')
    if derived and (not getattr(args,'cohort_sha',None) or getattr(args,'parallel_sha',None)):raise ValueError('Derived authority needs only its external SHA')
    publication_args=[getattr(args,f,None) for f in ['publication_wrapper','publication_wrapper_sha','publication_receipt','publication_receipt_sha']]
    if any(publication_args) and not all(publication_args):raise ValueError('All explicit publication paths and known SHAs are required together')
    output=Path(args.output).resolve()
    if output.exists(): raise ValueError('Fresh output required')
    bound={};directory=Path(args.data_dir).resolve();plotter_sha=sha(args.plotter)
    if plotter_sha not in APPROVED_PLOTTERS:raise ValueError('Unapproved scientific plotter SHA')
    checked(args.plotter,plotter_sha,bound)
    if getattr(args,'cohort_receipt',None):
        import importlib.util
        spec=importlib.util.spec_from_file_location('qa03_derived_authority',Path(__file__).with_name('qa_authority.py'))
        loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
        provenance=loader.load_derived(args.cohort_receipt,args.cohort_sha,directory,checked,bound)
        export=provenance['export_receipt'];expected_case_count=provenance['case_count'];authority=provenance['authority']
    else:
        receipt_path=checked(args.parallel_receipt,args.parallel_sha,bound);parallel=json.loads(receipt_path.read_text(encoding='utf-8-sig'))
        if parallel.get('source_binding_sha256')!=SOURCE: raise ValueError('Current scientific source mismatch')
        export=parallel['export_receipt'];expected_case_count=parallel['table_row_counts']['CASE_METRICS']
        authority=dict(kind='native_parallel',receipt_path=str(receipt_path),receipt_sha256=args.parallel_sha)
    native=directory/'ANALYSIS_RECEIPT.json'
    if json.loads(native.read_text(encoding='utf-8-sig'))!=export: raise ValueError('Native/parallel export receipts differ')
    checked(native,sha(native),bound)
    for entry in export['files']:
        path=Path(entry['path'])
        if path.is_absolute() or '..' in path.parts: raise ValueError('Unsafe table path')
        checked(directory/path,entry['sha256'],bound)
    mat=loadmat(directory/'SCI_TABLES.mat');tables={};numeric={}
    for entry in export['files']:
        if not entry['path'].endswith('.csv'): continue
        name=Path(entry['path']).stem;tables[name],numeric[name]=load_table(directory,name,mat)
        if len(tables[name])!=entry['rows']: raise ValueError('Export row mismatch')
    case=tables['CASE_METRICS']
    if not case: raise ValueError('Empty CASE table cannot yield validated scientific artifacts')
    if len(case)!=expected_case_count: raise ValueError('Authority CASE count mismatch')
    if any(r.get('source_binding_sha256')!=SOURCE for r in case): raise ValueError('CASE source mismatch')
    figures=[];skips=[]
    for fn,arguments in [(expected_class_panels,(case,)),(expected_mechanism,(case,tables.get('MECHANISM_EFFECTS',[]))),(expected_hardware,(case,)),(expected_boundary,(case,))]:
        f,s=fn(*arguments);figures.extend(f);skips.extend(s)
    actual_processes={p.resolve() for p in directory.rglob('PROCESS.csv')};process_entries=[]
    if args.process_index:
        index_path=checked(args.process_index,args.process_index_sha,bound);process_entries=json.loads(index_path.read_text(encoding='utf-8-sig'))['processes']
    if {Path(e['csv_path']).resolve() for e in process_entries}!=actual_processes: raise ValueError('Every PROCESS requires explicit receipt-bound index; no unbound/missing process')
    for e in process_entries:
        erp=checked(e['export_receipt_path'],e['export_receipt_sha256'],bound);er=json.loads(erp.read_text(encoding='utf-8-sig'))
        if er.get('source_binding_sha256')!=SOURCE: raise ValueError('Process export source mismatch')
        for field,suffix in [('csv_path','PROCESS.csv'),('mat_path','PROCESS.mat')]:
            candidates=[a for a in er['artifacts'] if Path(a['path']).name==suffix]
            if len(candidates)!=1: raise ValueError('Ambiguous process artifact in original receipt')
            checked(e[field],candidates[0]['sha256'],bound)
        if Path(e['csv_path']).parent!=Path(e['mat_path']).parent: raise ValueError('PROCESS CSV/MAT must be siblings')
        p,n=load_table(Path(e['csv_path']).parent,'PROCESS',process=True);pm=loadmat(e['mat_path']);raw_sha=str(np.asarray(pm['raw_sha256']).reshape(-1)[0])
        if raw_sha!=er['native_process_receipt']['raw_sha256']: raise ValueError('PROCESS MAT raw provenance mismatch')
        vector=process_vectors(p);figures.append(dict(family='F6',actual_source=str(Path(e['csv_path']).resolve()),series=vector['series'],axes_count=vector['axes_count'],numeric_columns_checked=n,source_rows=list(range(1,len(p)+1))))
    if not figures: raise ValueError('No nonempty supported figure expectations; NOT_VERIFIED')
    publication=None
    if getattr(args,'publication_wrapper',None):
        import importlib.util
        spec=importlib.util.spec_from_file_location('qa03_publication_authority',Path(__file__).with_name('qa_publication.py'))
        loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
        publication=loader.load_publication(args,figures,checked,bound)
    for path,b in bound.items():
        if sha(path)!=b['sha256'] or Path(path).stat().st_size!=b['bytes']: raise ValueError('Input changed during audit: '+path)
    result=dict(schema='hospital_ies.matlab_expected.v2',created_utc=datetime.now(timezone.utc).isoformat(),source_binding_sha256=SOURCE,plotter_sha256=plotter_sha,plotter_role=APPROVED_PLOTTERS[plotter_sha],data_directory=str(directory),authority=authority,bound_inputs=bound,input_files=[dict(path=p,**b) for p,b in bound.items()],case_count=len(case),numeric_columns_checked=numeric,figures=figures,skipped=skips,serialization_tolerance=dict(atol=1e-10,rtol=1e-12,unknown_masks='exact'),scientific_gate=False,matlab_execution='NOT_RUN',visual_review='NOT_RUN',formal_F4_F5_validation='NOT_RUN',actual_physics_started=0)
    if publication is not None:result['publication_wrapper']=publication
    output.mkdir(parents=True);(output/'EXPECTED.json').write_text(json.dumps(clean(result),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return {'expected_path':str(output/'EXPECTED.json'),'expected_sha256':sha(output/'EXPECTED.json'),'figures':len(figures),'case_count':len(case),'matlab_execution':'NOT_RUN','scientific_gate':False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    authority=p.add_mutually_exclusive_group(required=True)
    authority.add_argument('--parallel-receipt');authority.add_argument('--cohort-receipt')
    p.add_argument('--parallel-sha');p.add_argument('--cohort-sha');p.add_argument('--data-dir',required=True);p.add_argument('--plotter',required=True);p.add_argument('--output',required=True);p.add_argument('--process-index');p.add_argument('--process-index-sha')
    p.add_argument('--publication-wrapper');p.add_argument('--publication-wrapper-sha');p.add_argument('--publication-receipt');p.add_argument('--publication-receipt-sha')
    args=p.parse_args()
    if bool(args.process_index)!=bool(args.process_index_sha): p.error('process index and external SHA required together')
    if args.parallel_receipt and (not args.parallel_sha or args.cohort_sha):p.error('native authority requires only --parallel-sha')
    if args.cohort_receipt and (not args.cohort_sha or args.parallel_sha):p.error('derived authority requires only --cohort-sha')
    pub=[args.publication_wrapper,args.publication_wrapper_sha,args.publication_receipt,args.publication_receipt_sha]
    if any(pub) and not all(pub):p.error('all four publication wrapper/receipt paths and known SHAs are required together')
    print(json.dumps(prepare(args),indent=2))

if __name__=='__main__': main()
