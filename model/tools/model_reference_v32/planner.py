"""Independent-input fixed-capacity REopt-family static planning reference.

Hirwa's multi-period supply/conversion/storage structure is adapted to the
frozen hospital. Tiered service is an explicitly hospital-added adaptation.
No C/M trajectories, environment, execution hooks or actual-state writes.
"""
from __future__ import annotations

import copy
import json
import math
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
from tools.model_review_v30.offline import _validate as _validate_static

SCHEMA = 'hospital_ies.model_reference_v32.priority_plan.v1'
PRIORITIES = ('P0','P1','P2','P3')
CARRIERS = ('e','h','c')
LEX_LOCK_TOLERANCE = 1e-9


def _at(value, h):
    return float(value[h] if isinstance(value,(list,tuple,np.ndarray)) else value)


def _loads(spec):
    value = spec.get('original_demand_by_priority_kW',spec.get('demand_by_priority_kW'))
    return np.asarray(value,dtype=float)


def _eligible(obj):
    values = obj.get('eligible_priorities',[0,1])
    indices = tuple(int(v[1:]) if isinstance(v,str) and v.startswith('P') else int(v) for v in values)
    if not indices or len(set(indices))!=len(indices) or any(v not in range(4) for v in indices):
        raise ValueError('Invalid eligible building priorities')
    return indices


def validate_spec(spec):
    if spec.get('evidence_class')=='historical_C_conditioned_static_planning_envelope' or spec.get('built_from_cm_trace'):
        raise ValueError('Independent original input required, not historical C/M outcomes')
    n = _validate_static(spec)
    for budget in spec['fuel_budgets'].values():
        if isinstance(budget,(list,tuple,np.ndarray)):
            raise ValueError('Fixed full-window fuel budget must be a scalar')
    demand = _loads(spec)
    if demand.shape!=(n,4,3) or not np.isfinite(demand).all() or (demand<0).any():
        raise ValueError('Finite nonnegative original priority demand T x 4 x 3 required')
    if 'original_demand_by_priority_kW' in spec and 'demand_by_priority_kW' in spec:
        if not np.array_equal(demand,np.asarray(spec['demand_by_priority_kW'],dtype=float)):
            raise ValueError('Conflicting priority demand aliases')
    for c,carrier in enumerate(CARRIERS):
        aggregate = np.asarray([_at(spec['demand_'+carrier],h) for h in range(n)])
        if np.max(np.abs(demand[:,:,c].sum(axis=1)-aggregate))>1e-8:
            raise ValueError('Aggregate demand must equal original priority sum without double counting')
    synthetic = spec.get('evidence_class') in ('synthetic_mathematical_fixture','artificial_test_fixture')
    def coefficient(value, name, scalar_only=False):
        is_array=isinstance(value,(list,tuple,np.ndarray))
        if scalar_only and is_array:
            raise ValueError(name+' coefficient must be a scalar')
        values=np.asarray(value if is_array else [value],dtype=float)
        if values.ndim!=1 or (is_array and len(values)!=n) or not np.isfinite(values).all() or (values<0).any():
            raise ValueError('Finite nonnegative horizon-matched '+name+' coefficient required')
    names = []
    for g in spec['generators']:
        names.append(g['name'])
        for key in ('fixed_electric','electric_service_limit'):
            if key in g:
                coefficient(g[key],g['name']+'.'+key)
        for h in range(n):
            if _at(g.get('min_power',0.),h)>_at(g['capacity'],h):
                raise ValueError('Generator minimum exceeds physical capacity')
        if g.get('electric_service_scope') not in (None,'building_critical_only'):
            raise ValueError('Unknown generator electric service scope')
        if g.get('electric_service_scope'):
            _eligible(g)
        active_fuel=any(_at(g['capacity'],h)>0 and (_at(g.get('fuel_slope',0),h)>0 or _at(g.get('fuel_fixed',0),h)>0) for h in range(n))
        if active_fuel and g['fuel_type'] not in spec['fuel_prices']:
            raise ValueError('Missing explicit active-source fuel price')
    boiler=spec['boiler']
    if any(_at(boiler['capacity'],h)>0 and _at(boiler.get('fuel_slope',0),h)>0 for h in range(n)):
        if boiler.get('fuel_type','gas') not in spec['fuel_prices']:
            raise ValueError('Missing explicit boiler fuel price')
    for s in spec['storages']:
        names.append(s['name'])
        for key in ('charge_electric_per_carrier','discharge_electric_per_carrier'):
            if key in s:
                coefficient(s[key],s['name']+'.'+key,scalar_only=True)
        for key in ('capacity','power','initial','minimum','decay_per_h','terminal_min','terminal_max'):
            if key in s and isinstance(s[key],(list,tuple,np.ndarray)):
                raise ValueError('Fixed storage scalar parameters required')
        scope = s.get('discharge_scope')
        if scope not in (None,'building_critical_only'):
            raise ValueError('Unknown storage discharge scope')
        if scope:
            _eligible(s)
        if not synthetic:
            if (s['name'],s['carrier']) not in (('ups','e'),('cold_store','c')):
                raise ValueError('No uninstalled generic battery or thermal store may be added')
            if scope!='building_critical_only' or _eligible(s)!=(0,1):
                raise ValueError('Hospital UPS/cold store must retain P0/P1 building-critical-only scope')
    if len(set(names))!=len(names):
        raise ValueError('Duplicate source/storage names')
    return n


