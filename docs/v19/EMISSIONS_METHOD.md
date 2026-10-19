# V19 运行 CO2 事后核算方法

登记日期：2026-10-18；方法及四个因子情景在收到实际 V19 能源行之前登记。这里只计算排放评价，不增加或宣称运行了 CO2 调度目标。原物理模型、规划器、历史费用和 V18/SOURCE05 结果均不因本核算改变。

## 1. 系统、地区和报告边界

研究输入是 DOE San Diego 建筑需求与 NMCSD 历史设备资料启发的合成医院 IES，不是真实 NMCSD 运行盘查。封存的 `inputs/energyplus/SanDiego_BrownField_TMY3.epw` 首行给出 Brown Field Muni AP、CA、USA、站号 722904、北纬 32.58、经度 -116.98；`inputs/reference/provenance.json` 明确 TMY3 的日历标签不是观测天气年。因此选择 CAMX（WECC California）作为公开、声明的地区代理；2023 是排放因子数据年，不能写成仿真需求的实测年份，也不与历史 2001 年价格冒充同年资料。

报告量是现场天然气/柴油燃烧与购电对应的发电 CO2，单位 kg CO2。现场燃烧属于 Scope 1 形式，购电属于地区平均 Scope 2 形式；组织盘查范围的区别见 [EPA Scopes 1 & 2 指南](https://www.epa.gov/climateleadership/scopes-1-and-2-emissions-inventorying-and-guidance)。不计 CH4/N2O、燃料上游、输配电损耗、设备制造、制冷剂泄漏或生命周期，故不称完整 GHG/CO2e 或实际设施认证盘查。光有少购电或费用降低不能证明净减碳。

## 2. 原始机构来源及版次

所有在线来源均于 2026-10-18 查核。逐文件 URL、字节数、SHA256 和 ISO UTC 访问时刻保存在 `outputs/v19_model_closeout_20261018_run01/references/SOURCE_MANIFEST.json`；时刻定义为成功取回后立即写入快照的 UTC 时间。三份 PDF 的相关页面已渲染查看，用于核对表头和单位。

| 项目 | 采用值和定位 | 原始来源/适用范围 |
|---|---|---|
| 主购电因子 | 428.464 lb CO2/MWh，CAMX annual total-output；折合 0.19434800121968002 kg/kWh | [EPA eGRID2023 Summary Data](https://www.epa.gov/egrid/summary-data)，Revision 2，2025-06-12；[对应 PDF](https://www.epa.gov/system/files/documents/2025-06/summary_tables_rev2.pdf) p2 Table 1 四舍五入为 428.5。使用网页的三位小数值。 |
| 购电版次对照 | 436.7 lb CO2/MWh | [EPA GHG Emission Factors Hub 2025](https://www.epa.gov/system/files/documents/2025-01/ghg-emission-factors-hub-2025.pdf)，2025-01-15，p3 Table 6；仅作为旧公布版次敏感性，不与 Revision 2 混称最新值。 |
| 天然气 | 53.06 kg CO2/MMBtu HHV | 同上 EPA Hub 2025，p1 Table 1 Natural Gas。表注明确为 HHV 燃烧因子。 |
| 柴油燃烧 | 73.96 kg CO2/MMBtu HHV；No.2 馏分油 0.138 MMBtu HHV/US gal | 同上 EPA Hub 2025，p1 Table 1 Distillate Fuel Oil No.2。内部统一使用热值和热量因子，不把表中四舍五入的 10.21 kg/gal 再混入。 |
| 柴油轴功到油耗 | 60 Hz prime：608 kWm/154 L/h、456/118、304/84、152/50 | [Cummins VTA28-G5](https://mart.cummins.com/imagelibrary/data/assetfiles/0070377.pdf)，PD00000747 (09/21)，p2 右下表；kWm 是 gross engine 输出。它是代表设备资料，不是医院或本研究 900 kW 聚合机的识别结果。 |

[EPA 修订说明](https://www.epa.gov/egrid/known-issues-data-notes-and-revisions) 说明 Revision 2 调整了负发电量的处理，从而可改变排放率；因此 Hub 旧表与当前 eGRID 不强行视为同值。这里使用总输出因子做平均购电账，不使用 non-baseload 因子估计实际购电，也不把地区平均核算写成小时边际减排。

按任务查核的 [EIA CO2 系数表](https://www.eia.gov/environment/emissions/co2_vol_mass.php) 发布于 2024-09-18，采用 2022 数据：天然气 52.91、馏分油 74.14 kg CO2/MMBtu，柴油 10.19 kg/US gal。[EIA 方法](https://www.eia.gov/environment/emissions/includes/methodology.php) 给出碳到 CO2、质量及燃料热值换算，柴油热值为 5.770 MMBtu/barrel、每 barrel 42 US gal。本轮以 EPA 的同表 HHV 系数作为统一主口径，保留 EIA 差异，不拼接两套因子制造一个更精确的数。

## 3. 单位和核算公式

MMBtu 表示一百万 Btu，不能把它当作千 Btu。购电转换用 1 lb = 0.45359237 kg、1 MWh = 1000 kWh；油量使用 US liquid gallon，1 US gal = 3.785411784 L。单位定义可复核 [NIST SI 转换表](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors/nist-guide-si-appendix-b8) 与 [NIST Handbook 130](https://nvlpubs.nist.gov/nistpubs/Legacy/hb/nisthandbook130-2009.pdf)。保留多位数只是可重复计算，不表示原因子的测量精度。

每个同策略、同规格、同窗口的实际能源行独立核算：

```text
C_grid [kg] = E_grid [kWh] * 428.464 [lb/MWh] * 0.45359237 [kg/lb] / 1000 [kWh/MWh]
C_gas  [kg] = (Q_GT + Q_boiler) [MMBtu_HHV] * 53.06 [kg/MMBtu_HHV]
C_grid_plus_gas = C_grid + C_gas

V_DG_conditional [L] = W_shaft_debit [kWh_m] * s [L/kWh_m]
Q_DG_conditional [MMBtu_HHV] = V_DG_conditional / 3.785411784 [L/USgal] * 0.138 [MMBtu_HHV/USgal]
C_DG_conditional [kg] = Q_DG_conditional * 73.96 [kg/MMBtu_HHV]
C_total_conditional = C_grid_plus_gas + C_DG_conditional
```

GT 已有热耗为 HHV。原锅炉效率 0.80 的来源口径未注明 HHV/LHV；本轮对**已记录**锅炉燃气数登记同 HHV 的核算假设，不改效率、蒸汽焓差或运行结果。若其真实口径为 LHV，需要燃气成分或 HHV/LHV 比值再转换；该比例未知，不编造固定修正，也不将锅炉这部分称为实测精确排放。HRSG 是同一 GT 已燃气的余热，不能再加一次燃料/CO2；冷机、泵、塔扇在源端购电/燃气账中已有对应能耗，不能重复计入。

## 4. DG 账和不可识别部分

明确输入字段为 `DG_fuel_budget_debit_shaft_equivalent_kWh`。它来自实际机械功预算扣减，不是 L 油量，也不是化学 kWh。当前继承实现的实际 receipt 满足：

```text
W_m = W_e + W_loss + Delta_E_rotor
fuel_budget_debit_shaft_equivalent_kWh = W_m
```

来源为 `tools/operating_response/diesel.py::spin_up/_receipt` 与 `tools/essential_fixes/execution.py` 的真实扣账；`tools/execution_recovery/review_saved.py` 也检查相同身份。启动 spin_up 的机械功已含增加的转动能和启动期间声明的轴损耗；READY/OPEN 中的声明空转损耗也已反映在机械功。故采用已记录 debit 后，不能再加一遍启动、损耗或正转动能。燃料耗尽后转子耗散可有损耗、负 Delta_E，但机械功和新燃料扣账为零；不对此重复收取燃烧排放。

constant-s 情景只把**模型中已记录的机械功**换为代表性化学燃料。主值 `s=118/456=0.25877192982456143 L/kWh_m` 来自 75% 参考点。它不意味着实际机组一直在 75% 负荷，也不是拟合结果。原厂不同轴端辅助设备范围与本模型小损耗的对应、发动机数、真实低负荷/空载截距、启动富油、预热及外部启动电源均未识别；将恒定单位油耗用于启动/低于表支持域的功率是显式外推。因此：

- `grid_plus_gas_CO2_kg` 单列可核算部分，仍受地区/HHV 假设约束。
- `DG_CO2_kg_conditional` 和 `total_CO2_kg_conditional` 始终标为条件估计；`DG_exact_CO2_unknown=True`，不能称实际柴油量或实测总碳排放。
- 主值及敏感性之间的跨度是登记情景跨度，不是严格上下界、统计置信区间或设备保证范围。实际空转/启动化学燃料仍 unknown，不以零补齐。
- 若窗口切中 DG receipt 内部，继承核算将不可按比例重建的轴功置 unknown；本函数也识别 `partial_DG_receipt_not_interpolated`，只保留可计算的 grid/gas，DG 和合计为空。

## 5. 预登记情景和假设

`references/EMISSIONS_FACTOR_REGISTRY.json` 冻结四种情景，不做全组合扫描，不根据 C/E/K1 排名选因子。

| case_id | 电网 CO2 lb/MWh | s L/kWh_m | 含义 |
|---|---:|---:|---|
| CAMX2023r2_DG75pct | 428.464 | 118/456 | 主条件估计 |
| CAMX2023r2_DG100pct | 428.464 | 154/608 | 原厂 100% 点的单位油耗情景 |
| CAMX2023r2_DG25pct | 428.464 | 50/152 | 原厂 25% 点的单位油耗情景 |
| CAMX2023_originalHub_DG75pct | 436.7 | 118/456 | 旧电网因子版次敏感性 |

| 登记号 | 假设/限制 | 对结果的影响 |
|---|---|---|
| V19-E01 | San Diego 合成研究采用 CAMX 2023 年均购电代理，无实测运行年/供电合同匹配 | 不是小时边际排放或真实设施盘查 |
| V19-E02 | 原锅炉记录的 MMBtu 按 HHV 使用；来源未注明锅炉效率热值基准 | 锅炉 CO2 为该假设下的核算；实际热值修正未知 |
| V19-E03 | DG 采用 EPA No.2 馏分油燃烧/HHV因子 | 未识别实际燃料成分 |
| V19-E04 | 代表性 gross-engine 单位油耗映射聚合机械功，全部策略共享；含启动/空转低载外推 | 条件估计；实际化学燃料未知，非严格范围 |
| V19-E05 | 仅运行 CO2；无上游、CH4/N2O、T&D损耗或设备生命周期 | 不称完整 CO2e/生命周期减碳 |

## 6. 接口、验证和实际结果边界

独立接口为 `tools.model_closeout.emissions.evaluate_emissions(resource_rows, factor_case)`。输入是已汇总的实际能源行列表和 registry 中一个 case 字典；输出复制输入行并增加组件、条件总量和 canonical JSON 因子 SHA256，不读 raw、不运行求解器、不改控制状态。必须由调用者保留输入资源 CSV SHA、M19 模型/策略/窗口绑定及输出 CSV SHA。主窗、正常窗和长窗分别报告，不将不同窗相加比较，也不年化代表窗结果。

无效数值、负能量、天然气总量不一致、可检查的机械功/预算或转子身份不一致均抛出错误；缺失值传播为 `None`，不制造零排放。账内身份检查容限为 `max(1e-6 kWh, 1e-9*scale)`，只用于已导出浮点账的自洽性，不改变任何物理或失效阈值。字段齐全时验证 DG 身份；只有电功或旧模糊字段时不推断化学燃料。

最小实现计划及已执行记录：

- [x] 独立测试先写，真实 RED：`B19_emissions_red01`，退出码 1；16 条失败均由模块尚未实现的显式断言触发，无 collection error。
- [x] 仅新增独立核算代码；修正新测试中 1 MWh 期望值的手算笔误为 194.34800121968 kg，不放宽容限或改旧测试。
- [x] GREEN：`B19_emissions_green01`，退出码 0，16 passed in 0.08s。
- [x] 因素与接口交叉复核：`B19_emissions_registry_qa01`，退出码 0；9 份来源快照 SHA 和四个 case 的 canonical SHA 均匹配。仅用显式标为 synthetic 的单位示例，不是 V19 轨迹结果。`B19_emissions_registry_parse01` 退出码 0，确认 QA 文件可重新解析。
- [ ] 使用 root 提供、带版本绑定的 V19 实际资源 CSV 核算；未执行前不产生 V19 排放收益结论。

实际命令（工作目录均为本项目根目录）：

```powershell
python tools/p0_audit/run_logged.py --run-dir outputs/v19_model_closeout_20261018_run01 --name B19_emissions_red01 -- python -m pytest -q tests/test_model_closeout_emissions.py
python tools/p0_audit/run_logged.py --run-dir outputs/v19_model_closeout_20261018_run01 --name B19_emissions_green01 -- python -m pytest -q tests/test_model_closeout_emissions.py
```

命令 JSON、stdout、stderr 保存在本轮 `logs/B19_emissions_*.{command.json,stdout.log,stderr.log}`。这 16 条只是本独立核算模块的实际测试结果，不是全仓测试通过声明。正式排放结果必须来自新 V19 资源，不能将 V18 数值移植为 V19。正文宜称“在登记排放因子及柴油油耗情景下的事后运行 CO2 评价”；未新增优化目标，不写“实现了减碳调度”。
