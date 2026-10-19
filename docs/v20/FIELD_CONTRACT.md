# V20 区域电、热、冷字段合同

合同标识：`hospital_ies.load_accounting.v20.v1`，用于 `M20_load_accounting`。本合同改变分组时如何使用既有字段，**不改写四份原始负荷 CSV**。对应修正规模、测试及证据见 [数据修正报告](DATA_CORRECTION_REPORT.md)。区域用途分组仍是研究代理，不能据此称为实测临床供电回路或现场保障标准。

## 1. 源数据、时钟与倍率

实际源为 `Hospital_SanDiego_Starter/simulation/engineering_audit_20261010/eplusout.sql`；其 `Simulations.EnergyPlusVersion` 为 `26.2.0-4bd7a1f26f`。实际导出器为同 Starter 下 `scripts/energyplus_boundary_export.py`，不是后续调度轨迹导出器。本轮只读 SQL、IDF 与既有 CSV，没有运行 EnergyPlus 或重新导出数据。

年度 RunPeriod 有8760个60分钟小时；`hour_of_year` 为按 TimeIndex 排序的1–8760。时间戳代表小时结束，Hour=24顺延到次日00:00。所有下述字段均为 Hourly：J 型 Sum 表计除以3,600,000得到该小时 kWh，其数值等于1小时平均 kW；W 型 Avg 输出除以1000得到平均 kW，不能再乘3600作为功率。年度能量按各小时 kW×1h积分。

CSV 的 `zone_multiplier` 来自 SQL `Zones.Multiplier × ListMultiplier`。本次14区包含倍率1/4/5/6；审查 IDF 没有 ZoneList/ZoneGroup 对象，SQL 的 ListMultiplier=1，因此这份数据的重复数来自 Zone Multiplier。不得由“区域名称含 MULT”猜测字段是否已施加倍率，也不得对整张表统一乘除倍率。

## 2. 四字段逐项定义

每项规则由四层证据约束：SQL字典及数值、实际导出语句、IDF区域/设备连接及倍率、与 SQL 版本对应的 EnergyPlus 输出定义/源码。完整记录见 [热冷取证报告](../../outputs/v20_load_accounting_20261019_run01/provenance/thermal/THERMAL_FIELD_FINDINGS.md) 和 [电字段 SQL 对照](../../outputs/v20_load_accounting_20261019_run01/provenance/load_grouping/annual_audit01/H1_SQL_MULTIPLIER_EVIDENCE.csv)。

| CSV字段 | SQL Name / KeyValue | 字典类型与导出换算 | 倍率及分组规则 | 代表单区温度接口 |
|---|---|---|---|---|
| `interior_equipment_kW` | `InteriorEquipment:Electricity:Zone:<ZONE>`；表计 KeyValue 为 NULL，区域编码在表计名中 | ReportMeterData，Zone timestep、Sum、J；导出 `/3_600_000` | 表计已计入区域重复数，直接纳入电代理，不再乘 m | 不作为额外显热直接加到温度方程；不能把含 m 的电表值当单区显热 |
| `interior_lighting_kW` | `InteriorLights:Electricity:Zone:<ZONE>`；KeyValue 同上 | 同上 | 同上；与设备电相加后直接分组 | 同上 |
| `associated_reheat_coil_heating_kW` | `Heating Coil Heating Rate` / `<ZONE> VAV BOX REHEAT COIL` | ReportVariableData，HVAC System、Avg、W；导出 `/1000` | 实际盘管组件整流量换热已反映重复区域的系统负荷，按 CSV 值纳入热代理，不再乘 m | 不用该组件总热功率替换代表单区显热冷量或热容 |
| `zone_sensible_cooling_kW` | `Zone Air System Sensible Cooling Rate` / `<ZONE>` | ReportVariableData，HVAC System、Avg、W；导出 `/1000` | 此输出为未乘区域/分组倍率的代表单区显热冷量；汇总需求时乘 m 一次 | 使用 CSV 单区原值及单区 C、UA，不再乘 m |

导出器95–110行选择年度时钟并换算电表；132–141行区分变量取值与表计取值；191–204行读取 Zones 倍率并直接写入上述四字段，没有在导出时另乘倍率。电字段证据还比较了倍率1/4/5/6真实区域的 `Electric Equipment Electricity Rate` / `<ZONE>_MISCPLUG_EQUIP` 和 `Lights Electricity Rate` / `<ZONE>_LIGHTS`：这些单区对象变量 W×m，与 CSV 采用的小时区域电表严格对应。**对象单区变量与区域表计是不同统计量。**

热字段另逐区核验 `ZoneHVAC:EquipmentConnections → EquipmentList → AirDistributionUnit → AirTerminal:SingleDuct:VAV:Reheat → Coil:Heating:Water` 及出口节点。14个对应盘管互不重复，属于建筑全部62个 `Heating Coil Heating Rate` 键的严格子集。14区×8760小时的再热、显热冷量及旁证显热供热字段共367,920值与 SQL `/1000` 对应，最大绝对误差为0；建筑62盘管之和与 `Q_h_heating_coils_kW` 的最大误差为1.71×10⁻¹³ kW。此处不能仅凭第一小时数值大小推断倍率。

## 3. 建筑边界与 P0–P3 分配

对每小时，建筑需求边界保持：

