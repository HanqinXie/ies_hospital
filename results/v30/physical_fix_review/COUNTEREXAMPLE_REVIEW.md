# GT 待启动时钟最小反例与修复原理只读复核

复核结论：原公式存在可由已保存实际状态直接复算的时钟一致性缺陷。将投影锚点从 `time` 改为 `max(time, started)` 符合原最小停机和实际供电启动规则；该两行修复本身不放宽启动、爬坡、服务或容量约束。真实重放是否恢复求解与完整执行，由独立运行证据判定，本文件不替代重放。

本次只读检查源码及已失败结果的少量字段，运行纯 Python 浮点算术断言（退出 0）。未构造环境、未仿真、未调用求解器、未修改 D 盘源码。

## 已保存实际反例

证据：`E:/hospital_ies_v30_20261027/runs/d01_b02_L1_heat_pump_disturbed_M/RESULT.json` 和对应 `trajectory.json.gz`。

- 原运行 producer commit：`7e1a5320d98027ec9c5897e778a8b63b01ed41bb`；source digest：`cdea107ef1dfd5a5cce1d6d1c0419f97af602c4224279871dc68a88ed6f06f39`。
- 原生轨迹 SHA256：`908ffa80d178ae696515a6904e9f733566a9058510bc40c3934935dc96a09cb4`。
- 状态 `failed_unknown`，实际已观察至 90 min；请求终点 180 min，未知尾段 90 min。错误类型 `SolverExecutionError`，摘要为 `M23 checked boiler command domains unavailable; no global physical infeasibility claim`。不得将其升级为已证明全局物理不可行，也不得将未知尾段填零。
- 90 min 的 GT_1 实际出力为 0；实际时钟 `last_off=75`、`down=30`，故最早有资格启动时刻为 `105` min。
- 待启动记录 `started=105`、`ready=100`、`requested=126.83648 kW`、`start_event_recorded=false`；实际启动进度字典为空，即已积累供电启动进度为 0 min。
- 原终态导出亦明确 `required_powered_minutes=10`、`remaining_powered_minutes=10`、`actually_completed=false`。最后实际分段依次为 [87,88]、[88,89]、[89,90]，没有 90 min 之后的实际完成证据。

原生反例保留了“ready 早于 started”的矛盾。`tools/operating_response/readiness.py` 的既有校验也拒绝 `ready < started` 的待启动记录。本复核证明该时钟缺陷；不据错误摘要单独推断整条求解失败的全部因果链。

## 可手算的最小算术

原规则未变：

```text
t = 90 min
last_actual_off = 75 min
minimum_down = 30 min
started = max(t, last_actual_off + minimum_down) = 105 min
requested = 126.83648 kW
ramp = 900 kW/h
startup_delay = 10 min
required = max(10, requested / 900 * 60)
         = max(10, 8.455765333333332) = 10 min
actual_powered_progress = 0 min
remaining = max(0, required - progress) = 10 min

old_ready = t + remaining = 100 min
new_ready = max(t, started) + remaining = 115 min
```

纯算术断言实际通过：`started==105`、`required==10`、`old_ready==100`、`new_ready==115`、`new_ready>=old_ready`、`new_ready>=started`；对 t=105/110/113 的检查均确认 t>=started 后新旧公式相同。900 kW/h 来源于原 `configs/resilience_assumptions.json`，未更改。

## 为什么约束没有放宽

令 `R=max(0, required-progress)`，则：

```text
new_ready - old_ready = max(t, started) - t = max(0, started-t) >= 0
```

因此修复只可能推迟错误的待启动就绪投影，不会提前它。在实际时刻已经达到 eligible start 后，两式完全相同。`started` 仍由原 `GTClockLedger.earliest_start` 依据实际 last_off 与 minimum_down 产生；最小停机时间、原 requested 功率、原启动延时、原爬坡率、最小开机时间、联锁和硬件可用率均未改变。

当前根任务的源码 diff 仅替换 `tools/model_closure_v22/environment.py::_clamp` 在 inherited clamp 前后的两处 ready 公式。检查时该文件 SHA256 为 `1c0bed37bda575f160a5c7c6a2334cde0d1014cdc1e8cbf8349d33cfcdab5ef2`。前后两次刷新分别覆盖既有 pending 以及 inherited clamp 新建/更新 pending，不能只修导出而保留执行内部不一致。

## 供电不足仍继续推迟，投影不是完成

`_after_actual_segment` 只将 `started <= segment.start_minute` 的 pending 视为实际活动启动；仅在分段 `gt_start_electric_kW >= 活动启动数 × 原 gt_starter_kW_per_unit - 1e-6` 时累加实际 minutes。没有供电时 progress 保持不变。`_next_boundary` 保留待启动时至多 1 min 的实际分段，以及 inherited eligible-start 边界。`_clamp` 仍在 `progress < required-EPS` 时将实际 GT 出力压为 0。

纯算术例：105–110 min 实际供电累积 5 min，则 t=110 时剩 5 min、投影 115。若 110–113 min 未供电，progress 仍为 5，t=113 的投影改为 118；没有因墙钟流逝而获得额外启动进度。若之后持续供电，只有实际累积达到 10 min 才满足该启动进度条件。该例仅说明既有更新规则，不是新仿真结果。

`ready` 依旧是“未来持续获得所需启动功率时”的有条件投影。可用性、命令、源侧供电和服务锁仍可阻止实际执行。真实完成须核对原实际正出力、实际时钟转换及 `gt_start_completed`/`gt_actual_started` 事件，不能直接把 ready 时间写成实际完成或服务恢复时间。

## 复核边界

本文件支持根任务采用两行锚点修复开展回归与重放，不作“整个物理核心已无缺陷”的结论。所需后续证据包括等待最小停机、启动供电中断、正常已开始 pending，以及已失败真实前缀的受影响重放；这些由根任务统一执行并绑定新源摘要。旧失败轨迹、旧费用与未知尾段应全部保留。
