# M19 定点兼容比较定义

本文件和 `scripts/summarize_compatibility.py` 是冻结生产源码之外的事后审查工具。
不改 `tools/model_closeout/analysis.py`、策略、物理状态转移、服务判据或主汇总分母。
当前仅完成合成小 fixture 验证；本文件不报告真实 V19 结果。

## 比较对象与归因

E、K1、C19 与 C18_M19 均在同一 M19 模型上运行。C18_M19 是在 M19 上禁用
`critical_reserve` 的注册策略，不是封存的 M18/C18 轨迹。GT floor、DG 能量核算、
并网恢复等 M19 共用修复影响所有这些策略，不能把其效果全部归因于 C19。
仅在冻结策略配置与实际证据都匹配时，C19–C18_M19 才能解释注册的预留开关差异；
C19–E/K1 则还包含策略结构差异，不是单开关消融。

原 80 个背景已见。key001/003/007/043/067 与 long000/005 是事先指定的机制解释点，
不构成新的独立总体或总体算法优劣分母。不按收益选择场景、重试或窗口。
同 panel 的开发配对保留 `same_panel`；跨 panel 比较标为 `cross_panel`，保存双方
`policy_panel_sha256`，不回写主三策略 `PAIRED_WINDOWS/AGGREGATES`。

## 成对比较门槛

每个注册 outcome 都保留，包括失败、缺文件、缺 terminal、partial 和审计未知。
以下全部满足才产生指标差值；任何一项缺失或不符均为 `unknown`，不填零：

1. 显式给定、签名可重算的 `v19_final_shared_model_freeze`，且为 M19；双方
   plan/manifest 的 `source_sha256` 和已解析 `model_config` 与该 freeze 完全相等。
   双方策略 spec 必须与 freeze 的注册 spec 完全相等。
2. plan/manifest 文件 SHA、manifest 自签名、配置签名、策略 panel 签名、场景顺序
   和注册 pair 数一致；terminal 的 `source_unchanged=true`，plan/manifest SHA 一致。
   compact RESULT 的身份/签名与 terminal `pair_index` 的完整非 analysis 记录一致。
3. manifest `files` 的每个路径和 SHA 有效且不重复；双方完整 inventory 的路径–SHA
   集合相等。这里只核保存的输入清单，不打开年度负荷、原始轨迹或重新运行环境。
4. 从 manifest scene 重算原 `tools.operating_response.runner.matching_evidence` 的
   applied stream：`start_hour,duration_steps,step_minutes,faults,shock,shock_by_priority,`
   `exogenous_events,enable_propagation,propagation_enabled,grid_limit_kW,`
   `pre_event_grid_limit_kW,pre_event_warmup_steps,response_delay_minutes,`
   `automatic_fast_shedding,initialization_seed`，另含全部 `fault_*/shock_*` 字段。
   重算 SHA 必须与 terminal 的全部实际
   `applied_exogenous_definition_sha256` 相等；列表长度须等于该 batch 注册策略数，
   terminal 的 `same_exogenous_definition` 必须为真。
5. terminal `initial_state_sha256` 非空、每项为有效 SHA、长度等于注册策略数，且
   `same_initial_state=true`。双方真实 reset SHA 相等。单策略 panel 也必须有真实
   SHA，不能以其“自身相等”代替初态证据。干预时刻 injection state 可受真实前缀
   策略影响，不要求它相等，也不宣称独立同分布。
6. 原注册物理字段相等：上述 stream 加 root/base state、stage/event class、
   实际 P0 multiplier、完整源支持、fault/shock 时钟、first event、热扰动、
   DG 并网权限和 precontrol shedding 等。脚本采用有限的已审查 scene 字段集合；
   新出现且未审查的字段使其 `unknown`。完整 scene SHA 与全部 metadata diff 仍
   留在证明文件；schema/来源/批次元数据不冒充实际外生差异。双方 evaluation role
   必须相等，不拼接开发与最终样本以构造总体分母。
7. RESULT `status=completed`，无保存的 error/analysis_error；整个注册 episode 完整，
   保存的 requested end 和 `registered_full` 必须等于注册 duration×step，实际 observed
   end 覆盖末端；episode audit `status=passed,issues=[],complete_registered_interval=true`。
   当前窗口也必须完整、有保存的 all_registered 指标并被实际观测覆盖。
