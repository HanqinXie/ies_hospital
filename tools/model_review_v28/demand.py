"""Current-only building demand with fixed source direct-electric references.

The source's fan/CHW meters are moved exactly once. The separate heat-loop
pump remains an addition. Derived auxiliary electricity never drives itself.
No environment, future clock, actuator availability or yearly array is read.
"""
from __future__ import annotations
import copy
from types import SimpleNamespace
import numpy as np

DEMAND_CONTRACT_ID='hospital_ies.fixed_direct_aux.v28.v1'
KINDS=('fan','chw_pump','heat_pump')

def enabled(row):
    value=getattr(row,'sci_demand_boundary_contract',None)
    if value is not None and value!=DEMAND_CONTRACT_ID:
        raise ValueError('Unknown explicit direct/auxiliary demand contract')
    return value==DEMAND_CONTRACT_ID

def _matrix(value,name):
    result=np.asarray(value,float)
    if result.shape!=(4,3) or not np.isfinite(result).all() or (result<0.).any():
        raise ValueError(name+' must be finite nonnegative4x3')
    return result.copy()

def _field(row,name):
    if not hasattr(row,name):raise ValueError('Missing source demand field: '+name)
    value=getattr(row,name)
    if isinstance(value,(bool,np.bool_)):
        raise ValueError('Invalid source demand field: '+name)
    try:value=float(value)
    except (TypeError,ValueError,OverflowError) as exc:raise ValueError('Invalid source field: '+name) from exc
    if not np.isfinite(value) or value<0.:raise ValueError('Negative or nonfinite source field: '+name)
    return value

def source_reference(row):
    if not enabled(row):raise ValueError('Explicit V28 source demand contract required')
    reference=np.array([[_field(row,f'P{g}_reference_{k}_kW') for k in 'ehc'] for g in range(4)])
    aux=np.array([[_field(row,f'P{g}_{kind}_kW') for kind in KINDS] for g in range(4)])
    direct=np.array([_field(row,f'P{g}_base_e_kW') for g in range(4)])
    direct[3]-=_field(row,'source_fans_total_kW')+_field(row,'source_chw_pump_kW')
    if (direct<0.).any():raise ValueError('Negative source non-auxiliary direct electric baseline')
    if not np.allclose(reference[:,0],direct+aux.sum(axis=1),atol=1e-8,rtol=0.):
        raise ValueError('Source direct/auxiliary reference does not close original electric boundary')
    return dict(direct_reference_kW=direct,auxiliary_reference_kW=aux,source_load_reference_kW=reference)

def _ratio(current,reference):
    current=np.asarray(current,float);reference=np.asarray(reference,float)
    if np.any((reference==0.) & (current>0.)):
        raise ValueError('Positive current service has zero source reference; no proven auxiliary fallback')
    return np.divide(current,reference,out=np.zeros_like(current),where=reference>0.)

def _auxiliary(reference,thermal,direct_increment):
    # Preserve the original pre-auxiliary-increment electric driver. Using the
    # final aggregate request here would feed additional fan/pump power back
    # into itself and would change the clinical-shock response twice.
    source=reference['source_load_reference_kW']
    electric_driver=source[:,0]+direct_increment
    if (electric_driver<0.).any():raise ValueError('Negative direct-service electric driver')
    ratios=np.column_stack([_ratio(electric_driver,source[:,0]),
                            _ratio(thermal[:,0],source[:,1]),_ratio(thermal[:,1],source[:,2])])
    factors=np.column_stack([ratios.max(axis=1),ratios[:,2],ratios[:,1]])
    required=reference['auxiliary_reference_kW']*factors
    return required,ratios,factors

def construct_request(row,raw_loads,*,direct_increment_kW=None):
    raw=_matrix(raw_loads,'Raw current service request');source=source_reference(row)
    increment=raw[:,0]-source['source_load_reference_kW'][:,0]
    if direct_increment_kW is not None:
        explicit=np.asarray(direct_increment_kW,float)
        if explicit.shape!=(4,) or not np.isfinite(explicit).all() or not np.allclose(explicit,increment,atol=1e-9,rtol=0.):
            raise ValueError('Explicit direct shock differs from unaugmented source-electric increment')
        increment=explicit.copy()
    direct=source['direct_reference_kW']+increment
    if (direct<0.).any():raise ValueError('Negative non-auxiliary direct electric request')
    required,ratios,factors=_auxiliary(source,raw[:,1:],increment)
    aggregate=raw.copy();aggregate[:,0]=direct+required.sum(axis=1)
    return dict(contract_id=DEMAND_CONTRACT_ID,
        direct_reference_kW=source['direct_reference_kW'].tolist(),direct_increment_kW=increment.tolist(),
        direct_request_kW=direct.tolist(),auxiliary_reference_kW=source['auxiliary_reference_kW'].tolist(),
        auxiliary_request_kW=required.tolist(),source_load_reference_kW=source['source_load_reference_kW'].tolist(),
        raw_current_request_kW=raw.tolist(),aggregate_request_kW=aggregate.tolist(),
        auxiliary_driving_ratios=ratios.tolist(),auxiliary_factors=factors.tolist(),
        current_fan_components_by_priority_kW={},
        electric_driver_semantics='Original source aggregate electric reference plus explicit direct increment, before additional auxiliary power',
        represents_individually_metered_clinical_demand=False)

