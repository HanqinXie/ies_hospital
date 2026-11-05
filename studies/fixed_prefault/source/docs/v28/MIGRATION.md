# V28 接口、需求与证据迁移

本文说明采用实现及结果的读取方法。代码路径相对于`project/`，证据路径相对于交付根；原输入、V27结果和失败记录均未改写。公开reset/step签名及命令到达、启动、保护规则没有改变；t0新需求激活的行为在下文单列。

## 版本必须分别读取

|版本|实际用途|源码/输入绑定|
|---|---|---|
|V28 A01，`17bba458b0aba85a655ee6721fb84a57b7f38807`|固定直接电，保留原热需求；W1四例、临床叠加及热泵故障共6条完整实际轨迹|`execution/FROZEN_SOURCE_V28_A01.json`；source `28ac4893098fbc7f0a5067c1eb6a85fc64ad22badd84399bc01a4f444685776f`；input `10070101f2b016f6e238fae4e4b1b1ccb0a9cdf8b36a70bfefcfb8ab6e44564a`|
|V28 B01，`09f922840b7cfaef209d693ebfdda3492e16e24a`|采用普通DHW目标及非空气热风机分解；同窗N/F/D/FD和原热正常对照共5条实际运行|`execution/FROZEN_SOURCE_V28_B01.json`；source `9cb3ea825662324cf7b718ffca7c581066506907859a5739c7d5a3c366fc18f9`；input `5c6fea468008561acefd81b16d4cd57a8524f7897f861ccb436f1a32adaaeae8`|

运行终结不自动等于服务/费用独立验算通过。B实际费用及服务结果见已执行的原生审计与MODEL_REPORT，本文不重复数值表。A与B不能混为同一输入版本，也不能将A的14h费用和B的3h费用直接相减。

## 公开入口与兼容范围

新入口为`python -B -m tools.model_review_v28.validation`，研究身份`hospital_ies.demand_service_boundary.v28.v1`。例如从`project`运行：

```powershell
python -B -m tools.model_review_v28.validation --case smoke_normal --policy C --configuration H0 --output E:\V28_new_smoke
```

输出必须是新的D/E目录；依赖和完整复算步骤见README。注册默认需求版本为`V28_adopted`，`smoke_normal`也采用该版本。`A_original_heat`仅保留原热请求，**同样使用固定直接电**，不是回到V27电边界。正常无冷overlay入口也必须经过V28激活器。

行标志为`sci_demand_boundary_contract='hospital_ies.fixed_direct_aux.v28.v1'`。`demand.bind_request(row, raw_loads)`只读取当前行及当前请求，返回新行和4×3请求矩阵；原参考不改。旧行无标志时，既有legacy需求/服务路径保持原行为，供历史复现；V28公开入口不允许静默走该路径。底层仍复用共同M25物理执行核心，公开策略身份、研究身份、需求版本与实际source/input哈希另行绑定，不能只看底层model_id判断输入版本。

目标顺序保留V27已验证的`after_cost`；V28未再移动其他准备/调节目标。未增加MPC、预测器、拟合、容量、并网权限或故障快通道。

## 电请求与实际交付

唯一构造口径为：

`D_req = D_ref + 明确直接电冲击`；`A_req = 当前服务按原参考计算的建筑辅机`；`E_req = D_req + sum(A_req)`。

P0–P2的`D_ref=P_base_e`；P3的`D_ref=P3_base_e−source_fans_total−source_chw_pump`。另加的热循环泵不再扣一次。这里是源模型分解的非辅机直接用电，不是逐台临床实测电表。临床源电增量与热冷增量分别记录，旧shock路径不再额外补一遍辅机；风机电驱动使用原`E_ref+直接增量`，不会把新增A再送回风机比率。

`sci_direct_aux_request`保存完整原请求的D/A/E及驱动；观察、主计划、原有静态准备副本与实际执行共用该口径。连接子集表示接触器/切除后允许执行的范围，不是新的原始需求。实际字段在`service_dependencies.demand_components`与`m28_demand_components`：

`E_served = D_served + sum(A_served)`。

