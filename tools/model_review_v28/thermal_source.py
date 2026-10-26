"""Strict thermal-source observation primitives; no controller or solver calls.

WaterUse's native 26.2 CPHW and conversion-density semantics are bound by the
source receipt; Mixed water-heater properties are deliberately not conflated.
"""
from __future__ import annotations

from collections import Counter
import math
import re

import numpy as np


def idf_objects(text):
    clean='\n'.join(line.split('!')[0] for line in text.splitlines())
    return [tuple(part.strip() for part in chunk.split(',')) for chunk in clean.split(';') if chunk.strip()]


def physical_object_diff(before,after):
    def physical(text):
        return Counter((obj[0].lower(),*obj[1:]) for obj in idf_objects(text) if not obj[0].lower().startswith('output:'))
    a,b=physical(before),physical(after)
    return dict(removed=list((a-b).elements()),added=list((b-a).elements())) if a!=b else {}


def add_observation_outputs(source,requests):
    additions=[]
    existing={tuple(x.lower() for x in obj[1:4]) for obj in idf_objects(source) if obj[0].lower()=='output:variable' and len(obj)>=4}
    for key,name,frequency in requests:
        fields=(str(key).strip(),str(name).strip(),str(frequency).strip())
        if any(not x or any(c in x for c in ',;\n\r') for x in fields):raise ValueError('Invalid observation output request')
        if fields[2] not in ['Detailed','Timestep','Hourly']:raise ValueError('Unsupported observation frequency')
        normalized=tuple(x.lower() for x in fields)
        if normalized not in existing:
            additions.append('Output:Variable,'+','.join(fields)+';\n');existing.add(normalized)
    result=source+'\n! V28 observation requests only; physical source retained.\n'+''.join(additions)
    if physical_object_diff(source,result):raise ValueError('Observation changed a physical object')
    return result


def require_variable_keys(dictionary,variable,keys,frequency,units,aggregation):
    rows=dictionary.to_dict('records') if hasattr(dictionary,'to_dict') else list(dictionary)
    keys=[str(x).upper() for x in keys]
    if not keys or len(set(keys))!=len(keys):raise ValueError('Unique nonempty native keys required')
    selected=[r for r in rows if r['Name'].lower()==variable.lower() and r['ReportingFrequency']==frequency and r['KeyValue'].upper() in keys]
    if len(selected)!=len(keys) or {r['KeyValue'].upper() for r in selected}!=set(keys):raise ValueError('Missing or ambiguous native variable/key/frequency')
    if any(r['Units']!=units or r['Type']!=aggregation for r in selected):raise ValueError('Native variable unit or aggregation mismatch')
    return {r['KeyValue'].upper():r for r in selected}


def fixture_request(peak_m3_s,schedule,effective_multiplier,target_C,inlet_C,density_kg_m3,cp_J_kgK):
    values=np.broadcast_arrays(*[np.asarray(x,float) for x in [peak_m3_s,schedule,effective_multiplier,target_C,inlet_C,density_kg_m3,cp_J_kgK]])
    if any(not np.isfinite(x).all() for x in values):raise ValueError('Request requires finite values')
    peak,frac,mult,target,inlet,rho,cp=values
    if np.any(peak<0) or np.any(frac<0) or np.any(mult<=0) or np.any(rho<=0) or np.any(cp<=0):raise ValueError('Request has negative or invalid physical input')
    mass=peak*frac*mult*rho
    return mass,mass*cp*np.maximum(target-inlet,0.)


def integrate_native_power(standard_ends,dt_h,power_kW):
    from tools.model_review_v27.load_source import integrate_intervals_hourly
    return integrate_intervals_hourly(standard_ends,dt_h,power_kW)


def tank_energy_residual(*,heater_J,use_J,source_J,loss_J,off_parasitic_to_tank_J,on_parasitic_to_tank_J,vent_J,net_J):
    values=[np.asarray(x,float) for x in [heater_J,use_J,source_J,loss_J,off_parasitic_to_tank_J,on_parasitic_to_tank_J,vent_J,net_J]]
    if any(not np.isfinite(x).all() for x in values):raise ValueError('Missing/nonfinite tank energy term')
    return sum(values[:-1])-values[-1]


def compact_schedule_value(fields,daytype,hour):
    days=['sunday','monday','tuesday','wednesday','thursday','friday','saturday','holiday','summerdesignday','winterdesignday','customday1','customday2']
    day=daytype.lower();hour=float(hour)
    if day not in days or not math.isfinite(hour) or not 0<hour<=24:raise ValueError('Unknown day or invalid schedule clock')
    selected=False;already_matched=False
    for i,field in enumerate(fields[3:],3):
        value=field.lower().replace(' ','')
        if value.startswith('through:') and value!='through:12/31':raise ValueError('Only the actual all-year compact schedules are admitted')
        if value.startswith('for:'):
            conditions=value[4:]
            direct=(day in conditions or (day=='holiday' and 'holidays' in conditions))
            weekday=(day in days[1:6] and 'weekdays' in conditions)
            weekend=(day in ['sunday','saturday'] and 'weekends' in conditions)
            selected=direct or weekday or weekend or 'alldays' in conditions or ('allotherdays' in conditions and not already_matched)
            already_matched=already_matched or selected
        if selected and value.startswith('until:'):
            h,m=(int(x) for x in value[6:].split(':'))
            if hour<=h+m/60:
                result=float(fields[i+1])
                if not math.isfinite(result):raise ValueError('Nonfinite schedule value')
                return result
    raise ValueError('No schedule coverage for current day/hour')
