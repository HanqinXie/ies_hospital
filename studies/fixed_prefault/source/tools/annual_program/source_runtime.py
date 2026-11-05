"""Annual-only source boundary and checked reset; no new physical capability."""
from __future__ import annotations

import copy
import math

from tools.sci_engineering.design import digest
from .registration import validate_case,required_hours
from .data_contract import YEAR_MINUTES


def validate_reset_scene(env,scenario):
    """The real reset scene must be the exact scene whose data access was frozen.

    Input permission/code hashes were independently checked by the loader before
    numeric parsing. The reset may not substitute a different condition/calendar.
    M23 deliberately stores the availability calendar separately before SCI reset.
    """
    context=getattr(env,'annual_program_data_context',None)
    if not isinstance(context,dict):raise ValueError('Explicit annual data context required')
    case=validate_case(context['case']);expected=copy.deepcopy(case['scenario'])
    calendar=expected.pop('model_closure_events',[])
    receipt=getattr(env,'input_access_receipt',{}).get('annual_program_access',{})
    if receipt.get('case_sha256')!=digest(case):raise ValueError('Annual input access is not bound to the actual case')
    if digest(scenario)!=digest(expected) or digest(getattr(env,'model_closure_events',[]))!=digest(calendar):
        raise ValueError('Actual reset scene/calendar differs from frozen annual case')
    required=required_hours(case)
    if not required<=env.allowed_hours:raise ValueError('Annual scenario exceeds the checked source grant')
    return required


class AnnualSourceMixin:
    """Stop at the source end without settling a nonexistent following interval."""
    def _after_actual_segment(self,segment,result):
        # All original physical commits run first. The observer receives this
        # exact final accepted result; it does not reconstruct/solve a state.
        from .witness import record_actual_witness
        returned=super()._after_actual_segment(segment,result)
        record_actual_witness(self,segment,result)
        return returned

    def _record_error(self,*args,**kwargs):
        # Preserve the pinned native error body while avoiding a full-year
        # deepcopy of disk-backed history on a late physical failure.
        from .failure_history import record_error_with_history_refs
        return record_error_with_history_refs(self,*args,**kwargs)

    def _annual_error_row_context(self):
        """Name the last actual source interval only at the exact annual end.

        This is diagnostic metadata, never a physical query or an endpoint
        clamp. Ordinary native errors continue to query their current row.
        """
        absolute=self._annual_absolute_minute(self.time)
        if absolute<YEAR_MINUTES:return None
        if absolute!=YEAR_MINUTES:raise ValueError('Diagnostic clock exceeds the annual terminal endpoint')
        try:
            last=self.segments[-1]
            start,end=last['start_minute'],last['end_minute']
            if (not isinstance(last,dict) or type(start) not in (int,float)
                    or type(end) not in (int,float) or not math.isfinite(start)
                    or not math.isfinite(end) or not 0.<=start<end or end!=self.time):
                raise ValueError('Last completed actual interval does not end at the diagnostic clock')
            beginning=self._annual_absolute_minute(start)
            if not 0.<=beginning<YEAR_MINUTES:
                raise ValueError('Last completed actual interval lacks original annual source support')
        except (AttributeError,KeyError,IndexError,TypeError) as exc:
            raise ValueError('Last completed actual interval is missing or malformed') from exc
        return dict(schema='hospital_ies.annual_program.terminal_diagnostic_row.v1',
            source='last_completed_actual_interval',query_minute=start,
            actual_error_time_minute=self.time,source_interval_start_absolute_minute=beginning,
            source_interval_end_absolute_minute=absolute,physical_source_query_at_terminal=False)

    def _annual_absolute_minute(self,minute):
        if isinstance(minute,bool) or not isinstance(minute,(int,float)) or not math.isfinite(minute):
            raise ValueError('Finite physical source clock required')
        return (self.env.scenario['start_hour']-1)*60.+minute

    def data_at(self,minute):
        absolute=self._annual_absolute_minute(minute)
        # Match the original boundary arithmetic, but reject rather than clamp.
        hour=int(self.env.scenario['start_hour'])+int(math.floor((minute+1e-9)/60.))
        if not 0.<=absolute<YEAR_MINUTES or not 1<=hour<=8760:
            raise ValueError('No physical source query at/beyond the annual terminal boundary')
        return super().data_at(minute)

    def boundary(self):
        absolute=self._annual_absolute_minute(self.time)
        if absolute==YEAR_MINUTES:return None
        if not 0.<=absolute<YEAR_MINUTES:raise ValueError('Physical boundary exceeds annual input support')
        return super().boundary()

    def _annual_response_scene(self):
        # M23 removes the calendar from the native parser scene and stores it
        # on the environment. Hardware already consumes that detached calendar.
        # The simulator's existing response clock must consume the same events.
        scene=dict(self.env.scenario)
        scene['model_closure_events']=copy.deepcopy(getattr(self.env,'model_closure_events',[]))
        return scene

    def delayed_at(self,minute):
        from tools.operating_rules_study.environment import response_windows
        for start,end in response_windows(self._annual_response_scene(),
                self.env.study_clock_contract,observed_until=minute):
            if start<=minute<end:return True,end
        return False,minute

    def _next_boundary(self,end):
        from tools.operating_rules_study.environment import response_windows
        boundary=super()._next_boundary(end)
        future=[value for pair in response_windows(self._annual_response_scene(),
            self.env.study_clock_contract,merge=False) for value in pair if value>self.time+1e-9]
        return min([boundary]+future)
