"""Bounded REopt-family fixed-capacity offline mathematical reference.

No Hospital core imports, state mutation, simulation or implicit future input.
Inputs must explicitly grant offline-known-window permission. A feasible plan
is mathematical output, not a claim that all continuous powers were executed.
Hirwa (2023) pp5/16/17: 1/2, A.1/A.2/A.3/A.4/A.5 form the equation basis.
"""
from __future__ import annotations
import copy
import math
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

SCHEMA='hospital_ies.offline_reference.v30.v1'


def _at(value,h):
    return float(value[h] if isinstance(value,(list,tuple,np.ndarray)) else value)


def fixture_spec(fixture):
    """Use only explicitly artificial parameters from the hand-derived fixture."""
    p=fixture['parameters'];n=len(p['duration_h'])
    return dict(schema=SCHEMA,evidence_class='artificial_test_fixture',
        information_permission='offline_known_window',duration_h=p['duration_h'],
        demand_e=p['direct_electric_demand_kW_e'],demand_h=p['useful_heat_demand_kW_h'],
        demand_c=p['useful_cooling_demand_kW_c'],
        grid_capacity=[p['grid_capacity_kW_e'] if v else 0 for v in p['grid_available']],
        grid_price=p['grid_price_USD_per_kWh_e'],
        generators=[dict(name='CHP',capacity=p['CHP_capacity_kW_e'],min_power=p['CHP_min_on_power_kW_e'],
            fuel_slope=p['CHP_fuel_slope_kWh_fuel_per_kWh_e'],fuel_fixed=p['CHP_fixed_fuel_kWh_fuel_per_capacity_kW_on_hour']*p['CHP_capacity_kW_e'],
            heat_slope=p['CHP_heat_kW_h_per_kW_e'],heat_fixed=p['CHP_fixed_heat_kW_h_per_capacity_kW_on']*p['CHP_capacity_kW_e'],fuel_type='gas'),
            dict(name='DG',capacity=[0 if v else p['DG_capacity_kW_e'] for v in p['grid_available']],
            min_power=p['DG_min_on_power_kW_e'],fuel_slope=p['DG_fuel_slope_kWh_fuel_per_kWh_e'],
            fuel_fixed=p['DG_fixed_fuel_kWh_fuel_per_on_hour'],heat_slope=0,heat_fixed=0,fuel_type='diesel')],
        boiler=dict(capacity=p['boiler_useful_heat_capacity_kW_h'],fuel_slope=1/p['boiler_efficiency'],fuel_type='gas'),
        electric_chillers=[dict(name='EC',capacity=p['EC_cooling_capacity_kW_c'],electric_per_c=1/p['EC_COP'],tower_per_c=0)],
        absorption=dict(capacity=p['ABS_cooling_capacity_kW_c'],heat_per_c=1/p['ABS_COP'],electric_per_c=0,tower_per_c=0),
        tower_capacity=[1e12]*n,
        storages=[dict(name='battery',carrier='e',capacity=p['battery_capacity_kWh_e'],power=p['battery_power_sum_capacity_kW_e'],
            initial=p['battery_initial_stock_kWh_e'],minimum=p['battery_min_stock_kWh_e'],
            charge_efficiency=p['battery_charge_efficiency'],discharge_efficiency=p['battery_discharge_efficiency'],
            decay_per_h=0,loss_mode='none',charge_allowed=[True]*n,discharge_allowed=[True]*n,
            terminal_min=p['battery_terminal_stock_kWh_e'],terminal_max=p['battery_terminal_stock_kWh_e'])],
        fuel_budgets=dict(diesel=p['DG_fuel_budget_kWh_fuel'],gas=p['gas_fuel_budget_kWh_fuel']),
        fuel_prices=dict(diesel=p['DG_price_USD_per_kWh_fuel'],gas=p['gas_price_USD_per_kWh_fuel']))


