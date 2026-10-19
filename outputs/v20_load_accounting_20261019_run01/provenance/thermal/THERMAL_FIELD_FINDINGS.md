# V20 热、冷字段只读取证

本轮结论：`associated_reheat_coil_heating_kW` 按原值纳入区域组热需求，不能再次乘 `zone_multiplier`；`zone_sensible_cooling_kW` 是代表单区量，医院分组汇总时乘一次倍率，代表单区温度计算保持原值。原 CSV 不改。结论来自输出定义、真实 IDF 连接、逐小时同键匹配和同版引擎源码，不是由三个首小时总数的大小关系单独推断。

## 实际来源及执行

- 原工程：`D:\云大文件\论文\医院ies\code_4_2`。
- 原导出工程：`D:\云大文件\论文\医院ies\Hospital_SanDiego_Starter`。
- 导出脚本：`scripts/energyplus_boundary_export.py`，86 行入口使用 `simulation/engineering_audit_20261010/eplusout.sql`；126–130 行读 Hourly 变量；180–188 行生成建筑热/冷边界；192–204 行生成 14 区 CSV。
- 本轮执行：`audit_thermal_fields.py --project <原工程> --starter <原导出工程> --out D:\hospital_ies_v20_evidence_20261019\thermal_fields_r1`，实际退出码 **0**，原始标准输出 `thermal_fields_r1.stdout.log`。完整实际 argv、哈希和判据见 `THERMAL_AUDIT_COMMAND.json` 与 `thermal_fields_r1/THERMAL_EVIDENCE.json`。
- SQLite 采用 `mode=ro` 及 `PRAGMA query_only=ON`；无项目模块导入、无导出脚本执行、无 EnergyPlus 新模拟。15 份原始来源文件运行前后哈希一致。

SQL 的 `Simulations.EnergyPlusVersion` 为 `26.2.0-4bd7a1f26f`。本 SQL 中 `Completed`/`CompletedSuccessfully` 原始字段均为字符串 `FALSE`，没有据此改写状态；对应原 `.err` 末行明确 `EnergyPlus Completed Successfully`、0 Severe。该 `.err` 带大量警告，不能把旧报告的警告“标记数”当作警告发生次数，也不能把该转换运行视为临床或厂家校准。

## 字段契约

| CSV 字段 | SQL Name / KeyValue | 原单位、频率 | CSV 换算 | 倍率边界及使用 |
|---|---|---|---|---|
| `associated_reheat_coil_heating_kW` | `Heating Coil Heating Rate` / `<ZONE> VAV BOX REHEAT COIL` | W，Hourly，Avg，HVAC System，非 meter | `/1000`；导出不乘倍率 | 真实 VAV 盘管的组件侧总换热，系统容量和流量已经对应含重复区的 HVAC 负荷。分组直接使用，不能再次乘倍率。 |
| `zone_sensible_cooling_kW` | `Zone Air System Sensible Cooling Rate` / `<ZONE>` | W，Hourly，Avg，HVAC System，非 meter | `/1000`；导出不乘倍率 | 未含 Zone/List 倍率的单区净显冷交付。组汇总乘 `m` 一次；代表单区温升用原值与单区热容。 |
| `zone_sensible_heating_kW`（旁证） | `Zone Air System Sensible Heating Rate` / `<ZONE>` | 同上 | `/1000` | 同为单区净显热指标；所选 14 区本次全为 0，并非缺失补零。不能用盘管再热值替换它。 |
| `Q_h_heating_coils_kW` | 所有 62 个 `Heating Coil Heating Rate` KeyValue | 同上 | 62 个独立键相加后 `/1000` | 与区域再热使用同一个组件侧输出，14 区盘管是其严格子集，另有 48 个键。 |
| `Q_h_demand_kW` | 上述 62 盘管 + 30 个 `Water Use Equipment Heating Rate` 键 | W，Hourly，Avg | 两个集合各自求和、转 kW | 组件热量加生活热水，必须与纯盘管建筑热量区分。 |
| `Q_c_demand_kW` | 8 个 `Cooling Coil Total Cooling Rate` 键 | W，Hourly，Avg | 求和后 `/1000` | 整栋末端盘管总冷量；不同于 14 区净显冷，不能要求二者相等。 |

