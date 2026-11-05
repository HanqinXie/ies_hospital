# 固定故障前支路份额的严格无优化组实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. 同一物理状态转移由根代理串行实现；文档与只读审查可以并行。

**Goal:** 实现由共同故障前检查点启动、全执行期禁用优化且按冻结支路份额分流的新 U，覆盖用户要求的无优先调度语义。

**Architecture:** 共同环境准备与新 U 执行分离，以 `fork_fixed_unattended(common_env)` 深复制活状态。冻结最后实际区间的建筑电/热/冷及一般电力机房端口份额，按额度供能并允许需求封顶后的余量保持未用；保持原专用源连接和物理事件。新 U 的 reset 拒绝，优化防护覆盖检查点后所有实际与诊断路径。

**Tech Stack:** 现有 Python、NumPy、pytest、项目分钟事件执行核与算术流评估器；不增加优化库、MPC 或未来信息。

---

状态：设计已根据用户选择登记；以下均为执行清单，不代表实际测试通过。精确命令、退出码、原始日志、diff 和检查结果须由执行代理另行保存。本计划不启动科研扩样、训练或旧图替换。

## 文件职责

|文件|职责|
|---|---|
|`tools/noopt_unattended_20261104/forward.py`|冻结份额算术分配；一般与专用连接分离；资源及依赖闭合|
|`tools/noopt_unattended_20261104/runtime.py`|冻结状态接入、全部执行路径禁优化、旧命令/保护接口及区间证据|
|`tools/noopt_unattended_20261104/environment.py`|`finish_common_prefault_step(common_env, action)` 暂停故障边界、`fork_fixed_unattended(common_env)`、U reset 拒绝、准备/U 角色分离、版本和快照接口|
|`tools/noopt_unattended_20261104/fixed_branches.py`|冻结记录、份额校验、额度封顶与余量不转供的算术函数|
|`tools/noopt_unattended_20261104/checkpoint.py`|活状态深复制与工厂闭包捕获 bound method 的独立重绑定|
|`tools/noopt_unattended_20261104/probe_checkpoint.py`|实际共同准备与有界 U 的迁移验证，不作为完整科研轨迹|
|`tests/test_no_priority_fixed_branches.py`|冻结分配、机房/专用源、无隐性优先及共同检查点迁移回归|
|`tests/test_noopt_forward_arithmetic.py`|保留已有能量/连接/库存/求解拦截覆盖，登记旧优先断言迁移|
|`docs/no_priority_20261105/DESIGN_ZH.md`|已选物理代理、授权覆盖与资格边界|
|`docs/no_priority_20261105/IMPLEMENTATION_PLAN.md`|本清单、文件职责与验证顺序|

上述新增辅助模块已按单一职责拆分，不另写第二套物理状态转移。状态和验证结论仍由实际命令日志决定。

## Task 1：基线与 RED 证据

- [ ] 在 `D:/hospital_ies_no_priority_20261105` 保存 `git rev-parse HEAD`、`git status --short`、原 forward/runtime/environment SHA 和 Python/依赖版本。基提交应为 `468f5fe` 的完整提交对象。
- [ ] 核对新工作区与旧 producer 隔离；原始输入和旧 E 盘输出仅作只读来源。日志与新产物显式写入本轮 D/E 新目录。
- [ ] 新增冻结额度的最小反例：份额 `[0.25,0.75]`、预算 `40`、需求 `[5,100]` 应交付 `[5,30]`，未用额度为 `5`，不能转为 `[5,35]`。
- [ ] 新增零份额反例：份额 `[1,0]`、第二支路当前需求增加时，第二支路仍得 `0`；全零来源不能退化成均分。
- [ ] 新增优先顺序反例：对标签及其份额、需求和拓扑掩码做一致置换，输出应做相同置换；仅换标签不应改变分流规则。
- [ ] 新增隐性预留反例：一般电力的建筑与机房份额固定后，提高 P0/P1 需求不得夺取机房原冻结额度。
- [ ] 运行 `python -B -m pytest -q tests/test_no_priority_fixed_branches.py`，保存完整 RED 日志与退出码；检查失败来自旧语义或尚未实现入口，而非测试导入路径错误。

