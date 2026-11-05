# 医院 IES 最新有效研究结果（2026-10-28整理）

本目录整理已完成、已验收的 V30–V32 可读结果，没有重新运行模型、求解器或科学计算。每个复制文件保留版本内原相对路径；[CATALOG.json](CATALOG.json) 记录原始绝对路径、字节数、SHA-256、科学生产提交、样本范围和限制。完整原生证据经三份原验收 ZIP 作为 GitHub Release assets 提供，未把全部原生轨迹或原 PDF 放进 Git 文件树。

|版本|当前角色|最终科学记录|有效结果位置|
|---|---|---|---|
|V30 fix1 / C02|主 C/M 比较|八个背景×正常/扰动×C/M，共32条N02/D02|[主表](v30/engineering_report_tools/actual_final02/MAIN_RESULT_TABLE.csv)、[最终审计](v30/comparison_audit_fix1/final01/CAMPAIGN_AUDIT.json)、[工程解释](v30/release_narrative/ENGINEERING_INTERPRETATION_ZH.md)|
|V31 P01|两项简单准备措施收尾|b05/b07，共12条：8条冻结V30复用＋4条新增|[主表](v31/evaluation/final01/MAIN_RESULT_TABLE.csv)、[最终审计](v31/evaluation/final01/AUDIT.json)、[工程结论](v31/evaluation/FINAL_ENGINEERING_INTERPRETATION_ZH.md)|
|V32 P01|单个固定容量离线文献适配参照|b01，共6条：4条冻结V30复用＋2条新增|[主表](v32/evaluation/final02/MAIN_RESULT_TABLE.csv)、[最终审计](v32/evaluation/final02/AUDIT.json)、[工程报告](v32/ENGINEERING_REPORT_ZH.md)|

V31/V32 复用了 V30 记录，三版表格行数不能相加成独立实验样本数。各背景均为有限确定性窗口，不代表全年可靠率或临床资格。没有增加 MPC、未来在线故障脚本、硬件容量或执行快通道。

## V30：模型/评价可冻结，存在真实服务与费用取舍

同一 fix1 核心下最终32条N02/D02均完成180 min，最终审计记录的未知尾段、求解错误、策略回退、评价失败均为空。b01 为源小时1160、H1：C/M扰动实际费用分别为112.56322728559338和120.00232510658562 USD2001，P3电ENS分别为130.8172606290107和76.79572704443306 kWh_e。较低费用与较多一般区域电缺供同时出现，不能宣称全面优胜。

b02供热循环泵、b03共同冷却塔、b04冷水循环泵出现双方共同受限的服务中断。b07中锅炉早已就绪，C/M的关键热交付差异来自已到达许可与实际供汽差异，不能归因为M尚未预热。P0–P3电、热、冷ENS分别保留；微小ENS不改成零，实质中断为零也不代表原始ENS严格为零。

原先 b07 提案中的 GT_2 是待选择占位符；[ACTIVE_TARGET_LOCK.json](v30/ACTIVE_TARGET_LOCK.json) 记录按预登记规则在扰动前锁定的真实共同目标为 GT_3。不得据旧提案误写本轮故障对象。

## V31：简单许可措施有效，收益有明确适用背景

b05 的 M_cooling_permission 只打开健康制冷许可：正常/扰动 P3 冷 ENS 从原 M 的8.788847650063195/10.593407344124453 kWh_c降为0/0。正常工况本已有缺供，故不能把扰动轨迹全部损失都归于ER冲击。该措施相对M全程费用略降，扰动减自身正常的费用增量却略高，完整C仍有更低费用。

b07 的 M_steam_permission 提高已到达的健康锅炉供汽许可，未加速队列或新增预热。扰动P0/P1热ENS从原M的12.99693845167845/8.790220719501372 kWh_h降为0/0，P3冷ENS仍为29.902399823603297 kWh_c，费用从175.5826376577955增至177.757136529103 USD2001。完整C费用和供冷更好，但仍有P1热中断。两项措施分别针对已见背景，不能混称为一个已普遍验证的新控制器；登记范围内模型开发可以结束。

