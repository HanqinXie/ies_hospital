# 原冻源码片段，只读引用

不执行或导入物理模块。完整文件 SHA 位于 SELECTION_AND_SCOPE.json。

## tools/sci_engineering/policy.py:89-109

~~~python
89: def reserve_reference(critical_loads, *, remaining_kWh, ec_kWe_per_kWc,
90:                       shaft_loss_bound_kW, minimum_hours=1., options=None):
91:     cfg = coordination_options(options)
92:     reference = c19_reserve_rule(critical_loads,remaining_kWh=remaining_kWh,
93:         ec_kWe_per_kWc=ec_kWe_per_kWc,shaft_loss_bound_kW=shaft_loss_bound_kW,
94:         minimum_hours=minimum_hours)
95:     reference.update(reserve_rule=cfg['reserve_rule'], reserve_enabled=cfg['reserve_enabled'],
96:         observed_outage_minutes=cfg['observed_outage_minutes'],
97:         critical_non_diesel_ready=cfg['critical_non_diesel_ready'], causal_release_active=False,
98:         true_repair_or_outage_endpoint_used=False)
99:     if cfg['reserve_rule'] == 'fixed_hours':
100:         reference['reserve_hours'] = cfg['fixed_reserve_hours']
101:     elif cfg['reserve_rule'] == 'causal_release':
102:         elapsed = cfg['observed_outage_minutes']
103:         active = cfg['critical_non_diesel_ready'] and elapsed >= cfg['release_after_minutes']
104:         reference['causal_release_active'] = active
105:         if active:
106:             # Observed running alternative support permits release. This is a
107:             # registered rule, never an estimate of the true outage endpoint.
108:             reference['reserve_hours'] = min(reference['reserve_hours'],cfg['release_floor_hours'])
109:     return reference
~~~

## tools/sci_engineering/policy.py:176-201

~~~python
176: def _non_diesel_ready(features, loads, plant, auxiliary):
177:     if plant is None:
178:         return False
179:     electric = sum(float(features.get(f'pre_GT_{i}_kW',0.))*float(features.get(f'availability_GT_{i}',0.)) for i in (1,2,3))
180:     electric += float(features.get('grid_import_prev_kW',0.))*float(features.get('availability_GRID',0.))
181:     cold = float(loads[:2,2].sum())
182:     critical_power = float(loads[:2,0].sum()) + cold*plant.ec_electric_kW_per_rt/RT_TO_KW
183:     # All considered cooling requires the shared tower. Running GT support is
184:     # counted at measured output; pending starts and unused capacity are zero.
185:     tower_bank = sum(float(features.get(f'availability_TOWER_{i}',1.)) for i in (1,2,3,4))/4.
186:     rejection = plant.cooling_tower_rejection_kW*float(features.get('availability_TOWER',0.))*tower_bank
187:     tower = rejection >= cold*(1.+plant.ec_electric_kW_per_rt/RT_TO_KW)
188:     ec = sum(plant.electric_chillers_rt[i-1]*RT_TO_KW*float(features.get(f'availability_EC_{i}',0.)) for i in (1,2,3))
189:     boilers = sum(float(features.get(f'availability_BOILER_{i}',0.)) for i in (1,2,3))
190:     heat = float(loads[:2,1].sum())
191:     heat_cap = plant.boiler_steam_lbph/3.*boilers*plant.steam_kWh_per_lb*plant.exchanger_eff
192:     auxiliary_ready = True
193:     for g in (0,1):
194:         if loads[g,1] > 1e-9 or loads[g,2] > 1e-9:
195:             auxiliary_ready &= auxiliary.get(f'AUX_FAN_P{g}',1.) >= 1.-1e-9
196:         if loads[g,2] > 1e-9:
197:             auxiliary_ready &= auxiliary.get('AUX_CHW_PUMP',1.) >= 1.-1e-9
198:         if loads[g,1] > 1e-9:
199:             auxiliary_ready &= auxiliary.get('AUX_HEAT_PUMP',1.) >= 1.-1e-9
200:     return bool(auxiliary_ready and electric >= critical_power and (cold <= 1e-9 or (tower and ec >= cold)) and heat_cap >= heat)
201: 
~~~