def normalization(spec):
    demand = _loads(spec);duration = spec['duration_h']
    denominators = {p:{c:math.fsum(float(demand[h,ip,ic])*_at(duration,h) for h in range(len(duration)))
        for ic,c in enumerate(CARRIERS)} for ip,p in enumerate(PRIORITIES)}
    return dict(denominators_kWh=denominators,zero_demand_contribution='omitted',
        definition='For each priority: sum of carrier ENS divided by its own fixed positive full-window requested energy',
        actual_ENS_carriers_reported_separately=True,lock_tolerance_dimensionless=LEX_LOCK_TOLERANCE)


def classify_termination(result):
    x = getattr(result,'x',None)
    incumbent = x is not None and bool(np.isfinite(x).all())
    if result.status==0 and incumbent:
        return dict(status='optimal',termination_reason='optimal',incumbent=True)
    if result.status==1:
        return dict(status='time_limit' if incumbent else 'no_incumbent',termination_reason='limit_reached',incumbent=incumbent)
    if result.status==2:
        return dict(status='infeasible',termination_reason='proved_infeasible',incumbent=False)
    if result.status==3:
        return dict(status='unbounded',termination_reason='proved_unbounded',incumbent=False)
    return dict(status='solver_error' if result.status else 'no_incumbent',
                termination_reason='solver_returned_no_valid_plan',incumbent=False)


def _scope_groups(spec):
    groups = {}
    for store in spec['storages']:
        if store.get('discharge_scope')=='building_critical_only':
            groups.setdefault((store['carrier'],_eligible(store)),[]).append(store['name']+'.discharge')
    for source in spec['generators']:
        if source.get('electric_service_scope')=='building_critical_only':
            groups.setdefault(('e',_eligible(source)),[]).append(source['name'])
    return groups


