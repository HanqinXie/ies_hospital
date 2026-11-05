# Hospital IES：医院综合能源系统

当前仓库整理到 **V30 fix1/C02 物理核心 + V31 P01 准备措施 + V32 P01 外部文献参照**。这里保存已验收的模型、原始输入及可读结果；完整原生轨迹、失败记录和独立复核入口见 [当前研究 Release](https://github.com/HanqinXie/ies_hospital/releases/tag/research-v32-20261028)。

本次2026-10-28更新清理了默认分支中的过时输出和旧过程材料，保留科学源码及测试依赖。没有重新调参、改变容量/负荷/保护阈值或重新生成论文结果。旧文件可从 [更新前 main](https://github.com/HanqinXie/ies_hospital/tree/archive-main-before-v32-20261028) 和科学版本标签恢复；本地旧科研数据未删除。

## 当前结果

|版本|用途|实际记录|入口|
|---|---|---|---|
|V30 fix1/C02|统一物理核心下的C/M比较|八背景，共32条N02/D02|[主表](results/v30/engineering_report_tools/actual_final02/MAIN_RESULT_TABLE.csv) · [工程解释](results/v30/release_narrative/ENGINEERING_INTERPRETATION_ZH.md)|
|V31 P01|两项简单准备措施分别对照|8条复用＋4条新增|[主表](results/v31/evaluation/final01/MAIN_RESULT_TABLE.csv) · [工程解释](results/v31/evaluation/FINAL_ENGINEERING_INTERPRETATION_ZH.md)|
|V32 P01|一个固定容量离线文献适配参照|4条复用＋2条新增|[工程报告](results/v32/ENGINEERING_REPORT_ZH.md) · [主表](results/v32/evaluation/final02/MAIN_RESULT_TABLE.csv)|

这些表有历史复用，行数不能相加为独立实验样本数。详见 [结果总览](results/README_RESULTS_ZH.md)、[文件来源与SHA目录](results/CATALOG.json)。

在V32的b01共180分钟正常/扰动比较中（停电分支在60–120分钟断网），六条记录的P0/P1服务均无既定判据下的实质中断；微小电ENS仍保留。外部参照扰动分支实际费用88.116 USD2001，低于C的112.563和M的120.002，但P3电ENS为200.891 kWh_e，高于C的130.817和M的76.796，末UPS约26.33 kWh_e，低于C/M约40。因此不能解释成同服务、同末库存的节费优胜。

V31提高健康制冷许可或锅炉供汽许可，在各自登记背景改善了相应服务，也存在费用和其他载能代价。V30中共同水泵/冷却塔瓶颈并不因换策略自动消失。完整数值、分载能ENS、实质中断和恢复时刻均在报告及原生证据中。

## 获取与快速核验

```powershell
git clone https://github.com/HanqinXie/ies_hospital.git
cd ies_hospital
python -B release/verify_repository.py
```

这个默认核验命令只读文件并核对 **873个冻结科学源码、36项输入、315件结果副本** 及发布元数据，不启动医院仿真、规划求解器或训练。科学生产提交仍分别为V30 `3252727`、V31 `f009bdb`、V32 `575cb02`；当前发布提交用于目录整理，不改写既有结果的producer。

## 模型与测试

既有公共 `reset/step`、命令队列、启动/最短停机、保护、供能分配、有限库存和评价实现保持原字节。建筑直接电/风机水泵等辅助需求、设备站用电、供汽与吸收制冷耦合分别记账。数据包括DOE/IDF、气象与8760小时负荷，36项源绑定见 [冻结文件](release/FROZEN_SOURCE_V32_P01.json)。

```powershell
python -m pip install -r requirements.txt
python -B -m pytest -q
python -B -m pytest -q tests/test_v31_preparation.py tests/test_v32_inputs.py tests/test_v32_planner.py tests/test_v32_execution.py
```

`pytest.ini`是冻结科学源码的一部分，保持旧选择集；第二条显式测试命令补验V31/V32新增模块。实际本次命令、退出码、缺失夹具修复和通过/跳过情况见 [发布验证记录](release/VALIDATION.json)。需要的少量旧 `outputs/` 文件作为测试夹具保留，见 [清理及依赖说明](docs/publication/CLEANUP_AND_DEPENDENCIES.md)，不充当当前研究结果。

现有物理执行使用Gurobi及既有回退记录规则，静态参照规划使用SciPy/HiGHS；许可证由运行者自行配置，不随仓库提供。实际验证环境见 [环境记录](release/ENVIRONMENT.json)。CUDA/训练依赖不是当前结果的必需条件。

## 复核原生证据

[Release](https://github.com/HanqinXie/ies_hospital/releases/tag/research-v32-20261028) 提供原验收V30、V31、V32三份ZIP及SHA256SUMS，每份均小于100,000,000字节。它们保留完整原生精度与失败证据，未将大批轨迹塞进Git工作树。文件清单见 [ASSETS.json](release/ASSETS.json)。

例如下载V32 ZIP后，解压至新的较短D/E路径，在解压包根运行：

```powershell
python -B review_packet.py --output E:/hospital_v32_saved_evidence_review01
```

输出目录须不存在且在解压包外。该入口恢复15,430个原生文件并复算六条既存结果，不自动启动新仿真。精确恢复原gzip字节要求已记录的zlib1.2.12；错误运行库会明确拒绝。两篇文献对应表、独立输入与原始/采纳计划、请求/到达/实际四层记录均在V32包及可读结果中。

重新执行医院场景须使用相应版本的显式入口和全新输出目录；先查看帮助：

```powershell
python -B tools/model_review_v30/validation.py --help
python -B tools/model_closeout_v31/validation.py --help
python -B tools/model_reference_v32/reference.py --help
```

不要用旧 `reference.py` 默认hour211背景替代V32的b01/hour1160。既有根 `run.py` 等历史兼容入口保留，但不作为上述最新比较的默认运行命令。

## 研究边界与版本恢复

不加入MPC，不改变C/M观测信息。V32是Hirwa方程结构与Rosales评价边界适配的一个参照，具有 `offline_known_window` 外生信息权限；它不是同信息在线算法排名、官方REopt/作者代码运行、费用下界或原论文精确复现。完整连续计划、储能功率端口和计划缺供未逐值直接回放。静态可行不保证真实队列、联锁和启动后满供。

现有证据不证明全年可靠率、最大保障边界或临床合格。原始ENS、费用USD2001、末库存、真实失败和未知值按各版本口径保留。

|恢复对象|不可变标签|
|---|---|
|清理前远程main/V20展示树|`archive-main-before-v32-20261028`|
|V30 fix1/C02科学生产源码|`science-v30-fix1-c02`|
|V31 P01科学生产源码|`science-v31-p01`|
|V32 P01科学生产源码|`science-v32-p01`|
|本次整理后模型与结果|`research-v32-20261028`|

保留Git历史，不执行force-push。发布范围、删去文件的原blob/SHA与恢复说明见 [发布计划](docs/publication/IMPLEMENTATION_PLAN.md) 和 [清理账本](docs/publication/CLEANUP_LEDGER.json)。