## V32：严格静态可行与动态实际执行存在差距

最终有效审计为 evaluation/final02。固定 b01_L1_grid、源小时1160、H1、180 min，外部参照使用 offline_known_window 信息，和 C/M 在线合法观测条件不同。Hirwa提供数学结构，Rosales提供设计/服务/经济/韧性边界核对；本轮只有一个 R32_fixed_capacity_offline 数值参照，并非两套原作者控制器、官方 REopt 运行或原论文数值精确复现。

六记录的P0/P1均无冻结判据下的实质中断，但微小电ENS照常保留。外部参照扰动费用88.1156142 USD2001，低于C/M；同时P3电/热/冷ENS分别为200.890510/21.8760935/15.4087008，各自kWh，末UPS约26.3334386 kWh_e，C/M约40 kWh_e。参照正常分支还存在15 min P3电实质中断。因此不能将低费用称为同服务、同末库存的节费优胜，亦不能以离线未来信息参照证明在线算法排名。

两支严格静态规划均为optimal且独立算术可行，静态各级服务ENS为零；经共同队列、启动、禁止倒送联锁、辅助耗电与实际分配后，实际服务存在缺口。固定45 min提前准备转换不是通信延迟或必然就绪保证。完整连续计划、储能功率端口和规划缺供均未逐值直接回放。完整初始化求解器调用数保持null，没有以core日志中的0冒充总调用0。

旧V30 R03/R_off的hour211是历史参考背景，和V32的b01 hour1160分开保存；禁止把旧R03结果改标签冒充本轮b01。

## 验证口径、失败证据与归档

本次整理只新增文件清单和逐字节校验，不重跑以下已有验证。V30收据记录默认1363项测试通过；V31记录60项真实相关源测试和17项synthetic评价工具测试；V32记录60项真实相关源测试、35项最终静态审查。V30的1363不能复制成V31/V32本轮重跑数。

[原始失败位置索引](ORIGINAL_FAILURE_LOCATIONS.json) 保留V30的 d01_b02_L1_heat_pump_disturbed_M（已知90 min，后90 min未知）和 r01_normal_R_off（KeyError: strategy_fallback，无可用最终轨迹，180 min未知）。原始文件不改动，小收据复制在v30/runs，完整失败证据由原ZIP携带。审计RED日志、V32 final01生命周期问题和最终final02修正、拒绝的打包尝试也保留；失败/未知均未补零或混入最终比较。

原始输入36文件集合SHA-256在三版均为 f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15。科学生产提交分别为V30 3252727135645037cedc57ebc188a48d2aa34ccb、V31 f009bdb66d5b9d53f1ff2331f2dc58404c2d056b、V32 575cb02b2ac3a1bbdce99a6f283891dc05b3d51a。后续文档提交不是上述轨迹的生产提交。

|Release asset|字节数|SHA-256|
|---|---:|---|
|Hospital_IES_V30_REVIEW_under100MB.zip|98896789|48a93fcd391f9cbf903d1e6185fc12916ac91a219c75e0bd2c87bf420b96e54d|
|Hospital_IES_V31_Simple_Preparation_Closeout_20261027.zip|24246025|a36105a82be48b6ec2815928472bd1242111370af0bb3d3e2591133375d848b3|
|Hospital_IES_V32_External_Reference_20261027.zip|35908886|f884e2b8495f424303bfd18f49a7c2836c77cfcc4e79e7a7fdf129425b123f01|

V30/V31最终归档验收均为passed；V32最终状态为complete，最终ZIP独立解压只读复核为passed。以上状态来自已存原始收据；本次当前校验重新读取三ZIP并比对大小/SHA，未把旧验收说成本次重新解压或科学重算。下载后的完整复核入口和依赖以各ZIP内README为准。
