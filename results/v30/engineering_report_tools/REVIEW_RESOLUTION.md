# 工程报告生成器审查处置回查

本回查只读取当前代码、实际RED/GREEN/运行记录与已生成CSV。没有运行报告生成器、测试、模型或求解器；没有修改源码、旧CODE_REVIEW或已有报告。

当前冻结源码SHA：`build_engineering_report.py = 38b54c318d137882fd08f4bc8196cb30aca6bf2dcabd2985da9c21519a1e42a1`；测试SHA：`6d03f5f13050c3e62d382b1ebddb413b15debfbcc9da09d591445d81ebc9702b`。

## 首轮额外四项保护

|首轮问题|当前只读核对|处置结论|
|---|---|---|
|完整默认测试可被目录子集/合并-k等冒充|default_test_gate现只接受-q/--quiet及明确--basetemp两种写法，其他pytest tail拒绝；passed仍从真实日志动态解析|关闭|
|G1支持产物只哈希未读成功状态|validate_supporting_status核对历史passed、方法status/source unchanged、三个causality真值与impact明确完成状态；方法相关四份源SHA另与C02核对|关闭|
|R03费用等数值文件未指纹闭合|采用目录file_manifest逐文件长度/SHA验证，R03 producer_files逐文件SHA验证；normal/grid_AUDIT及manifest已进入支持evidence_sha并在报告后复核，最终audit本身前后SHA也核对|关闭|
|CO2两边因子SHA都是None也出比较差|conditional_CO2_delta要求非空且相同factor SHA，否则null|关闭|

## 原计划三项修补及剩余的有界文本路径

- validate_final_gate现逐条要求主32个审计条目的observed_until与requested_end精确为180；反例重放门槛也显式检查observed/requested180、真实退出0。所有原生文件仍按已采用审计SHA复核，终态在raw/RESULT/EVALUATION间相同并等于最后post_state。
- check_native_service对P0—P3各e/h/c的请求、交付、ENS、主动切除、连接缺供逐项与RESULT.reliability_v23核对；中断/恢复各字段也一致后才复制表格。
- compare_cell现在对不相等ENS且任一material_minutes为None时输出materiality_unknown，both_have_material_interruption也保留None。

**仍保留一条未完全关闭的通用helper路径**：critical_description仍使用`(material_minutes or 0)>0`。若直接传入六个关键单元的material_minutes全部None，它仍会返回“P0/P1未观察到实质供能中断”。因此不能声称当前代码对任意缺失materiality输入的所有文字路径都已拒绝/标未知。此项已反馈父任务；未自行修改冻结代码。

独立只读检查本次实际`actual_final01/MAIN_RESULT_TABLE.csv`确认：32条observed/requested/terminal时刻全部180；384个material_minutes及384个longest_material_minutes单元均有已知值，null数0。所以这条残留helper空值路径**没有影响本次实际报告**。其适用范围仍限定为经过最终完整原生门槛的这32条，不能泛化为缺失服务观测的通用报告器。

## 已有实际运行与QA记录

- `RED_review_guards.log`实际12 tests、5 failures；`GREEN_FINAL.log`实际12 tests、OK，`GREEN_FINAL_COMMAND.json`退出0。本次只读取，未冒充重新运行。
- `ACTUAL_FINAL01_COMMAND.json`记录采用最终CAMPAIGN_AUDIT一次实际报告导出，退出0、约31.109s，无新仿真。
- `ACTUAL_REPORT_QA.json`记录32 rows、221 columns、384个ENS值精确repr、32个纯费repr核验；队列/状态单格最大111 bytes，不含MB级diagnostic；UTF8有效。
- `ACTUAL_SCHEMA_CHECK.json`记录最终同源32条及R03正常/网故障、source2509/inputf414/FIX01、858个source文件/36个input文件；默认测试计数1363来自真实日志，未硬编码或宣称测试日志含源SHA。

当前实际报告的DG0端电/轴功损失、文献完成层级、无百分比和不跨载能合计表述与首轮复核一致。根任务拟补充b01“费用更低但P3电ENS更高”的取舍，以及b07“union时长相同但逐热单元不同”的解释，应直接引用逐单元原始值；不应改冻结数值、生成器或物理参数。最终ZIP/全量恢复验收仍在报告冻结范围之外。
