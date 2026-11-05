# 严格无优化U：实施状态与最小保护

日期：2026-11-04。工作区：`D:\hospital_ies_noopt_20261104`；起点：`9198b6b`；分支：`research/noopt-unattended-20261104`。

## 已实现的范围

本轮只交付旧U反例、公开step入口的fail-closed保护及后续任务书。**尚未实现可运行的无优化forward baseline，没有新科研轨迹。** 旧U只停新上层命令，仍使用下层供能/服务分配优化；旧U曲线不能作为新“上、下层都不优化”定义的结果。

新工作区的minute1工厂对U的任何结束将到达或超过60分钟的step，在进入原生step前抛出`StrictUnattendedForwardUnavailable`，category为`strict_unattended_forward_unavailable`。采用提前拒绝是因为`EssentialResponseMixin.advance`最后还调用`self.boundary()`，59→60步骤不能仅按入口时间放行。标准一分钟步长下，最后可完成区间为58→59，59分钟之后为未执行/未知尾段，不能记为缺供或成功。

保护不生成替代解、零向量、交付量或库存积分；它阻止整个尚不合格的步骤，保持队列、已发命令、时钟及历史原状。保留的前缀不能拼接成完整180分钟结果。该保护只保证受检查的公开环境step入口；直接调用core内部方法仍能进入旧优化链，明确不宣称全仓库任意入口已零调用。

## 根因及规模评估

- `tools/minute1_20261103/runner.py`仅对U在60分钟后令request=None，随后仍调用env.step。
- `tools/essential_fixes/execution.py`的advance调用fixed_execution `_solve`，并在结束时调用boundary。
- `tools/model_closure_v22/environment.py`既解实际平衡又按多个优化结果选择储能模式；当前端口选择同样解优化问题。
- `tools/essential_fixes/execution.py`的GT最低出力校正枚举子集，以ENS和成本择优。
- `src/hospital_ies/execution.py`的maximum_auxiliary_sink使用linprog；联锁/rearm/release及无储能见证还有额外求解入口。
- `tools/model_reference_v32/planner.py:audit_plan`是给定全端口流的纯算术核查器，并不是从状态生成交付的forward模拟器。

完整forward涉及至少上述实际流、离散运行域、储能模式、保护见证和账本接口，不能通过删除_solve、换零目标或函数改名完成。本轮按授权的受控交付路线停止大范围物理重写。

## 实际验证

所有测试为合成绑定/执行入口组件测试；未进行真实优化、环境数值初始化或科研轨迹。

1. 首次pytest受旧conftest绝对路径绑定拒绝：退出4，no tests ran。原始日志`logs/01_red_legacy_u.txt`保留。
2. 显式选中本次文件并使用`--noconftest -p no:cacheprovider`：修改前2 failed，退出1。60分钟的真实EssentialResponseMixin.advance到达fixed_execution `_solve`哨兵；另一个反例到达终点boundary哨兵。优化器在哨兵处中止，没有真正求解。日志`logs/02_red_legacy_u.txt`。
3. 初次保护后13 passed，退出0；日志`logs/03_green_guard.txt`。
4. 加入保留的未封装原生反例后，最终15 passed，退出0；日志`logs/04_final_guard.txt`。

最终命令：`python -B -m pytest -q tests/test_noopt_unattended_guard.py tests/test_minute1_environment.py --noconftest -p no:cacheprovider`。

`--noconftest`仅用于本次明确选定的新组件测试，原因是根conftest绑定旧minute worktree并路由323个历史node。没有改conftest、路由清单、pytest默认选择或历史断言；没有执行/宣称通过默认全套测试。

旧`test_unattended_boundary_stops_only_new_commands_and_retains_old_queue`的“post60仍成功推进”假设与新严格U定义冲突，已改为明确拒绝。原先的前边界发命令、队列保持、错误新命令拒绝覆盖保留；新增时钟/历史不得变化覆盖。没有放宽容差。

## 假设与边界

没有改变容量、负荷、故障日历、保护阈值、P0–P3判据、原始输入和旧输出。新的公开入口可用性限制是显式拒绝尚不合格的U执行，不是新增物理失效规则。固定运行设定方案只在后续任务书中登记，尚未实现，也没有任何收益/恶化结论。验收不以红线更差、算法优势或失效比例为目标。
