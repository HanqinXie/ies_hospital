"""Input-only conditional cooling profile over unchanged M25 physical equations."""
from __future__ import annotations
import copy,hashlib,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
from tools.model_correction_v25 import environment as base

validate_scenario=base.validate_scenario
validate_events=base.validate_events
run_episode=base.run_episode

def apply_cooling_profile(env,total,zones,mapping):
    """Change actual cold requests; retain source auxiliary reference denominators."""
    from hospital_ies.resilience import build_zone_groups,ZONE_PRIORITY
    hours=mapping.hour_of_year.to_numpy(int)
    if len(set(hours))!=len(hours) or not len(hours):raise ValueError('Unique complete mapped hours required')
    current=env.hourly.set_index('hour_of_year').loc[hours]
    candidate=total.set_index('hour_of_year').loc[hours]
    declared=mapping.set_index('hour_of_year').loc[hours]
    numeric=candidate[['Q_c_demand_kW','Q_c_terminal_coils_kW']].to_numpy(float)
    if not np.isfinite(numeric).all() or (numeric<0).any():raise ValueError('Nonnegative finite cold total required; no clipping')
    if not np.allclose(current.Q_c_demand_kW,declared.total_original_c_kW,atol=1e-6,rtol=0):raise ValueError('Different original annual baseline')
    if not np.allclose(candidate.Q_c_demand_kW,declared.total_new_c_kW,atol=1e-6,rtol=0):raise ValueError('Declared cold total mismatch')
    z=zones[zones.hour_of_year.isin(hours)].copy()
    if z.duplicated(['hour_of_year','zone_name']).any() or len(z)!=len(hours)*len(ZONE_PRIORITY):raise ValueError('Incomplete native critical zone profile')
    if set(z.zone_name)!=set(ZONE_PRIORITY):raise ValueError('Unknown critical zone allocation')
    if not np.isfinite(z.zone_sensible_cooling_kW).all() or (z.zone_sensible_cooling_kW<0).any():raise ValueError('Negative or nonfinite critical-zone cold proxy')
    proxy=(z.zone_sensible_cooling_kW*z.zone_multiplier).groupby(z.hour_of_year).sum().reindex(hours)
    if (proxy.to_numpy()>candidate.Q_c_demand_kW.to_numpy()+1e-6).any():raise ValueError('Critical proxy exceeds total; conditional mapping is not admissible')
    new_hourly=env.hourly.copy(deep=True);ix=new_hourly.set_index('hour_of_year').index.get_indexer(hours)
    for col in ['Q_c_demand_kW','Q_c_terminal_coils_kW']:
        if col in new_hourly:new_hourly.loc[new_hourly.index[ix],col]=candidate[col].to_numpy()
    new_zones=env.zones.copy(deep=True);oldkeys=pd.MultiIndex.from_frame(new_zones[['hour_of_year','zone_name']]);newkeys=pd.MultiIndex.from_frame(z[['hour_of_year','zone_name']]);positions=oldkeys.get_indexer(newkeys)
    if (positions<0).any():raise ValueError('Critical-zone profile has no original source row')
    # Only this one measured representative sensible-cold field changes.
    if not np.allclose(new_zones.iloc[positions].zone_multiplier.to_numpy(),z.zone_multiplier.to_numpy(),atol=0,rtol=0):raise ValueError('Zone multiplier cannot change')
    new_zones.loc[new_zones.index[positions],'zone_sensible_cooling_kW']=z.zone_sensible_cooling_kW.to_numpy()
    grouped=build_zone_groups(new_zones[new_zones.hour_of_year.isin(hours)],new_hourly.iloc[ix]).set_index('hour_of_year')
    cols=[f'P{g}_c_kW' for g in range(4)]
    expected=declared[[f'P{g}_new_c_kW' for g in range(4)]].to_numpy(float)
    if not np.allclose(grouped.loc[hours,cols],expected,atol=1e-6,rtol=0):raise ValueError('Native zone allocation differs from registered group profile')
    if not np.allclose(expected.sum(axis=1),candidate.Q_c_demand_kW,atol=1e-6,rtol=0):raise ValueError('Group cooling does not conserve total')
    new_groups=env.groups.copy(deep=True);new_groups.loc[hours,cols]=grouped.loc[hours,cols]
    # All *_reference_c_kW, source fans/pumps and every noncold input remain
    # the source-derived originals. Replacing reference denominators would
    # conceal the actual auxiliary load response to increased current cold.
    env.hourly=new_hourly;env.zones=new_zones;env.groups=new_groups
    return dict(hours=len(hours),hours_start=int(min(hours)),hours_end=int(max(hours)),source_auxiliary_and_reference_unchanged=True,
        noncold_inputs_unchanged=True,profile='finite-normal signed numerical delta, conditional transfer to annual baseline')

class OperatingHospitalIESEnv(base.OperatingHospitalIESEnv):
    def __init__(self,*args,load_overlay_spec=None,**kwargs):
        self.v27_load_overlay_spec=copy.deepcopy(load_overlay_spec)
        super().__init__(*args,**kwargs)
        self._v27_original_profiles=(self.hourly.copy(deep=True),self.zones.copy(deep=True),self.groups.copy(deep=True))
        self._v27_profile_frames=None
        if self.v27_load_overlay_spec is not None:
            if self.v27_load_overlay_spec.get('variant')!='conditional':raise ValueError('Unknown explicit V27 load profile')
            frames={}
            for name,item in self.v27_load_overlay_spec['files'].items():
                path=Path(item['path']);path=path if path.is_absolute() else self.root/path
                if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Conditional load input changed: '+name)
                frames[name]=pd.read_csv(path)
            if set(frames)!={'total','zones','mapping'}:raise ValueError('Complete total/zone/mapping input contract required')
            self._v27_profile_frames=frames

    def reset(self,*,scenario,seed=None):
        self.hourly,self.zones,self.groups=(x.copy(deep=True) for x in self._v27_original_profiles)
        original=super().reset(scenario=scenario,seed=seed)
        # Shared original input and seed create the same persistent initial
        # device/queue/fuel state before either load version begins at t=0.
        from tools.model_review_v27.objective_snapshot import _encode
        from tools.integrated_learning.snapshot import digest
        fields=('executed','queue','pending_commands','command_queue','gt_clocks','gt_start_pending','interlock_latched','ups','cold_store')
        common_initial=dict(state=_encode(self.state),core={k:_encode(getattr(self.core,k)) for k in fields if hasattr(self.core,k)})
        self.v27_load_profile_receipt=dict(initialization='original inputs and original seed reset first; conditional current demands begin at t0',
            initial_selected_persistent_state_sha256=digest(common_initial),initial_selected_persistent_state=common_initial,
            is_annual_replacement=False,is_re_equilibrated_perturbed_initial_state=False,thermal_coupling=None)
        if self._v27_profile_frames is not None:
            self.v27_load_profile_receipt.update(apply_cooling_profile(self,**self._v27_profile_frames))
            value=self._observation()
            return (value,original[1]) if isinstance(original,tuple) else value
        return original

def with_overlay(spec):
    if spec is None:return base
    return SimpleNamespace(validate_scenario=validate_scenario,validate_events=validate_events,run_episode=run_episode,
        OperatingHospitalIESEnv=lambda *args,**kwargs:OperatingHospitalIESEnv(*args,load_overlay_spec=spec,**kwargs))
