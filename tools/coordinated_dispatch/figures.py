"""Paired saved-summary figures; never open raw trajectories or physical inputs."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
import numpy as np

CLASSES=('L1','L2','L3','L4b','L4a','N2')
COLORS=dict(added='#BD5A59',avoided='#438D88',neutral='#8996A0',unknown='#C6C6C6')
WINDOWS=('primary','full')
plt.rcParams.update({'svg.fonttype':'none','font.family':'DejaVu Sans','font.size':8,
    'axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.7,
    'legend.frameon':False,'axes.titlesize':9,'axes.labelsize':8,'savefig.facecolor':'white'})


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _csv(path,rows):
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def _number(value):
    if value is None:return None
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):
        raise ValueError('Invalid saved numeric plot value')
    return float(value)


def _label(row):
    return f"D{int(row['source_day_index']):03d} {row['analysis_class']}"


def _direction(delta):
    return 'unknown' if delta is None else 'added' if delta>0 else 'avoided' if delta<0 else 'neutral'


def _load(summary_path,extra_path,group,candidate,reference,phase):
    summary,extra=map(_read,(summary_path,extra_path))
    evidence=[r for r in summary['evidence'] if r['group']==group]
    if not evidence or any(r['phase']!=phase for r in evidence):
        raise ValueError('Figure phase must match the actual selected campaign evidence')
    specs=summary['policy_specs_by_group'][group]
    if candidate==reference or not {candidate,reference}<={s['policy_id'] for s in specs}:
        raise ValueError('Two distinct actual registered policies are required')
    key=lambda r:(r['experiment_group'],r['scenario_id'],r['policy_id'])
    rows=summary['trajectory_results'];extras=extra['trajectory_results']
    if len(rows)!=summary['registered_trajectories'] or len(extras)!=extra['registered_trajectories']:
        raise ValueError('Campaign metadata denominator mismatch')
    if Counter(map(key,rows))!=Counter(map(key,extras)) or any(v!=1 for v in Counter(map(key,rows)).values()):
        raise ValueError('Main and extra campaign pair identities differ or repeat')
    by_key={key(r):r for r in rows};extra_key={key(r):r for r in extras}
    pairs=[];services=[];risk=[]
    scene_ids=sorted({r['scenario_id'] for r in rows if r['experiment_group']==group})
    for sid in scene_ids:
        c=by_key[(group,sid,candidate)];r=by_key[(group,sid,reference)]
        if (c['base_state_id']!=r['base_state_id'] or c['fixed_context_json']!=r['fixed_context_json']
                or c['analysis_class']!=r['analysis_class']):
            raise ValueError('Saved pair physical context differs')
        identity=dict(experiment_group=group,phase=phase,scenario_id=sid,root_family_id=c['root_family_id'],
            analysis_class=c['analysis_class'],source_day_index=c['source_day_index'],
            candidate_policy_id=candidate,reference_policy_id=reference)
        for window in WINDOWS:
            complete=bool(c[window+'_full_horizon'] and r[window+'_full_horizon'])
            cf,rf=c[window+'_P0_failure'],r[window+'_P0_failure']
            binary=None if cf is None or rf is None else cf-rf
            cm,rm=map(_number,(c[window+'_P0_failure_minutes'],r[window+'_P0_failure_minutes']))
            delta=None if not complete or cm is None or rm is None else cm-rm
            pair=dict(identity,window=window,complete_pair=complete,candidate_P0_failure=cf,reference_P0_failure=rf,
                P0_failure_known_delta=binary,candidate_P0_failure_minutes=cm,reference_P0_failure_minutes=rm,
                P0_failure_minutes_delta=delta,candidate_observed_until_minute=c['observed_until_minute'],
                reference_observed_until_minute=r['observed_until_minute'],candidate_status=c['status'],reference_status=r['status'])
            pairs.append(pair)
            for priority in ('P2','P3'):
                for carrier in 'ehc':
                    cv,rv=map(_number,(c[f'{window}_{priority}_{carrier}_ENS_kWh'],r[f'{window}_{priority}_{carrier}_ENS_kWh']))
                    services.append(dict(pair,priority=priority,carrier=carrier,candidate_ENS_kWh=cv,reference_ENS_kWh=rv,
                        ENS_delta_kWh=cv-rv if complete and cv is not None and rv is not None else None))
            if window=='full':
                for policy,main in ((candidate,c),(reference,r)):
                    e=extra_key[(group,sid,policy)]['full']
                    if bool(e['complete'])!=bool(main['full_full_horizon']):
                        raise ValueError('Extra and main completion flags differ')
                    p=e['observed_prefix'];rec=p['recovery'];temp=p['temperature']['P2']
                    status=('Unknown tail' if not e['complete'] else 'No P0 failure' if not rec['applicable']
                        else '30-min clean confirmed' if rec['confirmed_30min'] else 'Failure; no 30-min clean')
                    risk.append(dict(identity,policy_id=policy,full_complete=e['complete'],observed_until_minute=main['observed_until_minute'],
                        zero_electric_supply_minutes=p['gaps']['zero_electric_supply']['total_minutes'] if e['complete'] else None,
                        longest_P0_failure_minutes=p['gaps']['P0_guarantee_failure']['longest_minutes'] if e['complete'] else None,
                        P2_max_temperature_C=temp['maximum_C'] if e['complete'] and temp else None,
                        recovery_category=status,observed_confirmed_30min=rec['confirmed_30min'],
                        recurrence_after_30min_count=rec['recurrence_after_30min_count'],
                        terminal_observed_P0_failure=p['terminal_P0_failure'],P3_temperature_available=False))
    return summary,pairs,services,risk


def _title(fig,number,title,phase,candidate,reference):
    fig.suptitle(f'{number}  {title}',x=.07,ha='left',fontsize=12,fontweight='bold',y=.99)
    fig.text(.07,.945,f'{phase.upper()} | {candidate} vs {reference}',fontsize=7,color='#4E5963')


def _save(fig,out,name,source_rows,caption):
    fig.text(.07,.015,caption,fontsize=7,color='#4E5963',va='bottom')
    fig.savefig(out/(name+'.svg'),bbox_inches='tight')
    fig.savefig(out/(name+'.png'),dpi=210,bbox_inches='tight')
    _csv(out/(name+'_source.csv'),source_rows)
    plt.close(fig)
    return dict(name=name,source_rows=len(source_rows),files={p.name:dict(bytes=p.stat().st_size,sha256=_sha(p))
        for p in (out/(name+'.svg'),out/(name+'.png'),out/(name+'_source.csv'))},
        caption=caption,visual_qa='pending PNG inspection')


def _p0(pairs,out,phase,candidate,reference):
    full=[r for r in pairs if r['window']=='full']
    fig,axes=plt.subplots(2,1,figsize=(7.4,5.8),gridspec_kw={'height_ratios':[1,1.15]})
    fig.subplots_adjust(top=.85,bottom=.16,hspace=.38,left=.10,right=.98)
    _title(fig,'1','P0 consequences by mechanism',phase,candidate,reference)
    x=np.arange(len(CLASSES));labels=[];largest_count=1
    for i,cls in enumerate(CLASSES):
        rows=[r for r in full if r['analysis_class']==cls]
        if not rows:
            axes[0].text(i,.02,'Not\nregistered',ha='center',va='bottom',fontsize=7,color=COLORS['neutral'])
            labels.append(cls+'\nn=0');continue
        ds=[r['P0_failure_known_delta'] for r in rows]
        counts=[sum(v is not None and v>0 for v in ds),sum(v is not None and v<0 for v in ds),sum(v is None for v in ds)]
        largest_count=max(largest_count,*counts)
        for offset,value,color,sign in zip((-.22,0,.22),counts,(COLORS['added'],COLORS['avoided'],COLORS['unknown']),(1,-1,1)):
            axes[0].bar(i+offset,sign*value,width=.2,color=color)
        if not any(counts):axes[0].text(i,.06,'0 / 0',ha='center',va='bottom',fontsize=7,color='#4E5963')
        complete=[r for r in rows if r['P0_failure_minutes_delta'] is not None]
        labels.append(f'{cls}\nn={len(rows)}; full={len(complete)}')
        for j,row in enumerate(complete):
            jitter=(j-(len(complete)-1)/2)*min(.07,.55/max(1,len(complete)))
            val=row['P0_failure_minutes_delta']
            axes[1].scatter(i+jitter,val,s=26,color=COLORS[_direction(val)],edgecolors='white',linewidths=.4,zorder=3)
        if not complete:axes[1].text(i,0,'Unknown',ha='center',va='bottom',fontsize=7)
    for ax in axes:
        ax.axhline(0,color='#56616A',lw=.7);ax.axvline(3.5,color='#A9AFB4',ls=':',lw=.8)
        ax.set_xlim(-.55,5.55);ax.set_xticks(x,labels);ax.grid(axis='y',alpha=.14)
    axes[0].set_ylabel('Observed P0 case changes')
    axes[0].set_ylim(-largest_count*1.25,largest_count*1.5)
    axes[0].yaxis.set_major_locator(MaxNLocator(integer=True))
    handles=[Patch(facecolor=color,label=label) for color,label in ((COLORS['added'],'Added'),
        (COLORS['avoided'],'Avoided (negative)'),(COLORS['unknown'],'Binary unknown'))]
    axes[0].legend(handles=handles,ncol=3,loc='upper left',bbox_to_anchor=(0,1.2),fontsize=7)
    axes[1].set_ylabel('Candidate − reference\nP0 failure duration (min)')
    axes[1].set_xlabel('Four core classes                       L4a / normal N2 separately')
    return _save(fig,out,'FIG01_P0_CLASS',pairs,
        'Full window 0–600 min. Binary prefix positives remain known; durations require complete pairs.\n'
        'Categorical x-jitter only. Finite designed cases on seen backgrounds; no probability estimate.')


def _tradeoffs(services,out,phase,candidate,reference):
    full=[r for r in services if r['window']=='full']
    fig,axes=plt.subplots(2,3,figsize=(8.4,5.8));fig.subplots_adjust(top=.84,bottom=.17,hspace=.43,wspace=.42,left=.10,right=.98)
    _title(fig,'2','Lower-priority service tradeoffs',phase,candidate,reference)
    for i,priority in enumerate(('P2','P3')):
        for j,(carrier,label) in enumerate(zip('ehc',('Electric','Heat','Cold'))):
            ax=axes[i,j];subset=[r for r in full if r['priority']==priority and r['carrier']==carrier]
            plotted=[r for r in subset if r['P0_failure_minutes_delta'] is not None and r['ENS_delta_kWh'] is not None]
            for row in plotted:
                added=row['P0_failure_known_delta'] is not None and row['P0_failure_known_delta']>0
                direction='added' if added else _direction(row['ENS_delta_kWh'])
                ax.scatter(row['P0_failure_minutes_delta'],row['ENS_delta_kWh'],s=44 if direction=='added' else 23,
                    marker='*' if added else 'o',color=COLORS[direction],alpha=.8,edgecolors='white',linewidths=.3)
            ax.axhline(0,color='#A9AFB4',lw=.7);ax.axvline(0,color='#A9AFB4',lw=.7);ax.grid(alpha=.12)
            ax.set_title(f'{priority} {label} | {len(plotted)}/{len(subset)} complete')
            ax.set_xlabel('Δ P0 failure duration (min)');ax.set_ylabel('Δ unserved energy (kWh)')
    return _save(fig,out,'FIG02_SERVICE_TRADEOFFS',services,
        'Candidate − reference; positive values indicate higher loss. Each point is one incident.\n'
        'Teal: lower ENS; red: higher ENS. Stars: added P0 failure. Missing values stay blank, never zero. Actual ENS, not planned budgets.')


def _scatter(ax,rows,xkey,ykey,title,xlabel,ylabel,*,label_outliers=False):
    good=[r for r in rows if r.get(xkey) is not None and r.get(ykey) is not None]
    coords=Counter((r[xkey],r[ykey]) for r in good)
    for row in good:
        direction=_direction(row.get('P0_failure_known_delta',0))
        ax.scatter(row[xkey],row[ykey],s=48 if direction=='added' else 25,marker='*' if direction=='added' else 'o',
            color=COLORS[direction],alpha=.8,edgecolors='white',linewidths=.3,zorder=3)
    values=[r[k] for r in good for k in (xkey,ykey)]
    lo,hi=(min(values),max(values)) if values else (0.,1.)
    span=max(hi-lo,1.);bounds=(lo-.07*span,hi+.13*span)
    ax.plot(bounds,bounds,color='#A9AFB4',lw=.8,ls='--');ax.set_xlim(bounds);ax.set_ylim(bounds)
    ax.set_aspect('equal',adjustable='box');ax.set_title(f'{title}\n{len(good)}/{len(rows)} complete pairs')
    ax.set_xlabel(xlabel);ax.set_ylabel(ylabel);ax.grid(alpha=.12)
    for (x,y),n in coords.most_common(1):
        if n>1:ax.annotate(f'×{n}',(x,y),xytext=(5,5),textcoords='offset points',fontsize=7)
    if label_outliers:
        labels=sorted(good,key=lambda r:abs(r[ykey]-r[xkey]),reverse=True)[:2]
        for row in labels:
            if abs(row[ykey]-row[xkey])>1e-6:
                ax.annotate(_label(row),(row[xkey],row[ykey]),xytext=(4,-11),textcoords='offset points',fontsize=6)


def _fixed_state(pairs,out,phase,candidate,reference):
    fig=plt.figure(figsize=(8.,6.4));gs=fig.add_gridspec(2,2,height_ratios=[3.,1.3])
    fig.subplots_adjust(top=.84,bottom=.16,hspace=.55,wspace=.3,left=.08,right=.98)
    _title(fig,'3','Fixed versus state: paired consequences',phase,candidate,reference)
    for i,window in enumerate(WINDOWS):
        rows=[r for r in pairs if r['window']==window]
        _scatter(fig.add_subplot(gs[0,i]),rows,'reference_P0_failure_minutes','candidate_P0_failure_minutes',
            'Primary 60–420 min' if window=='primary' else 'Full 0–600 min',
            'Reference P0 failure (min)','Candidate P0 failure (min)',label_outliers=True)
    unknown=[]
    for row in pairs:
        if row['window']!='full' or row['complete_pair']:continue
        for role in ('reference','candidate'):
            if row[role+'_status']!='completed':
                unknown.append(dict(scenario_id=row['scenario_id'],case=_label(row),policy_id=row[role+'_policy_id'],
                    observed_until_minute=row[role+'_observed_until_minute'],status=row[role+'_status']))
    _csv(out/'FIG03_UNKNOWN_OBSERVATIONS.csv',unknown or [dict(scenario_id='',case='No incomplete observations',policy_id='',observed_until_minute=None,status='')])
    ax=fig.add_subplot(gs[1,:]);ax.axis('off')
    if unknown:
        shown=unknown[:8]
        table=ax.table(cellText=[[r['case'],r['policy_id'],f"{r['observed_until_minute']:g}",r['status']] for r in shown],
            colLabels=['Incomplete case','Policy','Observed min','Status'],cellLoc='left',loc='center',colWidths=[.15,.35,.15,.35])
        table.auto_set_font_size(False);table.set_fontsize(7);table.scale(1,1.3)
        ax.set_title(f'Unknown full tails: {len(unknown)} policy observations'+(' (first 8; CSV contains all)' if len(unknown)>8 else ''),loc='left',fontsize=8)
    else:ax.text(.5,.5,'No unknown full-tail observations in this pair.',ha='center',va='center')
    return _save(fig,out,'FIG03_FIXED_STATE',pairs,
        'Dashed line: equal duration. Red stars: added P0 failure; teal: avoided. Largest overlap labelled ×n.\n'
        'Incomplete durations are never plotted as zero. D denotes the original zero-based source day.')


def _risks(risk,out,phase,candidate,reference):
    fig=plt.figure(figsize=(8.4,6.));gs=fig.add_gridspec(2,3,height_ratios=[2.,1.4])
    fig.subplots_adjust(top=.84,bottom=.18,hspace=.75,wspace=.42,left=.08,right=.98)
    _title(fig,'4','Observed interruption, recovery and heat risk',phase,candidate,reference)
    by={(r['scenario_id'],r['policy_id']):r for r in risk};pairs=[]
    for sid in sorted({r['scenario_id'] for r in risk}):
        c=by[(sid,candidate)];r=by[(sid,reference)]
        pair={k:c[k] for k in ('scenario_id','source_day_index','analysis_class')}
        for field in ('zero_electric_supply_minutes','longest_P0_failure_minutes','P2_max_temperature_C'):
            pair['candidate_'+field]=c[field];pair['reference_'+field]=r[field]
        pairs.append(pair)
    for i,(field,label,unit) in enumerate((('zero_electric_supply_minutes','Zero electric supply','min'),
            ('longest_P0_failure_minutes','Longest P0 failure','min'),('P2_max_temperature_C','P2 internal temperature','°C'))):
        _scatter(fig.add_subplot(gs[0,i]),pairs,'reference_'+field,'candidate_'+field,label,'Reference ('+unit+')','Candidate ('+unit+')')
    ax=fig.add_subplot(gs[1,:]);categories=('No P0 failure','30-min clean confirmed','Failure; no 30-min clean','Unknown tail')
    counts={pid:Counter(r['recovery_category'] for r in risk if r['policy_id']==pid) for pid in (reference,candidate)}
    xs=np.arange(4)
    ax.bar(xs-.17,[counts[reference][k] for k in categories],width=.32,color='#9AA6AE',label=reference)
    ax.bar(xs+.17,[counts[candidate][k] for k in categories],width=.32,color='#438D88',label=candidate)
    ax.set_xticks(xs,['No P0\nfailure','30-min clean\nconfirmed','Failure; no\n30-min clean','Unknown\ntail'])
    ax.set_ylabel('Registered incidents');ax.legend(ncol=2,fontsize=7,loc='upper left',bbox_to_anchor=(0,1.32))
    ax.grid(axis='y',alpha=.12)
    return _save(fig,out,'FIG04_INTERRUPTION_RECOVERY_HEAT',risk,
        'Full-window paired points require complete observations; the recovery chart retains every registered incident.\n'
        'Unknown tail takes precedence over prefix recovery. Research 30-min description; ideal internal P2 model; P3 temperature unavailable.')


def build_figures(summary_path,extra_path,out_dir,*,group,phase,candidate,reference):
    summary_path,extra_path=map(Path,(summary_path,extra_path));out=Path(out_dir)
    if out.exists():raise FileExistsError('Use a new phase-labelled figure directory; retain prior exports')
    summary,pairs,services,risk=_load(summary_path,extra_path,group,candidate,reference,phase)
    out.mkdir(parents=True,exist_ok=False)
    artifacts=[_p0(pairs,out,phase,candidate,reference),_tradeoffs(services,out,phase,candidate,reference),
        _fixed_state(pairs,out,phase,candidate,reference),_risks(risk,out,phase,candidate,reference)]
    full=[r for r in pairs if r['window']=='full']
    index=dict(schema='coordinated_dispatch.saved_summary_figures.v1',phase=phase,experiment_group=group,
        candidate_policy_id=candidate,reference_policy_id=reference,registered_pairwise_incidents=len(full),
        complete_full_pairs=sum(r['complete_pair'] for r in full),unknown_full_pairs=sum(not r['complete_pair'] for r in full),
        inputs={str(p.resolve()):dict(sha256=_sha(p),bytes=p.stat().st_size) for p in (summary_path,extra_path)},
        builder_sha256=_sha(__file__),svg_fonttype='none',raw_files_opened=0,physical_episodes_started=0,
        original_final12_opened=False,strict_final_gate='not_verified',independent_hospital_claim=False,
        source_campaign_registered_trajectories=summary['registered_trajectories'],artifacts=artifacts,
        unknown_table_sha256=_sha(out/'FIG03_UNKNOWN_OBSERVATIONS.csv'),visual_qa_passed=None)
    with (out/'FIGURE_INDEX.json').open('x',encoding='utf-8') as stream:
        json.dump(index,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return index


def build_handoff_figure(source_dir,out_dir):
    """Plot only supplied checkpoint markers and explicitly extracted events.

    There is no interpolation between checkpoints or across the two time cuts.
    Source CSVs are checked against their upstream extraction, not reopened raw.
    """
    source=Path(source_dir);out=Path(out_dir)
    if out.exists():raise FileExistsError('Use a new development handoff figure directory')
    receipt=_read(source/'EXTRACTED.json')
    if receipt['schema']!='coordinated_dispatch.targeted_day47_narrow_extraction.v1':
        raise ValueError('Expected the actual bounded day47 extraction')
    pids=('state_coordinated_v12','fixed_bridge_no_handoff_v12')
    if set(receipt['policies'])!=set(pids):raise ValueError('Unexpected day47 policy panel')
    if len({r['initial_state_sha256'] for r in receipt['policies'].values()})!=1 or len(
            {r['exogenous_stream_id'] for r in receipt['policies'].values()})!=1:
        raise ValueError('Extracted policies differ in physical initial state or exogenous stream')
    actual=[];plans=[]
    for filename,key,destination in (('ACTUAL_CHECKPOINTS.csv','segment_checkpoints',actual),
                                     ('PLANNED_CHECKPOINTS.csv','plans',plans)):
        with (source/filename).open(encoding='utf-8-sig',newline='') as stream:csv_rows=list(csv.DictReader(stream))
        originals=[r for pid in pids for r in receipt['policies'][pid][key]]
        if len(csv_rows)!=len(originals):raise ValueError('Narrow CSV count differs from extraction')
        timekey='checkpoint_minute' if key=='segment_checkpoints' else 'generated_minute'
        by={(r['policy_id'],r[timekey]):r for r in originals}
        if len(by)!=len(originals):raise ValueError('Duplicate extracted checkpoint')
        for row in csv_rows:
            original=by[(row['policy_id'],float(row[timekey]))]
            if set(row)!=set(original):raise ValueError('Narrow CSV fields differ from extraction')
            for field,value in original.items():
                text=row[field]
                decoded=(json.loads(text) if isinstance(value,(dict,list)) else float(text)
                    if type(value) in (int,float) else text=='True' if type(value) is bool else None if value is None and text=='' else text)
                if decoded!=value:raise ValueError('Narrow CSV content differs from original extraction: '+field)
            destination.append(original)
    fig,axes=plt.subplots(3,2,figsize=(9.,7.8),gridspec_kw={'height_ratios':[1.5,1.3,1.]})
    fig.subplots_adjust(top=.84,bottom=.23,left=.10,right=.98,hspace=.48,wspace=.20)
    _title(fig,'5','Day47: actual handoff checkpoints','development',pids[0],pids[1])
    ranges=((150.,195.),(315.,360.));data=[]
    sources={'GT total':'#657985','GRID used':'#5087A5','DG used':'#C39345'}
    service_colors={'P0':'#BD5A59','P2':'#8B7CA8','P3':'#7CA29D'}
    event_markers={'command_arrived':('v',.03),'command_executed':('o',.17),'command_superseded':('x',.30)}
    for col,(left,right) in enumerate(ranges):
        for pid_index,pid in enumerate(pids):
            marker='o' if pid_index==0 else 's';face=True if pid_index==0 else False
            selected=[r for r in actual if r['policy_id']==pid and left<=r['checkpoint_minute']<=right]
            for row in selected:
                t=row['checkpoint_minute']
                values={'GT total':sum(row['actual_GT_kW']),'GRID used':row['actual_GRID_kW'],'DG used':row['actual_DIESEL_kW']}
                for name,value in values.items():
                    color=sources[name]
                    axes[0,col].scatter(t,value,marker=marker,s=24 if face else 44,facecolors=color if face else 'none',edgecolors=color,lw=.8,zorder=3)
                    data.append(dict(record_type='actual_checkpoint',policy_id=pid,time_minute=t,
                        quantity=name,value=value,unit='kW',command_id=row['command_id'],
                        provided_segment_start=row['segment_start'],provided_segment_end=row['segment_end']))
                for priority,color in service_colors.items():
                    value=row[priority+'_unserved_kW'][0]
                    axes[1,col].scatter(t,value,marker=marker,s=24 if face else 44,facecolors=color if face else 'none',edgecolors=color,lw=.8,zorder=3)
                    data.append(dict(record_type='actual_checkpoint',policy_id=pid,time_minute=t,
                        quantity=priority+' electric unserved',value=value,unit='kW',command_id=row['command_id']))
            for row in plans:
                if row['policy_id']!=pid:continue
                t=row['generated_minute'];eta=row['planned_arrival_minute']
                if left<=t<=right:
                    axes[0,col].scatter(t,row['planned_action']['grid_import_kW'],marker='D',s=24,facecolors='none',edgecolors=sources['GRID used'],lw=.8)
                    axes[2,col].scatter(t,pid_index-.18,marker='|',s=50,color='#555E65')
                    data.append(dict(record_type='generated_plan',policy_id=pid,time_minute=t,quantity='issued GRID ceiling',
                        value=row['planned_action']['grid_import_kW'],unit='kW',command_id=row['command_id'],planned_arrival_minute=eta))
                if left<=eta<=right:
                    axes[2,col].scatter(eta,pid_index-.06,marker='>',s=24,facecolors='none',edgecolors='#8996A0')
                    data.append(dict(record_type='planned_ETA',policy_id=pid,time_minute=eta,
                        quantity='prior plan ETA; not actual arrival',command_id=row['command_id'],generated_minute=t))
            for event in receipt['policies'][pid]['events']:
                t=event.get('time_minute');kind=event['kind']
                if t is None or not left<=t<=right:continue
                if kind in event_markers:
                    marker,offset=event_markers[kind]
                    axes[2,col].scatter(t,pid_index+offset,marker=marker,s=26,color='#438D88' if pid_index==0 else '#8996A0')
                    data.append(dict(record_type='observed_event',policy_id=pid,time_minute=t,quantity=kind,command_id=event.get('command_id')))
            for interval in receipt['policies'][pid]['P0_intervals']:
                a,b=max(left,interval['start_minute']),min(right,interval['end_minute'])
                if b>a:
                    if pid_index==0:axes[1,col].axvspan(a,b,color='#F4DFDC',zorder=0)
                    data.append(dict(record_type='observed_failure_interval',policy_id=pid,time_minute=a,
                        quantity='P0 guarantee failure',interval_end_minute=b,unit='min'))
        axes[0,col].set_title(f'{left:g}–{right:g} min | checkpoint markers only')
        for row in range(3):
            ax=axes[row,col];ax.set_xlim(left-1,right+1);ax.grid(alpha=.12)
            ax.tick_params(axis='x',labelsize=7)
        axes[2,col].set_yticks([0,1],['State','No handoff']);axes[2,col].set_ylim(-.4,1.5)
        axes[2,col].set_xlabel('Episode time (min)')
    axes[0,0].set_ylabel('Actual electric ports /\nissued GRID ceiling (kW)')
    axes[1,0].set_ylabel('Actual unserved\nelectric power (kW)')
    axes[2,0].set_ylabel('Original command queue')
    axes[0,0].legend(handles=[Line2D([],[],marker='o',ls='none',color=color,label=name) for name,color in sources.items()]+
        [Line2D([],[],marker='D',ls='none',mfc='none',color=sources['GRID used'],label='Issued GRID ceiling')],
        ncol=4,fontsize=7,loc='upper left',bbox_to_anchor=(0,1.32))
    axes[1,0].legend(handles=[Line2D([],[],marker='o',ls='none',color=color,label=p+' unserved') for p,color in service_colors.items()],
        ncol=3,fontsize=7,loc='upper left',bbox_to_anchor=(0,1.25))
    handles=[Line2D([],[],marker=marker,ls='none',color='#657985',mfc='none' if marker in ('s','>') else '#657985',label=label)
        for marker,label in (('o','Filled actual: state'),('s','Open actual: no handoff'),('|','Generated'),('>','Planned ETA'),('v','Actual arrival'),('o','Actual execution'),('x','Superseded'))]
    fig.legend(handles=handles,ncol=4,fontsize=7,loc='lower left',bbox_to_anchor=(.09,.12))
    out.mkdir(parents=True,exist_ok=False)
    artifact=_save(fig,out,'FIG05_DAY47_HANDOFF',data,
        'Markers are provided checkpoints, not an interpolated trajectory; no data bridge joins the two time cuts.\n'
        'P0 observed failure is shaded. P2/P3 shortfall can be partial, not total outage. No temperature values were supplied.\n'
        'Issued ceilings / ETAs are not delivered power / actual arrival. The two policies differ in more than one option; descriptive comparison only.')
    paths=[source/name for name in ('ACTUAL_CHECKPOINTS.csv','PLANNED_CHECKPOINTS.csv','EXTRACTED.json')]
    index=dict(schema='coordinated_dispatch.narrow_handoff_figure.v1',phase='development',
        scenario_id=receipt['scenario_id'],policies=list(pids),builder_sha256=_sha(__file__),
        inputs={str(p.resolve()):dict(sha256=_sha(p),bytes=p.stat().st_size) for p in paths},
        upstream_raw_sha256={pid:r['raw_sha256'] for pid,r in receipt['policies'].items()},
        upstream_original_raw_reads=sum(r['compressed_reads'] for r in receipt['policies'].values()),
        raw_files_opened_by_figure=0,physical_episodes_started=0,temperature_available=False,
        checkpoint_interpolation=False,independent_B_effect_claim=False,artifacts=[artifact],visual_qa_passed=None)
    with (out/'FIGURE_INDEX.json').open('x',encoding='utf-8') as stream:json.dump(index,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return index


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path);parser.add_argument('--extra',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--group')
    parser.add_argument('--handoff-source',type=Path)
    parser.add_argument('--phase',choices=('development','confirmation'),required=True)
    parser.add_argument('--candidate',default='state_coordinated_v12');parser.add_argument('--reference',default='fixed_bridge_v12')
    args=parser.parse_args()
    if args.handoff_source:
        if args.phase!='development':raise ValueError('The supplied day47 extraction is development only')
        value=build_handoff_figure(args.handoff_source,args.output)
        print(json.dumps(dict(phase=value['phase'],scenario_id=value['scenario_id'],raw_files_opened=0)))
        return
    if args.summary is None or args.extra is None or args.group is None:
        parser.error('Campaign figures require --summary, --extra and --group')
    value=build_figures(args.summary,args.extra,args.output,group=args.group,phase=args.phase,candidate=args.candidate,reference=args.reference)
    print(json.dumps({k:value[k] for k in ('phase','registered_pairwise_incidents','complete_full_pairs','unknown_full_pairs')}))


if __name__=='__main__':main()
