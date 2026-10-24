from pathlib import Path
import csv,json,hashlib
O=Path(__file__).resolve().parent
R=Path(r'D:\hospital_ies_v21_20261023')
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def rows(p):
 with (O/p).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def need(v,m):
 if not v:raise ValueError(m)
def save(name,text):
 with (O/name).open('x',encoding='utf-8',newline='\n') as f:f.write(text)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
ag=read(O/'AGGREGATES.json');cmds=rows('COMMANDS_A_C.csv');pair=rows('PAIRED_NATIVE_INTERVALS.csv');native=rows('NATIVE_INTERVALS_A_C.csv');joins=rows('METRIC_JOIN.csv')
need(len(native)==202 and len(cmds)==96 and len(pair)==101 and len(joins)==362,'counts')
need(all(float(x['difference'])==0 for x in joins),'exact native metric joins')
changes=[x for x in pair if float(x['P3_e_ENS_reduction_C_minus_A_kWh'])!=0]
need([(float(x['start_minute']),float(x['end_minute'])) for x in changes]==[(360.,375.),(375.,390.),(390.,405.),(405.,420.)],'differing service intervals')
need(all(float(x['material_minutes_reduction_C_minus_A'])==15 for x in changes),'material deltas')
for t in (360,375,390,405):
 a=next(x for x in cmds if x['policy']=='A' and float(x['decision_time_min'])==t);c=next(x for x in cmds if x['policy']=='C' and float(x['decision_time_min'])==t)
 need(a['causal_release_active']=='True' and c['causal_release_active']=='False' and a['critical_non_diesel_ready']==c['critical_non_diesel_ready']=='True','observed branch')
 need(a['objective_order_json']==c['objective_order_json'],'same actual objective order')
 need(all(float(x[f])==t for x in (a,c) for f in ('generated_minute','arrival_minute','logged_arrival_minute','logged_execution_minute')),'command timing')
