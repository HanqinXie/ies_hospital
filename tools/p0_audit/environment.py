"""Record the environment actually used; no dependency installation."""
import importlib.metadata
import json
import platform
import sys
import numpy as np
from scipy.optimize import milp, Bounds

print(json.dumps({'python':sys.version, 'executable':sys.executable,
    'platform':platform.platform(), 'packages':{k:importlib.metadata.version(k)
    for k in ('numpy','pandas','scipy','scikit-learn','pytest','joblib')},
    'seed':20261012, 'sobol_seed':20261012+7919,
    'solver':'SciPy bundled HiGHS; actual version printed below',
    'purpose':'diagnostic environment, no new training or full run'}, indent=2), flush=True)
# A one-variable diagnostic prints the bundled solver version (not a plant run).
r = milp(np.array([1.]), integrality=np.array([1]), bounds=Bounds([0.],[1.]), options={'disp':True})
print('solver_probe_status=',r.status, 'message=',r.message, flush=True)
