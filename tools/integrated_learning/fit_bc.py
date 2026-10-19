"""Hospital BC entry point: audited same-core data first, finite fits second."""
from __future__ import annotations
import json
import argparse
from collections import Counter
from pathlib import Path
import time
import traceback
import numpy as np
from tools.p2b_learning.risk import SequencePreprocessor,payload_digest
from .bc import build_teacher_records,split_teacher_records,cap_training_rows
from .models import REGISTRY,SEEDS,fit_candidate,sha,write

PRIMARY_TEACHER='secure_milp'


def bind_and_extract(manifest,audit,payloads,dataset_root):
    """Stream raw checkpoints, then retain only explicitly separate teacher rows."""
    root=Path(dataset_root).resolve();specs={s['id']:s for s in manifest['scenarios']}
    if audit.get('passed') is not True or audit.get('complete_budget') is not True or audit.get('manifest_signature')!=manifest['signature']:
        raise ValueError('BC requires actual complete same-manifest audit')
    allkeys={(sid,p) for sid in specs for p in manifest['policies']}
    entries=audit['checkpoint_digests'];keys=[(e['scenario_id'],e['policy_id']) for e in entries]
    if len(keys)!=len(allkeys) or set(keys)!=allkeys:raise ValueError('Audit checkpoint pair coverage missing or duplicate')
    indexed=dict(zip(keys,entries));expected={k for k in allkeys if specs[k[0]]['experiment_role'] in ('pilot_train','pilot_validation')}
    rows=[];seen=set();bindings=[];counts=Counter()
    for payload in payloads:
        spec=payload['scenario'];sid=spec['id'];ident=payload.get('identity');summary=payload['summary']
        policy=(ident or summary)['policy_id'];key=sid,policy
        if key not in expected or key in seen:raise ValueError('Unowned, frozen evaluation, or duplicate BC pair')
        seen.add(key);entry=indexed[key]
        if spec!=specs[sid]:raise ValueError('Raw BC scenario differs from frozen specification')
        if summary.get('scenario_id')!=sid or summary.get('policy_id')!=policy:raise ValueError('Raw BC summary identity differs')
        empty_error=not payload.get('decisions') and summary['status'] in ('orchestration_error','interrupted_unknown')
        if ident is None:
            if not empty_error:raise ValueError('Raw BC trajectory identity missing')
            counts['empty_unusable_error_payloads']+=1
        else:
            required=dict(manifest['splits'][sid],scenario_id=sid,policy_id=policy,continuation_policy_id=policy,
                manifest_signature=manifest['signature'])
            for field in ('execution_contract_id','assumption_set_id','simulator_hash','schemas'):required[field]=manifest[field]
            for field in ('family_id','parent_template_id','base_state_id','exogenous_stream_id','distribution_id'):
                if field in spec:required[field]=spec[field]
            for obj in [ident]+payload.get('decisions',[]):
                if any(obj.get(k)!=v for k,v in required.items()):raise ValueError('Raw BC identity/group/role differs from frozen manifest')
        if payload.get('seed')!=manifest['options']['seed'] and not(empty_error and 'seed' not in payload):
            raise ValueError('BC initializer seed differs')
        actual_digest=payload_digest(payload)
        if actual_digest!=entry['payload_sha256']:raise ValueError('Raw canonical checkpoint digest mismatch')
        path=Path(entry['path']);path=path if path.is_absolute() else root/path
        if not path.resolve().is_relative_to(root) or sha(path)!=entry['checkpoint_sha256']:
            raise ValueError('Raw checkpoint bytes/cohort mismatch')
        bindings.append(dict(scenario_id=sid,policy_id=policy,checkpoint_path=path.relative_to(root).as_posix(),
                             checkpoint_sha256=entry['checkpoint_sha256'],raw_payload_sha256=actual_digest))
        if policy==PRIMARY_TEACHER and ident is not None:
            current,stats=build_teacher_records([payload]);rows.extend(current);counts.update(stats)
        # The caller's generator drops its full solver-call matrix log here.
    if seen!=expected:raise ValueError('BC pilot trajectory pairs missing')
    return rows,dict(counts),{'verified_raw_pilot_payloads':len(seen),'raw_checkpoint_bindings':bindings,
        'raw_canonical_payloads_verified':True,'compact_teacher_rows_are_not_raw_payloads':True}


def stream_pilot_payloads(manifest,audit,dataset_root):
    from tools.p2b_experiment.runner import load_payload
    specs={s['id']:s for s in manifest['scenarios']};root=Path(dataset_root)
    for entry in sorted(audit['checkpoint_digests'],key=lambda e:(e['scenario_id'],e['policy_id'])):
        if specs[entry['scenario_id']]['experiment_role'] not in ('pilot_train','pilot_validation'):continue
        path=Path(entry['path']);path=path if path.is_absolute() else root/path
        if sha(path)!=entry['checkpoint_sha256']:raise ValueError('Checkpoint changed before stream load')
        yield load_payload(path.parent.parent,entry['scenario_id'],entry['policy_id'],manifest['signature'])


