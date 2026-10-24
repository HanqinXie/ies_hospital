"""Post-hoc two-case native-evidence extraction. Stdlib only; no physics/LP."""
from pathlib import Path
import csv,gzip,hashlib,json,math,collections,sys
ROOT=Path(r'D:\hospital_ies_v21_20261023')
E=Path(r'E:\hospital_ies_v21_20261023')
OUT=Path(__file__).resolve().parent
SCENE='m21_L1_55e24889a6e1b237d15a'
BLOCK='m21_final12_b046c742da517542'
CSV=E/'final_blind01/analysis01/tables/CASE_METRICS.csv'
SOURCE='4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def need(v,m):
 if not v:raise ValueError(m)
def write(p,v):
 with (OUT/p).open('x',encoding='utf-8',newline='\n') as f:json.dump(v,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
def csvout(name,rows):
 with (OUT/name).open('x',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def material(q,short):return short>.001 and (short>=.01 or (q>.001 and short/q>=.001))
def merge(xs):
 out=[]
 for a,b in sorted(xs):
  if out and a<=out[-1][1]+1e-9:out[-1][1]=max(out[-1][1],b)
  else:out.append([a,b])
 return out
known={CSV:'3cab466b53b0080f666dce46f003a92fe3ad3c478e353033bc634d604011ac93',
 E/'final_blind01/analysis01/PARALLEL_ANALYSIS_RECEIPT.json':'994a42eb6b9382e5a2645c470e2af0655a6d06227b2a0d22aa9045fc4c92dc59',
 E/'reviews/final240_root_terminal_admission01/SERIAL_METADATA_READ_ADMISSION.json':'30bd6803073281446d69c86ae540f66d6f9a393eef305fb7570a29b5afb60de5',
 E/'final_blind01/physical_audit01/POOL_PHYSICAL_AUDIT_RECEIPT.json':'c0b36604f939a8acab18a30cf6df4033b67787a41461618141d490737382b2b6',
 E/'final_blind01/physical_audit01/POOL_INVENTORY.json':'288c384cac500bae434efb11293fc1b3b92bb4fed3e5f99d3eb8f5713153694e'}
for p,h in known.items():need(sha(p)==h,'known metadata drift: '+str(p))
inv=read(E/'final_blind01/physical_audit01/POOL_INVENTORY.json')
fr=read(inv['attempts'][0]['freeze_path'])
source_paths=('tools/sci_engineering/policy.py','tools/sci_engineering/planner.py','tools/sci_engineering/environment.py',
 'tools/sci_engineering/runner.py','tools/model_closeout/readiness.py','src/hospital_ies/state_transition.py',
 'tools/operating_response/execution.py','src/hospital_ies/data_contract.py')
source_bytes={str(ROOT/p):sha(ROOT/p) for p in source_paths}
for p in source_paths:need(source_bytes[str(ROOT/p)]==fr['source_sha256'][p],'frozen source drift: '+p)
with CSV.open(encoding='utf-8-sig',newline='') as f:rows=[r for r in csv.DictReader(f) if r['scenario_id']==SCENE and r['policy_id'] in ('A_sci_v21','C_sci_v21')]
need(len(rows)==2 and {r['policy_id'] for r in rows}=={'A_sci_v21','C_sci_v21'},'exact two cases')
write('SELECTION_AND_SCOPE.json',dict(selection='post_hoc_after_final_outcomes',scenario_id=SCENE,source_block_id=BLOCK,
 independent_new_n=0,preregistered_representative=False,original_eight_preregistered_raw_selections_modified=False,
 scientific_acceptance_issued=False,physics_attempts=0,optimizer_calls=0,sidecar_body_decompressed=False,
 original_native_or_auditor_or_D_modified=False,source_binding_sha256=SOURCE,
 known_metadata=[dict(path=str(p),sha256=h) for p,h in known.items()],source_code_sha256=source_bytes,
 exploratory_readonly_scan_failures=['Three exploratory rg probes used nonexistent/globbed explicit paths and reported Windows path errors. Correct exact source paths were subsequently read; no model command, attempt, or output was changed.']))
csvout('CASE_METRICS_TWO_FIXED_ROWS.csv',rows)
payloads={};sources=[];native=[];commands=[];traces=[];events=[];joins=[];interruptions=[];integrals={}
for row in rows:
 alias=row['policy_alias'];need(alias in ('A','C'),'alias')
 rp=Path(row['result_path']);need(sha(rp)==row['result_sha256'],'RESULT drift')
 result=read(rp);raw=Path(result['raw_path']);side=Path(result['solver_row_evidence']['path'])
 need(str(raw)==row['raw_path'] and result['raw_sha256']==row['raw_sha256'],'raw declaration')
 before=dict(raw_sha256=sha(raw),raw_bytes=raw.stat().st_size,sidecar_sha256=sha(side),sidecar_bytes=side.stat().st_size,result_sha256=sha(rp))
 need(before['raw_sha256']==result['raw_sha256'] and before['raw_bytes']==result['raw_bytes'],'raw bytes')
 need(before['sidecar_sha256']==result['solver_row_evidence']['gzip_sha256'] and before['sidecar_bytes']==result['solver_row_evidence']['gzip_bytes'],'sidecar bytes')
 with gzip.open(raw,'rt',encoding='utf-8') as f:v=json.load(f)
 need(v['identity']['scenario_id']==SCENE and v['identity']['policy_id']==row['policy_id'] and v['identity']['simulator_hash']==SOURCE,'raw identity')
 need(v['scenario']['source_block_id']==BLOCK and v['execution_model_id']=='M21_sci_engineering','raw scene/model')
 need(v['solver_row_evidence']==result['solver_row_evidence'],'raw sidecar declaration')
 need(v.get('error') is None and not v.get('solver_errors'),'raw failure')
 payloads[alias]=v
 sources.append(dict(policy=alias,result_path=str(rp),raw_path=str(raw),sidecar_path=str(side),before=before))
 totals=collections.defaultdict(float);ints=collections.defaultdict(list)
 scene=v['scenario'];incident=float(scene.get('first_event_minute',scene['fault_start_minute']));recovery=float(scene.get('recovery_start_minute',incident+scene['fault_duration_minutes']))
 initial=incident+float(scene.get('initial_handoff_window_minutes',15.))
 byid={d['command_generated']['id']:d for d in v['decisions']}
 def addjoin(k,value):
  expected=float(row[k]);difference=value-expected;need(math.isclose(value,expected,rel_tol=0.,abs_tol=1e-9),'metric join '+alias+' '+k)
  joins.append(dict(policy=alias,metric=k,recomputed_native_intervals=value,CASE_METRICS_value=expected,difference=difference,join_check='abs_difference_le_1e-9_not_a_physical_tolerance'))
 for idx,s in enumerate(v['segments']):
  a,b=s['start_minute'],s['end_minute'];dt=(b-a)/60.;led=s['service_request_ledger'];act=s['action'];dg=s['diesel_local_response']
  d=byid[s['command_id']];c=d['command_generated'];j=c['joint_dispatch']['diagnostic'];m=j['model_closeout'];rr=m['critical_reserve_rule']
  item=dict(policy=alias,segment_index=idx,start_minute=a,end_minute=b,dt_minutes=b-a,command_id=s['command_id'],generated_minute=c['generated_minute'],arrival_minute=c['arrival_minute'],
   observed_outage_minutes=d['policy_diagnostic']['coordination']['observed_outage_minutes'],causal_release_active=rr['causal_release_active'],reserve_hours=rr['reserve_hours'],
   command_diesel_ceiling_kW=c['dispatch']['diesel_kW'],arrived_diesel_permission_kW=s['arrived_source_permissions']['diesel_kW'],effective_diesel_limit_kW=s['execution_limits']['effective_diesel_limit_kW'],
   actual_diesel_kW=act['diesel_kW'],actual_grid_kW=act['grid_import_kW'],grid_hardware_fraction=s['hardware_availability']['GRID'],grid_bus_state=s['execution_limits']['grid_bus_state'],
   diesel_status_pre=s['pre_state']['diesel_status'],diesel_budget_pre_kWh_equivalent=s['pre_state']['diesel_energy_remaining_kWh'],diesel_budget_post_kWh_equivalent=s['post_state']['diesel_energy_remaining_kWh'],
   DG_shaft_work_kWh=dg['mechanical_work_kWh'],DG_electrical_work_kWh=dg['electrical_work_kWh'],DG_loss_work_kWh=dg['loss_work_kWh'],DG_rotor_delta_kWh=dg['rotor_delta_kWh'],
   DG_budget_debit_kWh_equivalent=dg['fuel_budget_debit_kWh_equivalent'],cost_USD_native_interval=s['cost_USD_step'],
   ups_initial_kWh=s['m21_storage']['ups']['initial_kWh'],ups_final_kWh=s['m21_storage']['ups']['final_kWh'],cold_initial_kWh=s['m21_storage']['cold_store']['initial_kWh'],cold_final_kWh=s['m21_storage']['cold_store']['final_kWh'])
  for i in range(3):item['GT_'+str(i+1)+'_actual_kW']=act['gt_electric_kW'][i]
  for g in range(4):
   anymat=False
   for k,carrier in enumerate(('e','h','c')):
    prefix='P'+str(g)+'_'+carrier
    q=led['original_demand_kW'][g][k];cut=led['active_cut_kW'][g][k];un=led['connected_unserved_kW'][g][k];delivered=led['delivered_kW'][g][k];short=cut+un
    need(abs(q-delivered-short)<=1e-7,'ledger identity')
    for label,val in (('requested',q),('cut',cut),('connected_unserved',un),('delivered',delivered),('short',short)):item[prefix+'_'+label+'_kW']=val
    mat=material(q,short);item[prefix+'_material']=mat;anymat|=mat
    if mat:ints[prefix].append([a,b])
    for name,val in (('requested_kWh',q),('delivered_kWh',delivered),('ENS_kWh',short),('disconnected_ENS_kWh',cut),('connected_unserved_ENS_kWh',un)):totals[prefix+'_'+name]+=val*dt
    cuts=sorted({a,b,*[t for t in (incident,initial,recovery) if a<t<b]})
    for x,y in zip(cuts,cuts[1:]):
     phase='preparation' if x<incident else 'recovery' if x>=recovery else 'initial_handoff' if x<initial else 'endurance'
     totals['phase_'+phase+'_'+prefix+'_ENS_kWh']+=short*(y-x)/60.
   if anymat:ints['P'+str(g)+'_union'].append([a,b])
  for name,val in (('energy_DG_mechanical_work_kWh',dg['mechanical_work_kWh']),('energy_DG_electrical_work_kWh',dg['electrical_work_kWh']),('energy_DG_loss_work_kWh',dg['loss_work_kWh']),('energy_DG_rotor_delta_kWh',dg['rotor_delta_kWh']),('energy_DG_fuel_budget_debit_kWh_equivalent',dg['fuel_budget_debit_kWh_equivalent'])):totals[name]+=val
  for name,val in (('energy_grid_import_kWh',act['grid_import_kW']),('energy_diesel_kWh',act['diesel_kW']),('energy_gt_electric_kWh',sum(act['gt_electric_kW']))):totals[name]+=val*dt
  totals['native_cost_sum']+=s['cost_USD_step']
  native.append(item)
 for prefix,xs in ints.items():
  merged=merge(xs)
  vals=dict(material_minutes=sum(b-a for a,b in merged),longest_material_minutes=max((b-a for a,b in merged),default=0.),interruption_count=len(merged),repeat_interruption_count=max(0,len(merged)-1))
  for label,val in vals.items():totals[prefix+'_'+label]=val
  for idx,(a,b) in enumerate(merged):interruptions.append(dict(policy=alias,service=prefix,interval_index=idx,start_minute=a,end_minute=b,minutes=b-a))
 for g in range(4):
  for suffix in ('union','e','h','c'):
   prefix='P'+str(g)+'_'+suffix
   for field in ('material_minutes','longest_material_minutes','interruption_count','repeat_interruption_count'):totals.setdefault(prefix+'_'+field,0.)
 totals['energy_cost_USD']=totals['native_cost_sum']+.30*(totals['energy_DG_mechanical_work_kWh']-totals['energy_DG_electrical_work_kWh'])
 for key,val in totals.items():
  if key in row and row[key]!='':addjoin(key,val)
 integrals[alias]=dict(totals)
 for d in v['decisions']:
  t=d['decision_time_min'];c=d['command_generated'];j=c['joint_dispatch']['diagnostic'];m=j['model_closeout'];rr=m['critical_reserve_rule'];support=m['critical_reserve_support'];co=d['policy_diagnostic']['coordination']
  actual=[e['time_minute'] for e in v['events'] if e['kind']=='command_executed' and e['command_id']==c['id']]
  arrived=[e['time_minute'] for e in v['events'] if e['kind']=='command_arrived' and e['command_id']==c['id']]
  command=dict(policy=alias,decision_time_min=t,observation_time_min=d['observation_time_min'],command_id=c['id'],generated_minute=c['generated_minute'],arrival_minute=c['arrival_minute'],logged_arrival_minute=arrived[0] if len(arrived)==1 else 'UNKNOWN',logged_execution_minute=actual[0] if len(actual)==1 else 'UNKNOWN',
   control_available=d['features']['control_available'],observed_grid_fraction=d['features']['availability_GRID'],observed_outage_minutes=co['observed_outage_minutes'],observed_age_available_time_min=d['feature_metadata']['grid_outage_elapsed_minutes']['available_time_min'],
   observed_age_valid_time_min=d['feature_metadata']['grid_outage_elapsed_minutes']['valid_time_min'],critical_non_diesel_ready=co['critical_non_diesel_ready'],reserve_rule=co['reserve_rule'],release_after_minutes=co['release_after_minutes'],release_floor_hours=co['release_floor_hours'],
   causal_release_active=rr['causal_release_active'],reserve_hours=rr['reserve_hours'],reserve_preference_active=m['critical_reserve_active'],reserve_excess_kWh=m['critical_reserve_excess_kWh'],remaining_shaft_work_kWh=rr['remaining_shaft_work_kWh'],reference_critical_electric_kW=rr['reference_critical_electric_kW'],reference_shaft_kW=rr['reference_shaft_kW'],
   support_electric_ceiling_kW=support['electric_support_ceiling_kW'],command_diesel_ceiling_kW=c['dispatch']['diesel_kW'],command_grid_ceiling_kW=c['dispatch']['grid_import_kW'],command_P3_e_cut_kW=c['disconnected'][3][0],
   future_exogenous_input=j['future_exogenous_input'],true_repair_or_outage_endpoint_used=rr['true_repair_or_outage_endpoint_used'],future_rollouts_online=d['policy_diagnostic']['future_rollouts_online'],
   projection_changed='UNKNOWN' if d['policy_diagnostic']['projection_changed'] is None else d['policy_diagnostic']['projection_changed'],objective_order_json=json.dumps(j['objective_order'],ensure_ascii=False,separators=(',',':')))
  for i in range(3):command['command_GT_'+str(i+1)+'_kW']=c['dispatch']['gt_electric_kW'][i]
  commands.append(command)
  traces.append(dict(policy=alias,decision_index=v['decisions'].index(d),decision_time_min=t,feature_metadata_age=d['feature_metadata']['grid_outage_elapsed_minutes'],
   coordination=co,critical_reserve=m,objective_order=j['objective_order'],requested_command=d['requested_command'],permitted_command=d['permitted_command'],
   command_dispatch=c['dispatch'],command_disconnected=c['disconnected'],actual_execution=d['actual_execution'],operating_readiness=d['operating_readiness'],
   engineering_execution=d['engineering_execution'],projection_change_kW=d['policy_diagnostic']['projection_change_kW'],projection_changed=d['policy_diagnostic']['projection_changed']))
 for e in v['events']:
  if e['kind'] in ('command_arrived','command_executed','load_connection_restored','grid_bus_state_changed','diesel_state_changed','exogenous_fault_start','exogenous_fault_end','disconnected_port_demand_changed'):
   events.append(dict(policy=alias,**e))
 after=dict(raw_sha256=sha(raw),raw_bytes=raw.stat().st_size,sidecar_sha256=sha(side),sidecar_bytes=side.stat().st_size,result_sha256=sha(rp))
 need(after==before,'compressed evidence changed during extraction');sources[-1]['after']=after
csvout('NATIVE_INTERVALS_A_C.csv',native);csvout('COMMANDS_A_C.csv',commands);csvout('METRIC_JOIN.csv',joins);csvout('MATERIAL_INTERRUPTION_INTERVALS.csv',interruptions)
write('COMMAND_TRACE_SELECTED_NATIVE_FIELDS.json',traces);write('SOURCES_BEFORE_AFTER.json',sources)
with (OUT/'EVENTS_SELECTED_NATIVE.jsonl').open('x',encoding='utf-8',newline='\n') as f:
 for e in events:f.write(json.dumps(e,ensure_ascii=False,allow_nan=False)+'\n')
A=[r for r in native if r['policy']=='A'];C=[r for r in native if r['policy']=='C'];need(len(A)==len(C),'same native grid length')
paired=[]
for a,c in zip(A,C):
 need((a['start_minute'],a['end_minute'])==(c['start_minute'],c['end_minute']),'native grids differ; do not interpolate')
 q=dict(start_minute=a['start_minute'],end_minute=a['end_minute'],dt_minutes=a['dt_minutes'],A_segment_index=a['segment_index'],C_segment_index=c['segment_index'])
 for field in ('P3_e_requested_kW','P3_e_cut_kW','P3_e_connected_unserved_kW','P3_e_short_kW','P3_e_delivered_kW','P3_e_material','actual_diesel_kW','command_diesel_ceiling_kW','arrived_diesel_permission_kW','effective_diesel_limit_kW','diesel_budget_pre_kWh_equivalent','diesel_budget_post_kWh_equivalent','DG_shaft_work_kWh','DG_electrical_work_kWh','DG_loss_work_kWh','GT_1_actual_kW','GT_2_actual_kW','GT_3_actual_kW','actual_grid_kW','grid_bus_state'):
  q['A_'+field]=a[field];q['C_'+field]=c[field]
  if type(a[field]) in (int,float) and type(c[field]) in (int,float):q['A_minus_C_'+field]=a[field]-c[field]
 q['P3_e_ENS_reduction_C_minus_A_kWh']=(c['P3_e_short_kW']-a['P3_e_short_kW'])*a['dt_minutes']/60.
 q['material_minutes_reduction_C_minus_A']=(int(c['P3_e_material'])-int(a['P3_e_material']))*a['dt_minutes'];paired.append(q)
csvout('PAIRED_NATIVE_INTERVALS.csv',paired)
write('AGGREGATES.json',dict(post_hoc=True,integrals=integrals,CASE_delta_A_minus_C={k:float(next(r for r in rows if r['policy_alias']=='A')[k])-float(next(r for r in rows if r['policy_alias']=='C')[k]) for k in ('P0_e_ENS_kWh','P1_e_ENS_kWh','P2_e_ENS_kWh','P3_e_ENS_kWh','P3_e_disconnected_ENS_kWh','P3_e_connected_unserved_ENS_kWh','P3_e_material_minutes','P3_e_longest_material_minutes','energy_cost_USD','total_CO2_kg_conditional','energy_DG_mechanical_work_kWh','energy_DG_electrical_work_kWh','energy_grid_import_kWh','energy_gt_electric_kWh')},
 pair_grid_exactly_equal=True,native_intervals_per_policy=len(A),paired_integral_ENS_reduction=sum(q['P3_e_ENS_reduction_C_minus_A_kWh'] for q in paired),
 material_minutes_reduction=sum(q['material_minutes_reduction_C_minus_A'] for q in paired),metrics_join_count=len(joins),metrics_join_max_abs_error=max(abs(x['difference']) for x in joins)))
for p,h in known.items():need(sha(p)==h,'metadata changed after extraction')
for p,h in source_bytes.items():need(sha(p)==h,'source changed after extraction')
print(json.dumps(dict(status='posthoc_extraction_complete_no_scientific_gate',native_intervals_per_policy=len(A),commands_per_policy=len(commands)//2,
 metrics_join_count=len(joins),metric_join_max_abs_error=max(abs(x['difference']) for x in joins),
 P3_e_ENS_reduction_C_minus_A_kWh=sum(q['P3_e_ENS_reduction_C_minus_A_kWh'] for q in paired),
 material_minutes_reduction=sum(q['material_minutes_reduction_C_minus_A'] for q in paired),raw_compressed_before_after_identical=True,sidecar_compressed_hashes_only=True),ensure_ascii=False))
