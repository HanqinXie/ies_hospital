"""Shared linear auxiliary-service dependencies, without duplicate electricity.

Electric request already includes allocated building auxiliaries. Actual
electric delivery is decomposed into direct use and fan/CHW/heat-pump ports.
Air-served thermal delivery needs its fan and distribution pump. Identified
P3 domestic hot water is independent of the air fan, while retaining the heat
pump and source constraints. This aggregate bound is not a DHW delivery meter
or a ventilation compliance test.
"""
from __future__ import annotations
import math
import numpy as np

KINDS=('fan','chw_pump','heat_pump')

def validate_auxiliary_kW_bounds(solution,indices,required,upper_fraction):
    """Retain the backend's original 1e-7 kW bound check after mapping.

    Fraction-space acceptance alone would allow A times the original kW
    error. This check never clips a witness or changes a physical bound.
    """
    from hospital_ies.execution import SolverExecutionError
    values=np.asarray(solution,float)[indices]*required
    high=np.asarray(upper_fraction,float)[indices]*required
    violation=np.maximum(0.,np.maximum(-values,values-high))
    maximum=float(np.max(violation))
    if not np.isfinite(values).all() or maximum>1e-7:
        raise SolverExecutionError(f'Original auxiliary kW bounds violated after fraction mapping: {maximum}')
    return dict(max_original_auxiliary_kW_bound_violation=maximum,tolerance_kW=1e-7,
                original_kW_bounds_retained=True)

def dependency_variables(allocate, prefix='current'):
    return np.asarray([[allocate(f'{prefix}.P{g}.{kind}.electric_fraction','fraction')
                        for kind in KINDS] for g in range(4)],dtype=int)

def dependency_requirements(row,loads):
    if getattr(row,'sci_demand_boundary_contract',None) is not None:
        from tools.model_review_v28.demand import dependency_request
        return np.asarray(dependency_request(row,loads)['auxiliary_required_kW'],float)
    loads=np.asarray(loads,float)
    result=np.zeros((4,3))
    for g in range(4):
        ratios=[]
        for k,carrier in enumerate(('e','h','c')):
            reference=float(getattr(row,f'P{g}_reference_{carrier}_kW',loads[g,k]))
            ratios.append(loads[g,k]/reference if reference>1e-9 else 0.)
        factors=(max(ratios),ratios[2],ratios[1])
        for j,kind in enumerate(KINDS):
            value=float(getattr(row,f'P{g}_{kind}_kW',0.))
            if not math.isfinite(value) or value<0.:
                raise ValueError('Invalid source-derived auxiliary request')
            result[g,j]=value*factors[j]
    return result

