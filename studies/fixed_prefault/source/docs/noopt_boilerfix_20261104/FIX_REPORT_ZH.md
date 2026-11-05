# 隔离锅炉实际用汽闭环修复

工作区：`D:\hospital_ies_noopt_boilerfix_20261104`；起点`079111ed4b84fbd11633ccd2ff6b61208f0f09a1`；分支`research/noopt-boiler-useful-20261104`。

只修改`tools/noopt_unattended_20261104/forward.py`内`evaluate_forward`函数体。签名、装饰器、函数外AST、CONTRACT、cooling_vector、_take不变；共同runtime/env/动态/physical_balance/runner/inputs/tests目录均未修改。正在运行的原D工作区及旧E输出只读。

## 根因与结果行为

T06_U_attempt01的60→61分钟：锅炉2418.38812756492lb/h，实际建筑供热与ABS用汽合计326.5646119261892lb/h，HRSG0，ABS0。差值2091.823515638731lb/h被错误放入unused steam。原因是锅炉按缩放前冷量与原热请求确定，后续塔/泵/电力/冷需求裁剪后未回闭锅炉。

现把同一被动实际流核放入函数内`settle(boiler_cap)`，每次保持原冷量运行参考，重算锅炉与站用电、蒸汽、塔/电/冷需求限幅、建筑服务和库存端口。若最终锅炉超过实际有用目的，则在0至当前目标之间对被动压力/用汽饱和边界二分，仅接受`boiler <= 实际供热用汽 + 实际ABS用汽`的端点。不比较ENS或费用，不增加锅炉弃汽能力，不更改运行参考/许可/容量/优先级。

0是原模型合法OFF端点；原M23没有已登记正的OEM最小蒸汽负荷，因此没有发明非零最小值。所有正出力仍支付原固定+随蒸汽变化的站用电，仍受station_ac、boiler_ready、命令上限、设备可用性与供电预算限制。根定位区间宽度1e-5lb/h仅为工程求根精度；原1e-7lb/h用汽不等式与能量账本接受边界没有放宽。库存只预览端口，不在试算中提交。

闭环诊断放进既有`m22_mode_selection.boiler_pressure_regulation`，由原提交hook保留；记录初始/最终锅炉、实际useful、迭代次数和无优化声明。锅炉燃料、实际辅电、GRID/DG取用及费用由最终实际流重新计算，不能仅修表中steam字段。

## 实际验证

唯一必要测试文件放`docs/noopt_boilerfix_20261104/test_boiler_pressure.py`，避免为测试文件新增改变运行source_files集合。显式命令：

`python -B -m pytest -q docs/noopt_boilerfix_20261104/test_boiler_pressure.py --noconftest -p no:cacheprovider`

- RED：2 failed、1 passed，退出1，`logs/01_red.txt`。T06形态塔全停和无冷需求都抓到锅炉2418.3881 > actual useful326.5646；限额运行近邻原已通过。
- GREEN：3 passed，退出0，0.76s，`logs/02_green.txt`。
- 同3项检查原锅炉许可上限、无新增备用冷机、辅电与燃料费用回算、电/热/冷/蒸汽账本、不修改输入，以及优化器函数体前哨兵未触发。没有reset、仿真、扩样、无关全套测试。
- AST同一性检查与最终`git diff --check`均退出0。具体源SHA和AST结论见`FUNCTION_SCOPE_CHECK.json`。

`--noconftest`用于显式纯组件文件；继承根conftest绑定旧工作区路径，不能借它宣称新分支全套通过。

## N/R复用边界

生产调用闭包已只读核查：evaluate_forward唯一生产调用位于runtime._forward；其7个入口均在noopt为真后才可达，noopt只对unattended且time>=60-EPS成立。N/R不会调用变化函数；相关runtime、控制、动态和共同物理源文件未改。

这支持由独立certificate工具证明N/R闭包同一性，并保留12个原N/R producer的原始source pins。**本修复收据不是已签发的跨epoch复用证书，不能替代来源guard。** 必须由严格source-family检查确认完整source_files除forward.py外逐SHA相同、该文件函数外AST相同、原CASE/variant/config/input一致，且原N/R数据独立审计通过。所有7个U由根代理在新source pin下从0完整重跑，旧U不得挑选复用。