## tools/sci_engineering/policy.py:298-307

~~~python
298:             diagnostic.update(upper_optimizer_called=False,rule_diagnostic=rule)
299:             return request,diagnostic
300:         options = copy.deepcopy(self.config)
301:         options['observed_outage_minutes'] = float(features.get('grid_outage_elapsed_minutes',0.))
302:         options['critical_non_diesel_ready'] = _non_diesel_ready(features,current,self.plant,auxiliary[-1])
303:         request = validate_action({'state_coordinated_joint':dict(options=copy.deepcopy(DEFAULT_JOINT),
304:             coordination=options,risk_loads_kW=np.min(demands,axis=0).tolist())})
305:         diagnostic.update(upper_optimizer_called=True,coordination=copy.deepcopy(options))
306:         return request,diagnostic
307: 
~~~

## tools/sci_engineering/planner.py:383-390

~~~python
383:     reserve_active=bool(coordination['critical_reserve'] and command_support_active
384:         and state.diesel_energy_remaining_kWh>0.)
385:     reserve_rule=reserve_reference(loads[:2],remaining_kWh=state.diesel_energy_remaining_kWh,
386:         ec_kWe_per_kWc=plant.ec_electric_kW_per_rt/RT_TO_KW,
387:         shaft_loss_bound_kW=support['shaft_loss_bound_kW'],minimum_hours=support_hours,options=coordination)
388:     reserve_support=diesel_support_budget(readiness,rated_kW=plant.emergency_diesel_kW,
389:         remaining_kWh=state.diesel_energy_remaining_kWh,support_minutes=60.*reserve_rule['reserve_hours'],
390:         startup_delay_minutes=float((config or {}).get('diesel_start_delay_minutes',0.)))
~~~

## tools/sci_engineering/planner.py:695-699

~~~python
695:     if reserve_active:
696:         add({diesel_ceiling:reserve_rule['reserve_hours'],reserve_excess:-1.},-np.inf,
697:             state.diesel_energy_remaining_kWh-reserve_support['overhead_shaft_work_kWh'],
698:             'preference.current_critical_reserve','shaft equivalent kWh',kind='preference',
699:             source='current P0/P1 demand and existing conversion auxiliary reference; remaining shaft budget; no outage endpoint')
~~~

## tools/sci_engineering/planner.py:1225-1259

~~~python
1225:         # count, without crediting its output in current/pre-ready balances.
1226:         third_GT=[item for item in standby if item[0]=='standby_third_GT_exposure']
1227:         online_count=[item for item in standby if item[0]!='standby_third_GT_exposure']
1228:         reserve_preference=[]
1229:         if reserve_active:
1230:             co=vector();co[reserve_excess]=1.
1231:             reserve_preference=[('current_critical_energy_reserve',co,'shaft equivalent kWh soft reserve excess',None)]
1232:         grid_target_preference=[]
1233:         if grid_target_case is not None:
1234:             for group in (0,1):
1235:                 co=vector()
1236:                 for carrier in range(3):
1237:                     co[int(grid_target_case['credited_unmet'][3*group+carrier])]=1./max(1.,float(loads[:,carrier].sum()))
1238:                 grid_target_preference.append(('grid_open_target_'+GROUPS[group],co,
1239:                     'conditional ordinary GT target critical deficit; no promised completion time',None))
1240:         # The two registered mechanisms are independent even when the reserve
1241:         # preference becomes inactive (normal grid, or exhausted shaft budget).
1242:         critical_preparation=[item for item in preparation if item[0] in ('preparation_P0','preparation_P1')]
1243:         other_preparation=[item for item in preparation if item[0] not in ('preparation_P0','preparation_P1')]
1244:         common=(protection_preference+command_support_preference+grid_target_preference)
1245:         preparation_first = (reserve_active if coordination['legacy_C19_order']
1246:             else coordination['critical_preparation_first'])
1247:         if preparation_first:
1248:             ordered=(common+conditional_critical+bridge_preparation+critical_preparation+reserve_preference+service+
1249:                 conditional_general+other_preparation+commitment_preference+third_GT+endpoint_DG_preference+online_count)
1250:         else:
1251:             ordered=(common+reserve_preference+service+conditional_critical+conditional_general+bridge_preparation+preparation+
1252:                 commitment_preference+third_GT+endpoint_DG_preference+online_count)
1253:     else:
1254:         ordered = (preparation+command_support_preference+service+conditional_critical+conditional_general+standby if endurance_active else
1255:             service+conditional_critical+conditional_general+preparation+commitment_preference+standby)
1256:     if current_only:
1257:         # The common present protection preference remains behind P0/P1.
1258:         # M adds neither reserve nor any post-incident preparation preference.
1259:         ordered = (protection_preference if recovery else [])+service+conditional_critical+conditional_general
~~~