8760 个 RunPeriod 时刻均为 60 分钟、Minute=0；`hour_of_year` 按 TimeIndex 顺序 1–8760。时间戳是小时末：`Hour=24` 顺延到次日 00:00。W 小时平均量转 kW 只除 1000，不再除 3600；每小时能量为 kW×1h。电表 J→小时平均 kW 的 `/3,600,000` 是另一条导出路径。

本 IDF 没有 `ZoneList` 或 `ZoneGroup` 对象，SQL 所有 `ListMultiplier=1`。14 区 `zone_multiplier` 与审查 IDF 的 Zone Multiplier、SQL `Multiplier*ListMultiplier` 全部一致，覆盖 1/4/5/6。原 22.1 IDF 与审查 26.2 IDF 的这 14 个 Zone 和对应 `Coil:Heating:Water` 字段逐项相同；不据此宣称整份转换 IDF 与原 IDF 等价。

## 真正的盘管归属与全年闭合

每区均验证了 IDF 的 `ZoneHVAC:EquipmentConnections → EquipmentList → AirDistributionUnit → AirTerminal:SingleDuct:VAV:Reheat → Coil:Heating:Water`，并核对出口节点属于该区的入口 NodeList。14 个盘管名互异，每个盘管只被一个该类终端引用；62 个 SQL 热盘管键与 IDF 的 62 个热水盘管名完整一一对应。

逐小时读取 14×8760=**122,640** 行，三个热/冷字段共 **367,920** 值与 SQL `/1000` 精确匹配，最大绝对误差均为 **0**。建筑盘管热、DHW、组合热需求、末端冷量分别由其 SQL 完整键集合重建，最大绝对误差依次为 `1.71e-13`、`1.78e-14`、`1.71e-13`、`4.55e-13 kW`。未用缺失值填零；所需字典键、每键8760个有限值、无重复时刻均做了检查。

| 第1小时（2017-01-01 01:00） | kW |
|---|---:|
| 14个互异再热盘管按原值相加 | 105.311584910788 |
| 建筑其余48个热盘管 | 105.472826550869 |
| 建筑全部62个热盘管 | 210.784411461657 |
| 生活热水 | 8.805953443334 |
| 组合建筑热需求 | 219.590364904990 |
| 14盘管再次乘 Zone 倍率的旧代理 | 294.178796690570 |

14 个再热值已经处于与建筑全部62个盘管相同的组件侧边界。再次乘倍率重复放大了选中子集，使旧热代理在全部 **8760 小时**超过建筑组合热需求；按原值的14盘管总和在全部小时均不超过62盘管总和，其余48盘管汇总非负闭合。

全年按一小时积分：14盘管原值 **806,266.834277 kWh**；旧重复倍率代理 **2,246,606.189239 kWh**；建筑全部盘管 **1,175,613.498284 kWh**；DHW **121,338.554484 kWh**。这些是原始量及分组算术旁证，不是新调度结果。

## 倍率 1/4/5/6 的实际例子

| Zone | m | 再热 CSV=组件SQL/1000（kW） | 冷 CSV=单区SQL/1000（kW） | 该区组冷需求 m×CSV（kW） |
|---|---:|---:|---:|---:|
| OR1_FLR_2 | 1 | 2.611187644323 | 0.252841884659 | 0.252841884659 |
| ER_EXAM1_MULT4_FLR_1 | 4 | 13.711959575324 | 0.468355683896 | 1.873422735583 |
| OR2_MULT5_FLR_2 | 5 | 11.762287040801 | 0.511615185249 | 2.558075926246 |
| IC_PATROOM3_MULT6_FLR_2 | 6 | 7.004712828605 | 0.298676806371 | 1.792060838224 |

这些数来自同一小时，具体 SQL 字典索引、所有14个实际键和连接见 `ZONE_COIL_KEY_MAPPING.csv`。不同房间的几何、送风及工况不完全相同，因此“OR2 热量约为 OR1 五倍”只能作旁证，不能作为倍率证明或硬编码回归真值。

