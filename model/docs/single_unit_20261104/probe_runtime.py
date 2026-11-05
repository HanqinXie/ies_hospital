"""Real native reset + one minute interface probes, not formal trajectory results."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from tools.cooling_dynamics_20261104 import default_config
from tools.cooling_dynamics_20261104.run_worker import controller_records, record_observation
from tools.sci_engineering.runner import source_binding
from tools.single_unit_20261104.registration import make_case
from tools.single_unit_20261104.runtime import build_runtime, causal_ledger
from tools.single_fault_fuel_20261102.runtime import FuelCoreMixin

began = time.perf_counter(); receipts = []; failed = False
for topic, kind, variant in [('T05', 'tower_unit', 'responsive'),
                             ('T06', 'primary_pump', 'unattended'),
                             ('T06', 'healthy_no_dispatch', 'unattended')]:
    item = dict(topic=topic, kind=kind, variant=variant,
                status='failed_interface_probe', formal_scientific_trajectory=False)
    try:
        suffix = 'U' if variant == 'unattended' else 'R'
        path = Path('E:/hospital_ies_cooling_dynamics_20261104_run01/cases') / (topic + '_' + suffix + '.json')
        base = json.loads(path.read_text(encoding='utf-8-sig'))
        case = make_case(base, kind=kind, variant=variant, base_binding=dict(
            path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        env, controller, resolved = build_runtime(case, source_binding(ROOT), default_config())
        env.reset(scenario=deepcopy(case['scenario']), seed=int(case['scenario'].get('initialization_seed', 20261023)))
        assert env.core._solve.__func__ is FuelCoreMixin._solve
        assert env.core._local_interval_action.__func__ is FuelCoreMixin._local_interval_action
        assert env.model_closure_events == case['unit_study']['native_scene']['model_closure_events']
        assert env.core.time == 0.
        history = []; record_observation(env, history)
        request, _ = controller.decide(controller_records(controller, history))
        _, _, _, _, info = env.step(request)
        rows = info['segments']
        assert rows and env.core.time == 1.
        assert all('single_unit_causal_ledger' in row for row in rows)
        for row in rows:
            assert row['single_unit_causal_ledger']['effective_functional_availability'] == row['hardware_availability']
        # Pure current-state hardware queries do not mutate the physical clock,
        # inventory, cooling dynamics or any accepted trajectory state.
        causes = [causal_ledger(case, minute, env.core._hardware_data_at(minute)[-1])
                  for minute in (59., 60., 119., 120.)]
        assert env.core.time == 1.
        item.update(status='passed_real_reset_and_one_native_minute', actual_end_minute=env.core.time,
                    actual_segment_count=len(rows), native_reset_calendar=env.model_closure_events,
                    checked_current_cause_states=causes,
                    source_guard_methods_unchanged=True)
        print('PROBE_OK', topic, kind, variant, 'actual_end_minute', env.core.time, flush=True)
    except Exception as exc:
        failed = True; item.update(error=repr(exc), traceback=traceback.format_exc())
        print(item['traceback'], flush=True)
    receipts.append(item)
result = dict(status='failed' if failed else 'passed', formal_scientific_trajectories_executed=False,
              wall_seconds=time.perf_counter() - began, probes=receipts)
out = ROOT / 'docs/single_unit_20261104/logs/06_REAL_RESET_PROBE.json'
out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
sys.exit(1 if failed else 0)
