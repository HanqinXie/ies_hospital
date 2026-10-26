"""V28 current-request adapter; original reset, then explicit demand activation."""
from __future__ import annotations
import copy
from types import SimpleNamespace
import pandas as pd
from tools.model_correction_v25 import environment as physical
from tools.model_review_v27 import environment as cooling
from tools.model_review_v28 import DEMAND_CONTRACT

validate_scenario=physical.validate_scenario
validate_events=physical.validate_events
run_episode=physical.run_episode
DEMAND_VERSIONS=('A_original_heat','V28_adopted')

def activate_boundary(env):
    """Add an in-memory version flag only within the original source grant."""
    if not env.allowed_hours:raise ValueError('Original allowed-hour support is required')
    current=env.hourly.copy(deep=True)
    current['sci_demand_boundary_contract']=pd.Series(None,index=current.index,dtype=object)
    current.loc[current.hour_of_year.isin(env.allowed_hours),'sci_demand_boundary_contract']=DEMAND_CONTRACT
    env.hourly=current

class OperatingHospitalIESEnv(cooling.OperatingHospitalIESEnv):
    def __init__(self,*args,demand_version='A_original_heat',thermal_profile=None,**kwargs):
        if demand_version not in DEMAND_VERSIONS:raise ValueError('Unknown registered demand version')
        if demand_version=='V28_adopted' and thermal_profile is None:raise ValueError('Adopted V28 thermal input binding required')
        self.v28_demand_version=demand_version
        self.v28_thermal_profile=copy.deepcopy(thermal_profile)
        super().__init__(*args,**kwargs)
        self.operating_model_config['model_review_v28']=dict(demand_contract=DEMAND_CONTRACT,demand_version=demand_version,thermal_coupling=None)

    def reset(self,*,scenario,seed=None):
        # Original inputs/seed first, so all devices, queues and lawful history
        # have a common physical initialisation before current requests change.
        self.hourly,self.zones,self.groups=(x.copy(deep=True) for x in self._v27_original_profiles)
        original=physical.OperatingHospitalIESEnv.reset(self,scenario=scenario,seed=seed)
        from tools.model_review_v27.objective_snapshot import _encode
        from tools.integrated_learning.snapshot import digest,rng_state
        core={k:(list(v) if k=='solver_calls' else v) for k,v in vars(self.core).items() if k!='env'}
        initial=dict(state=_encode(self.state),core=_encode(core),rng=_encode(rng_state()))
        self.v28_demand_receipt=dict(demand_contract=DEMAND_CONTRACT,demand_version=self.v28_demand_version,
            initialization='original source and seed reset before current-request adaptation; no perturbed re-equilibration',
            full_initial_state_and_core=initial,full_initial_state_and_core_sha256=digest(initial),
            core_fields=sorted(core),core_env_back_reference_excluded=True,
            source_reference_preserved=True,request_changes_start_at_minute=0.,thermal_coupling=None)
        if self._v27_profile_frames is not None:
            self.v28_demand_receipt['cooling_profile']=cooling.apply_cooling_profile(self,**self._v27_profile_frames)
        if self.v28_thermal_profile is not None:
            # Import only for explicitly registered adopted thermal input.
            from .thermal_adapter import apply_thermal_profile
            self.v28_demand_receipt['thermal_profile']=apply_thermal_profile(self,self.v28_thermal_profile)
        activate_boundary(self)
        observation=self._observation()
        return (observation,original[1]) if isinstance(original,tuple) else observation

def with_boundary(overlay_spec=None,*,demand_version='A_original_heat',thermal_profile=None):
    # Even normal/no-overlay runs must pass through fixed-direct activation.
    return SimpleNamespace(validate_scenario=validate_scenario,validate_events=validate_events,run_episode=run_episode,
        OperatingHospitalIESEnv=lambda *args,**kwargs:OperatingHospitalIESEnv(*args,load_overlay_spec=overlay_spec,demand_version=demand_version,thermal_profile=thermal_profile,**kwargs))
