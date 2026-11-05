"""Declared fixed-reference arithmetic execution, with no dispatch optimizer.

Plant actuators retain their running references. Resource saturation scales the
four cooling ports in their held proportions, never activates a standby port.
Building electricity and each thermal carrier are rationed P0..P3. Within an
electric priority, direct/auxiliary ports retain their current physical shares.
The only scalar root locates the closed-loop no-cold-dumping balance; it does
not search dispatch candidates or an economic/ENS objective.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict
import math
import numpy as np
from hospital_ies.model import RT_TO_KW,KWH_PER_MMBTU,gt_level_properties,illustrative_tariff
from tools.sci_engineering.service import dependency_requirements,fan_independent_heat_bound

CONTRACT='hospital_ies.fixed_reference_arithmetic.v1'

class ForwardBalanceError(RuntimeError):
    category='arithmetic_forward_balance_error'


def cooling_vector(action):
    return np.array([action.get('absorption_cooling_kW',0.),*action.get('electric_chiller_cooling_kW',[0.,0.,0.])],float)


def _take(request,budget):
    values=np.zeros(4);remaining=max(0.,float(budget))
    for g in range(4):
        values[g]=min(float(request[g]),remaining);remaining-=values[g]
    return values,remaining


def evaluate_forward(*,row,plant,loads,availability,execution_action,reference,context,stores,
                     dt_h,config,execution_contract,cooling_limits_kW=None,fuel_curve=None,
                     fuel_path=None,require_balance=True):
    loads=np.asarray(loads,float);f=availability;a=execution_action;c=context
    if loads.shape!=(4,3) or not np.isfinite(loads).all() or np.any(loads<0.) or dt_h<=0.:
        raise ValueError('Finite nonnegative4x3 requests and positive actual interval required')
    ref=cooling_vector(reference);permission=cooling_vector(a)
    capacity=np.array([plant.absorption_rt,*plant.electric_chillers_rt])*RT_TO_KW
    raw=np.array([f.get('ABS',1.),*[f.get(f'EC_{i}',1.) for i in (1,2,3)]])
    desired=np.minimum(np.minimum(ref,permission),capacity*raw)
    if cooling_limits_kW is not None:desired=np.minimum(desired,np.asarray(cooling_limits_kW,float))
    desired=np.maximum(0.,desired)
    ppoints,spoints,fpoints=gt_level_properties(plant,float(row.outdoor_temperature_C))
    rated=float(ppoints[-1]);steam_per_kw=float(spoints[-1])/rated
    gts=np.minimum(np.maximum(0.,a['gt_electric_kW']),rated*np.array([f[f'GT_{i}'] for i in (1,2,3)]))
    hrsg=math.fsum(float(gts[i])*steam_per_kw*f[f'HRSG_{i+1}'] for i in range(3))
    grid_cap=min(float(a['grid_import_kW']),float(config['grid_import_limit_kW'])*f['GRID'])
    dg_cap=min(float(a['diesel_kW']),plant.emergency_diesel_kW*f['DIESEL'])
    if execution_contract.get('diesel_mode')=='emergency_bus':
        if f['GRID']>0.:dg_cap=0.
        dg_cap=min(dg_cap,float(loads[:2,0].sum()))
    general_primary_cap=float(gts.sum())+max(0.,grid_cap)
    primary_cap=general_primary_cap+max(0.,dg_cap)
    heat_factor=plant.steam_kWh_per_lb*plant.exchanger_eff
    abs_steam_per_kw=plant.abs_steam_lb_per_rt/RT_TO_KW
    electric_per_kw=np.array([plant.abs_electric_kW_per_rt,*([plant.ec_electric_kW_per_rt]*3)])/RT_TO_KW
    tower_per_kw=np.array([1.+abs_steam_per_kw*plant.steam_kWh_per_lb,*([1.+plant.ec_electric_kW_per_rt/RT_TO_KW]*3)])
    tower_cap=plant.cooling_tower_rejection_kW*f['TOWER']*sum(f.get(f'TOWER_{i}',1.) for i in (1,2,3,4))/4.
    pump=float(config.get('cold_pump_kW_per_kW',0.))
    required=dependency_requirements(row,loads)
    if getattr(row,'sci_demand_boundary_contract',None) is not None:
        from tools.model_review_v28.demand import dependency_request
        direct_cap=np.asarray(dependency_request(row,loads)['direct_cap_kW'],float)
    else:direct_cap=np.maximum(0.,loads[:,0]-required.sum(axis=1))
    fractions=np.array([[f.get(f'AUX_FAN_P{g}',1.),f.get('AUX_CHW_PUMP',1.),f.get('AUX_HEAT_PUMP',1.)] for g in range(4)])
    aux_cap=required*fractions
    electric_cap=np.minimum(loads[:,0],direct_cap+aux_cap.sum(axis=1))
    independent=np.asarray(fan_independent_heat_bound(row,loads)['maximum_fan_independent_heat_kW'])
    ups=stores['ups'];cold=stores['cold']
    ud_cap=min(float(ups['power_kW']),float(electric_cap[:2].sum())) if ups['energy_kWh']>1e-9 else 0.
    cd_cap=float(cold['power_kW'])*f.get('AUX_CHW_PUMP',1.) if cold['energy_kWh']>1e-9 else 0.
    uc_cap=float(ups['power_kW']) if ups['energy_kWh']<ups['capacity_kWh']-1e-9 else 0.
    cc_cap=float(cold['power_kW'])*f.get('AUX_CHW_PUMP',1.) if cold['energy_kWh']<cold['capacity_kWh']-1e-9 else 0.
    critical_non_ups=max(0.,float(electric_cap[:2].sum())-ud_cap)
    # Emergency DG and building UPS have no station/noncritical connection.
    # They may free a real GENERAL source for auxiliaries, but cannot themselves
    # fill that source role. Keep this partition before selecting actual flows.
    station_budget=max(0.,primary_cap-critical_non_ups)
    if execution_contract.get('diesel_mode')=='emergency_bus':
        station_budget=min(station_budget,general_primary_cap)
    boiler_permission=min(float(a['boiler_steam_lbph']),float(reference.get('boiler_steam_lbph',0.)),
        plant.boiler_steam_lbph/3.*sum(f[f'BOILER_{i}'] for i in (1,2,3)))
    boiler_var=float(c.get('boiler_kW_per_kWsteam',0.))*plant.steam_kWh_per_lb
    boiler_fixed=float(c.get('boiler_fixed_kW',0.))
    # Settle every resource and service port again when the passive boiler
    # pressure/use regulator reduces firing. Keep the original cold reference
    # for each trial: a prior trial must not accumulate converter curtailment.
    base_desired=desired.copy()
    def settle(boiler_cap):
        desired=base_desired.copy()
        boiler=0.;boiler_e=0.;starter=0.;restart=0.
        if c.get('station_ac_available',True):
            if c.get('boiler_ready',True) and boiler_permission>0. and station_budget>boiler_fixed:
                boiler=min(boiler_permission,boiler_cap,float(loads[:,1].sum())/heat_factor+desired[0]*abs_steam_per_kw,
                    (station_budget-boiler_fixed)/boiler_var if boiler_var>0. else boiler_permission)
                if boiler>0.:boiler_e=boiler_fixed+boiler_var*boiler
            left=max(0.,station_budget-boiler_e)
            starter=float(c.get('gt_start_kW',0.)) if left>=float(c.get('gt_start_kW',0.)) else 0.;left-=starter
            restart=float(c.get('boiler_restart_kW',0.)) if left>=float(c.get('boiler_restart_kW',0.)) else 0.
        station_e=boiler_e+starter+restart
        # The pre-fault valve share is a held physical allocation, not a new ENS
        # preference. Unused ABS share remains available to building heat.
        share=float(reference.get('_steam_abs_fraction',1. if ref[0]>0. else 0.))
        total_steam=hrsg+boiler
        if abs_steam_per_kw>0.:desired[0]=min(desired[0],total_steam*share/abs_steam_per_kw)
        tower_need=float(tower_per_kw@desired)
        if tower_need>tower_cap and tower_need>0.:desired*=max(0.,tower_cap)/tower_need
        conv_e=float(electric_per_kw@desired)
        conv_budget=max(0.,station_budget-station_e)
        if conv_e>conv_budget and conv_e>0.:desired*=conv_budget/conv_e

        def calculate(scale):
            q=desired*float(scale);conv=float(electric_per_kw@q)
            critical_cold_cap=loads[:2,2].copy()
            for g in (0,1):
                for j in (0,1):
                    if required[g,j]>1e-9:critical_cold_cap[g]=min(critical_cold_cap[g],loads[g,2]*fractions[g,j])
            cd=min(cd_cap,max(0.,float(critical_cold_cap.sum())-float(q.sum())))
            if pump>0.:cd=min(cd,max(0.,station_budget-station_e-conv)/pump)
            cd_pump=cd*pump
            primary_left=max(0.,primary_cap-station_e-conv-cd_pump)
            # Existing local UPS supports only building P0/P1, never station ports.
            e=np.zeros(4);aux=np.zeros((4,3));ud=0.;remaining=primary_left
            for g in range(4):
                used=min(float(electric_cap[g]),remaining);remaining-=used
                support=min(max(0.,float(electric_cap[g])-used),ud_cap-ud) if g<2 else 0.
                e[g]=used+support;ud+=support
                fraction=e[g]/electric_cap[g] if electric_cap[g]>0. else 0.
                aux[g]=aux_cap[g]*fraction
            paid=np.divide(aux,required,out=np.zeros_like(aux),where=required>1e-9)
            hcap=loads[:,1].copy();ccap=loads[:,2].copy()
            for g in range(4):
                if required[g,0]>1e-9:
                    hcap[g]=min(hcap[g],independent[g]+(loads[g,1]-independent[g])*paid[g,0])
                    ccap[g]=min(ccap[g],loads[g,2]*paid[g,0])
                if required[g,1]>1e-9:ccap[g]=min(ccap[g],loads[g,2]*paid[g,1])
                if required[g,2]>1e-9:hcap[g]=min(hcap[g],loads[g,1]*paid[g,2])
            # Tank discharge only covers an actually connectable critical deficit.
            actual_cd=min(cd,max(0.,float(ccap[:2].sum())-float(q.sum())))
            if cd-actual_cd>1e-7 and pump>0.:
                raise ForwardBalanceError('Unresolved tank-pump/electric-support event')
            cd=actual_cd
            h,_=_take(hcap,max(0.,total_steam-q[0]*abs_steam_per_kw)*heat_factor)
            cool,_=_take(ccap,float(q.sum())+cd)
            # No clinical service is surrendered to refill an inventory. Charging
            # uses only actual remaining energy and a current hardware path.
            uc=min(uc_cap,remaining) if ud<=1e-9 else 0.;remaining-=uc
            surplus_c=max(0.,float(q.sum())-float(cool.sum()))
            cc=min(cc_cap,surplus_c,remaining/pump if pump>0. else cc_cap) if cd<=1e-9 else 0.
            remaining-=cc*pump
            cold_surplus=float(q.sum())+cd-cc-float(cool.sum())
            return dict(q=q,served=np.column_stack((e,h,cool)),aux=aux,ud=ud,uc=uc,cd=cd,cc=cc,
                cold_surplus=cold_surplus,consumption=station_e+conv+cd_pump+cc*pump+float(e.sum())+uc-ud)

        final=calculate(1.)
        if final['cold_surplus']>1e-7:
            lo=0.;hi=1.
            for _ in range(36):
                mid=(lo+hi)/2.;trial=calculate(mid)
                if trial['cold_surplus']>1e-7:hi=mid
                else:lo=mid
                if (hi-lo)*float(desired.sum())<1e-6:break
            final=calculate(lo)
        return dict(final=final,boiler=boiler,boiler_e=boiler_e,starter=starter,
            restart=restart,station_e=station_e,total_steam=total_steam)

    def useful_steam(settled):
        flow=settled['final']
        return float(flow['served'][:,1].sum())/heat_factor+float(flow['q'][0])*abs_steam_per_kw

    settled=settle(boiler_permission)
    initial_boiler=float(settled['boiler']);initial_useful=useful_steam(settled)
    pressure_iterations=0
    if initial_boiler>initial_useful+1e-7:
        # Physical pressure/steam-demand closure, not candidate dispatch
        # scoring: retain the commanded upper target and find its passive
        # downward saturation boundary. OFF is the exact legal zero endpoint;
        # the existing model has no registered positive boiler minimum.
        low=0.;high=initial_boiler;accepted=settle(0.)
        for pressure_iterations in range(1,41):
            mid=(low+high)/2.;trial=settle(mid)
            if trial['boiler']<=useful_steam(trial):
                low=mid;accepted=trial
            else:high=mid
            # Root location only, in lb/h. The original useful-destination
            # inequality and energy-balance acceptance are not relaxed.
            if high-low<=1e-5:break
        settled=accepted
    boiler=settled['boiler'];boiler_e=settled['boiler_e']
    starter=settled['starter'];restart=settled['restart']
    station_e=settled['station_e'];total_steam=settled['total_steam']
    final=settled['final'];served=final['served'];q=final['q'];aux=final['aux']
    actual_useful=useful_steam(settled)
    if boiler>actual_useful+1e-7:
        raise ForwardBalanceError('Passive boiler pressure closure violates actual useful-steam destination')
    pressure_record=dict(method='passive_actual_steam_use_pressure_closure',
        initial_boiler_lbph=initial_boiler,initial_actual_useful_lbph=initial_useful,
        actual_boiler_lbph=boiler,actual_useful_lbph=actual_useful,
        pressure_iterations=pressure_iterations,dispatch_optimization=False,
        boiler_steam_dump_authorized=False,inventory_committed=False)
    demand=float(final['consumption'])
    grid=min(max(0.,grid_cap),max(0.,demand-float(gts.sum())))
    dg=min(max(0.,dg_cap),max(0.,demand-float(gts.sum())-grid))
    if execution_contract.get('diesel_mode')=='emergency_bus' and dg>float(served[:2,0].sum())-final['ud']+1e-7:
        raise ForwardBalanceError('Emergency DG delivery exceeds its original critical building bus bound')
    surplus=float(gts.sum())+grid+dg-demand
    steam_used=float(served[:,1].sum())/heat_factor+q[0]*abs_steam_per_kw
    unused=max(0.,total_steam-steam_used)
    steam_residual=total_steam-steam_used-unused
    residual=[surplus,final['cold_surplus'],steam_residual*heat_factor]
    if require_balance and (max(map(abs,residual))>1e-4 or abs(steam_residual)>1e-4):
        raise ForwardBalanceError(f'Fixed-reference resources require protection/event handling: residual={residual}')
    fuel_rates=[float(power)*float(fpoints[-1])/rated for power in gts]
    fuel_integrals=None
    if fuel_curve is not None:
        fuel_integrals=list(fuel_path.integrals(fuel_curve)) if fuel_path is not None else [fuel_curve.rate(float(p),float(row.outdoor_temperature_C))*dt_h for p in gts]
        fuel_rates=[x/dt_h for x in fuel_integrals]
    cost=(grid*illustrative_tariff(row.timestamp,plant)+dg*.30+sum(fuel_rates)*plant.gas_price_usd_per_mmbtu+
        boiler*plant.steam_kWh_per_lb/plant.boiler_eff/KWH_PER_MMBTU*plant.gas_price_usd_per_mmbtu)*dt_h
    action=dict(gt_electric_kW=gts.tolist(),grid_import_kW=grid,diesel_kW=dg,
        absorption_cooling_kW=float(q[0]),electric_chiller_cooling_kW=q[1:].tolist(),
        boiler_steam_lbph=boiler,hrsg_steam_lbph=hrsg,tower_rejection_kW=float(tower_per_kw@q),
        gt_fuel_MMBtu_h=sum(fuel_rates),ups_discharge_kW=final['ud'],ups_charge_kW=final['uc'],
        cold_discharge_kW=final['cd'],cold_charge_kW=final['cc'],cold_storage_pump_kW=pump*(final['cd']+final['cc']),
        boiler_auxiliary_kW=boiler_e,gt_start_electric_kW=starter,boiler_restart_electric_kW=restart,
        station_auxiliary_kW=station_e,boiler_station_running=boiler>0.,
        recovery_cooling_kW=0.,recovery_pump_electric_kW=0.,recovery_fan_electric_kW=0.)
    dependencies=dict(auxiliary_required_kW=required.tolist(),auxiliary_delivered_kW=aux.tolist(),
        direct_electric_delivered_kW=(served[:,0]-aux.sum(axis=1)).tolist(),
        fan_independent_heat=fan_independent_heat_bound(row,loads),
        auxiliary_bound_validation=dict(max_original_auxiliary_kW_bound_violation=0.,tolerance_kW=1e-7),
        semantics='Fixed direct/auxiliary electric shares; P0..P3 carrier rationing; original fan/pump support bounds')
    if getattr(row,'sci_demand_boundary_contract',None) is not None:
        from tools.model_review_v28.demand import served_components
        dependencies['demand_components']=served_components(row,loads,served,aux)
    return dict(action=action,served=served,lost=np.maximum(0.,loads-served),
        cost_USD_step=cost,tracking_penalty_USD_step=0.,security_objective_penalty_USD_step=0.,
        energy_balance_residuals=residual,steam_balance_residual_lbph=steam_residual,
        named_balance_residuals=dict(electric_kW=residual[0],cooling_kW=residual[1],
            useful_heat_kW=residual[2],steam_lbph=steam_residual),
        source_partition=dict(critical_DG_kW=dg,critical_building_UPS_kW=final['ud'],
            general_source_kW=float(gts.sum())+grid,
            general_consumption_kW=demand-dg,
            emergency_DG_excluded_from_station_and_noncritical=True),
        unused_steam_lbph=unused,optimizer_warning='',solver_backend_layers=[],dispatch_optimizer_calls=0,
        service_dependencies=dependencies,m22_mode_selection=dict(method='deterministic_current_surplus_deficit',
            inventory_committed=False,boiler_pressure_regulation=pressure_record),
        forward_contract=CONTRACT,unabsorbed_electric_kW=surplus,
        sf2_physical_operating_fee_USD_step=cost,sf2_artificial_unused_steam_tie_USD_step=0.,
        arithmetic_fuel=dict(running_fuel_by_unit_MMBtu=fuel_integrals,average_running_fuel_MMBtu_h=fuel_rates,
            path_context=asdict(fuel_path) if fuel_path is not None else None))
