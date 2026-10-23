"""Shared linear auxiliary-service dependencies, without duplicate electricity.

Electric request already includes allocated building auxiliaries. Actual
electric delivery is decomposed into direct use and fan/CHW/heat-pump ports.
Each thermal delivery needs both its fan and its distribution pump. This is
an aggregate proportional-flow assumption, not a ventilation compliance test.
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

def add_dependency_constraints(row,loads,availability,indices,unmet_indices,add,upper,prefix='current'):
    loads=np.asarray(loads,float);required=dependency_requirements(row,loads)
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
        direct=max(0.,loads[g,0]-required[g].sum())
        coefficients={int(unmet[g,0]):1.,
            **{int(indices[g,j]):float(required[g,j]) for j in range(3)}}
        add(coefficients,loads[g,0]-direct,loads[g,0],f'{prefix}.P{g}.electric_components')
        for carrier,ports in ((1,(0,2)),(2,(0,1))):
            for j in ports:
                if required[g,j]>1e-9 and loads[g,carrier]>1e-9:
                    add({int(unmet[g,carrier]):1.,int(indices[g,j]):loads[g,carrier]},
                        loads[g,carrier],np.inf,f'{prefix}.P{g}.{("h","c")[carrier-1]}_needs_{KINDS[j]}')
    return required
