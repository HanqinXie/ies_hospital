"""Only request validation changes; original failure-preserving exporter stays."""
from __future__ import annotations
from types import FunctionType
from tools.integrated_learning import episode as original
from tools.control_improvement.episode import BoundaryBoundPolicy
from .contracts import validate_action


def _isolated(function, namespace):
    value = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__)
    value.__kwdefaults__ = function.__kwdefaults__
    return value


class JointPolicyEnvironment(original.PolicyEnvironment):
    def step(self, ignored_exporter_request):
        namespace = dict(original.PolicyEnvironment.step.__globals__, validate_action=validate_action)
        return _isolated(original.PolicyEnvironment.step, namespace)(self, ignored_exporter_request)


def run_policy_episode(env, scenario, policy, seed, manifest, **options):
    bound = BoundaryBoundPolicy(env, policy) if hasattr(policy, 'bind_current') else policy
    namespace = dict(original.run_episode.__globals__, PolicyEnvironment=JointPolicyEnvironment)
    payload = _isolated(original.run_episode, namespace)(env, scenario, bound, seed, manifest, **options)
    payload['planning_interface'] = 'joint_command.v1'
    payload['execution_model_id'] = env.model_id
    payload['input_access_receipt'] = env.input_access_receipt
    return payload