def _build(spec, formulation):
    n = validate_spec(spec); demand = _loads(spec)
    keys = ['grid','boiler','boiler.on','ABS','heat_waste']+[e['name'] for e in spec['electric_chillers']]
    for g in spec['generators']:
        keys.extend((g['name'],g['name']+'.on'))
    for s in spec['storages']:
        keys.extend(s['name']+'.'+kind for kind in ('charge','discharge','stock'))
    keys.extend('unmet.'+p+'.'+c for p in PRIORITIES for c in CARRIERS)
    if len(set(keys))!=len(keys):
        raise ValueError('Planning variable names collide')
    index = {(key,h):j*n+h for j,key in enumerate(keys) for h in range(n)}
    nv = len(index); fee = np.zeros(nv);lo=np.zeros(nv);hi=np.full(nv,np.inf);integer=np.zeros(nv)
    rr=[];cc=[];dd=[];lower=[];upper=[];row_names=[]
    def V(key,h):
        return index[key,h]
    def row(terms,lb,ub,name):
        r=len(lower)
        for key,value in terms.items():
            if value:
                rr.append(r);cc.append(key);dd.append(value)
        lower.append(lb);upper.append(ub);row_names.append(name)
    fuel_terms={f:{} for f in spec['fuel_budgets']}
    for h in range(n):
        dt=_at(spec['duration_h'],h);b=spec['boiler'];ab=spec['absorption']
        hi[V('grid',h)]=_at(spec['grid_capacity'],h);fee[V('grid',h)]=dt*_at(spec['grid_price'],h)
        cap=_at(b['capacity'],h);hi[V('boiler',h)]=cap;hi[V('boiler.on',h)]=float(cap>0);integer[V('boiler.on',h)]=1
        row({V('boiler',h):1.,V('boiler.on',h):-cap},-np.inf,0.,'boiler.on')
        fuel=b.get('fuel_type','gas');slope=_at(b.get('fuel_slope',0),h)
        fee[V('boiler',h)]=dt*slope*_at(spec['fuel_prices'].get(fuel,0),h)
        if fuel in fuel_terms:
            fuel_terms[fuel][V('boiler',h)]=dt*slope
        hi[V('ABS',h)]=_at(ab['capacity'],h)
        balances={'e':{V('grid',h):1.,V('boiler',h):-_at(b.get('electric_per_h',0),h),
                       V('boiler.on',h):-_at(b.get('fixed_electric',0),h),V('ABS',h):-_at(ab.get('electric_per_c',0),h)},
                  'h':{V('boiler',h):1.,V('ABS',h):-_at(ab['heat_per_c'],h),V('heat_waste',h):-1.},
                  'c':{V('ABS',h):1.}}
        tower={V('ABS',h):_at(ab.get('tower_per_c',0),h)}
        for ec in spec['electric_chillers']:
            key=ec['name'];hi[V(key,h)]=_at(ec['capacity'],h)
            balances['e'][V(key,h)]=-_at(ec['electric_per_c'],h);balances['c'][V(key,h)]=1.
            tower[V(key,h)]=_at(ec.get('tower_per_c',0),h)
        for g in spec['generators']:
            key=g['name'];p=V(key,h);z=V(key+'.on',h);cap=_at(g['capacity'],h)
            hi[p]=cap;hi[z]=float(cap>0);integer[z]=1
            row({p:1.,z:-cap},-np.inf,0.,key+'.capacity')
            row({p:1.,z:-_at(g.get('min_power',0),h)},0.,np.inf,key+'.minimum')
            if 'electric_service_limit' in g:
                hi[p]=min(hi[p],_at(g['electric_service_limit'],h))
            balances['e'][p]=1.;balances['e'][z]=-_at(g.get('fixed_electric',0),h)
            balances['h'][p]=_at(g.get('heat_slope',0),h);balances['h'][z]=_at(g.get('heat_fixed',0),h)
            fuel=g['fuel_type'];fs=_at(g.get('fuel_slope',0),h);ff=_at(g.get('fuel_fixed',0),h)
            price=_at(spec['fuel_prices'].get(fuel,0),h);fee[p]=dt*fs*price;fee[z]=dt*ff*price
            if fuel in fuel_terms:
                fuel_terms[fuel][p]=dt*fs;fuel_terms[fuel][z]=dt*ff
        for s in spec['storages']:
            key=s['name'];ch=V(key+'.charge',h);dis=V(key+'.discharge',h);stock=V(key+'.stock',h)
            power=float(s['power']);hi[ch]=power*bool(_at(s.get('charge_allowed',1),h));hi[dis]=power*bool(_at(s.get('discharge_allowed',1),h))
            if 'discharge_limit' in s:
                hi[dis]=min(hi[dis],_at(s['discharge_limit'],h))
            lo[stock]=float(s.get('minimum',0));hi[stock]=float(s['capacity'])
            mode=s.get('loss_mode','none');rate=float(s.get('decay_per_h',0))
            a=1+dt*rate if mode=='implicit_end' else 1.
            retained=math.exp(-rate*dt) if mode=='exponential_beginning' else 1.
            terms={stock:a,ch:-dt*float(s['charge_efficiency']),dis:dt/float(s['discharge_efficiency'])}
            if h:
                terms[V(key+'.stock',h-1)]=-retained;rhs=0.
            else:
                rhs=float(s['initial'])*retained
            row(terms,rhs,rhs,key+'.inventory');row({ch:1.,dis:1.},-np.inf,power,key+'.shared_power')
            balances[s['carrier']][dis]=balances[s['carrier']].get(dis,0)+1.
            balances[s['carrier']][ch]=balances[s['carrier']].get(ch,0)-1.
            for variable,field in ((ch,'charge_electric_per_carrier'),(dis,'discharge_electric_per_carrier')):
                balances['e'][variable]=balances['e'].get(variable,0)-float(s.get(field,0))
            if h==n-1:
                lo[stock]=max(lo[stock],float(s.get('terminal_min',s.get('minimum',0))))
                hi[stock]=min(hi[stock],float(s.get('terminal_max',s['capacity'])))
        for ip,p in enumerate(PRIORITIES):
            for ic,c in enumerate(CARRIERS):
                variable=V('unmet.'+p+'.'+c,h)
                hi[variable]=float(demand[h,ip,ic]) if formulation=='tiered' else 0.
                balances[c][variable]=1.
        for carrier,terms in balances.items():
            requested=_at(spec['demand_'+carrier],h)
            row(terms,requested,requested,carrier+'.balance')
        for (carrier,priorities),suppliers in _scope_groups(spec).items():
            ic=CARRIERS.index(carrier);terms={V(key,h):1. for key in suppliers}
            for ip in priorities:
                terms[V('unmet.'+PRIORITIES[ip]+'.'+carrier,h)]=1.
            row(terms,-np.inf,float(sum(demand[h,ip,ic] for ip in priorities)),carrier+'.served_critical_scope')
        row(tower,-np.inf,_at(spec['tower_capacity'],h),'tower.capacity')
    for fuel,terms in fuel_terms.items():
        row(terms,-np.inf,float(spec['fuel_budgets'][fuel]),'budget.'+fuel)
    return dict(n=n,keys=keys,index=index,nv=nv,fee=fee,lo=lo,hi=hi,integer=integer,
        rr=rr,cc=cc,dd=dd,lower=lower,upper=upper,row_names=row_names,row=row)


