# 医院 IES V30 模型与评价工程验收报告

展示标签说明：仅当实质中断及恢复结果相同，且原始ENS差的绝对值不超过64×max(ulp(C),ulp(M))时，标签记为roundoff_only_same_material_outcome。该规则仅处理二进制浮点舍入级文字标签，不改变任何ENS、绝对差、物理判据、模型参数或CSV字节；较大真实差异继续保留原方向，无实质中断的微小差异不称算法优势。

结论：本轮注册边界内的模型与评价可冻结。最终证据来自同一修正核心下实际完成的32条N02/D02轨迹，按八个背景同时保留C/M正常与扰动结果。交付包仍须由最终manifest、独立全量恢复及大小核验另行验收。

## 医院服务如何接续、在哪里受限

本轮沿用既有GT—HRSG—蒸汽供热/吸收制冷、电制冷、共同冷却塔和循环泵，以及有限UPS、蓄冷和DG轴功预算。通信、启动、切换、服务锁和保护由共同执行器落实；准备与响应造成的事故前在线状态分别保留。热冷服务恢复以实际交付台账判定，低阶室温动态未获新增物理资格。

下列每个背景给出实际正常基线及扰动结果。P0/P1优先看各载能形式ENS、中断与实际/确认恢复，再看P2/P3、费用和末端资源；没有跨电热冷合成总分。

### b01_L1_grid（L1）

原生起始小时 1160；硬件 H1；实际注册故障 {"GRID":0.0}、辅助故障 {}。准备期、共同终点和恢复尾段按本背景预登记执行。

|方法/工况|P0 ENS（电/热/冷，各自kWh）|P1 ENS（电/热/冷，各自kWh）|纯费用 USD2001|相对本方法实际正常费用差|
|---|---|---|---:|---:|
|C正常|0.0001383505914986929/0.0/0.0|0.00013835059150579823/0.0/0.0|89.84372535888538|0.0|
|C扰动|0.00013277285709006528/0.0/0.0|0.00013277285709539427/0.0/0.0|112.56322728559338|22.719501926708006|
|M正常|0.0001383505914986929/0.0/0.0|0.00013835059150579823/0.0/0.0|89.8700969019322|0.0|
|M扰动|0.00013835059149869272/0.0/0.0|0.0001376950629633279/0.0/0.0|120.00232510658562|30.132228204653416|

C扰动：P0/P1未观察到实质供能中断；原始微小ENS仍完整保留在结果表。UPS实际放出 12.988971124670158 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 103.12506025451162 kWh；末端UPS 40.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2296.874939745496 kWh。
真实末端GT出力 [129.3186561,0.0,0.0] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after":"ISLAND","before":"GRID","kind":"grid_bus_state_changed","reason":"observed_grid_hardware_unavailable","time_minute":60.0},{"after":"STARTING","before":"OFF","kind":"diesel_state_changed","time_minute":60.0},{"after":"CONNECTED","before":"STARTING","kind":"diesel_state_changed","time_minute":75.0},{"after":"RETURN_PREP","before":"ISLAND","kind":"grid_bus_state_changed","reason":"healthy_grid_and_arrived_permission","time_minute":120.0},{"after":"TRANSFER_OPEN","before":"RETURN_PREP","kind":"grid_bus_state_changed","reason":"prepared_open_transition","time_minute":120.25},{"after":"READY","before":"CONNECTED","kind":"diesel_state_changed","time_minute":120.25},{"after":"GRID","before":"TRANSFER_OPEN","kind":"grid_bus_state_changed","reason":"finite_open_transfer_completed","time_minute":120.5},{"after":"OFF","before":"READY","kind":"diesel_state_changed","time_minute":120.5}]。

M扰动：P0/P1未观察到实质供能中断；原始微小ENS仍完整保留在结果表。UPS实际放出 12.988075876042366 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 108.02065769475081 kWh；末端UPS 39.99999999999999 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2291.979342305245 kWh。
真实末端GT出力 [0.0,129.31865599999998,129.31865599999998] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after":"ISLAND","before":"GRID","kind":"grid_bus_state_changed","reason":"observed_grid_hardware_unavailable","time_minute":60.0},{"after":"STARTING","before":"OFF","kind":"diesel_state_changed","time_minute":60.0},{"after":"CONNECTED","before":"STARTING","kind":"diesel_state_changed","time_minute":75.0},{"after_kW":225.0,"before_kW":0.0,"device":"GT_2","kind":"gt_actual_started","reason":"actual_execution","time_minute":90.0},{"after_kW":225.0,"before_kW":0.0,"device":"GT_3","kind":"gt_actual_started","reason":"actual_execution","time_minute":90.0},{"actual_kW":225.0,"device":"GT_2","kind":"gt_start_completed","time_minute":90.0},{"actual_kW":225.0,"device":"GT_3","kind":"gt_start_completed","time_minute":90.0},{"after_kW":0.0,"before_kW":202.73573168139293,"device":"GT_1","kind":"gt_actual_stopped","reason":"actual_execution","time_minute":90.0},{"after":"RETURN_PREP","before":"ISLAND","kind":"grid_bus_state_changed","reason":"healthy_grid_and_arrived_permission","time_minute":120.0},{"after":"TRANSFER_OPEN","before":"RETURN_PREP","kind":"grid_bus_state_changed","reason":"prepared_open_transition","time_minute":120.25},{"after":"READY","before":"CONNECTED","kind":"diesel_state_changed","time_minute":120.25},{"after":"GRID","before":"TRANSFER_OPEN","kind":"grid_bus_state_changed","reason":"finite_open_transfer_completed","time_minute":120.5},{"after":"OFF","before":"READY","kind":"diesel_state_changed","time_minute":120.5}]。

