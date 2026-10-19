# V20 运行 CO2 事后核算方法

方法继承自 [V19 标签排放说明](https://github.com/HanqinXie/ies_hospital/blob/v19-model-closeout/docs/v19/EMISSIONS_METHOD.md)。原方法及四个因子情景于 2026-10-18、收到 V19 实际能源行之前登记；V20 保留全部核算参数、单位、四情景与假设，不重新按策略结果选择因子。这里只评价 M20 实际运行能源对应的条件 CO2，不新增排放调度目标，也不将旧版结果改名为 V20。

本次使用 [V20 EMISSIONS_FACTOR_REGISTRY.json](../../outputs/v20_load_accounting_20261019_run01/references/EMISSIONS_FACTOR_REGISTRY.json)，SHA256 为 `67ad5404000040d9665f016ee4b9c540783b26a9ad8038550f148881cefc6ab0`。其原 `hospital_ies.v19.emissions.factor_registry.v1` schema、注册时间及 V19-E01–E05 编号作为来源身份保留；它们不表示正在使用 M19 物理结果。当前运行身份为 `M20_load_accounting`，结果绑定与发布边界见 [ENGINEERING_RESULTS_REPORT.md](ENGINEERING_RESULTS_REPORT.md) 和 [PUBLICATION_SCOPE.md](PUBLICATION_SCOPE.md)。

## 1. 系统、地区和报告边界

研究输入是 DOE San Diego 建筑需求与 NMCSD 历史设备资料启发的合成医院 IES，不是真实 NMCSD 运行盘查。既有 `inputs/energyplus/SanDiego_BrownField_TMY3.epw` 首行给出 Brown Field Muni AP、CA、USA、站号 722904、北纬 32.58、经度 -116.98；`inputs/reference/provenance.json` 明确 TMY3 的日历标签不是观测天气年。因此继续选择 CAMX（WECC California）作为公开声明的地区代理；2023 是排放因子数据年，不能写成需求的实测年份，也不与历史 2001 年价格冒充同年资料。

报告量是现场天然气/柴油燃烧与购电对应的发电 CO2，单位 kg CO2。现场燃烧属于 Scope 1 形式，购电属于地区平均 Scope 2 形式；组织盘查范围的区别见 [EPA Scopes 1 & 2 指南](https://www.epa.gov/climateleadership/scopes-1-and-2-emissions-inventorying-and-guidance)。不计 CH4/N2O、燃料上游、输配电损耗、设备制造、制冷剂泄漏或生命周期，故不称完整 GHG/CO2e 或实际设施认证盘查。少购电或费用降低不能单独证明净减碳。

## 2. 机构来源及继承版次

以下来源版次与取值继承原 2026-10-18 查核；该历史查核包括相关 PDF 表头和单位，不宣称 V20 又下载或重新渲染过这些原件。完整 [V19 来源清单](https://github.com/HanqinXie/ies_hospital/blob/v19-model-closeout/outputs/v19_model_closeout_20261018_run01/references/SOURCE_MANIFEST.json) 由历史标签追溯。本次 registry 在各 case 内保留采用来源的 URL、版次、定位、snapshot SHA256 和原 ISO UTC 访问时刻；时刻是当时成功取回后的记录，不是本版运行时间。第三方原 PDF 留在本地完整资料工程或从机构来源取得，不作为本精简发布树随附文件。

| 项目 | 采用值和定位 | 原始来源/适用范围 |
|---|---|---|
| 主购电因子 | 428.464 lb CO2/MWh，CAMX annual total-output；0.19434800121968002 kg/kWh | [EPA eGRID2023 Summary Data](https://www.epa.gov/egrid/summary-data)，Revision 2，2025-06-12；[对应 PDF](https://www.epa.gov/system/files/documents/2025-06/summary_tables_rev2.pdf) p2 Table 1 四舍五入为 428.5。原登记使用网页的三位小数值。 |
| 购电版次对照 | 436.7 lb CO2/MWh | [EPA GHG Emission Factors Hub 2025](https://www.epa.gov/system/files/documents/2025-01/ghg-emission-factors-hub-2025.pdf)，2025-01-15，p3 Table 6；仅作旧版次敏感性，不与 Revision 2 混称同一公布值。 |
| 天然气 | 53.06 kg CO2/MMBtu HHV | 同上 EPA Hub 2025，p1 Table 1 Natural Gas，HHV 燃烧因子。 |
| 柴油燃烧 | 73.96 kg CO2/MMBtu HHV；No.2 馏分油 0.138 MMBtu HHV/US gal | 同上 p1 Table 1 Distillate Fuel Oil No.2；统一使用热值和热量因子，不再混入表中四舍五入的 10.21 kg/gal。 |
| 柴油轴功到油耗 | 60 Hz prime：608 kWm/154 L/h、456/118、304/84、152/50 | [Cummins VTA28-G5](https://mart.cummins.com/imagelibrary/data/assetfiles/0070377.pdf)，PD00000747 (09/21)，p2 右下表；kWm 是 gross engine 输出。它是代表设备资料，不是医院或本研究 900 kW 聚合机的识别结果。 |

[EPA 修订说明](https://www.epa.gov/egrid/known-issues-data-notes-and-revisions) 解释 Revision 2 对负发电量处理的调整；原核算据此将 Hub 旧表与后续 eGRID 版次分列。这里用总输出因子做平均购电账，不用 non-baseload 因子估计实际购电，不称小时边际减排。

原登记另保留 [EIA CO2 系数表](https://www.eia.gov/environment/emissions/co2_vol_mass.php) 的 2024-09-18 发布/2022 数据对照：天然气 52.91、馏分油 74.14 kg CO2/MMBtu、柴油 10.19 kg/US gal；[EIA 方法](https://www.eia.gov/environment/emissions/includes/methodology.php) 的柴油热值为 5.770 MMBtu/barrel，每 barrel 42 US gal。这些仍只作交叉来源记录，主核算不拼接 EIA 因子与 EPA 热值。

## 3. 单位和核算公式

MMBtu 是一百万 Btu，不是千 Btu。购电转换用 1 lb = 0.45359237 kg、1 MWh = 1000 kWh；油量用 US liquid gallon，1 US gal = 3.785411784 L。单位定义可复核 [NIST SI 转换表](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b8) 与 [NIST Handbook 130](https://nvlpubs.nist.gov/nistpubs/Legacy/hb/nisthandbook130-2009.pdf)。多位小数只为可重复计算，不表示原因子的测量精度。

每个同策略、同规格、同窗口的实际能源行独立核算：

```text
C_grid [kg] = E_grid [kWh] * 428.464 [lb/MWh] * 0.45359237 [kg/lb] / 1000 [kWh/MWh]
C_gas [kg] = (Q_GT + Q_boiler) [MMBtu_HHV] * 53.06 [kg/MMBtu_HHV]
C_grid_plus_gas = C_grid + C_gas

V_DG_conditional [L] = W_shaft_debit [kWh_m] * s [L/kWh_m]
Q_DG_conditional [MMBtu_HHV] = V_DG_conditional / 3.785411784 [L/USgal] * 0.138 [MMBtu_HHV/USgal]
C_DG_conditional [kg] = Q_DG_conditional * 73.96 [kg/MMBtu_HHV]
C_total_conditional = C_grid_plus_gas + C_DG_conditional
```

GT 已有热耗为 HHV。原锅炉效率 0.80 的来源未注明 HHV/LHV；V20 继承对**已记录**锅炉燃气数使用同 HHV 的核算假设，不改效率、蒸汽焓差或运行结果。若真实口径为 LHV，需燃气成分或 HHV/LHV 比值转换；该比例未知，不编造固定修正，不将锅炉项称为实测精确排放。HRSG 是同一 GT 已燃气的余热，不再加一次燃料/CO2；冷机、泵、塔扇对应能耗已在源端账内，不重复计入。

## 4. DG 账和不可识别部分

输入字段为 `DG_fuel_budget_debit_shaft_equivalent_kWh`，来自实际机械功预算扣减，不是 L 油量或化学 kWh。继承 receipt 的身份为：

```text
W_m = W_e + W_loss + Delta_E_rotor
fuel_budget_debit_shaft_equivalent_kWh = W_m
```

来源为 `tools/operating_response/diesel.py::spin_up/_receipt` 与 `tools/essential_fixes/execution.py` 的实际扣账，`tools/execution_recovery/review_saved.py` 检查相同身份。启动机械功已含转动能增加和声明的启动轴损，READY/OPEN 的声明空转损耗也已入机械功；采用 debit 后不能再加一遍启动、损耗或正转动能。燃料耗尽后，转子耗散可有损耗和负 Delta_E，但机械功和新燃料扣账为零，不重复收取燃烧排放。

constant-s 情景只将**模型已记录机械功**换成代表性化学燃料。主值 `s=118/456=0.25877192982456143 L/kWh_m` 来自 75% 参考点，不代表实际机组始终在 75% 负荷，也不是拟合结果。原厂轴端辅助设备与模型损耗的对应、发动机数、真实低载/空载截距、启动富油、预热及外部启动电源均未识别；将常数用于启动/低于表支持域的功率是未校准外推。

- `grid_plus_gas_CO2_kg` 单列可核算部分，仍受地区/HHV 假设约束。
- `DG_CO2_kg_conditional`、`total_CO2_kg_conditional` 始终为条件估计，`DG_exact_CO2_unknown=True`，不称实际柴油量或实测总排放。
- 因子情景跨度不是严格上下界、统计置信区间或设备保证。实际空转/启动化学燃料仍 unknown，不以零补齐。
- 窗口切中 DG receipt 内部而无法精确重建时，继承核算将轴功置 unknown；`partial_DG_receipt_not_interpolated` 时仅保留可计算 grid/gas，DG 与总量为空。

## 5. 原值保留的四情景和假设

本次 registry 保留以下四情景，不做全组合扫描，不根据 C19/E/K1 排名选因子。主 case 为 `CAMX2023r2_DG75pct`，其余仅作登记敏感性。

| case_id | 电网 CO2 lb/MWh | s L/kWh_m | 含义 |
|---|---:|---:|---|
| CAMX2023r2_DG75pct | 428.464 | 118/456 | 主条件估计 |
| CAMX2023r2_DG100pct | 428.464 | 154/608 | 原厂 100% 点单位油耗情景 |
| CAMX2023r2_DG25pct | 428.464 | 50/152 | 原厂 25% 点单位油耗情景 |
| CAMX2023_originalHub_DG75pct | 436.7 | 118/456 | 旧电网因子版次敏感性 |

| 继承登记号 | 假设/限制 | 对结果的影响 |
|---|---|---|
| V19-E01 | San Diego 合成研究采用 CAMX 2023 年均购电代理，无实测运行年/供电合同匹配 | 非小时边际排放或真实设施盘查 |
| V19-E02 | 原锅炉 MMBtu 按 HHV，原效率热值基准未明 | 该假设下的锅炉 CO2，实际修正未知 |
| V19-E03 | DG 用 EPA No.2 馏分油燃烧/HHV 因子 | 未识别实际燃料成分 |
| V19-E04 | 代表 gross-engine 单位油耗映射聚合机械功，全部策略共享，含启动/空转低载外推 | 条件估计、化学燃料未知、非严格范围 |
| V19-E05 | 只计运行 CO2，不含上游、CH4/N2O、T&D 或设备生命周期 | 非完整 CO2e/生命周期减碳 |

## 6. M20 接口、验证和实际结果边界

继承算式接口为 [emissions.py](../../tools/model_closeout/emissions.py) 的 `evaluate_emissions(resource_rows, factor_case)`。输入实际汇总能源行及 registry 的一个 case，输出保留资源行并增加各组件、条件总量与 canonical JSON 因子 SHA256。函数不读 raw、不运行求解器、不改变控制状态。

M20 [analysis.py](../../tools/load_accounting/analysis.py) 只接受明确的 `M20_load_accounting` 物理/分析身份，在调用者提供的实际内存 episode 或已保存紧凑分析上复用算式。逐批 `analysis/V20_RESOURCES.csv`、`V20_EMISSIONS.csv` 与 `V20_EPISODE_AUDIT.json` 的 SHA、registry 路径/SHA 和兼容导出收据进入终态 `analysis_receipt`。旧兼容文件名不表示读取旧模型结果。未完成 episode 的已观察前缀需明确标识，不能冒充完整窗排放。

[scripts/v20_report.py](../../scripts/v20_report.py) 对当前 plan/manifest、终态、实际 ATTEMPTS、RESULT SHA、模型身份及 episode audit 判断执行资格，并按匹配窗口评价四情景。需保留资源来源、M20 模型/策略/配置/窗口、registry/case SHA 及导出 SHA；未合格、缺失或部分覆盖与完整比较分母分开。跨版本只能分别使用绑定的 V19 与 M20 实际能源行，不能用 V19 源能量替代新调度。

无效数值、应为非负的购电/燃气/轴功预算等输入出现负值、天然气总量不一致、可检查的机械功/预算或转子身份不一致均抛错；`Delta_E_rotor` 允许为负。缺失传播为 `None`，不制造零排放。继承算式的账内身份检查容限仍为 `max(1e-6 kWh, 1e-9*scale)`，只检查导出浮点账自洽，不改物理/失效阈值。M20 导出/汇总遇错时保留 `emissions_error` 或对应输入错误状态、条件总量为空，不把异常吞成零。只有电功或旧模糊字段时不推断化学燃料。

本次源码、输入和测试由 [MODEL_FREEZE_ACCOUNTING01.json](../../outputs/v20_load_accounting_20261019_run01/MODEL_FREEZE_ACCOUNTING01.json) 绑定；实际默认 [core_gate02.log](../../outputs/v20_load_accounting_20261019_run01/logs/core_gate02.log) 为 463 passed、exit 0。这里不复制旧 V19 排放单元测试数，也不将测试通过解释为 406 条调度完成或减排成功。

正式排放结论以 [ENGINEERING_RESULTS_REPORT.md](ENGINEERING_RESULTS_REPORT.md) 中同版、具备实际执行资格的结果为准，尚未齐备部分保持 pending。主窗、长窗与正常完整窗分列，不将不同长度总量相加排名，不年化代表窗口。独立目录及后续远程 clone 另按 [REPRODUCTION.md](REPRODUCTION.md) 的实际证据报告，未执行前不写通过。正文用语为“在登记排放因子及柴油油耗情景下的事后运行 CO2 评价”，不称已实现减碳调度或实测 CO2e。