8. 每个 case/window/policy 在显式输入 batch 中恰有一个 eligible outcome。若多个
   合格 attempt，则记录 `ambiguous_eligible_attempts`，不选择较优、较晚或任意一条。
   可通过显式 batch 列表分别生成开发表和最终表，不混入其它重复运行。

## 输出与指标语义

新目录 `review/compatibility_<label>/` 中：

- `CASE_STATUS.csv`：每个候选 outcome/window 的状态、SHA、完整性和拒绝原因。
- `COMPATIBILITY_CASES.csv`：逐 case/window/策略对的 matched/unknown 与 panel 身份。
- `CARRIER_SERVICE.csv`：P0–P3 × e/h/c 的原保存 ENS、disconnected/connected-unserved、
  material/longest、恢复/复发、censoring 等字段。未保存 served/demand 总量时不推造。
- `TEMPERATURE.csv`：保存的温度覆盖、违例与恢复字段。未知温度不填零。
- `ECONOMICS_ENERGY.csv`：原费用、购电、天然气、DG 电/轴功/损耗/转动能/预算 debit、
  电源发电量和 GT 在线/起停等可用字段。DG budget 同时给显式 shaft-equivalent 别名；
  该别名不等于化学燃料，也不新增 CO2 调度目标。
- `MATCH_PROOFS.json/CSV`：完整输入文件 SHA、实际 terminal 证据、逐字段 matching 值、
  full scene metadata diff、候选保存指标；JSON 包含完整拒绝证据。
- `SUMMARY.json`：外置脚本/最终 freeze/输入/输出 SHA、限定解释范围、matched/unknown
  行数。多个窗口和多个策略对不是独立样本数，也不计算新的总体胜率。

数值差统一为 `right - left`。非数值状态、列表和布尔分类原样保留，差值为空；
任一分量缺失时该分量及差值仍为 unknown。单位随原字段名保留（kWh、MMBtu、USD、minute）。
完整注册窗口与 common600 分列，即使数值区间恰好相同也不合并或重复作总体统计。

## 调用

先运行外置小自测（新 label，不读真实 RESULT）：

```powershell
python tools/p0_audit/run_logged.py --run-dir outputs/v19_model_closeout_20261018_run01 --name B19_compatibility_SELFTEST_NAME -- python outputs/v19_model_closeout_20261018_run01/scripts/summarize_compatibility.py self-test --label FRESH_LABEL
```

真实汇总须在最终 freeze 与 terminal 都到齐后，由 root 显式给定 batch 和 case ID。
下列是参数格式，不能把占位符当实际已完成实验：

```powershell
python outputs/v19_model_closeout_20261018_run01/scripts/summarize_compatibility.py compare --label final_key --freeze outputs/v19_model_closeout_20261018_run01/FINAL_FREEZE.json --batch outputs/v19_model_closeout_20261018_run01/experiments/MAIN_BATCH --batch outputs/v19_model_closeout_20261018_run01/experiments/COMPAT_BATCH --case key001=REGISTERED_SCENE_ID --pair C19:C18_M19 --pair C19:E --pair C19:K1
```

可重复 `--case` 与 `--batch`。默认策略对为 C19:C18_M19、C19:E、C19:K1、C18_M19:E、
C18_M19:K1；也可显式缩小。目录必须是本轮 canonical experiments 子目录；输出 label
必须新建，已有输出不覆盖。不得把不同 final source/config 或 contact/propagation
敏感性配置塞入同一主配置比较；其不匹配会保留为 unknown。

## 已执行验证

`B19_compatibility_red01` exit 1：核心门槛尚未实现的 12 项 fixture 实际失败。
`B19_compatibility_green01` exit 1：费用断言误取排序第一项（DG debit）；已改为按明确
`economics.actual_energy_fee_USD` 指标查找，保留 -2 USD 的原断言，不降低容差。
`B19_compatibility_green02` exit 0：12 项通过。
`B19_compatibility_red02` exit 1：新增注册末端不一致反例确实失败。
`B19_compatibility_green03` exit 0：16 项通过。
`B19_compatibility_green04` exit 0：17 项通过，含最终输出往返和禁止打开 raw 的 guard。
`B19_compatibility_green05` exit 0：17 项通过，确认 synthetic 目录标记迁移后行为不变。
所有小 fixture 位于 `review/test_tmp/compatibility_selftest_<label>`；不是真实 V19 实验。
早期自测目录已整体移至该 synthetic 标记目录，文件内容及失败日志保留。