def _finite_or_none(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def solve_reference(spec, formulation='strict', time_limit_s=60.):
    """Run explicit fixed priority layers, each with its disclosed budget."""
    if formulation not in ('strict','tiered'):
        raise ValueError('Unknown strict/tiered formulation')
    if isinstance(time_limit_s,bool) or not math.isfinite(time_limit_s) or time_limit_s<0:
        raise ValueError('Invalid per-layer solve budget')
    model=_build(spec,formulation);norm=normalization(spec);n=model['n']
    V=lambda key,h:model['index'][key,h]
    objectives=[]
    if formulation=='tiered':
        for p in PRIORITIES:
            objective=np.zeros(model['nv'])
            for c in CARRIERS:
                denominator=norm['denominators_kWh'][p][c]
                if denominator>0:
                    for h in range(n):
                        objective[V('unmet.'+p+'.'+c,h)]=_at(spec['duration_h'],h)/denominator
            objectives.append((p,objective))
    objectives.append(('fee',model['fee']))
    layers=[];locks=[];last_plan=None;last_audit=None;termination=None;failure=None
    for name,objective in objectives:
        if name!='fee' and not np.any(objective):
            layers.append(dict(name=name,status='not_applicable_zero_demand',incumbent=last_plan is not None,objective_value=0.,optimality_proven=True))
            continue
        matrix=coo_matrix((model['dd'],(model['rr'],model['cc'])),shape=(len(model['lower']),model['nv'])).tocsc()
        result=milp(c=objective,integrality=model['integer'],bounds=Bounds(model['lo'],model['hi']),
            constraints=LinearConstraint(matrix,np.asarray(model['lower']),np.asarray(model['upper'])),
            options={'time_limit':float(time_limit_s),'mip_rel_gap':1e-9})
        termination=classify_termination(result)
        entry=dict(name=name,**termination,raw_status=int(result.status),solver_message=str(result.message),
            objective_value=_finite_or_none(getattr(result,'fun',None)),
            mip_gap=_finite_or_none(getattr(result,'mip_gap',None)),mip_dual_bound=_finite_or_none(getattr(result,'mip_dual_bound',None)),
            mip_node_count=_finite_or_none(getattr(result,'mip_node_count',None)),
            time_limit_seconds=float(time_limit_s),optimality_proven=termination['status']=='optimal')
        layers.append(entry)
        if not termination['incumbent']:
            if termination['status']=='infeasible' and locks:
                failure='lexicographic_lock_infeasible'
            elif termination['status'] not in ('no_incumbent','time_limit'):
                failure=termination['status']
            break
        candidate={key:[float(result.x[V(key,h)]) for h in range(n)] for key in model['keys']}
        audit=audit_plan(spec,candidate,formulation=formulation,objective_locks=locks)
        entry['independent_audit_valid']=audit['valid']
        if audit['valid']:
            independently_recomputed=audit['independently_recomputed_fee'] if name=='fee' else audit['normalized_service_objectives'][name]
            reported=entry['objective_value']
            objective_tolerance=1e-7 if name=='fee' else LEX_LOCK_TOLERANCE
            objective_matches=reported is not None and abs(reported-independently_recomputed)<=objective_tolerance
            entry.update(independently_recomputed_objective=independently_recomputed,
                independent_objective_matches_solver=objective_matches,objective_metadata_tolerance=objective_tolerance)
            if not objective_matches:
                audit['valid']=False
                audit['violations'].append(dict(check='solver_objective_metadata',violation=abs(reported-independently_recomputed) if reported is not None else None))
                entry['independent_audit_valid']=False
        if not audit['valid']:
            last_plan=candidate;last_audit=audit;failure='invalid_incumbent';break
        last_plan,last_audit=candidate,audit
        if name!='fee':
            value=audit['normalized_service_objectives'][name]
            lock=dict(priority=name,value=value,upper=value+LEX_LOCK_TOLERANCE,optimality_proven=entry['optimality_proven'])
            locks.append(lock)
            model['row']({i:float(v) for i,v in enumerate(objective) if v},-np.inf,lock['upper'],'lock.'+name)
    verified=last_plan is not None and last_audit is not None and last_audit['valid']
    solved_all=len(layers)==len(objectives) and layers[-1]['name']=='fee' and bool(layers[-1].get('independent_audit_valid'))
    optimal=solved_all and all(layer.get('optimality_proven',False) for layer in layers)
    if failure:
        status=failure;reason='independent_or_lexicographic_gate_failure' if failure in ('invalid_incumbent','lexicographic_lock_infeasible') else termination['termination_reason']
    elif optimal:
        status='optimal';reason='optimal'
    elif verified:
        status='time_limit';reason='verified_incumbent_without_complete_optimality_proof'
    else:
        status=(termination or {}).get('status','no_incumbent');reason=(termination or {}).get('termination_reason','no_valid_plan')
    return dict(schema=SCHEMA,formulation=formulation,status=status,termination_reason=reason,
        incumbent=last_plan is not None,verified_incumbent=bool(verified),adoptable=bool(verified and not failure),
        plan=last_plan,audit=last_audit,objective_fee=last_audit['independently_recomputed_fee'] if verified else None,
        layers=layers,objective_locks=locks,normalization=norm,lexicographic_layers_completed=solved_all,
        lexicographic_optimality_proven=optimal,variable_count=model['nv'],binary_count=int(model['integer'].sum()),
        constraint_count=len(model['lower']),time_limit_per_layer_seconds=float(time_limit_s),
        information_permission=spec['information_permission'],actual_replayed=False,
        full_continuous_plan_replayed=False,planned_storage_ports_executed=False,
        registered_simplifications=copy.deepcopy(spec.get('registered_simplifications',[])),
        plan_fee_excludes_initial_inventory_preparation=True,
        dynamic_startup_ramp_minimum_on_off_qualified=False,
        fuel_budget_not_invented_for_absent_carrier=True)


def audit_plan(spec, plan, formulation='strict', tolerance=1e-6, objective_locks=None):
    """Independent named-flow arithmetic, without solver rows/index/matrix."""
    n=validate_spec(spec);demand=_loads(spec)
    if formulation not in ('strict','tiered'):
        raise ValueError('Unknown strict/tiered formulation')
    if plan is None:
        return dict(valid=False,reason='no_plan',maximum_violation=None)
    violations=[];observed=[];residuals=[];fuel_used={};fee=0.;standing={}
    previous={s['name']:float(s['initial']) for s in spec['storages']}
    service={p:{c:{key:[] for key in ('requested_kWh','served_kWh','ENS_kWh')} for c in CARRIERS} for p in PRIORITIES}
    def check(name,value,allowed=None):
        value=float(value)
        observed.append(value)
        if not math.isfinite(value) or value>(tolerance if allowed is None else allowed):
            violations.append(dict(check=name,violation=value))
    try:
        for values in plan.values():
            if len(values)!=n or not np.isfinite(values).all():
                raise ValueError('Malformed plan array')
            for value in values:
                check('nonnegative',-float(value))
        for h in range(n):
            P=lambda key:float(plan[key][h]);dt=_at(spec['duration_h'],h)
            delivered={c:0. for c in CARRIERS}
            for ip,p in enumerate(PRIORITIES):
                for ic,c in enumerate(CARRIERS):
                    requested=float(demand[h,ip,ic]);unmet=P('unmet.'+p+'.'+c)
                    check(p+'.'+c+'.unmet_upper',unmet-requested)
                    if formulation=='strict':
                        check(p+'.'+c+'.strict_unmet',abs(unmet))
                    delivered[c]+=requested-unmet
                    for key,value in (('requested_kWh',requested*dt),('served_kWh',(requested-unmet)*dt),('ENS_kWh',unmet*dt)):
                        service[p][c][key].append(value)
            b=spec['boiler'];ab=spec['absorption'];e=P('grid');heat=P('boiler')-P('heat_waste');cold=P('ABS')
            check('grid.capacity',P('grid')-_at(spec['grid_capacity'],h));fee+=dt*P('grid')*_at(spec['grid_price'],h)
            z=P('boiler.on');check('boiler.integral',abs(z-round(z)));check('boiler.binary_upper',z-1.)
            check('boiler.availability',z-float(_at(b['capacity'],h)>0.))
            check('boiler.capacity',P('boiler')-_at(b['capacity'],h)*z)
            e-=P('boiler')*_at(b.get('electric_per_h',0),h)+z*_at(b.get('fixed_electric',0),h)
            e-=P('ABS')*_at(ab.get('electric_per_c',0),h);heat-=P('ABS')*_at(ab['heat_per_c'],h)
            tower=P('ABS')*_at(ab.get('tower_per_c',0),h);check('ABS.capacity',P('ABS')-_at(ab['capacity'],h))
            fuel=dt*P('boiler')*_at(b.get('fuel_slope',0),h);kind=b.get('fuel_type','gas')
            fuel_used[kind]=fuel_used.get(kind,0)+fuel;fee+=fuel*_at(spec['fuel_prices'].get(kind,0),h)
            for ec in spec['electric_chillers']:
                q=P(ec['name']);check(ec['name']+'.capacity',q-_at(ec['capacity'],h))
                cold+=q;e-=q*_at(ec['electric_per_c'],h);tower+=q*_at(ec.get('tower_per_c',0),h)
            for g in spec['generators']:
                p=P(g['name']);z=P(g['name']+'.on')
                check(g['name']+'.integral',abs(z-round(z)));check(g['name']+'.binary_upper',z-1.)
                check(g['name']+'.availability',z-float(_at(g['capacity'],h)>0.))
                check(g['name']+'.capacity',p-_at(g['capacity'],h)*z);check(g['name']+'.minimum',_at(g.get('min_power',0),h)*z-p)
                if 'electric_service_limit' in g:
                    check(g['name']+'.service_limit',p-_at(g['electric_service_limit'],h))
                e+=p-z*_at(g.get('fixed_electric',0),h)
                heat+=p*_at(g.get('heat_slope',0),h)+z*_at(g.get('heat_fixed',0),h)
                fuel=dt*(p*_at(g.get('fuel_slope',0),h)+z*_at(g.get('fuel_fixed',0),h));kind=g['fuel_type']
                fuel_used[kind]=fuel_used.get(kind,0)+fuel;fee+=fuel*_at(spec['fuel_prices'].get(kind,0),h)
            for s in spec['storages']:
                key=s['name'];ch=P(key+'.charge');dis=P(key+'.discharge');stock=P(key+'.stock')
                mode=s.get('loss_mode','none');rate=float(s.get('decay_per_h',0))
                loss=dt*rate*stock if mode=='implicit_end' else previous[key]*(1-math.exp(-dt*rate)) if mode=='exponential_beginning' else 0.
                expected=previous[key]-loss+dt*(float(s['charge_efficiency'])*ch-dis/float(s['discharge_efficiency']))
                check(key+'.inventory',abs(stock-expected));check(key+'.capacity',stock-s['capacity']);check(key+'.minimum',s.get('minimum',0)-stock)
                check(key+'.shared_power',ch+dis-s['power'])
                check(key+'.charge_mode',ch-s['power']*bool(_at(s.get('charge_allowed',1),h)))
                check(key+'.discharge_mode',dis-s['power']*bool(_at(s.get('discharge_allowed',1),h)))
                if 'discharge_limit' in s:
                    check(key+'.discharge_limit',dis-_at(s['discharge_limit'],h))
                if h==n-1:
                    check(key+'.terminal_min',s.get('terminal_min',s.get('minimum',0))-stock)
                    check(key+'.terminal_max',stock-s.get('terminal_max',s['capacity']))
                previous[key]=stock;standing[key]=standing.get(key,0)+loss
                if s['carrier']=='e':
                    e+=dis-ch
                elif s['carrier']=='h':
                    heat+=dis-ch
                else:
                    cold+=dis-ch
                e-=ch*s.get('charge_electric_per_carrier',0)+dis*s.get('discharge_electric_per_carrier',0)
            # Rebuild eligible supplier totals from named ports, not MILP rows.
            groups={}
            for s in spec['storages']:
                if s.get('discharge_scope')=='building_critical_only':
                    key=(s['carrier'],_eligible(s));groups[key]=groups.get(key,0)+P(s['name']+'.discharge')
            for g in spec['generators']:
                if g.get('electric_service_scope')=='building_critical_only':
                    key=('e',_eligible(g));groups[key]=groups.get(key,0)+P(g['name'])
            for (carrier,eligible),value in groups.items():
                ic=CARRIERS.index(carrier)
                served=sum(float(demand[h,ip,ic])-P('unmet.'+PRIORITIES[ip]+'.'+carrier) for ip in eligible)
                check(carrier+'.served_critical_storage_source_scope',value-served)
            row=dict(period=h,electric=e-delivered['e'],heat=heat-delivered['h'],cooling=cold-delivered['c'],tower_violation=max(0.,tower-_at(spec['tower_capacity'],h)))
            for key in ('electric','heat','cooling'):
                check(key+'.balance',abs(row[key]))
            check('tower.capacity',row['tower_violation']);residuals.append(row)
        for fuel,budget in spec['fuel_budgets'].items():
            check('fuel_budget.'+fuel,fuel_used.get(fuel,0)-budget)
        service={p:{c:{key:math.fsum(values) for key,values in metrics.items()} for c,metrics in carriers.items()} for p,carriers in service.items()}
        denominators=normalization(spec)['denominators_kWh']
        objectives={p:math.fsum(service[p][c]['ENS_kWh']/denominators[p][c] for c in CARRIERS if denominators[p][c]>0.) for p in PRIORITIES}
        for lock in objective_locks or []:
            check('priority_lock.'+lock['priority'],objectives[lock['priority']]-lock['upper'],LEX_LOCK_TOLERANCE)
    except (KeyError,ValueError,TypeError,OverflowError) as error:
        return dict(valid=False,reason='invalid_or_incomplete_plan',error=str(error),maximum_violation=None)
    return dict(valid=not violations,violations=violations,period_residuals=residuals,service=service,
        normalized_service_objectives=objectives,fuel_used=fuel_used,independently_recomputed_fee=fee,
        storage_standing_loss=standing,terminal_stocks=previous,tolerance=tolerance,
        objective_lock_audit_tolerance_dimensionless=LEX_LOCK_TOLERANCE,
        maximum_violation=max([max(0.,v) for v in observed],default=0.),
        maximum_balance_residual=max([abs(row[key]) for row in residuals for key in ('electric','heat','cooling')],default=0.),
        fuel_budget_scope={fuel:'registered' if fuel in spec['fuel_budgets'] else 'not_registered_no_budget_imposed' for fuel in fuel_used},
        independent_of_solver_matrix=True,actual_execution_qualified=False)


def select_common_formulation(normal_strict, grid_strict, allow_unknown_tiered=False):
    results=(normal_strict,grid_strict)
    if any(r.get('formulation')!='strict' for r in results):
        raise ValueError('The common gate requires two strict planning records')
    verified=all(r.get('verified_incumbent') and r.get('adoptable') for r in results)
    proved=any(r.get('status')=='infeasible' and r.get('termination_reason')=='proved_infeasible' for r in results)
    unresolved_error=any(r.get('status') in ('invalid_incumbent','lexicographic_lock_infeasible','solver_error','unbounded') for r in results)
    if verified:
        selected='strict';reason='both_strict_have_independently_verified_incumbents'
    elif unresolved_error:
        selected=None;reason='unresolved_planning_gate_error'
    elif proved:
        selected='tiered';reason='at_least_one_strict_problem_proved_infeasible'
    elif allow_unknown_tiered:
        selected='tiered';reason='registered_no_incumbent_after_budget_path_strict_feasibility_unknown'
    else:
        selected=None;reason='no_verified_incumbent_strict_feasibility_unknown'
    return dict(selected_formulation=selected,selection_reason=reason,strict_infeasibility_proven=proved,
        both_strict_verified_incumbents=verified,same_formulation_required_for_normal_and_grid=True,
        performance_comparison_used_for_selection=False)


def stabilize_symmetric_generators(spec, plan, names, initial_targets, formulation='strict'):
    """Relabel only exact full-window symmetric units; raw plan is untouched."""
    objects={g['name']:g for g in spec['generators']}
    if len(set(names))!=len(names) or len(initial_targets)!=len(names) or any(name not in objects for name in names):
        raise ValueError('Invalid symmetric generator/initial target mapping')
    if not all(isinstance(x,(int,float)) and math.isfinite(x) and x>=0 for x in initial_targets):
        raise ValueError('Finite nonnegative original initial targets required')
    signatures=[json.dumps({key:value for key,value in objects[name].items() if key!='name'},sort_keys=True) for name in names]
    if len(set(signatures))!=1:
        return dict(plan=copy.deepcopy(plan),verified_equivalent=False,reason='not_identical_over_complete_window',permutations=[])
    before=audit_plan(spec,plan,formulation=formulation)
    if not before['valid']:
        raise ValueError('Cannot relabel an invalid raw plan')
    canonical=copy.deepcopy(plan);previous=list(initial_targets);permutations=[]
    for h in range(len(spec['duration_h'])):
        targets=sorted(range(len(names)),key=lambda i:(-previous[i],i))
        sources=sorted(range(len(names)),key=lambda i:(-float(plan[names[i]][h]),i))
        assignments=[]
        for target,source in zip(targets,sources):
            canonical[names[target]][h]=plan[names[source]][h]
            canonical[names[target]+'.on'][h]=plan[names[source]+'.on'][h]
            if target!=source:
                assignments.append(dict(source=names[source],target=names[target]))
        if assignments:
            permutations.append(dict(period=h,assignments=assignments))
        previous=[float(canonical[name][h]) for name in names]
    after=audit_plan(spec,canonical,formulation=formulation)
    service_equal=all(after['service'][p][c]==before['service'][p][c] for p in PRIORITIES for c in CARRIERS)
    if not after['valid'] or not service_equal or abs(after['independently_recomputed_fee']-before['independently_recomputed_fee'])>1e-7:
        raise ValueError('Symmetry relabeling did not preserve independent feasibility, service and fee')
    return dict(plan=canonical,verified_equivalent=True,reason='identical_full_window_parameters_static_label_permutation',
        permutations=permutations,independent_audit=after,original_plan_unchanged=True,
        proves_dynamic_minimum_up_down_or_startup_feasibility=False)