```text
e_total = P_e_nonplant_kW + P_e_unresolved_kW
          + 已有的 electric_extra_demand_kW（若该列存在）
h_total = Q_h_demand_kW = 全部62盘管热功率 + DHW热功率
c_total = Q_c_demand_kW = 全部8个末端盘管 Total Cooling Rate 之和
```

电需求不改为 `P_e_facility_kW`，以免将原 EnergyPlus 能源站电耗与当前模型另算的辅机重复计入。原 `loads_for_step` 冲击、设备转换和服务判据不由本合同改变。

14区域按既有映射形成 P0/P1/P2 代理。令该载能三个等级压缩前之和为 S、建筑总量为 T，沿用原算法：`scale=min(1,T/S)`（S近零时取1）；P0–P2 同载能代理乘该比例，P3为非负剩余。现有热冷负值 clip、比例压缩及 P3边界算法保留，改变的是电和再热输入代理的倍率口径。本批四源字段负值实际计数均为0，因此原 clip没有截去实际负值。

再热按一次组件功率汇总后，14盘管每小时都不超过建筑62盘管；其余48盘管与 DHW 进入剩余需求。冷则是14区域显热子集与建筑末端总冷量的不同覆盖/统计边界，不能要求二者相等。当前仍有137小时 S>T，最大差33.942128 kW；保留原比例分配并显式记录，不能把压缩后的守恒称作原始口径已严格闭合。

## 4. 代表单区温度与同版官方依据

对区域 z，以所在等级当前缺冷比例 α 作用于代表区显热冷量：`q_deficit,z = α × max(0, CSV_zone_sensible_cooling_kW)`；温度增量沿用原1R1C式 `ΔT_next = ΔT·exp(−UA·dt/C) + [1−exp(−UA·dt/C)]·q_deficit/UA`。dt为h、C为kWh/K、UA为kW/K。原附加显热/过程冷量归因保持原接口；没有用电表、组件再热或整个建筑缺冷功率替换这个单区输入。

同样的代表区域重复数增多时，汇总显热冷需求随 m 增加；在相同缺冷比例和固定单区 C/UA 下，代表区温升不应随 m 放大。原 `zone_parameter_table` 从代表区几何和内部热质量构造 C/UA，不乘区域重复数；这些仍是未做现场校准的低阶假设。

同版原始依据如下，完整解释和本地实证链同时保留，不能把 Zone 的通用倍率描述套到所有输出变量：

- [EnergyPlus 26.2 I/O Reference：Thermal Zone Description/Geometry](https://energyplus.readthedocs.io/en/v26.2.0/guides/input-output-reference/1.10-group-thermal-zone-descriptiongeometry.html)：Zone Multiplier 段说明设备/照明计量的倍率；具体 `Zone Air System Sensible Cooling/Heating Rate` 字段说明其不含 Zone/Group 倍率。
- [DataZoneEnergyDemands.cc，提交4bd7a1f26f](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/DataZoneEnergyDemands.cc#L320-L343)：代表区显热报告与送往设备/系统的倍率负荷分别处理。
- [ZoneTempPredictorCorrector.cc，同提交](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/ZoneTempPredictorCorrector.cc#L3754-L3760)：系统节点换热按 ZoneMult 还原代表区域。
- [WaterCoils.cc 输出注册](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/WaterCoils.cc#L435-L441) 与 [盘管计算](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/WaterCoils.cc#L2586-L2664)：组件功率来自实际流量换热，不在消费该 Rate 时再乘区域数。
- [ZoneEquipmentManager.cc，同提交](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/ZoneEquipmentManager.cc#L1107-L1122)：分区设备负荷分配处理包含倍率的系统需求。

SQL与原`.err`的完成状态字段差异、未记录全部出口状态、未以 m=1 重新运行 EnergyPlus 等限制见热冷取证报告；本合同不将来源核对提升为完整热工、厂家或临床验证。

## 5. 可审计 API

`src/hospital_ies/resilience.py` 的公共 `build_zone_groups(zone_df, hourly)` 保持原两参数和 `P0_e_kW` 等12个负荷返回列。默认分组已使用本合同，选择性数据读取和新环境不需全局替换函数。

`grouping_diagnostics(zone_df, hourly)` 返回28列：`hour_of_year`，加每个 e/h/c 的以下9列；DataFrame attrs 中 `load_accounting_contract_id` 为本合同标识。

| 列模板 | 含义 |
|---|---|
| `P0_<k>_raw_kW`、`P1_<k>_raw_kW`、`P2_<k>_raw_kW` | 各等级代理，按正确字段倍率计量、在比例压缩之前；热冷沿用原非负代理定义 |
| `<k>_proxy_sum_kW` | 上述三个压缩前代理之和 S |
| `<k>_building_total_kW` | 同小时建筑边界 T |
| `<k>_compression_scale` | 实际沿用的比例 |
| `<k>_proxy_excess_kW` | max(0,S−T)，保留原超量，不用压缩后残差代替 |
| `<k>_compression_applied` | 比例小于1是否发生 |
| `<k>_P3_residual_kW` | 最终剩余需求 |

这里的诊断不调用调度求解器，不改变设备、阈值或目标。新版本运行审计须保留实际载荷与这些字段的身份/时钟绑定，不能将诊断表当作已完成服务或已排除风险的证据。