def _validate(spec):
    if spec.get('information_permission')!='offline_known_window':
        raise ValueError('Explicit offline_known_window permission is required')
    n=len(spec['duration_h'])
    if n<1:raise ValueError('At least one period required')
    def check(value,positive=False):
        vals=np.asarray(value if isinstance(value,(list,tuple,np.ndarray)) else [value],dtype=float)
        if vals.ndim!=1 or (isinstance(value,(list,tuple,np.ndarray)) and vals.size!=n) or not np.isfinite(vals).all() or (vals<0).any() or (positive and (vals<=0).any()):
            raise ValueError('Finite nonnegative coefficients with matching horizon required')
    check(spec['duration_h'],True)
    for k in ('demand_e','demand_h','demand_c','grid_capacity','grid_price','tower_capacity'):check(spec[k])
    names=set()
    for g in spec['generators']:
        if g['name'] in names:raise ValueError('Duplicate generator name')
        names.add(g['name'])
        for k in ('capacity','min_power','fuel_slope','fuel_fixed','heat_slope','heat_fixed'):check(g.get(k,0))
    for obj in [spec['boiler'],spec['absorption']]+spec['electric_chillers']:
        for key,value in obj.items():
            if key not in ('name','fuel_type'):check(value)
    for s in spec['storages']:
        if s['carrier'] not in ('e','h','c') or s.get('loss_mode','none') not in ('none','implicit_end','exponential_beginning'):
            raise ValueError('Unknown storage carrier or discretization')
        for k in ('capacity','power','initial','minimum','decay_per_h','terminal_min','terminal_max'):
            if k in s:check(s[k])
        for k in ('charge_efficiency','discharge_efficiency'):
            check(s[k],True)
            if s[k]>1:raise ValueError('Storage efficiency cannot exceed one')
        for k in ('charge_allowed','discharge_allowed','discharge_limit'):
            if k in s:check(s[k])
        if not s.get('minimum',0)<=s['initial']<=s['capacity']:raise ValueError('Initial stock outside capacity/SOC domain')
        if not s.get('minimum',0)<=s.get('terminal_min',s.get('minimum',0))<=s.get('terminal_max',s['capacity'])<=s['capacity']:
            raise ValueError('Terminal stock outside capacity/SOC domain')
    for value in list(spec['fuel_budgets'].values())+list(spec['fuel_prices'].values()):check(value)
    return n


def classify_termination(result):
    incumbent=result.x is not None and bool(np.isfinite(result.x).all())
    if result.status==0 and incumbent:return dict(status='optimal',termination_reason='optimal',incumbent=True)
    if result.status==1:return dict(status='time_limit' if incumbent else 'no_incumbent',termination_reason='limit_reached',incumbent=incumbent)
    if result.status==2:return dict(status='infeasible',termination_reason='proved_infeasible',incumbent=False)
    if result.status==3:return dict(status='unbounded',termination_reason='proved_unbounded',incumbent=False)
    return dict(status='solver_error' if result.status else 'no_incumbent',termination_reason='solver_returned_no_valid_plan',incumbent=False)