## tools/sci_engineering/planner.py:1633-1639

~~~python
1633:     diagnostic['model_closeout']=dict(critical_reserve_active=reserve_active,
1634:         critical_reserve_rule=reserve_rule,critical_reserve_support=reserve_support,
1635:         critical_reserve_excess_kWh=None if reserve_excess is None else float(x[reserve_excess]),
1636:         grid_open_bridge_requested=grid_bridge_request,
1637:         request_contact_variable_count=12,current_unmet_is_active_cut_intent=False,
1638:         original_current_critical_service_locks_unchanged=True)
1639:     startup_bridge=None
~~~

## tools/sci_engineering/environment.py:287-297

~~~python
287:             elapsed_seconds=time.perf_counter()-began,upper_optimizer_called='ordinary_dispatch' not in request,
288:             optimizer_warning=warning,solver_backend_layers=layers))
289:         delayed,release=self.delayed_at(self.time);self.sequence+=1
290:         command=dict(id=self.sequence,generated_minute=self.time,arrival_minute=release if delayed else self.time,
291:             request=request,dispatch=dispatch,disconnected=contacts.tolist(),reference_cooling_kW=float(loads[:,2].sum()),
292:             weight=0.,optimizer_warning=warning,execution_model_id=MODEL_ID,
293:             joint_dispatch=clean(dict(planning_interface=policy.PLANNING_INTERFACE,planned_current_action=planned,
294:                 diagnostic=diagnostic,readiness_context=context,planning_is_not_execution=True)))
295:         self.pending_commands.append(command);self.log('command_generated',command=command)
296:         return command
297: 
~~~

## tools/sci_engineering/runner.py:255-303

