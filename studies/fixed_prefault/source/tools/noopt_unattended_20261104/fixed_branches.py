"""Explicit aggregate fixed-branch proxy, not a hydraulic or dispatch solver."""
from copy import deepcopy
import numpy as np
from hospital_ies.model import RT_TO_KW

SCHEMA = 'hospital_ies.frozen_prefault_branches.v2'


def shares(values):
    values = np.asarray(values, float)
    if not np.isfinite(values).all() or np.any(values < 0.):
        raise ValueError('Finite nonnegative pre-fault branch delivery required')
    total = float(values.sum())
    return values / total if total else np.zeros_like(values)


def allocate_fixed(request, budget, weights):
    request = np.asarray(request, float)
    weights = np.asarray(weights, float)
    budget = float(budget)
    if (request.shape != weights.shape or not np.isfinite(request).all()
            or not np.isfinite(weights).all() or not np.isfinite(budget)
            or np.any(request < 0.) or np.any(weights < 0.) or budget < 0.
            or float(weights.sum()) > 1. + 1e-12):
        raise ValueError('Invalid frozen branch allocation')
    served = np.minimum(request, budget * weights)
    return served, max(0., budget - float(served.sum()))


def _dedicated_delivery(served, amount, explicit, name):
    """Recover a source split only when its registered topology makes it unique."""
    served=np.asarray(served,float);amount=float(amount)
    if not np.isfinite(amount) or amount<0.:
        raise ValueError('Invalid dedicated source total: '+name)
    # Source identity is stricter than a plant balance acceptance tolerance:
    # otherwise a small closure residual can normalize into a new 100% branch.
    roundoff=32*np.finfo(float).eps*max(1.,amount,float(served.sum()))
    if explicit is not None:
        flow=np.asarray(explicit,float)
        if (flow.shape!=(4,) or not np.isfinite(flow).all() or np.any(flow<0.)
                or np.any(flow-served>roundoff) or np.any(flow[2:]>0.)
                or abs(float(flow.sum())-amount)>roundoff):
            raise ValueError('Invalid explicit source delivery: '+name)
        if amount==0.:
            if np.any(flow!=0.):raise ValueError('Zero source total has nonzero explicit delivery: '+name)
            return np.zeros(4),'inactive_dedicated_source'
        return flow,'explicit_source_delivery'
    if amount==0.:return np.zeros(4),'inactive_dedicated_source'
    eligible=float(served[:2].sum())
    if amount>eligible+roundoff:
        raise ValueError('Dedicated source exceeds connected service: '+name)
    if abs(amount-eligible)<=roundoff:
        return np.r_[served[:2],0.,0.],'unique_dedicated_topology_and_closed_balance'
    positive=np.flatnonzero(served[:2]>0.)
    if len(positive)==1:
        flow=np.zeros(4);flow[positive[0]]=amount
        return flow,'unique_single_connected_delivery'
    raise ValueError('Ambiguous dedicated source delivery; explicit source flows required: '+name)


def _backup_shares(flow, served):
    if float(flow.sum())>0.:return shares(flow),'actual_dedicated_source_delivery'
    # Existing standby proxy is retained when not running. Zero discharge is
    # not evidence of an absent connection or a prohibition on later startup.
    return np.r_[shares(served[:2]),0.,0.],'existing_connected_prefault_service_proxy'