## Task 2：冻结数据与算术分配

- [ ] 从最后已结算实际区间建立不可变冻结记录：时间、检查点身份、原端口量、分母和各份额；不得读未来故障或后续 R 轨迹。
- [ ] 对每个固定连接组实现 `quota=max(B,0)*s`、`delivered=min(max(request,0),quota)`，剩余额度原样进入未用量；拒绝非有限、负份额或不一致维度。
- [ ] 一次冻结建筑电/热/冷份额、一般电力建筑/机房份额和专用源连接内份额；一般电力账本不得把建筑辅机重复计入机房。
- [ ] 去除 `_take` 的 P0→P3 逐级取用和电力循环的逐级优先供给，去除 `critical_non_ups` 先为 P0/P1 预留普通供电的路径。
- [ ] 保持已连接 UPS/DG/蓄冷仅在原 P0/P1 集合中按冻结份额分流；缺少来源时显式报错或保持已登记零份额，不能新增其他连接。
- [ ] 将冷热源、蒸汽与辅机电力闭合接入新额度；需求封顶、故障或饱和产生的未用量不能重新归一化分配。
- [ ] 保留原物理方程/事件求根，并确认每次试算不提交库存、保护或设备时钟；不增加候选评分或优化回退。
- [ ] 重跑 `python -B -m pytest -q tests/test_no_priority_fixed_branches.py`，保存 GREEN 或实际失败及原因，不扩大容差。

## Task 3：共同检查点与全执行期禁优化

- [ ] 使用 `finish_common_prefault_step(common_env, action)` 完成严格 59→60 的共同实际区间，在 `ArithmeticCoreMixin.boundary` 暂停第 60 分钟故障/旧命令边界并写入暂停标记，防止共同准备提前执行事故响应。
- [ ] 在 `environment.py` 增加 `fork_fixed_unattended(common_env)`。校验暂停标记、最后实际区间结束于第 60 分钟及冷却动态状态；深复制共同活状态后安装冻结记录和新 U 身份，U 自己执行尚未施加的第 60 分钟边界。
- [ ] 在 `checkpoint.py` 重绑定工厂闭包捕获的原环境 bound method；以原日志实现和真实物理状态检查 fork 后推进没有落到共同原对象，不能只检查复制对象的 `core.env`。
- [ ] 新 U 公开 `reset` 明确拒绝；共同准备优化入口只允许在共同环境中运行，其调用与 producer 单列。
- [ ] U 的禁优化条件改为新 U 身份全执行期生效，不使用旧 `time>=60` 才开启的条件。
- [ ] 在已登记高层、项目后端和原生入口设置实际调用拦截；至少覆盖 `solve_hour`、`solve_physical`、项目 `milp`、`_gurobi_attempt`、SciPy `linprog/milp` 及直接原生优化调用。Gurobi 的 Model 构造、optimize/异步优化、feasRelax、computeIIS、tune、presolve 使用 context variable 控制的显式入口拦截，不能依赖 Cython 一定产生 profile 事件。零目标可行性优化同样拒绝；纯物理求根单独归类。
- [ ] 禁优化错误使用不可被普通 `except Exception` 吞掉的致命契约异常，首错置失败状态；检查错误被错误捕获时上下文仍抛出失败，后续受保护入口拒绝继续，不允许因 CPython 清除 profile 而恢复运行。
- [ ] 对实际步进、保护见证、评价、观察和快照执行禁优化审计；调用尝试在目标函数体/原生执行前拒绝并保存入口、时刻和角色。
- [ ] 验证旧排队命令合法到达可改变原许可目标，但冻结份额及建筑热/ABS 蒸汽比例不变；U 不生成新调度或优先自动切负荷请求，仅执行共同检查点前已经发送、到达时刻和请求数量固定的旧自动切除。
- [ ] 验证推进 U 后共同环境和响应分支的库存、队列、历史、保护时钟、冷却动态和份额未变；验证共同准备记录不会冒充 U 的零调用证明。