~~~python
255:         disconnected_ENS_kWh=0.,connected_unserved_ENS_kWh=0.) for k in CARRIERS} for p in PRIORITIES}
256:     intervals = {(p,k):[] for p in PRIORITIES for k in CARRIERS}
257:     unions = {p:[] for p in PRIORITIES}; critical=[]; segments=payload.get('segments',[])
258:     scene = payload.get('scenario',{})
259:     incident = float(scene.get('first_event_minute',scene.get('fault_start_minute',0.)) or 0.)
260:     recovery = float(scene.get('recovery_start_minute',incident+float(scene.get('fault_duration_minutes',0.))))
261:     initial_end = incident+float(scene.get('initial_handoff_window_minutes',15.))
262:     phase = {name:{p:{k:0. for k in CARRIERS} for p in PRIORITIES}
263:              for name in ('preparation','initial_handoff','endurance','recovery')}
264:     energy = Counter(); storage = {}; observed = 0.; gap=False
265:     for seg in segments:
266:         a,b = float(seg['start_minute']),float(seg['end_minute'])
267:         if b<=a or a<observed-1e-8:
268:             raise ValueError('Actual service segment times overlap or are invalid')
269:         if a>observed+1e-8:gap=True
270:         observed=b; dt=(b-a)/60.
271:         ledger=seg.get('service_request_ledger')
272:         if not isinstance(ledger,dict):
273:             raise ValueError('Missing actual M21 service ledger; unknown cannot become zero')
274:         arrays=[ledger.get(key) for key in ('original_demand_kW','active_cut_kW','connected_unserved_kW','delivered_kW')]
275:         if any(not isinstance(arr,list) or len(arr)!=4 or any(len(row)!=3 for row in arr) for arr in arrays):
276:             raise ValueError('Invalid actual service ledger matrix')
277:         p_short=[]
278:         for ip,p in enumerate(PRIORITIES):
279:             material=False
280:             for ik,k in enumerate(CARRIERS):
281:                 request,cut,unserved,delivered=(float(arr[ip][ik]) for arr in arrays)
282:                 if not all(math.isfinite(v) and v>=-1e-8 for v in (request,cut,unserved,delivered)) or abs(request-cut-unserved-delivered)>1e-7:
283:                     raise ValueError('Inconsistent actual service ledger; request must equal delivered plus both shortage kinds')
284:                 short=cut+unserved; row=service[p][k]
285:                 for key,value in (('requested_kWh',request),('delivered_kWh',delivered),('ENS_kWh',short),
286:                                   ('disconnected_ENS_kWh',cut),('connected_unserved_ENS_kWh',unserved)):
287:                     row[key]+=value*dt
288:                 is_material=short>.001 and (short>=.01 or (request>.001 and short/request>=.001))
289:                 if is_material:intervals[p,k].append([a,b]);material=True
290:                 cuts=sorted({a,b,*[x for x in (incident,initial_end,recovery) if a<x<b]})
291:                 for x,y in zip(cuts,cuts[1:]):
292:                     name=('preparation' if x<incident else 'recovery' if x>=recovery
293:                           else 'initial_handoff' if x<initial_end else 'endurance')
294:                     phase[name][p][k]+=short*(y-x)/60.
295:             if material:unions[p].append([a,b])
296:             p_short.append(material)
297:         if any(p_short[:2]):critical.append([a,b])
298:         action=seg.get('action',{})
299:         for field in ('grid_import_kW','diesel_kW','absorption_cooling_kW','tower_rejection_kW','gt_fuel_MMBtu_h','boiler_steam_lbph'):
300:             if field in action:energy[field.replace('_kW','_kWh').replace('_MMBtu_h','_MMBtu').replace('_lbph','_lb')]+=float(action[field])*dt
301:         for field in ('gt_electric_kW','electric_chiller_cooling_kW'):
302:             if field in action:energy[field.replace('_kW','_kWh')]+=sum(map(float,action[field]))*dt
303:         for name,receipt in seg.get('m21_storage',{}).items():
~~~

## tools/sci_engineering/runner.py:329-340

