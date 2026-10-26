"""Read-only hourly cooling sources and explicit finite conditional demand.

The MinPlant8 difference is a numerical-setting perturbation, never a true
annual error estimate. Original files are not changed and invalid additions
are rejected before the inherited priority allocator can clip/compress them.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT/'src') not in sys.path:sys.path.insert(0,str(ROOT/'src'))


def read_hourly_rates(sql_path,variable,keys):
    """Return source Hourly Avg W/1000 on *standard* hour-end timestamps.

    The SQL DST column describes local schedule clock adjustment; it is not
    added to Hour when indexing the continuous original 8760 service series.
    Native unrecorded HVAC substeps are not inferred from hourly averages.
    """
    keys=[str(k).upper() for k in keys]
    if not keys or len(set(keys))!=len(keys):raise ValueError('Unique source keys required')
    p=Path(sql_path).resolve()
    with sqlite3.connect(f'file:{p.as_posix()}?mode=ro',uri=True) as con:
        dictionary=pd.read_sql_query('SELECT * FROM ReportVariableDataDictionary WHERE VariableName=? AND ReportingFrequency=?',con,params=[variable,'Hourly'])
        selected=dictionary[dictionary.KeyValue.str.upper().isin(keys)]
        if len(selected)!=len(keys) or set(selected.KeyValue.str.upper())!=set(keys):
            raise ValueError(f'Missing or ambiguous source keys for {variable}')
        if not (selected.VariableUnits.eq('W').all() and selected.VariableType.eq('Avg').all()
                and selected.TimestepType.eq('HVAC System').all()):
            raise ValueError('Source rates must be Hourly Avg W on HVAC System clock')
        clock=pd.read_sql_query('SELECT * FROM Time WHERE IntervalType=1 AND EnvironmentPeriodIndex IN (SELECT EnvironmentPeriodIndex FROM EnvironmentPeriods WHERE EnvironmentType=3) ORDER BY TimeIndex',con)
        if clock.empty or clock.TimeIndex.duplicated().any() or not clock.Interval.eq(60).all() or not clock.Minute.eq(0).all():
            raise ValueError('Complete sixty-minute source hour records required')
        ids=selected.ReportVariableDataDictionaryIndex.astype(int).tolist()
        data=pd.read_sql_query('SELECT d.KeyValue,x.TimeIndex,x.VariableValue FROM ReportVariableData x JOIN ReportVariableDataDictionary d USING(ReportVariableDataDictionaryIndex) WHERE x.ReportVariableDataDictionaryIndex IN ('+','.join('?' for _ in ids)+')',con,params=ids)
    data['KeyValue']=data.KeyValue.str.upper();data=data[data.TimeIndex.isin(clock.TimeIndex)]
    if data.duplicated(['TimeIndex','KeyValue']).any():raise ValueError('Duplicate native source record')
    pivot=data.pivot(index='TimeIndex',columns='KeyValue',values='VariableValue').reindex(index=clock.TimeIndex,columns=keys)
    values=pivot.to_numpy(float)
    if not np.isfinite(values).all():raise ValueError('Unknown/nonfinite/incomplete native hourly rate')
    ends=pd.DatetimeIndex([pd.Timestamp(int(r.Year),int(r.Month),int(r.Day))+pd.Timedelta(hours=int(r.Hour)) for r in clock.itertuples(index=False)])
    if ends.has_duplicates or not ends.is_monotonic_increasing or np.any(np.diff(ends.asi8)!=3600000000000):
        raise ValueError('Non-contiguous standard hour clock')
    return pd.DataFrame(values/1000,index=ends,columns=keys).rename_axis('standard_hour_end')


def integrate_intervals_hourly(standard_ends,dt_h,rates_kW):
    """Split exact completed intervals at hour boundaries, retaining signs."""
    ends=pd.DatetimeIndex(pd.to_datetime(standard_ends,format='mixed'))
    dt=np.asarray(dt_h,float);rates=np.asarray(rates_kW,float)
    if (ends.hasnans or dt.shape!=(len(ends),) or rates.ndim!=2 or rates.shape[1]!=len(ends)
            or not np.isfinite(dt).all() or not np.isfinite(rates).all() or np.any(dt<=0)):
        raise ValueError('Finite actual durations and complete source rates required')
    rows={}
    for i,end in enumerate(ends):
        start=end-pd.Timedelta(int(round(dt[i]*3600000000000)),unit='ns')
        while start<end:
            boundary=start.floor('h')+pd.Timedelta(hours=1);stop=min(boundary,end)
            hours=(stop.value-start.value)/3600000000000
            rows[boundary]=rows.get(boundary,np.zeros(rates.shape[0]))+rates[:,i]*hours
            start=stop
    return pd.DataFrame.from_dict(rows,orient='index').sort_index().rename_axis('standard_hour_end')


def map_standard_hours(reference_ends,annual_ends):
    """Match month/day/hour endpoints and verify the original weekday context."""
    ref=pd.DatetimeIndex(reference_ends);annual=pd.DatetimeIndex(annual_ends)
    if ref.hasnans or annual.hasnans:raise ValueError('Unknown calendar timestamp')
    key=lambda t:(t.month,t.day,t.hour,t.minute,t.second,t.microsecond)
    lookup={key(t):i for i,t in enumerate(annual)}
    if len(lookup)!=len(annual):raise ValueError('Ambiguous annual calendar')
    result=[]
    for t in ref:
        if key(t) not in lookup:raise ValueError('Unmappable calendar hour')
        i=lookup[key(t)]
        if t.dayofweek!=annual[i].dayofweek:raise ValueError('Reference/annual weekday calendar differs')
        result.append(i)
    return np.asarray(result,int)


def apply_conditional_deltas(annual,zones,deltas):
    """Add the full eight-coil and fourteen observed-zone difference once.

    ``deltas.total`` is kW whole-terminal service; each zone column is kW
    representative-zone sensible cooling. The inherited V20 grouping applies
    its multiplier exactly once. Negative or proxy-excess additions reject
    mapping rather than silently clipping or reallocating critical demand.
    """
    from hospital_ies.resilience import ZONE_PRIORITY,build_zone_groups,grouping_diagnostics
    required={'total',*ZONE_PRIORITY}
    if not required.issubset(deltas.columns):raise ValueError('Missing fourteen-zone or total conditional difference')
    if not np.isfinite(deltas[list(required)].to_numpy(float)).all():raise ValueError('Nonfinite conditional demand difference')
    aa=annual.copy(deep=True);zz=zones.copy(deep=True)
    if not zz.index.is_unique:raise ValueError('Ambiguous original zone dataframe index')
    lookup={}
    for index,h,zone in zip(zz.index,zz.hour_of_year,zz.zone_name.str.upper()):
        lookup.setdefault((int(h),zone),[]).append(index)
    positions=map_standard_hours(deltas.index,pd.to_datetime(aa.timestamp));hours=aa.iloc[positions].hour_of_year.to_numpy(int)
    for i,h in enumerate(hours):
        pos=int(positions[i]);delta=deltas.iloc[i]
        total=float(aa.iloc[pos].Q_c_demand_kW)+float(delta.total)
        if total<0:raise ValueError(f'Negative total cooling at original hour {h}')
        aa.loc[aa.index[pos],'Q_c_demand_kW']=total
        if 'Q_c_terminal_coils_kW' in aa:aa.loc[aa.index[pos],'Q_c_terminal_coils_kW']=total
        for zone in ZONE_PRIORITY:
            found=lookup.get((int(h),zone),[])
            if len(found)!=1:raise ValueError(f'Missing/ambiguous original zone/hour {zone}/{h}')
            index=found[0]
            value=float(zz.at[index,'zone_sensible_cooling_kW'])+float(delta[zone])
            if value<0:raise ValueError(f'Negative representative-zone cooling {zone}/{h}')
            zz.at[index,'zone_sensible_cooling_kW']=value
    diag=grouping_diagnostics(zz,aa).set_index('hour_of_year').loc[hours]
    if np.any(diag.c_proxy_excess_kW.to_numpy()>0):raise ValueError('Conditional critical cooling proxy exceeds total; mapping invalid')
    old=build_zone_groups(zones,annual).set_index('hour_of_year').loc[hours]
    new=build_zone_groups(zz,aa).set_index('hour_of_year').loc[hours]
    detail=pd.DataFrame(dict(hour_of_year=hours,standard_hour_end=pd.DatetimeIndex(pd.to_datetime(aa.iloc[positions].timestamp)),total_original_c_kW=annual.iloc[positions].Q_c_demand_kW.to_numpy(),total_delta_c_kW=deltas.total.to_numpy(),total_new_c_kW=aa.iloc[positions].Q_c_demand_kW.to_numpy()))
    for g in range(4):
        key=f'P{g}_c_kW';detail[f'P{g}_original_c_kW']=old[key].to_numpy();detail[f'P{g}_new_c_kW']=new[key].to_numpy();detail[f'P{g}_delta_c_kW']=(new[key]-old[key]).to_numpy()
    if not np.allclose(detail[[f'P{g}_new_c_kW' for g in range(4)]].sum(axis=1),detail.total_new_c_kW,rtol=0,atol=1e-9):
        raise ValueError('Conditional priority groups fail conservation')
    return aa,zz,detail
