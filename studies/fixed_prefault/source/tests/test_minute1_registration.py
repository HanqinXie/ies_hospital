"""Minute registration derives a new identity from an authenticated old case."""
import copy
import hashlib
import importlib
import json

import pytest

from tools.annual_readiness.registration import make_case as old_make,validate_case as old_validate
from tools.operating_rules_study.registration import load_registry
from tools.sci_engineering.design import digest


def api():
    try:return importlib.import_module('tools.minute1_20261103.registration')
    except ModuleNotFoundError as exc:
        if exc.name not in ('tools.minute1_20261103','tools.minute1_20261103.registration'):raise
        pytest.fail('Explicit minute-case registration is missing',pytrace=False)


@pytest.fixture(scope='module')
def registry():return load_registry()


def parent(registry,*,normal=False,grid=False):
    return old_make(registry,'b08_L4_GRID_OR_disturbed_M' if grid else 'b07_L4_GT_ER_disturbed_M',
        'M_available' if grid else 'M_ready',mode='N',start_hour=1977 if grid else 33,
        duration_minutes=240. if grid else 180.,configuration='H2',
        calendar=[] if normal else [dict(time_minute=60.,availability={'GRID' if grid else 'GT_1':0.}),
            dict(time_minute=120.,availability={'GRID' if grid else 'GT_1':1.})],demand_amplitude=0.)


def source_spec():
    return dict(schema='hospital_ies.ep_minute_source_manifest.v1',files={
        name:dict(path='/minute_source/'+name+'.csv',sha256=str(i)*64)
        for i,name in enumerate(('loads','critical_zones','thermal_components','metadata'),1)})


@pytest.mark.parametrize('grid',[False,True])
def test_new_case_has_new_clock_and_identity_but_unchanged_parent_physics(registry,grid):
    old=parent(registry,grid=grid);before=copy.deepcopy(old)
    c=api().make_case(old,registry,minute_source=source_spec(),variant='responsive')
    assert old == before and old_validate(old,registry)==old
    assert c['id'].startswith('minute1_') and c['id']!=old['id']
    assert c['scenario']['step_minutes']==1.
    assert c['scenario']['duration_steps']==(240 if grid else 180)
    assert c['scenario']['pre_event_warmup_steps']==old['scenario']['pre_event_warmup_steps']
    for name in ('response_delay_minutes','model_closure_events','initial_reserve_kWh','grid_limit_kW'):
        assert c['scenario'][name]==old['scenario'][name]
    for name in ('configuration','policy','operating_rule_id'):
        assert c[name]==old[name]
    assert 'annual_readiness' not in c and 'v28_thermal_profile' not in c
    assert c['minute1']['parent']==old
    assert c['minute1']['parent_case_sha256']==digest(old)
    assert c['minute1']['minute_source']==source_spec()
    assert api().parent_case(c,registry)==old
    assert api().validate_case(c,registry)==c


def test_unattended_identity_only_disables_new_upper_commands_after_fault(registry):
    old=parent(registry)
    response=api().make_case(old,registry,minute_source=source_spec(),variant='responsive')
    unattended=api().make_case(old,registry,minute_source=source_spec(),variant='unattended')
    assert response['id']!=unattended['id']
    assert unattended['minute1']['upper_commands_disabled_at_minute']==60.
    assert response['minute1']['upper_commands_disabled_at_minute'] is None
    assert response['scenario']['exogenous_stream_id']==unattended['scenario']['exogenous_stream_id']
    assert unattended['scenario']['model_closure_events']==old['scenario']['model_closure_events']
    assert unattended['policy']==response['policy']


def test_normal_registration_has_no_exogenous_fault(registry):
    c=api().make_case(parent(registry,normal=True),registry,minute_source=source_spec(),variant='normal')
    assert c['scenario']['model_closure_events']==[]
    assert c['minute1']['variant']=='normal'
    assert api().worker_registry(c,registry)==__import__('tools.annual_readiness.registration',fromlist=['worker_registry']).worker_registry(c['minute1']['parent'],registry)


@pytest.mark.parametrize('damage',['threshold','old_parent','clock','sourcepin','metadata','extra_event'])
def test_any_mutation_of_canonical_source_physics_or_clock_is_rejected(registry,damage):
    c=api().make_case(parent(registry),registry,minute_source=source_spec(),variant='responsive')
    if damage=='threshold':c['scenario']['grid_limit_kW']=999.
    elif damage=='old_parent':c['minute1']['parent']['scenario']['pre_event_warmup_steps']=60
    elif damage=='clock':c['scenario']['step_minutes']=15.
    elif damage=='sourcepin':c['minute1']['minute_source']['files']['loads']['sha256']='9'*64
    elif damage=='metadata':c['study_id']='hospital_ies.annual_readiness.20261030.v1'
    else:c['scenario']['model_closure_events'].append(dict(time_minute=150.,availability={'ABS':0.}))
    with pytest.raises(ValueError):api().validate_case(c,registry)


@pytest.mark.parametrize('variant',['normal','other'])
def test_fault_parent_cannot_be_relabelled_clean_normal_or_unknown_variant(registry,variant):
    with pytest.raises(ValueError):api().make_case(parent(registry),registry,minute_source=source_spec(),variant=variant)


def test_old_canonical_case_cannot_be_preedited_into_a_minute_parent(registry):
    old=parent(registry);old['scenario']['step_minutes']=1.
    with pytest.raises(ValueError):api().make_case(old,registry,minute_source=source_spec(),variant='responsive')


def test_parent_file_is_checked_without_rewriting_or_trusting_its_label(tmp_path,registry):
    old=parent(registry);path=tmp_path/'CASE.json'
    path.write_text(json.dumps({'case':old}),encoding='utf8')
    bound=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    c=api().make_case(old,registry,minute_source=source_spec(),parent_binding=bound)
    assert c['minute1']['parent_binding']==bound
    before=path.read_bytes()
    assert api().validate_case(c,registry)==c and path.read_bytes()==before
    wrong=copy.deepcopy(old);wrong['scenario']['grid_limit_kW']=999.
    path.write_text(json.dumps({'case':wrong}),encoding='utf8')
    bound['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='different canonical case'):
        api().make_case(old,registry,minute_source=source_spec(),parent_binding=bound)


def test_canonical_but_different_fault_target_is_outside_this_registered_topic(registry):
    old=old_make(registry,'b07_L4_GT_ER_disturbed_M','M_ready',mode='N',start_hour=33,
        duration_minutes=180.,configuration='H2',demand_amplitude=0.,calendar=[
            dict(time_minute=60.,availability={'ABS':0.}),
            dict(time_minute=120.,availability={'ABS':1.})])
    with pytest.raises(ValueError,match='background.*target'):
        api().make_case(old,registry,minute_source=source_spec(),variant='responsive')
