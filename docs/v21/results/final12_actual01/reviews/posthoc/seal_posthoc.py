from pathlib import Path
from datetime import datetime,timezone
import json,csv,hashlib,sys,time
O=Path(__file__).resolve().parent
started=datetime.now(timezone.utc).isoformat();t0=time.perf_counter()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def need(v,m):
 if not v:raise ValueError(m)
def rows(p):
 with (O/p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
scope=read(O/'SELECTION_AND_SCOPE.json')
need(scope['selection']=='post_hoc_after_final_outcomes' and scope['independent_new_n']==0 and scope['scientific_acceptance_issued'] is False,'posthoc scope')
for x in scope['known_metadata']:need(sha(x['path'])==x['sha256'],'fixed metadata changed')
for p,h in scope['source_code_sha256'].items():need(sha(p)==h,'frozen source changed')
sources=read(O/'SOURCES_BEFORE_AFTER.json')
for s in sources:
 need(s['before']==s['after'],'source before/after')
 for kind in ('raw','sidecar'):
  p=Path(s[kind+'_path']);need(sha(p)==s['before'][kind+'_sha256'] and p.stat().st_size==s['before'][kind+'_bytes'],'current compressed evidence')
 need(sha(s['result_path'])==s['before']['result_sha256'],'current RESULT')
case={r['policy_alias']:r for r in rows('CASE_METRICS_TWO_FIXED_ROWS.csv')}
for g in (0,1):
 for k in ('e','h','c'):
  for metric in ('material_minutes','longest_material_minutes','interruption_count','repeat_interruption_count'):
   key='P'+str(g)+'_'+k+'_'+metric;need(case['A'][key]==case['C'][key],'critical material differs')
 for metric in ('material_minutes','longest_material_minutes','interruption_count','repeat_interruption_count'):
  key='P'+str(g)+'_union_'+metric;need(case['A'][key]==case['C'][key],'critical union differs')
for k in ('critical_initial_handoff_gap_minutes','critical_post_handoff_gap_minutes','critical_recovery_gap_minutes'):need(case['A'][k]==case['C'][k],'three phase critical gap differs')
for k in ('h','c'):
 for metric in ('ENS_kWh','material_minutes','longest_material_minutes','interruption_count','repeat_interruption_count'):
  key='P3_'+k+'_'+metric;need(case['A'][key]==case['C'][key],'P3 heat/cold differs')
need(float(case['A']['P0_e_ENS_kWh'])-float(case['C']['P0_e_ENS_kWh'])==4.063206741358955e-7,'preserve tiny critical ENS')
joins=rows('METRIC_JOIN.csv');need(len(joins)==362 and all(float(r['difference'])==0 for r in joins),'exact joins')
pair=rows('PAIRED_NATIVE_INTERVALS.csv');need(len(pair)==101,'pair count')
need(all(float(r['A_minus_C_'+k])==0 for r in pair for k in ('GT_1_actual_kW','GT_2_actual_kW','GT_3_actual_kW','actual_grid_kW')),'GT/grid trajectories equal')
commands=[]
for name in ('extract_posthoc01','explanation_verify01'):
 c=read(O/'logs'/(name+'.command.json'));need(c['exit_code']==0,'actual logged exit')
 need((O/'logs'/(name+'.stderr.log')).stat().st_size==0,'logged stderr')
 commands.append(c)
need(read(O/'EXPLANATION_VERIFICATION.json')['status']=='posthoc_explanation_verified','explanation status')
report=(O/'POSTHOC_ENGINEERING_EXPLANATION.md').read_text(encoding='utf-8')
need('事后' in report and 'UNKNOWN' in report and '4.063206741358955e-7' in report and '不能称恢复收益' in report,'required limitations')
files=[dict(path=p.relative_to(O).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(O.rglob('*')) if p.is_file()]
receipt=dict(schema='hospital_ies.final12_A_C_posthoc_explanation.seal.v1',status='posthoc_outputs_byte_sealed_no_scientific_gate',
 selection='post_hoc_after_final_outcomes',independent_new_n=0,scientific_acceptance_issued=False,
 actual_seal_command=[sys.executable,'-B',str(Path(__file__).resolve())],cwd=str(Path.cwd()),started_utc=started,
 finished_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.perf_counter()-t0,exit_code=0,
 upstream_commands=commands,source_result_raw_sidecar_current_bytes_rechecked=True,sidecar_body_decompressed=False,
 current_frozen_referenced_source_bytes_rechecked=True,native_intervals_per_policy=101,native_commands_per_policy=48,
 metric_joins=362,metric_join_max_abs_error=0.,GT_and_grid_trajectories_exactly_equal=True,
 critical_material_union_and_three_phase_gap_exactly_equal=True,tiny_critical_ENS_increase_retained=True,
 physics_attempts=0,optimizer_calls=0,preregistered_eight_selections_modified=False,
 file_count_excluding_this_seal=len(files),total_bytes_excluding_this_seal=sum(x['bytes'] for x in files),files=files)
with (O/'FINAL_POSTHOC_SEAL.json').open('x',encoding='utf-8',newline='\n') as f:json.dump(receipt,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
for x in files:need(sha(O/x['path'])==x['sha256'] and (O/x['path']).stat().st_size==x['bytes'],'output changed after sealing')
print(json.dumps(dict(status=receipt['status'],exit_code=0,files=len(files),bytes=sum(x['bytes'] for x in files),seal_path=str(O/'FINAL_POSTHOC_SEAL.json'),seal_sha256=sha(O/'FINAL_POSTHOC_SEAL.json'),
 report_sha256=sha(O/'POSTHOC_ENGINEERING_EXPLANATION.md'),native_intervals_sha256=sha(O/'NATIVE_INTERVALS_A_C.csv'),paired_intervals_sha256=sha(O/'PAIRED_NATIVE_INTERVALS.csv'),sources_sha256=sha(O/'SOURCES_BEFORE_AFTER.json')),ensure_ascii=False))
