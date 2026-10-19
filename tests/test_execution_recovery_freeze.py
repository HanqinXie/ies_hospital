"""A filename or an old passing command cannot authorize a final comparison."""
import copy
import importlib
import pytest

def api():
    return importlib.import_module('tools.execution_recovery.runner')

def frozen():
    m=api();spec=m.policy_specs()['C']
    f=dict(kind='v18_final_shared_model_freeze',source_sha256={'source.py':'a'*64},
        model_id='M18_execution_recovery',model_config={'execution_recovery':{'local_contact_restore_delay_minutes':0.}},
        policies={'C':spec})
    f['signature']=m.digest(f)
    return f,spec

def test_final_comparison_requires_exact_freeze_signature_source_config_and_policy():
    m=api();f,spec=frozen()
    m.validate_final_freeze(f,f['source_sha256'],f['model_id'],f['model_config'],[spec])
    for which in ('signature','source','config','policy'):
        changed=copy.deepcopy(f);binding=copy.deepcopy(f['source_sha256']);config=copy.deepcopy(f['model_config']);specs=[copy.deepcopy(spec)]
        if which=='signature':changed['signature']='bad'
        if which=='source':binding['source.py']='b'*64
        if which=='config':config['execution_recovery']['local_contact_restore_delay_minutes']=.25
        if which=='policy':specs[0]['config']['support_minutes']=120.
        with pytest.raises(ValueError,match='freeze|frozen|sensitivity'):
            m.validate_final_freeze(changed,binding,f['model_id'],config,specs)

def test_assumption_change_requires_signed_source_bound_preregistration():
    m=api();f,spec=frozen();config=copy.deepcopy(f['model_config']);config['execution_recovery']['local_contact_restore_delay_minutes']=.25
    reg=dict(schema='hospital_ies.v18.assumption_preregistration.v1',source_freeze_signature=f['signature'],
        source_sha256=f['source_sha256'],model_id=f['model_id'],model_config=config,policy_specs=[spec],
        reason='Finite local contact timing sensitivity')
    reg['signature']=m.digest(reg)
    m.validate_final_freeze(f,f['source_sha256'],f['model_id'],config,[spec],sensitivity=reg)
    bad=copy.deepcopy(reg);bad['model_config']['capacity']=999999.;bad.pop('signature');bad['signature']=m.digest(bad)
    with pytest.raises(ValueError,match='sensitivity|configuration'):
        m.validate_final_freeze(f,f['source_sha256'],f['model_id'],bad['model_config'],[spec],sensitivity=bad)

def test_freeze_rejects_passing_test_gate_from_other_source():
    module=importlib.import_module('tools.execution_recovery.freeze')
    gate=dict(passed=True,exit_code=0,source_before={'x':'a'},source_after={'x':'a'},
        test_files_sha256={},test_files_after_sha256={})
    module.validate_test_gate(gate,{'x':'a'},{})
    with pytest.raises(ValueError,match='source|gate'):
        module.validate_test_gate(gate,{'x':'b'},{})
