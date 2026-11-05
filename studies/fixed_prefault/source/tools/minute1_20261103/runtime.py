"""Minute sampling contract over unchanged SF2 fuel/storage mathematics."""
from copy import deepcopy
from dataclasses import dataclass,asdict
import math
from numbers import Real

from hospital_ies.model import illustrative_tariff
from tools.annual_program.source_runtime import AnnualSourceMixin
from tools.single_fault_model_20261102.runtime import validate_single_fault_scenario
from . import MODEL_ID,SAMPLING_CONTRACT


@dataclass(frozen=True)
class MinuteSamplingSpec:
    load_interval_minutes: float=1.
    control_interval_minutes: float=1.
    physical_max_segment_minutes: float=1.
    model_id: str=MODEL_ID
    contract: str=SAMPLING_CONTRACT

    def __post_init__(self):
        if (type(self.load_interval_minutes) is not float or self.load_interval_minutes!=1.
                or type(self.control_interval_minutes) is not float or self.control_interval_minutes!=1.
                or isinstance(self.physical_max_segment_minutes,bool)
                or not isinstance(self.physical_max_segment_minutes,Real)
                or self.physical_max_segment_minutes not in (1.,.5)
                or self.model_id!=MODEL_ID or self.contract!=SAMPLING_CONTRACT):
            raise ValueError('Explicit one-minute load/control and registered physical segmentation required')

    def as_dict(self):return asdict(self)


def validate_minute_scenario(scene):
    if (type(scene.get('step_minutes')) is not float or scene['step_minutes']!=1.
            or type(scene.get('duration_steps')) is not int or scene['duration_steps'] not in (180,240)):
        raise ValueError('Minute scenario must retain the complete registered 180/240 minute horizon')
    # Reuse the existing single-exogenous-fault restriction on a detached scope
    # description only. This is not an old canonical case or runtime admission.
    scope=deepcopy(scene);scope['step_minutes']=15.;scope['duration_steps']=scene['duration_steps']//15
    validate_single_fault_scenario(scope)


class MinuteActualCoreMixin(AnnualSourceMixin):
    """Retain the final actual hook, and the existing causal local calendar."""
    def __init__(self,env,initial_action):
        super().__init__(env,initial_action)
        self._check_minute_model()
        self.log('minute1_sampling_installed',**env.minute1_sampling_spec.as_dict(),
                 original_time_delays_retained=True,inventory_reset=False)

    def _check_minute_model(self):
        e=self.env;spec=e.minute1_sampling_spec
        if (type(spec) is not MinuteSamplingSpec or spec is not e._minute1_bound_sampling_spec
                or e.minute_source is not e._minute1_bound_source
                or e.operating_model_config.get('minute1_sampling')!=spec.as_dict()
                or e.dt_minutes!=1.):
            raise ValueError('Minute model sampling/source binding changed')

    def data_at(self,minute):
        self._check_minute_model()
        return super(AnnualSourceMixin,self).data_at(minute)

    def boundary(self):
        self._check_minute_model()
        return super(AnnualSourceMixin,self).boundary()

    def _record_error(self,*args,**kwargs):
        return super(AnnualSourceMixin,self)._record_error(*args,**kwargs)

    def _annual_error_row_context(self):
        native=getattr(super(AnnualSourceMixin,self),'_annual_error_row_context',None)
        return native() if native is not None else None

    def _annual_absolute_minute(self,minute):
        raise ValueError('Annual source endpoints are not granted by a local minute case')

    def _after_actual_segment(self,segment,result):
        self._check_minute_model()
        hour,row,_=self.env.minute_source.at_elapsed(segment['start_minute'],self.env.scenario['start_hour'])
        base=(self.env.scenario['start_hour']-1)*60.
        start=base+segment['start_minute'];end=base+segment['end_minute']
        source_start=float(row.minute_of_year-1);source_end=source_start+1.
        if not (math.isfinite(start) and math.isfinite(end) and source_start<=start<end<=source_end):
            raise ValueError('Actual segment exceeds its current native source interval')
        # Preserve the physical leaf's native timestamp price in downstream
        # accounting; the historical hour-end fallback is a different clock.
        segment['grid_tariff_USD_kWh']=illustrative_tariff(row.timestamp,self.env.plant)
        segment['minute1_source_context']=dict(schema='hospital_ies.minute1.actual_source_context.v1',
            source_manifest_schema=self.env.minute_source.spec['schema'],
            files=deepcopy(self.env.minute_source.spec['files']),minute_of_year=int(row.minute_of_year),
            source_interval_start_absolute_minute=source_start,source_interval_end_absolute_minute=source_end,
            actual_interval_start_absolute_minute=start,actual_interval_end_absolute_minute=end,
            compiled_flow_DTO_contains_this_context=False)
        segment['minute1_sampling']=dict(self.env.minute1_sampling_spec.as_dict(),
            minute_of_year=int(row.minute_of_year),hour_of_year=hour,
            source_interval_start_standard=str(row.timestamp_start_standard),
            source_interval_end_standard=str(row.timestamp_end_standard),
            actual_interval_minutes=segment['end_minute']-segment['start_minute'])
        return super()._after_actual_segment(segment,result)

    def observe(self):
        self._check_minute_model()
        result=super().observe()
        result['minute1_sampling']=dict(self.env.minute1_sampling_spec.as_dict(),
            observation_time_minute=self.time,available_time_minute=self.time,
            future_samples_exposed=False)
        return result
