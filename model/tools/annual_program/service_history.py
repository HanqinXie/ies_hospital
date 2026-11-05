"""Exact ordered reduction of completed telemetry; no policy or physics change.

Arithmetic and validation follow tools.executed_service.history.make_snapshot.
Only the last measurement and its four accumulator matrices stay in memory.
The complete physical ledger remains the producer's separate durable evidence.
"""
from __future__ import annotations

import copy

from tools.executed_service import history as native
from tools.sci_engineering.design import digest

SCHEMA = 'hospital_ies.annual_program.service_accumulator.v1'


def _thresholds(config):
    return native._thresholds(dict(solver_tol_kW=config['p0_solver_feasibility_tol_kW'],
        absolute_kW=config['p0_unserved_abs_tol_kW'],relative=config['p0_unserved_rel_tol']))


class ServiceHistoryAccumulator:
    def __init__(self,config):
        self.thresholds=_thresholds(config)
        self.segment_count=0
        self.history=dict(contract_id=native.EXECUTED_SERVICE_HISTORY_CONTRACT_ID,
            available_time_min=0.,valid_time_min=0.,history_start_min=0.,history_end_min=0.,
            groups=list(native.GROUPS),carriers=list(native.CARRIERS),
            material_thresholds=copy.deepcopy(self.thresholds),
            fraction_semantics=native.FRACTION_SEMANTICS,last_executed_segment=None,
            **{key:[[0.]*3 for _ in range(2)] for key in native.ACCUMULATORS})

    @property
    def retained_segment_count(self):
        return int(self.history['last_executed_segment'] is not None)

    def append(self,segment):
        if not isinstance(segment,dict):raise ValueError('Invalid completed executed-service segment')
        start=native._number(segment['start_minute'],'segment start')
        end=native._number(segment['end_minute'],'segment end')
        if abs(start-self.history['history_end_min'])>native.TIME_EPS or end<=start:
            raise ValueError('Executed-service segments must be completed and contiguous from zero')
        loads=native._matrix(segment['loads_kW'],'segment loads',4)[2:]
        unserved=native._matrix(segment['unserved_kW'],'segment unserved',4)[2:]
        command=native._command_id(segment['command_id'])
        value=copy.deepcopy(self.history);duration=end-start;thresholds=self.thresholds
        for p in range(2):
            for k in range(3):
                loss=unserved[p][k];demand=loads[p][k]
                fraction=loss/demand if demand>0. else 0.
                value['cumulative_ENS_kWh'][p][k]+=duration*loss/60.
                value['cumulative_equivalent_loss_minutes'][p][k]+=duration*fraction
                material_fraction=loss/demand if demand>thresholds['solver_tol_kW'] else 0.
                material=(loss>thresholds['solver_tol_kW'] and
                    (loss>=thresholds['absolute_kW'] or material_fraction>=thresholds['relative']))
                if material:
                    value['cumulative_material_minutes'][p][k]+=duration
                    value['trailing_material_minutes'][p][k]+=duration
                else:value['trailing_material_minutes'][p][k]=0.
        value['last_executed_segment']=dict(start_minute=start,end_minute=end,
            command_id=command,loads_kW=loads,unserved_kW=unserved)
        for key in ('available_time_min','valid_time_min','history_end_min'):value[key]=end
        self.history=native.validate_history(value,end)
        self.segment_count+=1

    def snapshot(self,base_snapshot):
        if not isinstance(base_snapshot,dict) or native.FORBIDDEN_SNAPSHOT_FIELDS.intersection(base_snapshot):
            raise ValueError('Expected fresh pre-action snapshot without outcomes')
        t=self.history['history_end_min']
        for key in ('decision_time_min','observation_time_min'):
            if key not in base_snapshot or abs(native._number(base_snapshot[key],key)-t)>native.TIME_EPS:
                raise ValueError('Base snapshot clock differs from completed execution')
        value=copy.deepcopy(base_snapshot)
        value['executed_service_history']=native.validate_history(self.history,t)
        return value

    def to_state(self):
        value=dict(schema=SCHEMA,segment_count=self.segment_count,
            history=copy.deepcopy(self.history),thresholds=copy.deepcopy(self.thresholds))
        return dict(value,content_sha256=digest(value))

    @classmethod
    def from_state(cls,state,*,config):
        if not isinstance(state,dict):raise ValueError('Invalid service accumulator state')
        value=copy.deepcopy(state);checksum=value.pop('content_sha256',None)
        if value.get('schema')!=SCHEMA or digest(value)!=checksum:
            raise ValueError('Service accumulator identity/integrity mismatch')
        obj=cls(config)
        if value['thresholds']!=obj.thresholds:raise ValueError('Original material thresholds changed')
        n=value['segment_count']
        if type(n) is not int or n<0:raise ValueError('Invalid service segment count')
        h=native.validate_history(value['history'],value['history']['history_end_min'])
        if h['material_thresholds']!=obj.thresholds:
            raise ValueError('Restored inner material thresholds differ from original configuration')
        if (n==0)!=(h['last_executed_segment'] is None):raise ValueError('Segment count/support mismatch')
        obj.history=h;obj.segment_count=n
        return obj
