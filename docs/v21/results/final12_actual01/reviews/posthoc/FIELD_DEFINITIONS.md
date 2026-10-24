# 导出字段与数据语义

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
