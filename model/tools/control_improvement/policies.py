"""Stable policy factory for matched nominal and explicit sensitivity runs."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from tools.integrated_learning.policies import BaselinePolicy,PolicyController
from tools.integrated_learning.models import load_predictor
from .runtime import ExecutionAwarePolicy
from .models import load_rank_model,sha,SEEDS

RECIPES=('priority_milp','secure_milp','frozen_BC_projection035','current_rule_projection0',
         'frozen_BC_projection0','execution_aware_RF_rank_projection0')


def make_policy(recipe_id,*,bc_directory=None,rank_directory=None,seed=4101,formal=True):
    """No scenario/IDs/future arguments. Caller binds the same env abilities."""
    if recipe_id not in RECIPES:raise ValueError('Unregistered policy recipe')
    if recipe_id in ('priority_milp','secure_milp'):return BaselinePolicy(recipe_id)
    if recipe_id=='current_rule_projection0':return ExecutionAwarePolicy(model_id=recipe_id)
    if recipe_id.startswith('frozen_BC'):
        if bc_directory is None:raise ValueError('Actual frozen BC artifact required')
        import torch
        torch.set_num_threads(2)  # Explicit new-run inference configuration.
        model=load_predictor(Path(bc_directory));projection='secure_milp' if recipe_id.endswith('035') else 'priority_milp'
        return PolicyController(model,model_id=recipe_id,fallback_policy=projection,projection_policy=projection)
    if int(seed) not in SEEDS:raise ValueError('Unregistered rank training seed')
    if rank_directory is None:raise ValueError('Actual rank artifact required')
    directory=Path(rank_directory)
    if not (directory/'metadata.json').exists():directory=directory/f'RF_seed{int(seed)}'
    model=load_rank_model(directory)
    if int(model.metadata['seed'])!=int(seed):raise ValueError('Rank artifact differs from requested frozen seed')
    if formal and model.metadata['source_binding'].get('prototype_train_only'):
        raise ValueError('Prototype training smoke artifact cannot be called formal evaluation')
    return ExecutionAwarePolicy(model=model,model_id=recipe_id+f'__seed{int(seed)}',scale=np.asarray(model.metadata['scale_kWh'],float))
