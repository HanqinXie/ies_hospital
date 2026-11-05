# V31 最终实现只读复核

结论：本次复核没有发现阻止当前四条正式对照收尾的 critical 或 important 实现问题。现有改动符合“保留完整 M23/M22 决策与共同准备，仅增加显式注册权限”的要求。此结论限定于本轮两个背景、四条新增正式运行及其对应复用记录；交付压缩包完整性由独立验收负责。

## 版本与文件边界

- 实际 HEAD 为 `f009bdb66d5b9d53f1ff2331f2dc58404c2d056b`；审查时 git status 为空。
- 与文档基点 `e635b919dae9e1c89793a4c29eecbab4e27a05ec` 的差异为六个新增科学文件及 AGENTS/三份 V31 文档；没有改动旧科学文件或原输入。
- 独立逐文件读取并计算当前 P01 的 864 个 source、36 个 input SHA：全部匹配。旧 C02 的 858 个 source 在新 freeze 中逐项完全相同，36 个 input map 完全相同。新增科学文件正好四个模块、一个配置、一个测试。
- P01 科学来源为本次 HEAD，source digest 为 `e4c740329fddcddae9e729acc24e6149d1d873e89783e0ef310e3623f604a6fa`；新增运行没有冒称由旧 producer 直接生成。

## 逐条实现复核

**原策略与当前见证没有丢失。** `tools/model_closeout_v31/environment.py:6` 导入真实 `tools.model_correctness_v23.policy.SciPolicy`；`:10` 从它继承，`:18` 先完成 `super().decide(records)`，之后才提取当前锅炉观测。原 M23→M22→sci 决策链仍验证完整合法历史及当前物理见证，M22 对 `critical_non_diesel_ready` 的当前 witness 替换仍有效。新增代码不以裸 sci 策略替代它。`tests/test_v31_preparation.py:97` 用完整原 Plant 比较新旧请求，防止意外绕过既有 converter/tower 信息依赖。

**mailbox 的信息边界明确。** `rules.py:24` 只读取最后一条决策记录的 decision clock、三个 `availability_BOILER_i` 和各自 available/valid clock，外加 controller 初始化时复制的固定 Plant rating。数值检查拒绝 bool、非有限数、负值及 >1 的可用率；时钟拒绝未来或缺失信息。该纯函数不接收 env，不读 scenario、事件表、未来故障、修复、需求轨迹或旧 C 输出。`environment.py:19` 的输入来自已通过父类验证的同一记录；hook 读取 env 上的专用 mailbox 和当前生成时钟，没有增加 observe、snapshot、witness 或求解调用。

**共同准备先执行，唯一 dispatch 改动为锅炉。** `environment.py:26` 首先调用完整 MRO 的原 `_prepare_command_dispatch`；`:27` 再调用纯规则。`rules.py:41` 检查 receipt clock 等于生成时钟、容量与原权限有限且不越原总额定容量，深复制原 dispatch/diagnostic 后仅给 `value['boiler_steam_lbph']` 赋值。规则为 max(common old, healthy rated capacity)，保留健康容量低于原 common 权限时的 old。原 common EC/热准备仍保留；没有写 actual state、stock、arrived dispatch、实际出力或额外热需求。

**默认与冷准备路径隔离。** `environment.py:32` 对 None 和 M_cooling_permission 直接返回原 V30 adapter；不创建新 core/mailer/factory 或增加锅炉权限。只有显式 M_steam_permission 才设置专用 env mailbox，并在正常 reset 前组合 `(SteamPermissionMixin, existing_core)`。mailbox 不进入 core 的初始实例变量；`reset_policy_memory` 清空 mailbox。当前正式入口每个 run 使用全新 adapter、controller、episode 和进程，没有多实例共享使用。

**固定场景与在线控制分开。** GT_3 和背景限制只存在于离线配置/入口验证；在线 `rules.py` 与 SteamPermissionPolicy 中没有 GT_3、场景编号、60 分钟或故障状态触发。四个登记场景与原对应 M 场景逐项相同。b07 disturbed 仍固定 GT_3，即使新准备改变其故障前出力，也不重新挑选目标。

**配置重展开没有覆盖 adopted M。** `rules.py:11` 强制完整 adopted M 的 after_cost/current/no reserve/no time-energy 设置，并再次用真实 M23 policy_config 展开核对。`validation.py:12` 先走原 V30 resolver，然后要求内部 alias=M 且实际完整配置等于登记的唯一变化。b05 仅 cooling_preparation=true；b07 完整配置与原 M 相同。新增方法使用独立 policy_id、method 和目录，而非新 alias 触发旧工厂 bare defaults。

**队列与真实执行仍来自原路径。** mixin 放在原 planner 结束、原 command 入队之前；没有修改原 generated/arrival 时间计算、pending_commands、实际到达或启动过程。原 V30 terminal adapter、fix1、热边界与 V30 exporter 均保留。当前最终 AUDIT 的 command_trace 已分别保留 planned_arrival_minute、actual_arrival_events、cold native layer pointers 和 compact steam receipts，没有把计划到达混作实际出汽。

## 实际证据核对

保存的 `steps/targeted_shared_green01/COMMAND.json` exit=0；对应 stdout 为 `60 passed in 14.50s`，stderr 为空。这个 60 包括 14 个 V31 测试；不再相加，不称为本审查者新跑的 60 项，也不声称重跑过历史 1363。原 RED、第一次测试 fixture 失败及后续 GREEN 的保留方式见 MIGRATION 和原 steps 记录。本复核没有重跑模型、求解器或这些 pytest。

`evaluation/final01/AUDIT.json` 保存 12 audited、12 completed、8 reused_v30、4 new_v31、0 not_started，final_matrix_complete_and_clean=true；无 audit exceptions/integrity failures，所有 run checks 和 75 个 context checks 通过。四个新增记录均绑定 HEAD/P01 科学来源、实际 observed/requested=180；完整 native coverage/terminal 检查通过。

两个新方案 normal/disturbed 的 t0 state 和 core 都是 **raw exact equal**；[0,60) 在线实际前缀在仅归一明确 solver 容器的 elapsed_seconds/TimeLimit/remaining_original_budget_seconds 后完全相同。RNG 的真实差异仍记在审计中，未称 full snapshot 相同，也没有把 RNG 差异当求解耗时过滤掉。跨方法共同 t0 state/core 的实际检查也通过。

冷准备的实际 native healthy_converter_permission 层及锅炉 old/common/healthy/new receipt 均在最终 command_trace 中有紧凑记录和原生指针；不能单凭 current-only 摘要 cooling_constraints_active=false 否认权限层执行。权限与实际冷/热输出仍分开。四条新运行是否改善或产生代价应按最终工程报告的原始数值解释，本复核不添加获胜或全面物理资格结论。

## 本复核的实际命令与产物

执行 `check_saved_evidence.py` 的实际 exit=0，共 19 项文件/配置/已保存证据检查通过；没有导入生产环境、创建环境或求解。检查当前 864/36 文件、git HEAD/差异、四个配置、最终矩阵与 t0/prefix 摘要。原始 stdout/stderr、完整命令与环境、检查结果及源码/证据 SHA 在本目录：`COMMAND.json`、`CHECKS.json`、`CHECKS.stdout.log`、`CHECKS.stderr.log`、`REVIEW_SHA256.json`。

没有遗留必须修复的审查项；不建议因样式或未经授权的新功能继续扩展本轮源码。