def solve_reference(spec, time_limit_s=10.0):
    """Solve fixed-capacity hard-service planning; never replace no plan by zeros."""
    n=_validate(spec)
    if not math.isfinite(time_limit_s) or time_limit_s<0:raise ValueError('Invalid solve time limit')
    keys=['grid','boiler','boiler.on','ABS','heat_waste']
    keys += [e['name'] for e in spec['electric_chillers']]
    for g in spec['generators']:keys += [g['name'],g['name']+'.on']
    for s in spec['storages']:keys += [s['name']+'.'+kind for kind in ('charge','discharge','stock')]
    if len(set(keys))!=len(keys):raise ValueError('Variable names collide')
    index={(k,h):j*n+h for j,k in enumerate(keys) for h in range(n)}
    nv=len(index);c=np.zeros(nv);lo=np.zeros(nv);hi=np.full(nv,np.inf);integer=np.zeros(nv)
    rr=[];cc=[];dd=[];lower=[];upper=[];row_names=[]
    def row(terms,lb,ub,name):
        r=len(lower)
        for k,v in terms.items():
            if v:rr.append(r);cc.append(k);dd.append(v)
        lower.append(lb);upper.append(ub);row_names.append(name)
    def V(k,h):return index[k,h]
    fuel_terms={f:{} for f in spec['fuel_budgets']}
    for h in range(n):
        dt=_at(spec['duration_h'],h)
        hi[V('grid',h)]=_at(spec['grid_capacity'],h);c[V('grid',h)]=dt*_at(spec['grid_price'],h)
        b=spec['boiler'];bc=_at(b['capacity'],h);hi[V('boiler',h)]=bc;hi[V('boiler.on',h)]=float(bc>0);integer[V('boiler.on',h)]=1
        row({V('boiler',h):1,V('boiler.on',h):-bc},-np.inf,0,'boiler.on')
        bf=_at(b.get('fuel_slope',0),h);fuel=b.get('fuel_type','gas');price=_at(spec['fuel_prices'].get(fuel,0),h)
        c[V('boiler',h)]=dt*bf*price
        if fuel in fuel_terms:fuel_terms[fuel][V('boiler',h)]=dt*bf
        ab=spec['absorption'];hi[V('ABS',h)]=_at(ab['capacity'],h)
        balances={'e':{V('grid',h):1,V('boiler',h):-_at(b.get('electric_per_h',0),h),V('boiler.on',h):-_at(b.get('fixed_electric',0),h),V('ABS',h):-_at(ab.get('electric_per_c',0),h)},
                  'h':{V('boiler',h):1,V('ABS',h):-_at(ab['heat_per_c'],h),V('heat_waste',h):-1},
                  'c':{V('ABS',h):1}}
        tower={V('ABS',h):_at(ab.get('tower_per_c',0),h)}
        for e in spec['electric_chillers']:
            k=e['name'];hi[V(k,h)]=_at(e['capacity'],h)
            balances['e'][V(k,h)]=-_at(e['electric_per_c'],h);balances['c'][V(k,h)]=1
            tower[V(k,h)]=_at(e.get('tower_per_c',0),h)
        for g in spec['generators']:
            k=g['name'];p=V(k,h);z=V(k+'.on',h);cap=_at(g['capacity'],h)
            hi[p]=cap;hi[z]=float(cap>0);integer[z]=1
            row({p:1,z:-cap},-np.inf,0,k+'.capacity')
            row({p:1,z:-_at(g.get('min_power',0),h)},0,np.inf,k+'.minimum')
            balances['e'][p]=1;balances['e'][z]=-_at(g.get('fixed_electric',0),h)
            balances['h'][p]=_at(g.get('heat_slope',0),h);balances['h'][z]=_at(g.get('heat_fixed',0),h)
            fs=_at(g.get('fuel_slope',0),h);ff=_at(g.get('fuel_fixed',0),h);f=g['fuel_type'];price=_at(spec['fuel_prices'].get(f,0),h)
            c[p]=dt*fs*price;c[z]=dt*ff*price
            if f in fuel_terms:fuel_terms[f][p]=dt*fs;fuel_terms[f][z]=dt*ff
            if 'electric_service_limit' in g:hi[p]=min(hi[p],_at(g['electric_service_limit'],h))
        for s in spec['storages']:
            k=s['name'];ch=V(k+'.charge',h);dis=V(k+'.discharge',h);st=V(k+'.stock',h);power=float(s['power'])
            hi[ch]=power*bool(_at(s.get('charge_allowed',1),h));hi[dis]=power*bool(_at(s.get('discharge_allowed',1),h))
            if 'discharge_limit' in s:hi[dis]=min(hi[dis],_at(s['discharge_limit'],h))
            lo[st]=float(s.get('minimum',0));hi[st]=float(s['capacity'])
            mode=s.get('loss_mode','none');rate=float(s.get('decay_per_h',0));a=1+dt*rate if mode=='implicit_end' else 1
            retained=math.exp(-rate*dt) if mode=='exponential_beginning' else 1
            terms={st:a,ch:-dt*float(s['charge_efficiency']),dis:dt/float(s['discharge_efficiency'])}
            if h:terms[V(k+'.stock',h-1)]=-retained;rhs=0
            else:rhs=float(s['initial'])*retained
            row(terms,rhs,rhs,k+'.inventory')
            row({ch:1,dis:1},-np.inf,power,k+'.power_sum')
            balances[s['carrier']][dis]=balances[s['carrier']].get(dis,0)+1
            balances[s['carrier']][ch]=balances[s['carrier']].get(ch,0)-1
            for variable,field in [(ch,'charge_electric_per_carrier'),(dis,'discharge_electric_per_carrier')]:
                balances['e'][variable]=balances['e'].get(variable,0)-float(s.get(field,0))
            if h==n-1:
                lo[st]=max(lo[st],float(s.get('terminal_min',0)));hi[st]=min(hi[st],float(s.get('terminal_max',s['capacity'])))
        for carrier,terms in balances.items():
            demand=_at(spec['demand_'+carrier],h);row(terms,demand,demand,carrier+'.balance')
        row(tower,-np.inf,_at(spec['tower_capacity'],h),'tower.capacity')
    for fuel,terms in fuel_terms.items():row(terms,-np.inf,float(spec['fuel_budgets'][fuel]),'budget.'+fuel)
    matrix=coo_matrix((dd,(rr,cc)),shape=(len(lower),nv)).tocsc()
    result=milp(c=c,integrality=integer,bounds=Bounds(lo,hi),constraints=LinearConstraint(matrix,np.array(lower),np.array(upper)),
        options={'time_limit':float(time_limit_s),'mip_rel_gap':1e-9})
    status=classify_termination(result)
    plan={k:[float(result.x[V(k,h)]) for h in range(n)] for k in keys} if status['incumbent'] else None
    audit=audit_plan(spec,plan) if plan is not None else None
    return dict(schema=SCHEMA,**status,raw_status=int(result.status),solver_message=str(result.message),
        objective_fee=float(result.fun) if plan is not None else None,plan=plan,audit=audit,
        mip_gap=float(result.mip_gap) if getattr(result,'mip_gap',None) is not None else None,
        variable_count=nv,binary_count=int(integer.sum()),constraint_count=len(lower),
        evidence_class=spec.get('evidence_class','offline_mathematical_plan'),
        information_permission=spec['information_permission'],actual_replayed=False)


