"""One V26 three-store candidate with zero-capacity algebraic surface ports.

This is a declared low-order reduction, not an EnergyPlus CTF replacement.
No measured future normal or surface temperature is accepted by the solver.
"""
from __future__ import annotations

import math
import numpy as np
from tools.model_correction_v26.temperature_inputs import SIGMA


def _tarp_coefficient(delta,cosine):
    if abs(cosine)<1e-10:return 1.31
    unstable=(delta<0 and cosine<0) or (delta>0 and cosine>0)
    return 9.482/(7.238-abs(cosine)) if unstable else 1.810/(1.382+abs(cosine))


def inside_face(*,air_C,bulk_C,half_conductance_kW_K,radiant_kW,area_m2,
                law='tarp',cosine_inward=0.,convective_scale=1.,constant_conductance_kW_K=None):
    """Ghalf*(Tbulk-Tface)+Qabs = Qconv; the face stores no energy."""
    vals=[air_C,bulk_C,half_conductance_kW_K,radiant_kW,area_m2,cosine_inward,convective_scale]
    if not np.isfinite(vals).all() or min(half_conductance_kW_K,area_m2,convective_scale)<=0 or abs(cosine_inward)>1+1e-10:
        raise ValueError('Finite physical face temperatures/positive material port required')
    g=half_conductance_kW_K;d=g*(bulk_C-air_C)+radiant_kW
    if law=='constant':
        c=float(constant_conductance_kW_K)
        if not np.isfinite(c) or c<=0:raise ValueError('Positive constant convection port required')
        delta=d/(g+c);q=c*delta;dq=c
    elif law=='tarp':
        sign=1. if d>=0 else -1.;magnitude=abs(d)
        c=area_m2*convective_scale*_tarp_coefficient(sign,cosine_inward)/1000
        floor=area_m2*convective_scale*.1/1000
        x=magnitude/(g+floor+c*np.cbrt(magnitude/g)) if magnitude else 0.
        for _ in range(16):
            raw=c*np.cbrt(x);conductance=max(floor,raw)
            derivative=floor if raw<=floor else 4/3*raw
            residual=g*x+conductance*x-magnitude
            if abs(residual)<=1e-12*(1+magnitude):break
            x=max(0.,x-residual/(g+derivative))
        else:raise RuntimeError('Massless inside face failed finite Newton solve')
        delta=sign*x;q=conductance*delta;dq=derivative
    else:raise ValueError('Unknown registered inside port law')
    face=air_C+delta;body=g*(face-bulk_C)
    k=g*dq/(g+dq)
    return dict(face_C=float(face),air_heat_kW=float(q),body_heat_kW=float(body),
                d_air_heat_d_bulk_kW_K=float(k),d_air_heat_d_air_kW_K=float(-k),
                algebraic_residual_kW=float(q+body-radiant_kW))


def outside_face(*,bulk_C,outdoor_C,sky_C,ground_C,half_conductance_kW_K,area_m2,
                 emissivity,solar_absorptance,solar_W_m2,wind_m_s,cosine_outward):
    """Fixed material/weather exterior face balance with no storage or fit."""
    vals=[bulk_C,outdoor_C,sky_C,ground_C,half_conductance_kW_K,area_m2,emissivity,solar_absorptance,solar_W_m2,wind_m_s,cosine_outward]
    if not np.isfinite(vals).all() or min(half_conductance_kW_K,area_m2)<=0 or not 0<emissivity<=1 or not 0<=solar_absorptance<=1 or min(solar_W_m2,wind_m_s)<0 or abs(cosine_outward)>1+1e-10 or min(bulk_C,outdoor_C,sky_C,ground_C)<=-273.15:
        raise ValueError('Complete physically valid current exterior boundary required')
    g=half_conductance_kW_K;hc=(5.8+3.8*wind_m_s)*area_m2/1000
    rad=emissivity*SIGMA*area_m2/1000;fs=(1+cosine_outward)/2;fg=1-fs
    solar=solar_absorptance*solar_W_m2*area_m2/1000
    incident=fs*(sky_C+273.15)**4+fg*(ground_C+273.15)**4
    face=float(bulk_C)
    for _ in range(16):
        kelvin=face+273.15
        if kelvin<=0:raise RuntimeError('Exterior face left physical Kelvin domain')
        conv=hc*(outdoor_C-face);lw=rad*(incident-kelvin**4)
        residual=g*(bulk_C-face)+conv+lw+solar
        derivative=g+hc+4*rad*kelvin**3
        if abs(residual)<=1e-11*(1+abs(solar)):break
        face+=residual/derivative
    else:raise RuntimeError('Massless exterior face failed finite Newton solve')
    body=g*(face-bulk_C)
    return dict(face_C=float(face),body_heat_kW=float(body),solar_heat_kW=float(solar),
        convection_into_face_kW=float(conv),longwave_into_face_kW=float(lw),
        d_body_heat_d_bulk_kW_K=float(g*(g/derivative-1)),algebraic_residual_kW=float(conv+lw+solar-body))