need(all(x['future_exogenous_input']=='False' and x['true_repair_or_outage_endpoint_used']=='False' and int(x['future_rollouts_online'])==0 for x in cmds),'recorded information boundary')
need(all(x['projection_changed']=='UNKNOWN' for x in cmds),'unrecorded projection status')
need(all(float(x[k])==0 for x in native for k in ('ups_initial_kWh','ups_final_kWh','cold_initial_kWh','cold_final_kWh')),'H0 zero storage')
scope=read(O/'SELECTION_AND_SCOPE.json')
for p,h in scope['source_code_sha256'].items():need(sha(p)==h,'source SHA')
report="""# 最终 12 背景中 A/C 一般电服务差异的事后追溯

本记录是在看到最终结果后，选择 m21_L1_55e24889a6e1b237d15a 进行的两条固定原生轨迹解释。它不是事先指定代表案例，不是新增独立样本，也不改变原八组预声明 raw 优先选择、主比较、评价阈值或策略。本分支不签科学阶段验收。

场景为 L1/Q1、背景 m21_final12_b046c742da517542，H0。观察窗 0–720 min，外电故障在 [120,480) min、availability=0。原 response_delay_minutes=15。下文时刻为原轨迹分钟，所有区间均为原生区间，不平滑或重采样。

## 可直接核对的工程结果

A 在 [360,420) min 比 C 多交付 P3 电约 4.063166109291046 kWh。原生区间积分给出 4.063166109291082 kWh，与 CASE 总量相减的差约 3.6e-14 kWh，来自加总次序；两条轨迹的服务、阶段、能量与费用重算共 362 项与原表精确相等，METRIC_JOIN.csv 最大绝对差 0。

| 指标 | C | A | A-C |
|---|---:|---:|---:|
| P3 电 ENS / kWh | 75.59000202874498 | 71.52683591945393 | -4.063166109291046 |
| P3 电 material 分钟 | 135.25 | 75.25 | -60 |
| P3 电最长 material 分钟 | 120 | 60 | -60 |
| P3 电中断次数 / 重复次数 | 3 / 2 | 3 / 2 | 0 / 0 |
| 费用 / 2001 USD | 505.5349974791259 | 506.7539383203635 | +1.218940841237611 |
| 条件 CO2 / kg | 3405.9256081041112 | 3408.7604574522893 | +2.834849348178068 |

P3 热、冷的 ENS 与 material 指标相同；P3 union 的 60 min 差异由电服务贡献。P0、P1、P2 的电 ENS 各增加 **4.063206741358955e-7 kWh**，未置零；P0/P1 各载能、union 的 material 和三阶段 gap 保持相同。因此不能称严格无关键代价或 Pareto 改善。

## 命令、到达、实际执行的可核链条

1. t=345 时两者记录相同：已观测停电 225 min，非柴油支撑就绪，备用参考 17.276289493990223 h；柴油命令上限 95.54593050214072 kW，P3 主动切除 5.2332730917941355 kW。
2. t=360，A 的原 model_closeout.critical_reserve_rule 直接记录 observed_outage_minutes=240、critical_non_diesel_ready=true、causal_release_active=true，备用参考转为 0.5 h。C 同刻 active=false，备用参考仍为 16.9869338225568 h。两者此刻剩余轴功等价预算均为 1998.6195185719864 kWh。
3. A 的 reserve support electric ceiling 和本次发出柴油 ceiling 均为 900 kW，P3 主动切除为 0；C ceiling 为 95.8734415222482 kW，仍主动切除 4.063143904745673 kW。900 是许可上限，不是实际发电或新增容量。
4. 命令 #25 均在 t=360 生成、到达并记录执行。原 command_arrived、command_executed、load_connection_restored 事件与首个执行段 [360,375) 对应。A 当次接回 P3 电 5.238161885700836 kW；C 部分接回 1.1750179809551629 kW，留下上述差额。t=375、390、405 的后续命令同样生成/到达/执行同刻，持续该差异。
5. 原 native action 在这四段记录 A 柴油实际分配 99.9326590736938 kW；C 从 95.86955701933026 变化到 95.86943011600071 kW。两者 GT 均为 [0,0,307.3952] kW，外电实际进口均为 0。A/C 实际目标顺序在这四次决策中完全相同；差异对应备用参考参数及其软约束作用，不是给 A 更高的 P0–P3 权限。
6. t=420，C 也将 P3 主动切除归零。该时刻 P3 电请求由 286.71666630317475 变为 286.8515132268034 kW，GT3 由 307.3952 变为 309.72223999999994 kW；C 的柴油命令 95.35671603975953 kW 已处于其 reserve support ceiling 96.19455717885575 kW 内。之后两者实际电服务相同。A 的 release 记录在 360–480 的九次决策为 true，495 起为 false；不能据此推定每次 release 都产生服务收益。

源代码支持这一解释：policy.py 89–109 的 reserve_reference 在当前就绪且观测年龄达到冻结 240 min 后将 reserve_hours 压到 0.5；planner.py 383–390、695–699 将该参考用于柴油 ceiling 的软备用约束，原物理容量和保护不变。原诊断中 reserve excess 均为 0。reserve 目标位于一般服务目标之前。这里没有重算 LP、求对偶或做分支消融，故“该规则是唯一因果贡献”的反事实量仍未验证；上面的参数触发和实际时序属于直接记录事实。

critical_non_diesel_ready 是原策略的当前就绪代理：实测前一步 GT/外电出力与当前关键电需求和制冷转换用电比较，并检查当时冷却塔、电冷机、锅炉及必要辅机可用性。它不是 OEM 保证，也不自动证明临床环境服务合规。

## 15 min、观测年龄与未来信息边界

原 state_transition.py 86–90 的 delayed_at 返回“事件开始至开始+delay”的响应不可用窗口，而非给以后每条命令恒加 15 min。该案例 t=120 的命令均到 t=135 才到达/执行；t=135 以后重新产生的命令可当刻到达。故不能把 t=360 接回解释成绕过 15 min 的新快通道。

原当前时钟在观察到外电不可用时登记 grid_outage_start，observe 输出当前时刻减此已观测起点。t=360 的年龄字段 available_time=valid_time=360，值 240；A 的冻结 release_after_minutes=240 直接使用此观测。两条原命令诊断均记录 future_rollouts_online=0、future_exogenous_input=false、true_repair_or_outage_endpoint_used=false。策略源码未在该分支读取真实故障终点；实际修复 t=480 只用于本报告的事后阶段划分。此项是原记录与源代码追溯，不是新增信息边界反事实仿真。

## 分阶段、库存与服务判据

| P3 电 ENS / kWh | C | A |
|---|---:|---:|
| 准备 [0,120) | 0.006730498539694639 | 0.006730498539694639 |
| 初始接续 [120,135) | 65.87341022283461 | 65.87341022283461 |
| 持续保障 [135,480) | 9.318664963559826 | 5.255498854268746 |
| 恢复 [480,720) | 0.3911963438108626 | 0.3911963438108626 |

收益全部落在持续保障区间。初始 15 min 和恢复切换不足不变，不能称恢复收益。两者恢复时都在 [480.25,480.5) 处于 TRANSFER_OPEN、柴油已断开且外电尚未接回；该共用 0.25 min 的 P3 material 中断保留。

C 的 P3 电 material 区间为 [120,135)、[300,420)、[480.25,480.5)；A 为 [120,135)、[300,360)、[480.25,480.5)。原判据为 short>0.001 kW 且（short>=0.01 kW 或 short/request>=0.001）。360–420 的原请求为 286.71666630317475 kW；A 不足仍为 0.00388747851494219 kW，C 为约 4.067 kW。A 达不到 material 判据，但并非严格零不足。60 min 只能解释为约 4.063 kW 的边际电服务恢复使该等级 material 时间减少，不能写作全一般需求额外获得 60 min 完整供能。

P3 主动切除 ENS 减少 4.063206741358496 kWh，已连接但不足 ENS 反而增加 4.0632067438650665e-5 kWh，合计为上述净减少。请求分母未改。

H0 的 UPS/蓄冷库存与端口均为零。A/C 全窗 DG 轴功等价扣账分别 633.195222663344 / 629.1320577730149 kWh，电功分别 527.0648100412338 / 523.0016451509048 kWh；增量均为 4.0631648903290625 kWh，损失功之差仅浮点末位。最终剩余预算 A=1766.8047773366554、C=1770.8679422269847 kWh；t=420 已分别为 1880.6869929185932 / 1884.7500343366119，均未耗尽。没有免费回充、自动满额恢复或修改库存。轴功等价预算不可称作柴油体积、质量或化学能。GT 电能和外电进口的全窗差均为 0。

费用由原区间 cost 与原轴功费用修正重新积分，与原 CASE 精确匹配；CO2 保留 CASE 的 conditional_estimate，不新增排放因子或声称实现在线碳优化。

## 留痕、未知与适用范围

- NATIVE_INTERVALS_A_C.csv：两条完整 101 段，原端点、请求/主动切除/已连接不足/交付、各载能判据、实际发电、许可与库存。
- PAIRED_NATIVE_INTERVALS.csv：两原生网格完全相同，逐段配对；没有插值、平滑或额外时间网格。
- COMMANDS_A_C.csv / COMMAND_TRACE_SELECTED_NATIVE_FIELDS.json：各 48 条原命令及观测、reserve 分支、到达/执行、readiness/actual_clock。
- EVENTS_SELECTED_NATIVE.jsonl：原接点、命令、柴油/外电状态事件。
- METRIC_JOIN.csv / MATERIAL_INTERRUPTION_INTERVALS.csv：精确指标对应与原判据区间。
- SOURCES_BEFORE_AFTER.json：从固定 CASE 行和 RESULT 追溯 raw/sidecar，压缩字节前后 SHA 完全相同。sidecar 仅做压缩文件哈希，未重读其 body。
- projection_changed 和 projection_change_kW 在原诊断中为 null；明确保留 UNKNOWN。requested_command 记录的是合法上层配置请求，不能冒充未记录的完整 preprojection 数值向量。不能据 null 断言“没有投影”。
- 本次没有新轨迹、优化、参数搜索、阈值改动或新独立 n。它解释这个事后选中案例，不能当作 A 对所有一般服务或最终 12 背景普遍优势的证据。
- 三次探索性 rg 路径探测出现不存在路径/Windows glob 错误，已改用正确源路径读取；一次报告脚本传输在工具 JavaScript 解析时因引用符报 SyntaxError、未执行或写文件。保留这些已知失败说明；实际提取和修正后的报告核验命令另有完整 exit/log。
"""
save('POSTHOC_ENGINEERING_EXPLANATION.md',report)
definitions="""# 导出字段与数据语义

全部字段来自两条固定 RESULT 声明的 native JSON，原文件只读。policy=A/C 仅为展示身份；时间单位 min，区间采用 [start,end)，没有重采样。

| 输出字段/模式 | 原始路径或定义 |
|---|---|
| segment_index | payload.segments 的零起始位置 |
| start/end_minute | segments[i].start/end_minute，原生边界 |
| Pn_k_requested_kW | service_request_ledger.original_demand_kW[n][carrier] |
| Pn_k_cut_kW | service_request_ledger.active_cut_kW |
| Pn_k_connected_unserved_kW | service_request_ledger.connected_unserved_kW |
| Pn_k_delivered_kW | service_request_ledger.delivered_kW |
| Pn_k_short_kW | cut+connected_unserved，不改变原始请求分母 |
| Pn_k_material | 原 runner.compact_metrics: short>.001 and (short>=.01 or request>.001 and short/request>=.001) |
| actual_diesel/GT/grid_kW | segment.action 的实际执行层分配功率；不等于上层许可，不替代 rotor/shaft 积分 receipt |
| command_diesel_ceiling_kW | 与 segment.command_id 对应 decision.command_generated.dispatch.diesel_kW |
| arrived_diesel_permission_kW | segment.arrived_source_permissions.diesel_kW，持久许可 |
| effective_diesel_limit_kW | segment.execution_limits.effective_diesel_limit_kW，当前接点/设备约束后的限制 |
| grid_bus_state | execution_limits.grid_bus_state |
| diesel_budget_pre/post | segment.pre_state/post_state.diesel_energy_remaining_kWh；轴功等价预算 |
| DG_*work* | segment.diesel_local_response receipt 的实际积分，不由 nominal power 臆造 |
| generated/arrival_minute | decision.command_generated；logged_* 另由 events.kind=command_arrived/executed 对应 command_id |
| observed_outage_minutes | policy_diagnostic.coordination 的原当前观测输入 |
| causal_release_active/reserve_hours | command_generated.joint_dispatch.diagnostic.model_closeout.critical_reserve_rule |
| support_electric_ceiling_kW | 同一 model_closeout.critical_reserve_support 的策略预算参考，不是实际输出 |
| projection_changed=UNKNOWN | 原 policy_diagnostic.projection_changed=null；projection_change_kW 亦为 null |
| PAIRED A_minus_C | 原生区间同起止端点后相减，未插值 |
| phase_ENS | 原 scene 的 incident/initial_handoff/recovery 边界与原段精确相交积分；事件终点只供事后评价 |
| METRIC_JOIN difference | 独立 stdlib 重复原算术与固定 CASE 字段的差。1e-9 只为对应检查，实际全部差=0，不是放宽物理阈值 |

P0–P3 为原模型服务优先级，carrier e/h/c 为电/热/冷，不能跨载能抵消。NATIVE_INTERVALS 同时保留实际 action 和 DG receipt；原保护、接点、执行约束资格来自已完成全240物理审计，本事后导出不另颁 gate。
"""
save('FIELD_DEFINITIONS.md',definitions)
specs={'tools/sci_engineering/policy.py':[(89,109),(176,201),(298,307)],
 'tools/sci_engineering/planner.py':[(383,390),(695,699),(1225,1259),(1633,1639)],
 'tools/sci_engineering/environment.py':[(287,297)],'tools/sci_engineering/runner.py':[(255,303),(329,340)],
 'tools/model_closeout/readiness.py':[(17,32)],'src/hospital_ies/state_transition.py':[(86,90),(112,125),(235,241)],
 'tools/operating_response/execution.py':[(280,302),(307,322)]}