def audit_plan(spec, plan, tolerance=1e-6):
    """Independent named-flow checks; no solver matrix/index/constraint reuse."""
    n=_validate(spec)
    if plan is None:return dict(valid=False,reason='no_plan',maximum_violation=None)
    violations=[];residuals=[];fuel_used={};fee=0.0;standing={};previous={s['name']:float(s['initial']) for s in spec['storages']}
    def check(name,value):
        if not math.isfinite(value) or value>tolerance:violations.append(dict(check=name,violation=float(value)))
    try:
        for values in plan.values():
            if len(values)!=n or not np.isfinite(values).all():raise ValueError('Malformed plan array')
            for value in values:check('nonnegative',-float(value))
        for h in range(n):
            P=lambda key:float(plan[key][h]);dt=_at(spec['duration_h'],h)
            e=P('grid');heat=P('boiler')-P('heat_waste');cold=P('ABS');ab=spec['absorption'];b=spec['boiler']
            check('grid',P('grid')-_at(spec['grid_capacity'],h));fee+=dt*P('grid')*_at(spec['grid_price'],h)
            check('boiler_on',abs(P('boiler.on')-round(P('boiler.on'))));check('boiler_capacity',P('boiler')-_at(b['capacity'],h)*P('boiler.on'))
            check('boiler_binary_upper',P('boiler.on')-1)
            e-=P('boiler')*_at(b.get('electric_per_h',0),h)+P('boiler.on')*_at(b.get('fixed_electric',0),h)
            e-=P('ABS')*_at(ab.get('electric_per_c',0),h);heat-=P('ABS')*_at(ab['heat_per_c'],h)
            tower=P('ABS')*_at(ab.get('tower_per_c',0),h);check('ABS_capacity',P('ABS')-_at(ab['capacity'],h))
            bfuel=dt*P('boiler')*_at(b.get('fuel_slope',0),h);ft=b.get('fuel_type','gas');fuel_used[ft]=fuel_used.get(ft,0)+bfuel;fee+=bfuel*_at(spec['fuel_prices'].get(ft,0),h)
            for ec in spec['electric_chillers']:
                q=P(ec['name']);check(ec['name']+'.capacity',q-_at(ec['capacity'],h));cold+=q;e-=q*_at(ec['electric_per_c'],h);tower+=q*_at(ec.get('tower_per_c',0),h)
            for g in spec['generators']:
                p=P(g['name']);z=P(g['name']+'.on');check(g['name']+'.integral',abs(z-round(z)));check(g['name']+'.binary_upper',z-1)
                check(g['name']+'.capacity',p-_at(g['capacity'],h)*z);check(g['name']+'.minimum',_at(g.get('min_power',0),h)*z-p)
                if 'electric_service_limit' in g:check(g['name']+'.service_limit',p-_at(g['electric_service_limit'],h))
                e+=p-z*_at(g.get('fixed_electric',0),h);heat+=p*_at(g.get('heat_slope',0),h)+z*_at(g.get('heat_fixed',0),h)
                f=dt*(p*_at(g.get('fuel_slope',0),h)+z*_at(g.get('fuel_fixed',0),h));ft=g['fuel_type'];fuel_used[ft]=fuel_used.get(ft,0)+f;fee+=f*_at(spec['fuel_prices'].get(ft,0),h)
            for s in spec['storages']:
                k=s['name'];ch=P(k+'.charge');dis=P(k+'.discharge');stock=P(k+'.stock');mode=s.get('loss_mode','none');rate=float(s.get('decay_per_h',0))
                loss=dt*rate*stock if mode=='implicit_end' else previous[k]*(1-math.exp(-dt*rate)) if mode=='exponential_beginning' else 0
                expected=previous[k]-loss+dt*(float(s['charge_efficiency'])*ch-dis/float(s['discharge_efficiency']))
                r=stock-expected;check(k+'.inventory',abs(r));check(k+'.capacity',stock-s['capacity']);check(k+'.minimum',s.get('minimum',0)-stock)
                check(k+'.power_sum',ch+dis-s['power']);check(k+'.charge_mode',ch-s['power']*bool(_at(s.get('charge_allowed',1),h)));check(k+'.discharge_mode',dis-s['power']*bool(_at(s.get('discharge_allowed',1),h)))
                if 'discharge_limit' in s:check(k+'.eligibility',dis-_at(s['discharge_limit'],h))
                if h==n-1:check(k+'.terminal_min',s.get('terminal_min',0)-stock);check(k+'.terminal_max',stock-s.get('terminal_max',s['capacity']))
                standing[k]=standing.get(k,0)+loss;previous[k]=stock
                if s['carrier']=='e':e+=dis-ch
                elif s['carrier']=='h':heat+=dis-ch
                else:cold+=dis-ch
                e-=ch*s.get('charge_electric_per_carrier',0)+dis*s.get('discharge_electric_per_carrier',0)
            row=dict(period=h,electric=e-_at(spec['demand_e'],h),heat=heat-_at(spec['demand_h'],h),cooling=cold-_at(spec['demand_c'],h),tower_violation=max(0,tower-_at(spec['tower_capacity'],h)))
            for key in ('electric','heat','cooling'):check(key+'.balance',abs(row[key]))
            check('tower.capacity',row['tower_violation']);residuals.append(row)
        for f,budget in spec['fuel_budgets'].items():check('fuel_budget.'+f,fuel_used.get(f,0)-budget)
    except (KeyError,ValueError,TypeError) as exc:
        return dict(valid=False,reason='invalid_or_incomplete_plan',error=str(exc),maximum_violation=None)
    return dict(valid=not violations,violations=violations,period_residuals=residuals,fuel_used=fuel_used,
        independently_recomputed_fee=fee,storage_standing_loss=standing,tolerance=tolerance,
        maximum_balance_residual=max([abs(row[k]) for row in residuals for k in ('electric','heat','cooling')],default=0))


