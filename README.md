# 医院综合能源系统研究模型 V21

V21（`M21_sci_engineering`）研究医院关键服务在故障、保护传播许可、需求冲击及两者叠加时的接续、持续供能与恢复。共同模型纳入风机和水泵依赖、有限UPS与蓄冷、实际设备就绪与命令到达、有限柴油轴功等效预算；R、M、F、C、A五方案使用同一物理核心、合法观测和评价。没有MPC或未来故障/真实修复时刻信息。

**科学阶段及归档复现/默认测试已验收，最终普通Git提交/推送状态以Git和外置最终回执为准，不由本proof代签。** a5d3ed7/source4dfe共同源码已实际通过692项回归（另1项可选SQL检查跳过）及45条工程资格。主1920、Q1补充240、支持252、必要界112、硬件126及最终旧十二背景240均已实际完成并接受；原256中4条构造失败保持未知。硬件22图和最终12背景F1/F2均已实际生成、重开、检查格式并完成完整页面视觉审查，已发布数据/代码与正式图。科学计算、审查与正式图件已完成；冻结紧凑ZIP已通过成员核验与新解压，当前4dfe源实际完成365/365标量完整重放及新默认pytest（692 passed, 1 skipped in 218.45s (0:03:38)）。最终普通Git提交/推送状态以Git和外置最终回执为准，不由本proof代签；本次归档与运行证据见交付验证说明。

主19与Q1补充池中A/C的原服务平局各自仍成立。final12中，A/C关键material时间/次数和三阶段gap相同，但一个L1电网案例的P0/P1电ENS各增加4.063206741e−7 kWh；P3电material少60 min、少供电量少4.063166 kWh，伴随费用多1.21894084历史2001 USD和条件CO2多2.83484935 kg，初始与恢复窗不变。这是有限一般电服务取舍，不能写成全面关键优势或严格Pareto改善。R仍有额外关键中断，F/M并集平局也不等于所有ENS相同。保留电力不能跨过坏风机、水泵和共享散热端。详见[工程研究报告](docs/v21/SCI_ENGINEERING_REPORT.md)。

- [共同模型与研究假设](docs/v21/MODEL_SPECIFICATION.md)
- [情景、五方案与数据边界](docs/v21/SCENARIO_AND_POLICY_PROTOCOL.md)
- [参数与来源登记](docs/v21/PARAMETER_AND_SOURCE_REGISTER.csv)
- [完整结果与来源索引](docs/v21/results/README.md)
- [MATLAB可编辑图表](docs/v21/figures/)
- [安装、代表运行与复核](docs/v21/REPRODUCTION.md)
- [本次ZIP、当前源365与默认测试的外置验收](docs/v21/DELIVERY_VERIFICATION.md)

主1920、Q1补充240、支持252、硬件126、最终240这五个实际科学池共2778条轨迹；必要界112属于支持252子集，作为第六科学阶段单独审计但不再加112。原后续master256另有4条构造失败/UNKNOWN，不能当作已执行事故或补零。primary19、Q1_3、final12分别以19、3、12个源块为统计单位，不合并CI，也不是34家医院。

最终12背景[结果与审查](docs/v21/results/final12_actual01/README.md)、[正式F1/F2与本地重画配方](docs/v21/figures/final12_actual01/README.md)已发布；该配方的后续实际本地功能重绘现已通过，原镜像README保留复制时未运行身份；新输出未另重复四完整页视觉，详见复现说明。硬件已验证原生FIG的本地再导出已于2026-10-24 20:13:24 UTC取得真实exit0（246.4177489 s），输出为E:/hospital_ies_v21_20261023/reviews/hardware22_portable_runtime01/generated01；4+18 FIG及88新格式、源FIG至staging链和native snapshot数据exact检查通过，原scanner两组均exit0。字体验收为宋体/Times白名单和PDF嵌入，不证明逐对象FontName/Size前后exact；新44完整页视觉未重复，不能继承原正式22图的44页视觉接受。它是原生FIG再导出，不是CSV重算或科学重放。实际小证明已发布于docs/v21/figures/functional_entry_checks01/README.md。

本仓库README是研究总述。本次已验收紧凑包项目根README取自已冻结guarded复现overlay，SHA为`6e36c6bf125f23e6a0d8c008523aeb2f59120721f537c9fddb485b99115bd0fd`，不承诺与本文件字节相同；overlay及其4+7固定复现文件本次未改。

在新的D/E盘目录安装依赖并执行现有默认测试：

```powershell
python -m pip install -r docs/v21/reproduction/requirements.txt
python -m pytest -q
```

求解需要有效Gurobi许可，交付不含许可证。最终科学图使用MATLAB R2024b，包含FIG、SVG、PDF、600 dpi PNG、绘图代码和CSV/MAT。EnergyPlus独立校验及重新抽取原始SQL需要另行具备原始资料；已发布辅助电CSV可用于日常M21运行。临床房间回路、厂家暂态和环境合规仍是明确限制，不能由聚合服务模型代替。

<details>
<summary>V20历史说明与原结果（保留原身份）</summary>

# 医院综合能源系统研究模型 V20（历史）

**M20_load_accounting** 修正关键区域负荷分级的重复倍率，并在同一新口径下重跑四类情景比较。原始四份8760小时负荷表、IDF、天气及设备参数保留原字节；不增加设备能力、MPC、未来故障信息或放宽服务判据。

