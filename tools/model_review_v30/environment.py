"""V30 terminal-output adapter; all physical integration delegates unchanged."""
from types import SimpleNamespace
from tools.model_review_v28.environment import with_boundary as original_adapter
from .terminal import terminal_snapshot

def with_terminal(overlay_spec=None,*,thermal_profile=None):
    adapter=original_adapter(overlay_spec,demand_version='V28_adopted',thermal_profile=thermal_profile)
    def execute(env,*args,**kwargs):
        try:payload=adapter.run_episode(env,*args,**kwargs)
        except Exception:
            env.v30_terminal_snapshot=terminal_snapshot(env,getattr(getattr(env,'core',None),'time',None))
            raise
        snapshot=terminal_snapshot(env,payload.get('summary',{}).get('observed_until_minute'))
        payload['terminal_snapshot']=snapshot
        return payload
    return SimpleNamespace(validate_scenario=adapter.validate_scenario,validate_events=adapter.validate_events,
        OperatingHospitalIESEnv=adapter.OperatingHospitalIESEnv,run_episode=execute)