parts=['# 原冻源码片段，只读引用\n\n不执行或导入物理模块。完整文件 SHA 位于 SELECTION_AND_SCOPE.json。\n']
for p,ranges in specs.items():
 lines=(R/p).read_text(encoding='utf-8-sig').splitlines()
 for a,b in ranges:parts.append('\n## '+p+':'+str(a)+'-'+str(b)+'\n\n~~~python\n'+'\n'.join(str(i)+': '+lines[i-1] for i in range(a,min(b,len(lines))+1))+'\n~~~\n')
save('FROZEN_SOURCE_EXCERPTS.md',''.join(parts))
verification=dict(status='posthoc_explanation_verified',new_independent_n=0,scientific_acceptance_issued=False,
 native_interval_rows=len(native),native_commands=len(cmds),metric_joins=len(joins),metric_join_max_abs_error=0.,
 differing_P3_e_native_intervals=[dict(start_minute=float(x['start_minute']),end_minute=float(x['end_minute']),ENS_reduction_kWh=float(x['P3_e_ENS_reduction_C_minus_A_kWh'])) for x in changes],
 all_four_differing_commands_generation_arrival_execution_joined=True,current_recorded_information_boundary_flags_verified=True,unrecorded_projection_preserved_unknown=True,H0_zero_stock_verified=True,
 no_physics_or_optimizer=True,preregistered_selection_modified=False)
save('EXPLANATION_VERIFICATION.json',json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(verification,ensure_ascii=False))