def extract_plan_actions(spec, plan, preparation_lead_minutes=0.0):
    """Extract queued setpoint/preparation *requests*, never storage controls.

    A positive lead may request a future on target earlier, within known current
    hardware capacity. Common execution remains responsible for permission,
    delay, startup, physical projection and real-time reallocation.
    """
    if plan is None or not audit_plan(spec,plan)['valid']:
        raise ValueError('A verified incumbent plan is required')
    mapping=spec.get('dispatch_mapping')
    if not mapping:raise ValueError('An explicit Hospital dispatch mapping is required')
    if not math.isfinite(preparation_lead_minutes) or preparation_lead_minutes<0:raise ValueError('Invalid preparation lead')
    n=len(spec['duration_h']);starts=[];clock=float(spec.get('start_minute',0))
    for dt in spec['duration_h']:starts.append(clock);clock+=60*dt
    gens={g['name']:g for g in spec['generators']};requests=[]
    for h in range(n):
        future=[j for j in range(h,n) if starts[j]<=starts[h]+preparation_lead_minutes+1e-9]
        gt=[];preparations=[]
        for name in mapping['gt_names']:
            if name is None:gt.append(0.);continue
            target=max(float(plan[name][j]) for j in future)
            target=min(target,_at(gens[name]['capacity'],h));gt.append(target)
            if target>float(plan[name][h])+1e-7:preparations.append(dict(device=name,kind='early_on_target_request',current_plan_kW=plan[name][h],requested_kW=target,reason='offline_known_future_within_registered_lead'))
        boiler=max(float(plan['boiler'][j]) for j in future)
        boiler=min(boiler,_at(spec['boiler']['capacity'],h))
        if boiler>float(plan['boiler'][h])+1e-7:preparations.append(dict(device='boiler',kind='early_heat_setting_request',current_plan_kW=plan['boiler'][h],requested_kW=boiler))
        ec=[float(plan[name][h]) if name else 0. for name in mapping['ec_names']]
        ab=float(plan['ABS'][h]);tower=ab*_at(spec['absorption'].get('tower_per_c',0),h)
        for obj in spec['electric_chillers']:tower+=float(plan[obj['name']][h])*_at(obj.get('tower_per_c',0),h)
        steam=sum(power*_at(mapping['gt_steam_per_e'][i],h) for i,power in enumerate(gt))
        fuel=0.
        for i,name in enumerate(mapping['gt_names']):
            if name:fuel+=gt[i]*_at(gens[name].get('fuel_slope',0),h)+(gt[i]>1e-9)*_at(gens[name].get('fuel_fixed',0),h)
        dispatch=dict(gt_electric_kW=gt,grid_import_kW=float(plan['grid'][h]),
            diesel_kW=float(plan[mapping['dg_name']][h]) if mapping.get('dg_name') else 0.,
            boiler_steam_lbph=boiler*float(mapping['boiler_heat_to_steam']),
            absorption_cooling_kW=ab,electric_chiller_cooling_kW=ec,
            hrsg_steam_lbph=steam,tower_rejection_kW=tower,gt_fuel_MMBtu_h=fuel)
        corrections=[]
        for field,value in list(dispatch.items()):
            values=value if isinstance(value,list) else [value]
            revised=[]
            for j,v in enumerate(values):
                if v<0:
                    if v < -1e-9:raise ValueError('Material negative dispatch cannot become a request')
                    corrections.append(dict(field=field+'['+str(j)+']' if isinstance(value,list) else field,before=v,after=0.,reason='solver_nonnegative_bound_roundoff'))
                    v=0.
                revised.append(v)
            dispatch[field]=revised if isinstance(value,list) else revised[0]
        omitted={key:float(plan[key][h]) for s in spec['storages'] for key in (s['name']+'.charge',s['name']+'.discharge',s['name']+'.stock')}
        requests.append(dict(time_minute=starts[h],period=h,preparation_requests=preparations,numeric_roundoff_corrections=corrections,
            action={'ordinary_dispatch':dict(dispatch=dispatch,disconnected_kW=[[0.,0.,0.] for _ in range(4)],
                rule_diagnostic=dict(policy_id='R_off',offline_known_future=True,planning_only=True,
                    full_continuous_plan_replayed=False,preparation_lead_minutes=preparation_lead_minutes))},
            unexecuted_storage_plan=omitted,storage_ports_available=False,
            requested_dispatch_is_not_execution_receipt=True))
    return dict(schema=SCHEMA,execution_identity='offline_onoff_preparation_plus_common_realtime_allocation',
        requests=requests,actual_replayed=False,full_continuous_plan_replayed=False,
        information_permission='offline_known_window',
        gaps=['ordinary_dispatch has no UPS/cold charge-discharge port',
              'continuous source/converter requests may be reallocated by common physical solver',
              'startup, communication, switching and protection require actual common execution receipts'])


