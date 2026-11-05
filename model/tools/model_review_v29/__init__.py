"""Bounded mechanism acceptance; reuse the adopted V28 physical/request core."""
STUDY_ID='hospital_ies.same_version_mechanisms.v29.v1'
DEMAND_CONTRACT='hospital_ies.fixed_direct_aux.v28.v1'
CORE_REUSE={
    'scientific_producer_commit':'09f922840b7cfaef209d693ebfdda3492e16e24a',
    'source_sha256':'9cb3ea825662324cf7b718ffca7c581066506907859a5739c7d5a3c366fc18f9',
    'input_sha256':'5c6fea468008561acefd81b16d4cd57a8524f7897f861ccb436f1a32adaaeae8',
    'interpretation':'V28 adopted physics/request baseline; new entry/quality/registration has its own full source/input binding.',
    'new_control_algorithm':False,
}
ADOPTED_THERMAL_PROFILE={'files':{
    'thermal_service_components':{'path':'inputs/model_review_v28/THERMAL_SERVICE_COMPONENTS_8760.csv','sha256':'27b608272ed268c16fc6fb86ce5fbcf66e4f4f177f3ed7feefacc15c823b11f4'},
    'thermal_input_metadata':{'path':'inputs/model_review_v28/THERMAL_INPUT_METADATA.json','sha256':'d24ab6a3458ffa02e57616969ea4866f2c3a051bed3d203748e9888ac0cb9ccb'},
}}
