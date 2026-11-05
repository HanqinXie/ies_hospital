from tools.integrated_experiment.execution_analysis import command_timing


def payload(executed=135., arrived=135.):
    cmd={'id':3,'generated_minute':120.,'arrival_minute':135.,'dispatch':{'gt_electric_kW':[1.,2.,3.]}}
    events=[{'kind':'command_arrived','command_id':3,'time_minute':arrived}]
    if executed is not None:
        events.append({'kind':'command_executed','command_id':3,'time_minute':executed,
                       'executed_dispatch':{'gt_electric_kW':[1.,2.,2.]}})
    return {'decisions':[{'decision_time_min':120.,'command_generated':cmd}], 'events':events}


def test_use_actual_queue_arrival_not_full_delay_per_alarm():
    result=command_timing(payload(),120.,[140.,150.])
    assert result['planned_arrival_minute']==135.
    assert result['actual_execution_relation']=='before_entire_first_interval'
    assert result['GT_arrival_revalidation_delta_kW']==[0.,0.,-1.]


def test_arrival_inside_first_interval_is_not_early_rescue():
    assert command_timing(payload(),120.,[130.,150.])['actual_execution_relation']=='within_first_interval'


def test_superseded_command_and_unobserved_request_are_not_fabricated():
    assert command_timing(payload(executed=None),120.,[130.,150.])['actual_execution_relation']=='arrived_but_not_executed'
    assert command_timing({'decisions':[], 'events':[]},120.,[130.,150.])['actual_execution_relation']=='request_not_observed'
