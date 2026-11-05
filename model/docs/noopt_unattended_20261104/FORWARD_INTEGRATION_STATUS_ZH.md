# 无优化U与冷却动态接续：当前实现状态

日期：2026-11-04；工作区`D:\hospital_ies_noopt_20261104`。本说明接续此前仅fail-closed的阶段：**新factory现已具备实际算术forward及冷却动态接口；旧minute1 factory仍保留拒绝旧U的保护。** 两个入口不得混称。新正式连续轨迹由根代理统一RAM准入及从0执行，本代理未启动19轨迹campaign。

## 当前入口与边界

新入口：`tools.noopt_unattended_20261104.environment.with_forward_minute1`。正常/响应与U前60分钟沿用native优化；U达到60分钟后，原SF2 FuelCore wrapper仍验证实际GT FuelPath，随后进入ArithmeticCoreMixin的纯算术flow。其保护、转移、non-DG见证、重启可行性与储能模式选择均不调用调度优化器。公开step/decision snapshot外层以Python profile在原始函数体执行前拒绝physical_balance、项目milp、scipy milp/linprog等入口并保存attempt；不替换受保护函数对象。

旧工厂`tools.minute1_20261103.environment.with_minute1`仍在59→60前fail-closed，避免任何人误把旧“只停上层”的路线当严格U。新runner应显式使用新factory。

## 固定运行规则

- t60前最后真实实际运行参考锁存；已发队列按既有到达/覆盖时序执行。旧队列的planned_current_action明确给出实际欲执行目标时使用该目标，不把独立ceiling任意变成备用启动许可。
- 故障硬停/容量裁剪、GT爬坡/最短开停机及启动、DG启动/ATS/有限燃料、锅炉连续供电复位、既有联锁、温度与失效积分沿用物理状态提交。U无优化不等于冻结交付。
- 冷却端口保持运行参考及固定比例，受蒸汽、冷却塔、实际辅电与动态可达性饱和裁剪。不会因另一机故障而优化启用从未出力且无旧目标的备用冷机。蒸汽支路份额来自已执行运行状态/已到达旧计划，不是新增ENS偏好。
- 建筑每个载能服务依原P0→P3顺序配给；同级直接电与已登记辅机保持物理份额，风机/冷水泵/热水泵支撑上限保留。固定规则不宣称重现原优化目标的最优值。
- UPS和冷储能只在实际关键服务缺口下支撑；充电仅用全部当前服务后的真实剩余，不用许可上限积分，也不以剩余stock/dt人为抹平功率。原ContinuousLossStore与close_storage_interval在耗尽/充满前切段，保留效率/连续站损。
- 新factory统一启用`strict_emergency_source_partition=True`。native仅加5行受flag限制的DG+UPS共同关键母线约束：`DG+UPS_discharge <= served eligible critical E`。新算术核同样先区分GT/grid普通源与DG/UPS关键源，station/一般负荷/充电不能从应急源池借供能。旧worktree、旧输出及flag缺省行为不改。

## 冷却动态与账本迁移

`tools/cooling_dynamics_20261104/`由根代理维护。本代理仅接API：native先解实际欲执行q，再按动态preview均值上限收缩converter许可并重解；U后只对锁存reference作preview，纯算术裁剪。所有试算不提交动态状态；只有最终实际区间调用commit_actual，传实际塔排热和冷机/蓄冷泵负荷的support_running旗，禁止无实际运行免费就绪。

新typed CoolingDynamics在继承reset完成原始初始化snapshot后安装，保留原初始化来源且不要求旧snapshot白名单接纳新类。新outer commit跳过旧AnnualSource native-witness导出，保留SF1/库存/锅炉/GT/资格/温度实际提交。native仍使用真实已接受SF2 gas primal校验；算术区间使用`arithmetic_running_fuel`与同一不可变FuelPath积分，明确新result owner，绝不伪造47/48维native证书。

算术段附`strict_unattended_forward`（锁存reference、源分账、具名残差和零调用声明）。数组residual顺序沿用旧physical_balance的`[electric kW,cooling kW,useful heat kW]`，另保留steam lb/h独立值。新runner应绑定新源清单并执行独立实际能量/库存审计，不能借旧source admission或把新账本称为旧native primal证明。

## 已实际执行的必要验证

- 原始算术reference/优先级/UPS与原始E/H/C核：先2 failed（模块未实现，log05），后2 passed（log06/07）。
- 加入真实hook不委托优化与优化入口body前哨兵：4 passed（log12）。
- 应急母线算术反例：先1 failed（log14），修后该文件5 passed（log15）。
- 原ContinuousLossStore+原close_storage_interval，耗尽和充满两项：2 passed（log16），η=.8且站损=.1；全额10kW先切真实事件再提交，库存闭合未超支。
- 新共同native拓扑小LP反例：先1 failed（log23，DG49.999995+UPS1.7631579错误共同支撑）；5行条件约束后native+算术两个聚焦回归2 passed（log24）。
- `git diff --check`最终退出0。没有跑无关全套测试；已过且未受修改影响的组件不再重复。

实际新env的合成时间锚点检查（**不是连续科研结果**）：

1. `synthetic_onset04`：原始初始化后明确把component时钟/站控提交游标设59；直接原core.advance推进59→60，再公开U step到61/62。zero optimizer attempts，最大E/H/C残差1.62e-13kW。这个极小残差是实得数，不是新增验收要求。
2. `synthetic_repair01`发现初始化蒸汽份额错误默认为0，导致ABS不恢复；失败机制和产物保留。修为从真实initial_action的蒸汽供应比例初始化，旧目标到达也更新份额。受影响接口仅重跑`synthetic_repair02`：119→120 ABS0；120→121均值119.5743/末态223.0065kW；121→122均值300.3504/末态367.2532kW；zero optimizer attempts，真实库存提交。
3. 合成锚点不能通过完整政策历史coverage校验；本代理没有伪造0→59/119服务历史。初始t0真实decision snapshot和physical_state已可严格clean；锚点后以真实物理core/step与状态日志检查执行接口。

探针输出根：`E:\hospital_ies_cooling_dynamics_20261104_run01\component_checks\`。原长smoke `forward_crossing04`按根代理授权受控终止，持久实际前缀到10分钟/40段；进程内最终状态未知，不能认作62分钟完成。`CONTROLLED_STOP.json`保留原因、有效前缀、SHA及未知范围。其余早期失败attempt及原始日志不删除。

## 已知限制与正式运行门槛

这是登记固定执行规则的研究代理，不能称作本院标定水力/设备瞬态、完整热质量能量模型或临床资格。上游native planner仍可提出后来被共同执行约束裁剪的命令，必须保留其投影/不可执行日志。完整19条均须在最终源绑定下从0连续执行；只有正式完整轨迹、共同初始状态与原始需求匹配、独立能量/库存/母线审计通过后，才可作新N/U/R比较。任何实际失败或未知尾段必须显式保留，不以曲线更差或更好作通过标准。
