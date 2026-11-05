"""Current saved-state math and original fixed-source balance only."""
from __future__ import annotations
import copy,importlib.util,json
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
spec=importlib.util.spec_from_file_location('v18_saved_math',HERE.parent/'c18_constraint_localization/replay.py')
saved=importlib.util.module_from_spec(spec);spec.loader.exec_module(saved)


def inputs(*, no_running_GT=False, unavailable_GT=False):
    f=json.loads((HERE/'BRIDGE_FIXTURE.json').read_text(encoding='utf-8'))
    if no_running_GT or unavailable_GT:
        f['state']['gt_prev']=[0.,0.,0.]
        c=f['readiness_context'];c['actual_gt_kW']=[0.,0.,0.];c['gt_ramp_anchor_kW']=[0.,0.,0.]
        c['gt_physical_status']=['EXOGENOUS_UNAVAILABLE' if unavailable_GT else 'OFF']*3
        c['gt_can_stop']=[True]*3;c['pending_starts']=[None]*3
        for d in (c['arrived_dispatch'],f['execution_limits']['effective_dispatch']):
            d.update(gt_electric_kW=[0.,0.,0.],hrsg_steam_lbph=0.,gt_fuel_MMBtu_h=0.)
        if unavailable_GT:
            for i in range(3):f['availability']['GT_'+str(i+1)]=0.
    kwargs=saved.inputs(f);kwargs['execution_limits']=f['execution_limits']
    return kwargs


def fixed_source_balance(kwargs,queued_action,*,retain_observed_GT):
    """Original physical equations; no plant/state transitions or cold DG."""
    from hospital_ies.model import gt_level_properties
    from hospital_ies.resilience import _solve_milp
    action=copy.deepcopy(queued_action);availability=copy.deepcopy(kwargs['availability'])
    availability['GRID']=0.;action['grid_import_kW']=0.;action['diesel_kW']=0.
    action['gt_electric_kW']=(copy.deepcopy(kwargs['readiness_context']['actual_gt_kW'])
        if retain_observed_GT else [0.,0.,0.])
    gp,heat,fuel=gt_level_properties(kwargs['plant'],kwargs['row'].outdoor_temperature_C)
    action['hrsg_steam_lbph']=sum(action['gt_electric_kW'][i]*availability['HRSG_'+str(i+1)] for i in range(3))*float(heat[-1]/gp[-1])
    action['gt_fuel_MMBtu_h']=sum(action['gt_electric_kW'])*float(fuel[-1]/gp[-1])
    keys=('row','plant','state','dt_h','grid_import_limit_kW','gt_ramp_kW_per_h',
        'solver_time_limit_sec','config','execution_contract')
    return _solve_milp(**{key:kwargs[key] for key in keys},loads=np.asarray(kwargs['loads']),
        availability=availability,diesel_start_available=False,execution_action=action)