def hospital_envelope_spec(snapshot):
    """Pure-data adapter of existing Hospital static equipment, not a simulator.

    Requires native original-demand ledgers and actual stock, with explicit
    provenance. Retains all four priority demands and freezes them as recorded.
    C-conditioned auxiliary requests make this unsuitable for causal rankings.
    """
    plant=snapshot['plant'];periods=snapshot['periods'];n=len(periods)
    if n==0:raise ValueError('Missing original periods')
    try:loads=np.asarray([p['original_demand_kW'] for p in periods],dtype=float)
    except KeyError as exc:raise ValueError('Missing original demand ledger') from exc
    if loads.shape!=(n,4,3) or not np.isfinite(loads).all() or (loads<0).any():raise ValueError('Invalid original demand ledger')
    heat_factor=float(plant['steam_kWh_per_lb'])*float(plant['exchanger_eff']);rt=3.51685284;kwh_mmbtu=293.0710702
    availability=[p['availability'] for p in periods]
    def A(name):
        if any(name not in a for a in availability):raise ValueError('Missing original availability '+name)
        return [float(a[name]) for a in availability]
    rated=[float(p['gt_capacity_kW']) for p in periods];steam=[float(p['gt_steam_lbph_per_kW']) for p in periods];fuel=[float(p['gt_fuel_MMBtu_per_kWh']) for p in periods]
    generators=[]
    for i in range(3):
        av=A('GT_'+str(i+1));ha=A('HRSG_'+str(i+1))
        generators.append(dict(name='GT_'+str(i+1),capacity=[rated[h]*av[h] for h in range(n)],min_power=[0.4*rated[h]*av[h] for h in range(n)],
            fuel_type='gas',fuel_slope=fuel,fuel_fixed=0,
            heat_slope=[steam[h]*ha[h]*heat_factor for h in range(n)],heat_fixed=0))
    grid_av=A('GRID');dgav=A('DIESEL');critical_e=loads[:,:2,0].sum(axis=1).tolist();critical_c=loads[:,:2,2].sum(axis=1).tolist()
    generators.append(dict(name='DG',capacity=[plant['emergency_diesel_kW']*dgav[h] if grid_av[h]<1-1e-9 else 0 for h in range(n)],
        min_power=0,fuel_type='diesel_shaft',fuel_slope=1,fuel_fixed=snapshot['diesel_loss_kW'],
        heat_slope=0,heat_fixed=0,electric_service_limit=critical_e))
    boilers=[A('BOILER_'+str(i+1)) for i in range(3)]
    boiler=dict(capacity=[plant['boiler_steam_lbph']/3*sum(boilers[i][h] for i in range(3))*heat_factor for h in range(n)],
        fuel_slope=1/(plant['exchanger_eff']*plant['boiler_eff']*kwh_mmbtu),fuel_type='gas',
        fixed_electric=snapshot['boiler_station_fixed_kW'],electric_per_h=snapshot['boiler_electric_per_kW_steam']/plant['exchanger_eff'])
    ec=[]
    for i in range(3):
        av=A('EC_'+str(i+1));ec.append(dict(name='EC_'+str(i+1),capacity=[plant['electric_chillers_rt'][i]*rt*av[h] for h in range(n)],
            electric_per_c=plant['ec_electric_kW_per_rt']/rt,tower_per_c=1+plant['ec_electric_kW_per_rt']/rt))
    aa=A('ABS');ta=A('TOWER')
    absorption=dict(capacity=[plant['absorption_rt']*rt*aa[h] for h in range(n)],
        heat_per_c=plant['abs_steam_lb_per_rt']*heat_factor/rt,
        electric_per_c=plant['abs_electric_kW_per_rt']/rt,
        tower_per_c=1+plant['abs_steam_lb_per_rt']*plant['steam_kWh_per_lb']/rt)
    stores=[]
    for name,carrier,critical in [('ups','e',critical_e),('cold_store','c',critical_c)]:
        cfg=snapshot['stores'][name]
        # Zero-size stores remain explicit, not reinterpreted as generic BESS.
        cap=float(cfg.get('capacity_kWh',0));power=float(cfg.get('power_kW',0))
        if name not in snapshot['initial_stocks']:raise ValueError('Missing actual initial stock '+name)
        charge=[grid_av[h]>=1-1e-9 and (name=='ups' or ta[h]>=1-1e-9) for h in range(n)]
        stores.append(dict(name=name,carrier=carrier,capacity=cap,power=power,initial=float(snapshot['initial_stocks'][name]),
            minimum=0,charge_efficiency=float(cfg.get('charge_efficiency',0.95)),discharge_efficiency=float(cfg.get('discharge_efficiency',0.95)),
            decay_per_h=float(cfg.get('loss_per_hour',0)),loss_mode='exponential_beginning',
            charge_allowed=charge,discharge_allowed=[not value for value in charge],discharge_limit=critical,
            charge_electric_per_carrier=snapshot['cold_pump_electric_per_kW'] if carrier=='c' else 0,
            discharge_electric_per_carrier=snapshot['cold_pump_electric_per_kW'] if carrier=='c' else 0))
    spec=dict(schema=SCHEMA,evidence_class='historical_C_conditioned_static_planning_envelope',
        information_permission='offline_known_window',physical_execution_validated=False,
        start_minute=periods[0]['start_minute'],duration_h=[(p['end_minute']-p['start_minute'])/60 for p in periods],
        demand_e=loads[:,:,0].sum(axis=1).tolist(),demand_h=loads[:,:,1].sum(axis=1).tolist(),demand_c=loads[:,:,2].sum(axis=1).tolist(),
        original_demand_by_priority_kW=loads.tolist(),grid_capacity=[snapshot['grid_limit_kW']*x for x in grid_av],grid_price=[p['grid_price'] for p in periods],
        generators=generators,boiler=boiler,electric_chillers=ec,absorption=absorption,
        tower_capacity=[snapshot['tower_capacity_kW']*x for x in ta],storages=stores,
        fuel_budgets={'diesel_shaft':snapshot['diesel_shaft_budget_kWh']},fuel_prices={'gas':plant['gas_price_usd_per_mmbtu'],'diesel_shaft':0.30},
        dispatch_mapping=dict(gt_names=['GT_1','GT_2','GT_3'],dg_name='DG',ec_names=['EC_1','EC_2','EC_3'],boiler_heat_to_steam=1/heat_factor,
            gt_steam_per_e=[[steam[h]*A('HRSG_'+str(i+1))[h] for h in range(n)] for i in range(3)]),
        provenance=copy.deepcopy(snapshot['provenance']),
        registered_simplifications=[
            'Demand/availability/native interval partition frozen from an observed C trajectory; auxiliary requests and endogenous trip history may depend on C',
            'Static full-window envelope omits GT ramp/startup/minimum-up/down and command/transfer/readiness queue dynamics',
            'GT fuel uses existing common-planner full-load linear slope, not a new part-load fit',
            'DG shaft accounting includes terminal output plus frozen rated-speed running loss; startup/rotor/governor work not represented',
            'DG electrical contribution conservatively capped at P0/P1 building electricity, no grant to arbitrary plant buses',
            'UPS discharge only P0/P1 building electricity; cold discharge only P0/P1 useful cold; no general BESS port',
            'Cold charge/discharge mode chosen by known grid/tower availability; actual mode search/deadband/bridge rule remains common-executor responsibility',
            'No terminal equality imposed; report inventories. Initial preparation charge/fee not included in plan within-window fee',
            'Original segment boundaries retained; no minute trajectory interpolation',
            'Finite gas availability not provided by original Hospital model; no invented gas budget',
            'Hard full P0-P3 demand required; no clinical unmet-load penalty or deletion of load',
            'No claim of original-paper reproduction, actual replay, causal ranking, performance upper bound or cost lower bound'])
    _validate(spec)
    return spec



