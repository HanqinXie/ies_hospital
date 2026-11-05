# R2 当前实测蒸汽支持策略实现记录

本任务只完成新增策略模块及接口验证；未运行医院轨迹、未提交 Git、未修改既有 R1 或物理源码。授权依据为本轮明确的 R2 实现任务；本报告写入时，本目录尚无另行提供的 `IMPLEMENTATION_PLAN.md`。

## 文件与接口

- 新增 `tools/recovery_steam_20261104/policy.py`、`__init__.py`。
- 新增本目录 `test_policy.py`、`POLICY_RED.log`、`POLICY_GREEN01.log` 和本报告。
- API：`attach_recovery_policy(env, base_controller, config=R2_CONFIG)`，传入原生控制器，在首次 reset 前调用；模块内部先安装原 R1。
- 配置标识：`observed_steam_recovery_R2`。注册配置包含完整原 R1 配置；不接受未登记覆盖项。
- 原 R1 策略文件在两目录的实际 SHA256 均为 `c679cd76cb40fd5506dc8b445a7f66adea881820a9cc7edb6871af48800624b4`；`git diff --exit-code -- tools/recovery_dispatch_20261104 docs/recovery_dispatch_20261104` 实际退出码 0。

## 实际行为

原控制器、原 R1 决策请求和原 R1 命令准备先照常执行。R2 只在原 R1 监视器已观察到 TOWER 零到正恢复，且两个共享支持当前均为正时生效。仅泵恢复时逐值保留 R1 输出，不要求额外蒸汽字段；支持再次失效时暂停 R2。环境 reset 清除 R2 观测并沿用原 R1 的单次原控制器记忆重置。

生效时读取当前决策时刻可获得的最后完整实际区间 HRSG、锅炉总蒸汽，以及 P0–P3 当前全部热需求。额外源计量必须有效到当前时刻且区间连续至当前边界；缺失、陈旧或异常数值显式失败，不以命令权限代替实际蒸汽。

```text
热保留蒸汽 = sum(P0–P3 当前热需求 kW) / (原 steam_kWh_per_lb × 原 exchanger_eff)
剩余实测蒸汽 = max(0, 实际 HRSG lb/h + 实际锅炉 lb/h − 热保留蒸汽)
ABS 权限 = min(R1 ABS 权限, 当前有效 ABS 容量,
               剩余实测蒸汽 × 原 RT_TO_KW / 原 abs_steam_lb_per_rt)
```

转换系数来自环境原 plant 与原模型常数，随收据记录，没有新增物理参数。该上限来自已观察的蒸汽支持，不读取 ABS 动态实际产量作为上限，因此修复瞬间 ABS 实际供冷为零仍可正常获得正启动权限。它不是未来蒸汽或实际供冷保证；每段真实供给继续由原实际求解和动态提交限制。

EC 权限重新调用未改写的 R1 纯分配函数：原 EC 总权限若已满额则保持真实满额；否则仅按当前冷需求和保守 ABS 覆盖补充，并受原当前有效 EC 容量限制。未覆盖权限请求明示保留。不声称保留原先过大的 ABS+EC 独立权限总和，也不为补偿 ABS 权限下降制造额外 EC 容量。

GT、网电、柴油、锅炉与切负荷选择不变。原命令序号、排队和到达机制不变，原实际物理求解器、冷机/塔/泵动态与状态提交不变。

主动命令收据位置为 `command.joint_dispatch.diagnostic.recovery_steam_selection`。它保留原 native dispatch、R1 变换前阶段、R2 后阶段、实际源计量窗口、热保留量、转换系数和 EC 未覆盖请求。原 R1 收据明确是上一阶段；新收据不将变换后命令称为原 optimizer primal 或物理可行性证书。

## 实际验证

工作目录：`D:/hospital_ies_recovery_steam_20261104`。

```text
python -m pytest -q --confcutdir=docs/recovery_steam_20261104 docs/recovery_steam_20261104/test_policy.py --tb=short
```

| 阶段 | 原始日志 | 退出码 | 实际结果 |
|---|---|---:|---|
| 实现前 RED | `POLICY_RED.log` | 1 | 6 failed in 0.93s；均为缺失 R2 功能的显式断言 |
| 实现后 GREEN | `POLICY_GREEN01.log` | 0 | 6 passed in 0.51s |

六项实例覆盖：实际蒸汽而非锅炉命令信用、全优先级热需求预留及 P0 热需求增加后的上限降低、零 ABS 实际供冷不形成启动闭锁、满 EC 容量及源字段不变、泵故障恢复与独立原 R1 精确对照、缺失/陈旧实际源计量拒绝、原 native 命令生成函数中先变换后 15 分钟排队、切负荷不变、再次故障与 reset 暂停。没有调用优化器或构造医院轨迹。

`--confcutdir` 仅用于当前新增测试，保留旧工作区身份路由、旧测试和旧断言，未声称完整既有套件通过。

## 限制和待验证项

- 该上限是新登记的当前观测策略选择，不是新设备能力、热安全保证或 OEM 校准。
- R1、N/U 和此前失败证据继续保留；R2 收益、P0/P1 非劣、事故期一致性和能量结算均待另行连续运行及独立审计。
- 未改变源侧出力；源侧电力不足及不可避免的真实启动过程仍可能限制改善。
- 未训练、扫描参数、扩展场景或绘图。
