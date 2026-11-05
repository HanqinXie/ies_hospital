"""Pure reviewed source-profile registry and output-only leaf migration checks.

These functions compare declarations and source text; they never authenticate
files, a caller, a DTO, a solve or an actual result. The external producer and
observer must verify the returned requirements against their exact source root,
BINDINGS, loaded caller, original owned rows and final-result argument.

Only reviewed releases may edit the exact source/recorder/helper/expression
literals below. No caller-supplied approval,
new-hash registration API, filesystem lookup or native import is provided.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import math
import re

LEGACY_PROFILE='native_return_v1'
CAPTURE_PROFILE='completed_leaf_capture_v1'
SCIENTIFIC_CAPTURE_PROFILE='single_fault_continuous_storage_capture_v1'
LEGACY_RECORDER_SHA256='7640aba759d67d0a07a149e86c46f85eb11f647d099dbdda95506e314b21be62'
LEGACY_NATIVE_SOURCE_CONTRACT={
    'src/hospital_ies/solver_backend.py':'14d3aa2a179cdf00c9e6e43d224bde31f1ed832abeb05c692516029a6406b4d7',
    'tools/model_closeout/numerics.py':'822bf480936cd184b35033eaa631e3b0a244590ef346148432ce409b1be4d3d5',
    'tools/operating_response/physical_balance.py':'fe13fe96a58c0f8816a38419074e4a5c12b65e0207b0d37e85a2d8abf82a76f5',
    'tools/model_correctness_v23/domains.py':'2eda2c44317c563db1e20e8855f4f00437a542664639e9777167bfdf3aa496d8',
    'tools/essential_fixes/execution.py':'0d1bb1161810a49275b03fbd9c8951bb64727ef9b0f12d7658655d3a615d24f4',
    'tools/model_correction_v25/reserve.py':'6754608e317f94fae2f40516dbca033c6be869a01edd8cd257ac1753179f3dba',
    'src/hospital_ies/solver_diagnostics.py':'fcc3bdad7ace8b880f72279eaf4a551ef812977839555435f7e7227ef5584419',
    'tools/model_correctness_v23/numerics.py':'d49eeac8bf099bf50466bbdf6c967f4505d3ea5820b4802209c8d97546933d76',
    'tools/execution_recovery/numerics.py':'3cd9b6b7757866154f7b80c0240803d942c30affbfa7c4792b6bbaaea47641e2',
    'tools/engineering_model/environment.py':'cf0d465bda948159eeffb7a451ddeea701602def597b8f79f11a7473a2a5bdab',
    'tools/model_closure_v22/environment.py':'3b4d6a3cbe59b9bdffb3db039cc12cb8c361f016fecd93c2512199da5fda03ea',
    'tools/model_correctness_v23/environment.py':'6db840b548680f1ba942c23d4ed825d67a52afa7b39bc6ff2894e29af5de0d30',
    'tools/engineering_completion_v24/environment.py':'a45426db0f1c9a205e438a50d209ea6c702ce139a8147b2d787c7fb5dd2916d7',
    'tools/engineering_completion_v24/cold_modes.py':'6add409d192ec8cc6f3b10b043c72055075a138548f6efe30682d5d10b89c34c'}

# Exact reviewed output-only capture release; unknown and mixed hashes fail closed.
CAPTURE_NATIVE_SOURCE_CONTRACT=dict(LEGACY_NATIVE_SOURCE_CONTRACT,**{
    'tools/operating_response/physical_balance.py':'5d8f19a4d61afbc6bdc9f6df6b856ccca3eab10c7a6527233e2746c7b656e71c'})
CAPTURE_RECORDER_SHA256='3f774bc3a0871b00703c948a680ec4295146f487d8f496d925b82ec2ac36c14c'
# A60602 remains an exact readable backend/helper/recorder release after the
# source-only local resolver changes the recorder bytes. No numeric grant or
# actual-result/source verification is supplied by a registry declaration.
FROZEN_A60602_CAPTURE_RECORDER_SHA256='f38fc6e4f45886599f3fe1d6efa2b84013ba21ed3607f131293d4fbb25bb697e'
FROZEN_A60602_EXACT_CACHE_BACKEND_SHA256='5c67375a12d9972862bc0fa24b98fef07da9309e7c4ad01770223d1c7dad8fdd'
FROZEN_A60602_EXACT_CACHE_HELPER_SOURCE_CONTRACT={
    'src/hospital_ies/solver_result_cache.py':'0ecbf955523f8c7a8a2a45ab38931dbe9cf86aac5f17625e329cedd0701725e4'}
# Frozen source12 remains readable after the current recorder advances. This
# literal is a reviewed release pin, never a caller-supplied approval.
FROZEN12_CAPTURE_RECORDER_SHA256='24ca59f219f3217a4755052f76b8deb96cbd0022b208a97e8aaad78091cd83ee'
FROZEN12_CAPTURE_LEAF_SHA256='5d8f19a4d61afbc6bdc9f6df6b856ccca3eab10c7a6527233e2746c7b656e71c'
CAPTURE_AUXILIARY_SOURCE_CONTRACT={
    'tools/annual_program/compiled_capture.py':'9415fd671c163f96ad27f13f8863b25a3bd9269071f47e6056e7d31758072bd1',
    'tools/annual_program/compiled_flow_input.py':'ad8e8262c5da7095e5e850c722f8461e59216164c875e3d52ea2ffe18d0641ad'}
# Reviewed exact-result cache release. Original native function, numerical
# parameters and original acceptance code are AST-identical to the old release.
# No runtime/caller registration mechanism is provided.
FROZEN16_CAPTURE_RECORDER_SHA256='f38fc6e4f45886599f3fe1d6efa2b84013ba21ed3607f131293d4fbb25bb697e'
FROZEN16_EXACT_CACHE_BACKEND_SHA256='6d6874146d368ace320fe471bd25f5107aa88d4db46495f6a2c6b3d416875576'
FROZEN16_EXACT_CACHE_HELPER_SOURCE_CONTRACT={
    'src/hospital_ies/solver_result_cache.py':'82a1f6ef5bb7c5d7b078f25092bca3306837efe5cbae8f03336df4a09f89002a'}
EXACT_CACHE_BACKEND_SHA256='5c67375a12d9972862bc0fa24b98fef07da9309e7c4ad01770223d1c7dad8fdd'
EXACT_CACHE_HELPER_SOURCE_CONTRACT={
    'src/hospital_ies/solver_result_cache.py':'0ecbf955523f8c7a8a2a45ab38931dbe9cf86aac5f17625e329cedd0701725e4'}
CAPTURE_LOGGING_EXPRESSION_AST_SHA256='75324423f7f8b30e9bc927b9d91783316db78be2d6e11aefbcafb0e4f95e7a20'

LEGACY_PHYSICAL_FUNCTION_AST_SHA256='6e9a0eab45589c888362aa344df5b68791caf9a58e9f2223e18804198ee1d164'
LEGACY_PHYSICAL_MODULE_AST_SHA256='fe69b4bd0c5ba5b3946b10f87cb33710f393d00eab267c6a698b517296ffb581'

# Separate reviewed scientific release. These exact bytes implement continuous
# storage loss and actual-interval reserve coefficients; they are NOT an
# output-only migration of the historical scientific AST. The capture envelope
# and final-result ownership rules remain the same. No numeric access is granted.
SCIENTIFIC_NATIVE_SOURCE_CONTRACT={
    'src/hospital_ies/solver_backend.py':'5c67375a12d9972862bc0fa24b98fef07da9309e7c4ad01770223d1c7dad8fdd',
    'tools/model_closeout/numerics.py':'822bf480936cd184b35033eaa631e3b0a244590ef346148432ce409b1be4d3d5',
    'tools/operating_response/physical_balance.py':'775534e61f5fe8ca519cd20b69f47c44e6102ab079539e7d40a600d35f232746',
    'tools/model_correctness_v23/domains.py':'2eda2c44317c563db1e20e8855f4f00437a542664639e9777167bfdf3aa496d8',
    'tools/essential_fixes/execution.py':'0d1bb1161810a49275b03fbd9c8951bb64727ef9b0f12d7658655d3a615d24f4',
    'tools/model_correction_v25/reserve.py':'9a90b67a017bdc88b84a352bffcc535dee94ed48d2e74fd2a199bc89a416ef34',
    'src/hospital_ies/solver_diagnostics.py':'fcc3bdad7ace8b880f72279eaf4a551ef812977839555435f7e7227ef5584419',
    'tools/model_correctness_v23/numerics.py':'d49eeac8bf099bf50466bbdf6c967f4505d3ea5820b4802209c8d97546933d76',
    'tools/execution_recovery/numerics.py':'3cd9b6b7757866154f7b80c0240803d942c30affbfa7c4792b6bbaaea47641e2',
    'tools/engineering_model/environment.py':'cf0d465bda948159eeffb7a451ddeea701602def597b8f79f11a7473a2a5bdab',
    'tools/model_closure_v22/environment.py':'3b4d6a3cbe59b9bdffb3db039cc12cb8c361f016fecd93c2512199da5fda03ea',
    'tools/model_correctness_v23/environment.py':'c388f735e59b7b276ade039503dc725b0aa7760d77b545cdb132238f5ad6b37f',
    'tools/engineering_completion_v24/environment.py':'a45426db0f1c9a205e438a50d209ea6c702ce139a8147b2d787c7fb5dd2916d7',
    'tools/engineering_completion_v24/cold_modes.py':'6add409d192ec8cc6f3b10b043c72055075a138548f6efe30682d5d10b89c34c'}
SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT={
    'src/hospital_ies/solver_result_cache.py':'0ecbf955523f8c7a8a2a45ab38931dbe9cf86aac5f17625e329cedd0701725e4',
    'tools/sci_engineering/storage.py':'44676255a5dd1cef3b8f847646c47700c33d6730396aa7e0e545e99c4703c0d3',
    'tools/single_fault_model_20261102/__init__.py':'ebc931c4233d6a689a8a211d2913f74b9b339c1e74a08e2a6afc28f984e12f94',
    'tools/single_fault_model_20261102/storage.py':'649595e9b77550ac5ab16c046ed63ef17d76d7f59229f99da1cb97825034279d',
    'tools/single_fault_model_20261102/runtime.py':'b569911c9364dab333ec9b933565362e4f94556ef8a6b3b08141d4a4d3978b48',
    'tools/single_fault_model_20261102/environment.py':'ae6477bb998171d922fd78392a6d5c7d30357f3f86dae6e343a41904938b488b',
    'tools/single_fault_model_20261102/runner.py':'bd9adfb3d0bd71b9e670a29bd903ba7be628f5ce89eba644f2eb24fd37c796cf',
    'tools/integrated_learning/snapshot.py':'9212dcc0a6f5e63f07a31c86962d0e4952790fecd6f5f67e6d1b30608963730c',
    'tools/model_review_v27/objective_snapshot.py':'466880db2a4984af60afd70145a0f4f4c5363631bade12e4823db8a1037e23f9',
    'tools/eight_fault_study/runtime.py':'871bace0c3bbef947a24da53aa3f91df5f0f6ff830a6c4599f4b0b59646720e0',
    'tools/annual_program/source_runtime.py':'992d80719dd0728b047b30e847ac6ab86225035a4712c4658efc90fd831e61c2'}
SCIENTIFIC_CAPTURE_RECORDER_SHA256='67d7d5eb1871d4acb3def536770147b68aad59e0ad1ad60b753ef3c0200ed048'
SCIENTIFIC_PHYSICAL_FUNCTION_AST_SHA256='1bd00440c5be04172d4118de73974211022cef2c683548fba8da39c1aa3fa588'
SCIENTIFIC_PHYSICAL_MODULE_AST_SHA256='77ae07cad757f8cd927e6a6dfb92cc6af47684457445c86de913273a2826031b'
SCIENTIFIC_CAPTURE_LOGGING_EXPRESSION_AST_SHA256='75324423f7f8b30e9bc927b9d91783316db78be2d6e11aefbcafb0e4f95e7a20'
_LEAF_FILE='tools/operating_response/physical_balance.py'
_LEGACY_ITEMS=tuple(sorted(LEGACY_NATIVE_SOURCE_CONTRACT.items()))
_AUXILIARY_FILES=frozenset(('tools/annual_program/compiled_capture.py','tools/annual_program/compiled_flow_input.py'))
_EXACT_CACHE_HELPER_FILES=frozenset(('src/hospital_ies/solver_result_cache.py',))
_SCIENTIFIC_EXTRA_FILES=frozenset((
    'src/hospital_ies/solver_result_cache.py','tools/sci_engineering/storage.py',
    'tools/single_fault_model_20261102/__init__.py','tools/single_fault_model_20261102/storage.py',
    'tools/single_fault_model_20261102/runtime.py','tools/single_fault_model_20261102/environment.py',
    'tools/single_fault_model_20261102/runner.py','tools/integrated_learning/snapshot.py',
    'tools/model_review_v27/objective_snapshot.py','tools/eight_fault_study/runtime.py',
    'tools/annual_program/source_runtime.py'))
# BEGIN SCI_FUEL REVIEWED LITERALS
# Independent SF2 scientific candidate; exact bytes await release SPEC/QUALITY.
# These pins neither equate old physics nor authorize numerical access.
SCI_FUEL_CAPTURE_PROFILE='single_fault_pnnl_exact_fuel_capture_v1'
SCI_FUEL_NATIVE_SOURCE_CONTRACT={
    'src/hospital_ies/solver_backend.py':'979b3c92f7f0d123c49ae157ee8970e033fb1875ef1e821ab6cb72b04d44a0a3',
    'tools/model_closeout/numerics.py':'822bf480936cd184b35033eaa631e3b0a244590ef346148432ce409b1be4d3d5',
    'tools/operating_response/physical_balance.py':'2f9efdc8e3f7d5d2b8f09c3da425f2af30c0b03cd222853d2244282b64d4af0a',
    'tools/model_correctness_v23/domains.py':'2eda2c44317c563db1e20e8855f4f00437a542664639e9777167bfdf3aa496d8',
    'tools/essential_fixes/execution.py':'0d1bb1161810a49275b03fbd9c8951bb64727ef9b0f12d7658655d3a615d24f4',
    'tools/model_correction_v25/reserve.py':'9a90b67a017bdc88b84a352bffcc535dee94ed48d2e74fd2a199bc89a416ef34',
    'src/hospital_ies/solver_diagnostics.py':'30c5a757ff2242a10a0228d011acff257ca4a3f53d26dae8d70ef4db3b413990',
    'tools/model_correctness_v23/numerics.py':'d49eeac8bf099bf50466bbdf6c967f4505d3ea5820b4802209c8d97546933d76',
    'tools/execution_recovery/numerics.py':'3cd9b6b7757866154f7b80c0240803d942c30affbfa7c4792b6bbaaea47641e2',
    'tools/engineering_model/environment.py':'cf0d465bda948159eeffb7a451ddeea701602def597b8f79f11a7473a2a5bdab',
    'tools/model_closure_v22/environment.py':'3b4d6a3cbe59b9bdffb3db039cc12cb8c361f016fecd93c2512199da5fda03ea',
    'tools/model_correctness_v23/environment.py':'c388f735e59b7b276ade039503dc725b0aa7760d77b545cdb132238f5ad6b37f',
    'tools/engineering_completion_v24/environment.py':'a45426db0f1c9a205e438a50d209ea6c702ce139a8147b2d787c7fb5dd2916d7',
    'tools/engineering_completion_v24/cold_modes.py':'6add409d192ec8cc6f3b10b043c72055075a138548f6efe30682d5d10b89c34c',
}
SCI_FUEL_CAPTURE_EXTRA_SOURCE_CONTRACT={
    'configs/plant_configs.json':'cd45c357de1a54bb9d98477a082d4703a65e28b3109556fd1d6161fb1cd2f109',
    'src/hospital_ies/control_env.py':'289f34acf6073bbc338a3d097ba69df213f459f5cd70c4380b1dce8c23214364',
    'src/hospital_ies/model.py':'a6eb5d7a8e12c3cf265c5ddf9428d37106b02da3f2f6d01456256f02987c993c',
    'src/hospital_ies/nonlinear.py':'8ce7686728b0a979bfceddd745ec19d9ca5aefd47f8f11bd7d89db7bf5966f32',
    'src/hospital_ies/solver_result_cache.py':'0ecbf955523f8c7a8a2a45ab38931dbe9cf86aac5f17625e329cedd0701725e4',
    'src/hospital_ies/state_transition.py':'c77ecd4387706ef1468afd6c797308554c25b126cff7bd8481ccb24eb13ca3d6',
    'tools/annual_program/source_runtime.py':'992d80719dd0728b047b30e847ac6ab86225035a4712c4658efc90fd831e61c2',
    'tools/annual_readiness/runner.py':'4dcddde8e89096c783503f5b83d963707a5196b8e3c17c2c1a3fe45a91af7b9a',
    'tools/eight_fault_study/runtime.py':'871bace0c3bbef947a24da53aa3f91df5f0f6ff830a6c4599f4b0b59646720e0',
    'tools/integrated_learning/snapshot.py':'9212dcc0a6f5e63f07a31c86962d0e4952790fecd6f5f67e6d1b30608963730c',
    'tools/model_review_v27/objective_snapshot.py':'466880db2a4984af60afd70145a0f4f4c5363631bade12e4823db8a1037e23f9',
    'tools/operating_response/execution.py':'ec9150e840fa6c9140ff61b8837c10fc783f07988f78e74d7ffedffa359e6ec7',
    'tools/operating_response/planner.py':'53ee155f7f601afb0e88e5167be3c8b6f8a1bbd6066f08a5291e35ca78166c18',
    'tools/sci_engineering/planner.py':'d16d8cd00f6b483013717c91c90e88519de2fb693ce4c3d5b4c6a0e4bbd818cc',
    'tools/sci_engineering/storage.py':'44676255a5dd1cef3b8f847646c47700c33d6730396aa7e0e545e99c4703c0d3',
    'tools/single_fault_fuel_20261102/__init__.py':'c59e09d00ba0ffa2badda980b3b0330a92d5adf6ea7bff7b7ffec2dffb9959d3',
    'tools/single_fault_fuel_20261102/environment.py':'f19ea6d741466eb1c99412aeb441d3e34348f0a8641ef05c2281b30b21790d2a',
    'tools/single_fault_fuel_20261102/fuel.py':'5eeacd530bfb3dd3ae0b6ff14b5ece3a5d5408b83802846a6419791fb3360e24',
    'tools/single_fault_fuel_20261102/leaf.py':'e134e2628e2b2cef3030ccc5a26c066ca57f42204154e6ac0e499b38b548ecc9',
    'tools/single_fault_fuel_20261102/nl_store.py':'1d767d2398fa46533a93aed0d065bd0d0302083bb83bc61173df060ffbea54f6',
    'tools/single_fault_fuel_20261102/runner.py':'be3d660e6e4d5c6683a7d2e13004c11e2346c509f7d1f535b18f9c5486209633',
    'tools/single_fault_fuel_20261102/runtime.py':'c5012bf30b2093695e564f52f75e798d504fd7ff5c59e6c7b64e2034c68fa56d',
    'tools/single_fault_model_20261102/__init__.py':'ebc931c4233d6a689a8a211d2913f74b9b339c1e74a08e2a6afc28f984e12f94',
    'tools/single_fault_model_20261102/environment.py':'ae6477bb998171d922fd78392a6d5c7d30357f3f86dae6e343a41904938b488b',
    'tools/single_fault_model_20261102/runner.py':'bd9adfb3d0bd71b9e670a29bd903ba7be628f5ce89eba644f2eb24fd37c796cf',
    'tools/single_fault_model_20261102/runtime.py':'b569911c9364dab333ec9b933565362e4f94556ef8a6b3b08141d4a4d3978b48',
    'tools/single_fault_model_20261102/storage.py':'649595e9b77550ac5ab16c046ed63ef17d76d7f59229f99da1cb97825034279d',
}
SCI_FUEL_CAPTURE_RECORDER_SHA256='9d2e3e0a66020889b94ff1ee49208da58d5b8d1b889e9afe388fc1eaf4cef4d3'
SCI_FUEL_PHYSICAL_FUNCTION_AST_SHA256='c924bb02bb3d15fa6925940d61b7e7db02f61c7bdfaae0e44b1613dcd0568eba'
SCI_FUEL_PHYSICAL_MODULE_AST_SHA256='c7f18ab33022a24d090112b14ebd503093ca5882f2038f6aea999415cf695d8a'
SCI_FUEL_CAPTURE_LOGGING_EXPRESSION_AST_SHA256='0dbd425e7781466584c31a8edec566a22e1231b2e1c14e3e70ba9541398f9ef5'
_SCI_FUEL_EXTRA_FILES=frozenset((
    'configs/plant_configs.json',
    'src/hospital_ies/control_env.py',
    'src/hospital_ies/model.py',
    'src/hospital_ies/nonlinear.py',
    'src/hospital_ies/solver_result_cache.py',
    'src/hospital_ies/state_transition.py',
    'tools/annual_program/source_runtime.py',
    'tools/annual_readiness/runner.py',
    'tools/eight_fault_study/runtime.py',
    'tools/integrated_learning/snapshot.py',
    'tools/model_review_v27/objective_snapshot.py',
    'tools/operating_response/execution.py',
    'tools/operating_response/planner.py',
    'tools/sci_engineering/planner.py',
    'tools/sci_engineering/storage.py',
    'tools/single_fault_fuel_20261102/__init__.py',
    'tools/single_fault_fuel_20261102/environment.py',
    'tools/single_fault_fuel_20261102/fuel.py',
    'tools/single_fault_fuel_20261102/leaf.py',
    'tools/single_fault_fuel_20261102/nl_store.py',
    'tools/single_fault_fuel_20261102/runner.py',
    'tools/single_fault_fuel_20261102/runtime.py',
    'tools/single_fault_model_20261102/__init__.py',
    'tools/single_fault_model_20261102/environment.py',
    'tools/single_fault_model_20261102/runner.py',
    'tools/single_fault_model_20261102/runtime.py',
    'tools/single_fault_model_20261102/storage.py',
))
# END SCI_FUEL REVIEWED LITERALS

# Minute observation/load/runtime revision. These are schema and closure keys,
# deliberately NOT current-file hashes or a runtime self-registration path.
# The immutable external admission pin supplies the independently reviewed
# complete producer map after implementation and review have finished.
MINUTE1_CAPTURE_PROFILE='single_fault_minute1_pnnl_exact_fuel_capture_v1'
MINUTE1_ADMISSION_SCHEMA='hospital_ies.minute1.reviewed_source_admission.v1'
MINUTE1_REQUIRED_SOURCE_FILES=frozenset(SCI_FUEL_NATIVE_SOURCE_CONTRACT)|frozenset(SCI_FUEL_CAPTURE_EXTRA_SOURCE_CONTRACT)|frozenset(CAPTURE_AUXILIARY_SOURCE_CONTRACT)|frozenset((
    'tools/annual_program/witness.py','tools/annual_program/flow_source_contracts.py',
    'tools/annual_readiness/registration.py','tools/annual_readiness/environment.py',
    'src/hospital_ies/data_contract.py','tools/sci_engineering/environment.py',
    'tools/sci_engineering/service.py','tools/model_review_v28/environment.py',
    'tools/model_review_v28/demand.py','tools/model_review_v28/thermal_adapter.py',
    'tools/minute1_20261103/__init__.py','tools/minute1_20261103/profile.py',
    'tools/minute1_20261103/registration.py','tools/minute1_20261103/source.py',
    'tools/minute1_20261103/runtime.py','tools/minute1_20261103/environment.py',
    'tools/minute1_20261103/runner.py'))

# A separate reviewed actual-loop revision, not an override of any old profile.
# This literal was supplied after the bounded Essential thermal-selector patch;
# neither this registry nor a runtime reads current bytes to create approval.
MINUTE_THERMAL_CAPTURE_PROFILE='single_fault_minute1_thermal_pnnl_exact_fuel_capture_v2'
MINUTE_THERMAL_ADMISSION_SCHEMA='hospital_ies.minute1.thermal_reviewed_source_admission.v2'
MINUTE_THERMAL_NATIVE_REVISION_SCHEMA='hospital_ies.minute1.thermal_native_revision.v2'
MINUTE_THERMAL_NATIVE_REVISION_ID='essential_actual_native_minute_thermal_reference_v2'
MINUTE_THERMAL_NATIVE_OVERRIDES={
    'tools/essential_fixes/execution.py':'8eb3b210c6615a7bd72bac2c5e7c6fe495164e92f850dc78cd7d70e8e69a2309'}

_IMPORT_SOURCE=('from tools.annual_program.compiled_capture import '
    'capture_enabled as _annual_capture_enabled, flow_input_fields as _annual_flow_input_fields')
_IMPORT_AST=ast.parse(_IMPORT_SOURCE).body[0]
_FORBIDDEN_NAMES=frozenset(('state','env','core','self','row','res','locals','globals','vars','__builtins__','active_collector'))
_PLANT_ATTRIBUTES=frozenset(('abs_electric_kW_per_rt','ec_electric_kW_per_rt',
    'abs_steam_lb_per_rt','steam_kWh_per_lb','exchanger_eff'))
_OUTER_REQUIREMENTS=(
    'Verify source_root is the exact canonical producer/context/env root; declarations do not authenticate a root.',
    'Verify the actual loaded leaf and caller file/function belong to that source_root and the approved execution chain.',
    'Verify all native and auxiliary source file keys and actual bytes against the exact registered profile and BINDINGS.',
    'Verify the approved leaf module/function erasure and independently pinned logging expression; retain source drift checks.',
    'Bind the exact final result argument to interval/dt, original layer/attempt, full raw_x SHA/length and owned rows_ref.',
    'Do not select a candidate/probe/endpoint by equal values or content SHA, and do not upgrade DTO False qualification flags.')


def _hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def _source_map(value,keys):
    return type(value) is dict and set(value)==set(keys) and all(type(key) is str and _hash(item) for key,item in value.items())


def _capture_sources():
    value=CAPTURE_NATIVE_SOURCE_CONTRACT
    if not _source_map(value,dict(_LEGACY_ITEMS)):return None
    if value[_LEAF_FILE]==dict(_LEGACY_ITEMS)[_LEAF_FILE]:return None
    if any(value[name]!=pin for name,pin in _LEGACY_ITEMS if name!=_LEAF_FILE):return None
    return dict(value)


def _cache_release_sources(backend_sha256,helper_contract):
    value=_capture_sources();backend='src/hospital_ies/solver_backend.py'
    if (value is None or not _hash(backend_sha256) or backend_sha256==value[backend]
            or not _source_map(helper_contract,_EXACT_CACHE_HELPER_FILES)):
        return None
    value[backend]=backend_sha256
    return value


def _registered_cache_releases():
    """Literal release pairs only; ambiguous backend/helper declarations fail."""
    releases=[];helpers={}
    for backend,extra,recorder in (
            (EXACT_CACHE_BACKEND_SHA256,EXACT_CACHE_HELPER_SOURCE_CONTRACT,CAPTURE_RECORDER_SHA256),
            (FROZEN_A60602_EXACT_CACHE_BACKEND_SHA256,FROZEN_A60602_EXACT_CACHE_HELPER_SOURCE_CONTRACT,FROZEN_A60602_CAPTURE_RECORDER_SHA256),
            (FROZEN16_EXACT_CACHE_BACKEND_SHA256,FROZEN16_EXACT_CACHE_HELPER_SOURCE_CONTRACT,FROZEN16_CAPTURE_RECORDER_SHA256)):
        sources=_cache_release_sources(backend,extra)
        if sources is None:continue
        if backend in helpers and helpers[backend]!=extra:return ()
        helpers[backend]=extra;releases.append((sources,dict(extra),recorder))
    return tuple(releases)


def recognized_capture_native_source_contract(source_hashes):
    """Return a detached exact registered map, never authenticate a source."""
    if not _source_map(source_hashes,dict(_LEGACY_ITEMS)):return None
    for value in (_capture_sources(),*(row[0] for row in _registered_cache_releases())):
        if value is not None and source_hashes==value:return dict(value)
    return None


def capture_source_extra_contract(source_hashes):
    """Additional independently authenticated files; packet shape is unchanged."""
    for sources,extra,_ in _registered_cache_releases():
        if source_hashes==sources:return dict(extra)
    value=_capture_sources()
    return {} if value is not None and source_hashes==value else None


def recognized_scientific_native_source_contract(source_hashes):
    """Match only the entire separate scientific map, never authenticate it."""
    value=SCIENTIFIC_NATIVE_SOURCE_CONTRACT
    if (not _source_map(value,dict(_LEGACY_ITEMS)) or not _source_map(source_hashes,dict(_LEGACY_ITEMS))
            or value==dict(_LEGACY_ITEMS) or recognized_capture_native_source_contract(value) is not None):
        return None
    return dict(value) if source_hashes==value else None


def scientific_capture_source_extra_contract(source_hashes):
    """The new model's complete additional source closure, not an access grant."""
    if (recognized_scientific_native_source_contract(source_hashes) is None
            or not _source_map(SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT,_SCIENTIFIC_EXTRA_FILES)):
        return None
    return dict(SCIENTIFIC_CAPTURE_EXTRA_SOURCE_CONTRACT)