C−M各单元绝对ENS差与状态：{"P0":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-5.577734408627436e-06},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P1":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-4.922205867933626e-06},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P2":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":true,"classification":"C_ENS_higher","difference_C_minus_M":1.8792382963983556e-05},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P3":{"c":{"both_have_material_interruption":true,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":true,"classification":"C_ENS_higher","difference_C_minus_M":54.021533584577625},"h":{"both_have_material_interruption":true,"classification":"C_ENS_lower","difference_C_minus_M":-1.1469936112007417e-10}}}。实质中断双方均存在的单元明确标为共同受限；无实质中断的微小数值差不作为醒目算法优势。
正常[45,60)实际功能：{"C":{"AUX_CHW_PUMP_actual_kW_e":0.1359449545930043,"AUX_HEAT_PUMP_actual_kW_e":0.015300021465376538,"GRID_actual_kW_e":246.38590187538648,"GT_actual_kW_e":[131.180288,0.0,0.0],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":304.1835365530078},"M":{"AUX_CHW_PUMP_actual_kW_e":0.1359449545930043,"AUX_HEAT_PUMP_actual_kW_e":0.015300021465376538,"GRID_actual_kW_e":246.38590187538645,"GT_actual_kW_e":[131.180288,0.0,0.0],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":304.18353655300837}}。故障对象是否承担功能依据这些实际输出与已锁定对象核对，不用容量或权限上限代替。

### b02_L1_heat_pump（L1）

原生起始小时 1016；硬件 H0；实际注册故障 {}、辅助故障 {"AUX_HEAT_PUMP":0.5}。准备期、共同终点和恢复尾段按本背景预登记执行。

|方法/工况|P0 ENS（电/热/冷，各自kWh）|P1 ENS（电/热/冷，各自kWh）|纯费用 USD2001|相对本方法实际正常费用差|
|---|---|---|---:|---:|
|C正常|0.00010602385647473511/0.0/0.0|0.00010602385647828783/0.0/0.0|72.07342369782435|0.0|
|C扰动|0.0022452932513115087/22.409228565359957/0.0|0.0019406789583022999/19.218339912503154/0.0|71.48838469924186|-0.5850389985824904|
|M正常|0.00010602385647473511/0.0/0.0|0.00010602385647828783/0.0/0.0|72.0962896155383|0.0|
|M扰动|0.0022452932513115087/22.409228565359957/0.0|0.0019406789583022999/19.21833991250315/0.0|73.31214683742454|1.215857221886239|

C扰动：P0热 ENS 22.409228565359957 kWh_h，实质中断 60.0 min，实际恢复/确认 120.0/121.0 min；P1热 ENS 19.218339912503154 kWh_h，实质中断 60.0 min，实际恢复/确认 120.0/121.0 min。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [127.45702399999996,0.0,0.0] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[]。

M扰动：P0热 ENS 22.409228565359957 kWh_h，实质中断 60.0 min，实际恢复/确认 120.0/121.0 min；P1热 ENS 19.21833991250315 kWh_h，实质中断 60.0 min，实际恢复/确认 120.0/121.0 min。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,0.0,0.0] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after_kW":0.0,"before_kW":126.83648,"device":"GT_1","kind":"gt_actual_stopped","reason":"actual_execution","time_minute":75.0}]。

C−M各单元绝对ENS差与状态：{"P0":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"h":{"both_have_material_interruption":true,"classification":"same","difference_C_minus_M":0.0}},"P1":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"h":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":3.552713678800501e-15}},"P2":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P3":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.1367989882771212e-13},"h":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":-7.105427357601002e-15}}}。实质中断双方均存在的单元明确标为共同受限；无实质中断的微小数值差不作为醒目算法优势。
正常[45,60)实际功能：{"C":{"AUX_CHW_PUMP_actual_kW_e":0.36264335093262223,"AUX_HEAT_PUMP_actual_kW_e":0.018722586074605363,"GRID_actual_kW_e":201.72505167772832,"GT_actual_kW_e":[125.20755199999999,0.0,0.0],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":463.21642001966745},"M":{"AUX_CHW_PUMP_actual_kW_e":0.36264335093262223,"AUX_HEAT_PUMP_actual_kW_e":0.018722586074605363,"GRID_actual_kW_e":201.72505167772823,"GT_actual_kW_e":[125.20755199999999,0.0,0.0],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":463.2164200196681}}。故障对象是否承担功能依据这些实际输出与已锁定对象核对，不用容量或权限上限代替。

### b03_L2_tower（L2）

原生起始小时 5816；硬件 H2；实际注册故障 {"TOWER":0.01}、辅助故障 {}。准备期、共同终点和恢复尾段按本背景预登记执行。

|方法/工况|P0 ENS（电/热/冷，各自kWh）|P1 ENS（电/热/冷，各自kWh）|纯费用 USD2001|相对本方法实际正常费用差|
|---|---|---|---:|---:|
|C正常|0.00020811617647029595/0.0/0.0|0.00020811617646319063/0.0/0.0|191.2195095907656|0.0|
|C扰动|0.0001412175418475389/0.0/10.645878318739957|0.00014121754184344065/0.0/15.986279851973755|181.15513135165375|-10.064378239111846|
|M正常|0.00019237636964063357/0.0/0.0|0.0001923763696351795/0.0/0.0|191.3303502287276|0.0|
|M扰动|0.00013917029292751602/0.0/10.645878318739959|0.00013917029292341776/0.0/15.986279851973801|183.44890768093188|-7.881442547795729|

