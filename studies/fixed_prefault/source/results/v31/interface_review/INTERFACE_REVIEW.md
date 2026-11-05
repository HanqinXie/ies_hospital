# V31 权限薄适配：只读接口复核

本复核仅阅读现有源码、已保存的 V30 N02 结果，并执行 AST 切片和明确标为 synthetic 的接口探针。没有创建环境、推进时钟、调用规划器、执行物理仿真或求解器；没有修改 D 盘源码、输入或旧证据。V31 正式实现与模型验证由 root 单独完成。

## 建议采用的最小接入

沿用 common.worker 默认的 `tools.model_correctness_v23.policy.SciPolicy`，在它的 `super().decide(records)` **成功完成后**提取最后一条合法记录。不能改为直接继承裸 `tools.sci_engineering.policy.SciPolicy`：M23 的父类 M22 会检查当前物理见证，覆盖请求里的 `critical_non_diesel_ready`，并保留 `release_witness`。M23 又添加自己的 planning interface。对应 `tools/model_correctness_v23/policy.py:6`、`tools/model_closure_v22/policy.py:21`，默认工厂在 `tools/model_correctness_v23/validation.py:240`。

工厂和环境创建函数共享一个 mailbox；mailbox 存在 env 属性或封装对象，不加到 core 的实例变量。controller 每次成功决策后发布独立复制的只读 receipt。环境创建时，在第一次 reset 前，把原 `engineering_core_type` 包成 `type('V31Core', (V31SteamMixin, existing_core), {})` 的等价多继承类。M23 reset 在 `tools/model_correctness_v23/environment.py:311` 显式读取这个类型。需保留 existing_core 的完整 MRO，尤其 V25 的 SustainedReserveMixin 和原 M23 hook。

V31 hook 的顺序应是：

1. `command, diagnostic = super()._prepare_command_dispatch(...)`；原 common hook 完成后才开始 V31 的规则。
2. 验证当前 mailbox variant、决策时间和 core 的生成时间完全一致；空 mailbox、旧时间、未来时间、错误 variant 均明确失败。不同 episode reset 应清空 receipt，避免同一时间值复用旧 episode 信息。
3. 以 post-common 的锅炉值为 old；只修改 `boiler_steam_lbph`，其余 dispatch 字段（含 common hook 已生成的 EC 准备权限）逐字段完全保留。
4. 附加独立的 `v31_steam_permission` 诊断 receipt，不覆盖原 `m23_preparation`。原诊断应保留“common hook 后的值”，新 receipt 记录最终值，两者的阶段含义须明确。

原 M23 common hook 在 `tools/model_correctness_v23/environment.py:145` 增加当前 P0/P1 热权限和冷机权限；`critical_preparation_first=false` 并不关闭这层准备。V25 的 `tools/model_correction_v25/reserve.py::_prepare_command_dispatch` 先调用 super 再整理说明，薄适配应继续经过它。

## b07 当前信息、容量与算术边界

合法输入仅需：`records[-1].decision_time_min`，三个 `features.availability_BOILER_1/2/3`，各自 `feature_metadata` 的 `available_time_min`、`valid_time_min`，以及构造 controller 时传入并复制的 immutable Plant `boiler_steam_lbph`。不用读取 `env.scenario`、`env.state`、故障脚本、未来修复或旧 C 的权限。

原 snapshot 在 `src/hospital_ies/data_contract.py:153` 为每个 feature 设置可用/有效时刻；`:167` 从当前 graph 节点导出 availability。`tools/integrated_learning/policies.py:23` 会拒绝未来或非有限时刻和缺失 provenance，但**不会验证 availability 数值范围**。V31 提取时还需显式拒绝 bool、非数值、NaN/Inf、超出 [0,1]、缺字段。rating 也须有限且为正。建议同时记录 observation_time，验证它不晚于 decision；当前字段的 provenance 应来自本条 snapshot，不能从旧 receipt 补值。

原生模型的三个锅炉按总容量等分。证据同时见 `src/hospital_ies/data_contract.py:118`、`tools/sci_engineering/policy.py:192`、`:247` 与 `tools/sci_engineering/planner.py:639`、`:801`。可用率允许原模型支持的部分降额，不宜把 0.5 转成 bool。规则为：

`healthy_cap = total_rated_steam_lbph / 3 * sum(current_availability)`

`new = max(post_common_old, min(total_rated_steam_lbph, healthy_cap))`

这是“当前健康容量所对应的普通排队权限”，不是直接赋予实际出汽、已完成重启或已供电。若 post-common old 已大于当前 healthy_cap，按任务要求保留 old；当前硬件降额仍由原执行层裁定。若 old 本身非有限、负数或大于总额定容量，则无法同时满足“不降低”与“不越总容量”，应显式失败，不能静默截断。

availability 只表示当前可观察设备可用率，不代表 boiler station 已完成供电启动/重启。V31 不读取隐藏 readiness 来授权实际执行；原执行的最小停机、供电进度、当前辅助电、库存、供回转移及实际约束继续有效。不得在 hook 中调用 `observe()`、`decision_snapshot()`、`non_diesel_current_witness` 或新的求解以重建这些状态。

## 生成、排队、到达与实际执行的证据链