def bind_request(row,raw_loads,*,direct_increment_kW=None):
    request=construct_request(row,raw_loads,direct_increment_kW=direct_increment_kW)
    data=copy.deepcopy(row._asdict() if hasattr(row,'_asdict') else vars(row))
    data['sci_direct_aux_request']=request
    return SimpleNamespace(**data),np.asarray(request['aggregate_request_kW'],float)

def counterfactual_request(row,scaled_loads):
    """Rebuild an existing planner copy from current legal service information.

    Its existing electric-demand multiplier applies to direct service, then
    auxiliary power is recomputed from its thermal requests exactly once. It
    grants no capability and is never reported as actual supply or demand.
    """
    scaled=_matrix(scaled_loads,'Named planner-copy service')
    q=getattr(row,'sci_direct_aux_request',None)
    if not isinstance(q,dict) or q.get('contract_id')!=DEMAND_CONTRACT_ID:
        raise ValueError('Planner copy lacks current V28 demand reference')
    full=np.asarray(q['aggregate_request_kW'],float)
    if np.array_equal(scaled,full):return row,scaled
    ratio=_ratio(scaled[:,0],full[:,0])
    direct=np.asarray(q['direct_request_kW'])*ratio
    source=source_reference(row)
    raw=scaled.copy();raw[:,0]=source['source_load_reference_kW'][:,0]+direct-source['direct_reference_kW']
    bound,request=bind_request(row,raw,direct_increment_kW=direct-source['direct_reference_kW'])
    bound.sci_direct_aux_request['request_scope']='named_planner_counterfactual'
    bound.sci_direct_aux_request['actual_current_P3_heat_request_kW']=float(q.get('actual_current_P3_heat_request_kW',full[3,1]))
    return bound,request

def dependency_request(row,loads):
    """Constraint scope is a connected subset; the original request is fixed."""
    loads=_matrix(loads,'Connected current request')
    q=getattr(row,'sci_direct_aux_request',None)
    if not isinstance(q,dict) or q.get('contract_id')!=DEMAND_CONTRACT_ID:
        raise ValueError('V28 service row lacks its bound direct/auxiliary request')
    full=_matrix(q['aggregate_request_kW'],'Bound original current request')
    if np.any(loads>full+1e-7):
        raise ValueError('Larger counterfactual requires its own bound demand request')
    source=source_reference(row)
    required,_,_=_auxiliary(source,loads[:,1:],np.asarray(q['direct_increment_kW'],float))
    direct=np.asarray(q['direct_request_kW'],float)
    return dict(auxiliary_required_kW=required.tolist(),direct_cap_kW=np.minimum(direct,loads[:,0]).tolist(),
        original_request=copy.deepcopy(q),connected_loads_kW=loads.tolist(),
        scope='Original request retained; connected thermal service may reduce executable auxiliaries, never create new direct demand')

def served_components(row,loads,served,auxiliary):
    """Signed native service ledger; actual power is never assumed from request."""
    detail=dependency_request(row,loads);q=detail['original_request']
    delivered=np.asarray(served,float);aux=np.asarray(auxiliary,float)
    if delivered.shape!=(4,3) or aux.shape!=(4,3) or not np.isfinite(delivered).all() or not np.isfinite(aux).all():
        raise ValueError('Invalid native direct/auxiliary service arrays')
    direct=delivered[:,0]-aux.sum(axis=1)
    if np.any(direct < -1e-7) or np.any(direct>np.asarray(detail['direct_cap_kW'])+1e-7):
        raise ValueError('Native direct service violates original1e-7kW electric component bound')
    full=np.asarray(q['aggregate_request_kW'],float)
    return dict(original_request=q,connected_loads_kW=detail['connected_loads_kW'],
        executable_auxiliary_required_kW=detail['auxiliary_required_kW'],
        direct_delivered_kW=direct.tolist(),auxiliary_delivered_kW=aux.tolist(),
        aggregate_delivered_kW=delivered[:,0].tolist(),
        direct_unserved_kW=(np.asarray(q['direct_request_kW'])-direct).tolist(),
        auxiliary_unserved_kW=(np.asarray(q['auxiliary_request_kW'])-aux).tolist(),
        aggregate_unserved_kW=(full[:,0]-delivered[:,0]).tolist(),
        thermal_unserved_kW=(full[:,1:]-delivered[:,1:]).tolist(),
        served_electric_decomposition_residual_kW=(delivered[:,0]-direct-aux.sum(axis=1)).tolist(),
        raw_signed_values_retained=True,request_not_redefined_by_fault_or_service_cut=True)
