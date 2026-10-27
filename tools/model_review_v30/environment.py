"""V30 terminal-output adapter; all physical integration delegates unchanged."""
from types import SimpleNamespace
from tools.model_review_v28.environment import with_boundary as original_adapter
from .terminal import terminal_snapshot
from . import PHYSICAL_PATCH_ID

def with_terminal(overlay_spec=None,*,thermal_profile=None):
    adapter=original_adapter(overlay_spec,demand_version='V28_adopted',thermal_profile=thermal_profile)
    def create(*args,**kwargs):
        env=adapter.OperatingHospitalIESEnv(*args,**kwargs)
        env.operating_model_config['v30_physical_patch']=dict(patch_id=PHYSICAL_PATCH_ID,
            semantics='Powered startup ready clock is no earlier than actual min-off eligibility; no capacity or work requirement changed')
        return env
    def execute(env,*args,**kwargs):
        try:payload=adapter.run_episode(env,*args,**kwargs)
        except Exception:
            env.v30_terminal_snapshot=terminal_snapshot(env,getattr(getattr(env,'core',None),'time',None))
            raise
        snapshot=terminal_snapshot(env,payload.get('summary',{}).get('observed_until_minute'))
        payload['terminal_snapshot']=snapshot
        payload['v30_physical_patch_id']=PHYSICAL_PATCH_ID
        return payload
    return SimpleNamespace(validate_scenario=adapter.validate_scenario,validate_events=adapter.validate_events,
        OperatingHospitalIESEnv=create,run_episode=execute)
