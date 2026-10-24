# 在线计算记录的解释范围

本轮没有部署真实医院控制器。下述时间均是在实际仿真调用中用墙钟测得，不是医院现场通信、开关动作或设备响应时间。

- `resource_online_upper_seconds` / `resource_online_upper_max_seconds`：同一决策时刻累计的上层规划求解与已记录策略推理耗时；`resource_online_upper_period_exceedance_count` 将其与该情景登记的调度周期比较。
- `resource_online_full_step_seconds` / `resource_online_full_step_max_seconds`：`env.step` 的实际墙钟耗时，包含上层命令、共同本地执行、内部事件与分段模拟；不是仅算法推理耗时，也不是事件在医院里持续的物理时间。
- `resource_online_solver_seconds`：已记录求解调用的累计耗时，不能与上层时间及完整 step 时间相加，三者存在包含关系。
- `resource_solver_timeout_attempt_count` 与 `resource_solver_backend_rejected_attempt_count`：分别保留超时尝试和被验算拒绝的内部求解尝试。它们不自动等于一条科学轨迹失败；轨迹是否完整由其终态、完整窗口、实际服务账及原始证据决定。
- 策略回退、投影回退及投影修正从 RESULT 的实际计数分别报告。没有超时不能推断没有回退；发生投影修正也不能直接称为供能成功。

主实验按同一情景的登记周期（通常 15 min，即 900 s）比较上层计算时间。不同并行负载、事件分段与硬件条件影响墙钟时间，须连同实际工作进程数、内存和求解器单线程条件报告。批次总耗时不能替代单次决策最大耗时；即使所有受测决策小于周期，也只支持本机本批次的计算可行性，不构成硬实时保证或现场可用性认证。

实际数值取自对应分析目录的 `RESOURCE_MEASUREMENTS.csv` 与 `CASE_METRICS.csv`。两表均由同一冻结版本原生分析产生，并绑定 RESULT、轨迹及 solver sidecar；完整分布、未知值和异常计数应随主报告保留。
