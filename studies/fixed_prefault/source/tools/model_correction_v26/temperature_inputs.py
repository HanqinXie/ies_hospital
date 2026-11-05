"""Fixed V26 geometry/material inventory and completed current weather.

No calibration is performed here. Missing physical inputs fail explicitly.
The reduced solar/sky/weather boundary is documented in B_SURFACE_PORT_DESIGN.
"""
from __future__ import annotations

from pathlib import Path
import math
import sqlite3
import numpy as np
import pandas as pd

SIGMA=5.6697e-8  # exact26.2 DataGlobalConstants.hh618
ZONES=('OR1_FLR_2','OR2_MULT5_FLR_2','OR3_FLR_2','OR4_FLR_2')


def idf_objects(text):
    clean='\n'.join(line.split('!',1)[0] for line in text.splitlines())
    return [[v.strip() for v in chunk.split(',')] for chunk in clean.split(';') if chunk.strip()]


def construction_inventory(text):
    """Sum original material R and inventory C, without duplicating NoMass C."""
    objects=idf_objects(text);materials={}
    def number(f,index,default):
        return float(f[index]) if len(f)>index and f[index] else default
    for f in objects:
        kind=f[0].lower()
        if kind=='material':
            resistance=float(f[3])/float(f[4]);capacity=float(f[3])*float(f[5])*float(f[6]);start=7
        elif kind=='material:nomass':resistance=float(f[3]);capacity=0.;start=4
        elif kind=='material:airgap':
            materials[f[1].upper()]=dict(resistance=float(f[2]),capacity=0.,thermal=.9,solar=.7,visible=.7,inside_thermal=.9,inside_solar=.7,inside_visible=.7);continue
        else:continue
        thermal=number(f,start,.9);solar=number(f,start+1,.7);visible=number(f,start+2,.7)
        materials[f[1].upper()]=dict(resistance=resistance,capacity=capacity,thermal=thermal,solar=solar,visible=visible,
            inside_thermal=number(f,start+3,thermal),inside_solar=number(f,start+4,solar),inside_visible=number(f,start+5,visible))
    result={}
    for f in objects:
        if f[0].lower()!='construction':continue
        missing=[x for x in f[2:] if x and x.upper() not in materials]
        if missing:
            result[f[1].upper()]=dict(unsupported_materials=missing)
            continue
        layers=[materials[x.upper()] for x in f[2:] if x]
        result[f[1].upper()]=dict(resistance_m2K_W=sum(x['resistance'] for x in layers),
            capacity_J_m2K=sum(x['capacity'] for x in layers),outside_thermal_absorptance=layers[0]['thermal'],
            outside_solar_absorptance=layers[0]['solar'],inside_thermal_absorptance=layers[-1]['inside_thermal'],
            inside_visible_absorptance=layers[-1]['inside_visible'])
    return result


def global_normal(azimuth_deg,tilt_deg):
    az,tilt=np.radians([azimuth_deg,tilt_deg])
    return np.array([math.sin(az)*math.sin(tilt),math.cos(az)*math.sin(tilt),math.cos(tilt)])


def zone_geometry(idf_text,normal_sql,zone_scales):
    """Use unchanged native world geometry; surface names are not directions."""
    objects=idf_objects(idf_text);inventory=construction_inventory(idf_text)
    original={f[1].upper():f for f in objects if f[0].lower() in ['buildingsurface:detailed','internalmass']}
    or_bases={f[1].upper() for f in objects if f[0].lower()=='buildingsurface:detailed' and f[4].upper() in ZONES}
    for f in objects:
        if f[0].lower()=='fenestrationsurface:detailed' and f[4].upper() in or_bases:
            raise ValueError('OR window port is unsupported; cannot silently omit it')
    # Actual original schedule has one identical value for every day class.
    schedule=next(f for f in objects if f[0].lower()=='schedule:compact' and f[1].upper()=='INFIL_SCH_PNNL')
    vals=[]
    for s in schedule[3:]:
        try:vals.append(float(s))
        except ValueError:pass
    if not vals or len(set(vals))!=1:raise ValueError('Nonconstant infiltration schedule requires an explicit parser')
    sch=vals[0]
    sql=Path(normal_sql).resolve();c=sqlite3.connect(sql.as_uri()+'?mode=ro',uri=True)
    try:
        cur=c.execute('SELECT ZoneName,ZoneIndex,Volume,ExtGrossWallArea FROM Zones')
        native_zones={r[0]:r for r in cur.fetchall()}
        result={}
        for zone in ZONES:
            _,zi,volume,extarea=native_zones[zone];scale=float(zone_scales[zone]);surfaces=[]
            for name,area,az,tilt,kind in c.execute('SELECT SurfaceName,Area,Azimuth,Tilt,ClassName FROM Surfaces WHERE ZoneIndex=? AND HeatTransferSurf=1',(zi,)):
                if name not in original:raise ValueError('Native OR surface has no source material object')
                f=original[name];internal=f[0].lower()=='internalmass';cn=f[2] if internal else f[3];inv=inventory[cn.upper()]
                if 'unsupported_materials' in inv:raise ValueError('Unsupported original OR construction materials: '+str(inv['unsupported_materials']))
                if inv['resistance_m2K_W']<=0:raise ValueError('Original half-layer resistance must be positive')
                exterior=not internal and f[6].upper()=='OUTDOORS'
                surfaces.append(dict(name=name,body_index=1 if internal else 2,area_m2=area/scale,physical_area_m2=area,
                    half_conductance_kW_K=2*area/inv['resistance_m2K_W']/1000/scale,
                    cosine_inward=-math.cos(math.radians(tilt)),normal=global_normal(az,tilt).tolist(),
                    exterior=exterior,source_boundary='InternalMass' if internal else f[6],construction=cn,
                    capacity_kWh_K=area*inv['capacity_J_m2K']/3.6e6/scale,**inv))
            inf=next(f for f in objects if f[0].lower()=='zoneinfiltration:designflowrate' and f[2].upper()==zone)
            if inf[4].upper()!='FLOW/EXTERIORWALLAREA' or any(float(inf[i] or 0.)!=0. for i in [9,10,12]):
                raise ValueError('Only the actual original wind-only exterior-wall infiltration is supported')
            result[zone]=dict(air_capacity_kWh_K=volume*1.2*1005/3.6e6/scale,volume_m3=volume,area_scale=scale,
                surfaces=surfaces,infiltration_design_m3_s=float(inf[7])*extarea*sch/scale,
                infiltration_wind_coefficient=float(inf[11]),infiltration_schedule=sch,
                furniture_full_capacity_kWh_K=sum(s['capacity_kWh_K'] for s in surfaces if s['body_index']==1),
                construction_full_capacity_kWh_K=sum(s['capacity_kWh_K'] for s in surfaces if s['body_index']==2),
                origin='Original unchanged SQL world geometry + original IDF material inventory; no fitted geometry')
        return result
    finally:c.close()