def fit_bc(manifest,audit,payloads,out,*,manifest_path,audit_path,dataset_root):
    from tools.integrated_experiment.runner import verify_manifest
    out=Path(out)
    if out.exists():raise FileExistsError('A fresh directory is required for every BC attempt')
    # The fresh output is created only after the byte-level data/source gates.
    manifest_path=Path(manifest_path);audit_path=Path(audit_path)
    if json.loads(manifest_path.read_text(encoding='utf-8'))!=manifest:
        raise ValueError('BC manifest object differs from actual bytes')
    if json.loads(audit_path.read_text(encoding='utf-8'))!=audit:
        raise ValueError('BC audit object differs from actual bytes')
    verify_manifest(manifest)
    if audit.get('manifest_sha256')!=sha(manifest_path):raise ValueError('Audit manifest bytes differ')
    rows,counts,binding=bind_and_extract(manifest,audit,payloads,dataset_root)
    train,val,held=split_teacher_records(rows)
    if held or not train or not val:raise ValueError('Nonempty train/devval, no frozen evaluation rows, required')
    train_all=len(train);train=cap_training_rows(train,6000)
    pre=SequencePreprocessor().fit(train);x=pre.transform(train);vx=pre.transform(val)
    y=np.asarray([r['targets_kW'] for r in train],float);vy=np.asarray([r['targets_kW'] for r in val],float)
    scale=np.maximum(np.std(y,axis=0),1.)
    out.mkdir(parents=True)
    source={p.relative_to(Path(__file__).resolve().parents[2]).as_posix():sha(p)
        for p in sorted(Path(__file__).parent.glob('*.py'))}
    for module in ('risk.py','regression.py','provenance.py'):
        p=Path(__file__).resolve().parents[2]/'tools'/'p2b_learning'/module
        source[p.relative_to(Path(__file__).resolve().parents[2]).as_posix()]=sha(p)
    write(out/'fit_provenance.json',{'manifest':{'path':str(manifest_path),'sha256':sha(manifest_path),
        'signature':manifest['signature']},'audit':{'path':str(audit_path),'sha256':sha(audit_path)},
        'payload_binding':binding,'source_sha256':source,'teacher_source':PRIMARY_TEACHER,
        'teacher_security_weight_USD_per_kWh':.35,'learned_action_count':6,
        'projection_base':'secure_milp','projection_fixed_weight_USD_per_kWh':.35,
        'fallback_policy':'secure_milp','registry':list(REGISTRY),'seeds':list(SEEDS),
        'training_available_rows':train_all,'training_selected_rows':len(train),'validation_rows':len(val),
        'teacher_counts':counts,'selection':'Mean over three RF/GRU seeds of devval normalized six-port MAE; constant diagnostic',
        'seed_selection':'Lowest devval normalized six-port MAE within chosen class; tie lower seed',
        'train_row_ids':[r['row_id'] for r in train],'validation_row_ids':[r['row_id'] for r in val],
        'scope':'Development fitting/selection; frozen development evaluation and final blind backgrounds unused'})
    write(out/'actual_teacher_rows.json',{'training':train,'validation':val})
    results=[]
    for config in REGISTRY:
        seeds=SEEDS if config['class']!='constant' else SEEDS[:1]
        for seed in seeds:
            directory=out/f'{config["id"]}_seed{seed}'
            with (out/'fit_attempts.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'event':'started','config':config['id'],'seed':seed,'time_unix':time.time()})+'\n')
            try:
                r=fit_candidate(config,seed,x,y,vx,vy,directory,pre.to_dict(),scale)
                r['directory']=directory.name;results.append(r);event=dict(event='finished',status='success')
            except Exception as exc:
                event=dict(event='finished',status='error',error_type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc())
                with (out/'fit_attempts.jsonl').open('a',encoding='utf-8') as f:
                    f.write(json.dumps(dict(event,config=config['id'],seed=seed,time_unix=time.time()))+'\n')
                raise
            with (out/'fit_attempts.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(event,config=config['id'],seed=seed,time_unix=time.time()))+'\n')
    means={c['id']:float(np.mean([r['normalized_six_port_MAE'] for r in results if r['model_id']==c['id']]))
        for c in REGISTRY}
    # Constant remains an honest diagnostic but the learned candidate must be RF or GRU.
    selected_id=min(('BC_RF','BC_GRU'),key=lambda k:(means[k],k))
    selected=min((r for r in results if r['model_id']==selected_id),key=lambda r:(r['normalized_six_port_MAE'],r['seed']))
    report={'schema':'integrated.bc_comparison.v1','manifest_signature':manifest['signature'],
        'teacher_source':PRIMARY_TEACHER,'metrics':results,'three_seed_mean_MAE':means,
        'selected_class':selected_id,'selected_seed':selected['seed'],'selected_model_directory':selected['directory'],
        'selected_model_metadata_sha256':sha(out/selected['directory']/'model_metadata.json'),
        'fits_attempted':len(results),'new_selected_models':1,'training_rows':len(train),'validation_rows':len(val),
        'selection_scope':'development_validation','frozen_development_evaluation_used':False,
        'closed_loop_validated':False,'interpretation':'Command imitation only; no closed-loop safety or benefit claim'}
    write(out/'BC_REPORT.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('manifest');parser.add_argument('audit');parser.add_argument('out')
    args=parser.parse_args();mp=Path(args.manifest);ap=Path(args.audit)
    m=json.loads(mp.read_text(encoding='utf-8'));a=json.loads(ap.read_text(encoding='utf-8'))
    result=fit_bc(m,a,stream_pilot_payloads(m,a,mp.parent),args.out,
        manifest_path=mp,audit_path=ap,dataset_root=mp.parent)
    print(json.dumps(result,ensure_ascii=False,indent=2))