def freeze_branches(plant, served, action, *, source=None, source_flows=None):
    served = np.asarray(served, float)
    if served.shape != (4, 3) or not np.isfinite(served).all() or np.any(served<0.):
        raise ValueError('Pre-fault actual branch delivery must be 4x3')
    flows={} if source_flows is None else source_flows
    if set(flows)-{'cold','ups','diesel'}:raise ValueError('Unknown source flow key')
    cold,cold_basis=_dedicated_delivery(served[:,2],action.get('cold_discharge_kW',0.),flows.get('cold'),'cold')
    ups_total=float(action.get('ups_discharge_kW',0.));dg_total=float(action.get('diesel_kW',0.))
    if ups_total>0. and dg_total>0. and not all(k in flows for k in ('ups','diesel')):
        raise ValueError('Ambiguous simultaneous electric dedicated sources; explicit source flows required')
    ups,ups_basis=_dedicated_delivery(served[:,0],ups_total,flows.get('ups'),'ups')
    dg,dg_basis=_dedicated_delivery(served[:,0],dg_total,flows.get('diesel'),'diesel')
    dedicated_e=ups+dg
    e_roundoff=32*np.finfo(float).eps*max(1.,float(served[:,0].sum()),ups_total+dg_total)
    if np.any(dedicated_e-served[:,0]>e_roundoff):raise ValueError('Electric source deliveries overlap service')
    ordinary_e=np.maximum(0.,served[:,0]-dedicated_e)
    ordinary_c=np.maximum(0.,served[:,2]-cold)
    cold_roundoff=32*np.finfo(float).eps*max(1.,float(served[:,2].sum()),float(cold.sum()))
    e_roundoff_removed=np.where(ordinary_e<=e_roundoff,ordinary_e,0.)
    c_roundoff_removed=np.where(ordinary_c<=cold_roundoff,ordinary_c,0.)
    ordinary_e-=e_roundoff_removed;ordinary_c-=c_roundoff_removed
    cold_weights,cold_weights_basis=_backup_shares(cold,served[:,2])
    ups_weights,ups_weights_basis=_backup_shares(ups,served[:,0])
    dg_weights,dg_weights_basis=_backup_shares(dg,served[:,0])
    # Source references are actual running ports, not broad arrived ceilings.
    converter = (float(action.get('absorption_cooling_kW', 0.)) * plant.abs_electric_kW_per_rt
        + sum(action.get('electric_chiller_cooling_kW', [])) * plant.ec_electric_kW_per_rt) / RT_TO_KW
    station = (float(action.get('station_auxiliary_kW', 0.)) + converter
        + float(action.get('cold_storage_pump_kW', 0.)))
    charge_e = float(action.get('ups_charge_kW', 0.))
    charge_c = float(action.get('cold_charge_kW', 0.))
    electric_ports=np.r_[ordinary_e,station,charge_e]
    cold_ports=np.r_[ordinary_c,charge_c]
    return dict(schema=SCHEMA, carrier_shares=
        np.column_stack([shares(served[:, i]) for i in range(3)]).tolist(),
        electric_port_shares=shares(electric_ports).tolist(),
        cold_port_shares=shares(cold_ports).tolist(),
        ordinary_electric_ports_kW=electric_ports.tolist(),ordinary_cold_ports_kW=cold_ports.tolist(),
        ordinary_electric_denominator_kW=float(electric_ports.sum()),ordinary_cold_denominator_kW=float(cold_ports.sum()),
        dedicated_cold_shares=cold_weights.tolist(),dedicated_ups_shares=ups_weights.tolist(),
        dedicated_diesel_shares=dg_weights.tolist(),
        source_partition=dict(cold=dict(ordinary_delivery_kW=ordinary_c.tolist(),dedicated_delivery_kW=cold.tolist(),
            basis=cold_basis,backup_weights_basis=cold_weights_basis,source_roundoff_bound_kW=cold_roundoff,
            ordinary_roundoff_removed_kW=c_roundoff_removed.tolist()),
            electric=dict(ordinary_delivery_kW=ordinary_e.tolist(),dedicated_delivery_kW=dedicated_e.tolist(),
                ups_delivery_kW=ups.tolist(),diesel_delivery_kW=dg.tolist(),ups_basis=ups_basis,diesel_basis=dg_basis,
                ups_backup_weights_basis=ups_weights_basis,diesel_backup_weights_basis=dg_weights_basis,
                source_roundoff_bound_kW=e_roundoff,ordinary_roundoff_removed_kW=e_roundoff_removed.tolist())),
        actual_prefault_served_kW=served.tolist(), held_ups_charge_kW=charge_e,
        held_cold_charge_kW=charge_c, source=deepcopy(source),
        unused_share_transfer=False, priority_order_used=False,
        qualification='Aggregate held branch allocation assumption; no calibrated hydraulic flow claimed')


def validate_branches(reference):
    value = reference.get('_fixed_branches')
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError('Explicit frozen pre-fault branch reference required')
    carrier = np.asarray(value['carrier_shares'], float)
    electric = np.asarray(value['electric_port_shares'], float)
    cold = np.asarray(value['cold_port_shares'], float)
    backups=[np.asarray(value[k],float) for k in
        ('dedicated_cold_shares','dedicated_ups_shares','dedicated_diesel_shares')]
    if carrier.shape != (4, 3) or electric.shape != (6,) or cold.shape != (5,):
        raise ValueError('Invalid frozen branch reference shape')
    if any(x.shape!=(4,) or np.any(x[2:]!=0.) for x in backups):
        raise ValueError('Invalid dedicated source topology')
    for weights in [*carrier.T, electric, cold,*backups]:
        allocate_fixed(np.zeros_like(weights), 0., weights)
        if not (abs(float(weights.sum())) < 1e-12 or abs(float(weights.sum()) - 1.) < 1e-12):
            raise ValueError('Frozen branch shares must sum to zero or one')
    for weights,key in ((electric,'electric'),(cold,'cold')):
        ports=np.asarray(value['ordinary_'+key+'_ports_kW'],float)
        if (ports.shape!=weights.shape or not np.isfinite(ports).all() or np.any(ports<0.)
                or abs(float(ports.sum())-value['ordinary_'+key+'_denominator_kW'])>1e-7
                or not np.allclose(shares(ports),weights,rtol=0.,atol=1e-12)):
            raise ValueError('Frozen source ports and weights disagree: '+key)
    return value, carrier, electric, cold