def recognized_sci_fuel_native_source_contract(source_hashes):
    """A separate complete literal scientific map; never a source grant."""
    value=SCI_FUEL_NATIVE_SOURCE_CONTRACT
    if (not _source_map(value,dict(_LEGACY_ITEMS)) or not _source_map(source_hashes,dict(_LEGACY_ITEMS))
            or value==dict(_LEGACY_ITEMS) or recognized_capture_native_source_contract(value) is not None
            or value==SCIENTIFIC_NATIVE_SOURCE_CONTRACT):return None
    return dict(value) if source_hashes==value else None


def sci_fuel_capture_source_extra_contract(source_hashes):
    if (recognized_sci_fuel_native_source_contract(source_hashes) is None
            or not _source_map(SCI_FUEL_CAPTURE_EXTRA_SOURCE_CONTRACT,_SCI_FUEL_EXTRA_FILES)):
        return None
    return dict(SCI_FUEL_CAPTURE_EXTRA_SOURCE_CONTRACT)


def _sci_fuel_registry_complete():
    return (recognized_sci_fuel_native_source_contract(SCI_FUEL_NATIVE_SOURCE_CONTRACT) is not None
        and sci_fuel_capture_source_extra_contract(SCI_FUEL_NATIVE_SOURCE_CONTRACT) is not None
        and _source_map(CAPTURE_AUXILIARY_SOURCE_CONTRACT,_AUXILIARY_FILES)
        and _hash(SCI_FUEL_CAPTURE_RECORDER_SHA256)
        and SCI_FUEL_CAPTURE_RECORDER_SHA256 not in (LEGACY_RECORDER_SHA256,SCIENTIFIC_CAPTURE_RECORDER_SHA256)
        and not _capture_recorder_registered(SCI_FUEL_CAPTURE_RECORDER_SHA256)
        and _hash(SCI_FUEL_PHYSICAL_FUNCTION_AST_SHA256) and _hash(SCI_FUEL_PHYSICAL_MODULE_AST_SHA256)
        and SCI_FUEL_PHYSICAL_FUNCTION_AST_SHA256 not in (LEGACY_PHYSICAL_FUNCTION_AST_SHA256,SCIENTIFIC_PHYSICAL_FUNCTION_AST_SHA256)
        and SCI_FUEL_PHYSICAL_MODULE_AST_SHA256 not in (LEGACY_PHYSICAL_MODULE_AST_SHA256,SCIENTIFIC_PHYSICAL_MODULE_AST_SHA256)
        and _hash(SCI_FUEL_CAPTURE_LOGGING_EXPRESSION_AST_SHA256))


