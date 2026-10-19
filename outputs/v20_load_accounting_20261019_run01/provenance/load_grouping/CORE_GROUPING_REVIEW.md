# V20 电、热分组修正与全年算术审计

本证据是原始输入口径与 P0–P3 分组算术的审计，不是新调度、ENS、费用或排放结果。原四份 CSV 字节未变；没有修改设备能力、动作权限、队列、实际执行、保护、DG 预算或服务阈值。

## 字段规则及实际来源

- `interior_equipment_kW`、`interior_lighting_kW` 是 EnergyPlus `InteriorEquipment:Electricity:Zone:<zone>`、`InteriorLights:Electricity:Zone:<zone>` 小时表计 J / 3,600,000。小时积分的 kWh 数值等于这一小时的平均 kW；表计已包含 Zone/ZoneList 倍率，分组直接使用 CSV 值。
- `associated_reheat_coil_heating_kW` 是实际对应组件 `Heating Coil Heating Rate` 的 W / 1000，已反映重复区域。热字段代理直接使用 CSV 值；14 个唯一盘管是建筑62个盘管的子集，其他盘管及 DHW 留在 P3。完整 SQL/IDF 连接与热冷字段取证由 `../thermal_fields_r1` 独立证据负责。
- `zone_sensible_cooling_kW` 是代表单区显热冷量。汇总负荷保留一次 `zone_multiplier`；代表单区温度的冷量、热容 C、UA 不随重复数量再乘。没有修改 `update_thermal`。

真实 exporter 为 `D:/云大文件/论文/医院ies/Hospital_SanDiego_Starter/scripts/energyplus_boundary_export.py`：95–110行取年度小时并将表计 J / 3,600,000；132–141行区分输出变量与表计；180–188行生成建筑冷热需求；191–204行记录 Zones 倍率、直接导出区域电表及热冷变量。SQL 实际路径为同 Starter 下 `simulation/engineering_audit_20261010/eplusout.sql`，本审计按 read-only + query_only 查询。`annual_audit01/H1_SQL_MULTIPLIER_EVIDENCE.csv` 保存倍率1/4/5/6的八条组件/表计真实对照。

原调用链没有除回重复倍率：`tools/model_closeout/environment.py:64` → essential/operating 的选择性环境初始化 → `tools/autonomous_dispatch/environment.py:88` → `selective_data.py:152` → `resilience.build_zone_groups`。新增测试也从真实 `selective_inputs` 入口验证修正，无全局 monkeypatch。

## 最小生产变更及诊断接口

仅修改 V20 工作树的 `src/hospital_ies/resilience.py`，新增 `tests/test_v20_load_accounting.py`。原 V19 工作区保持不变。

`resilience.py:52` 的 `_zone_group_arrays` 将电与再热取消再次乘倍率，保留冷的一次倍率。保留的是原有热冷非负 clip、比例压缩和 P3 余量**算法**，不是说热代理数值未变。原输入四字段负值实际计数均为0；没有增加截断来抹去异常。全年审计还逐AST确认其他12个既有函数/类不变。

公共 `build_zone_groups(zone_df, hourly)` 的两参数与原返回列保持；`resilience.py:95` 新增 `grouping_diagnostics(zone_df, hourly)`。合同为 `hospital_ies.load_accounting.v20.v1`，返回 DataFrame 共28列：`hour_of_year`；每个 e/h/c 各有 `P0/P1/P2_<carrier>_raw_kW`、`<carrier>_proxy_sum_kW`、`building_total_kW`、`compression_scale`、`proxy_excess_kW`、`compression_applied`、`P3_residual_kW`（后六列均以载能前缀起名）。attrs 的 `load_accounting_contract_id` 绑定合同。raw 表示既有非负热冷代理在比例压缩前的值，不能解释成覆盖全院的独立实测回路。

## 修正影响

小时1：

| 分量 kW | V19 | V20 |
|---|---:|---:|
| P0 电 | 35.100008386 | 21.174921676 |
| P1 电 | 48.035303710 | 18.150541605 |
| P3 电 | 196.275912966 | 240.085761782 |
| P0 热 | 82.125138821 | 62.971501089 |
| P1 热 | 137.465226084 | 42.340083821 |
| P3 热 | 约0 | 114.278779994 |

8760小时积分：

| 分量 MWh | V19 | V20 |
|---|---:|---:|
| P0 电 | 641.037371 | 334.156254 |
| P1 电 | 580.677143 | 219.038095 |
| P2 电 | 260.297551 | 260.297551 |
| P3 电 | 2527.120354 | 3195.640520 |
| P0 热 | 524.670929 | 508.728975 |
| P1 热 | 772.281124 | 297.537859 |
| P2 热 | 0 | 0 |
| P3 热 | 约0 | 490.685218 |

整栋各载能总量保持不变，电有668.520165 MWh从错误的关键分组回到 P3；P3 热恢复为整栋热量的37.8337%。冷分组逐值不变。电、热、冷都保留逐小时压缩前审计：压缩小时分别为 0→0、8760→0、137→137；旧热最大超量222.940661 kW，冷最大33.942128 kW。137小时冷压缩仍属于区域覆盖与显热/整栋总冷量不同边界下的既有代理分配约束，不能把压缩后的守恒当作原代理已经同口径闭合。

全年减少不等于每小时都减少：P0 热在4695小时上升、4065小时下降，最大增加13.234814 kW，原因是去掉错误 P1 权重后解除统一比例压缩。P1 热、P0/P1 电在全部8760小时下降。不能由这些算术变化预先宣布策略改善；M20必须重新运行并报告实际服务和取舍。

## 实际验证及文件

| run_logged 名称 | 实际退出码 | 实际结果 |
|---|---:|---|
| `load_accounting_red01` | 1 | 旧实现10 failed、3 passed：重复倍率、缺诊断和默认选择性入口均被击中 |
| `load_accounting_green01` | 0 | 新增13 passed；真实倍率1/4/5/6、单区/表计区分、热剩余、三载能守恒、压缩前审计、代表区温升不变 |
| `inherited_grouping_green01` | 0 | 7项既有分组/授权测试通过；没有改旧断言或扩大容差 |
| `annual_accounting_audit01` | 0 | 8760×14真实数据算术审计，8产物、SHA读回无问题，最大守恒残差2.27×10⁻¹³ kW |

命令、时间、stdout、stderr、退出码位于 `logs/`。全年CSV、输入/源码SHA、统一diff和收据位于 `annual_audit01/`，总计约9.24 MB。其中 `HOURLY_PRIORITY_COMPARISON.csv` 保留原后所有分组，`ANNUAL_PRIORITY_COMPARISON.csv` 保留积分、占比和峰值；`V20_GROUPING_DIAGNOSTICS.csv` 与 `PRECOMPRESSION_BY_HOUR_CARRIER.csv` 保留压缩前全部小时，而不是仅保存修正后的守恒。

V20 core SHA256：`3a57bfd91c1fc75e7c80d2c48d96c4ca92c17a6670b0a37dc5c23ccc00e6dc62`。新增测试 SHA256：`db8794f9631dfb11a84a77f3bf096393bb1ebf7ab5d2089ae775a1badcfcc422`。

此次未启动调度轨迹或优化器；全历史测试、M20新身份门槛和新比较运行由后续阶段负责，不能将上述13+7项称作整个新版已验收。
