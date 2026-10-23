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
    value['exogenous_stream_id']='m21_exogenous:'+digest(exogenous)
    return value

def exporter_metadata(scenes):
    splits={s['id']:dict(group_id=s['source_block_id'],split=s.get('role','development'),
        fit_allowed=False,embargo_minutes=60.,grouping_basis='full source support and calendar-day isolation') for s in scenes}
    schemas=copy.deepcopy(SCHEMA_VERSIONS)
    schemas['sci_engineering']=CONTRACT_ID
    return dict(assumption_set_id=CONTRACT_ID,schemas=schemas,splits=splits)