def _scientific_registry_complete():
    return (recognized_scientific_native_source_contract(SCIENTIFIC_NATIVE_SOURCE_CONTRACT) is not None
        and scientific_capture_source_extra_contract(SCIENTIFIC_NATIVE_SOURCE_CONTRACT) is not None
        and _source_map(CAPTURE_AUXILIARY_SOURCE_CONTRACT,_AUXILIARY_FILES)
        and _hash(SCIENTIFIC_CAPTURE_RECORDER_SHA256)
        and SCIENTIFIC_CAPTURE_RECORDER_SHA256!=LEGACY_RECORDER_SHA256
        and not _capture_recorder_registered(SCIENTIFIC_CAPTURE_RECORDER_SHA256)
        and _hash(SCIENTIFIC_PHYSICAL_FUNCTION_AST_SHA256)
        and _hash(SCIENTIFIC_PHYSICAL_MODULE_AST_SHA256)
        and SCIENTIFIC_PHYSICAL_FUNCTION_AST_SHA256!=LEGACY_PHYSICAL_FUNCTION_AST_SHA256
        and SCIENTIFIC_PHYSICAL_MODULE_AST_SHA256!=LEGACY_PHYSICAL_MODULE_AST_SHA256
        and _hash(SCIENTIFIC_CAPTURE_LOGGING_EXPRESSION_AST_SHA256))


