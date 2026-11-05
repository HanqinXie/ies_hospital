"""Independent small actual energy/stock audit, with no model execution.

Reads each selected SEGMENTS stream once. Formulae use the producer's resolved
original physical coefficients and actual storage-state parameter receipts.
No native primal certificate, optimization, annual or physical certification
is reconstructed or inferred by this engineering balance check.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from .extract_results import read,write,sha

SCHEMA='hospital_ies.cooling_dynamics.independent_energy_audit.v1'
RATE_TOL=1e-4       # Original physical_balance acceptance, in kW or lb/h.
STOCK_TOL=1e-7      # Original SF1 commit inventory acceptance, in kWh.
CLOCK_TOL=1e-8     # Clock comparison precision, in minutes.


def number(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Missing/nonfinite quantity: '+name)
    return float(value)


def get(obj,*keys):
    current=obj
    for key in keys:
        if not isinstance(current,dict) or key not in current:raise ValueError('Missing required field: '+'.'.join(keys))
        current=current[key]
    return current


def num(obj,*keys):return number(get(obj,*keys),'.'.join(keys))


def vec(value,size,name):
    if not isinstance(value,list) or len(value)!=size:raise ValueError('Missing/wrong vector: '+name)
    return [number(x,name+'['+str(i)+']') for i,x in enumerate(value)]


def close(actual,expected,tolerance,name,residuals):
    residual=number(actual,name)-number(expected,name+' expected')
    residuals[name]=max(residuals.get(name,0.),abs(residual))
    if abs(residual)>tolerance:raise ValueError(f'{name}: residual={residual}; tolerance={tolerance}')


def rising(initial,target,seconds,tau):
    # Independent exact first-order mean/end for a held attainable reference.
    if target<=initial:return target,target
    z=seconds/tau
    rise=-math.expm1(-z)
    mean_rise=z/2-z*z/6+z*z*z/24 if z<1e-5 else 1-rise/z
    return initial+(target-initial)*mean_rise,initial+(target-initial)*rise


def audit_row(row,resolved,*,previous_stocks=None,previous_dynamic=None,residuals=None):
    errors={} if residuals is None else residuals
    a=get(row,'action');plant=get(resolved,'plant');rt=num(resolved,'physical_constants','RT_TO_KW')
    steam_heat=num(plant,'steam_kWh_per_lb');eta_h=num(plant,'exchanger_eff')
    abs_e=num(plant,'abs_electric_kW_per_rt');ec_e=num(plant,'ec_electric_kW_per_rt');abs_steam=num(plant,'abs_steam_lb_per_rt')
    if min(rt,steam_heat,eta_h)<=0:raise ValueError('Nonpositive required physical coefficient')
    start=num(row,'start_minute');end=num(row,'end_minute');dt=(end-start)/60.
    if not 0.<end-start<=1.+CLOCK_TOL:raise ValueError('Nonpositive/nonminute actual interval')
    delivered=get(row,'service_request_ledger','delivered_kW')
    if not isinstance(delivered,list) or len(delivered)!=4:raise ValueError('Missing4x3 delivered actual service')
    cells=[vec(x,3,'building delivered') for x in delivered]
    served=[math.fsum(x[k] for x in cells) for k in range(3)]
    gt=vec(get(a,'gt_electric_kW'),3,'actualGT');ec=vec(get(a,'electric_chiller_cooling_kW'),3,'actualEC')
    qa=num(a,'absorption_cooling_kW');qc=qa+math.fsum(ec)
    ud,uc,cd,cc=[num(a,k) for k in ('ups_discharge_kW','ups_charge_kW','cold_discharge_kW','cold_charge_kW')]
    station=num(a,'station_auxiliary_kW');boiler_aux=num(a,'boiler_auxiliary_kW')
    starter=num(a,'gt_start_electric_kW');restart=num(a,'boiler_restart_electric_kW')
    rec_c=num(a,'recovery_cooling_kW');rec_p=num(a,'recovery_pump_electric_kW');rec_f=num(a,'recovery_fan_electric_kW')
    storage_pump=num(a,'cold_storage_pump_kW')
    converter_e=(qa*abs_e+math.fsum(ec)*ec_e)/rt
    e_source=math.fsum(gt)+num(a,'grid_import_kW')+num(a,'diesel_kW')+ud
    # Building delivered electric already includes assigned fan/pump electricity.
    # Do not add that auxiliary allocation again. IES conversion/stock/station
    # and restorative auxiliaries are distinct physical consumers.
    e_sink=served[0]+converter_e+station+storage_pump+rec_p+rec_f+uc
    close(e_source,e_sink,RATE_TOL,'electric_kW',errors)
    if get(resolved,'operating_execution_contract','diesel_mode')=='emergency_bus':
        emergency=num(a,'diesel_kW')+ud;critical=cells[0][0]+cells[1][0]
        if emergency>critical+1e-7:raise ValueError('Shared emergencyDG/buildingUPS delivery exceeds actual critical building electricity')
        general_source=math.fsum(gt)+num(a,'grid_import_kW')
        general_only=cells[2][0]+cells[3][0]+converter_e+station+storage_pump+rec_p+rec_f+uc
        if general_only>general_source+1e-7:raise ValueError('Station/conversion/charging/noncritical sinks lack an eligible general source')
    close(station,boiler_aux+starter+restart,1e-7,'station_components_kW',errors)
    close(storage_pump,num(resolved,'sci_config','cold_pump_kW_per_kW')*(cd+cc),1e-7,'cold_storage_pump_kW',errors)
    context=get(resolved,'reset_physical_context')
    close(rec_p,rec_c*num(context,'recovery_pump_kW_per_kW'),1e-7,'recovery_pump_kW',errors)
    close(rec_f,rec_c*num(context,'recovery_fan_kW_per_kW'),1e-7,'recovery_fan_kW',errors)
    boiler=num(a,'boiler_steam_lbph');hrsg=num(a,'hrsg_steam_lbph');unused=num(row,'unused_steam_lbph')
    steam_used=served[1]/(steam_heat*eta_h)+qa/rt*abs_steam
    if boiler>steam_used+1e-7:raise ValueError('Boiler exceeds useful current heat/ABS steam purpose;unused steam cannot be assigned to boiler')
    close(hrsg+boiler,steam_used+unused,RATE_TOL,'steam_lbph',errors)
    close((hrsg+boiler-unused-qa/rt*abs_steam)*steam_heat*eta_h,served[1],RATE_TOL,'useful_heat_kW',errors)
    close(qc+cd,served[2]+cc+rec_c,RATE_TOL,'cooling_kW',errors)
    tower_expected=qa*(1.+abs_steam*steam_heat/rt)+math.fsum(ec)*(1.+ec_e/rt)
    close(num(a,'tower_rejection_kW'),tower_expected,RATE_TOL,'tower_rejection_kW',errors)
    running=get(a,'boiler_station_running')
    if type(running) is not bool:raise ValueError('Missing explicit boiler running flag')
    station_cfg=get(resolved,'station_config')
    close(boiler_aux,(num(station_cfg,'boiler_fixed_kW') if running else 0.)+num(station_cfg,'boiler_kW_per_kWsteam')*steam_heat*boiler,
        RATE_TOL,'boiler_auxiliary_kW',errors)
    dependencies=get(row,'service_dependencies')
    direct=vec(get(dependencies,'direct_electric_delivered_kW'),4,'direct deliveredE')
    aux=get(dependencies,'auxiliary_delivered_kW')
    if not isinstance(aux,list) or len(aux)!=4:raise ValueError('Missing assigned auxiliary electric receipt')
    aux=[vec(x,3,'assigned auxiliaryE') for x in aux]
    for g in range(4):close(cells[g][0],direct[g]+math.fsum(aux[g]),1e-7,f'building_electric_once_P{g}_kW',errors)
    stocks={}
    for name,prefix,pc,pd in [('ups','ups',uc,ud),('cold_store','cold',cc,cd)]:
        receipt=get(row,'m21_storage',name);parameters=get(resolved,'storage_state_receipts',name)
        if get(parameters,'integration_contract')!='single_fault.continuous_loss_held_flow.v1':raise ValueError('Missing SF1 continuous-loss state contract')
        eta_c=num(parameters,'charge_efficiency');eta_d=num(parameters,'discharge_efficiency');loss=num(parameters,'loss_per_hour')
        if not 0.<eta_c<=1. or not 0.<eta_d<=1. or loss<0.:raise ValueError('Invalid actual storage parameter receipt')
        initial=num(receipt,'initial_kWh');final=num(receipt,'final_kWh');net=pc*eta_c-pd/eta_d
        phi=dt if loss==0. else -math.expm1(-loss*dt)/loss
        expected=initial*math.exp(-loss*dt)+net*phi
        standing=initial+net*dt-expected
        conversion=pc*dt*(1.-eta_c)+pd*dt*(1./eta_d-1.)
        close(final,expected,STOCK_TOL,name+'_analytic_inventory_kWh',errors)
        close(num(receipt,'charged_input_kWh'),pc*dt,STOCK_TOL,name+'_charged_input_kWh',errors)
        close(num(receipt,'discharged_kWh'),pd*dt,STOCK_TOL,name+'_useful_discharge_kWh',errors)
        close(num(receipt,'standing_loss_kWh'),standing,STOCK_TOL,name+'_continuous_standing_loss_kWh',errors)
        close(num(receipt,'conversion_loss_kWh'),conversion,STOCK_TOL,name+'_conversion_loss_kWh',errors)
        close(initial+pc*dt-pd*dt-standing-conversion,final,STOCK_TOL,name+'_energy_balance_kWh',errors)
        close(num(receipt,'balance_residual_kWh'),0.,STOCK_TOL,name+'_recorded_balance_residual_kWh',errors)
        prior=(previous_stocks[name] if previous_stocks is not None else num(parameters,'energy_kWh'))
        close(initial,prior,STOCK_TOL,name+'_inventory_continuity_kWh',errors)
        cap=num(parameters,'capacity_kWh');power=num(parameters,'power_kW')
        if min(pc,pd,final)<-STOCK_TOL or max(pc,pd)>power+1e-7 or final>cap+STOCK_TOL or min(pc,pd)>1e-7:raise ValueError('Actual stock power/energy/mode bound: '+name)
        stocks[name]=final
    diesel=get(row,'diesel_local_response');debit=num(diesel,'fuel_budget_debit_kWh_equivalent')
    close(num(row,'pre_state','diesel_energy_remaining_kWh')-debit,num(row,'post_state','diesel_energy_remaining_kWh'),STOCK_TOL,'diesel_budget_kWh_equivalent',errors)
    close(num(diesel,'mechanical_work_kWh'),num(diesel,'electrical_work_kWh')+num(diesel,'loss_work_kWh')+num(diesel,'rotor_delta_kWh'),STOCK_TOL,'diesel_rotor_energy_kWh',errors)
    close(num(diesel,'electrical_work_kWh'),num(a,'diesel_kW')*dt,STOCK_TOL,'diesel_terminal_energy_kWh',errors)
    dynamic=get(row,'cooling_dynamics');before=get(dynamic,'initial_state');after=get(dynamic,'final_state');preview=get(dynamic,'preview')
    if get(dynamic,'schema')!='cooling_dynamics.actual_commit.v1' or get(dynamic,'commit_count')!=1 or get(preview,'state_committed') is not False:raise ValueError('Missing exact single dynamic actual commit')
    close(num(preview,'dt_minutes'),end-start,CLOCK_TOL,'dynamic_interval_minutes',errors)
    close(num(after,'elapsed_minutes')-num(before,'elapsed_minutes'),end-start,CLOCK_TOL,'dynamic_clock_minutes',errors)
    cfg=get(resolved,'cooling_config');taus=vec(get(cfg,'cooling_tau_seconds'),4,'cooling tau');q0=vec(get(before,'cooling_kW'),4,'initial dynamic cooling')
    target=vec(get(preview,'reference_kW'),4,'reference cooling');means=vec(get(preview,'mean_cooling_limits_kW'),4,'dynamic mean');ends=vec(get(preview,'end_cooling_kW'),4,'dynamic endpoint')
    actual=vec(get(dynamic,'actual_mean_cooling_kW'),4,'actual mean cooling');qend=vec(get(after,'cooling_kW'),4,'committed endpoint')
    energy=vec(get(dynamic,'actual_cooling_kWh'),4,'dynamic energy');e0=vec(get(before,'actual_cooling_kWh_by_device'),4,'initial cumulative cooling energy');e1=vec(get(after,'actual_cooling_kWh_by_device'),4,'final cumulative cooling energy')
    hardware=get(row,'hardware_availability')
    for i,device in enumerate(('ABS','EC_1','EC_2','EC_3')):
        av=num(hardware,device)
        pm,pe=rising(q0[i],target[i],dt*3600.,taus[i]) if av>0. else (0.,0.)
        close(means[i],pm,1e-7,'dynamic_mean_'+device+'_kW',errors);close(ends[i],pe,1e-7,'dynamic_end_'+device+'_kW',errors)
        close(actual[i],([qa]+ec)[i],1e-7,'dynamic_actual_'+device+'_kW',errors)
        if actual[i]>means[i]+1e-6*max(1.,means[i]):raise ValueError('Actual cooling exceeds original dynamic mean tolerance')
        close(qend[i],ends[i]*min(1.,actual[i]/means[i]) if means[i]>0. else 0.,1e-7,'dynamic_committed_end_'+device+'_kW',errors)
        close(energy[i],actual[i]*dt,STOCK_TOL,'dynamic_energy_'+device+'_kWh',errors)
        close(e1[i]-e0[i],energy[i],STOCK_TOL,'dynamic_cumulative_'+device+'_kWh',errors)
    support=get(dynamic,'actual_support_running')
    for name,active,tau,available in [('tower',num(a,'tower_rejection_kW')>1e-9,num(cfg,'tower_support_tau_seconds'),
                num(hardware,'TOWER')*math.fsum(num(hardware,'TOWER_'+str(i)) for i in (1,2,3,4))/4.),
            ('pump',qc+cd+cc>1e-9,num(cfg,'pump_support_tau_seconds'),num(hardware,'AUX_CHW_PUMP'))]:
        if type(get(support,name)) is not bool or support[name]!=active:raise ValueError('Support flag does not match actual action: '+name)
        sm,se=rising(num(before,name+'_ready'),1.,dt*3600.,tau) if available>0. else (0.,0.)
        close(num(preview,name+'_mean_factor'),sm,1e-9,name+'_support_mean',errors)
        close(num(preview,name+'_end_factor'),se,1e-9,name+'_support_end',errors)
        close(num(after,name+'_ready'),se if active else 0.,1e-9,name+'_support_actual_commit',errors)
    tower_cap=num(row,'m24_tower_capacity','capacity_kW')*num(hardware,'TOWER')*math.fsum(num(hardware,'TOWER_'+str(i)) for i in (1,2,3,4))/4.*num(preview,'tower_mean_factor')
    if num(a,'tower_rejection_kW')>tower_cap+RATE_TOL:raise ValueError('Tower rejection exceeds actual dynamic hardware capacity')
    if previous_dynamic is not None:
        for key in ('cooling_kW','actual_cooling_kWh_by_device'):
            for i,(x,y) in enumerate(zip(get(before,key),get(previous_dynamic,key))):close(x,y,STOCK_TOL,'dynamic_continuity_'+key+'_'+str(i),errors)
        for key in ('elapsed_minutes','tower_ready','pump_ready'):close(num(before,key),num(previous_dynamic,key),CLOCK_TOL,'dynamic_continuity_'+key,errors)
    return dict(stocks=stocks,dynamic=after,residuals=errors,energy_kWh=dict(electric_source=e_source*dt,electric_sink=e_sink*dt,
        heat_delivered=served[1]*dt,cooling_source=(qc+cd)*dt,cooling_sink=(served[2]+cc+rec_c)*dt,tower_rejection=tower_expected*dt))


def audit_producer(path,resolved,result,variant,expected_end):
    cursor=0.;stocks=dynamic=None;residuals={};count=noopt=0;totals={}
    with Path(path).open(encoding='utf8') as stream:
        for line in stream:
            row=json.loads(line);start=num(row,'start_minute');end=num(row,'end_minute')
            if abs(start-cursor)>CLOCK_TOL:raise ValueError('Actual audit coverage gap/overlap')
            check=audit_row(row,resolved,previous_stocks=stocks,previous_dynamic=dynamic,residuals=residuals)
            stocks,dynamic=check['stocks'],check['dynamic'];count+=1;cursor=end
            for key,value in check['energy_kWh'].items():totals[key]=totals.get(key,0.)+value
            if variant=='unattended' and start>=60.-1e-9:
                noopt+=1
                if get(row,'actual_result_owner')!='arithmetic_forward' or get(row,'forward_contract')!='hospital_ies.fixed_reference_arithmetic.v1' or get(row,'dispatch_optimizer_calls')!=0:raise ValueError('Post60 U does not carry actual arithmetic/no-optimizer ownership')
    if count==0 or abs(cursor-expected_end)>CLOCK_TOL:raise ValueError('Actual audit coverage is incomplete')
    if variant=='unattended' and (noopt==0 or get(result,'strict_noopt_check','optimizer_entry_attempts')!=[] or get(result,'strict_noopt_check','all_post60_segments_arithmetic') is not True):raise ValueError('Strict no-optimization actual entry evidence failed')
    return dict(status='passed',segment_count=count,end_minute=cursor,independently_computed_max_abs_residuals=residuals,
        integrated_actual_energy_kWh=totals,post60_strict_U_segments=noopt,all_formulas_recomputed_from_actual_fields=True)


def audit(root):
    root=Path(root).resolve();destination=root/'audit01';destination.mkdir(exist_ok=False)
    report=dict(schema=SCHEMA,status='failed',scope='Finite actual energy, recorded stocks and dynamic commit arithmetic only; no native primal or OEM/clinical qualification',
        units=dict(power='kW',energy='kWh',steam_rate='lb/h',time='minutes',storage_loss_rate='1/h'),
        engineering_tolerances=dict(balance_kW=RATE_TOL,steam_lbph=RATE_TOL,storage_kWh=STOCK_TOL,clock_minute=CLOCK_TOL,
            dynamic_actual_mean_bound='Original model1e-6*max(1,mean_kW)'),
        electricity_boundary='Building total delivered electricity includes its assigned auxiliaries exactly once;conversion/station/storage/recovery/charging are separate sinks',
        producer_results={},errors=[])
    try:
        registration=read(root/'REGISTRATION.json');selection=read(root/'SELECTION.json')
        report['source_binding']=dict(path=str(root/'SOURCE_BINDING.json'),sha256=sha(root/'SOURCE_BINDING.json'))
        if len(registration['producers'])!=19 or registration['topics'][7]['producers']!=registration['topics'][4]['producers']:raise ValueError('19 unique producer / T08=T05 contract differs')
        for item in registration['producers']:
            label=item['label']
            try:
                selected=get(selection,'producers',label);p=Path(selected['producer']);result=read(p/'RESULT.json')
                if sha(p/'RESULT.json')!=selected['result_sha256'] or result.get('status')!='completed' or result.get('source_binding')!=report['source_binding']:raise ValueError('Missing/changed/mixed completed producer')
                for name in ('SEGMENTS.jsonl','RESOLVED_CASE.json'):
                    pin=next((x for x in result['files'] if Path(x['path']).name==name),None)
                    if pin is None or Path(pin['path']).resolve()!=p/name or sha(p/name)!=pin['sha256']:raise ValueError('Required actual evidence pin missing or changed: '+name)
                checked=audit_producer(p/'SEGMENTS.jsonl',read(p/'RESOLVED_CASE.json'),result,item['variant'],float(item['duration_minutes']))
                checked['selected_result_sha256']=selected['result_sha256'];checked['producer']=str(p)
                report['producer_results'][label]=checked;write(destination/(label+'.json'),checked)
            except Exception as exc:
                report['errors'].append(dict(label=label,type=type(exc).__name__,message=str(exc)))
        if report['errors'] or len(report['producer_results'])!=19:raise ValueError('Independent engineering balance audit did not pass all19 producers')
        report.update(status='passed',unique_main_producers=19,logical_condition_rows=24,T08_exact_T05_continuous_alias=True)
    except Exception as exc:
        report['errors'].append(dict(type=type(exc).__name__,message=str(exc)))
    write(destination/'CAMPAIGN_AUDIT.json',report)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args();result=audit(args.root)
    print(json.dumps(dict(status=result['status'],passed_producers=len(result['producer_results']),errors=result['errors']),ensure_ascii=False))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