def stabilize_symmetric_generators(spec, plan, names, initial_targets):
    """Relabel only exactly identical full-window units; preserve raw plan.

    MILP symmetry may swap unit identities every minute. Such exchange does not
    represent a need to start all units. This equivalent static plan is labeled,
    separately audited, and cannot erase real unit-specific availability/dynamics.
    """
    import json
    objects={g['name']:g for g in spec['generators']}
    signatures=[json.dumps({k:v for k,v in objects[name].items() if k!='name'},sort_keys=True) for name in names]
    if len(set(signatures))!=1:
        return dict(plan=copy.deepcopy(plan),verified_equivalent=False,reason='not_identical_over_complete_window',permutations=[])
    before=audit_plan(spec,plan)
    if not before['valid']:raise ValueError('Cannot relabel an invalid raw plan')
    canonical=copy.deepcopy(plan);previous=list(initial_targets);permutations=[]
    for h in range(len(spec['duration_h'])):
        targets=sorted(range(len(names)),key=lambda i:(-previous[i],i))
        sources=sorted(range(len(names)),key=lambda i:(-float(plan[names[i]][h]),i))
        perm=[]
        for target,source in zip(targets,sources):
            canonical[names[target]][h]=plan[names[source]][h]
            canonical[names[target]+'.on'][h]=plan[names[source]+'.on'][h]
            if target!=source:perm.append(dict(source=names[source],target=names[target]))
        if perm:permutations.append(dict(period=h,assignments=perm))
        previous=[float(canonical[name][h]) for name in names]
    after=audit_plan(spec,canonical)
    if not after['valid'] or abs(after['independently_recomputed_fee']-before['independently_recomputed_fee'])>1e-7:
        raise ValueError('Symmetry relabeling did not preserve independently audited feasibility and fee')
    return dict(plan=canonical,verified_equivalent=True,reason='identical_full_window_parameters_static_label_permutation',
        permutations=permutations,independent_audit=after,original_plan_unchanged=True,
        proves_dynamic_minimum_up_down_or_startup_feasibility=False)

