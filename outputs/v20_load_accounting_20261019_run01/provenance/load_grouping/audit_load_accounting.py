"""V20 arithmetic-only audit of unchanged annual inputs; no dispatch or raw trajectory."""
from __future__ import annotations
import argparse
import ast
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import numpy as np
import pandas as pd


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-root',type=Path,required=True)
    parser.add_argument('--after-root',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();before=args.before_root.resolve();after=args.after_root.resolve();out=args.destination.resolve()
    if out.exists():raise FileExistsError('Use a new immutable arithmetic-audit directory')
    out.mkdir(parents=True)
    sys.path[:0]=[str(after/'src'),str(after)]
    from hospital_ies import resilience as api
    assert Path(api.__file__).resolve()==after/'src/hospital_ies/resilience.py'
    old_path=before/'src/hospital_ies/resilience.py';new_path=after/'src/hospital_ies/resilience.py'
    old_source=old_path.read_text(encoding='utf-8');new_source=new_path.read_text(encoding='utf-8')
    old_ast=ast.parse(old_source);new_ast=ast.parse(new_source)
    old_fn=next(n for n in old_ast.body if isinstance(n,ast.FunctionDef) and n.name=='build_zone_groups')
    namespace=dict(np=np,pd=pd,ZONE_PRIORITY=api.ZONE_PRIORITY,GROUPS=api.GROUPS,CARRIERS=api.CARRIERS)
    exec(compile(ast.Module(body=[old_fn],type_ignores=[]),str(old_path),'exec'),namespace)
    old_functions={n.name:ast.dump(n,include_attributes=False) for n in old_ast.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    new_functions={n.name:ast.dump(n,include_attributes=False) for n in new_ast.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    unchanged={n:old_functions[n]==new_functions.get(n) for n in old_functions if n!='build_zone_groups'}
    assert all(unchanged.values()),'Unrelated function/class changed'
    input_rows=[]
    for name in ['hospital_ies_demand_8760.csv','critical_zones_8760.csv','electricity_boundary_8760.csv','thermal_boundary_8760.csv']:
        rel=Path('inputs/loads')/name;a,b=before/rel,after/rel
        assert sha(a)==sha(b),'Original input bytes changed'
        input_rows.append(dict(path=rel.as_posix(),bytes=a.stat().st_size,before_sha256=sha(a),after_sha256=sha(b)))
    hourly=pd.read_csv(after/'inputs/loads/hospital_ies_demand_8760.csv')
    zones=pd.read_csv(after/'inputs/loads/critical_zones_8760.csv')
    old=namespace['build_zone_groups'](zones,hourly);new=api.build_zone_groups(zones,hourly)
    diag=api.grouping_diagnostics(zones,hourly)
    assert len(new)==8760 and len(zones)==14*8760
    totals=np.column_stack((hourly.P_e_nonplant_kW+hourly.P_e_unresolved_kW,hourly.Q_h_demand_kW,hourly.Q_c_demand_kW))
    cols=[f'{g}_{k}_kW' for g in api.GROUPS for k in api.CARRIERS]
    old_values=old[cols].to_numpy().reshape(8760,4,3);new_values=new[cols].to_numpy().reshape(8760,4,3)
    assert np.array_equal(old_values[:,:,2],new_values[:,:,2]),'Cooling grouping changed'
    assert np.min(new_values)>=0 and np.max(abs(new_values.sum(1)-totals))<1e-10
    annotated=zones.assign(priority=zones.zone_name.map(api.ZONE_PRIORITY))
    annotated['e_raw']=(annotated.interior_equipment_kW+annotated.interior_lighting_kW)*annotated.zone_multiplier
    annotated['h_raw']=annotated.associated_reheat_coil_heating_kW.clip(lower=0)*annotated.zone_multiplier
    annotated['c_raw']=annotated.zone_sensible_cooling_kW.clip(lower=0)*annotated.zone_multiplier
    keys=pd.MultiIndex.from_product([hourly.hour_of_year,[0,1,2]])
    raw_old=annotated.groupby(['hour_of_year','priority'])[['e_raw','h_raw','c_raw']].sum().reindex(keys).to_numpy().reshape(8760,3,3)
    old_sum=raw_old.sum(1);old_scale=np.minimum(1.,np.divide(totals,old_sum,out=np.ones_like(old_sum),where=old_sum>1e-12))
    annual=[];compression=[]
    for j,k in enumerate(api.CARRIERS):
        for p,g in enumerate(api.GROUPS):
            x,y=old_values[:,p,j],new_values[:,p,j]
            annual.append(dict(priority=g,carrier=k,before_kWh=float(x.sum()),after_kWh=float(y.sum()),delta_kWh=float((y-x).sum()),
                before_share=float(x.sum()/totals[:,j].sum()),after_share=float(y.sum()/totals[:,j].sum()),
                before_peak_kW=float(x.max()),after_peak_kW=float(y.max()),before_peak_hour=int(x.argmax()+1),after_peak_hour=int(y.argmax()+1)))
        for h in range(8760):
            compression.append(dict(hour_of_year=h+1,carrier=k,building_total_kW=float(totals[h,j]),
                before_proxy_sum_kW=float(old_sum[h,j]),after_proxy_sum_kW=float(diag.iloc[h][f'{k}_proxy_sum_kW']),
                before_compression_scale=float(old_scale[h,j]),after_compression_scale=float(diag.iloc[h][f'{k}_compression_scale']),
                before_proxy_excess_kW=float(max(0.,old_sum[h,j]-totals[h,j])),after_proxy_excess_kW=float(diag.iloc[h][f'{k}_proxy_excess_kW'])))
    pd.DataFrame(annual).to_csv(out/'ANNUAL_PRIORITY_COMPARISON.csv',index=False)
    pd.DataFrame(compression).to_csv(out/'PRECOMPRESSION_BY_HOUR_CARRIER.csv',index=False)
    pd.DataFrame([dict(priority=g,carrier=k,before_kW=float(old_values[0,p,j]),after_kW=float(new_values[0,p,j]),delta_kW=float(new_values[0,p,j]-old_values[0,p,j]))
        for p,g in enumerate(api.GROUPS) for j,k in enumerate(api.CARRIERS)]).to_csv(out/'H1_PRIORITY_COMPARISON.csv',index=False)
    hourly_comparison=pd.DataFrame(dict(hour_of_year=hourly.hour_of_year,timestamp=hourly.timestamp))
    for p,g in enumerate(api.GROUPS):
        for j,k in enumerate(api.CARRIERS):
            hourly_comparison[f'before_{g}_{k}_kW']=old_values[:,p,j];hourly_comparison[f'after_{g}_{k}_kW']=new_values[:,p,j]
    hourly_comparison.to_csv(out/'HOURLY_PRIORITY_COMPARISON.csv',index=False)
    diag.to_csv(out/'V20_GROUPING_DIAGNOSTICS.csv',index=False)
    sql=before.parent/'Hospital_SanDiego_Starter/simulation/engineering_audit_20261010/eplusout.sql'
    exporter=before.parent/'Hospital_SanDiego_Starter/scripts/energyplus_boundary_export.py'
    conn=sqlite3.connect(sql.as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row;conn.execute('PRAGMA query_only=ON')
    time=conn.execute('SELECT t.TimeIndex FROM Time t JOIN EnvironmentPeriods e USING(EnvironmentPeriodIndex) WHERE e.EnvironmentType=3 ORDER BY t.TimeIndex LIMIT 1').fetchone()[0]
    rows=[]
    for name in ['OR1_FLR_2','ER_EXAM1_MULT4_FLR_1','OR2_MULT5_FLR_2','IC_PATROOM3_MULT6_FLR_2']:
        z=zones.loc[(zones.hour_of_year==1)&(zones.zone_name==name)].iloc[0]
        multiplier=conn.execute('SELECT Multiplier,ListMultiplier FROM Zones WHERE ZoneName=?',(name,)).fetchone()
        assert z.zone_multiplier==multiplier[0]*multiplier[1]
        for meter,var,key,field in [('InteriorEquipment:Electricity:Zone:','Electric Equipment Electricity Rate','_MISCPLUG_EQUIP','interior_equipment_kW'),
            ('InteriorLights:Electricity:Zone:','Lights Electricity Rate','_LIGHTS','interior_lighting_kW')]:
            m=conn.execute("SELECT d.VariableUnits,x.VariableValue FROM ReportMeterData x JOIN ReportMeterDataDictionary d USING(ReportMeterDataDictionaryIndex) WHERE d.ReportingFrequency='Hourly' AND x.TimeIndex=? AND d.VariableName=?",(time,meter+name)).fetchall()
            v=conn.execute("SELECT d.VariableUnits,x.VariableValue FROM ReportVariableData x JOIN ReportVariableDataDictionary d USING(ReportVariableDataDictionaryIndex) WHERE d.ReportingFrequency='Hourly' AND x.TimeIndex=? AND d.VariableName=? AND d.KeyValue=?",(time,var,name+key)).fetchall()
            assert len(m)==len(v)==1 and m[0][0]=='J' and v[0][0]=='W'
            metered=m[0][1]/3600000.;representative=v[0][1]/1000.;csv_value=float(z[field])
            assert abs(metered-csv_value)<1e-12 and abs(metered-representative*z.zone_multiplier)<1e-12
            rows.append(dict(hour_of_year=1,time_index=time,zone=name,multiplier=float(z.zone_multiplier),csv_field=field,csv_kW=csv_value,
                meter_name=meter+name,meter_J=m[0][1],meter_hourly_kWh_equals_average_kW=metered,
                representative_variable=var,key_value=name+key,representative_W=v[0][1],
                representative_kW_times_multiplier=representative*z.zone_multiplier,
                erroneous_double_mult_kW=csv_value*z.zone_multiplier))
    conn.close();pd.DataFrame(rows).to_csv(out/'H1_SQL_MULTIPLIER_EVIDENCE.csv',index=False)
    diff=''.join(difflib.unified_diff(old_source.splitlines(keepends=True),new_source.splitlines(keepends=True),fromfile=str(old_path),tofile=str(new_path)))
    (out/'GROUPING_CORE.diff').write_text(diff,encoding='utf-8')
    counts={k:dict(before=int(np.count_nonzero(old_scale[:,j]<1.)),after=int(diag[f'{k}_compression_applied'].sum()),
        before_max_excess_kW=float(np.maximum(0,old_sum[:,j]-totals[:,j]).max()),after_max_excess_kW=float(diag[f'{k}_proxy_excess_kW'].max())) for j,k in enumerate(api.CARRIERS)}
    source_rows=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in [old_path,new_path,exporter,before/'inputs/reference/export_summary.json',after/'tests/test_v20_load_accounting.py']]
    report=dict(schema='hospital_ies.v20.load_accounting_arithmetic_audit.v1',created_utc=datetime.now(timezone.utc).isoformat(),status='passed',
        before_model='M19_model_closeout',after_load_contract=api.LOAD_ACCOUNTING_CONTRACT_ID,
        evaluation_scope='Annual data grouping arithmetic only; no dispatch/strategy/ENS result is inferred.',
        source_year_read_for_field_audit=True,original_final12_used_for_control_or_evaluation=False,
        original_csv_bytes_unchanged=True,inputs=input_rows,sources=source_rows,
        compression_hours=counts,annual_priority_comparison=annual,
        building_annual_kWh={k:float(totals[:,j].sum()) for j,k in enumerate(api.CARRIERS)},
        cold_grouping_max_abs_change_kW=float(np.max(np.abs(new_values[:,:,2]-old_values[:,:,2]))),
        conservation_max_abs_residual_kW=float(np.max(np.abs(new_values.sum(1)-totals))),
        unchanged_function_and_class_AST=unchanged,source_sqlite=dict(path=str(sql),bytes=sql.stat().st_size,open_mode='read-only query_only',
            sha256_recomputed=False,declared_provenance_sha256='12771fb55683c2f08df7008ed8358a8912c41f0d14e9c1b9c47f317769838ef9'),
        source_negative_value_counts={name:int((zones[name]<0).sum()) for name in ['interior_equipment_kW','interior_lighting_kW','associated_reheat_coil_heating_kW','zone_sensible_cooling_kW']},
        boundary_notes=['Electricity uses already-multiplied hourly zone meter J/3600000 once.',
            'Reheat uses actual component Heating Coil Heating Rate W/1000 once; 14 distinct components are a subset of building coil load; DHW remains in residual demand.',
            'Cooling uses representative zone sensible W/1000 times zone multiplier once for aggregation; temperature still uses per-zone q, C and UA without count multiplication.',
            '137 cooling proxy compression hours remain a declared sensible/total/coverage allocation boundary, not evidence of meter corruption or a new load cut.',
            'The existing proportional compression/nonnegative proxy algorithm remains; no new clipping or service threshold is added.'],
        physical_episodes_started=0,solver_calls=0,old_outputs_modified=False)
    (out/'AUDIT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    files=[p for p in sorted(out.iterdir()) if p.is_file()]
    receipt=dict(status='passed',script_sha256=sha(__file__),outputs=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in files],physical_episodes_started=0,raw_trajectory_opens=0)
    (out/'RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='passed',compression_hours=counts,annual=annual,outputs=len(files),physical_episodes_started=0),ensure_ascii=True))


if __name__=='__main__':main()