冷量的正确一次倍率代理仍在 **137 小时**超过整栋末端总冷量。这是原有混合测量边界/代理分配问题，不能为消除它而撤销单区倍率。14区只是区域子集；净显冷还受送风、通风和再热影响，建筑盘管总冷量包含不同的潜热与测量面。若继续采用原有比例压缩，需在新版本显式报告原始代理、超出量和压缩时刻，不能把压缩后的守恒称为边界已经精确统一。

## 同版原机构定义与源码

访问日期：2026-10-19 UTC。以下源码 URL 固定到 SQL 记录的编译提交 `4bd7a1f26f`，没有用较新 master 替代。

- [EnergyPlus 26.2 I/O Reference, Thermal Zone Description/Geometry](https://energyplus.readthedocs.io/en/v26.2.0/guides/input-output-reference/1.10-group-thermal-zone-descriptiongeometry.html)：Zone multiplier 将负荷送往 HVAC 时放大；内部增益表计有倍率；逐字段说明中 Zone Air System Sensible Cooling/Heating Rate 不含 Zone/Group 倍率。应读具体输出定义，不能将 Zone 对象总述泛化到所有报表字段。
- [ZoneTempPredictorCorrector.cc](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/ZoneTempPredictorCorrector.cc#L3754-L3760)：单区净显热计算中系统节点流量除以 ZoneMult，随后传给报表过程。
- [DataZoneEnergyDemands.cc](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/DataZoneEnergyDemands.cc#L320-L343)：净显冷/显热报表直接取该单区量；送给设备的系统预测需求另乘 Zone Multiplier×ListMultiplier。二者是分开的变量。
- [WaterCoils.cc 输出定义](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/WaterCoils.cc#L435-L441)及[组件换热计算](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/WaterCoils.cc#L2586-L2664)：Heating Coil Heating Rate 报组件实际水/空气流量对应的总换热功率，没有将它除回代表单区。该组件服务的是含 Zone 倍率的终端/系统；导出不能再乘该区数量。
- [ZoneEquipmentManager.cc](https://github.com/NatLabRockies/EnergyPlus/blob/4bd7a1f26f/src/EnergyPlus/ZoneEquipmentManager.cc#L1107-L1122)：设计区负荷和流量已考虑倍率，手工给定的设计流量须相应处理；这也解释了组件侧与代表区报表侧的差别。

上述依据足以决定本数据接口的倍率规则；本 SQL 没有请求节点质量流量和进出口焓/温度，故没有额外声称逐小时独立重建盘管 `m_dot×Δh`。无倍率为1的反事实 EnergyPlus 模拟，也未声称完成了此类新模拟。

## 哈希索引

全部15份输入的绝对路径、字节数、SHA256在 `thermal_fields_r1/THERMAL_EVIDENCE.json`，输出索引在 `thermal_fields_r1/OUTPUT_SHA256.json`。关键摘要：

| 文件 | SHA256 |
|---|---|
| 原审查 eplusout.sql | `12771fb55683c2f08df7008ed8358a8912c41f0d14e9c1b9c47f317769838ef9` |
| 审查 IDF | `7b8a6c95743529e8174a7912992674e469a991d3cc6f0f58ae5a83d1fcd8b0d1` |
| 原/项目 IDF | `129bd06f9dccc1030d6461f284da788342569319c0d3d3fc933fa72ffcdcb8e2` |
| 实际导出脚本 | `746042d6643109bc5522adaac62987b59040ebeeea53803f73d33463fb3fe451` |
| 项目/Starter 原关键区 CSV | `6577fa5bed87c5b53dae2a814231e8fbd98d34bf486ddb351224bedde60d88d6` |
| 项目/Starter 建筑需求 CSV | `0adc2c3f2cd82d600cd17b38e634eae7701710fff0a146e588482dd3cd0a0627` |

本报告是输入字段取证，不是 V20 调度结果或生产修改。后续新口径、压缩诊断、热容不变性与服务结果应由新版本自己的测试、冻结和实际运行分别证明。
