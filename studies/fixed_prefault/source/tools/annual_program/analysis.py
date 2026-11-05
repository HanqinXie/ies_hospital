"""Read-only annual endpoint/accounting and independent native steam audit.

The original analyzer and all of its failures/unknowns remain visible. The steam
observer reads accepted original primal/row evidence one raw block at a time;
its result is an additional qualification, never a replacement global pass.
No environment, controller, optimizer or simulation is constructed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import gzip
import hashlib
import json
import math
from numbers import Real
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]

from tools.annual_readiness import analysis as native
from tools.annual_readiness import journal
from . import MODEL_ID
from .registration import validate_case
from . import steam
from . import witness

SCHEMA=MODEL_ID+'.offline_analysis.v1'


def _observer_bindings():
    """Identify this read-only observer separately from the simulated source."""
    paths={Path(module.__file__).resolve() for module in (native,steam,steam.native,witness)}
    paths.add(Path(__file__).resolve())
    return {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def observed_source_hours(case,observed_minutes):
    """Exact source hours intersecting the actually observed [start,end)."""
    case=validate_case(case)
    if isinstance(observed_minutes,bool) or not isinstance(observed_minutes,Real) or not math.isfinite(observed_minutes):
        raise ValueError('Finite actually observed minutes are required')
    scene=case['scenario'];requested=scene['duration_steps']*scene['step_minutes']
    if not 0.<=observed_minutes<=requested:
        raise ValueError('Observed prefix is outside the registered requested interval')
    if observed_minutes==0.:return []
    first=scene['start_hour'];end=(first-1)*60.+float(observed_minutes)
    if not 0.<end<=8760.*60.:raise ValueError('Observed prefix exceeds the real original source year')
    last=math.ceil(end/60.)
    if not 1<=first<=last<=8760:raise ValueError('Observed source hours exceed1..8760')
    return list(range(first,last+1))


def _number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def _steam_audit(output,repo,verified,*,use_actual_witness=False,steam_flow_only=False):
    totals=[];maxima=[];blocks=[];reasons=Counter();context=None;error=None
    counts=dict(segment_count=0,passed_segments=0,unknown_segments=0,failed_segments=0)
    context_status=None;final_verified=False
    try:
        context=steam.prepare_steam_context(output,source_root=repo,verified_journal=verified)
        for index,record in enumerate(context['raw_files_sha256']):
            with gzip.open(Path(output)/'evidence'/record['file'],'rt',encoding='utf8') as stream:
                block=json.load(stream)
            report=steam.audit_steam_block(context,block,block_record=record,
                use_actual_witness=use_actual_witness,steam_flow_only=steam_flow_only)
            if steam_flow_only and report.get('qualification_scope')!='algebraic_steam_flow_only':
                raise ValueError('Requested steam-only qualification was not applied by the block observer')
            if report.get('status') not in ('passed','unknown','failed'):
                raise ValueError('Unrecognized independent steam block status')
            summary=report['summary']
            for key in counts:
                value=summary[key]
                if type(value) is not int or value<0:raise ValueError('Invalid independent steam segment count')
                counts[key]+=value
            block_reasons=Counter(str(reason) for reason in report.get('reasons',[]))
            for segment in report.get('segments',[]):
                block_reasons.update(str(reason) for reason in segment.get('reasons',[]))
            reasons.update(block_reasons)
            value=summary.get('total_metered_waste_lb')
            if _number(value):totals.append(value)
            else:
                known=[row.get('metered_waste_lb') for row in report.get('segments',[])
                    if row.get('status')=='passed' and _number(row.get('metered_waste_lb'))]
                if known:totals.append(math.fsum(known))
            residual=summary.get('maximum_absolute_steam_residual_lbph')
            if _number(residual):maxima.append(residual)
            blocks.append(dict(index=index,file=record['file'],status=report['status'],
                reasons=dict(sorted(block_reasons.items())),**{key:summary[key] for key in counts}))
            del block,report
    except steam.native._Issue as exc:
        context_status=exc.status;reasons[exc.reason]+=1
        error=dict(type=type(exc).__name__,message=exc.reason)
    except FileNotFoundError as exc:
        context_status='unknown';reasons['missing_independent_steam_evidence']+=1
        error=dict(type=type(exc).__name__,message=str(exc))
    except (OSError,KeyError,IndexError,TypeError,ValueError) as exc:
        context_status='failed';reasons['invalid_independent_steam_evidence']+=1
        error=dict(type=type(exc).__name__,message=str(exc))
    finally:
        if context is not None:
            try:
                steam.verify_steam_context(context,full=True);final_verified=True
            except steam.native._Issue as exc:
                context_status='failed' if exc.status=='failed' else context_status or 'unknown'
                reasons[exc.reason]+=1;error=dict(type=type(exc).__name__,message=exc.reason)
            except (OSError,KeyError,TypeError,ValueError) as exc:
                context_status='failed';reasons['final_steam_source_or_producer_verification_failed']+=1
                error=dict(type=type(exc).__name__,message=str(exc))
    statuses=[row['status'] for row in blocks]
    status=('failed' if context_status=='failed' or 'failed' in statuses else 'unknown'
        if context_status=='unknown' or not statuses or 'unknown' in statuses else 'passed')
    known_total=math.fsum(totals) if totals else None
    complete=status=='passed' and final_verified
    expected=len(context['raw_files_sha256']) if context is not None else None
    if complete and len(blocks)!=expected:
        status='failed';complete=False;reasons['incomplete_independent_steam_block_coverage']+=1
    return dict(status=status,error=error,reasons=dict(sorted(reasons.items())),
        summary=dict(**counts,audited_block_count=len(blocks),expected_block_count=expected,
            total_metered_waste_lb=known_total if complete else None,known_metered_waste_lb=known_total,
            maximum_absolute_steam_residual_lbph=max(maxima) if complete and maxima else None,
            known_maximum_absolute_steam_residual_lbph=max(maxima) if maxima else None),
        blocks=blocks,final_source_and_producer_verification=final_verified,
        observed_prefix_only=True,whole_run_complete=(context['observation']['complete'] if context is not None else None),
        unobserved_tail_metered_waste_lb=None,imputed_from_energy_remainder=False,
        physical_meter_or_OEM_qualification=False,maximum_decoded_blocks=1,
        full_raw_trajectory_retained=False,source_root=str(Path(repo).resolve()),
        qualification_scope='algebraic_steam_flow_only' if steam_flow_only else 'accepted_native_steam_witness',
        steam_flow_only=steam_flow_only,full_startup_context_validated=False,
        electricity_validated=False,whole_system_validated=False,
        source_sha_checks=copy.deepcopy(context['source_hashes']) if context is not None else None)


def analyze_run(output,*,repo=ROOT,use_actual_witness=False,steam_flow_only=False):
    """Attach streamed accepted-primal steam evidence to unchanged native QA."""
    if type(use_actual_witness) is not bool or type(steam_flow_only) is not bool:
        raise ValueError('Explicit Boolean actual-witness and steam-only opt-ins required')
    observer_sources=_observer_bindings()
    output=Path(output).resolve();repo=Path(repo).resolve()
    case=validate_case(json.loads((output/'CASE.json').read_text(encoding='utf-8-sig'))['case'])
    result=native.analyze_run(output,repo=repo)
    original_status=result['audit_status'];original_schema=result['schema']
    hours=observed_source_hours(case,result['observation']['observed_until_minute'])
    verified=journal.verify_journal(output/'evidence')
    independent=_steam_audit(output,repo,verified,use_actual_witness=use_actual_witness,
        steam_flow_only=steam_flow_only)
    result['independent_steam']=independent
    failed=list(result['failed_checks']);unknown=list(result['unknown_checks'])
    if independent['status']=='failed':failed.append('independent_steam.native_witness_failure')
    elif independent['status']=='unknown':unknown.append('independent_steam.incomplete_native_proof')
    result['failed_checks']=sorted(set(failed));result['unknown_checks']=sorted(set(unknown))
    result['audit_status']=('failed' if failed or original_status=='failed' else 'unknown'
        if unknown or original_status=='unknown' else 'passed')
    result['schema']=SCHEMA
    result['annual_program_analysis']=dict(model_id=MODEL_ID,native_schema=original_schema,
        native_audit_status=original_status,observed_source_hours=hours,
        source_interval='[start,end)',endpoint_hour_not_added=True,canonical_annual_case_verified=True,
        native_unknowns_and_failures_preserved=True,native_steam_unknown_not_silently_replaced=True,
        use_actual_witness=use_actual_witness,
        steam_flow_only=steam_flow_only,actual_final_receipts_required=use_actual_witness or steam_flow_only,
        observer_source_sha256=observer_sources,
        simulation_rerun=False,annual_study_is_blind=False,
        qualification='Observed native accounting and accepted-primal steam witness only; no clinical, OEM, DC-energy or annual safety qualification')
    if _observer_bindings()!=observer_sources:raise ValueError('Observer source changed during read-only analysis')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--repo',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--use-actual-witness',action='store_true',help='Require new producer final-actual-primal receipts; no fallback to legacy attribution')
    parser.add_argument('--steam-flow-only',action='store_true',help='Require owned final receipts for algebraic steam accounting only; no startup/electricity qualification')
    args=parser.parse_args(argv);destination=args.output.resolve();producer=args.run.resolve()
    if destination.exists() or destination==producer or destination.is_relative_to(producer):
        raise ValueError('Analysis output must be fresh and detached from the producer')
    if os.name=='nt' and destination.drive.upper()!='E:':raise ValueError('Annual analysis output must remain on E')
    result=analyze_run(producer,repo=args.repo,use_actual_witness=args.use_actual_witness,
        steam_flow_only=args.steam_flow_only)
    destination.mkdir(parents=True,exist_ok=False)
    with (destination/'ANALYSIS.json').open('x',encoding='utf8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps(dict(output=str(destination),audit_status=result['audit_status'],
        independent_steam_status=result['independent_steam']['status'],simulation_rerun=False)))
    return 2 if result['audit_status']=='failed' else 0


if __name__=='__main__':raise SystemExit(main())
