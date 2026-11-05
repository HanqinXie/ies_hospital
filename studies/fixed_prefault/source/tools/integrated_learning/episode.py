"""External policy runner using the unchanged failure-preserving exporter.

The exporter owns labels, errors and lifecycle traces. This adapter replaces
only its policy request and then corrects the decision request log from the
request actually submitted. It never intercepts the physical projection,
command arrivals or actual step exceptions. Continuations do not call reset.
"""
from __future__ import annotations
import copy
import time
import numpy as np
from hospital_ies.data_contract import decision_snapshot, validate_action
from hospital_ies.dataset_export import run_trajectory
from .snapshot import capture_execution
from .policies import six_targets


class PolicyEnvironment:
    def __init__(self, env, policy, *, reset=True, prefix_decisions=(),
                 snapshot_steps=(), snapshots=None, binding=None):
        self.env = env; self.policy = policy; self.reset_requested = reset
        self.records = copy.deepcopy(list(prefix_decisions)); self.requests = {}
        self.snapshot_steps = set(snapshot_steps); self.snapshots = {} if snapshots is None else snapshots
        self.binding = binding

    def __getattr__(self, name):
        return getattr(self.env, name)

    def reset(self, *, scenario, seed):
        if self.reset_requested:
            self.records.clear(); self.requests.clear()
            return self.env.reset(scenario=scenario, seed=seed)
        if self.env.scenario != scenario:
            raise ValueError('Continuation scenario differs from restored execution')
        return None

    def step(self, ignored_exporter_request):
        current = decision_snapshot(self.env)
        if self.records and float(self.records[-1]['decision_time_min']) >= float(current['decision_time_min']):
            raise ValueError('Continuation observation history overlaps the current decision')
        self.records.append(copy.deepcopy(current))
        t = float(current['decision_time_min'])
        if self.env.steps_done in self.snapshot_steps:
            if not self.binding:
                raise ValueError('Snapshot capture needs explicit current source/data binding')
            self.snapshots[t] = {'execution': capture_execution(self.env, binding=self.binding),
                                'prefix_decisions': copy.deepcopy(self.records[:-1])}
        begin = time.perf_counter()
        request, diagnostic = self.policy.decide(copy.deepcopy(self.records))
        validate_action(request)
        diagnostic = dict(diagnostic, decision_time_min=t, requested_command=copy.deepcopy(request),
                          inference_ms=1000*(time.perf_counter()-begin))
        self.requests[t] = diagnostic
        # The actual env handles all five objective layers, locks and queue.
        return self.env.step(request)


def run_episode(env, scenario, policy, seed, manifest, *, horizons=(15,30,60),
                reset=True, prefix_decisions=(), snapshot_steps=(), snapshots=None):
    start = 0. if reset else float(env.core.time)
    binding = {'manifest_signature': manifest['signature'], 'simulator_hash': manifest['simulator_hash'],
               'execution_contract_id': manifest['execution_contract_id'], 'scenario_id': scenario['id']}
    proxy = PolicyEnvironment(env, policy, reset=reset, prefix_decisions=prefix_decisions,
                             snapshot_steps=snapshot_steps, snapshots=snapshots, binding=binding)
    payload = run_trajectory(proxy, scenario, policy.policy_id, seed, .35, manifest, horizons)
    diagnostics = []
    for row in payload['decisions']:
        d = copy.deepcopy(proxy.requests.get(float(row['decision_time_min'])))
        if d is None:
            # A boundary/feature failure occurred before any policy request.
            row['requested_command'] = None
            continue
        row['requested_command'] = copy.deepcopy(d['requested_command'])
        generated = row.get('command_generated')
        if generated is not None and generated['request'] != d['requested_command']:
            raise AssertionError('Generated command differs from submitted policy request')
        if generated is not None and 'targets' in d['requested_command']:
            asked = np.asarray([d['requested_command']['targets'][k] for k in
                ('gt_1','gt_2','gt_3','grid_import','diesel','absorption_cooling_kW')], float)
            delta = six_targets(generated['dispatch']) - asked
            d['projection_change_kW'] = delta.tolist()
            d['projection_changed'] = bool(np.max(np.abs(delta)) > 1e-7)
        else:
            d['projection_change_kW'] = None; d['projection_changed'] = None
        row['policy_diagnostic'] = d; diagnostics.append(d)
    payload['policy_diagnostics'] = diagnostics
    payload['continuation_start_minute'] = start
    payload['summary']['strategy_fallback_steps'] = sum(d['strategy_fallback'] for d in diagnostics)
    payload['summary']['projection_changed_steps'] = sum(d['projection_changed'] is True for d in diagnostics)
    payload['summary']['continuation_semantics'] = 'Restored state without reset; segment and summary retain observed prefix'
    return payload
