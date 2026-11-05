# R1 恢复期命令选择层实现记录

本记录只交付策略模块及接口验证；没有运行医院轨迹，没有提交 Git，也没有修改既有物理源码。

## 实际修改

- 新增 `tools/recovery_dispatch_20261104/__init__.py`。
- 新增 `tools/recovery_dispatch_20261104/policy.py`。
- 新增本目录 `test_policy.py`、RED/GREEN 原始日志及本记录。
- 冻结原目录 `D:/hospital_ies_noopt_boilerfix_20261104` 未写入。全部实现位于新的 `D:/hospital_ies_recovery_dispatch_20261104`。

## API 与行为

```python
from tools.recovery_dispatch_20261104.policy import R1_CONFIG, attach_recovery_policy

env, base_controller, resolved = original_build_runtime(case, source_freeze, cooling_config)
controller = attach_recovery_policy(env, base_controller, config=R1_CONFIG)
# 原 env.reset/step 调用形式保持不变；必须在首次 reset 之前 attach。
```

`R1_CONFIG` 固定为 `recovery_converter_continuity_R1`；不接受未登记参数。包装器每次照常调用原控制器，原 request 和原控制器 diagnostic 对象均原样返回。它只读取当前快照中的有时间来源的支持可用率、冷需求、有效设备容量、已完成区间 ABS 供冷和冷机动态状态，不接收场景或未来事件表。

支持设备为 `TOWER` 与 `AUX_CHW_PUMP`。先观察某一支持的可用率为零，再观察同一支持为正，才记录恢复激活。正值不要求人为提高到 1；同时两个共享支持均须当前为正。再次支持失效时暂停本层，恢复前仍由原命令决定。每次原环境 reset 同时重置策略与原控制器记忆。未来时钟、非当前有效的支持/容量值和动态状态时钟不匹配显式报错。

原 `_prepare_command_dispatch` 的完整继承链先运行。本层不激活时，返回该链的原对象。本层激活后只替换 `electric_chiller_cooling_kW`：

1. ABS 覆盖取当前已完成区间实际 ABS、当前 ABS 动态状态、当前有效 ABS 容量及原 ABS 权限的最小值。陈旧的实际供冷不授予当前覆盖信用；原观测数值不被改写。
2. 请求 EC 总权限为 `max(原 EC 权限总和, 当前 P0–P3 冷需求总和 − ABS 覆盖)`。不把储冷余额计作未来保证供给。
3. 按当前已运行冷量降序、机号稳定择优，逐台分配至当前有效容量。`capacity_EC_i` 已在原 `data_contract.graph_view` 中乘以可用率，不能再次降额。
4. 实际设备容量不足时记录未覆盖权限请求；若原 EC 总权限本身超过当前设备总有效容量，则显式拒绝，避免默默降低原权限或假造容量。

GT、网电、柴油、锅炉、ABS、其余派生字段和所有切负荷输出保持原值。生成命令仍进入原 `sci_engineering.environment.EngineeringCore.generate_command`，沿用原序号、队列及到达时刻。实际产冷仍由原源侧、电力、热、塔、泵、储能、服务次序及动态限制决定。

主动命令日志位置为 `command.joint_dispatch.diagnostic.recovery_dispatch_selection`，包括原 solver-emitted dispatch、原继承准备后的 dispatch、本层变换后 dispatch、恢复观测、真实容量及未覆盖请求。原 native solver diagnostic 不变；收据明确声明 native optimizer proof 不覆盖变换后命令，本层不生成物理可行性证书。

## 实际验证命令与退出码

以下命令工作目录均为 `D:/hospital_ies_recovery_dispatch_20261104`：

```text
python -m pytest -q --confcutdir=docs/recovery_dispatch_20261104 docs/recovery_dispatch_20261104/test_policy.py --tb=short
```

| 阶段 | 原始日志 | 退出码 | 实际结果 |
|---|---|---:|---|
| 首次 RED，生产模块尚不存在 | `POLICY_RED.log` | 1 | 11 failed，均为缺失 R1 功能的显式断言 |
| 首次 GREEN | `POLICY_GREEN01.log` | 0 | 11 passed in 0.49s |
| 时效性和容量保留反例 RED | `POLICY_RED02.log` | 1 | 4 failed, 12 passed in 0.49s |
| 最终 GREEN | `POLICY_GREEN02.log` | 0 | 16 passed in 0.46s |

RED02 实际暴露了陈旧但非未来的支持/容量值仍可触发恢复，以及小于原先 1e-8 容差的容量超额会被静默裁掉的问题。修复分别要求当前有效时钟及严格拒绝不可能同时满足的容量/原权限总和，没有放宽断言或容差。

已通过的验证覆盖：原控制器/请求/诊断精确委托、不激活输出对象不变、任意时刻的塔/泵零到部分恢复、未来及陈旧时钟拒绝、有效容量不二次降额、暖机优先和冷需求覆盖、原字段不变、原权限总量保留、容量不足显式结果、原 native 命令生成函数的 15 分钟排队与切负荷不变、环境 reset 隔离。排队测试使用原函数的 ordinary route，不调用优化器，不构造医院轨迹。

根目录旧 `conftest.py` 有绑定历史工作区身份的旧测试路由。本命令以 `--confcutdir` 只收集并验证当前新模块，不修改旧钩子、旧清单或旧断言；未声称完整既有套件通过。

## 假设与限制

- 本层为登记的策略启发式；不增加物理能力、执行路径、医院/OEM 动态参数或服务保证。
- 完整既有测试、两条连续 R1 轨迹和 R0/R1 比较尚未由本实现任务执行。策略收益、P0/P1 非劣和事故期结果不变仍须由运行与独立审计验证。
- R1 不修改 GT 反复升降调度，也不保证消除 T06 的源侧电力限制。负结果必须保留。
- 没有训练、参数搜索、额外场景或绘图。
