# 局部八故障执行与证据适配迁移记录

范围：只为已批准的 canonical 局部案例增加 detached calendar 因果等待与原生实际解读出。不启动年度、35 天、训练或策略搜索；原物理方程、容量、P0–P3 服务锁、六动作、15 min 决策、普通队列和负荷授权保持原语义。

## 局部入口

新入口 `python -B -m tools.eight_fault_study.runner --case-file CASE.json --output E:/.../fresh_attempt`；每次使用新目录。`--retain-history` 仅决定已持久化 solver-call 的内存保留，合法观察和原实际 segment 完整前缀始终保留。`run_case` 是同一局部入口的可调用形式。经独立审查补充的可选 `--stopfile PATH` / `run_case(..., stopfile=...)` 见 README。

局部入口复用 `annual_readiness.runner._prepare_attempt`、`_execute_attempt`、`_execute_loop` 和原始 journal/失败/sidecar/核账。旧 runner 只增加私有参数 `_execute_attempt(output, *, runtime_builder=None)`；默认行为仍调用原 `_build_runtime`。原公共 `run_case`、`reset`、`step` 和日志字段未改。新 producer 保留原日志 schema，并通过 `INVOCATION.json`、`RESOLVED_CASE.json` 与 `LOCAL_ACTUAL_WITNESS_SOURCE_CONTEXT.json` 明确标识局部 adapter 和新的 source identity，不重标历史 producer。

## 已复现问题与最小修复

M23 在 reset 前把 `model_closure_events` 从 scene 分离到 env；旧 StudyClockMixin 仍只查询 scene。因此 60.25 min 故障和 15 min 等待曾产生 `(False, 60.25)`，且 90 min 前不切分释放点。新的 `LocalActualCoreMixin` 真实继承原 `AnnualSourceMixin`，复用其 detached calendar 的因果 clock：当前等待只受已发生 onset 影响，未来 onset 只切分模拟物理区间。60.25 min 时为 `(True, 75.25)`，75.25 min 释放。

继承仅保留原精确 `_after_actual_segment` 和 clock。`data_at`、`boundary`、`_record_error`、`_annual_error_row_context` 显式沿 `super(AnnualSourceMixin, self)` 返回 native MRO；不复用年度 endpoint 或年度 failure-history 替代。`source_runtime.py` 字节不改，原 native commit 完成后才把同一 segment 与同一 final result 对象传给原 recorder。未新增 solve/observe、候选等值匹配或平衡余量弃汽计量。

## source-only context 与数值读取边界

`local_actual_witness_source_context` 由局部 wrapper 绑定真实 env 对象。它在任何 native 构造/数值解析前核对当前 source/input/HEAD BINDINGS、规范 case、硬件/规则配置、完整 allowed_hours、原始 SEALED_DAYS。年度 context、final-blind release、非规范 case、封存支持和支持越界均在 native 构造前拒绝。source context 不传给 loader，不进入 selective_inputs；env 的 `annual_program_data_context` 仍为 None。原 native loader 继续独立核对数值授权。

reset 前核对真实完整 scene，reset 后核对实际 stripped scene、detached calendar、actual config、执行参数字典和 operating rule。每个 compiled actual-witness 取 freeze 时再次核对 env 对象/root/scene/calendar/support/sealed/rule/config。native `_model_closure_v22` 与 `_sci_storage_ports` 是每次 solve 的动态上下文，在执行参数静态投影中分离。首个正式 attempt 的失败还定位了原 M25 在命令生成时缓存到 env.config 的 `_model_correction_v25_reserve`：仅在已核对的 `sustained_critical` 规则下分离这一具名动态字段；非 sustained 规则的意外注入仍作为差异拒绝。原 solver_context 为实际 solve 的 dt/库存重新构建上下文，原 validate_solver_context 检查其形状/数值，原 packet/source/code/object/rows 的实际结果认证保持不变。command 缓存使用原 15 min 决策时长，不能在 post-commit hook 按已前进库存/任意 dt 重算要求相等；本迁移也不声称 compiled DTO 直接携带 V25 reserve 对象。native reset 对已登记 `initial_reserve_kWh` 的原始赋值显式核对；不把它误报为配置漂移。

独立审查 `LOCAL-SPEC-01` 指出，SCI 原 solve 消费的是 `operating_execution_contract`，而原 `execution_contract` 是剔除了 diesel_dispatch_mode 等字段的另一个字典。现将前者也加入同一静态快照，覆盖构造后、reset 内、reset 后及 recorded witness 前的漂移拒绝；没有修改这两个原字典的实际值。

`witness._actual_source_freeze` 只选择 annual source freeze 或具名 local source-only freeze；双 context 拒绝。原 compiled_origin、loaded code、source bytes、hook frame/code/self/segment/result、raw_x、dt、rows ownership 校验全部保留。capture_scope 只复制原 leaf 已使用的输入，不能赋予 actual 身份或数值权限。origin 失败留在原 attempt 的 try/journal/RESULT 失败链内。

capture_scope 的生命周期与原 annual runner 一致：只薄包装原 `env.step(action)`；reset/初始化、合法 snapshot、terminal export 均不在 capture scope。synthetic warmup-hook sentinel 对早期整 attempt scope 复现失败后已收窄，未放松 `reset_complete` 校验。operating-rule constructor 仍传原 `name`/`design_minutes` 字段，实际 env 的规范 rule_id 则通过原严格 parser 派生并核对；保留原 V25 构造门。

受控 stop 只在原 attempt builder try 内的环境构造前、以及薄包装 step 的 capture/原 step 前检查具名绝对路径的存在。`ControlledStopRequested` 经原失败链记录，不能将失败 RESULT 改写为 completed；没有改原执行循环、命令队列或 solver。初始化/单次 solve 内无法即时响应的限制由 README 明确记载，marker 不会进入 controller 或 source numeric grant。

## exact release 兼容

recorder 新增 resolver 改变其 SHA，因此当前 literal release 为 `3f774bc3a0871b00703c948a680ec4295146f487d8f496d925b82ec2ac36c14c`。

- A60602 继续精确保留 backend `5c67375a12d9972862bc0fa24b98fef07da9309e7c4ad01770223d1c7dad8fdd`、helper `0ecbf955523f8c7a8a2a45ab38931dbe9cf86aac5f17625e329cedd0701725e4`、recorder `f38fc6e4f45886599f3fe1d6efa2b84013ba21ed3607f131293d4fbb25bb697e`。
- FROZEN16、FROZEN12 与 legacy 已登记配对继续可读；新 recorder 不可冒充 FROZEN16 的 recorder。未知/混用 helper/backend/recorder 均拒绝。
- 没有动态 hash 审批或注册接口。

旧 `test_annual_exact_cache_source_releases.py` 的两处假设失效：当时 current 与 FROZEN16 recorder 恰好同为 f38，且没有显式冻结 A60602+S17。现在按各 release 的 literal recorder 检验，并新增新 recorder 冒充 S16 的拒绝覆盖。原 helper 选择、冲突 fail-closed 和 FROZEN12 拒绝断言保留；没有扩大容差或删除物理断言。

## 本实现的资格限制

本阶段仅运行 synthetic pytest，未构造任何真实医院环境、未读取数值用于仿真、未产生正式轨迹。四条首批 M_ready/H2 case 仅做 canonical 元数据配置覆盖。局部 clock 和证据适配通过测试不等于八专题已满足科学验收；真实短例和独立审查由主任务后续在单一物理进程树与已登记预算下进行。源/输入最终清单必须绑定本次新源码 commit，不能覆盖 991/36 的启动基线清单。