def _new_recorder_registered():
    return _hash(CAPTURE_RECORDER_SHA256) and CAPTURE_RECORDER_SHA256!=LEGACY_RECORDER_SHA256


def _capture_recorder_registered(value):
    return ((_new_recorder_registered() and value==CAPTURE_RECORDER_SHA256)
        or (_hash(FROZEN_A60602_CAPTURE_RECORDER_SHA256) and value==FROZEN_A60602_CAPTURE_RECORDER_SHA256)
        or (_hash(FROZEN12_CAPTURE_RECORDER_SHA256) and value==FROZEN12_CAPTURE_RECORDER_SHA256)
        or (_hash(FROZEN16_CAPTURE_RECORDER_SHA256) and value==FROZEN16_CAPTURE_RECORDER_SHA256))


def _cache_recorder_pair_approved(recorder,source_hashes):
    matches=[row for row in _registered_cache_releases() if row[0]==source_hashes]
    return not matches or any(row[2]==recorder for row in matches)


def _frozen12_capture_registered():
    sources=_capture_sources()
    return (_hash(FROZEN12_CAPTURE_RECORDER_SHA256) and sources is not None
        and sources[_LEAF_FILE]==FROZEN12_CAPTURE_LEAF_SHA256)


def _capture_registry_complete():
    return (_capture_sources() is not None
        and (_new_recorder_registered() or _frozen12_capture_registered())
        and _source_map(CAPTURE_AUXILIARY_SOURCE_CONTRACT,_AUXILIARY_FILES)
        and _hash(CAPTURE_LOGGING_EXPRESSION_AST_SHA256))