def advance_three_stores(state,history_air,*,parameters,geometry,forcing,dt_h,structure):
    """Implicit solid ODEs + source BDF3 air, exactly three physical stores.

    Inputs must be already completed actual delivery/current source packets.
    Reference adaptive clocks are only supplied by the offline study caller.
    Body/air endpoint energy and BDF3 history correction are separate ledgers.
    """
    old=np.asarray(state,float);hist=np.asarray(history_air,float);p=np.asarray(parameters,float)
    if old.shape!=(3,) or hist.shape!=(3,) or p.shape!=(4,) or not np.isfinite(np.r_[old,hist,p,dt_h]).all() or min(p[:3])<=0 or not 0<p[3]<1 or dt_h<=0 or structure not in ['baseline','surface']:
        raise ValueError('Three finite states, declared histories and registered positive parameters required')
    if 'weather_record_end' in forcing or 'completed_standard_time' in forcing:
        if not {'weather_record_end','completed_standard_time'}<=set(forcing):raise ValueError('Current weather availability and completion time must both be declared')
        if np.datetime64(forcing['weather_record_end'])>np.datetime64(forcing['completed_standard_time']):raise ValueError('A future weather packet cannot drive a completed current step')
    cf,cc,g,f=p;ca=float(geometry['air_capacity_kWh_K']);surfaces=geometry['surfaces']
    if ca<=0 or not surfaces:raise ValueError('Original air capacity and exposed material surfaces required')
    qsys=float(forcing['system_sensible_kW']);qinternal=float(forcing['internal_convective_kW'])+float(forcing.get('system_convective_kW',0.))
    qir=float(forcing['internal_radiant_kW']);qvis=float(forcing['internal_visible_kW'])
    if not np.isfinite([qsys,qinternal,qir,qvis]).all():raise ValueError('Finite already-delivered/current heat sources required')
    areas=np.array([s['area_m2'] for s in surfaces]);body=np.array([s['body_index'] for s in surfaces])
    wi=areas*np.array([s['inside_thermal_absorptance'] for s in surfaces]);wv=areas*np.array([s['inside_visible_absorptance'] for s in surfaces])
    if min(wi.sum(),wv.sum())<=0 or not set(body)<={1,2} or not {1,2}<=set(body):raise ValueError('Nonoverlapping furniture/construction source allocation required')
    absorbed=qir*wi/wi.sum()+qvis*wv/wv.sum()
    refs=np.array([s['area_m2']*_tarp_coefficient(1.,s['cosine_inward'])/1000 for s in surfaces])
    scales={1:g*f/refs[body==1].sum(),2:g*(1-f)/refs[body==2].sum()}
    kinf=float(geometry.get('infiltration_design_m3_s',0.))*float(geometry.get('infiltration_wind_coefficient',.224))*float(forcing['wind_m_s'])*1.2*1005/1000
    air_history=3*hist[0]-1.5*hist[1]+hist[2]/3
    def evaluate(x):
        ta,tf,tc=x;qa=np.zeros(3);qb=np.zeros(3);k=np.zeros(3);inside=[];outside=[];qext=0.;dext=0.
        if structure=='baseline':
            for bi,cond in [(1,g*f),(2,g*(1-f))]:
                qa[bi]=cond*(x[bi]-ta);qb[bi]=absorbed[body==bi].sum()-qa[bi];k[bi]=cond
        else:
            for i,s in enumerate(surfaces):
                bi=s['body_index'];r=inside_face(air_C=ta,bulk_C=x[bi],half_conductance_kW_K=s['half_conductance_kW_K'],radiant_kW=absorbed[i],area_m2=s['area_m2'],cosine_inward=s['cosine_inward'],convective_scale=scales[bi])
                inside.append(dict(name=s['name'],**r));qa[bi]+=r['air_heat_kW'];qb[bi]+=r['body_heat_kW'];k[bi]+=r['d_air_heat_d_bulk_kW_K']
        for s in surfaces:
            if not s['exterior']:continue
            solar=forcing['solar_by_surface_W_m2']
            if s['name'] not in solar:raise ValueError('A current solar packet is missing for an actual exterior face')
            r=outside_face(bulk_C=tc,outdoor_C=forcing['outdoor_C'],sky_C=forcing['sky_C'],ground_C=forcing['ground_C'],
                half_conductance_kW_K=s['half_conductance_kW_K'],area_m2=s['area_m2'],emissivity=s['outside_thermal_absorptance'],
                solar_absorptance=s['outside_solar_absorptance'],solar_W_m2=solar[s['name']],wind_m_s=forcing['wind_m_s'],cosine_outward=s['normal'][2])
            outside.append(dict(name=s['name'],**r));qext+=r['body_heat_kW'];dext+=r['d_body_heat_d_bulk_kW_K']
        qinf=kinf*(forcing['outdoor_C']-ta);air_storage=ca/dt_h*(11/6*ta-air_history)
        residual=np.array([air_storage-qsys-qinternal-qinf-qa.sum(),cf/dt_h*(tf-old[1])-qb[1],cc/dt_h*(tc-old[2])-qb[2]-qext])
        jac=np.array([[11/6*ca/dt_h+kinf+k.sum(),-k[1],-k[2]],[-k[1],cf/dt_h+k[1],0.],[-k[2],0.,cc/dt_h+k[2]-dext]])
        return residual,jac,dict(inside=inside,outside=outside,qext=qext,qinf=qinf,air_storage=air_storage,qa=float(qa.sum()))
    current=old.copy();iterations=0
    for iterations in range(1,17):
        residual,jac,ports=evaluate(current);norm=float(np.max(abs(residual)))
        if norm<1e-10:break
        delta=np.linalg.solve(jac,residual);factor=1.
        for _ in range(10):
            trial=current-factor*delta
            if np.isfinite(trial).all() and min(trial)>-150 and max(trial)<200:
                tr,_,_=evaluate(trial)
                if np.max(abs(tr))<norm:current=trial;break
            factor*=.5
        else:raise RuntimeError('Three-store Newton line search failed; no zero-filled trajectory')
    else:raise RuntimeError('Three-store finite Newton iterations exhausted')
    actual=(qsys+qinternal+qir+qvis+ports['qext']+ports['qinf'])*dt_h
    physical=float(np.dot([ca,cf,cc],current-old));correction=ports['air_storage']*dt_h-ca*(current[0]-old[0])
    return dict(state=current,actual_input_kWh=float(actual),physical_store_energy_change_kWh=physical,
        numerical_air_history_correction_kWh=float(correction),balance_residual_kWh=float(actual-physical-correction),
        air_algorithm_storage_kW=float(ports['air_storage']),surface_into_air_kW=ports['qa'],exterior_into_body_kW=float(ports['qext']),
        infiltration_into_air_kW=float(ports['qinf']),newton_iterations=iterations,inside_faces=ports['inside'],outside_faces=ports['outside'])