C扰动：P0冷 ENS 10.645878318739957 kWh_c，实质中断 25.392823832342543 min，实际恢复/确认 120.0/121.0 min；P1冷 ENS 15.986279851973755 kWh_c，实质中断 25.392823832342543 min，实际恢复/确认 120.0/121.0 min。UPS实际放出 0.0 kWh_e，蓄冷实际放出 56.26566829760339 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 59.717265417997744 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,120.553472,120.55347210000002] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[]。

M扰动：P0冷 ENS 10.645878318739959 kWh_c，实质中断 25.392823832342543 min，实际恢复/确认 120.0/121.0 min；P1冷 ENS 15.986279851973801 kWh_c，实质中断 25.392823832342543 min，实际恢复/确认 120.0/121.0 min。UPS实际放出 0.0 kWh_e，蓄冷实际放出 56.26566829760339 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 59.734419428664005 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,0.0,120.553472] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after_kW":0.0,"before_kW":121.716992,"device":"GT_2","kind":"gt_actual_stopped","reason":"actual_execution","time_minute":105.0}]。

C−M各单元绝对ENS差与状态：{"P0":{"c":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":-1.7763568394002505e-15},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":2.0472489200228886e-06},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P1":{"c":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":-4.618527782440651e-14},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":2.0472489200228886e-06},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P2":{"c":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":3.552713678800501e-14},"e":{"both_have_material_interruption":true,"classification":"C_ENS_higher","difference_C_minus_M":2.047248706504856e-06},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P3":{"c":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":-1.1368683772161603e-13},"e":{"both_have_material_interruption":true,"classification":"C_ENS_higher","difference_C_minus_M":0.00020472489206713185},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}}}。实质中断双方均存在的单元明确标为共同受限；无实质中断的微小数值差不作为醒目算法优势。
正常[45,60)实际功能：{"C":{"AUX_CHW_PUMP_actual_kW_e":0.7862671089336318,"AUX_HEAT_PUMP_actual_kW_e":0.006393384697334543,"GRID_actual_kW_e":487.749068533223,"GT_actual_kW_e":[0.0,124.12159999999996,124.12160010000004],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":1260.9362102188059},"M":{"AUX_CHW_PUMP_actual_kW_e":0.7862671089336318,"AUX_HEAT_PUMP_actual_kW_e":0.006393384697334543,"GRID_actual_kW_e":487.74906866348186,"GT_actual_kW_e":[0.0,124.12159999999996,124.12159999999996],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":1260.9362099748137}}。故障对象是否承担功能依据这些实际输出与已锁定对象核对，不用容量或权限上限代替。

### b04_L2_chw_pump（L2）

原生起始小时 5072；硬件 H0；实际注册故障 {}、辅助故障 {"AUX_CHW_PUMP":0.5}。准备期、共同终点和恢复尾段按本背景预登记执行。

|方法/工况|P0 ENS（电/热/冷，各自kWh）|P1 ENS（电/热/冷，各自kWh）|纯费用 USD2001|相对本方法实际正常费用差|
|---|---|---|---:|---:|
|C正常|0.00020693371175184438/0.0/0.0|0.0002069337117589498/0.0/0.0|194.85272099829677|0.0|
|C扰动|0.05711849492724852/0.0/28.138893763226825|0.03759566922778035/0.0/18.486199103627374|181.9553203226089|-12.897400675687862|
|M正常|0.00018943169682117397/0.0/2.938875611135927e-05|0.00018943128767112682/0.0/2.9389443154670403e-05|194.90283896600178|0.0|
|M扰动|0.057118494927252086/0.0/28.138893763226825|0.03759566922778035/0.0/18.486199103627374|185.75779168055794|-9.145047285443837|

C扰动：P0电 ENS 0.05711849492724852 kWh_e，实质中断 60.0 min，实际恢复/确认 120.0/120.0 min；P0冷 ENS 28.138893763226825 kWh_c，实质中断 60.0 min，实际恢复/确认 120.0/121.0 min；P1电 ENS 0.03759566922778035 kWh_e，实质中断 60.0 min，实际恢复/确认 120.0/120.0 min；P1冷 ENS 18.486199103627374 kWh_c，实质中断 60.0 min，实际恢复/确认 120.0/121.0 min。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,118.924544,118.924544] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[]。

M扰动：P0电 ENS 0.057118494927252086 kWh_e，实质中断 60.0 min，实际恢复/确认 120.0/120.0 min；P0冷 ENS 28.138893763226825 kWh_c，实质中断 60.0 min，实际恢复/确认 120.0/121.0 min；P1电 ENS 0.03759566922778035 kWh_e，实质中断 60.0 min，实际恢复/确认 120.0/120.0 min；P1冷 ENS 18.486199103627374 kWh_c，实质中断 60.0 min，实际恢复/确认 120.0/121.0 min。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,0.0,118.924544] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after_kW":0.0,"before_kW":121.32915200000001,"device":"GT_2","kind":"gt_actual_stopped","reason":"actual_execution","time_minute":75.0}]。