def outer_verification_requirements():
    """Return immutable requirements, not a source-authentication receipt."""
    return _OUTER_REQUIREMENTS


def minute1_source_contract(source_hashes,source_admission):
    """Match a complete external declaration; authenticate no files or review.

    Native mathematical leaf bytes must remain the exact reviewed SF2 release.
    Runtime/source extras and the recorder have their own complete identity.
    A caller must separately authenticate the pinned admission and its review.
    """
    value=source_admission
    if (type(value) is not dict or set(value)!={'schema','source_profile',
            'base_scientific_profile','source_files','recorder_sha256','review_receipt'}
            or value.get('schema')!=MINUTE1_ADMISSION_SCHEMA
            or value.get('source_profile')!=MINUTE1_CAPTURE_PROFILE
            or value.get('base_scientific_profile')!=SCI_FUEL_CAPTURE_PROFILE):return None
    files=value.get('source_files');review=value.get('review_receipt')
    if (type(files) is not dict or not MINUTE1_REQUIRED_SOURCE_FILES<=set(files)
            or not _source_map(files,files) or not _source_map(source_hashes,files)
            or source_hashes!=files or not _hash(value.get('recorder_sha256'))
            or files.get('tools/annual_program/witness.py')!=value['recorder_sha256']
            or type(review) is not dict or set(review)!={'path','sha256'}
            or type(review.get('path')) is not str or not review['path'].strip()
            or not _hash(review.get('sha256'))):return None
    if any(not path or '\\' in path or ':' in path or any(part in ('','.','..') for part in path.split('/')) for path in files):return None
    native={name:files[name] for name in SCI_FUEL_NATIVE_SOURCE_CONTRACT}
    if native!=SCI_FUEL_NATIVE_SOURCE_CONTRACT:return None
    return dict(files)