def read_epw(path,*,year=2006):
    lines=Path(path).read_text(encoding='utf-8-sig').splitlines();location=lines[0].split(',')
    frame=pd.read_csv(path,skiprows=8,header=None)
    out=pd.DataFrame(dict(record_end=[pd.Timestamp(year,int(m),int(d))+pd.Timedelta(hours=int(h)) for m,d,h in zip(frame[1],frame[2],frame[3])],
        outdoor_C=frame[6],pressure_Pa=frame[9],horizontal_ir_W_m2=frame[12],global_horizontal_W_m2=frame[13],direct_normal_W_m2=frame[14],diffuse_horizontal_W_m2=frame[15],wind_m_s=frame[21]))
    out.attrs['location']=dict(latitude_deg=float(location[6]),longitude_deg=float(location[7]),timezone_h=float(location[8]))
    return out


def sky_temperature_C(horizontal_ir_W_m2):
    if not np.isfinite(horizontal_ir_W_m2) or not 0<horizontal_ir_W_m2<2000:
        raise ValueError('Missing/invalid current infrared IR boundary; no fabricated sky heat')
    return (horizontal_ir_W_m2/SIGMA)**.25-273.15


def completed_weather(frame,at):
    """Latest fully completed record; never interpolate a future hourly value."""
    clock=pd.DatetimeIndex(frame.record_end);at=pd.Timestamp(at)
    if not clock.is_monotonic_increasing or clock.has_duplicates:raise ValueError('Weather records must be chronological')
    i=clock.searchsorted(at,side='right')-1
    if i<0:raise ValueError('No already completed weather packet is available')
    r=frame.iloc[i].to_dict()
    values=[r[k] for k in ['outdoor_C','horizontal_ir_W_m2','global_horizontal_W_m2','direct_normal_W_m2','diffuse_horizontal_W_m2','wind_m_s']]
    if not np.isfinite(values).all() or not -70<r['outdoor_C']<70 or not 0<=r['wind_m_s']<75 or min(values[2:5])<0 or max(values[2:5])>=9999:
        raise ValueError('Missing/invalid completed current weather input')
    r['sky_C']=sky_temperature_C(r['horizontal_ir_W_m2']);r['ground_C']=r['outdoor_C']
    r['weather_age_h']=(at-r['record_end']).total_seconds()/3600
    return r


def solar_vector(at,*,latitude_deg,longitude_deg,timezone_h):
    """NOAA General Solar Position Calculations, no future weather/state."""
    t=pd.Timestamp(at);hour=t.hour+t.minute/60+t.second/3600
    days=366 if t.is_leap_year else 365;g=2*math.pi/days*(t.dayofyear-1+(hour-12)/24)
    eq=229.18*(.000075+.001868*math.cos(g)-.032077*math.sin(g)-.014615*math.cos(2*g)-.040849*math.sin(2*g))
    dec=.006918-.399912*math.cos(g)+.070257*math.sin(g)-.006758*math.cos(2*g)+.000907*math.sin(2*g)-.002697*math.cos(3*g)+.00148*math.sin(3*g)
    ha=math.radians((hour*60+eq+4*longitude_deg-60*timezone_h)/4-180);lat=math.radians(latitude_deg)
    return np.array([-math.cos(dec)*math.sin(ha),math.cos(lat)*math.sin(dec)-math.sin(lat)*math.cos(dec)*math.cos(ha),math.sin(lat)*math.sin(dec)+math.cos(lat)*math.cos(dec)*math.cos(ha)])


def incident_solar(weather,normal,sun,*,ground_reflectance=.2):
    n=np.asarray(normal,float);s=np.asarray(sun,float)
    if n.shape!=(3,) or s.shape!=(3,) or not np.isfinite(n).all() or not np.isfinite(s).all():raise ValueError('Finite actual global normal and solar direction required')
    beam=weather['direct_normal_W_m2']*max(0.,float(n@s)) if s[2]>0 else 0.
    return beam+weather['diffuse_horizontal_W_m2']*(1+n[2])/2+ground_reflectance*weather['global_horizontal_W_m2']*(1-n[2])/2
