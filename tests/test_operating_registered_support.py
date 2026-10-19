"""Real registered manifest entry gates only; no data loader/reset/solver/step."""
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
RUN=ROOT/'outputs/v15_engineering_operation_20261017_run01'


@pytest.mark.parametrize('batch',[
    'dev_incidents_M14_engineering_day10_L1',
    'dev_normal_M14_engineering_day10_N2',
    'pilot_v2_known_M14_engineering_early45',
])
def test_real_registered_support_reaches_native_reset_entry_without_extra_future_hour(batch,monkeypatch):
    from tools.operating_response import environment as m,data_design as d
    manifest=json.loads((RUN/'experiments'/batch/'manifest.json').read_text(encoding='utf8'))
    scene=manifest['scenarios'][0]
    env=object.__new__(m.OperatingHospitalIESEnv)
    env.allowed_hours=frozenset(d.allowed_hours(manifest));env.sealed_days=frozenset(d.SEALED_DAYS)
    env.model_id='M14_engineering';entered=[]
    def native_entry(self,*,scenario,seed=None):
        entered.append(scenario['id']);return {'test':'entry-only'},{}
    monkeypatch.setattr(m,'isolated',lambda function,namespace:native_entry)
    # Only the actual v15 registration guard runs. No NativeEnvironment reset,
    # annual values, initialization solve or physical trajectory is executed.
    assert env.reset(scenario=scene,seed=20261017)==({'test':'entry-only'},{})
    assert entered==[scene['id']]


@pytest.mark.parametrize('violation',['missing_registered_hour','sealed_hour'])
def test_real_incident_still_rejects_missing_or_sealed_actual_support(violation,monkeypatch):
    from tools.operating_response import environment as m,data_design as d
    manifest=json.loads((RUN/'experiments/dev_incidents_M14_engineering_day10_L1/manifest.json').read_text(encoding='utf8'))
    scene=manifest['scenarios'][0];allowed=d.allowed_hours(manifest)
    env=object.__new__(m.OperatingHospitalIESEnv);env.model_id='M14_engineering'
    env.allowed_hours=frozenset(allowed-{max(allowed)} if violation=='missing_registered_hour' else allowed)
    env.sealed_days=frozenset({10} if violation=='sealed_hour' else d.SEALED_DAYS)
    def forbidden(*args,**kwargs):pytest.fail('Rejected support must not reach native initialization')
    monkeypatch.setattr(m,'isolated',forbidden)
    with pytest.raises(ValueError):env.reset(scenario=scene,seed=20261017)