def minute_thermal_native_revision_contract():
    """Return the exact reviewed single-file revision, never approve bytes."""
    overrides=MINUTE_THERMAL_NATIVE_OVERRIDES;name='tools/essential_fixes/execution.py'
    if (not _source_map(overrides,{name}) or overrides[name]==SCI_FUEL_NATIVE_SOURCE_CONTRACT[name]):return None
    return dict(schema=MINUTE_THERMAL_NATIVE_REVISION_SCHEMA,revision_id=MINUTE_THERMAL_NATIVE_REVISION_ID,
        source_files=dict(overrides))


def minute_thermal_source_contract(source_hashes,source_admission):
    """A new complete external declaration with only the reviewed native delta.

    All other native mathematical source files retain their original exact pins.
    The independent pinned review must also bind this explicit revision contract.
    """
    value=source_admission;revision=minute_thermal_native_revision_contract()
    if (revision is None or type(value) is not dict or set(value)!={'schema','source_profile',
            'base_scientific_profile','source_files','recorder_sha256','review_receipt','native_revision_contract'}
            or value.get('schema')!=MINUTE_THERMAL_ADMISSION_SCHEMA
            or value.get('source_profile')!=MINUTE_THERMAL_CAPTURE_PROFILE
            or value.get('base_scientific_profile')!=SCI_FUEL_CAPTURE_PROFILE
            or value.get('native_revision_contract')!=revision):return None
    files=value.get('source_files');review=value.get('review_receipt')
    if (type(files) is not dict or not MINUTE1_REQUIRED_SOURCE_FILES<=set(files)
            or not _source_map(files,files) or not _source_map(source_hashes,files)
            or source_hashes!=files or not _hash(value.get('recorder_sha256'))
            or files.get('tools/annual_program/witness.py')!=value['recorder_sha256']
            or type(review) is not dict or set(review)!={'path','sha256'}
            or type(review.get('path')) is not str or not review['path'].strip()
            or not _hash(review.get('sha256'))):return None
    if any(not path or '\\' in path or ':' in path or any(part in ('','.','..') for part in path.split('/')) for path in files):return None
    native=dict(SCI_FUEL_NATIVE_SOURCE_CONTRACT);native.update(revision['source_files'])
    if any(files[name]!=pin for name,pin in native.items()):return None
    return dict(files)


def match_native_source_profile(source_hashes,*,source_admission=None):
    """Match only the entire declared source map; no files/caller are read.

    A matched source profile alone never authorizes a recorder or compiled DTO.
    """
    if source_admission is not None:
        if minute_thermal_source_contract(source_hashes,source_admission) is not None:return MINUTE_THERMAL_CAPTURE_PROFILE
        return MINUTE1_CAPTURE_PROFILE if minute1_source_contract(source_hashes,source_admission) is not None else None
    if not _source_map(source_hashes,dict(_LEGACY_ITEMS)):return None
    if source_hashes==dict(_LEGACY_ITEMS):return LEGACY_PROFILE
    if recognized_sci_fuel_native_source_contract(source_hashes) is not None:return SCI_FUEL_CAPTURE_PROFILE
    if recognized_scientific_native_source_contract(source_hashes) is not None:return SCIENTIFIC_CAPTURE_PROFILE
    return CAPTURE_PROFILE if recognized_capture_native_source_contract(source_hashes) is not None else None


def classify_recorder_source_pair(recorder_sha256,source_hashes,*,source_admission=None):
    """Pure release-pair policy, separate from actual source/execution proof.

    A registered new recorder may record old14 base witnesses but cannot grant
    compiled qualification to that old leaf. The legacy native-return recorder
    cannot attest a capture leaf. Frozen12's exact capture pair remains readable;
    all unregistered source/recorder hashes remain unsupported.
    """
    profile=match_native_source_profile(source_hashes,source_admission=source_admission);supported=compiled=False
    if profile is None:reason='unrecognized_native_source_profile'
    elif not _hash(recorder_sha256):reason='unregistered_recorder_sha256'
    elif profile==MINUTE_THERMAL_CAPTURE_PROFILE:
        supported=compiled=recorder_sha256==source_admission['recorder_sha256']
        reason='external_minute_thermal_v2_pair_requires_pinned_revision_admission_and_review' if supported else 'recorder_source_pair_not_approved'
    elif profile==MINUTE1_CAPTURE_PROFILE:
        supported=compiled=recorder_sha256==source_admission['recorder_sha256']
        reason='external_minute1_pair_requires_pinned_admission_and_review_verification' if supported else 'recorder_source_pair_not_approved'
    elif profile==SCI_FUEL_CAPTURE_PROFILE:
        if recorder_sha256!=SCI_FUEL_CAPTURE_RECORDER_SHA256:reason='recorder_source_pair_not_approved'
        elif _sci_fuel_registry_complete():
            supported=compiled=True;reason='registered_sci_fuel_capture_pair_requires_external_verification'
        else:reason='sci_fuel_capture_profile_incomplete'
    elif recorder_sha256==SCI_FUEL_CAPTURE_RECORDER_SHA256 and _sci_fuel_registry_complete():
        supported=profile==LEGACY_PROFILE
        reason='legacy_source_basic_witness_only' if supported else 'recorder_source_pair_not_approved'
    elif profile==SCIENTIFIC_CAPTURE_PROFILE:
        if recorder_sha256!=SCIENTIFIC_CAPTURE_RECORDER_SHA256:reason='recorder_source_pair_not_approved'
        elif _scientific_registry_complete():
            supported=compiled=True;reason='registered_scientific_capture_pair_requires_external_verification'
        else:reason='scientific_capture_profile_incomplete'
    elif recorder_sha256==SCIENTIFIC_CAPTURE_RECORDER_SHA256 and _scientific_registry_complete():
        supported=profile==LEGACY_PROFILE
        reason='legacy_source_basic_witness_only' if supported else 'recorder_source_pair_not_approved'
    elif recorder_sha256==LEGACY_RECORDER_SHA256:
        supported=profile==LEGACY_PROFILE
        reason='legacy_registered_pair' if supported else 'recorder_source_pair_not_approved'
    elif _capture_recorder_registered(recorder_sha256):
        if profile==LEGACY_PROFILE:
            supported=True;reason='legacy_source_basic_witness_only'
        elif recorder_sha256==FROZEN12_CAPTURE_RECORDER_SHA256 and (
                not _frozen12_capture_registered() or source_hashes!=_capture_sources()):
            reason='recorder_source_pair_not_approved'
        elif not _cache_recorder_pair_approved(recorder_sha256,source_hashes):
            reason='recorder_source_pair_not_approved'
        elif _capture_registry_complete():
            supported=compiled=True;reason='registered_capture_pair_requires_external_verification'
        else:reason='capture_profile_incomplete'
    else:reason='unregistered_recorder_sha256'
    return dict(supported=supported,basic_witness_allowed=supported,source_profile=profile,
        compiled_source_profile_allowed=compiled,reason=reason,
        source_authentication_performed=False,actual_execution_bound=False,
        requires_external_source_authentication=True,requires_exact_final_result_binding=True,
        outer_verification_requirements=_OUTER_REQUIREMENTS)


