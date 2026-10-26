"""Real constructor integration without reset, solve, or trajectory attempts."""
import importlib,json
from pathlib import Path
import pytest

def test_factory_preserves_original_m25_constructor_and_does_not_reset(tmp_path):
    try:api=importlib.import_module('tools.model_review_v27.factory')
    except ModuleNotFoundError:pytest.fail('V27 bound factory is absent')
    from tools.model_correction_v25.environment import OperatingHospitalIESEnv
    root=Path(__file__).resolve().parents[1]
    registry=json.loads((root/'configs/model_correction_v26/cases.json').read_text())
    case=next(c for c in registry['cases'] if c['id']=='combined_F')
    context=api.build_context(case,registry,tmp_path/'context',order='legacy_before_cost')
    try:
        assert type(context.env) is OperatingHospitalIESEnv
        assert getattr(context.env,'core',None) is None and context.env.steps_done==0
        assert context.scene['duration_steps']==case['scenario']['duration_steps']
        assert context.controller.config['online_GT_count_order']=='legacy_before_cost'
        assert context.manifest['execution_contract_id'].endswith('v25.v1')
        assert context.manifest['study_contract_id'].endswith('v27.v1')
        assert context.io_resources['_solver_row_sidecar'] is context.env._solver_row_sidecar
        assert context.env.thermal_coupling is None
    finally:context.close()
    assert (tmp_path/'context/FACTORY_IO_RECEIPT.json').is_file()
    receipt=json.loads((tmp_path/'context/FACTORY_IO_RECEIPT.json').read_text())
    assert receipt['solver_sidecar']['entry_count']==0 and receipt['actual_reset_or_episode_calls_by_factory']==0

