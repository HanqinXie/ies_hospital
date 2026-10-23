"""Explicit M21 metadata adapter for the inherited trajectory exporter.

Only identity/provenance fields are added. Physical events, demands, clocks,
permissions and the development/confirmation role are never rewritten here.
"""
from __future__ import annotations
import copy
from hospital_ies.dataset_export import normalize_scenario
from hospital_ies.data_contract import SCHEMA_VERSIONS
from . import CONTRACT_ID
from .design import digest

def normalize_study_scenario(scene):
    seed=int(scene.get('initialization_seed',20261023))
    value=normalize_scenario(scene,seed)
    value['purpose']=scene.get('role',scene.get('purpose','development'))
    value['sampling_cohort']=scene.get('sampling_cohort',value['purpose'])
    value['parent_template_id']=scene.get('parent_template_id',scene.get('source_block_id',value['family_id']))
    exogenous={k:v for k,v in scene.items() if k in ('start_hour','faults','auxiliary_faults','shock',
        'shock_by_priority','shock_by_zone','exogenous_events','enable_propagation','initial_reserve_kWh')
        or k.startswith(('fault_','shock_'))}
    value['m21_physical_exogenous_sha256']=digest(exogenous)
    value['exogenous_stream_id']=scene.get('exogenous_stream_id','m21_exogenous:'+digest(exogenous))
    value['q_design']=scene.get('q_design',value['q_design'])
    if scene.get('shock_by_zone'):
        # A single legacy scalar ignores hourly zonal requests and must not
        # falsely report a real M21 P0 increase of exactly one.
        value.pop('actual_p0_load_multiplier',None)
        value['m21_actual_demand_semantics']='hour-dependent original requests and zonal increments in segment ledger'
    return value

def observed_engineering_events(scene,observed_until,*,has_segments):
    """Export new event kinds only after their actual observation boundary."""
    if not has_segments:return []
    from hospital_ies.state_transition import event_window
    events=[]
    for field,clock,kind in (('auxiliary_faults','fault','auxiliary_availability'),
                             ('shock_by_zone','shock','zonal_clinical_demand')):
        if not scene.get(field):continue
        start,end=event_window(scene,clock)
        for edge,t in (('start',start),('end',end)):
            if t>float(observed_until)+1e-9:continue
            events.append(dict(event_id=f'm21_exo:{field}:{edge}',time_minute=t,
                kind=f'm21_exogenous_{kind}_{edge}',origin='exogenous',cause_class=kind,
                parent_event_id=None,details={field:copy.deepcopy(scene[field]),'active':edge=='start'},
                provenance='Registered boundary applied by M21 common execution; no future boundary exported'))
    return events

def exporter_metadata(scenes):
    splits={s['id']:dict(group_id=s['source_block_id'],split=s.get('role','development'),
        fit_allowed=False,embargo_minutes=60.,grouping_basis='full source support and calendar-day isolation') for s in scenes}
    schemas=copy.deepcopy(SCHEMA_VERSIONS)
    schemas['sci_engineering']=CONTRACT_ID
    return dict(assumption_set_id=CONTRACT_ID,schemas=schemas,splits=splits)