~~~python
329:     event_counts=dict(Counter(event.get('kind','unknown') for event in payload.get('events',[])))
330:     attempts=[attempt for call in calls for layer in call.get('solver_backend_layers',[])
331:         for attempt in layer.get('attempts',[])]
332:     costs=[seg.get('cost_USD_step') for seg in segments]
333:     cost_sum=sum(map(float,costs)) if costs and all(isinstance(x,(int,float)) for x in costs) else None
334:     shaft=energy.get('DG_mechanical_work_kWh');electric=energy.get('DG_electrical_work_kWh')
335:     correction=.30*(shaft-electric) if shaft is not None and electric is not None else None
336:     economics=dict(accounting_cost_USD_sum_segments=cost_sum,DG_fee_correction_USD=correction,
337:         energy_cost_USD=None,initial_inventory_grid_input_kWh_conditional=None,
338:         initial_inventory_fee_USD_conditional=None,inventory_recharge_fee_added_twice=False,
339:         price_year=2001,price_currency='USD',actual_recharge_is_already_in_segment_energy_and_cost=True,
340:         DG_fee_semantics='0.30 USD per actual shaft-equivalent kWh; old electric fee replaced once',
~~~

## tools/model_closeout/readiness.py:17-32

~~~python
17: def critical_reserve_rule(critical_loads,*,remaining_kWh,ec_kWe_per_kWc,
18:                           shaft_loss_bound_kW,minimum_hours=1.):
19:     demand=np.asarray(critical_loads,float)
20:     values=[remaining_kWh,ec_kWe_per_kWc,shaft_loss_bound_kW,minimum_hours]
21:     if (demand.shape!=(2,3) or not np.isfinite(demand).all() or np.any(demand<0.)
22:         or any(isinstance(v,bool) or not math.isfinite(v) or v<0. for v in values)
23:         or minimum_hours<=0.):raise ValueError('Invalid observed critical reserve inputs')
24:     electric=float(demand[:,0].sum()+demand[:,2].sum()*ec_kWe_per_kWc)
25:     shaft=max(.001,electric+shaft_loss_bound_kW)
26:     horizon=max(minimum_hours,float(remaining_kWh)/shaft)
27:     return dict(reference_critical_electric_kW=electric,reference_shaft_kW=shaft,
28:         reserve_hours=horizon,remaining_shaft_work_kWh=float(remaining_kWh),
29:         minimum_hours=float(minimum_hours),actual_outage_endpoint_used=False,
30:         cold_reference='all existing electric-chiller conversion auxiliary; not extra demand',
31:         heat_reference='existing gas boiler has no separate electrical auxiliary in this model',
32:         bound_is_policy_preference=True,actual_power_or_energy=False)
~~~

## src/hospital_ies/state_transition.py:86-90

~~~python
86:     def delayed_at(self,t):
87:         s=self.env.scenario
88:         start,_=event_window(s,'fault' if s['stage'] in (1,3) else 'shock')
89:         delay=float(s.get('response_delay_minutes',float(s.get('response_delay_steps',0))*self.env.dt_minutes))
90:         return start<=t<start+delay,start+delay
~~~

## src/hospital_ies/state_transition.py:112-125

~~~python
112:     def observe(self):
113:         h,row,loads,f=self.data_at(self.time)
114:         return dict(step=self.env.steps_done,time_minute=self.time,
115:             grid_outage_elapsed_minutes=max(0.,self.time-self.grid_outage_start) if self.grid_outage_start is not None else 0.,
116:             hour_of_year=h,outdoor_temperature_C=float(row.outdoor_temperature_C),
117:             grid_import_limit_kW=float(self.env.scenario.get('grid_limit_kW',self.env.config['grid_import_limit_kW'])),
118:             loads_kW=loads.copy(),availability=f,gt_prev_kW=self.state.gt_prev.copy(),
119:             diesel_energy_kWh=float(self.state.diesel_energy_remaining_kWh),
120:             grid_import_prev_kW=float(self.state.last_grid_import_kW),secondary_faults=sorted(self.state.extra_faulted),
121:             diesel_status=self.state.diesel_status,topology_mode=self.state.topology_mode,
122:             arrived_grid_import_limit_kW=float(self.executed['grid_import_kW']),
123:             gt_status=['FAILED' if f[f'GT_{i+1}']<=0 else 'STARTING' if i in self.gt_start_pending else
124:                        'RUNNING' if self.state.gt_prev[i]>EPS else 'OFF' for i in range(3)],
125:             zone_thermal_delta_C=dict(self.state.thermal_delta),control_available=not self.delayed_at(self.time)[0])
~~~

## src/hospital_ies/state_transition.py:235-241

~~~python
235:     def boundary(self):
236:         _,_,loads,f=self.data_at(self.time)
237:         self._sync_connections(loads)
238:         self._update_support_states(f)
239:         if f['GRID']<1 and self.grid_outage_start is None:self.grid_outage_start=self.time
240:         if f['GRID']>=1:self.grid_outage_start=None
241:         if self.last_availability is not None and f!=self.last_availability:
~~~

## tools/operating_response/execution.py:280-302

~~~python
280:                     tracking_targets=cmd.get('request',{}).get('targets',{}),security_gt_penalty_USD_per_kWh=cmd.get('weight',0.))
281:             lost=sol['lost']+self.state.disconnected_loads
282:             # Commit only the final, event-split actual interval. Original
283:             # electrical service/cost definitions are retained separately.
284:             safe,rotor,local_receipt=self._limit_by_diesel(dt*3600.,sol['action']['diesel_kW'],
285:                 self.diesel_available(self.time,f))
286:             if safe<dt*3600.-1e-6:
287:                 raise RuntimeError('Uncommitted local dynamic interval after protection split')
288:             # Re-previewing exactly the valid prefix may no longer report the
289:             # attempted crossing. Preserve its cause only if the final physical
290:             # interval actually reaches it (a prior protection split does not).
291:             if local_domain_boundary is not None and abs(next_time-local_domain_boundary['minute'])<=1e-8:
292:                 local_receipt['domain_exceeded']|=local_domain_boundary['domain_exceeded']
293:                 if local_domain_boundary['fuel_budget_domain_exhausted']:
294:                     local_receipt['fuel_budget_domain_exhausted']=True
295:             self.diesel_rotor=rotor
296:             debit=float(local_receipt['mechanical_work_kWh'])
297:             self.state.diesel_energy_remaining_kWh=max(0.,self.state.diesel_energy_remaining_kWh-debit)
298:             local_receipt['fuel_budget_debit_kWh_equivalent']=debit
299:             local_receipt['arrived_ceiling_kW']=float(interval_action['diesel_kW'])
300:             local_receipt['actual_terminal_kW']=float(sol['action']['diesel_kW'])
301:             local_receipt['original_electric_fee_USD']=.30*float(local_receipt['electrical_work_kWh'])
302:             local_receipt['shaft_equivalent_fee_USD']=.30*debit
~~~

