# V30 事实驱动工程报告工具

科学 producer、原生轨迹、旧报告与旧源码均保持原样。本目录只生成中文工程验收说明和机器可读表格，不调用环境、优化器或仿真，不修改 D 盘源码，不制作论文或图表。

## 最终建议采用版本

`actual_final02` 是在完整真实 final32 审计下生成的 v2 展示版；`actual_final01` 及其原始生成器保留。两个版本的 `MAIN_RESULT_TABLE.csv` 字节完全相同，SHA256 均为 `684448deb817b9478cffdc9bba9f0083a414c9dbfe1cf9ab46320f5192201ffe`。

v2 只修正浮点舍入级的改善/变差标签：当实质中断与恢复记录相同，且 `abs(C-M) <= 64*max(ulp(C),ulp(M))` 时，显示 `roundoff_only_same_material_outcome`。ENS、绝对差、物理判据、模型参数、测试容差、CSV和实际 producer 均不改变。较大真实差异保留原方向，无实质中断的微小差异不称算法优势。

根任务会另行突出解释：b01 的 C 费用较低但 P3 电 ENS 更高，不能称净节费或全面胜出；b07 的共同中断 union 时长相同，但逐热服务结果明显不同；b02/b03/b04 的计算完成与关键服务保障结果应分开判断。这些工程解释不改变表格或科学证据。

## 初始事实报告 API

```powershell
python -B E:/hospital_ies_v30_20261027/engineering_report_tools/build_engineering_report.py --root E:/hospital_ies_v30_20261027 --audit comparison_audit_fix1/final01/CAMPAIGN_AUDIT.json --output E:/hospital_ies_v30_20261027/engineering_report_tools/FRESH_FACT_REPORT
```

输出目录必须新建。生成器要求 final_matrix_complete_and_clean 和 patch 一致性为 true、32 个不同的实际 N02/D02 case 均达到180 min、同一producer/source/patch、所有原生审计检查通过，以及真实修复后默认测试 exit0。还核对原生 service/终态/库存及参考审计manifest；未通过时不创建报告目录，不预写“可冻结”。

真实 `normal_final02` 的 partial 审计拒绝证明保留在 `ACTUAL_PARTIAL_REFUSAL.log` 和命令 JSON：实际退出1、输出目录没有创建。最终真实 `final01` 审计生成 `actual_final01` 的命令退出0；日志和原始输出 SHA 保留。

三个主要产物：

- `ENGINEERING_REPORT_ZH.md`：先医院服务接续与资源约束，再列G1五修、真实最小停机反例、配置/信息边界、来源/测试、两篇文献与有限R_off层级及交付边界。
- `MAIN_RESULT_TABLE.csv`：32行、221列。逐P0—P3、逐e/h/c保留请求、交付、切除、连接后缺供、ENS、中断与恢复；包含本方法实际normal基线与扰动差、纯费用、条件CO2、库存、DG轴功预算与真实终态队列。float使用原始repr，未知为`null`，无百分比或跨载能ENS总分。
- `COMPLETION_MATRIX.json`：实际门槛、source/input计数、测试日志、文件SHA、文献/参考层级及尚待根任务封装验收的事项。

CSV的e/h/c各自对应kWh_e/kWh_h/kWh_c，不能直接相加。恢复`null`与not_applicable不填0。条件CO2只有共同非空因子时才计算差值，实际柴油化学能和油量仍未知。真实终态与最后决策时刻分列。

## v2 展示处理 API

```powershell
python -B E:/hospital_ies_v30_20261027/engineering_report_tools/report_presentation_v2.py --root E:/hospital_ies_v30_20261027 --audit comparison_audit_fix1/final01/CAMPAIGN_AUDIT.json --base-report engineering_report_tools/actual_final01 --base-receipt engineering_report_tools/ACTUAL_FINAL01.stdout.log --output engineering_report_tools/FRESH_PRESENTATION_V2
```

v2先核对原生成器冻结SHA、真实审计门槛、原报告成功receipt、全部原产物SHA及支持证据；检查实际原CSV中的384个ENS字符串与审计完全相符，且全部实质中断时长已知。再只替换比较段的标签，逐字节复制原CSV。新matrix明确记录原生成器、新展示处理器、原报告SHA、ULP规则及重标单元，科学producer不变。

实际 `actual_final02` 命令退出0，11个展示标签改变。主CSV最大单格为111字节，真实队列/等待字段没有复制MB诊断；完整terminal与诊断继续保留在原生RESULT中。

## 测试与冻结

原生成器12项测试通过；严格门槛、原生一致性与四项只读审查保护均保留真实RED/GREEN。v2的6项测试先用实际b02单ULP差复现旧误标，再GREEN；另覆盖实际相同22.409228565359957、相邻浮点的明确人工边界、65 ULP边界、较大真实差异、不同中断结果及未知状态。没有重跑模型或默认测试。

`ACTUAL_REPORT_QA.json`核对了32行、221列、384个ENS与32个纯费的精确repr、全部真实终态180 min、UTF-8及实际source/input/test计数。CODE_REVIEW和REVIEW_RESOLUTION保留审查边界；原生成器的通用缺失materiality文字辅助路径没有宣称对任意未来输入完全正确，实际32条相关时长均已知，v2会拒绝未知时长。

源码、测试、日志和真实输出的生成receipt见 `FROZEN_REPORT_TOOLS.json`。所有科学数字来自实际final native/audit，默认通过数来自真实stdout；源码/输入计数来自C02冻结文件，测试COMMAND没有被伪造加入git/source字段。最终ZIP、全局尝试索引、源diff、独立原字节恢复与大小验收由根任务另行完成。
