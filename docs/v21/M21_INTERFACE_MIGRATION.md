# M21 当前供给与服务依赖接口迁移

本文件记录M当前供给、共同优先级和DHW扩展约定。修正仍须统一源门槛与新实际pilot，不宣布主比较完成。历史日志不改名为新结果；即使model_id同为 `M21_sci_engineering`，也须同时绑定源码/输入/配置/情景SHA。

## M字段：目标态与可执行当前见证

旧 `sci_engineering.current_only_command.v21.v1` 调用静态LP，提前绕过strict readiness/min-up及当前执行副本。容量/普通爬坡内的OFF/STARTING GT目标与HRSG被计入规划“当前”供给；实际执行却仍要等到达、最小启停与启动。旧规划无缺口不证明能源已经送达，这一实现缺口不能作为M方法天然较差的结论。

| 字段 | 原M口径 | schema `.v21.v2`迁移口径 |
|---|---|---|
| `action` | 拟入队目标/源许可 | 仍为普通命令/许可，经队列/接点/启动，不是实供 |
| `planned_current_action` | conditional target平衡，可能含待启动源 | `arrived_current`见证，只计已运行机组在原普通调节域的阶段供给 |
| `lost` / `planned_unserved` / `served` | 请求与目标态亏缺 | 同一arrived见证；请求与等级不改 |
| `joint_diagnostic.planned_unserved_kW` | 目标态亏缺 | 当前阶段亏缺 |
| `conditional_target_unserved_kW` | 未独立命名 | 目标态亏缺另列，不与当前混用 |
| `current_service_phases` | 无完整集合 | arrived/regulated/held证据分列，各自语义保留 |
| `sci.current_auxiliary_served_kW` | 随主目标记录 | 同arrived见证；目标态辅机另列，避免混账 |
| `risk_scenarios` | 空 | M仍为空，不因阶段证据加入未来故障优化 |

arrived_current以普通命令到达为条件、包含原调节域假设，不保证瞬时实现或完整动态路径。健康OFF仍可收到合法START，原pending目标保留/取消沿共同接口，不能以当前交付必须为零为由禁掉所有启动命令。M保留现时保护与正常操作，不加未来事故、response deadline、备用持续时长或准备软目标。

外电硬件恢复而母线仍孤岛时可发普通条件GRID许可，只有共同回切gate成功后才有实际外电交付；保持DG许可也不等于DG实供。不能用控制可用、rearm或硬件availability恢复代替服务恢复或危险解除。

## 共同优先级修正与C19迁移

独立反例证实M/F/C/A旧compiler先锁conditional target P0/P1，再锁arrived当前服务，可能因新GT最小稳定目标挤掉本可送达的P0；即使当前P0只差少量，也不能让不可立即改善的target P0优先于当前P0/P1。因此不是只修M或保留旧C19错次序。

共同约定为可执行arrived P0及请求→arrived P1及请求先锁，再允许conditional target/endpoint与后续准备。M进一步先arrived P2→P3，再所有条件目标；C/F/A保留预登记备用/关键准备相对一般服务的独立插入次序，当前P2/P3也按等级排序。regulated_current保留原local-down endpoint，可含未就绪目标，不修改其原物理域，不假称瞬时实供，也不得跨过即时关键服务锁。

诊断新增 `current_priority_contract` 标记当前/目标层语义；历史 `current_Pg` 层名表示同一当前需求下的条件设备目标，不等于实际交付。C继续保留原强准备/桥接/备用目标及风险副本，但旧“逐字相同动作/错误目标顺序”迁移断言不再是正确验收；用原机制完整保留与即时关键服务反例验证替代，不放宽容量、阈值或容差。原源、旧断言失败和新红绿证据均保留。

## 实际执行与评价语义不变

reset/step、普通队列和实际分段职责不变：生成/校验/到达许可、实际GT/GRID/DG/锅炉/HRSG/ABS/EC、原请求/交付/未满足、接点、保护、有限资源账仍由共同执行层记录。R直接dispatch不再经完整上层优化；未到达/未许可上限不能扩大，仅换身份也不能改变同命令同状态的物理结果。

当前日志的 `decisions.requested_command` 是策略内部validate之后实际提交的请求，与 `command_generated.request` 对应；没有独立保存validate之前的原始请求，不能据此重建未记录的raw→validated差异。可追溯实际提交、生成的dispatch/permitted_command、普通到达后的requested_dispatch/executed_dispatch、分段arrived_source_permissions、投影effective_source_setpoints及实际 `segments.action`。投影值不是实供，`segments.action`是区间平均，不能称瞬时输出；无法关联或没有独立记录的阶段留未知。

正式ENS、持续时间、恢复、费用及条件CO2继续读取实际 `segments.unserved_kW`、`segments.action` 和逐段账，不以规划lost或许可替代。旧四类及正常M pilot数值/图仅保留缺陷诊断或绘图接口用途，raw/RESULT/源码/失败不删改。新真实运行后另生新绑定图。

## DHW上界与来源字段

共同helper只将P3热的风机门控量L改为L−d，`d=min(当前连接P3热, 原P3热, 原Q_h_DHW_water_use_kW)`；原请求/蒸汽平衡/热泵及其他等级不变，actual与所有planner副本同式。

实际源行的 `sci_heat_decomposition_required`、`sci_heat_decomposition_source` 绑定原需求CSV路径/SHA、hour_of_year、DHW列和原P3请求。缺分项/坏trace报错；旧数学fixture的零分项假设明确记录。

观测 `sci_auxiliary.fan_independent_heat` 与实际 `service_dependencies.fan_independent_heat` 含 `maximum_fan_independent_heat_kW`、源DHW/原P3识别量、trace/status，以及 **`represents_actual_DHW_delivery=false`**。这是最大无风机热上界，不是DHW meter、温度或真实P3子服务优先级。没有独立DHW接点，不能宣称实际DHW送达/合规。A ready仍按实测非DG电端+即时EC/锅炉/塔/aux代理解释，不能因新增trace就声称冷热已实测ready，详见 `ASSUMPTIONS_AND_LIMITATIONS.md`。

## 冻结与证据迁移

`raw_bytes`仍仅trajectory gzip；`raw_total_bytes`含trajectory与solver-row sidecar，资源统计优先后者并分列。两者SHA/字节/完整性计数分别核验；sidecar漂移即使数值存在也标evidence_drift/unknown，不填零、不删除或用旧成功掩盖新失败。

M修正记录在 `E:/hospital_ies_v21_20261023/reviews/M_current_fix01/`，共同优先级在 `reviews/current_priority_fix01/`，DHW在 `reviews/DHW_core_fix01/`。第一版M补丁的F/C/A不变性证明仅限当时补丁，不能替代共同DHW和优先级修正后的全开发重跑。dev01已自然收尾并封存旧源；新源统一门槛/新freeze/dev02全68×6、闭合F/A、正常与L1—L4新pilot及主面板依次留证。

新freeze不重置正式预算：5000 actual attempts/96 h从dev01 UTC `2026-10-23T06:06:02.282879+00:00`跨版本累计，预实验另列M21总ledger。确认/旧十二结果未用于修复调参。闭合选择、主确认、盲测、硬件和最终干净目录交付仍未完成，接口验收不能替代医院供能结果。