V19保存在 [v19-model-closeout](https://github.com/HanqinXie/ies_hospital/tree/v19-model-closeout) 标签，提交 `01baf68c3e12d0336a5d9d54026ca479a7023b6d`。V19结果不能改标签作为V20结果。

## 字段口径

| 原CSV字段 | 已确认口径 | V20使用规则 |
|---|---|---|
| interior_equipment_kW | Zone电表已含倍率 | 不再乘倍率 |
| interior_lighting_kW | Zone电表已含倍率 | 不再乘倍率 |
| associated_reheat_coil_heating_kW | 14个实际再热盘管，属于建筑62盘管的互不重叠子集 | 不再乘区域倍率 |
| zone_sensible_cooling_kW | 代表单区显热冷量 | 汇总乘一次；单区温度使用原值和单区热容 |

全年基础分组算术中，约668.520 MWh电从P0/P1回到P3，约490.685 MWh热回到P3；建筑总需求不变。解除旧热压缩后P0热在4695小时上升。冷代理仍有137小时超过建筑末端总冷量，保留压缩前逐小时诊断。

证据见 [数据修正报告](docs/v20/DATA_CORRECTION_REPORT.md)、[字段合同](docs/v20/FIELD_CONTRACT.md)、[模型说明](docs/v20/MODEL_SPECIFICATION.md)。基础分组算术与实际调度后果分别报告。

## 情景与策略

L1为单设备故障；L2为匹配故障下允许继发传播；L3为无设备故障的需求冲击；L4为故障与冲击叠加。传播许可不等于实际发生了继发跳机。

E、K1、C19及C18兼容偏好均登记为新M20身份，偏好参数与V19对应策略逐值一致。它们共用新负荷、设备、队列、接点和评价口径；本次不搜索新策略来修饰结果。

固定比较406条：主80×3、长8×3、正常4×3、7条新增C18兼容对照、原八背景32×3、传播18、OPEN1六条和接点0.25分钟三条。原八背景仅称已见固定背景复核；最终12背景继续封存。正常窗拆为12个单策略进程，全局串行。

## 已核验的工程结果

148批、406条策略轨迹均已实际完成并严格合格；最终`FINAL01`中未开始、未知完整尾段和失格终态均为0。默认核心门槛实际463项通过；发布层159项及7个子测试、正常分析188项均通过。推送前独立候选的600分钟E调度与94项参考标量一致。推送后新克隆的真实验收时间晚于本提交，证据随最终交付包另附，不用候选结果代替。

修正负荷分组后，策略收益没有全部保留。主80中K1有8个规格出现新的关键载能实质失供；C19自身P0分钟不变，旧有部分对照优势成为平局。C19在主窗的关键分钟不高于E/K1，但共享塔故障仍造成最长60分钟关键缺冷，且部分P2/P3服务变差。长窗C19各例仍有初始15分钟关键电冷失供；E/K1各6/8例DG预算耗尽，C19为0/8，不能由此宣称所有服务或场景均获保障。固定八背景的共享塔坏例更有180分钟关键缺冷。

费用下降也不保证减碳：主80的600分钟窗口合计，C19相对E能源费少154.25 USD，条件运行CO₂却多19568.53 kg；更多天然气替代购电是主要原因。不同长度窗口分别比较。完整请求、交付、主动断开、连接少供、设备事件、坏结果及条件排放口径见 [工程结果报告](docs/v20/ENGINEERING_RESULTS_REPORT.md)；这些设计场景计数不是实际事故概率、年度收益或临床安全证明。

四个正常窗口的12条轨迹均无P0/P1实质中断，但有微小ENS；DG电功和轴功预算扣减均为0。72小时窗口中C19比E费用高约23.17 USD、条件CO₂高338.69 kg；其余三个窗口C19费用略低而条件CO₂仍较高，不能把无故障运行写成C19全面获胜。逐窗结果见 [正常窗口分析](docs/v20/NORMAL_WINDOW_ANALYSIS.md)。

## 运行

在D/E盘新目录克隆仓库，准备具备适用Gurobi许可的Python环境：

```powershell
git -c core.longpaths=true clone https://github.com/HanqinXie/ies_hospital.git D:\ies_v20
Set-Location D:\ies_v20
git config --local core.longpaths true
python -m pip install -r requirements-v20.txt
python scripts/v20_repository.py verify
python -m pytest -q
python publication/reproduce_v20.py --name clone_check01
```

最后一条实际重跑主面板第一个规格的E策略完整600分钟，保存新目录、命令、退出码和比较证明。每次使用新名称。仓库不提供求解器许可。

克隆目标须为新目录。部分嵌套证据路径较长；上述Git设置只影响该次命令和新仓库，不修改系统全局长路径或Git全局配置。

`pytest.ini` 明确V20默认核心门槛，含适用原物理/数值测试和新增回归；不等于全部历史研究测试。V19专属冻结检查在V19标签下执行。详情见 [复现说明](docs/v20/REPRODUCTION.md)。

M20调度不要求第三方论文及来源PDF/XLSX；原审查SQL、完整EnergyPlus产物和大规模raw留在本地，仓库保存来源SHA与紧凑证据。复核原SQL需要相应外部材料，不能把紧凑字段审计称作重新运行EnergyPlus。

实际状态、全部分母、失败、回退、未知和未开始记录以 `outputs/v20_load_accounting_20261019_run01/` 为准。工程后果见 [工程结果报告](docs/v20/ENGINEERING_RESULTS_REPORT.md)，来源与边界见 [假设登记](docs/v20/ASSUMPTIONS_AND_LIMITATIONS.md)。

本系统是DOE医院原型与历史能源站资料构成的聚合研究模型；P0–P3是功能区代理优先级，缺少真实关键回路、厂家暂态、配电潮流和临床验证。DG轴功预算不是柴油体积，条件运行CO₂不是完整生命周期CO₂e；温度未越限不能抵销实质缺冷。

当前入口位于 `tools/load_accounting/` 与 `scripts/v20_*.py`。旧命名模块和小型fixture中仍有继承实现与回归依赖，保留原路径并登记用途；旧训练/审计入口不作为本轮运行命令。

</details>
