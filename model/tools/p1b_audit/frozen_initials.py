"""Fresh initialization through the frozen P1A implementation, not old results."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];RUN=ROOT/'outputs/v43_p1b_20261012_235940'
sys.path.insert(0,str(RUN/'.before/src'))
from hospital_ies.control_env import HospitalIESEnv
from hospital_ies.state_transition import clean
env=HospitalIESEnv(ROOT);result={}
for s in json.loads((RUN/'delivery_replay/scenarios.json').read_text(encoding='utf-8')):
    env.reset(scenario=s,seed=20261012)
    result[s['id']]=dict(scenario=s,state=clean(vars(env.state)),action=env.core.executed)
(RUN/'frozen_p1a_initials.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(dict(families=len(result),implementation=str(Path(sys.modules['hospital_ies.control_env'].__file__).relative_to(ROOT)))))