## Task 4：物理与接口回归

- [ ] 覆盖普通供电不足、机房失电、风机/泵依赖、固定冷源份额、锅炉蒸汽实际去向、无外送和最小稳定出力保护。
- [ ] 覆盖 UPS/DG 不向机房或 P2/P3 输送、专用连接内无 P0 优先、蓄冷泵耗、库存空/满事件、燃料耗尽和故障恢复。
- [ ] 对每个合成区间重算电/热/冷、库存和服务账本，使用原验收容差；未用量与未知尾段不得静默归零。
- [ ] 将旧优先测试迁移为 `test_fixed_branch_allocation_and_storage_are_energy_bounded`：储能功率、拓扑与能量断言保留，以普通支路保持冻结份额替代“P2/P3 必为零”。将 t60 边界拦截测试迁移为整个 U 生命周期；充满库存测试提供原已存在充电支路。保留旧反例及 RED 日志，不得只删除断言。
- [ ] 运行 `python -B -m pytest -q tests/test_no_priority_fixed_branches.py tests/test_noopt_forward_arithmetic.py tests/test_noopt_unattended_guard.py`，保存完整命令、退出码和日志；失败逐项定位后有限修复。
- [ ] 运行现有默认入口 `python -B -m pytest -q`。如选择集未含新增测试，上一步的显式测试结果仍单列；不得改选择集或沿用上版测试数字。
- [ ] 检查新 U 快照、历史和区间 schema 的版本迁移，确保调用者能区分共同准备、旧固定规则 U、新冻结份额 U 和原响应组。
- [ ] 使用有界真实运行核工具检查共同准备与 U 迁移。本轮已启动命令为 `python -B tools/noopt_unattended_20261104/probe_checkpoint.py --case E:/hospital_ies_cooling_dynamics_20261104_run01/runs/T03_R_attempt02/CASE.json --output E:/hospital_ies_no_priority_20261105_run01/integration03 --minutes 3`；该输出目录已经存在，不可原样重复启动，任何重试须使用另一新目录并登记原因。退出码和日志由运行回执补齐，启动不等于通过。该工具单列 60 分钟共同准备，检查 3 分钟 U、共同物理状态不变、算术结果归属、优化尝试与资源残差；不将其报告为完整轨迹或图重生成。

## Task 5：交付与结论边界

- [ ] 审查 `git diff --check` 与完整 diff；核实仅本轮授权文件变化，响应组目标、微小容差、容量、负荷和保护阈值未变。
- [ ] 保存实际修改文件清单、源 SHA、diff、命令/退出码、原始日志、已通过/失败/未执行测试、假设变更和已知限制。
- [ ] 报告共同准备可能含优化，新 U 自检查点起禁优化；不能简写成整个准备过程从未优化。
- [ ] 报告冻结份额来自模拟状态，是聚合研究代理，尚未获得现场分流/动态标定；测试通过不作为临床或现场物理资格证明。
- [ ] 若本轮未执行完整新轨迹，明确注明；不使用旧轨迹作为新模型结果，不替换旧图或混合 producer。后续实际轨迹使用新目录和新绑定，结果不以强行分开红绿线为目标。

## 计划自审

本计划已覆盖用户选定的冻结份额、需求封顶、余量不转供、一般电力机房支路、零份额、原专用连接、旧命令、共同状态分叉、公开 reset 拒绝、全执行期禁优化、RED/GREEN 与接口迁移。未加入 MPC、未来信息、新容量或凭空硬件参数。清单完成状态只由后续实际运行证据更新。