def fan_independent_heat_bound(row,loads):
    """Maximum identifiable P3 non-air heat, never actual DHW delivery.

    Connected P3 heat may retain at most the identified original DHW amount.
    Aggregate contacts do not identify which subservice was disconnected, so
    this is a permissive delivery bound, not a new intra-P3 priority rule.
    Actual M21 rows require their original source trace; legacy mathematical
    fixtures without the decomposition explicitly use a zero allowance.
    """
    loads=np.asarray(loads,float)
    if loads.shape!=(4,3) or not np.isfinite(loads).all() or np.any(loads<0.):
        raise ValueError('Invalid heat decomposition load request')
    target_trace=getattr(row,'v28_thermal_service_trace',None)
    if target_trace is not None:
        from tools.model_review_v28.demand import DEMAND_CONTRACT_ID,thermal_target_context
        target_context=thermal_target_context(row);trace=target_context['source_trace']
        q=getattr(row,'sci_direct_aux_request',{})
        if q.get('contract_id')!=DEMAND_CONTRACT_ID:raise ValueError('V28 DHW target lacks bound current request')
        target=target_context['target_kW'];legacy=target_context['legacy_delivered_kW']
        full_P3=float(q.get('actual_current_P3_heat_request_kW',np.asarray(q['aggregate_request_kW'])[3,1]))
        if any(not math.isfinite(v) or v<0. for v in (target,legacy,full_P3)) or target>full_P3+1e-7:
            raise ValueError('Invalid V28 ordinary DHW target/P3 request decomposition')
        return dict(maximum_fan_independent_heat_kW=[0.,0.,0.,min(float(loads[3,1]),target)],
            original_P3_identified_heat_kW=target,source_DHW_kW=target,
            legacy_DHW_delivered_kW=legacy,original_P3_heat_request_kW=float(row.P3_reference_h_kW),
            bound_current_P3_heat_request_kW=full_P3,source_trace=dict(trace),
            decomposition_status='source_bound_ordinary_DHW_target_v28',represents_actual_DHW_delivery=False,
            semantics='Bound requested useful heat of ordinary30WaterUse services, not source heater input or actual delivery. Fan independent; water pump/heat source constraints retained. Local high-temperature services excluded from central request.')
    required=bool(getattr(row,'sci_heat_decomposition_required',False))
    present=hasattr(row,'Q_h_DHW_water_use_kW')
    if required and not present:raise ValueError('Actual M21 row lacks source DHW decomposition')
    dhw=float(getattr(row,'Q_h_DHW_water_use_kW',0.))
    original=float(getattr(row,'P3_reference_h_kW',loads[3,1]))
    if any(not math.isfinite(v) or v<0. for v in (dhw,original)):
        raise ValueError('Invalid nonnegative source DHW/heat decomposition')
    trace=getattr(row,'sci_heat_decomposition_source',None)
    if required:
        if (not isinstance(trace,dict) or trace.get('path')!='inputs/loads/hospital_ies_demand_8760.csv'
            or trace.get('DHW_column')!='Q_h_DHW_water_use_kW'
            or not isinstance(trace.get('sha256'),str) or len(trace['sha256'])!=64
            or any(c not in '0123456789abcdef' for c in trace['sha256'])
            or isinstance(trace.get('hour_of_year'),bool) or not isinstance(trace.get('hour_of_year'),int)
            or not 1<=trace['hour_of_year']<=8760 or 'original_P3_h_kW' not in trace
            or trace.get('source_DHW_kW')!=dhw):
            raise ValueError('Actual M21 row lacks valid source DHW decomposition trace')
        original=float(trace['original_P3_h_kW'])
        if not math.isfinite(original) or original<0.:raise ValueError('Invalid source P3 heat decomposition')
    identified=min(original,dhw)
    allowance=[0.,0.,0.,min(float(loads[3,1]),identified)]
    return dict(maximum_fan_independent_heat_kW=allowance,
        original_P3_identified_heat_kW=identified,source_DHW_kW=dhw,
        original_P3_heat_request_kW=original,
        source_trace=dict(trace) if isinstance(trace,dict) else None,
        decomposition_status=('source_bound_original_DHW' if required else
            'explicit_unbound_decomposition_fixture' if present else 'fixture_without_decomposition_assumed_zero'),
        represents_actual_DHW_delivery=False,
        semantics='Maximum identifiable non-air heat inside original P3 residual, clipped by connected heat; all heat still needs its distribution pump and source. DHW service and aggregate contact composition are not independently identified.')

def add_dependency_constraints(row,loads,availability,indices,unmet_indices,add,upper,prefix='current'):
    loads=np.asarray(loads,float);component_request=None
    if getattr(row,'sci_demand_boundary_contract',None) is not None:
        from tools.model_review_v28.demand import dependency_request
        component_request=dependency_request(row,loads)
        required=np.asarray(component_request['auxiliary_required_kW'],float)
    else:required=dependency_requirements(row,loads)
    independent=fan_independent_heat_bound(row,loads)['maximum_fan_independent_heat_kW']
    unmet=np.asarray(unmet_indices,int).reshape(4,3)
    for g in range(4):
        fan=float(availability.get(f'AUX_FAN_P{g}',1.))
        available=(fan,float(availability.get('AUX_CHW_PUMP',1.)),
                   float(availability.get('AUX_HEAT_PUMP',1.)))
        for j in range(3):
            if not 0.<=available[j]<=1.:raise ValueError('Invalid auxiliary availability')
            # x_aux = required * fraction is a diagonal variable change.
            # Tiny heating-pump kW must not create D/required coefficients
            # that amplify the solver's scaled feasibility error in kW.
            upper[indices[g,j]]=available[j] if required[g,j]>0. else 0.
        # 0 <= delivered_e - sum(aux_e) <= direct_e_request.
        if component_request is not None:
            direct=component_request['direct_cap_kW'][g]
        else:direct=max(0.,loads[g,0]-required[g].sum())
        coefficients={int(unmet[g,0]):1.,
            **{int(indices[g,j]):float(required[g,j]) for j in range(3)}}
        add(coefficients,loads[g,0]-direct,loads[g,0],f'{prefix}.P{g}.electric_components')
        for carrier,ports in ((1,(0,2)),(2,(0,1))):
            for j in ports:
                if required[g,j]>1e-9 and loads[g,carrier]>1e-9:
                    air_request=(loads[g,carrier]-independent[g] if carrier==1 and j==0 else loads[g,carrier])
                    add({int(unmet[g,carrier]):1.,int(indices[g,j]):air_request},
                        air_request,np.inf,f'{prefix}.P{g}.{("h","c")[carrier-1]}_needs_{KINDS[j]}')
    return required
