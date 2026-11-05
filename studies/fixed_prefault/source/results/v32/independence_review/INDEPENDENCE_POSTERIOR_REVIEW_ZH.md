# V32 独立输入与执行路径后验核查

本次只读核查通过，未发现阻止既定 b01 参考试验的真实差异。结论限于共同初态、原始请求与静态执行路径，不是规划结果或实际保障结果。实际主命令 `ACTUAL02_COMMAND.json` 退出码为 0；证据为 `actual02/POSTERIOR_CONSISTENCY.json`。

## 共同初态

新 `initialization/normal/INITIALIZATION.json` 和 `initialization/grid/INITIALIZATION.json` 的 `initial_state`，分别与四条旧 b01 原生 gzip 的 `initial_state.state` 完全相等。两份新 `physical_initial_receipt.state/core` 与四条旧 `RESULT.v28_demand_boundary.full_initial_state_and_core.state/core` 也全部逐字段完全相等，共 8 组比较，未归一或删除任何状态、队列、时钟、命令、库存字段。

GT 初始功率为 `[131.180288, 0.0, 0.0] kW`；UPS 物理库存 40 kWh，冷储 0 kWh，DG 初始轴功等效预算 2400 kWh。初始 UPS 准备输入为 42.10526315789474 kWh_e，不能再加到物理库存。两条新分支 RNG 记录相同，但与旧记录不同，差异作为诊断保留，未伪造 RNG 一致性。

核查的是 **V31 P01 既有 864 个源文件仍全部字节匹配**，以及原 36 个输入全部字节匹配；不声称当前 V32 新分支总共只有 864 个文件。核查读取前后的全部绑定文件 SHA 保持一致。

## 12 个时段与四条旧 b01 的请求

两份纯 spec 均为 `1160 / H1 / 180 min`，12 个 15 min 时段，源小时依次为 1160、1161、1162，每小时四个时段。两份 spec 内嵌的初始化证书和单独保存的证书完全相等，内嵌与单独保存的需求台账也完全相等。normal/grid 的全部 P0–P3 电、热、冷原始请求与台账完全相同。

四条旧记录为 `n02/d02_b01_L1_grid_{normal/disturbed}_{C/M}`，共 811 个原生实际 segment。逐 segment 核查 `service_request_ledger.original_demand_kW` 与对应新 spec 时段的 P0–P3×e/h/c 请求，全部**精确相等**，最大差为 0。每时段观测覆盖恰为 15 min，原生源小时均正确；三个载能总量与各优先级之和完全相等，电的 direct + building auxiliary 与完整电请求完全相等。

分量台账另对 direct、building auxiliary、风机驱动、热泵驱动和普通 DHW 的原始字段核对。少量源参考数组受独立 CSV 浮点解析次序影响有标量末位差：最大绝对差 `3.410605131648481e-13 kW`；各字段最大 ULP 差为 12。全部原值和差值保留。64 ULP 仅用于这次后验显示差异的分类，不改变任何物理判据、规划容差或生产输入。

| 源小时 | building 电请求 kW_e | 其中 building auxiliary kW_e | 热请求 kW_h | 冷请求 kW_c | 普通 DHW 新目标 / 旧已交付代理 kW_h |
|---|---:|---:|---:|---:|---:|
| 1160 | 371.6807451711184 | 78.9793718624152 | 155.67480339166752 | 117.83611369198594 | 9.092755279619013 / 8.878304016241648 |
| 1161 | 487.40234727995073 | 82.87510790588779 | 138.66383355098674 | 133.9790803941043 | 12.999995649388085 / 12.683170017211658 |
| 1162 | 524.4228225729441 | 106.22877338324022 | 215.90835701316823 | 240.42847057551785 | 16.39083201689698 / 16.00543602250567 |

这些是原模型聚合服务边界，不是逐个临床设备的实测负荷。普通 DHW 的新增中心热请求只按既有规则影响对应驱动；不把全部风机随新增 DHW 一起放大。动态站用电另在实际 action 中核对，不作为 building auxiliary 或原始需求再加一次。四条旧记录实际站用电最大值依次为 normal C 0.5、normal M 0.5、disturbed C 0.7109847278508874、disturbed M 20.0 kW；该差异不是原始需求输入不一致。

本后验程序实际读取旧结果，只用于独立审计。它不被 builder、planner 或生产入口导入，输出角色明确禁止作为实例构建输入。输入生产者单独保存的隔离读取收据显示：禁止读取尝试为空、未读完成后的 operating results、builder 自身 planner/episode/C/M decision 调用均为 0。**数值一致本身不单独证明信息独立性**；隔离读取收据、源代码数据路径和后验一致性是不同证据。

## 静态执行路径

| 核查点 | 源位置与结论 |
|---|---|
| 共同初始化 | `snapshot.py:8,23–49` 取 b01 normal/disturbed 的 M **配置**，仅调用原共同构造/reset，不创建控制器、不调用 C/M.decide、不推进 episode。|
| 外部参考身份 | `reference.py:13–29,44–45` 强制 R32 身份并注入 `OfflinePlanPolicy`；缺计划、顺序错误、当前无请求均显式报错，无零计划或 C/M fallback。共同 `model_correctness_v23/validation.py:240–241` 实际使用注入的 factory。|
| 正确背景 | `reference.py:66` 显式约束 1160 / H1 / 180，不使用旧 R03/dev 的 211 小时背景。|
| 45 min 准备 | `actions.py:16–22` 只提前改变 GT/boiler 普通请求目标。登记为 `ceil_to15(15通信+10名义启动+15采样裕量)=45`，不是把实际通信改为 45 或 30 min，也不是启动成功保证。|
| 原队列与物理约束 | `sci_engineering/environment.py:317–328` 仍先执行原 M23 准备，再走同一 `delayed_at` 与 `pending_commands`；不改到达、ready、实际功率、最小开停机或 ramp。|
| storage/unmet | `actions.py:40–53` 主动切负荷矩阵为零；库存和 unmet 计划仅保存为 `unexecuted_*`。`full_continuous_plan_replayed / planned_storage_ports_executed / planned_unmet_directly_executed` 均明确为 false。|
| 共同 M23 扩许可 | `reference.py:75–77`、登记 `scope` 及原 `model_correctness_v23/environment.py:145–175` 明示：共同 M23 可在 frozen 请求之后按当前关键热/冷和热债扩锅炉/EC普通排队许可。诊断明确 `conditional_permission_is_actual_supply=False`，不是新控制器隐藏供给。|
| 已知未来权限 | normal/grid 各自知道登记的未来外生 GRID 窗口，因此允许 `[0,60)` 的准备动作不同；共同初态与原始请求仍一致。不作同信息策略排名或理论界。|

## 求解计数与范围

`initialization/INIT_SOLVER_SCOPE.json` 已明确：每条分支 reset 后 core 日志记录 0 次，但底层 NativeEnvironment reset 可能在 core 日志创建前求解初始正常平衡，故 **total_initialization_solver_calls=null**。空日志不证明底层总求解次数为零。

本后验程序只使用标准库读取 JSON/gzip/文件字节，没有导入项目模块；自身 solver 调用、模型 episode、测试、D 写入和旧证据写入均为 0。未重新运行初始化或输入构建，没有把旧轨迹数值回灌 builder。未求解真实参考 spec；未评价尚未发生的实际参考轨迹。

`actual01` 与原 R1 程序均保留。R2 仅把覆盖时长、源小时、内嵌证书、需求总量及隔离读取收据明确纳入本审计器的退出门槛；`actual02` 再次实际执行并退出 0，不涉及模型变化。