C−M各单元绝对ENS差与状态：{"P0":{"c":{"both_have_material_interruption":true,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":true,"classification":"C_ENS_lower","difference_C_minus_M":-3.566591466608315e-15},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P1":{"c":{"both_have_material_interruption":true,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":true,"classification":"same","difference_C_minus_M":0.0},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P2":{"c":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":7.105427357601002e-15},"e":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":6.938893903907228e-18},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P3":{"c":{"both_have_material_interruption":true,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":-3.410605131648481e-13},"e":{"both_have_material_interruption":true,"classification":"C_ENS_lower","difference_C_minus_M":-1.8451906669270102e-13},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}}}。实质中断双方均存在的单元明确标为共同受限；无实质中断的微小数值差不作为醒目算法优势。
正常[45,60)实际功能：{"C":{"AUX_CHW_PUMP_actual_kW_e":1.1873837239418763,"AUX_HEAT_PUMP_actual_kW_e":0.006351827369355283,"GRID_actual_kW_e":521.6144127890041,"GT_actual_kW_e":[0.0,123.57862399999999,123.57862399999999],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":1478.193625710474},"M":{"AUX_CHW_PUMP_actual_kW_e":1.1873837239418763,"AUX_HEAT_PUMP_actual_kW_e":0.006351827369355283,"GRID_actual_kW_e":521.6144127890041,"GT_actual_kW_e":[0.0,123.57862399999999,123.57862399999999],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":1478.193625710474}}。故障对象是否承担功能依据这些实际输出与已锁定对象核对，不用容量或权限上限代替。

### b05_L3_ER（L3）

原生起始小时 3416；硬件 H0；实际注册故障 {}、辅助故障 {}。准备期、共同终点和恢复尾段按本背景预登记执行。

|方法/工况|P0 ENS（电/热/冷，各自kWh）|P1 ENS（电/热/冷，各自kWh）|纯费用 USD2001|相对本方法实际正常费用差|
|---|---|---|---:|---:|
|C正常|0.00020641914709074172/0.0/0.0|0.00020641914709074172/0.0/0.0|163.48747879368625|0.0|
|C扰动|0.0002069227318202138/0.0/0.0|0.0002069227318202138/0.0/0.0|163.96115334545777|0.4736745517715235|
|M正常|0.0001889235623195873/0.0/1.0859911656169174e-05|0.0001889231110752121/0.0/1.0860191753891968e-05|168.56011316508238|0.0|
|M扰动|0.00018936216777060534/0.0/1.089150063205223e-05|0.00018936170067872037/0.0/1.0891790334208158e-05|169.00472413675644|0.4446109716740523|

C扰动：P0/P1未观察到实质供能中断；原始微小ENS仍完整保留在结果表。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [125.595392,0.0,125.595392] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after_kW":126.9140479999997,"before_kW":0.0,"device":"GT_1","kind":"gt_actual_started","reason":"actual_execution","time_minute":10.0},{"actual_kW":126.9140479999997,"device":"GT_1","kind":"gt_start_completed","time_minute":10.0}]。

M扰动：P0/P1未观察到实质供能中断；原始微小ENS仍完整保留在结果表。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,0.0,125.595392] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[]。

C−M各单元绝对ENS差与状态：{"P0":{"c":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.089150063205223e-05},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":1.7560564049608467e-05},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P1":{"c":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.0891790334208158e-05},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":1.756103114149344e-05},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P2":{"c":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.0891790334208158e-05},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":1.756103113972321e-05},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P3":{"c":{"both_have_material_interruption":false,"classification":"C_ENS_lower","difference_C_minus_M":-10.593407344124453},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":2.5384569107486654e-07},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}}}。实质中断双方均存在的单元明确标为共同受限；无实质中断的微小数值差不作为醒目算法优势。
正常[45,60)实际功能：{"C":{"AUX_CHW_PUMP_actual_kW_e":0.3982769639410747,"AUX_HEAT_PUMP_actual_kW_e":0.007732582812049483,"GRID_actual_kW_e":417.2169969738647,"GT_actual_kW_e":[126.9140481,0.0,126.91404799999997],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":710.9805410222049},"M":{"AUX_CHW_PUMP_actual_kW_e":0.3982769639410747,"AUX_HEAT_PUMP_actual_kW_e":0.007732582812049483,"GRID_actual_kW_e":567.108730890638,"GT_actual_kW_e":[0.0,0.0,126.91404799999997],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":525.7001538012403}}。故障对象是否承担功能依据这些实际输出与已锁定对象核对，不用容量或权限上限代替。

### b06_L3_OR（L3）

原生起始小时 6704；硬件 H0；实际注册故障 {}、辅助故障 {}。准备期、共同终点和恢复尾段按本背景预登记执行。

|方法/工况|P0 ENS（电/热/冷，各自kWh）|P1 ENS（电/热/冷，各自kWh）|纯费用 USD2001|相对本方法实际正常费用差|
|---|---|---|---:|---:|
|C正常|0.00015443687735228195/0.0/0.0|0.00015443687734517653/0.0/0.0|104.23905801238833|0.0|
|C扰动|0.00015563657518669055/0.0/0.0|0.00015563657518669055/0.0/0.0|105.08013411723205|0.8410761048437223|
|M正常|0.0001412838679897277/0.0/1.0805888120302143e-05|0.0001413468055659639/0.0/1.075418159151102e-05|104.39601090767607|0.0|
|M扰动|0.00014218401843280049/0.0/1.0937319915527155e-05|0.00014218341578242644/0.0/1.0937809887145987e-05|105.18177940576425|0.7857684980881743|

C扰动：P0/P1未观察到实质供能中断；原始微小ENS仍完整保留在结果表。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,0.0,124.81971199999998] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[]。

M扰动：P0/P1未观察到实质供能中断；原始微小ENS仍完整保留在结果表。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,0.0,124.81971199999998] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[]。