缺口从完整请求减实际交付得到；辅机故障、服务切除或低供电不会缩小原D_req。热冷切除释放的辅机电不能变成未请求的额外direct电吸纳器。原始微小有符号残差保留，工程中断按原阈值判断；未发生中断时恢复时间为不适用。

源fan组件、priority shares及M22冻结故障设计权重保留；当前组件请求按同组fan factor放大，组内故障归属不变。建筑风机、CHW分配泵和热循环泵不与EC/ABS综合转换电、GT启动电、锅炉辅机、储能专用泵重复计量。请求辅机不等于已支付辅机，实际付款仍来自执行能量账。

## 采用热输入与非空气热

正式热输入为`inputs/model_review_v28/THERMAL_SERVICE_COMPONENTS_8760.csv`及`THERMAL_INPUT_METADATA.json`。普通30个WaterUse的原交付代理替换为接受区间核实的目标；空间供暖/再热仍保留原交付代理。P3当前H增加“普通目标−旧交付”，旧H_ref不变，泵增量只经上述唯一构造器。

只有有效`v28_thermal_service_trace`启用B分解：完整请求的fan热驱动为`H−新普通DHW目标+旧普通DHW交付`；连接/静态副本为`H_scope−min(H_scope,新目标)+旧交付`；循环泵仍用全当前`H/H_ref`。旧交付是保留的源基线代理，不是声称连接子集已实际得到这份水热。A无trace保持原结果口径，见`execution/B_A_ONLY_EQUIVALENCE.json`的8760小时与16个扰动精确对照。

运行行中的`Q_h_DHW_water_use_kW`在B作新版目标的兼容alias；`v28_ordinary_DHW_source_delivered_kW`保留旧交付。`sci_heat_decomposition_source`在B明确指向新输入/目标列/哈希，同时单列旧交付值及原输入路径/哈希；A/legacy仍标原来源。非空气热上界使用绑定目标及连接热量，仍受真实热源和所需循环泵约束。60℃在这里是有用热目标等级，不是末端实际出水温度或全水网动态保证。

Laundry和booster保留源本地设备，不新增中央82.22℃能力。Laundry燃气、booster原电及设备能量/储罐账是独立source-reference；booster电已经在D_ref，不能再加入中央增温热并重复计电。源booster只有60℃进水排程、没有可证上游连接；该预热义务已披露，当前中央保障明确不覆盖，来源能耗/费用/排放为未知null，不能写成免费备用。

## 配对、公平性与失败记录

共同原源/seed先reset全部物理状态、core、队列和合法历史，再从t0启用当前冷/热版本；不是分别预热到两个不同平衡态。初始当前需求可以因输入版本合法变化。进程全局Python/NumPy RNG诊断状态不相同，整份含RNG receipt的SHA也不同；`execution/GLOBAL_RNG_USAGE_REVIEW.json`独立确认本次固定C/M/物理路线没有消费这些全局随机状态。只据此分开比较完整物理core和合法历史，不声称RNG本身相同，也不推广到未来随机/学习策略。

B正常原热对照与采用版同窗、同初态，但比较的是“普通目标+fan非空气分解”整体热边界包；原热入口没有B trace，不能将费用变化归因于纯CSV数值差。A独立阶段才用于隔离电边界修正。

A原冷C输出路径门槛异常和临床stage编码异常均发生在物理episode之前，实际episode数0；失败原件、未知metrics及新输出重试均保留。路径触发原因未证实，插桩重试未修改原门槛；临床修正只改stage/event_class解析编码，冲击、故障、时刻和幅度不变。见`execution/DISPATCH_PREFLIGHT_FAILURE01.json`、`execution/PATH_PREFLIGHT_FOLLOWUP01.json`、`clinical_case_repair01/REPAIR_RECEIPT.json`。后端不可行探针/原域重试与新增轨迹数分别统计，不把被拒候选删除或填零成功。

主要证据为`A_native_audit/final01/`、`thermal_source/THERMAL_SERVICE_LEDGER.md`、`thermal_source/service_ledger01/`及各版本冻结记录。默认测试、便携入口和最终ZIP后验成绩以其实际日志与FINAL_ACCEPTANCE为准，不能拿历史通过数代替本轮证据。
