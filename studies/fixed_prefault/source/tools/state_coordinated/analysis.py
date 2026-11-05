"""V17 saved-trajectory service, temperature and source accounting.

No environment construction, physical solve, annual-file read or raw reload.
Legacy nineteen targets and material thresholds are delegated unchanged.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from tools.operating_response import analysis as previous
from tools.engineering_model import analysis as engineering
from hospital_ies.dataset_export import json_clean
from hospital_ies.model import KWH_PER_MMBTU, illustrative_tariff

SCHEMA = 'hospital_ies.state_coordinated_saved_analysis.v17.v1'
PRIORITIES = ('P0','P1','P2','P3')
CARRIERS = ('e','h','c')
EPS = 1e-8


def _num(value):
    value=float(value)
    if not math.isfinite(value):raise ValueError('Nonfinite saved V17 value')
    return value


def _parts(payload,start,end):
    for segment in payload['segments']:
        left=max(float(start),float(segment['start_minute']))
        right=min(float(end),float(segment['end_minute']))
        if right>left:yield segment,left,right


def _runs(observations, start, finish):
    """Material/violation runs from observed subinterval classifications only."""
    runs=[]
    for a,b,flag,left_censored in observations:
        if not flag:continue
        if runs and abs(runs[-1]['end_minute']-a)<=EPS:
            runs[-1]['end_minute']=b
        else:runs.append(dict(start_minute=a,end_minute=b,left_censored=bool(left_censored)))
    for run in runs:
        run.update(duration_minutes=run['end_minute']-run['start_minute'],
            right_censored=abs(run['end_minute']-finish)<=EPS)
    clean=[]
    for i,run in enumerate(runs):
        stop=runs[i+1]['start_minute'] if i+1<len(runs) else finish
        if stop>run['end_minute']+EPS:clean.append([run['end_minute'],stop])
    confirmed=next(([a,a+30.] for a,b in clean if b-a>=30.-EPS),None)
    first_clean=clean[0][0] if clean else None
    return dict(loss_runs=runs,first_loss_minute=runs[0]['start_minute'] if runs else None,
        first_clean_minute=first_clean,confirmed_30min_interval=confirmed,
        recurrence_after_30min_count=sum(r['start_minute']>=confirmed[1]-EPS for r in runs) if confirmed else 0,
        longest_material_minutes=max((r['duration_minutes'] for r in runs),default=0.),
        material_minutes=sum(r['duration_minutes'] for r in runs),
        recovery_status='confirmed_30min_clean' if confirmed else 'unconfirmed_observed_clean' if clean else 'not_recovered_in_observed_prefix' if runs else 'no_observed_material_loss',
        observation_finish_minute=finish,window_start_minute=float(start),
        recovery_is_permanent=False,first_clean_is_30min_confirmation=False)


def _service_window(payload,env,start,end):
    cfg=getattr(env,'config',{}) if env is not None else {}
    thresholds=dict(solver_tol_kW=float(cfg.get('p0_solver_feasibility_tol_kW',1e-3)),
        absolute_kW=float(cfg.get('p0_unserved_abs_tol_kW',.01)),
        relative=float(cfg.get('p0_unserved_rel_tol',.001)))
    finish=min(float(end),max(float(start),float(payload['summary']['observed_until_minute'])))
    service={p:{c:dict(ENS_kWh=0.,disconnected_ENS_kWh=0.,connected_unserved_ENS_kWh=0.) for c in CARRIERS} for p in PRIORITIES}
    flags={(p,c):[] for p in PRIORITIES for c in CARRIERS}
    temperatures={p:[] for p in PRIORITIES}; temperature_flags={p:[] for p in PRIORITIES}
    temp_missing={p:False for p in PRIORITIES}; segments=list(_parts(payload,start,end))
    for s,a,b in segments:
        loads,loss,cuts,_,unmet,_=engineering._actual_matrices(s)
        for pi,p in enumerate(PRIORITIES):
            for ci,c in enumerate(CARRIERS):
                row=service[p][c];dt=(b-a)/60.
                row['ENS_kWh']+=float(loss[pi,ci])*dt
                row['disconnected_ENS_kWh']+=float(cuts[pi,ci])*dt
                row['connected_unserved_ENS_kWh']+=float(unmet[pi,ci])*dt
                ratio=float(loss[pi,ci]/loads[pi,ci]) if loads[pi,ci]>0 else 0.
                material=(loss[pi,ci]>thresholds['solver_tol_kW'] and
                    (loss[pi,ci]>=thresholds['absolute_kW'] or ratio>=thresholds['relative']))
                flags[p,c].append((a,b,bool(material),a==start and float(s['start_minute'])<start))
            temp=s.get('temperature',{}).get(p); violation=s.get('violation',{}).get(p)
            if temp is None or violation is None:
                temp_missing[p]=True
            else:
                temperatures[p].append(_num(temp))
                temperature_flags[p].append((a,b,bool(violation),a==start and float(s['start_minute'])<start))
    for p in PRIORITIES:
        for c in CARRIERS:service[p][c].update(_runs(flags[p,c],start,finish))
    thermal={}
    for p in PRIORITIES:
        # P3 is not a disturbed thermal model; a legacy false violation flag
        # with null temperature never establishes thermal coverage.
        covered=bool(segments) and not temp_missing[p]
        thermal[p]=dict(coverage='saved_disturbed_temperature_proxy' if covered else 'not_modeled_or_missing',
            max_temperature_C=max(temperatures[p]) if covered and temperatures[p] else None,
            violation_minutes=None,first_violation_minute=None,first_clean_minute=None,
            confirmed_30min_interval=None,loss_runs=None)
        if covered:
            run=_runs(temperature_flags[p],start,finish)
            thermal[p].update(run,violation_minutes=run['material_minutes'],first_violation_minute=run['first_loss_minute'])
        thermal[p]['temporal_semantics']='Saved subinterval-end proxy and classified duration; crossing time is not interpolated'
        thermal[p]['energy_restoration_is_temperature_recovery']=False
    return dict(carrier_service=service,temperature=thermal,material_thresholds=thresholds,
        observation_finish_minute=finish,early_loss_is_retained=True)


def _gas_parameters(env,payload):
    plant=getattr(env,'plant',None) if env is not None else None
    if plant is not None:return plant
    # Offline callers may provide an explicit frozen accounting receipt.
    fields=payload.get('accounting_parameters')
    if isinstance(fields,dict):
        from types import SimpleNamespace
        required=('steam_kWh_per_lb','boiler_eff','gas_price_usd_per_mmbtu')
        if all(fields.get(k) is not None for k in required):return SimpleNamespace(**fields)
    return None


def _grid_tariff(s,env):
    if s.get('grid_tariff_USD_kWh') is not None:return _num(s['grid_tariff_USD_kWh'])
    action=s.get('action',{})
    if action.get('grid_tariff_USD_kWh') is not None:return _num(action['grid_tariff_USD_kWh'])
    if env is None or getattr(env,'hourly',None) is None or getattr(env,'plant',None) is None:return None
    raw=s.get('hour_of_year')
    if raw is None:return None
    hour=int(raw)
    if hour!=raw or not 1<=hour<=len(env.hourly):raise ValueError('Saved grid tariff hour outside allowed input')
    row=env.hourly.iloc[hour-1]
    if 'hour_of_year' in env.hourly and int(row.hour_of_year)!=hour:raise ValueError('Saved tariff hour mismatch')
    return float(illustrative_tariff(row.timestamp,env.plant))


def economic_window(payload,env,start,end):
    """Observed-prefix source ledger; exact full receipts, no chemical DG guess."""
    result={k:0. for k in ('GT_gas_MMBtu','boiler_gas_MMBtu','grid_purchase_kWh','grid_fee_USD',
        'DG_electric_work_kWh','DG_shaft_work_kWh','DG_loss_work_kWh','DG_rotor_delta_kWh',
        'DG_fuel_budget_debit_kWh_equivalent','DG_original_electric_fee_USD','DG_shaft_equivalent_fee_USD',
        'accounting_cost_USD')}
    seen={k:True for k in result}; any_segment=False; plant=_gas_parameters(env,payload)
    partial_dg=False; tariffs=set()
    for s,a,b in _parts(payload,start,end):
        any_segment=True; action=s['action'];dt=(b-a)/60.;length=float(s['end_minute'])-float(s['start_minute'])
        fraction=(b-a)/length
        for source,target in (('gt_fuel_MMBtu_h','GT_gas_MMBtu'),):
            value=action.get(source)
            if value is None:seen[target]=False
            else:
                fuel=_num(value)
                # Same ambient segment has a linear fuel/output relation.
                if 'source_start_gt_kW' in s and 'source_end_gt_kW' in s:
                    left=sum(map(_num,s['source_start_gt_kW']));right=sum(map(_num,s['source_end_gt_kW']))
                    mean_saved=sum(map(_num,action['gt_electric_kW']))
                    mean_window=left+(right-left)*(((a+b)/2.-float(s['start_minute']))/length)
                    if mean_saved>EPS:fuel*=mean_window/mean_saved
                result[target]+=fuel*dt
        boiler=action.get('boiler_steam_lbph')
        if boiler is None or plant is None:seen['boiler_gas_MMBtu']=False
        else:result['boiler_gas_MMBtu']+=_num(boiler)*plant.steam_kWh_per_lb/plant.boiler_eff/KWH_PER_MMBTU*dt
        result['grid_purchase_kWh']+=_num(action['grid_import_kW'])*dt
        tariff=_grid_tariff(s,env)
        if tariff is None:seen['grid_fee_USD']=False
        else:
            result['grid_fee_USD']+=_num(action['grid_import_kW'])*dt*tariff;tariffs.add(tariff)
        result['accounting_cost_USD']+=_num(s['cost_USD_step'])*fraction
        receipt=s.get('diesel_local_response')
        mapping=dict(DG_electric_work_kWh='electrical_work_kWh',DG_shaft_work_kWh='mechanical_work_kWh',
            DG_loss_work_kWh='loss_work_kWh',DG_rotor_delta_kWh='rotor_delta_kWh',
            DG_fuel_budget_debit_kWh_equivalent='fuel_budget_debit_kWh_equivalent',
            DG_original_electric_fee_USD='original_electric_fee_USD',DG_shaft_equivalent_fee_USD='shaft_equivalent_fee_USD')
        if receipt and fraction<1.-EPS:
            partial_dg=True
            for key in mapping:seen[key]=False
        elif receipt:
            for key,field in mapping.items():
                if receipt.get(field) is None:seen[key]=False
                else:result[key]+=_num(receipt[field])
        else:
            # Electrical ports identify only electrical work/its old fee.
            # Even Pe=0 does not identify idle mechanical loss or rotor work.
            diesel=_num(action['diesel_kW']); result['DG_electric_work_kWh']+=diesel*dt
            result['DG_original_electric_fee_USD']+=diesel*dt*.30
            for key in mapping:
                if key not in ('DG_electric_work_kWh','DG_original_electric_fee_USD'):seen[key]=False
    result={k:(v if any_segment and seen[k] else None) for k,v in result.items()}
    gas=plant.gas_price_usd_per_mmbtu if plant is not None else None
    for source,target in (('GT_gas_MMBtu','GT_gas_fee_USD'),('boiler_gas_MMBtu','boiler_gas_fee_USD')):
        result[target]=result[source]*gas if gas is not None and result[source] is not None else None
    result['natural_gas_total_MMBtu']=(result['GT_gas_MMBtu']+result['boiler_gas_MMBtu']
        if result['GT_gas_MMBtu'] is not None and result['boiler_gas_MMBtu'] is not None else None)
    fee_fields=('GT_gas_fee_USD','boiler_gas_fee_USD','grid_fee_USD','DG_original_electric_fee_USD')
    result['actual_energy_fee_USD']=sum(result[k] for k in fee_fields) if all(result[k] is not None for k in fee_fields) else None
    result.update(diesel_chemical_fuel_or_carbon=None,prices='unchanged historical/illustrative model; DG 0.30 USD per electric kWh',
        DG_shaft_budget_is_chemical_fuel=False,partial_DG_receipt_not_interpolated=partial_dg,
        grid_tariff_values_USD_kWh=sorted(tariffs),window_minutes=[float(start),float(end)],
        missing_fields_are_unknown=True,tracking_security_and_unused_steam_objectives_excluded_from_actual_fee=True)
    return result


def _device_outputs(segment):
    action=segment['action'];gt=list(map(float,action['gt_electric_kW']))
    values={**{'GT_'+str(i+1):(v,'kW electric','saved actual source output') for i,v in enumerate(gt)},
        'GRID':(action['grid_import_kW'],'kW electric','saved actual import, not permission'),
        'DIESEL':(action['diesel_kW'],'kW electric','saved actual electrical output'),
        'ABS':(action.get('absorption_cooling_kW'),'kW cold','saved actual converter output'),
        'TOWER':(action.get('tower_rejection_kW'),'kW rejected heat','saved actual common rejection')}
    for i,value in enumerate(action.get('electric_chiller_cooling_kW',[])):
        values['EC_'+str(i+1)]=(value,'kW cold','saved actual converter output')
    availability=segment.get('availability',{})
    aggregate=action.get('hrsg_steam_lbph')
    names=['HRSG_'+str(i+1) for i in range(3)]
    weights=[gt[i]*float(availability[name]) for i,name in enumerate(names)] if all(name in availability for name in names) else None
    for i,name in enumerate(names):
        value=None
        if aggregate is not None and weights is not None:
            # The common-ambient M16 equation is sum(GT_i * steam_per_kW *
            # effective_HRSG_i); no heat-to-specific-priority flow is inferred.
            if sum(weights)>EPS:value=float(aggregate)*weights[i]/sum(weights)
            elif abs(float(aggregate))<=EPS:value=0.
        values[name]=(value,'lb/h steam','derived common GT/HRSG coupling from recorded aggregate and effective availability' if value is not None else 'individual HRSG output not identified')
    return values


def _target_operation(payload,hardware):
    targets=[];scenario=payload.get('scenario',{});start=float(scenario.get('fault_start_minute',0.))
    observations=[(s,_device_outputs(s)) for s in payload['segments']]
    health={r['device']:r for r in hardware['devices']}
    for device in sorted(scenario.get('faults',{})):
        before=[(s,v) for s,v in observations if float(s['end_minute'])<=start+EPS]
        first=next(((s,v) for s,v in observations if float(s['start_minute'])>=start-EPS),None)
        pre=before[-1] if before else None
        default=(None,None,'individual output not saved')
        value,unit,basis=pre[1].get(device,default) if pre else default
        # Actual GT right endpoint, rather than the ramp interval mean.
        if pre and device.startswith('GT_') and 'source_end_gt_kW' in pre[0]:
            value=float(pre[0]['source_end_gt_kW'][int(device[-1])-1]);basis='saved actual pre-boundary GT endpoint'
        returned=health.get(device,{}).get('first_effective_full_return_observed_minute')
        candidates=[(s,v.get(device,default)[0]) for s,v in observations
            if returned is not None and float(s['start_minute'])>=returned-EPS]
        positive=next((float(s['start_minute']) for s,v in candidates if v is not None and v>engineering.GT_POSITIVE_KW),None)
        targets.append(dict(device=device,fault_start_minute=start,pre_fault_actual_output=value,
            pre_fault_observation_interval=[float(pre[0]['start_minute']),float(pre[0]['end_minute'])] if pre else None,
            pre_fault_operating_role='not_observed' if value is None else 'in_service' if value>engineering.GT_POSITIVE_KW else 'standby_or_not_producing',
            first_fault_interval_actual_output=first[1].get(device,default)[0] if first else None,
            output_unit=unit,output_basis=basis,
            first_effective_full_return_observed_minute=returned,
            first_positive_output_after_effective_return_minute=positive,
            positive_source_is_service_recovery=False,
            source_restore_timestamp_semantics='Start of first observed positive-output interval, not continuous threshold interpolation or restored priority service',
            source_to_priority_load_attribution='not identified; use bus-level P0/P1 served loads and ENS'))
    return targets


def _source_rows(payload,env):
    rows=[]
    for s in payload['segments']:
        a=s['action'];gt=a['gt_electric_kW']
        sources={**{'GT_'+str(i+1):float(v) for i,v in enumerate(gt)},
            'GRID':a['grid_import_kW'],'DIESEL':a['diesel_kW'],
            'BOILERS_STEAM_lbph':a.get('boiler_steam_lbph'),'HRSG_TOTAL_STEAM_lbph':a.get('hrsg_steam_lbph'),
            'ABS_COOLING_kW':a.get('absorption_cooling_kW'),'TOWER_REJECTION_kW':a.get('tower_rejection_kW')}
        for i,v in enumerate(a.get('electric_chiller_cooling_kW',[])):sources['EC_'+str(i+1)+'_COOLING_kW']=v
        rows.append(dict(start_minute=float(s['start_minute']),end_minute=float(s['end_minute']),
            command_id=s.get('command_id'),actual_sources=sources,
            individual_device_outputs={name:dict(value=value,unit=unit,basis=basis) for name,(value,unit,basis) in _device_outputs(s).items()},
            arrived_source_setpoints=copy.deepcopy(s.get('arrived_source_setpoints')),
            availability=copy.deepcopy(s.get('availability')),
            observed_grid_tariff_USD_kWh=_grid_tariff(s,env),
            DG_budget_before_kWh_equivalent=(s.get('pre_state') or {}).get('diesel_energy_remaining_kWh'),
            DG_budget_after_kWh_equivalent=(s.get('post_state') or {}).get('diesel_energy_remaining_kWh'),
            output_is_source_interval_mean=True))
    return rows


def analyze_payload(payload,env):
    result=previous.analyze_payload(payload,env)
    windows={}
    for name,legacy in result['windows'].items():
        start,end=legacy['window_minutes'];prefix=_service_window(payload,env,start,end)
        prefix['economics']=economic_window(payload,env,start,end)
        prefix['GT_actual']=copy.deepcopy(legacy['observed_prefix']['gt_actual'])
        prefix['GT_unit_online_hours']=sum(v['observed_running_minutes'] for v in prefix['GT_actual'].values())/60.
        prefix['GT_actual_start_count']=sum(v['zero_to_positive_count'] for v in prefix['GT_actual'].values())
        prefix['original_P0_classifier']=copy.deepcopy(legacy['original_19'])
        windows[name]=dict(window_minutes=[start,end],complete=legacy['complete'],
            unknown_tail_minutes=[max(start,float(payload['summary']['observed_until_minute'])),end]
                if not legacy['complete'] and float(payload['summary']['observed_until_minute'])<end else None,
            observed_prefix=prefix,all_registered=copy.deepcopy(prefix) if legacy['complete'] else None)
    scenario=payload.get('scenario',{})
    hardware=result['engineering']['execution_recovery']['device_health']
    events=result['timelines']['events']
    gas=_gas_parameters(env,payload)
    result['v17']=dict(schema=SCHEMA,canonical_class=scenario.get('canonical_class'),
        legacy_class=scenario.get('legacy_class',scenario.get('event_class',scenario.get('analysis_class'))),
        core_four_class_denominator=scenario.get('core_four_class_denominator',False),
        windows=windows,recovery_stages=dict(
            hardware_effective_state=copy.deepcopy(hardware),
            ordinary_command_lifecycle=copy.deepcopy(result['timelines']['command_lifecycle']),
            actual_source_intervals=_source_rows(payload,env),target_device_operation=_target_operation(payload,hardware),
            service_and_temperature='Per-window per-priority carrier_service and temperature; not inferred from hardware or modes',
            observed_fault_events=[copy.deepcopy(e) for e in result['timelines']['events'] if e.get('origin')=='exogenous'],
            physical_repair_time='not modeled; effective availability is not actual source or service recovery'),
        propagation=dict(allowed=bool(scenario.get('enable_propagation')),
            matched_OFF=bool(scenario.get('v17_matched_propagation_ablation')),
            exogenous_device_count=len(scenario.get('faults',{})),
            residual_stress_protection_events=[copy.deepcopy(e) for e in events if e.get('cause_class')=='residual_stress_protection'],
            no_export_interlock_events=[copy.deepcopy(e) for e in events if e.get('cause_class')=='no_export_isolation'],
            minimum_output_interlock_events=[copy.deepcopy(e) for e in events if e.get('cause_class')=='minimum_output_interlock'],
            exogenous_multiple_faults_are_endogenous=False,
            functional_dependency_is_protection_trip=False),
        accounting_parameters={k:getattr(gas,k,None) if gas is not None else None
            for k in ('steam_kWh_per_lb','boiler_eff','gas_price_usd_per_mmbtu')},
        definitions=dict(material='Original unchanged loss/load thresholds; descriptive, not a clinical incident classifier',
            recovery='First observed clean and first continuous thirty-minute confirmation after material loss; recurrence retained',
            temperature='Saved low-order disturbed proxy only; P3 null is not coverage',
            economics='Same specification and same window comparisons only; gas and DG shaft equivalents are separate units',
            positive_source_output_is_critical_service_recovery=False,
            source_to_priority_flow_attribution='not_identified_by_bus_balance'),
        physical_episodes_started=0,additional_raw_opens=0)
    result['schema']=SCHEMA
    return json_clean(result)


def collect_rows(analysis):
    v=analysis['v17']; identity=dict(analysis.get('identity',{}))
    identity.update({k:analysis.get(k) for k in ('scenario_id','source_day_index','execution_model_id')})
    identity.update(canonical_class=v['canonical_class'],legacy_class=v['legacy_class'],
        core_four_class_denominator=v['core_four_class_denominator'])
    tables={name:[] for name in ('V17_CARRIER_SERVICE','V17_TEMPERATURE','V17_ECONOMICS','V17_RECOVERY_STAGES')}
    for name,w in v['windows'].items():
        common=dict(identity,window=name,window_minutes=w['window_minutes'],complete=w['complete'],
            unknown_tail_minutes=w['unknown_tail_minutes'],registered_full_known=w['all_registered'] is not None)
        prefix=w['observed_prefix']
        for priority,carriers in prefix['carrier_service'].items():
            for carrier,row in carriers.items():tables['V17_CARRIER_SERVICE'].append(dict(common,priority=priority,carrier=carrier,**row))
        for priority,row in prefix['temperature'].items():tables['V17_TEMPERATURE'].append(dict(common,priority=priority,**row))
        tables['V17_ECONOMICS'].append(dict(common,**prefix['economics'],GT_actual=prefix['GT_actual'],
            GT_unit_online_hours=prefix['GT_unit_online_hours'],GT_actual_start_count=prefix['GT_actual_start_count']))
    stages=v['recovery_stages']
    for row in stages['hardware_effective_state']['devices']:
        tables['V17_RECOVERY_STAGES'].append(dict(identity,stage='hardware_effective_availability',**row))
    for row in stages['ordinary_command_lifecycle']:
        tables['V17_RECOVERY_STAGES'].append(dict(identity,stage='ordinary_command',**row))
    for row in stages['actual_source_intervals']:
        tables['V17_RECOVERY_STAGES'].append(dict(identity,stage='actual_source_interval',**row))
    for row in stages['target_device_operation']:
        tables['V17_RECOVERY_STAGES'].append(dict(identity,stage='target_device_operation',**row))
    return tables


def export_rows(records,out):
    """Compatible immutable CSV export of caller's already-open analyses."""
    records=json_clean(copy.deepcopy(list(records)));destination=Path(out)
    tables={name:[] for name in ('V17_CARRIER_SERVICE','V17_TEMPERATURE','V17_ECONOMICS','V17_RECOVERY_STAGES')}
    for record in records:
        analysis=record.get('analysis',record)
        for name,rows in collect_rows(analysis).items():tables[name].extend(rows)
    extra=[name+'.csv' for name in tables]+['V17_ANALYSIS_RECEIPT.json']
    if any((destination/name).exists() for name in extra):raise FileExistsError('Keep previous V17 analysis immutable; use fresh output')
    # Reuse the bounded V16 CSV exporter, which compacts old timeline JSON.
    # V17 tables were already collected above; do not serialize them a second
    # time inside that legacy RESULTS.json. The V17 runner keeps metrics in its
    # own compact per-pair record and retains the original gzip separately.
    from tools.essential_fixes.runner import export_rows as compact_export
    baseline_records=[]
    for record in records:
        baseline=copy.deepcopy(record if 'analysis' in record else dict(analysis=record))
        baseline['analysis'].pop('v17',None)
        baseline_records.append(baseline)
    receipt=compact_export(baseline_records,destination)
    for name,rows in tables.items():engineering._write_csv(destination/(name+'.csv'),rows)
    receipt.update(schema=SCHEMA+'.receipt',v17_table_row_counts={k:len(v) for k,v in tables.items()},
        v17_output_sha256={name:hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in extra if name!='V17_ANALYSIS_RECEIPT.json'},
        v17_analysis_program_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        different_length_total_costs_pooled=False)
    with (destination/'V17_ANALYSIS_RECEIPT.json').open('x',encoding='utf8') as stream:
        json.dump(receipt,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return receipt
