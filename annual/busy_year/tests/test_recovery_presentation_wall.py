"""Only the authenticated recovery presentation's remaining wall budget is metadata."""
import copy
import importlib.util
from pathlib import Path

import pytest

PACKAGE=Path(__file__).resolve().parents[1]
NUMERICS='tools/execution_recovery/numerics.py'
PIN='3cd9b6b7757866154f7b80c0240803d942c30affbfa7c4792b6bbaaea47641e2'


def api():
    spec=importlib.util.spec_from_file_location('recovery_presentation_comparator',
        PACKAGE/'scripts/vendor/compare_recovery.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def context(module,tmp_path,side='left'):
    target=tmp_path/NUMERICS;target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes((PACKAGE/'source'/NUMERICS).read_bytes())
    assert module.sha(target)==PIN
    return module._recovery_wall_source_context(tmp_path,{'source':{NUMERICS:PIN}},side=side)


def attempt(budget=5.9):
    return dict(attempt=1,global_presentation_attempt=3,accepted=True,status=0,native_status=2,
        raw_x=[1.,0.],parameters={'TimeLimit':5.8},elapsed_seconds=.01,
        presentation='priority_lock_rows_positive_x1000',presentation_row_factor=1000.,
        row_scales_sha256='a'*64,remaining_original_budget_seconds=budget,
        validation={'max_original_violation':0.},raw_objective=1.)


def block(value,root='calls'):
    layer={'solver_backend_layers':[{'attempts':[value]}]}
    diagnostic=dict(joint_dispatch=dict(diagnostic=dict(m23_boiler_domains=dict(checked_domains=[layer]))))
    if root=='calls':return {'solver_calls':[dict(phase='physical',status='success',**layer)]}
    if root=='decision':return {'decision':{'command_generated':diagnostic}}
    if root=='event':return {'events':[{'command':diagnostic}]}
    if root=='terminal':return {'terminal':{'execution_state':{'active_command':diagnostic}}}
    raise ValueError(root)


def differences(module,left,right):
    stats=module.statistics();module.compare_values(left,right,'block',stats)
    return stats


@pytest.mark.parametrize('root',['calls','decision','event','terminal'])
def test_exact_known_paths_project_only_authenticated_wall_and_retain_originals(tmp_path,root):
    m=api();left=context(m,tmp_path/'l','left');right=context(m,tmp_path/'r','right')
    a=block(attempt(5.9),root);b=block(attempt(5.95),root)
    before=copy.deepcopy(a)
    pa=m.project(a,recovery_wall_context=left);pb=m.project(b,recovery_wall_context=right)
    assert differences(m,pa,pb)['difference_count']==0
    assert a==before
    for ctx,budget,side in ((left,5.9,'left'),(right,5.95,'right')):
        assert len(ctx['excluded_timing'])==1
        proof=ctx['excluded_timing'][0]
        assert proof['side']==side and proof['original_budget_seconds']==budget
        assert proof['native_time_limit_seconds']==5.8
        assert proof['source_file']==NUMERICS and proof['source_sha256']==PIN
        assert proof['path'].endswith('.remaining_original_budget_seconds')


@pytest.mark.parametrize('bad',[-1.,6.1,True,float('nan'),float('inf')])
def test_invalid_remaining_budget_is_rejected_not_ignored(tmp_path,bad):
    m=api();ctx=context(m,tmp_path)
    with pytest.raises(ValueError,match='budget'):
        m.project(block(attempt(bad)),recovery_wall_context=ctx)


def test_native_limit_cannot_exceed_original_remaining_budget(tmp_path):
    m=api();ctx=context(m,tmp_path);value=attempt();value['parameters']['TimeLimit']=5.99
    with pytest.raises(ValueError,match='budget'):
        m.project(block(value),recovery_wall_context=ctx)


@pytest.mark.parametrize('limit',[0.,True,float('nan'),float('inf')])
def test_nonpositive_or_invalid_native_limit_is_not_ignored(tmp_path,limit):
    m=api();ctx=context(m,tmp_path);value=attempt();value['parameters']['TimeLimit']=limit
    with pytest.raises(ValueError,match='budget'):
        m.project(block(value),recovery_wall_context=ctx)


@pytest.mark.parametrize('change',['presentation','factor','global_low','global_high','local_low','local_high','counter_pair','row_scale'])
def test_unknown_or_incomplete_presentation_never_qualifies_for_projection(tmp_path,change):
    m=api();ctx=context(m,tmp_path);a=attempt(5.9);b=attempt(5.95)
    for v in (a,b):
        if change=='presentation':v['presentation']='unknown_presentation'
        elif change=='factor':v['presentation_row_factor']=1.
        elif change=='global_low':v['global_presentation_attempt']=2
        elif change=='global_high':v['global_presentation_attempt']=5
        elif change=='local_low':v['attempt']=0
        elif change=='local_high':v['attempt']=3
        elif change=='counter_pair':v['global_presentation_attempt']=4
        else:v['row_scales_sha256']='not-a-sha'
    pa=m.project(block(a),recovery_wall_context=ctx);pb=m.project(block(b),recovery_wall_context=ctx)
    assert differences(m,pa,pb)['difference_count']>0
    assert ctx['excluded_timing']==[]


@pytest.mark.parametrize('path',['untrusted','physical_state','user_metadata'])
def test_same_key_in_unknown_container_remains_scientific_data(tmp_path,path):
    m=api();ctx=context(m,tmp_path)
    a={path:{'solver_backend_layers':[{'attempts':[attempt(5.9)]}]}}
    b={path:{'solver_backend_layers':[{'attempts':[attempt(5.95)]}]}}
    assert differences(m,m.project(a,recovery_wall_context=ctx),m.project(b,recovery_wall_context=ctx))['difference_count']>0
    assert ctx['excluded_timing']==[]


def test_unknown_or_missing_source_binding_does_not_enable_projection(tmp_path):
    m=api()
    for bindings in ({'source':{}},{'source':{NUMERICS:'b'*64}}):
        ctx=m._recovery_wall_source_context(tmp_path,bindings,side='left')
        assert ctx is None
        assert differences(m,m.project(block(attempt(5.9)),recovery_wall_context=ctx),
            m.project(block(attempt(5.95)),recovery_wall_context=ctx))['difference_count']>0


def test_declared_known_source_requires_matching_actual_bytes(tmp_path):
    m=api();path=tmp_path/NUMERICS;path.parent.mkdir(parents=True);path.write_bytes(b'unknown implementation')
    with pytest.raises(ValueError,match='source'):
        m._recovery_wall_source_context(tmp_path,{'source':{NUMERICS:PIN}},side='left')


def test_warmed_context_cannot_hide_a_changed_source_file(tmp_path):
    m=api();ctx=context(m,tmp_path)
    (tmp_path/NUMERICS).write_bytes(b'changed after context creation')
    with pytest.raises(ValueError,match='source'):
        m.project(block(attempt()),recovery_wall_context=ctx)


@pytest.mark.parametrize('change',['raw_x','objective','physical_elapsed','inventory','service',
    'row_scales','owned_rows','status','native_status'])
def test_raw_or_physical_difference_still_fails_with_wall_projection(tmp_path,change):
    m=api();left=context(m,tmp_path/'l','left');right=context(m,tmp_path/'r','right')
    a=block(attempt(5.9));b=block(attempt(5.95))
    if change=='raw_x':b['solver_calls'][0]['solver_backend_layers'][0]['attempts'][0]['raw_x'][0]=2.
    elif change=='objective':b['solver_calls'][0]['solver_backend_layers'][0]['attempts'][0]['raw_objective']=2.
    elif change=='physical_elapsed':a['physical']={'elapsed_seconds':1.};b['physical']={'elapsed_seconds':2.}
    elif change=='inventory':a['terminal']={'inventory':1.};b['terminal']={'inventory':2.}
    elif change=='service':a['service']={'P0_unserved':0.};b['service']={'P0_unserved':1.}
    else:
        value=b['solver_calls'][0]['solver_backend_layers'][0]['attempts'][0]
        if change=='row_scales':value['row_scales_sha256']='b'*64
        elif change=='owned_rows':value['validation']['rows_ref']={'rows_sha256':'b'*64}
        elif change=='status':value['status']=2
        else:value['native_status']=3
    assert differences(m,m.project(a,recovery_wall_context=left),m.project(b,recovery_wall_context=right))['difference_count']>0


@pytest.mark.parametrize('fake',['solver_calls[0]','decision.command_generated'])
def test_encoded_dict_key_cannot_impersonate_a_known_list_or_command_path(tmp_path,fake):
    m=api();ctx=context(m,tmp_path)
    if fake=='solver_calls[0]':
        a={fake:{'solver_backend_layers':[{'attempts':[attempt(5.9)]}]}}
        b={fake:{'solver_backend_layers':[{'attempts':[attempt(5.95)]}]}}
    else:
        a={fake:block(attempt(5.9),'decision')['decision']['command_generated']}
        b={fake:block(attempt(5.95),'decision')['decision']['command_generated']}
    assert differences(m,m.project(a,recovery_wall_context=ctx),m.project(b,recovery_wall_context=ctx))['difference_count']>0
    assert ctx['excluded_timing']==[]


def test_existing_qualification_flags_remain_separate():
    text=(PACKAGE/'scripts/vendor/compare_recovery.py').read_text('utf8')
    assert 'independent_carrier_balance_qualification=False' in text
    assert 'checkpoint_recovery_qualification=False' in text


def test_new_wall_scope_never_bypasses_original_cache_certificate_validation(tmp_path):
    m=api();ctx=context(m,tmp_path);value=attempt()
    value['result_cache_provenance']={'unrecognized_certificate':True}
    with pytest.raises(ValueError,match='cache execution'):
        m.project(block(value),recovery_wall_context=ctx)


def test_original_side_effect_authentication_remains_required(tmp_path):
    m=api();ctx=context(m,tmp_path)
    value={'terminal':{'side_effect_check':{'before_sha256':'a'*64,'after_sha256':'b'*64}}}
    with pytest.raises(ValueError,match='side_effect_check'):
        m.project(value,recovery_wall_context=ctx)


def test_original_actual_owner_may_not_select_an_equal_valued_probe():
    m=api();raw=[1.,0.];ref={'rows_sha256':'a'*64,'row_count':1}
    layer={'layer':'cost','attempts':[dict(accepted=True,status=0,raw_x=raw,validation={'rows_ref':ref})]}
    value=dict(solver_calls=[dict(phase='annual_program_actual_witness',status='success',solver_backend_layers=[layer]),
        dict(phase='equal_valued_probe',status='success',solver_backend_layers=[copy.deepcopy(layer)])],
        segments=[dict(annual_program_actual_witness=dict(status='recorded',reasons=[],solver_call_index=1,
            accepted_attempt_index=0,rows_ref=ref))])
    with pytest.raises(ValueError,match='original cost record'):
        m._actual_final_primal(value)
