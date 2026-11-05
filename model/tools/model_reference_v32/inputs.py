"""Independent fixed-Hospital offline inputs from frozen raw data only.

This module never creates an environment, resets a core, calls a controller or
optimizer, or opens an operating result. An explicit caller-supplied common
initial-state certificate is required. The old C-envelope adapter is not used.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd

SCHEMA='hospital_ies.offline_reference.v30.v1'
INPUT_SCHEMA='hospital_ies.independent_raw_inputs.v32.v1'


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1048576),b''):h.update(block)
    return h.hexdigest()


def _finite(value,name,*,positive=False):
    if isinstance(value,bool):raise ValueError('Invalid '+name)
    x=float(value)
    if not math.isfinite(x) or x<0 or (positive and x<=0):raise ValueError('Invalid '+name)
    return x


def source_hour_at(start_hour,minute):
    """Same one-based source clock as the common data_at, including endpoint."""
    start=_finite(start_hour,'start_hour',positive=True)
    if not start.is_integer():raise ValueError('Integer start_hour required')
    minute=_finite(minute,'minute')
    hour=int(start)+math.floor((minute+1e-9)/60.)
    if not 1<=hour<=8760:raise ValueError('Outside8760 source support')
    return hour


def planning_grid(duration_minutes=180,decision_minutes=15,event_edges=()):
    duration=_finite(duration_minutes,'duration',positive=True)
    step=_finite(decision_minutes,'decision_minutes',positive=True)
    points={0.,duration}
    points.update(i*step for i in range(1,math.ceil(duration/step)) if i*step<duration)
    points.update(float(i*60) for i in range(1,math.ceil(duration/60)) if i*60<duration)
    for edge in event_edges:
        value=_finite(edge,'event_edge')
        if value>duration:raise ValueError('Event outside planning window')
        points.add(value)
    return sorted(points)


def _registered(root,branch):
    if branch not in ('normal','grid'):raise ValueError('Only normal/grid b01 branches authorized')
    registry=json.loads((root/'configs/model_review_v30/cases.json').read_text(encoding='utf-8-sig'))
    mode='normal' if branch=='normal' else 'disturbed'
    candidates=[c for c in registry['cases'] if c.get('v30_context')=='b01_L1_grid' and c.get('v30_mode')==mode]
    if len(candidates)!=2:raise ValueError('Both frozen C/M physical registrations required')
    first=candidates[0]
    for other in candidates[1:]:
        if any(other[k]!=first[k] for k in ('scenario','configuration','v28_thermal_profile','v28_demand_contract')):
            raise ValueError('Frozen C/M physical source registrations disagree')
    scene=copy.deepcopy(first['scenario'])
    if scene['start_hour']!=1160 or first['configuration']!='H1' or scene['duration_steps']*scene['step_minutes']!=180:
        raise ValueError('Only registered b01/start1160/H1/180 window supported')
    if scene.get('shock') or scene.get('shock_by_priority') or scene.get('shock_by_zone') or scene.get('auxiliary_faults'):
        raise ValueError('b01 forbids clinical/auxiliary shocks')
    if scene.get('faults')!=({} if branch=='normal' else {'GRID':0.0}):raise ValueError('Different frozen grid event')
    return registry,first,scene


def _initial(value):
    if not isinstance(value,dict):raise ValueError('Explicit initial_snapshot certificate required')
    missing={'initial_stocks','diesel_shaft_budget_kWh','initial_state','provenance'}-set(value)
    if missing:raise ValueError('Incomplete initial_snapshot: '+','.join(sorted(missing)))
    initial=copy.deepcopy(value)
    if not isinstance(initial['initial_stocks'],dict) or set(initial['initial_stocks'])!={'ups','cold_store'}:
        raise ValueError('Both explicit initial stocks required')
    initial['initial_stocks']={k:_finite(v,'initial stock '+k) for k,v in initial['initial_stocks'].items()}
    initial['diesel_shaft_budget_kWh']=_finite(initial['diesel_shaft_budget_kWh'],'initial diesel shaft budget')
    if (not isinstance(initial['provenance'],dict)
            or not (initial['provenance'].get('origin') or initial['provenance'].get('source'))):
        raise ValueError('Initial certificate provenance origin/source required')
    if 'time_minute' in initial and _finite(initial['time_minute'],'initial time')!=0:
        raise ValueError('Initial certificate must describe t=0')
    contract=initial.get('execution_contract') or initial.get('operating_execution_contract')
    if not isinstance(contract,dict) or contract.get('diesel_mode') not in ('legacy_explicit_permission','emergency_bus'):
        raise ValueError('Actual initial execution_contract diesel mode required')
    initial['execution_contract']=copy.deepcopy(contract)
    return initial


def _raw_frames(root,registry,case,hours):
    # Only pure processing functions are imported. No environment/runner import.
    from hospital_ies.pipeline import read_data
    from hospital_ies.resilience import build_zone_groups
    from tools.sci_engineering.auxiliary import apply_auxiliary_allocation
    from tools.model_closure_v22.auxiliary import compute_allocation
    from tools.model_review_v28.thermal_adapter import apply_thermal_profile
    hourly=read_data(root)
    zones=pd.read_csv(root/'inputs/loads/critical_zones_8760.csv')
    source=hourly[hourly.hour_of_year.isin(hours)].copy(deep=True)
    selected_zones=zones[zones.hour_of_year.isin(hours)].copy(deep=True)
    raw=build_zone_groups(selected_zones,source)
    options=registry['model_closure_v22']['auxiliary']
    if options!=dict(pump_weight_exponent=1.0,actual_power_scale=None,fan_priority_shares=None):
        raise ValueError('This fixed input adapter does not alter frozen auxiliary ownership/power')
    auxiliary=compute_allocation(root,**options)
    metadata=copy.deepcopy(auxiliary.attrs['metadata'])
    selected=auxiliary[auxiliary.hour_of_year.isin(hours)].copy(deep=True)
    groups=apply_auxiliary_allocation(raw,selected).set_index('hour_of_year')
    indexed=selected.set_index('hour_of_year')
    for column in selected.columns:
        if column!='hour_of_year':source[column]=source.hour_of_year.map(indexed[column])
    for g in range(4):
        for carrier in 'ehc':source[f'P{g}_reference_{carrier}_kW']=source.hour_of_year.map(groups[f'P{g}_{carrier}_kW'])
    frames=SimpleNamespace(root=root,hourly=source,groups=groups,allowed_hours=set(hours))
    thermal_receipt=apply_thermal_profile(frames,case['v28_thermal_profile'])
    frames.hourly['sci_demand_boundary_contract']='hospital_ies.fixed_direct_aux.v28.v1'
    return frames,raw.set_index('hour_of_year'),metadata,thermal_receipt


def build_hospital_spec(project_root,*,branch='normal',initial_snapshot=None):
    """Return a planner-compatible spec; no historical native result is read.

    ``initial_snapshot`` requires explicit initial_stocks:{ups,cold_store},
    diesel_shaft_budget_kWh, initial_state, provenance:{origin/source:...}, and the
    actual execution_contract (or operating_execution_contract). Synthetic
    certificates remain labelled synthetic and never become common-init proof.
    """
    initial=_initial(initial_snapshot)  # before any data read
    root=Path(project_root).resolve();registry,case,scene=_registered(root,branch)
    from hospital_ies.model import load_plant,gt_level_properties,illustrative_tariff,RT_TO_KW,KWH_PER_MMBTU
    from hospital_ies.resilience import empty_faults
    from tools.model_review_v28.demand import construct_request
    from tools.model_review_v29.source_scope import window_scope
    event_edges=[]
    for kind in ('fault','shock'):
        if float(scene.get(kind+'_duration_minutes',0))>0:
            begin=float(scene[kind+'_start_minute']);event_edges.extend([begin,begin+float(scene[kind+'_duration_minutes'])])
    for event in scene.get('model_closure_events',[]):event_edges.append(float(event['time_minute']))
    grid=planning_grid(180,float(scene['step_minutes']),event_edges)
    hours=sorted({source_hour_at(1160,t) for t in grid})
    frames,raw_groups,auxmeta,thermal_receipt=_raw_frames(root,registry,case,hours)
    hourly=frames.hourly.set_index('hour_of_year')
    plant=load_plant(root/'configs/plant_configs.json','doe_capacity_matched_exploratory')
    hardware=copy.deepcopy(registry['hardware_profiles']['H1'])
    station=copy.deepcopy(registry['model_closure_v22']['station'])
    arrays=[];ledgers=[];period_hours=[];availability=[];rated=[];steam=[];fuel=[];prices=[];temps=[]
    for begin,end in zip(grid[:-1],grid[1:]):
        h=source_hour_at(1160,begin);row=SimpleNamespace(**hourly.loc[h].to_dict())
        raw_current=np.array([[frames.groups.loc[h,f'P{g}_{k}_kW'] for k in 'ehc'] for g in range(4)],float)
        request=construct_request(row,raw_current)
        # Preserve component fault-path ownership, not actual connected power.
        request['current_fan_components_by_priority_kW']={name:[float(getattr(row,item['power_column']))*
            float(item['priority_shares'][f'P{g}'])*request['auxiliary_factors'][g][0] for g in range(4)]
            for name,item in auxmeta['fan_component_columns'].items()}
        demand=np.asarray(request['aggregate_request_kW'],float)
        arrays.append(demand);period_hours.append(h)
        f=empty_faults()
        if branch=='grid' and scene['fault_start_minute']<=begin<scene['fault_start_minute']+scene['fault_duration_minutes']:f.update(scene['faults'])
        for event in scene.get('model_closure_events',[]):
            if event['time_minute']<=begin:f.update(event['availability'])
        availability.append(f)
        power,sp,fp=gt_level_properties(plant,float(row.outdoor_temperature_C))
        rated.append(float(power[-1]));steam.append(float(sp[-1]/power[-1]));fuel.append(float(fp[-1]/power[-1]))
        prices.append(float(illustrative_tariff(pd.Timestamp(row.timestamp),plant)));temps.append(float(row.outdoor_temperature_C))
        ledgers.append(dict(start_minute=begin,end_minute=end,source_hour=h,source_timestamp=str(pd.Timestamp(row.timestamp)),
            raw_group_loads_before_aux_kW=[[float(raw_groups.loc[h,f'P{g}_{k}_kW']) for k in 'ehc'] for g in range(4)],
            direct_aux_request=request,building_station_boundary='aggregate building request includes direct+full building auxiliaries once; plan station auxiliaries are separate',
            local_thermal_source_reference=copy.deepcopy(getattr(row,'v28_local_thermal_source_reference',None))))
    loads=np.asarray(arrays);n=len(arrays);A=lambda name:[a[name] for a in availability]
    heat_factor=plant.steam_kWh_per_lb*plant.exchanger_eff
    generators=[dict(name='GT_'+str(i),capacity=[rated[t]*A('GT_'+str(i))[t] for t in range(n)],
        min_power=[.4*rated[t]*A('GT_'+str(i))[t] for t in range(n)],fuel_type='gas',fuel_slope=fuel,fuel_fixed=0,
        heat_slope=[steam[t]*A('HRSG_'+str(i))[t]*heat_factor for t in range(n)],heat_fixed=0) for i in (1,2,3)]
    dg=dict(name='DG',capacity=[plant.emergency_diesel_kW*a['DIESEL'] if a['GRID']<1-1e-9 else 0. for a in availability],
        min_power=0.,fuel_type='diesel_shaft',fuel_slope=1.,fuel_fixed=plant.emergency_diesel_kW*hardware['diesel_local_response']['shaft_loss_fraction'],heat_slope=0.,heat_fixed=0.)
    contract=initial['execution_contract']
    if contract['diesel_mode']=='emergency_bus':
        eligible=[int(p[1:]) for p in contract.get('diesel_allowed_priorities',[]) if p in ('P0','P1','P2','P3')]
        if not eligible:raise ValueError('Emergency DG needs explicit allowed priorities')
        dg.update(electric_service_limit=loads[:,eligible,0].sum(axis=1).tolist(),electric_service_scope='building_critical_only',eligible_priorities=eligible)
    generators.append(dg)
    boiler=dict(capacity=[plant.boiler_steam_lbph/3*sum(a['BOILER_'+str(i)] for i in (1,2,3))*heat_factor for a in availability],
        fuel_slope=1/(plant.exchanger_eff*plant.boiler_eff*KWH_PER_MMBTU),fuel_type='gas',
        fixed_electric=station['boiler_fixed_kW'],electric_per_h=station['boiler_kW_per_kWsteam']/plant.exchanger_eff)
    ec=[dict(name='EC_'+str(i+1),capacity=[plant.electric_chillers_rt[i]*RT_TO_KW*a['EC_'+str(i+1)] for a in availability],
        electric_per_c=plant.ec_electric_kW_per_rt/RT_TO_KW,tower_per_c=1+plant.ec_electric_kW_per_rt/RT_TO_KW) for i in range(3)]
    absorption=dict(capacity=[plant.absorption_rt*RT_TO_KW*a['ABS'] for a in availability],
        heat_per_c=plant.abs_steam_lb_per_rt*heat_factor/RT_TO_KW,electric_per_c=plant.abs_electric_kW_per_rt/RT_TO_KW,
        tower_per_c=1+plant.abs_steam_lb_per_rt*plant.steam_kWh_per_lb/RT_TO_KW)
    defaults=dict(capacity_kWh=0.,power_kW=0.,initial_soc=0.,charge_efficiency=.95,discharge_efficiency=.95,loss_per_hour=0.)
    stores=[]
    for name,carrier in [('ups','e'),('cold_store','c')]:
        cfg=dict(defaults,**hardware['sci_engineering'][name]);stock=initial['initial_stocks'][name]
        if stock>cfg['capacity_kWh']:raise ValueError('Initial stock exceeds fixed installed capacity')
        charge=[a['GRID']>=1-1e-9 and (name=='ups' or a['TOWER']>=1-1e-9) for a in availability]
        stores.append(dict(name=name,carrier=carrier,capacity=float(cfg['capacity_kWh']),power=float(cfg['power_kW']),initial=stock,
            minimum=0.,charge_efficiency=cfg['charge_efficiency'],discharge_efficiency=cfg['discharge_efficiency'],
            decay_per_h=cfg['loss_per_hour'],loss_mode='exponential_beginning',charge_allowed=charge,discharge_allowed=[not b for b in charge],
            discharge_limit=loads[:,:2,'ehc'.index(carrier)].sum(axis=1).tolist(),
            discharge_scope='building_critical_only',eligible_priorities=[0,1],
            charge_electric_per_carrier=hardware['sci_engineering']['cold_pump_kW_per_kW'] if carrier=='c' else 0.,
            discharge_electric_per_carrier=hardware['sci_engineering']['cold_pump_kW_per_kW'] if carrier=='c' else 0.))
    binding_paths=['configs/plant_configs.json','configs/model_review_v30/cases.json',
        'inputs/loads/hospital_ies_demand_8760.csv','inputs/loads/electricity_boundary_8760.csv',
        'inputs/loads/thermal_boundary_8760.csv','inputs/loads/critical_zones_8760.csv',
        'inputs/sci_engineering/auxiliary_allocation_8760.csv','inputs/sci_engineering/auxiliary_metadata.json',
        'inputs/model_review_v28/THERMAL_SERVICE_COMPONENTS_8760.csv','inputs/model_review_v28/THERMAL_INPUT_METADATA.json',
        'inputs/model_review_v29/SOURCE_QUALITY_METADATA.json','inputs/model_review_v29/SOURCE_COOLING_UNMET_HOURS.csv',
        'inputs/model_review_v29/ALL55_ZONES_NATIVE_UNMET_SUMMARY.csv']
    spec=dict(schema=SCHEMA,input_schema=INPUT_SCHEMA,evidence_class='independent_raw_input_offline_planning',
        information_permission='offline_known_window',physical_execution_validated=False,branch=branch,start_hour=1160,configuration='H1',start_minute=0.,
        duration_h=[(b-a)/60 for a,b in zip(grid[:-1],grid[1:])],time_grid_minutes=grid,
        source_hours_by_period=period_hours,source_terminal_hour=source_hour_at(1160,180),
        demand_e=loads[:,:,0].sum(axis=1).tolist(),demand_h=loads[:,:,1].sum(axis=1).tolist(),demand_c=loads[:,:,2].sum(axis=1).tolist(),
        original_demand_by_priority_kW=loads.tolist(),
        electric_direct_by_priority_kW=[q['direct_aux_request']['direct_request_kW'] for q in ledgers],
        electric_auxiliary_by_priority_kW=[np.asarray(q['direct_aux_request']['auxiliary_request_kW']).sum(axis=1).tolist() for q in ledgers],
        demand_ledger=ledgers,exogenous_availability_by_period=availability,outdoor_temperature_C=temps,
        grid_capacity=[float(scene['grid_limit_kW'])*a['GRID'] for a in availability],grid_price=prices,
        generators=generators,boiler=boiler,electric_chillers=ec,absorption=absorption,
        tower_capacity=[float(registry['engineering_completion_v24']['tower_rejection_capacity_kW'])*a['TOWER'] for a in availability],
        storages=stores,fuel_budgets={'diesel_shaft':initial['diesel_shaft_budget_kWh']},
        fuel_prices={'gas':plant.gas_price_usd_per_mmbtu,'diesel_shaft':.30},
        dispatch_mapping=dict(gt_names=['GT_1','GT_2','GT_3'],dg_name='DG',ec_names=['EC_1','EC_2','EC_3'],
            boiler_heat_to_steam=1/heat_factor,gt_steam_per_e=[[steam[t]*A('HRSG_'+str(i))[t] for t in range(n)] for i in (1,2,3)]),
        initial_state=copy.deepcopy(initial['initial_state']),initial_snapshot=copy.deepcopy(initial),
        initialization_provenance=copy.deepcopy(initial['provenance']),
        execution_parameters=dict(station=station,hardware_profile=hardware,execution_contract=contract),
        provenance=dict(grid_source='decision_clock+raw_hourly+registered_exogenous_event_edges',historical_operating_results_read=False,
            input_bindings={name:dict(sha256=_sha(root/name),bytes=(root/name).stat().st_size) for name in binding_paths},
            transformations=['read_data','build_zone_groups','compute_allocation(hash-verified published auxiliary cache)',
                'apply_auxiliary_allocation','apply_thermal_profile(pure frame namespace)','V28 construct_request'],
            units=dict(electric='kW_e',heat='useful kW_h',cooling='useful kW_c',gas='MMBtu',diesel='shaft-equivalent kWh',prices='USD2001'),
            thermal_boundary=thermal_receipt,source_quality_scope=window_scope(root,scene),
            availability_scope='registered exogenous only; no future trips, actual restart, bus state or connected-service history',
            initialization_solver_scope=dict(
                recorded_core_solver_calls=len(initial['initialization_solver_calls']) if isinstance(initial.get('initialization_solver_calls'),list) else None,
                total_initialization_solver_calls=None,
                scope='Common reset may solve before creating core logs; total not separately instrumented. This pure input builder performs no initialization or solve.'),
            terminal_source_row_is_support_only=True,station_demand_is_not_in_building_request=True),
        registered_simplifications=[
            'Independent full original demand; no historical C/M auxiliary, station power, endogenous availability or native boundaries used',
            'GT full-load linear heat/fuel coefficients reuse current common-planner surrogate, not original author numerical software',
            'Dynamic GT startup/queue/ramp/minimum-up/down and actual boiler/DG readiness remain common-executor physics; planning station/start parameters provided separately',
            'Boiler fixed/variable station electric included; GT starter and DG startup/rotor/governor work require own-plan approximation and actual replay accounting',
            'Legacy-permission DG old-envelope critical-demand cap removed; emergency_bus only uses its actual registered allowed priorities',
            'DG shaft surrogate counts electric output plus rated-speed loss; actual startup/no-load work must be compared separately',
            'UPS only P0/P1 full building electricity including building auxiliaries, never plant/station/general services',
            'Uninstalled cold store remains zero; no extra PV, BESS or gas budget',
            'No terminal inventory equality added; planned/actual inventories and initial preparation charge reported separately',
            'Source quality metadata is evaluation-only and never changes/scales original demand',
            'Full continuous/storage plan is not an actual execution interface'])
    if not np.isfinite(loads).all() or (loads<0).any():raise ValueError('Invalid independent demand')
    return spec
