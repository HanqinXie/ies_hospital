"""New-budget V21 execution with immutable attempts and E-drive raw evidence.

The runner does not generate controls or change physics. Public factories are
lazy imports so frozen experiments and mathematical runner tests share the same
registration, drift and failure handling code.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import traceback

from .design import MODEL_ID, SEALED_DAYS, allowed_hours, digest, source_support

SCHEMA = 'hospital_ies.sci_runner.v21.v1'
PRIORITIES = ('P0','P1','P2','P3')
CARRIERS = ('e','h','c')


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def write_new(path, value):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')


def append(path, value):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
        stream.flush(); os.fsync(stream.fileno())


def source_binding(root):
    root = Path(root).resolve(); paths = set()
    for folder in ('src','tools','scripts','tests'):
        paths.update((root/folder).rglob('*.py'))
    paths.update((root/'configs').rglob('*.json'))
    paths.update(root.glob('*.py'))
    if (root/'pytest.ini').is_file(): paths.add(root/'pytest.ini')
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(paths)
        if '__pycache__' not in p.parts and not p.is_symlink()}


def input_binding(root):
    root = Path(root).resolve()
    suffixes = {'.csv','.json','.idf','.epw','.txt'}
    paths = [p for p in (root/'inputs').rglob('*') if p.is_file() and p.suffix.lower() in suffixes
             and 'literature' not in p.parts and not p.is_symlink()]
    paths += list(root.glob('requirements*.txt'))
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(paths)}


def _signed(value):
    value = copy.deepcopy(value); value.pop('signature',None)
    value['signature'] = digest(value)
    return value


def create_freeze(root, path, policy_specs, configurations, budget, *, selection_record=None):
    root = Path(root).resolve()
    required = ('max_actual_attempts','max_experiment_wall_hours')
    if any(float(budget.get(key,0))<=0 for key in required):
        raise ValueError('New study needs explicit positive attempt and elapsed-time budgets')
    if not policy_specs or not configurations:
        raise ValueError('Freeze must bind actual policy specifications and model configurations')
    factors=root/'outputs/v20_load_accounting_20261019_run01/references/EMISSIONS_FACTOR_REGISTRY.json'
    factor_binding=dict(path=factors.relative_to(root).as_posix(),sha256=sha(factors),registry=read(factors)) if factors.is_file() else None
    freeze = _signed(dict(schema=SCHEMA+'.freeze',created_utc=now(),model_id=MODEL_ID,
        source_sha256=source_binding(root),runtime_inputs=input_binding(root),
        policy_specs=copy.deepcopy(list(policy_specs)),configurations=copy.deepcopy(configurations),
        budget=copy.deepcopy(budget),selection_record=copy.deepcopy(selection_record),
        emissions_factor_binding=factor_binding,
        budget_clock_semantics='Starts at first actual attempt recorded for this new freeze; no historical campaign clock',
        historical_results_unchanged=True))
    write_new(path,freeze)
    return freeze


def verify_freeze(root, freeze):
    unsigned = {k:v for k,v in freeze.items() if k!='signature'}
    if freeze.get('model_id')!=MODEL_ID or freeze.get('signature')!=digest(unsigned):
        raise ValueError('Invalid new M21 freeze signature or model identity')
    if source_binding(root)!=freeze['source_sha256'] or input_binding(root)!=freeze['runtime_inputs']:
        raise ValueError('Frozen source or input drift; register a new freeze and retain old evidence')
    factors=freeze.get('emissions_factor_binding')
    if factors and (not (Path(root)/factors['path']).is_file() or sha(Path(root)/factors['path'])!=factors['sha256']):
        raise ValueError('Frozen emissions factor source drift')
    return True


def _scientific_path(path):
    path = Path(path).resolve()
    if os.name=='nt' and path.drive.lower()=='c:':
        raise ValueError('Research raw and temporary data must use D/E, not C')
    return path


def register_campaign(root, directory, panel, policy_specs, configurations, freeze_path,
        *, phase='pilot', raw_root=None, pilot_proof=None):
    root = Path(root).resolve(); directory = _scientific_path(directory)
    raw_root = _scientific_path(raw_root if raw_root is not None else directory/'raw')
    freeze_path = Path(freeze_path).resolve(); freeze = read(freeze_path)
    verify_freeze(root,freeze)
    from .contracts import normalize_study_scenario,exporter_metadata
    panel=copy.deepcopy(panel)
    panel['scenarios']=[normalize_study_scenario(s) for s in panel['scenarios']]
    panel.pop('signature',None);panel['signature']=digest(panel)
    if phase not in ('pilot','development','formal','mechanism','boundary','hardware','sensitivity','reproduction'):
        raise ValueError('Explicit registered experiment phase required')
    if directory.exists():
        raise FileExistsError('Registration exists; never overwrite old attempts')
    if not panel.get('scenarios') or len({x['id'] for x in panel['scenarios']})!=len(panel['scenarios']):
        raise ValueError('Nonempty unique physical scenarios required')
    pool = {p['policy_id']:p for p in freeze['policy_specs']}
    if any(p.get('policy_id') not in pool or digest(pool[p['policy_id']])!=digest(p) for p in policy_specs):
        raise ValueError('Policy specification differs from frozen candidate')
    if any(key not in freeze['configurations'] or digest(value)!=digest(freeze['configurations'][key])
           for key,value in configurations.items()):
        raise ValueError('Hardware/model configuration differs from frozen identity')
    proof_sha = None
    if phase=='formal':
        if pilot_proof is None or not Path(pilot_proof).is_file():
            raise ValueError('Formal registration requires an actual passed pilot proof')
        proof = read(pilot_proof)
        if proof.get('completed',0)<=0 or proof.get('failed',0) or proof.get('pending',1) or not proof.get('pilot_qualified'):
            raise ValueError('Formal registration requires an actual qualified pilot')
        if not freeze.get('selection_record'):
            raise ValueError('Formal freeze must contain fixed development selection record')
        proof_sha = sha(pilot_proof)
    for scene in panel['scenarios']:
        support = source_support(scene)
        if any(support['raw_start_minute']<(day+1)*1440 and day*1440<support['raw_end_minute'] for day in SEALED_DAYS):
            raise ValueError('Old sealed final backgrounds remain excluded from this registration')
        if phase=='formal' and scene.get('role')!='confirmation':
            raise ValueError('Formal outcomes must use the frozen confirmation role')
    pairs = len(panel['scenarios'])*len(policy_specs)*len(configurations)
    if pairs>int(freeze['budget'].get('max_registered_pairs',freeze['budget']['max_actual_attempts'])):
        raise ValueError('Registration exceeds the new study budget')
    manifest = _signed(dict(schema=SCHEMA+'.manifest',execution_model_id=MODEL_ID,
        simulator_hash=digest(freeze['source_sha256']),execution_contract_id='hospital_ies.sci_engineering_execution.v21.v1',
        model_config_signature=digest(configurations),source_sha256=freeze['source_sha256'],
        files=freeze['runtime_inputs'],freeze_signature=freeze['signature'],
        scenarios=copy.deepcopy(panel['scenarios']),source_blocks=copy.deepcopy(panel.get('source_blocks',[])),
        policy_specs_sha256=digest(policy_specs),configuration_signatures={k:digest(v) for k,v in configurations.items()},
        phase=phase,original_final12_evaluated=False,**exporter_metadata(panel['scenarios'])))
    plan = _signed(dict(schema=SCHEMA+'.plan',registered_utc=now(),model_id=MODEL_ID,
        phase=phase,root=str(root),raw_root=str(raw_root),freeze_path=str(freeze_path),freeze_sha256=sha(freeze_path),
        freeze_signature=freeze['signature'],manifest_signature=manifest['signature'],registered_pairs=pairs,
        panel=copy.deepcopy(panel),policy_specs=copy.deepcopy(list(policy_specs)),
        configurations=copy.deepcopy(configurations),pilot_proof=str(pilot_proof) if pilot_proof else None,
        pilot_proof_sha256=proof_sha,budget_scope=str(freeze_path.parent),
        action_information_boundary='Current and historical legal observation only; no future event scripts to policies'))
    directory.mkdir(parents=True)
    write_new(directory/'MANIFEST.json',manifest); write_new(directory/'PLAN.json',plan)
    write_new(directory/'FREEZE_REFERENCE.json',dict(path=str(freeze_path),sha256=sha(freeze_path),signature=freeze['signature']))
    return plan


@contextmanager
def _budget_lock(path):
    deadline = time.monotonic()+10.
    while True:
        try:
            fd = os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            os.write(fd,json.dumps(dict(pid=os.getpid(),utc=now())).encode('ascii')); os.close(fd)
            break
        except FileExistsError:
            if time.monotonic()>=deadline:
                raise RuntimeError('New-study budget lock busy; retain pending attempts')
            time.sleep(.05)
    try:
        yield
    finally:
        path.unlink()


def _reserve_attempt(plan, freeze, identity):
    base = Path(plan['budget_scope']); base.mkdir(parents=True,exist_ok=True)
    ledger = base/('BUDGET_'+freeze['signature']+'.jsonl')
    with _budget_lock(base/('BUDGET_'+freeze['signature']+'.lock')):
        rows = [json.loads(x) for x in ledger.read_text(encoding='utf8').splitlines()] if ledger.exists() else []
        elapsed = (datetime.now(timezone.utc)-datetime.fromisoformat(rows[0]['utc'])).total_seconds()/3600. if rows else 0.
        if len(rows)>=int(freeze['budget']['max_actual_attempts']) or elapsed>=float(freeze['budget']['max_experiment_wall_hours']):
            raise RuntimeError('New M21 actual-attempt or elapsed-time budget reached; remaining pairs retained pending')
        append(ledger,dict(event='started',utc=now(),**identity))


def _merge(intervals):
    result = []
    for start,end in sorted(intervals):
        if result and start<=result[-1][1]+1e-9:
            result[-1][1]=max(result[-1][1],end)
        else:
            result.append([start,end])
    return result


def _times(intervals):
    intervals = _merge(intervals)
    return dict(material_minutes=sum(b-a for a,b in intervals),
        longest_material_minutes=max((b-a for a,b in intervals),default=0.),
        interruption_count=len(intervals),repeat_interruption_count=max(0,len(intervals)-1),
        first_material_minute=intervals[0][0] if intervals else None,intervals=intervals)


def compact_metrics(payload, *, env=None, factor_registry=None):
    """Self-owned M21 service/phase/storage accounting from actual subintervals."""
    service = {p:{k:dict(requested_kWh=0.,delivered_kWh=0.,ENS_kWh=0.,
        disconnected_ENS_kWh=0.,connected_unserved_ENS_kWh=0.) for k in CARRIERS} for p in PRIORITIES}
    intervals = {(p,k):[] for p in PRIORITIES for k in CARRIERS}
    unions = {p:[] for p in PRIORITIES}; critical=[]; segments=payload.get('segments',[])
    scene = payload.get('scenario',{})
    incident = float(scene.get('first_event_minute',scene.get('fault_start_minute',0.)) or 0.)
    recovery = float(scene.get('recovery_start_minute',incident+float(scene.get('fault_duration_minutes',0.))))
    initial_end = incident+float(scene.get('initial_handoff_window_minutes',15.))
    phase = {name:{p:{k:0. for k in CARRIERS} for p in PRIORITIES}
             for name in ('preparation','initial_handoff','endurance','recovery')}
    energy = Counter(); storage = {}; observed = 0.; gap=False
    for seg in segments:
        a,b = float(seg['start_minute']),float(seg['end_minute'])
        if b<=a or a<observed-1e-8:
            raise ValueError('Actual service segment times overlap or are invalid')
        if a>observed+1e-8:gap=True
        observed=b; dt=(b-a)/60.
        ledger=seg.get('service_request_ledger')
        if not isinstance(ledger,dict):
            raise ValueError('Missing actual M21 service ledger; unknown cannot become zero')
        arrays=[ledger.get(key) for key in ('original_demand_kW','active_cut_kW','connected_unserved_kW','delivered_kW')]
        if any(not isinstance(arr,list) or len(arr)!=4 or any(len(row)!=3 for row in arr) for arr in arrays):
            raise ValueError('Invalid actual service ledger matrix')
        p_short=[]
        for ip,p in enumerate(PRIORITIES):
            material=False
            for ik,k in enumerate(CARRIERS):
                request,cut,unserved,delivered=(float(arr[ip][ik]) for arr in arrays)
                if not all(math.isfinite(v) and v>=-1e-8 for v in (request,cut,unserved,delivered)) or abs(request-cut-unserved-delivered)>1e-7:
                    raise ValueError('Inconsistent actual service ledger; request must equal delivered plus both shortage kinds')
                short=cut+unserved; row=service[p][k]
                for key,value in (('requested_kWh',request),('delivered_kWh',delivered),('ENS_kWh',short),
                                  ('disconnected_ENS_kWh',cut),('connected_unserved_ENS_kWh',unserved)):
                    row[key]+=value*dt
                is_material=short>.001 and (short>=.01 or (request>.001 and short/request>=.001))
                if is_material:intervals[p,k].append([a,b]);material=True
                cuts=sorted({a,b,*[x for x in (incident,initial_end,recovery) if a<x<b]})
                for x,y in zip(cuts,cuts[1:]):
                    name=('preparation' if x<incident else 'recovery' if x>=recovery
                          else 'initial_handoff' if x<initial_end else 'endurance')
                    phase[name][p][k]+=short*(y-x)/60.
            if material:unions[p].append([a,b])
            p_short.append(material)
        if any(p_short[:2]):critical.append([a,b])
        action=seg.get('action',{})
        for field in ('grid_import_kW','diesel_kW','absorption_cooling_kW','tower_rejection_kW','gt_fuel_MMBtu_h','boiler_steam_lbph'):
            if field in action:energy[field.replace('_kW','_kWh').replace('_MMBtu_h','_MMBtu').replace('_lbph','_lb')]+=float(action[field])*dt
        for field in ('gt_electric_kW','electric_chiller_cooling_kW'):
            if field in action:energy[field.replace('_kW','_kWh')]+=sum(map(float,action[field]))*dt
        for name,receipt in seg.get('m21_storage',{}).items():
            row=storage.setdefault(name,dict(first_receipt=copy.deepcopy(receipt),last_receipt=None,totals={}))
            row['last_receipt']=copy.deepcopy(receipt)
            for key,value in receipt.items():
                if isinstance(value,(int,float)) and ('charge' in key or 'loss' in key) and 'kWh' in key:
                    row['totals'][key]=row['totals'].get(key,0.)+float(value)
        dg=seg.get('diesel_local_response',{})
        receipt=dg.get('energy_receipt',dg) if isinstance(dg,dict) else {}
        for field in ('mechanical_work_kWh','electrical_work_kWh','loss_work_kWh','rotor_delta_kWh','fuel_budget_debit_kWh_equivalent'):
            if isinstance(receipt,dict) and isinstance(receipt.get(field),(int,float)):
                energy['DG_'+field]+=float(receipt[field])
    for p in PRIORITIES:
        for k in CARRIERS:service[p][k].update(_times(intervals[p,k]))
    requested=float(payload.get('summary',{}).get('requested_end_minute',scene.get('duration_steps',0)*scene.get('step_minutes',15.)))
    summary=payload.get('summary',{})
    calls=payload.get('solver_calls',[])
    times=[float(x['elapsed_seconds']) for x in calls if isinstance(x,dict) and isinstance(x.get('elapsed_seconds'),(int,float))]
    costs=[seg.get('cost_USD_step') for seg in segments]
    cost_sum=sum(map(float,costs)) if costs and all(isinstance(x,(int,float)) for x in costs) else None
    shaft=energy.get('DG_mechanical_work_kWh');electric=energy.get('DG_electrical_work_kWh')
    correction=.30*(shaft-electric) if shaft is not None and electric is not None else None
    economics=dict(accounting_cost_USD_sum_segments=cost_sum,DG_fee_correction_USD=correction,
        energy_cost_USD=None,initial_inventory_grid_input_kWh_conditional=None,
        initial_inventory_fee_USD_conditional=None,inventory_recharge_fee_added_twice=False,
        price_year=2001,price_currency='USD',actual_recharge_is_already_in_segment_energy_and_cost=True,
        DG_fee_semantics='0.30 USD per actual shaft-equivalent kWh; old electric fee replaced once',
        initial_inventory_method='Conditional grid purchase at first-hour tariff; cold charged input uses original EC conversion plus registered cold pump ratio')
    plant=getattr(env,'plant',None) if env is not None else None
    accounting_keys=('steam_kWh_per_lb','boiler_eff','gas_price_usd_per_mmbtu','ec_electric_kW_per_rt')
    economics['missing_plant_accounting_parameters']=[key for key in accounting_keys if not hasattr(plant,key)]
    if plant is not None and not economics['missing_plant_accounting_parameters']:
        from tools.execution_recovery.analysis import economic_window,_grid_tariff
        from hospital_ies.model import RT_TO_KW
        source_economics=economic_window(payload,env,0.,observed)
        economics['source_economics']=source_economics
        inventory=payload.get('m21_initial_storage') or {}
        def stored_input(name):
            value=inventory.get(name,0.)
            return float(value.get('initial_charged_input_kWh',0.)) if isinstance(value,dict) else float(value)
        pump=float(getattr(env,'sci_config',{}).get('cold_pump_kW_per_kW',0.))
        initial_electric=stored_input('ups')+stored_input('cold_store')*(plant.ec_electric_kW_per_rt/RT_TO_KW+pump)
        tariff=_grid_tariff(segments[0],env) if segments else None
        inventory_fee=initial_electric*tariff if tariff is not None else None
        economics.update(initial_inventory_grid_input_kWh_conditional=initial_electric,
                         initial_inventory_fee_USD_conditional=inventory_fee)
        if all(x is not None for x in (cost_sum,correction,inventory_fee)):
            economics['energy_cost_USD']=cost_sum+correction+inventory_fee
        if factor_registry:
            from tools.model_closeout.emissions import evaluate_emissions
            case=factor_registry['cases'][factor_registry['main_case_id']]
            resource=dict(source_economics)
            resource['grid_purchase_kWh']=(resource['grid_purchase_kWh']+initial_electric
                if resource.get('grid_purchase_kWh') is not None else None)
            resource['DG_fuel_budget_debit_shaft_equivalent_kWh']=resource.get('DG_fuel_budget_debit_kWh_equivalent')
            economics['emissions_conditional']=evaluate_emissions([resource],case)[0]
            economics['emissions_include_conditional_initial_inventory_grid_purchase']=True
    return dict(carrier_service=service,priority_union={p:_times(unions[p]) for p in PRIORITIES},
        critical_union=_times(critical),phase_ENS_kWh=phase,actual_energy=dict(energy),storage=storage,
        initial_storage=copy.deepcopy(payload.get('m21_initial_storage')),economics=economics,
        observed_until_minute=observed,requested_end_minute=requested,
        complete=not gap and abs(observed-requested)<1e-7,unknown_tail_minutes=max(0.,requested-observed),
        segment_count=len(segments),online_solver_seconds=sum(times),online_solver_max_seconds=max(times,default=None),
        solver_call_count=len(calls),strategy_fallback_steps=summary.get('strategy_fallback_steps'),
        projection_fallback_steps=summary.get('projection_fallback_steps'),
        projection_changed_steps=summary.get('projection_changed_steps'),
        temperatures_are_exploratory_and_do_not_cancel_cooling_ENS=True)


class _Resources:
    def __init__(self):
        self.stop=threading.Event();self.peak_rss=0;self.peak_private=0;self.os_peak_rss=0;self.samples=0;self.errors=0
    def sample(self):
        try:
            import psutil
            process=psutil.Process();memory=process.memory_info()
            self.peak_rss=max(self.peak_rss,memory.rss)
            self.peak_private=max(self.peak_private,int(getattr(memory,'private',memory.vms)))
            self.os_peak_rss=max(self.os_peak_rss,int(getattr(memory,'peak_wset',memory.rss)))
            self.samples+=1
        except Exception:self.errors+=1
    def loop(self):
        while not self.stop.wait(.25):self.sample()
    def __enter__(self):
        self.sample();self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start();return self
    def __exit__(self,*args):
        self.stop.set();self.thread.join();self.sample()
    def receipt(self):
        return dict(peak_sampled_rss_bytes=self.peak_rss,peak_sampled_private_bytes=self.peak_private,
                    os_peak_rss_bytes=self.os_peak_rss,sample_count=self.samples,unknown_samples=self.errors,
                    sample_interval_seconds=.25,scope='Current serial episode runner process; sampled peaks may miss short intervals')


def _save_raw(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as binary:
        with gzip.GzipFile(fileobj=binary,mode='wb',mtime=0) as compressed:
            for fragment in json.JSONEncoder(ensure_ascii=False,allow_nan=False,separators=(',',':')).iterencode(payload):
                compressed.write(fragment.encode('utf-8'))
    return sha(path)


def _default_env(*args,**kwargs):
    from .environment import OperatingHospitalIESEnv
    return OperatingHospitalIESEnv(*args,**kwargs)


def _default_policy(spec, plant=None):
    from .policy import factory
    return factory(spec,plant=plant)


def _default_episode(*args,**kwargs):
    from .environment import run_episode
    return run_episode(*args,**kwargs)


def _latest(directory):
    attempts=sorted(directory.glob('attempt_*'))
    if not attempts:return None
    path=attempts[-1]/'RESULT.json'
    if not path.is_file():
        return dict(status='failed_unknown',error=dict(type='InterruptedAttempt',
            message='Started attempt has no terminal RESULT; explicit retry creates a new immutable attempt'),
            _path=str(path))
    result=read(path);result['_path']=str(path);return result


def run_registered(root,directory,*,env_factory=None,policy_factory=None,episode_fn=None,
                   analyze_fn=None,retry_failed=False,max_pairs=None):
    root=Path(root).resolve();directory=Path(directory).resolve()
    plan=read(directory/'PLAN.json');manifest=read(directory/'MANIFEST.json');freeze=read(plan['freeze_path'])
    if plan.get('signature')!=digest({k:v for k,v in plan.items() if k!='signature'}) or manifest.get('signature')!=digest({k:v for k,v in manifest.items() if k!='signature'}):
        raise ValueError('Registration metadata drift')
    if sha(plan['freeze_path'])!=plan['freeze_sha256'] or manifest['signature']!=plan['manifest_signature']:
        raise ValueError('Freeze/manifest drift')
    verify_freeze(root,freeze)
    use_default_policy=policy_factory is None
    env_factory=env_factory or _default_env;policy_factory=policy_factory or _default_policy;episode_fn=episode_fn or _default_episode
    completed=failed=pending=attempted=0;began=time.perf_counter()
    for scene in plan['panel']['scenarios']:
        for policy in plan['policy_specs']:
            for config_id,config in plan['configurations'].items():
                key=scene['id']+'__'+policy['policy_id']+'__'+config_id
                if any(x in key for x in ('/','\\',':')):raise ValueError('Unsafe pair identity')
                pair=directory/'pairs'/key;previous=_latest(pair)
                if previous is not None and previous['status']=='completed':
                    journals=[json.loads(line) for line in (directory/'ATTEMPTS.jsonl').read_text(encoding='utf8').splitlines()]
                    terminals=[row for row in journals if row.get('event')=='finished'
                        and row.get('result_path')==previous['_path']]
                    if len(terminals)!=1 or sha(previous['_path'])!=terminals[0].get('result_sha256'):
                        raise ValueError('Completed result evidence drift or missing immutable terminal journal')
                    if not Path(previous['raw_path']).is_file() or sha(previous['raw_path'])!=previous['raw_sha256']:
                        raise ValueError('Completed raw evidence drift')
                    completed+=1;continue
                if previous is not None and not retry_failed:
                    failed+=1;continue
                if max_pairs is not None and attempted>=max_pairs:
                    pending+=1;continue
                verify_freeze(root,freeze)
                number=max([int(p.name.split('_')[-1]) for p in pair.glob('attempt_*')]+[0])+1
                attempt_id='attempt_'+str(number).zfill(4)
                identity=dict(scenario_id=scene['id'],policy_id=policy['policy_id'],configuration_id=config_id,
                    source_block_id=scene['source_block_id'],model_id=MODEL_ID,attempt_id=attempt_id,
                    seed=int(scene.get('initialization_seed',20261023)),freeze_signature=freeze['signature'])
                try:_reserve_attempt(plan,freeze,dict(pair=key,**identity))
                except RuntimeError as exc:
                    append(directory/'PENDING.jsonl',dict(utc=now(),reason=str(exc),**identity));pending+=1;continue
                target=pair/attempt_id;target.mkdir(parents=True,exist_ok=False)
                append(directory/'ATTEMPTS.jsonl',dict(event='started',utc=now(),**identity))
                attempted+=1;t0=time.perf_counter();payload=metrics=analysis=error=raw_sha=raw_path=None
                actual_manifest=None
                status='failed_unknown';source_unchanged=True
                with _Resources() as resources:
                    try:
                        env=env_factory(root,allowed_hours=allowed_hours(dict(scenarios=[scene])),sealed_days=SEALED_DAYS,
                            model_id=MODEL_ID,model_config=copy.deepcopy(config))
                        actual_manifest=copy.deepcopy(manifest)
                        actual_manifest['model_config_signature']=digest(config)
                        actual_manifest['configuration_id']=config_id
                        actual_manifest=_signed(actual_manifest)
                        controller=(policy_factory(copy.deepcopy(policy),plant=getattr(env,'plant',None))
                            if use_default_policy else policy_factory(copy.deepcopy(policy)))
                        payload=episode_fn(env,copy.deepcopy(scene),controller,identity['seed'],actual_manifest)
                        raw_path=Path(plan['raw_root'])/directory.name/key/attempt_id/'trajectory.json.gz'
                        raw_sha=_save_raw(raw_path,payload)
                        if payload.get('execution_model_id')!=MODEL_ID:
                            raise ValueError('Actual episode model identity differs from M21 registration')
                        factors=freeze.get('emissions_factor_binding')
                        metrics=compact_metrics(payload,env=env,factor_registry=factors['registry'] if factors else None)
                        if analyze_fn is not None:analysis=analyze_fn(payload,env)
                        if payload.get('error') is not None:error=payload['error']
                        if payload.get('summary',{}).get('status')=='completed' and metrics['complete'] and error is None:
                            status='completed'
                    except Exception as exc:
                        error=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc())
                    try:verify_freeze(root,freeze)
                    except ValueError as exc:
                        source_unchanged=False;status='failed_unknown'
                        error=dict(type='SourceDriftDuringEpisode',message=str(exc),previous_error=error)
                requested=scene['duration_steps']*scene.get('step_minutes',15.)
                record=dict(schema=SCHEMA+'.result',**identity,status=status,phase=plan['phase'],
                    canonical_class=scene.get('canonical_class'),season_quarter=scene.get('season_quarter'),
                    raw_path=str(raw_path) if raw_path else None,raw_sha256=raw_sha,
                    raw_bytes=raw_path.stat().st_size if raw_path and raw_path.exists() else None,
                    elapsed_seconds=time.perf_counter()-t0,metrics=metrics,analysis=analysis,error=error,
                    unknown_tail_minutes=metrics['unknown_tail_minutes'] if metrics else requested,
                    source_unchanged=source_unchanged,source_binding_sha256=digest(freeze['source_sha256']),
                    scenario_sha256=digest(scene),policy_sha256=digest(policy),configuration_sha256=digest(config),
                    manifest_signature=actual_manifest['signature'] if actual_manifest is not None else None,
                    resource_measurement=resources.receipt(),no_zero_substitute_for_failure=True)
                write_new(target/'RESULT.json',record)
                append(directory/'ATTEMPTS.jsonl',dict(event='finished',utc=now(),status=status,
                    result_path=str(target/'RESULT.json'),result_sha256=sha(target/'RESULT.json'),
                    raw_sha256=raw_sha,**identity))
                append(directory/'COMPACT_RESULTS.jsonl',record)
                if status=='completed':completed+=1
                else:failed+=1
                print(json.dumps(dict(pair=key,attempt=attempt_id,status=status,elapsed_seconds=record['elapsed_seconds'])),flush=True)
                if payload is not None:del payload
                if 'env' in locals():del env
                import gc
                gc.collect()
                if not source_unchanged:
                    raise ValueError('Frozen source drift during episode; failed attempt retained and dependent campaign stopped')
    summary=dict(schema=SCHEMA+'.campaign_result',model_id=MODEL_ID,phase=plan['phase'],
        freeze_signature=freeze['signature'],registered_pairs=plan['registered_pairs'],
        source_binding_sha256=digest(freeze['source_sha256']),input_binding_sha256=digest(freeze['runtime_inputs']),
        actual_attempts_this_invocation=attempted,completed=completed,failed=failed,pending=pending,
        elapsed_seconds=time.perf_counter()-began,
        pilot_qualified=plan['phase']=='pilot' and completed>0 and failed==0 and pending==0,
        original_final12_evaluated=False,all_failed_unknown_attempts_retained=True)
    invocation=len(list(directory.glob('CAMPAIGN_RESULT_*.json')))+1
    write_new(directory/('CAMPAIGN_RESULT_'+str(invocation).zfill(4)+'.json'),summary)
    return summary
