"""Explicit engineering-rate adapter of the frozen source ledger formulae.

Only instantaneous1e-7 rate comparison literals become the declared1e-6 rate
allowance. Existing1e-4 balance,1e-7 kWh inventory,1e-8 minute clock and dynamic
attainability bounds are retained. No service/protection criterion is changed.
The original source function digest is checked before use; exact uses source.
"""
import hashlib
import inspect
import math
from functools import partial
from engineering_receipts import canonical_profile

SOURCE_FORMULA_SHA256 = '75468b767a603c5d6b3ed96fab7c38ab5f65ffb962d55d3fa9b8b3021df930a9'


def make_auditor(dependency, profile):
    actual=inspect.getsource(dependency.audit_row).strip()
    if hashlib.sha256(actual.encode('utf8')).hexdigest()!=SOURCE_FORMULA_SHA256:
        raise ValueError('Engineering adapter formula derivation differs from bound source')
    if profile!=canonical_profile():raise ValueError('Canonical engineering audit profile required')
    return partial(audit_row_with_profile, dependency=dependency, profile=profile)


def audit_row_with_profile(row,resolved,*,previous_stocks=None,previous_dynamic=None,residuals=None,dependency=None,profile=None):
    if profile != canonical_profile(): raise ValueError('Canonical engineering numerical audit profile required')
    get,num,vec,close,rising = dependency.get,dependency.num,dependency.vec,dependency.close,dependency.rising
    RATE_TOL,STOCK_TOL,CLOCK_TOL = dependency.RATE_TOL,dependency.STOCK_TOL,dependency.CLOCK_TOL
    numerical_rate_tolerance = profile['tolerances']['physical_rows_bounds']
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
        if emergency>critical+numerical_rate_tolerance:raise ValueError('Shared emergencyDG/buildingUPS delivery exceeds actual critical building electricity')
        general_source=math.fsum(gt)+num(a,'grid_import_kW')
        general_only=cells[2][0]+cells[3][0]+converter_e+station+storage_pump+rec_p+rec_f+uc
        if general_only>general_source+numerical_rate_tolerance:raise ValueError('Station/conversion/charging/noncritical sinks lack an eligible general source')
    close(station,boiler_aux+starter+restart,numerical_rate_tolerance,'station_components_kW',errors)
    close(storage_pump,num(resolved,'sci_config','cold_pump_kW_per_kW')*(cd+cc),numerical_rate_tolerance,'cold_storage_pump_kW',errors)
    context=get(resolved,'reset_physical_context')
    close(rec_p,rec_c*num(context,'recovery_pump_kW_per_kW'),numerical_rate_tolerance,'recovery_pump_kW',errors)
    close(rec_f,rec_c*num(context,'recovery_fan_kW_per_kW'),numerical_rate_tolerance,'recovery_fan_kW',errors)
    boiler=num(a,'boiler_steam_lbph');hrsg=num(a,'hrsg_steam_lbph');unused=num(row,'unused_steam_lbph')
    steam_used=served[1]/(steam_heat*eta_h)+qa/rt*abs_steam
    if boiler>steam_used+numerical_rate_tolerance:raise ValueError('Boiler exceeds useful current heat/ABS steam purpose;unused steam cannot be assigned to boiler')
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
    for g in range(4):close(cells[g][0],direct[g]+math.fsum(aux[g]),numerical_rate_tolerance,f'building_electric_once_P{g}_kW',errors)
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
        if min(pc,pd,final)<-STOCK_TOL or max(pc,pd)>power+numerical_rate_tolerance or final>cap+STOCK_TOL or min(pc,pd)>numerical_rate_tolerance:raise ValueError('Actual stock power/energy/mode bound: '+name)
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
        close(means[i],pm,numerical_rate_tolerance,'dynamic_mean_'+device+'_kW',errors);close(ends[i],pe,numerical_rate_tolerance,'dynamic_end_'+device+'_kW',errors)
        close(actual[i],([qa]+ec)[i],numerical_rate_tolerance,'dynamic_actual_'+device+'_kW',errors)
        if actual[i]>means[i]+1e-6*max(1.,means[i]):raise ValueError('Actual cooling exceeds original dynamic mean tolerance')
        close(qend[i],ends[i]*min(1.,actual[i]/means[i]) if means[i]>0. else 0.,numerical_rate_tolerance,'dynamic_committed_end_'+device+'_kW',errors)
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
