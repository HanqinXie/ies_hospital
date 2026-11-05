"""Minimal v15 inventory adapter over the mature v14 light-package ZIP engine.

No simulation, scoring, raw decoding or active-campaign collection is performed.
Only root's final explicit selection can build an archive. Production modules
are parsed for imports, never imported by this helper.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from types import FunctionType
import zipfile

from tools.engineering_reporting import package as mature

SCHEMA='hospital_ies.operating_light_package.v15.v1'
SELECTION_SCHEMA='hospital_ies.operating_light_selection.v15.v1'
PHYSICAL_INPUTS=mature.PHYSICAL_INPUTS
KNOWN_REFERENCE_MANIFESTS=tuple(
    'outputs/v14_engineering_model_20261017_run01/experiments/'+name+'/manifest.json'
    for name in ('confirmation_r6_M14_engineering_boundary_early45_day51',
                 'confirmation_r6_M14_engineering_boundary_late85_day51',
                 'historical_recovery_M0','revision_r6_M14_engineering_unknown_day51'))
METADATA_DEPENDENCIES=tuple(dict.fromkeys((*mature.METADATA_DEPENDENCIES,*KNOWN_REFERENCE_MANIFESTS)))
source_closure=mature.source_closure
sha=mature.sha
_read=mature._read
_bytes=mature._bytes
_within=mature._within
_put=mature._put
_name=mature._name


def _safe_context(path,run):
    relative=path.relative_to(run)
    if any(part.casefold() in mature.SKIP or part.casefold().startswith(('pytest-','pytest_')) for part in relative.parts):
        return False
    if any(part.casefold() in {'secrets','credentials'} for part in relative.parts):
        return False
    if path.name.casefold() in {'credentials.json','secrets.json','gurobi.lic','license.lic','.env'}:
        return False
    return path.suffix.lower() in mature.SUFFIXES and 'PAPER' not in path.name.upper()


def _cancellations(run):
    found={}
    for path in run.glob('*.json'):
        if 'CANCELLATION' not in path.name.upper():continue
        value=_read(path)
        if value.get('actual_attempts')!=0:continue
        for batch in value.get('batches',[]):
            name=_name(batch)
            if '/' in name:raise ValueError('Cancellation must name a direct batch')
            found[name]=value.get('reason','explicit root cancellation before any attempt')
    return found


def _proposal(root,run,selection,scenario_id):
    if selection.get('replay_proposal'):
        path=_within(selection['replay_proposal'],root)
        candidates=[path] if path.is_file() else []
    else:
        candidates=[]
        for folder in ('design','design_v2','frozen'):
            candidates.extend(p for p in (run/folder).glob('*.json')
                              if p.name.upper().endswith('PROPOSAL.JSON'))
        candidates=[p for p in candidates if scenario_id in {s['id'] for s in _read(p).get('scenarios',[])}]
    if len(candidates)!=1 or scenario_id not in {s['id'] for s in _read(candidates[0]).get('scenarios',[])}:
        raise ValueError('Explicit replay proposal must uniquely contain selected scenario; set replay_proposal')
    return candidates[0]


def _policy_keys(specs):
    known={'fixed_standby_constant_v12b':'F','endurance_restore_p0_ready_v14r6':'E',
           **{'readiness_handoff_'+k+'_v15':k for k in ('K0','K1','K2')},
           'readiness_multiplier_only_K2_v15':'A2','readiness_no_continuity_K2_v15':'C2'}
    try:return ','.join(known[s['policy_id']] for s in specs)
    except KeyError as exc:raise ValueError('Unknown replay policy key; root must provide a supported policy identity') from exc


def _readme(run_relative,proposal,selected,plan):
    first=next((s for s in selected if s['model_id']=='M15_local_response'),selected[0])
    return f'''# v15 operating response light package

Extract into a D/E workspace and open PowerShell at this package root. This is
saved engineering evidence plus runnable dependencies. Original final12 remains
not_verified and unopened; copied annual input bytes do not authorize its use.

DEPENDENCIES.json records actual installed distributions and static import
candidates. Replay requires numpy/pandas/scipy/gurobipy and a separately valid
Gurobi license for the configured backend. No credentials, license, system
settings, neural training, CUDA work, figures or paper are supplied/required.

The canonical {run_relative}/experiments directory is intentionally empty.
Historical batches are relocated under evidence/{run_relative}/experiments;
their completed/failed/cancelled/unknown indices remain intact. Use a NEW
canonical directory for register/run, never an old evidence batch directory.

```powershell
$env:TEMP = Join-Path $PWD '{run_relative}/temp/replay'
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$env:TMP = $env:TEMP
python -B -m tools.operating_response.runner register --proposal "{proposal}" --directory "{run_relative}/experiments/replay_selected_{first['model_id']}" --policies {_policy_keys(plan['policy_specs'])} --model {first['model_id']} --model-config "{run_relative}/replay/MODEL_CONFIG.json" --scenario-id "{first['scenario_id']}" --phase replay_development
python -B -m tools.operating_response.runner run --directory "{run_relative}/experiments/replay_selected_{first['model_id']}"
```

The example uses a real selected scene and its recorded model configuration.
For a second model use that selected batch's recorded model/model_config and a
different new directory; matched input does not mean model capabilities equal.
Current runtime dependencies retain original workspace-relative names, including
old signed manifests used by known_counterexamples. Historical absolute paths
inside saved results are provenance; package indices provide relative locations.

Only explicitly selected compressed raws are included. EXTERNAL_RAW_INDEX.json
retains every other saved raw path/SHA without rereading it. A whole requested
outcome remains unknown where its original run stopped. This package never
fills missing service or reclassifies failures as completed trajectories.

P3 source CSV/receipt, if supplied, is a normal baseline only. Disturbed P3 room
temperature remains unmodeled. The external large original SQL is not copied;
fresh baseline extraction needs that separate source. Core replay uses the
included physical input CSVs/IDF and does not rerun EnergyPlus.

Verify saved archive bytes without a physical simulation:

```powershell
python -B -m tools.operating_reporting.package verify --archive "<D/E archive.zip>"
```

ZIP/member/hash verification proves byte integrity only. It is not independent
solver installation, end-to-end physical replay, hospital OEM calibration or
clinical safety certification. See docs/v15 for explicit source/units/domain.
'''.encode('utf8')


def prepare(workspace,run,selection_path,*,reports):
    """Only the v15 inventory/identity adaptation; all byte helpers are reused."""
    root=Path(workspace).resolve();run=_within(run,root)
    selection_path=_within(selection_path,run);selection=_read(selection_path)
    if (selection.get('schema')!=SELECTION_SCHEMA or selection.get('status')!='final'
        or selection.get('selected_by')!='root' or selection.get('all_batches_stopped') is not True):
        raise ValueError('Explicit final root selection with all batches stopped is required')
    examples=selection.get('raw_examples')
    if not isinstance(examples,list) or not 2<=len(examples)<=24:
        raise ValueError('Selection requires 2 to24 explicit matched raw examples')
    for example in examples:
        if '/' in _name(example['batch']):raise ValueError('Selected batch must be direct and safe')
    entries={};registered=[];raw_index=[];selected=[];snapshots=[];plans={};input_index=[]
    seeds=set()
    for folder in ('tools/operating_response','tools/operating_reporting'):
        seeds.update(p.relative_to(root).as_posix() for p in (root/folder).glob('*.py'))
    if not seeds:raise FileNotFoundError('Missing v15 runtime/reporting source dependency')
    cancelled=_cancellations(run)
    for plan_path in sorted((run/'experiments').glob('*/plan.json')):
        folder=plan_path.parent;plan=_read(plan_path);plans[folder.name]=plan
        manifest=_read(folder/'manifest.json');scenes={s['id']:s for s in manifest['scenarios']}
        expected={(sid,s['policy_id']) for sid in plan['scenario_ids'] for s in plan['policy_specs']}
        if len(expected)!=plan['registered_pairs']:raise ValueError('Registered package denominator differs')
        rows_path=folder/'analysis/RESULTS.json';rows=_read(rows_path)['records'] if rows_path.exists() else []
        actual={}
        for row in rows:
            key=row['scenario_id'],row['policy_id']
            if key in actual or key not in expected:raise ValueError('Saved result differs from registered package denominator')
            actual[key]=row
        # Analysis can be unavailable after a real attempt; keep its lightweight
        # pair RESULT metadata instead of losing the attempt from the index.
        for sid,pid in sorted(expected):
            result_path=folder/'pairs'/(sid+'__'+pid)/'RESULT.json'
            if (sid,pid) not in actual and result_path.is_file():actual[sid,pid]=_read(result_path)
            row=actual.get((sid,pid));status=(row.get('status') if row else
                'cancelled_before_start' if folder.name in cancelled else 'missing_saved_analysis')
            registered.append(dict(batch=folder.name,scenario_id=sid,policy_id=pid,model_id=plan['model_id'],
                phase=plan['phase'],analysis_class=scenes[sid].get('analysis_class'),status=status,
                cancellation_reason=cancelled.get(folder.name) if status=='cancelled_before_start' else None))
            if row and row.get('raw_path'):
                raw=_within(row['raw_path'],run)
                raw_index.append(dict(batch=folder.name,scenario_id=sid,policy_id=pid,model_id=plan['model_id'],
                    original_workspace_path=raw.relative_to(root).as_posix(),raw_sha256=row.get('raw_sha256'),
                    packaged_path=None,included=False,sha_semantics='producer saved index; unselected raw not reread'))
        for name,digest in plan.get('source_sha256',{}).items():
            source=_within(name,root)
            if source.is_file() and sha(source)==digest:
                if source.suffix=='.py':seeds.add(source.relative_to(root).as_posix())
                else:_put(entries,source.relative_to(root).as_posix(),source,'registered runtime dependency',digest)
            else:
                snapshot=_within(folder/'source_snapshot'/_name(name),root)
                if not snapshot.is_file() or sha(snapshot)!=digest:raise FileNotFoundError('Missing registered source/version: '+name)
                snapshots.append(dict(batch=folder.name,original_path=name,
                    packaged_path='evidence/'+snapshot.relative_to(root).as_posix(),sha256=digest))
        for example in examples:
            if example['batch']!=folder.name:continue
            row=actual.get((example.get('scenario_id'),example.get('policy_id')))
            if not row or not row.get('raw_path'):raise ValueError('Selected raw has no registered saved result')
            raw=_within(row['raw_path'],run);name='evidence/'+raw.relative_to(root).as_posix()
            _put(entries,name,raw,'explicit root matched raw',row['raw_sha256'])
            selected.append(dict(example,model_id=plan['model_id'],packaged_path=name))
            result_path=raw.with_name('RESULT.json')
            if result_path.is_file():_put(entries,'evidence/'+result_path.relative_to(root).as_posix(),result_path,'selected raw result')
            for item in raw_index:
                if (item['batch'],item['scenario_id'],item['policy_id'])==(folder.name,example['scenario_id'],example['policy_id']):
                    item.update(packaged_path=name,included=True,sha_semantics='compressed bytes verified while packaging')
    identities={(s['batch'],s['scenario_id'],s['policy_id']) for s in selected}
    if len(selected)!=len(examples) or len(identities)!=len(examples):raise ValueError('Invalid/duplicate selected identity')
    matched=defaultdict(set)
    for item in selected:matched[item['scenario_id']].add((item['model_id'],item['policy_id']))
    if any(len(v)<2 for v in matched.values()):raise ValueError('Selected raw requires matched policies or models for each scenario')
    sources=source_closure(root,sorted(seeds))
    for name,path in sources.items():_put(entries,name,path,'runtime/reporting source')
    for name in (*PHYSICAL_INPUTS,*METADATA_DEPENDENCIES):
        path=_within(name,root)
        if not path.is_file():raise FileNotFoundError('Required replay input/metadata dependency: '+name)
        _put(entries,name,path,'physical input/config' if name in PHYSICAL_INPUTS else 'required frozen metadata')
        input_index.append(dict(path=name,sha256=entries[name]['sha256'],bytes=entries[name]['bytes'],physical_values_parsed=False))
    for pattern in ('configs/*.json','requirements*.txt','tests/test_operating*.py','tests/test_engineering*.py',
        'docs/v15/*.md','docs/v14/*.md','docs/superpowers/*/*operating-response*.md',
        'Hospital_IES_V15_Engineering_Operation_Plan/*.md','Hospital_IES_V15_Engineering_Operation_Plan/*.json'):
        for path in root.glob(pattern):_put(entries,path.relative_to(root).as_posix(),path,'config/test/task/engineering context')
    for path in run.rglob('*'):
        if not path.is_file() or not _safe_context(path,run):continue
        relative=path.relative_to(run)
        # Every lightweight producer RESULT remains evidence, even when batch
        # analysis failed. Compressed raw remains selected-only.
        if 'pairs' in relative.parts and path.name not in ('RESULT.json','analysis_error.json','error.json'):continue
        name=('evidence/' if relative.parts[0]=='experiments' else '')+path.relative_to(root).as_posix()
        _put(entries,name,_within(path,root),'saved operating evidence')
    if not reports:raise ValueError('Actual engineering report is required')
    for report in reports:
        path=_within(report,run)
        if not path.is_file():raise FileNotFoundError(path)
        if path.suffix.lower() not in {'.md','.json','.csv','.txt'} or not _safe_context(path,run):
            raise ValueError('Engineering text/data report required, no paper/image or sensitive files')
        _put(entries,path.relative_to(root).as_posix(),path,'root operating report')
    replay=next((s for s in selected if s['model_id']=='M15_local_response'),selected[0])
    proposal=_proposal(root,run,selection,replay['scenario_id'])
    proposal_name=proposal.relative_to(root).as_posix();_put(entries,proposal_name,proposal,'selected replay proposal')
    relative_run=run.relative_to(root).as_posix();replay_plan=plans[replay['batch']]
    _put(entries,relative_run+'/replay/MODEL_CONFIG.json',_bytes(replay_plan.get('model_config',{})),'actual selected model configuration')
    _put(entries,'README_REPLAY.md',_readme(relative_run,proposal_name,selected,replay_plan),'v15 replay instructions')
    dependencies=mature._dependencies(sources)
    for name,value,role in (
        ('DEPENDENCIES.json',dependencies,'runtime distribution evidence'),
        ('ALL_REGISTERED_PAIR_INDEX.json',registered,'all-denominator index'),
        ('EXTERNAL_RAW_INDEX.json',raw_index,'selected/external raw metadata index'),
        ('RUNTIME_INPUT_INDEX.json',input_index,'replay input byte inventory'),
        ('SOURCE_VERSION_INDEX.json',snapshots,'historical source versions')):
        _put(entries,name,_bytes(value),role)
    return dict(entries=entries,registered_pairs=len(registered),selected_raw_count=len(selected),
        original_run=str(run),selection_sha256=sha(selection_path),selected_examples=selected,
        dependencies=dependencies,all_unselected_raw_external=True,physical_reproduction_performed=False,
        raw_values_decoded=False,original_final12='not_verified; unopened',
        estimated_uncompressed_bytes=sum(row['bytes'] for row in entries.values()))


def verify(archive):
    result=mature.verify(archive)
    with zipfile.ZipFile(archive) as bundle:
        if json.loads(bundle.read('PACKAGE_INDEX.json')).get('schema')!=SCHEMA:
            raise ValueError('Archive is not the explicit v15 light-package schema')
    return result


def build(workspace,run,selection_path,output,*,reports):
    # Reuse the unchanged mature streaming ZIP/hash/disk guard bytecode with
    # this module's prepare/verify/schema bindings. Never patch old globals.
    namespace=dict(mature.build.__globals__,prepare=prepare,verify=verify,SCHEMA=SCHEMA)
    implementation=FunctionType(mature.build.__code__,namespace,mature.build.__name__,
                                mature.build.__defaults__,mature.build.__closure__)
    implementation.__kwdefaults__=mature.build.__kwdefaults__
    return implementation(workspace,run,selection_path,output,reports=reports)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    make=sub.add_parser('build');make.add_argument('--workspace',type=Path,required=True)
    make.add_argument('--run',type=Path,required=True);make.add_argument('--selection',type=Path,required=True)
    make.add_argument('--output',type=Path,required=True);make.add_argument('--report',type=Path,action='append',required=True)
    check=sub.add_parser('verify');check.add_argument('--archive',type=Path,required=True)
    args=parser.parse_args(argv)
    result=verify(args.archive) if args.command=='verify' else build(args.workspace,args.run,args.selection,args.output,reports=args.report)
    print(json.dumps(result,ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