`tools/sci_engineering/environment.py:317` 在原 planner 返回之后、创建 command 之前调用准备 hook；`:322` 才计算原排队延迟，`:323` 创建 id/generated_minute/arrival_minute，`:328` append pending_commands 并记录原 `command_generated`。因此 hook 只需写 receipt 的 decision/observation 时间，不应自己调用 delayed_at 或预知未来实际 arrival。

保留原生成事件的 command id、generated_minute、计划 arrival_minute 和原 request。以 id 关联后续原 `command_arrived` 的实际时刻，再关联实际 segments 的 action、arrived permission、boiler restart/readiness/status。生成许可、许可到达、实际出汽必须分列。修改后的 dispatch 会自然进入原 command_generated 日志；无需改 generate_command、延迟函数、执行核或重放到达规则。

诊断至少记录：variant/rule id、receipt decision_time、三锅炉可用率及其 provenance clocks、immutable 总额定容量、每台等分规则、common old、healthy_cap、new、`permission_is_actual_supply=false`。完整 dispatch 的“除 boiler 外相同”比较应以 **common hook 后**为基准。

## b05 cooling_preparation 的实际生效点

只复制完整 adopted M config 并把 cooling_preparation 设为 true。原 `_solve_joint_candidate` 在 current 模式仍采用当前工况及已到达工况；current_only 分支的服务目标排列在 `tools/sci_engineering/planner.py:1410`。`healthy_converter_permission` 的 `run_layer` 位于 `:1471`，在服务和原 cost 层之后；这个 if 只判断 cooling_preparation 和 soft_optimization_stopped，**不在排除 current_only 的分支内**。其目标为最小化非负的 unused healthy converter permission gap，锁住权限偏好后再做后续 GRID 择优。它不提高设备上限，也不等同实际冷量。

最终 current-only 返回在 `:1954`，晚于该权限目标。需要警惕 `:2025` 的 `_finish_current_plan` 会把 diagnostic.coordination 重建为 `cooling_constraints_active=false`，且不保留前面更细的 preference_active 摘要。这个字段不能证明权限目标未运行；它反映 current-only 没有未来冷量准备工况。应核对真实 `diagnostic.layers`/`solver_backend_layers` 中 healthy_converter_permission 的求解与失败/回退状态，并同时输出生成权限、到达权限和实际冷量。允许的 late preference 失败会保留已有有效命令，不能把“请求了该目标”写成“目标成功优化”。

V31 不必为此更改 frozen planner；可以在新策略/审计 receipt 中准确说明请求与实际层状态。即使权限目标实际运行，也不保证一定改变 dispatch 或降低 ENS，common M23 冷准备可能已经覆盖部分权限。

## 工厂 alias 与最小 V31 入口范围

`tools/sci_engineering/policy.py:67` 只接受 R/M/F/C/A。原 M bare defaults 的 online_GT_count_order 是 legacy_before_cost；V30 adopted M 是 after_cost。`tools/model_closure_v22/validation.py:107` 在 alias 与原 case 相同时保留原完整 config，但 alias 不同时会从 policy_specs 重新选默认配置。`tools/model_review_v27/validation.py` 又做配置扩展，V28 固定 after_cost 且仅接受 C/M；V30 公共 CLI 也只接受 C/M（`tools/model_review_v30/validation.py:21`）。

建议新的 V31 独立入口先合法解析原 M case、确认完整 adopted M 配置，然后深复制并精确改唯一 cooling 字段；steam variant 的配置应与 adopted M 完全相同，差异仅为显式 thin hook。策略内部 alias 保持 M，用独立非空 policy_id/display variant 区分两种新方案。传给 common.worker 的 case 必须已经带完整最终 M config，避免再用新的 display 名去触发 policy_override_alias；controller_factory 必须收到完全相同的 config。环境 adapter 包装 V30 with_terminal 的 create，保留 V30 thermal boundary、fix1 和 true terminal hook。

最小新增范围为 V31 自己的 registry/resolver/CLI、controller 与 mailbox、core mixin 包装及新 export/audit；不需修改 frozen policy/planner/执行核/容量/输入。最终物理文件 SHA、输入文件 SHA 的不变性要独立验证，新增 V31 文件导致整树摘要改变属于新 producer 的正常情况。V31 法定窗口、GT_3 实际锁定目标、四个新 run、八个原 N02/D02 reused producer 角色和 prefix 比较由正式注册/审计负责；本接口复核没有执行这些运行。

## 实际接口探针与限制

`test_interface_review.py` 共 7 个检查：原 hook/队列/日志顺序、完整 adopted M 配置扩展及唯一 cooling diff、alias 改变会丢失 adopted 配置的反例、实际 legal_history 未来 provenance 拒绝、M23 super 委托保留、current-only 下冷权限层的 AST 控制范围，以及明确为 synthetic 的 mailbox 算术与坐标保持边界。测试通过仅说明这些现存接口和建议契约，不说明尚未实施的 V31 hook 已通过生产测试。

第一次探针错误地检查最外层 solve_joint wrapper，未发现内部候选函数的 layer，7 项中 1 项失败；这是审查探针的选择错误，非模型失败。保留 ATTEMPT01 日志与 receipt。随后仅把探针目标改为 `_solve_joint_candidate`，不改任何生产源码；实际最终命令、退出码、stdout/stderr 和源文件 SHA 清单见同目录 INTERFACE_PROBES_RECEIPT.json、INTERFACE_PROBES.*.log、SOURCE_EVIDENCE.json。