def _dump(node):return ast.dump(node,include_attributes=False)


def _ast_sha(node):return hashlib.sha256(_dump(node).encode('utf8')).hexdigest()


def _safe_value(node):
    """Permit reads/finite construction of used locals, never another query.

    Exact expression approval is still independently required. The syntax gate
    does not authenticate runtime object types, caller/source or DTO contents.
    """
    if isinstance(node,ast.Name):
        return node.id not in _FORBIDDEN_NAMES and (not node.id.startswith('__') or node.id=='__file__')
    if isinstance(node,ast.Constant):
        value=node.value
        return value is None or type(value) in (str,bool,int) or type(value) is float and math.isfinite(value)
    if isinstance(node,(ast.List,ast.Tuple)):
        return all(_safe_value(item) for item in node.elts)
    if isinstance(node,ast.Dict):
        return all(key is not None and isinstance(key,ast.Constant) and type(key.value) is str
            and _safe_value(value) for key,value in zip(node.keys,node.values))
    if isinstance(node,ast.Attribute):
        return isinstance(node.value,ast.Name) and node.value.id=='plant' and node.attr in _PLANT_ATTRIBUTES
    if isinstance(node,ast.Subscript):return _safe_value(node.value) and _safe_value(node.slice)
    if isinstance(node,ast.Slice):
        return all(value is None or _safe_value(value) for value in (node.lower,node.upper,node.step))
    if isinstance(node,ast.UnaryOp):
        return isinstance(node.op,(ast.UAdd,ast.USub)) and isinstance(node.operand,ast.Constant) and type(node.operand.value) in (int,float) and _safe_value(node.operand)
    if isinstance(node,ast.BoolOp):return isinstance(node.op,ast.Or) and all(_safe_value(value) for value in node.values)
    if isinstance(node,ast.Call):
        # Only a builtin finite dictionary constructor, without ** or calls in
        # its values. Native dict is not shadowed in the exact retained module.
        return (isinstance(node.func,ast.Name) and node.func.id=='dict' and not node.args
            and all(keyword.arg is not None and _safe_value(keyword.value) for keyword in node.keywords))
    if isinstance(node,ast.DictComp):
        if len(node.generators)!=1:return False
        generator=node.generators[0]
        if (generator.is_async or not isinstance(generator.target,ast.Name)
                or not _safe_value(ast.Name(id=generator.target.id,ctx=ast.Load()))
                or not isinstance(generator.iter,(ast.Tuple,ast.List)) or not 0<len(generator.iter.elts)<=32
                or any(not isinstance(item,ast.Constant) or type(item.value) is not str for item in generator.iter.elts)):
            return False
        if not _safe_value(node.key) or not _safe_value(node.value):return False
        return all(isinstance(test,ast.Compare) and isinstance(test.left,ast.Name)
            and test.left.id==generator.target.id and len(test.ops)==len(test.comparators)==1
            and isinstance(test.ops[0],ast.In) and _safe_value(test.comparators[0]) for test in generator.ifs)
    return False


def _logging_shape(expression):
    if not isinstance(expression,ast.IfExp):return False
    guard=expression.test;call=expression.body
    return (isinstance(guard,ast.Call) and isinstance(guard.func,ast.Name)
        and guard.func.id=='_annual_capture_enabled' and not guard.args and not guard.keywords
        and isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='_annual_flow_input_fields'
        and all(_safe_value(value) for value in call.args)
        and all(keyword.arg is not None and _safe_value(keyword.value) for keyword in call.keywords)
        and isinstance(expression.orelse,ast.Dict) and not expression.orelse.keys)