## tools/operating_response/execution.py:307-322

~~~python
307:             weight=self.active_command['weight'] if self.active_command else 0.
308:             seg=dict(start_minute=self.time,end_minute=next_time,step=e.steps_done,hour_of_year=h,
309:                 command_id=self.active_command['id'] if self.active_command else None,
310:                 execution_mode='finite_local_down' if np.any(source_start-source_end>EPS) else 'held_executed_setpoints',availability=f,pre_state=pre_state,
311:                 source_start_gt_kW=source_start,source_end_gt_kW=source_end,
312:                 arrived_source_setpoints=copy.deepcopy(self.executed),execution_contract=e.execution_contract['name'],
313:                 auxiliary_electric_kW=self.aux(sol['action']),thermal_attribution=self.thermal_attribution(self.time),
314:                 loads_kW=loads,connected_loads_kW=connected,disconnected_loads_kW=self.state.disconnected_loads.copy(),
315:                 unserved_kW=lost,action=sol['action'],cost_USD_step=sol['cost_USD_step'],
316:                 tracking_penalty_USD_step=sol['tracking_penalty_USD_step'],
317:                 security_objective_penalty_USD_step=weight*sum(sol['action']['gt_electric_kW'])*dt,
318:                 energy_balance_residuals=sol['energy_balance_residuals'],steam_balance_residual_lbph=sol['steam_balance_residual_lbph'],
319:                 protection_stress=stress,protection_debug=debug,post_state=clean(asdict(self.state)),
320:                 temperature=temp,violation=viol,classification=cls,optimizer_warning=sol.get('optimizer_warning',''))
321:             seg['diesel_local_response']=clean(local_receipt)
322:             self.segments.append(clean(seg))
~~~