C−M各单元绝对ENS差与状态：{"P0":{"c":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.0937319915527155e-05},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":1.3452556753890067e-05},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P1":{"c":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.0937809887145987e-05},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":1.3453159404264116e-05},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P2":{"c":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.093780988625781e-05},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":1.3453159402487759e-05},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P3":{"c":{"both_have_material_interruption":false,"classification":"C_ENS_lower","difference_C_minus_M":-14.683145971446125},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":8.074870536459167e-08},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}}}。实质中断双方均存在的单元明确标为共同受限；无实质中断的微小数值差不作为醒目算法优势。
正常[45,60)实际功能：{"C":{"AUX_CHW_PUMP_actual_kW_e":0.4397760609243857,"AUX_HEAT_PUMP_actual_kW_e":0.009524479611400422,"GRID_actual_kW_e":396.7248117520903,"GT_actual_kW_e":[0.0,0.0,126.13836799999999],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":513.6568106721104},"M":{"AUX_CHW_PUMP_actual_kW_e":0.4397760609243857,"AUX_HEAT_PUMP_actual_kW_e":0.009524479611400422,"GRID_actual_kW_e":396.7248117520902,"GT_actual_kW_e":[0.0,0.0,126.13836799999999],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":513.6568106721111}}。故障对象是否承担功能依据这些实际输出与已锁定对象核对，不用容量或权限上限代替。

### b07_L4_GT_ER（L4）

原生起始小时 3128；硬件 H0；实际注册故障 {"GT_3":0.0}、辅助故障 {}。准备期、共同终点和恢复尾段按本背景预登记执行。

|方法/工况|P0 ENS（电/热/冷，各自kWh）|P1 ENS（电/热/冷，各自kWh）|纯费用 USD2001|相对本方法实际正常费用差|
|---|---|---|---:|---:|
|C正常|0.00020697725722129695/0.0/0.0|0.00020697725722129695/0.0/0.0|164.28106493452972|0.0|
|C扰动|0.00018978166042771252/4.546657912385399e-06/0.0|0.00018978166042771252/3.6920258142680913/0.0|168.24705643882987|3.965991504300149|
|M正常|0.0001894160612216922/0.0/1.12198369741634e-05|0.00018941563466512434/0.0/1.1220109501053344e-05|167.16327475872373|0.0|
|M扰动|0.0001897816604277125/12.99693845167845/0.0|0.0001897926454077492/8.790220719501372/1.1288912604712209e-05|175.5826376577955|8.419362899071757|

C扰动：P1热 ENS 3.6920258142680913 kWh_h，实质中断 15.0 min，实际恢复/确认 75.0/76.0 min。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [125.75052799999995,125.75052799999995,0.0] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after_kW":0.0,"before_kW":128.23270399999998,"device":"GT_3","kind":"gt_actual_stopped","reason":"forced_fault_or_protection","time_minute":60.0},{"after_kW":125.98323210000002,"before_kW":0.0,"device":"GT_1","kind":"gt_actual_started","reason":"actual_execution","time_minute":100.0},{"after_kW":125.983232,"before_kW":0.0,"device":"GT_2","kind":"gt_actual_started","reason":"actual_execution","time_minute":100.0},{"actual_kW":125.98323210000002,"device":"GT_1","kind":"gt_start_completed","time_minute":100.0},{"actual_kW":125.983232,"device":"GT_2","kind":"gt_start_completed","time_minute":100.0}]。

M扰动：P0热 ENS 12.99693845167845 kWh_h，实质中断 15.0 min，实际恢复/确认 75.0/76.0 min；P1热 ENS 8.790220719501372 kWh_h，实质中断 15.0 min，实际恢复/确认 75.0/76.0 min。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 0.0 kWh；末端UPS 0.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2400.0 kWh。
真实末端GT出力 [0.0,0.0,0.0] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after_kW":0.0,"before_kW":128.23270399999998,"device":"GT_3","kind":"gt_actual_stopped","reason":"forced_fault_or_protection","time_minute":60.0}]。

C−M各单元绝对ENS差与状态：{"P0":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":2.710505431213761e-20},"h":{"both_have_material_interruption":false,"classification":"C_ENS_lower","difference_C_minus_M":-12.996933905020537}},"P1":{"c":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.1288912604712209e-05},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-1.0984980036683007e-08},"h":{"both_have_material_interruption":true,"classification":"C_ENS_lower","difference_C_minus_M":-5.098194905233281}},"P2":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"roundoff_only_same_material_outcome","difference_C_minus_M":2.710505431213761e-20},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P3":{"c":{"both_have_material_interruption":false,"classification":"C_ENS_lower","difference_C_minus_M":-66.85648801065737},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":1.770917360488164e-05},"h":{"both_have_material_interruption":true,"classification":"C_ENS_lower","difference_C_minus_M":-4.546657937254395e-06}}}。实质中断双方均存在的单元明确标为共同受限；无实质中断的微小数值差不作为醒目算法优势。
正常[45,60)实际功能：{"C":{"AUX_CHW_PUMP_actual_kW_e":0.24617008951755873,"AUX_HEAT_PUMP_actual_kW_e":0.006179892766755447,"GRID_actual_kW_e":548.1453940219961,"GT_actual_kW_e":[0.0,0.0,128.2883576603722],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":438.43509530939883},"M":{"AUX_CHW_PUMP_actual_kW_e":0.24617008951755867,"AUX_HEAT_PUMP_actual_kW_e":0.0061798927667554475,"GRID_actual_kW_e":548.2173479585307,"GT_actual_kW_e":[0.0,0.0,128.23270399999998],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":438.3036581619312}}。故障对象是否承担功能依据这些实际输出与已锁定对象核对，不用容量或权限上限代替。

### b08_L4_GRID_OR（L4）

原生起始小时 7112；硬件 H1；实际注册故障 {"GRID":0.0}、辅助故障 {}。准备期、共同终点和恢复尾段按本背景预登记执行。

|方法/工况|P0 ENS（电/热/冷，各自kWh）|P1 ENS（电/热/冷，各自kWh）|纯费用 USD2001|相对本方法实际正常费用差|
|---|---|---|---:|---:|
|C正常|0.0002069499557251228/0.0/0.0|0.0002069499557322281/0.0/0.0|169.34995334223711|0.0|
|C扰动|0.0002082255671315524/0.0/0.0|0.00020822556712444707/0.0/0.0|183.8682213923836|14.518268050146474|
|M正常|0.0001894040455425745/0.0/1.259685322274606e-05|0.00018940355970542617/0.0/1.2597202024622335e-05|175.23720218455364|0.0|
|M扰动|0.00020851865410520596/0.0/0.0|0.0002085186540868209/0.0/0.0|192.75845585409667|17.52125366954303|

C扰动：P0/P1未观察到实质供能中断；原始微小ENS仍完整保留在结果表。UPS实际放出 0.0 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 100.07881977758423 kWh；末端UPS 40.0 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2299.9211802224227 kWh。
真实末端GT出力 [125.20755199999999,125.20755199999999,0.0] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after_kW":125.97547519999934,"before_kW":0.0,"device":"GT_2","kind":"gt_actual_started","reason":"actual_execution","time_minute":10.0},{"actual_kW":125.97547519999934,"device":"GT_2","kind":"gt_start_completed","time_minute":10.0},{"after":"ISLAND","before":"GRID","kind":"grid_bus_state_changed","reason":"observed_grid_hardware_unavailable","time_minute":60.0},{"after":"STARTING","before":"OFF","kind":"diesel_state_changed","time_minute":60.0},{"after":"CONNECTED","before":"STARTING","kind":"diesel_state_changed","time_minute":75.0},{"after":"RETURN_PREP","before":"ISLAND","kind":"grid_bus_state_changed","reason":"healthy_grid_and_arrived_permission","time_minute":120.0},{"after":"TRANSFER_OPEN","before":"RETURN_PREP","kind":"grid_bus_state_changed","reason":"prepared_open_transition","time_minute":120.25},{"after":"READY","before":"CONNECTED","kind":"diesel_state_changed","time_minute":120.25},{"after":"GRID","before":"TRANSFER_OPEN","kind":"grid_bus_state_changed","reason":"finite_open_transfer_completed","time_minute":120.5},{"after":"OFF","before":"READY","kind":"diesel_state_changed","time_minute":120.5}]。

M扰动：P0/P1未观察到实质供能中断；原始微小ENS仍完整保留在结果表。UPS实际放出 10.823481712264273 kWh_e，蓄冷实际放出 0.0 kWh_c，DG实际轴功 141.0137314814703 kWh；末端UPS 39.999999999999986 kWh_e、冷库存 0.0 kWh_c、DG剩余轴功预算 2258.986268518524 kWh。
真实末端GT出力 [125.20755199999999,125.20755199999999,125.20755199999999] kW_e；待启动 {}；待命令/接触恢复/断载 [[],null,null]。
实际转供/启动事件链：[{"after":"ISLAND","before":"GRID","kind":"grid_bus_state_changed","reason":"observed_grid_hardware_unavailable","time_minute":60.0},{"after":"STARTING","before":"OFF","kind":"diesel_state_changed","time_minute":60.0},{"after":"CONNECTED","before":"STARTING","kind":"diesel_state_changed","time_minute":75.0},{"after_kW":225.0,"before_kW":0.0,"device":"GT_2","kind":"gt_actual_started","reason":"actual_execution","time_minute":90.0},{"after_kW":225.0,"before_kW":0.0,"device":"GT_3","kind":"gt_actual_started","reason":"actual_execution","time_minute":90.0},{"actual_kW":225.0,"device":"GT_2","kind":"gt_start_completed","time_minute":90.0},{"actual_kW":225.0,"device":"GT_3","kind":"gt_start_completed","time_minute":90.0},{"after":"RETURN_PREP","before":"ISLAND","kind":"grid_bus_state_changed","reason":"healthy_grid_and_arrived_permission","time_minute":120.0},{"after":"TRANSFER_OPEN","before":"RETURN_PREP","kind":"grid_bus_state_changed","reason":"prepared_open_transition","time_minute":120.25},{"after":"READY","before":"CONNECTED","kind":"diesel_state_changed","time_minute":120.25},{"after":"GRID","before":"TRANSFER_OPEN","kind":"grid_bus_state_changed","reason":"finite_open_transfer_completed","time_minute":120.5},{"after":"OFF","before":"READY","kind":"diesel_state_changed","time_minute":120.5}]。

C−M各单元绝对ENS差与状态：{"P0":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-2.930869736535722e-07},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P1":{"c":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0},"e":{"both_have_material_interruption":false,"classification":"raw_numerical_difference_without_material_interruption","difference_C_minus_M":-2.9308696237383093e-07},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P2":{"c":{"both_have_material_interruption":false,"classification":"C_ENS_lower","difference_C_minus_M":-10.3832286321794},"e":{"both_have_material_interruption":false,"classification":"C_ENS_lower","difference_C_minus_M":-13.563732976337292},"h":{"both_have_material_interruption":false,"classification":"same","difference_C_minus_M":0.0}},"P3":{"c":{"both_have_material_interruption":true,"classification":"C_ENS_lower","difference_C_minus_M":-30.585843267841184},"e":{"both_have_material_interruption":true,"classification":"C_ENS_lower","difference_C_minus_M":-5.063330896865892},"h":{"both_have_material_interruption":true,"classification":"C_ENS_lower","difference_C_minus_M":-8.11130740963195}}}。实质中断双方均存在的单元明确标为共同受限；无实质中断的微小数值差不作为醒目算法优势。
正常[45,60)实际功能：{"C":{"AUX_CHW_PUMP_actual_kW_e":0.39961472989937963,"AUX_HEAT_PUMP_actual_kW_e":0.008442336967905624,"GRID_actual_kW_e":424.99437707876046,"GT_actual_kW_e":[125.97547529999999,125.97547519999995,0.0],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":908.1564083508712},"M":{"AUX_CHW_PUMP_actual_kW_e":0.39961472989937963,"AUX_HEAT_PUMP_actual_kW_e":0.008442336967905624,"GRID_actual_kW_e":587.9029317477091,"GT_actual_kW_e":[125.97547519999995,0.0,0.0],"complete":true,"interval_minutes":[45.0,60.0],"observed_minutes":15.0,"pump_columns":"auxiliary KINDS=(fan,chw_pump,heat_pump); all are electricity, not e/h/c","tower_actual_rejection_kW":610.3468159790299}}。故障对象是否承担功能依据这些实际输出与已锁定对象核对，不用容量或权限上限代替。

费用、末库存与服务应联合解释。低费用若伴随少供服务、较高ENS或更低末端库存，表示资源与服务取舍，本报告不据此宣称净节费。C/M是完整组合策略的比较，不能把组合差异当成某一个开关的单因素因果。原始微小ENS、0对0相同、改善、变差和共同中断均保留；全部百分比留空。

## G1五项修补与实际最小停机反例

1. 纯费用单列actual_fee_USD2001，来源能源费加一次DG轴功/端电差额及一次初始库存准备费，实际回充不重复收费；旧优化目标费用保留。
2. 初始实际库存、准备充入、准备购电、实际充放、损失、末库存与闭合残差分列，电与冷的单位保持独立。
3. 原始请求、交付、主动断载与连接后缺供逐P0—P3、逐电热冷积分；原实质中断判据和恢复确认规则不放宽，无中断恢复为not_applicable，未知不补0。
4. 终态取实际积分结束后的core/state、时钟、库存和队列，保留未完成启动的实际供电进度及剩余需求；不以最后一次决策替代。
5. critical_preparation_first=false表示准备目标次序后移；完整C/M配置和factory/legacy影响另有展开记录，不能把它当成关闭全部准备。

物理核心确有一项最小修正：powered-start ready的两处投影由now+remaining改为max(now,eligible_start)+remaining_actual_powered_work。旧M反例在90 min保存started=105、ready=100、实际启动进度0，90 min之后仍有90 min未知尾段；原失败不删除。修补保留最小停机、原爬坡、实际启动供电进度、容量、价格与服务/保护阈值，没有扩大权限。新独立fix重放和最终32条轨迹均绑定修正核心。

## 方法配置、信息与因果边界

C/M保留正式采用版V28_adopted请求、thermal_coupling=null、research_stress_proxy=false与online_GT_count_order=after_cost。完整实际配置来自各RESULT并与单一C02注册核对。C/F历史默认同时改变准备次序、储备规则和legacy次序，不能作为单因素证据；F没有冒充已通过本轮正式C/M入口。
准备前物理state/core与RNG分别列报；RNG不同不伪造完整快照相同。同方法正常/扰动60 min前的实际请求、观测、命令、队列和状态经最终审计核对，只归一明确求解记录的三个计时/预算键，物理时钟和命令不删除。有限前缀核验支持本批因果边界，不等于全项目形式证明。

## 来源、默认测试与旧证据

实际producer：`3252727135645037cedc57ebc188a48d2aa34ccb`；完整源码摘要：`2509d2fa562e672af3cfe4058ace55f0e38de6172d1ac2d633210d0e776f0776`；原输入摘要：`f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15`。本次冻结记录包含 858 个源码文件与 36 个输入文件；计数来自实际冻结文件。
修复后默认测试实际退出 0，stdout记录 1363 passed，pytest耗时 488.06 s。测试COMMAND没有内嵌source/git字段；本报告分别引用真实测试日志与随后C02源码冻结，不伪造测试日志内的源码摘要。
最终主比较只含N02/D02。旧N01/D01、原R01/R02、开发接入与失败证据保留为排除角色及原producer，见最终审计excluded_prior_attempts；全局尝试索引由根任务单独交付。

## 两篇全文映射与共同离线参考的完成层级

Hirwa与Rosales分别保留全文/附录、页码和实现边界映射。Hirwa提供共同R_off的主要多时段数学来源；Rosales提供设计、服务和信息边界参照。两篇均涉及REopt家族配置/调度设计，不能计作两个未知故障在线控制器。
Hirwa的当前完成层级：{"R_off_onoff_preparation_replayed_on_common_core":{"grid":true,"normal":true,"tower":false},"exact_paper_numerical_reproduction":false,"fixed_Hospital_static_envelope_verified":true,"full_continuous_or_storage_plan_replayed":false,"mapping_completed":true,"original_author_software_executed":false,"printed_equation_checked":true,"synthetic_multiperiod_prototype_verified":true}。Rosales当前完成层级：{"boundary_reference_used_in_shared_R_off":true,"exact_paper_numerical_reproduction":false,"independent_Rosales_algorithm_implemented":false,"independent_Rosales_algorithm_replayed":false,"mapping_completed":true,"numbered_equation_check_not_applicable":true}。Rosales原PDF没有编号方程，编号方程核对记为不适用；原映射阶段prototype/replayed=false与后续R03有限接入证据分层保留。
共同参考规范名称为“REopt类固定容量离线规划参考”；实际执行身份为“离线启停/准备计划＋共同实时分配”。R03正常和外网背景各自真实完成180 min，绑定同一修正producer/source。塔背景的静态硬服务规划已证明infeasible，未进行实际重放，实际供能/费用保持null。完整连续出力与储能功率计划没有重放；未运行原作者程序或官方REopt，未完成原论文精确数值复现，也不据离线事前信息参考与C/M做同信息排名或上下界结论。

R03 normal：{"core_execution_status":"completed","export_status":"completed","observed_until_minute":180.0,"status":"completed","unknown_tail_minutes":0.0}；独立实际能源费账：{"DG_budget_receipt_count":187,"DG_chemical_kWh":null,"DG_electrical_receipt_kWh_e":0.0,"DG_liters":null,"DG_loss_kWh":0.0,"DG_rotor_delta_kWh":0.0,"DG_shaft_fee_USD2001":0.0,"DG_shaft_work_kWh":0.0,"DG_terminal_energy_kWh_e":0.0,"DG_terminal_zero_does_not_imply_shaft_zero":true,"GT_gas_MMBtu":6.3914532601349485,"GT_gas_fee_USD2001":30.93463377905315,"actual_fee_USD2001":103.2194328928529,"boiler_gas_MMBtu":0.0,"boiler_gas_fee_USD2001":0.0,"grid_fee_USD2001":69.18753595590502,"grid_purchase_kWh_e":940.5592163662982,"initial_preparation_counted_once":true,"objective_or_cost_USD_step_used":false,"operating_fee_USD2001":100.12216973495816,"preparation_fee_USD2001":3.097263157894737,"preparation_grid_purchase_kWh_e":42.10526315789474,"preparation_input_by_carrier":{"cold_store_kWh_c":0.0,"ups_kWh_e":42.10526315789474},"producer_aggregate_used_for_calculation":false,"recharge_added_again":false,"segment_count":187,"unknown_fields":[]}。规划与实际分别保留，成本差不作为节费证明。

R03 grid：{"core_execution_status":"completed","export_status":"completed","observed_until_minute":180.0,"status":"completed","unknown_tail_minutes":0.0}；独立实际能源费账：{"DG_budget_receipt_count":260,"DG_chemical_kWh":null,"DG_electrical_receipt_kWh_e":0.0,"DG_liters":null,"DG_loss_kWh":16.430543682081172,"DG_rotor_delta_kWh":-7.099124924364277e-17,"DG_shaft_fee_USD2001":4.929163104624354,"DG_shaft_work_kWh":16.43054368208118,"DG_terminal_energy_kWh_e":0.0,"DG_terminal_zero_does_not_imply_shaft_zero":true,"GT_gas_MMBtu":12.385526181206984,"GT_gas_fee_USD2001":59.9459467170418,"actual_fee_USD2001":105.98169127612638,"boiler_gas_MMBtu":0.0,"boiler_gas_fee_USD2001":0.0,"grid_fee_USD2001":38.00931829656549,"grid_purchase_kWh_e":516.7117767341698,"initial_preparation_counted_once":true,"objective_or_cost_USD_step_used":false,"operating_fee_USD2001":102.88442811823164,"preparation_fee_USD2001":3.097263157894737,"preparation_grid_purchase_kWh_e":42.10526315789474,"preparation_input_by_carrier":{"cold_store_kWh_c":0.0,"ups_kWh_e":42.10526315789474},"producer_aggregate_used_for_calculation":false,"recharge_added_again":false,"segment_count":260,"unknown_fields":[]}。规划与实际分别保留，成本差不作为节费证明。

R03外网参考的DG端电收据为 0.0 kWh_e，但实际轴功为 16.430543682081172 kWh。请求、排队、到达与有效DG正出力上限均为0；实际自动孤岛支持状态机仍经历启动/连接/返回过程并发生轴侧损失。共同v23准备改变的是锅炉/EC上限，不是DG正出力请求，也没有调用C控制器。轴功预算、端电、柴油化学能与油量不能混称。

## 限定范围与交付复现

八个背景属于有限确定性工程比较，不解释为年度可靠率；积分小段和重复运行不是独立统计样本。没有新增MPC、机器学习、容量优化、未来故障脚本、执行快通道或临床阈值。室温/湿度/洁净度、临床结局、完整水力网络和高温工艺动态仍不在本轮新增资格内。
条件CO2仅在共同因子及其边界下列报；DG实际化学燃料未知，条件换算不等于精确柴油油耗或排放。价格保持历史/示意USD2001口径，短窗能源费不冒充25年LCC或NPV。
本目录的MAIN_RESULT_TABLE.csv保留全部32条原始精度的逐载能数值、实际正常基线、扰动差、条件CO2、库存与真实终态；COMPLETION_MATRIX.json给出门槛、SHA、层级与限制。根任务另交付全尝试索引、物理patch diff、无损多容器恢复工具及最终ZIP manifest。
复现报告：`python -B engineering_report_tools/build_engineering_report.py --root ROOT --audit FINAL_AUDIT/CAMPAIGN_AUDIT.json --output FRESH_OUTPUT`。原物理重放按各INVOCATION/冻结注册执行，报告生成本身不调用模型或求解器。最终ZIP须独立全量恢复、逐原字节SHA及严格大小检查通过后再作交付声明。

本文为工程验收报告，不是论文正文或论文图表。