def inspect_leaf_migration(candidate_source):
    """Check only the one approved import/final conditional-unpack shape.

    Historical output-only releases must erase to the complete frozen09 AST.
    The separate SF1 release must match its own full scientific AST pins and is
    explicitly reported as non-equivalent to the old model. These approvals
    give only a source-text report. An approved match authenticates no files,
    actual caller, DTO, optimizer result or execution; outer checks remain due.
    """
    result=dict(schema='hospital_ies.annual_program.flow_source_contracts.leaf_migration.v1',
        shape_valid=False,original_function_ast_equal=False,original_module_ast_equal=False,
        original_function_ast_sha256=None,original_module_ast_sha256=None,
        logging_expression_ast_sha256=None,logging_expression_registered=False,
        candidate_source_sha256=None,design_equivalent=False,reviewed_scientific_model_change=False,
        runtime_profile_approved=False,
        source_authentication_performed=False,actual_execution_bound=False,
        requires_external_source_authentication=True,requires_exact_final_result_binding=True,
        outer_verification_requirements=_OUTER_REQUIREMENTS,reasons=[])
    if type(candidate_source) not in (str,bytes):
        result['reasons']=['unsupported_source_type'];return result
    try:raw=candidate_source.encode('utf8') if type(candidate_source) is str else candidate_source
    except UnicodeError:
        result['reasons']=['invalid_source_encoding'];return result
    result['candidate_source_sha256']=hashlib.sha256(raw).hexdigest()
    try:tree=ast.parse(raw)
    except (SyntaxError,ValueError,UnicodeError,RecursionError):
        result['reasons']=['invalid_source_syntax'];return result
    imports=[node for node in tree.body if isinstance(node,ast.ImportFrom) and _dump(node)==_dump(_IMPORT_AST)]
    leaves=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='solve_physical']
    if len(imports)!=1 or len(leaves)!=1:
        result['reasons']=['missing_or_nonunique_approved_import_or_leaf'];return result
    if result['candidate_source_sha256']==SCI_FUEL_NATIVE_SOURCE_CONTRACT.get(_LEAF_FILE):
        return _inspect_sci_fuel_leaf(tree,result)
    leaf=leaves[0]
    if (not leaf.body or not isinstance(leaf.body[-1],ast.Return)
            or not isinstance(leaf.body[-1].value,ast.Dict) or not leaf.body[-1].value.keys
            or leaf.body[-1].value.keys[-1] is not None):
        result['reasons']=['missing_unique_final_logging_unpack'];return result
    expression=leaf.body[-1].value.values[-1]
    result['logging_expression_ast_sha256']=_ast_sha(expression)
    calls=[node.func.id for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)]
    if (not _logging_shape(expression) or calls.count('_annual_capture_enabled')!=1
            or calls.count('_annual_flow_input_fields')!=1):
        result['reasons']=['unsupported_logging_expression_or_extra_query'];return result
    result['shape_valid']=True
    erased=deepcopy(tree);erased.body.remove(next(node for node in erased.body if isinstance(node,ast.ImportFrom) and _dump(node)==_dump(_IMPORT_AST)))
    old_leaf=next(node for node in erased.body if isinstance(node,ast.FunctionDef) and node.name=='solve_physical')
    old_leaf.body[-1].value.keys.pop();old_leaf.body[-1].value.values.pop()
    result['original_function_ast_sha256']=_ast_sha(old_leaf)
    result['original_module_ast_sha256']=_ast_sha(erased)
    result['original_function_ast_equal']=result['original_function_ast_sha256']==LEGACY_PHYSICAL_FUNCTION_AST_SHA256
    result['original_module_ast_equal']=result['original_module_ast_sha256']==LEGACY_PHYSICAL_MODULE_AST_SHA256
    result['design_equivalent']=result['original_function_ast_equal'] and result['original_module_ast_equal']
    if not result['design_equivalent']:
        result['reviewed_scientific_model_change']=bool(
            _hash(SCIENTIFIC_PHYSICAL_FUNCTION_AST_SHA256) and _hash(SCIENTIFIC_PHYSICAL_MODULE_AST_SHA256)
            and result['original_function_ast_sha256']==SCIENTIFIC_PHYSICAL_FUNCTION_AST_SHA256
            and result['original_module_ast_sha256']==SCIENTIFIC_PHYSICAL_MODULE_AST_SHA256
            and recognized_scientific_native_source_contract(SCIENTIFIC_NATIVE_SOURCE_CONTRACT) is not None
            and result['candidate_source_sha256']==SCIENTIFIC_NATIVE_SOURCE_CONTRACT[_LEAF_FILE])
        result['logging_expression_registered']=(_hash(SCIENTIFIC_CAPTURE_LOGGING_EXPRESSION_AST_SHA256)
            and result['logging_expression_ast_sha256']==SCIENTIFIC_CAPTURE_LOGGING_EXPRESSION_AST_SHA256)
        result['runtime_profile_approved']=bool(result['reviewed_scientific_model_change']
            and result['logging_expression_registered'] and _scientific_registry_complete())
        if result['runtime_profile_approved']:return result
        result['reasons']=['erased_complete_native_AST_differs'];return result
    result['logging_expression_registered']=(_hash(CAPTURE_LOGGING_EXPRESSION_AST_SHA256)
        and result['logging_expression_ast_sha256']==CAPTURE_LOGGING_EXPRESSION_AST_SHA256)
    sources=_capture_sources()
    result['runtime_profile_approved']=bool(_capture_registry_complete() and result['logging_expression_registered']
        and sources[_LEAF_FILE]==result['candidate_source_sha256'])
    return result


def _inspect_sci_fuel_leaf(tree,result):
    """Only the exact independently reviewed SF2 return/guard shape.

    Old release erasure rules above are unchanged. SF2 has a subsequent private
    nonactual-layout unpack; only its capture import/unpack is erased here.
    """
    leaf=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='solve_physical')
    if (not leaf.body or not isinstance(leaf.body[-1],ast.Return)
            or not isinstance(leaf.body[-1].value,ast.Dict) or len(leaf.body[-1].value.keys)<2
            or leaf.body[-1].value.keys[-2] is not None):
        result['reasons']=['unsupported_sci_fuel_capture_return_shape'];return result
    expression=leaf.body[-1].value.values[-2]
    expected_guard=ast.parse('_annual_capture_enabled() and sf2_free_indices is None',mode='eval').body
    shape=deepcopy(expression)
    if not isinstance(shape,ast.IfExp) or _dump(shape.test)!=_dump(expected_guard):
        result['reasons']=['unsupported_sci_fuel_capture_guard'];return result
    shape.test=deepcopy(expected_guard.values[0])
    calls=[node.func.id for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)]
    if (not _logging_shape(shape) or calls.count('_annual_capture_enabled')!=1 or calls.count('_annual_flow_input_fields')!=1):
        result['reasons']=['unsupported_sci_fuel_logging_expression'];return result
    result['shape_valid']=True
    result['logging_expression_ast_sha256']=_ast_sha(expression)
    erased=deepcopy(tree)
    erased.body.remove(next(node for node in erased.body if isinstance(node,ast.ImportFrom) and _dump(node)==_dump(_IMPORT_AST)))
    old_leaf=next(node for node in erased.body if isinstance(node,ast.FunctionDef) and node.name=='solve_physical')
    old_leaf.body[-1].value.keys.pop(-2);old_leaf.body[-1].value.values.pop(-2)
    result['original_function_ast_sha256']=_ast_sha(old_leaf);result['original_module_ast_sha256']=_ast_sha(erased)
    result['original_function_ast_equal']=result['original_function_ast_sha256']==LEGACY_PHYSICAL_FUNCTION_AST_SHA256
    result['original_module_ast_equal']=result['original_module_ast_sha256']==LEGACY_PHYSICAL_MODULE_AST_SHA256
    result['design_equivalent']=result['original_function_ast_equal'] and result['original_module_ast_equal']
    result['reviewed_scientific_model_change']=bool(
        result['original_function_ast_sha256']==SCI_FUEL_PHYSICAL_FUNCTION_AST_SHA256
        and result['original_module_ast_sha256']==SCI_FUEL_PHYSICAL_MODULE_AST_SHA256
        and recognized_sci_fuel_native_source_contract(SCI_FUEL_NATIVE_SOURCE_CONTRACT) is not None)
    result['logging_expression_registered']=result['logging_expression_ast_sha256']==SCI_FUEL_CAPTURE_LOGGING_EXPRESSION_AST_SHA256
    result['reviewed_scientific_source_profile']=SCI_FUEL_CAPTURE_PROFILE
    result['runtime_profile_approved']=bool(result['reviewed_scientific_model_change']
        and not result['design_equivalent'] and result['logging_expression_registered'] and _sci_fuel_registry_complete())
    if not result['runtime_profile_approved']:result['reasons']=['unregistered_sci_fuel_scientific_AST_or_release']
    return result
