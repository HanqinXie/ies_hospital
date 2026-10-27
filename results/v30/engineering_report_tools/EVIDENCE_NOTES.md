# 最终中文工程报告：文献、R03参考与测试证据提取

本文件是只读取证建议，不是新运行结果。未修改旧文献/规划/审计/冻结产物、D盘源码；未构造环境或调用求解器/仿真。以下路径相对于 `E:/hospital_ies_v30_20261027`。

## 1. 可供生成器直接读取的入口

|路径|schema/状态字段|用途|
|---|---|---|
|`reference_replay_audit/FINAL_READY.json`|`adopted_audit=audit_02`，`mechanism_supplement=mechanisms_01`，`ready_for_parent_freeze=true`|采用哪一轮审计、测试命令/退出码、各产物核验、当前producer及缺口|
|`reference_replay_audit/audit_02/AUDIT_STATUS.json`|`schema=hospital_ies.reference_replay_audit.v30.v1`；`layer_status`|本方文献适配各完成层级的当前事实|
|`reference_replay_audit/audit_02/PAPER_COMPLETION.json`|Hirwa/Rosales分开，`frozen_mapping_status/completion`与`current_layers`分开|避免把初始mapper任务的未重放状态混作后续总任务状态，也避免同一个R_off算两次|
|`reference_replay_audit/audit_02/{normal,grid}_AUDIT.json`|`formal_status`、`observation`、`independently_recomputed_fee`、`actual_storage`、`actual_service`、`fee_validation`|180min正常/网故障参考结果及独立积分|
|`reference_replay_audit/audit_02/{normal,grid}_COMMAND_CHAIN.json`|逐请求/排队/到达/共同修改/执行证据|不能把计划值直接当实际供能|
|`reference_replay_audit/audit_02/{normal,grid}_TERMINAL_CHECK.json`|`all_checks_pass=true`，snapshot180，lastdecision165|终态一致性与无副作用producer回执边界|
|`reference_replay_audit/mechanisms_01/MECHANISMS.json`|`schema=hospital_ies.reference_replay_mechanisms.v30.v1`；`DG`、`grid_return_chain`、`interpretation`|DG零端电仍耗轴功、共同准备hook、返回时序的原生与代码证据|
|`offline_reference/hospital_tower.plan.json`|`status=infeasible`，`termination_reason=proved_infeasible`，`incumbent=false`|塔静态硬服务规划不可行；不是缺失轨迹被填成0|
|`offline_reference/hospital_tower.infeasibility_certificate.json`|`evidence_class=independent_necessary_condition_certificate_for_static_envelope`|静态必要条件证书；不是医院实际ENS/续航预测|
|`steps/full_default_after_fix01/{COMMAND.json,stdout.log}`|exit0；实际1363 passed|修复后完整默认测试证据|

读取JSON宜使用 `utf-8-sig`，部分保存文件带UTF-8 BOM。不要将超大 `error.diagnostic` 整体写入工程报告；保留原证据路径/SHA及有界错误摘要。

## 2. 两篇文献映射字段及可信结论

### Hirwa

`literature/hirwa/mapping.json` 的schema键叫 **`schema_version`**，值 `hospital-ies-v30-literature-mapping-1`，不是schema；完成层级在 `status`。PDF SHA `e051354256c9f389c0418aba285ef8cad8b70cda71d34194f981cca0d810e608`；本轮全文含附录23页。该原始映射记录 mapping/equation=true，prototype/replayed=false，是mapper当时的任务范围，冻结后未改写。

当前完成层级应读 `PAPER_COMPLETION.json.Hirwa.current_layers`：原式核对、人工多时段原型、本院固定容量静态包络已验证；一个共同R_off启停/准备适配在正常与网故障完成真实重放，塔未重放；完整连续出力/储能计划、原作者软件与原文精确数值复现仍false。

报告短句建议：“Hirwa提供主要数学来源，已完成全文/附录方程映射、人工多时段验证及固定本院硬件的离线参考原型；已实际执行的是启停/准备请求与共同实时分配，未运行原作者/官方REopt，也未逐值重放完整出力和库存计划。”

原文DG容量2500/1874kW及O&M单位疑点仍登记，不猜改。末端库存等式未明确，不能声称文献已解决本院短窗库存公平性。

### Rosales

`literature/rosales/mapping.json.schema=hospital_ies_v30_literature_mapping_v1`，完成层级在 `completion`。PDF SHA `0487864648dc930f041fc28ead25dad074f80f448ae8db54a3b592cb9e25ef1d`；全文/附录16页。该PDF无编号方程，`original_numbered_equations_present=false`、`equation_checked=null`表示不适用，不能写成方程核对失败，也不能编造式号。

当前 `PAPER_COMPLETION.json.Rosales.current_layers` 明确：独立全文映射完成，作为共同R_off的设计/服务/信息边界参照；独立Rosales算法 implemented/replayed均false，精确原文数值复现false。

报告短句建议：“Rosales的指定四小时已知停电、聚合50%关键电和停电冷需求置零属于原文设计边界；本院保留全部P0—P3原始电热冷请求，不将原文简化当作本院服务成功。该文未被实现为第二个未知故障在线控制器。”

两篇的原始8760、作者代码/版本、部分首尾库存和补充资料仍缺；不联系作者、不估读曲线补成精确数据。25年LCC/NPV、需量费和事后100美元/kWh避免停电价值不能改名为本院短窗能源费或临床缺供罚值。

## 3. R03正常/网故障的真实身份与绑定

两条采用的实际轨迹为 `runs/r03_normal_R_off` 和 `runs/r03_grid_R_off`。各RESULT：

- `schema=hospital_ies.model_correctness_validation.v25.v1`，`model_id=M25_model_correction`。
- `status=completed`、actual_episode_attempts=1、observed/requested_end均180、unknown_tail=0；两条INVOCATION的exit_code均0。
- `git_commit=3252727135645037cedc57ebc188a48d2aa34ccb`。
- `source_sha256=2509d2fa562e672af3cfe4058ace55f0e38de6172d1ac2d633210d0e776f0776`。
- `resolved_model_config.v30_physical_patch.patch_id=hospital_ies.powered_start_minimum_off_readiness.v30.fix1`。
- `evaluation_role=offline_known_window_reference_replay`，policy alias R_off，execution_identity `offline_onoff_preparation_plus_common_realtime_allocation`。
- `offline_reference.full_continuous_plan_replayed=false`、`planned_storage_ports_executed=false`、`causal_comparison_eligible=false`、information_permission `offline_known_window`。

字段类型注意：RESULT的 `input_sha256` 是文件→SHA的**映射**，不是aggregate字符串；其规范JSON聚合digest为 `f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15`，与C02 freeze.input_sha256一致。C02的 `physical_patch` 是顶层字符串；实际RESULT使用上述嵌套patch_id。

动作请求表SHA：normal `b0665568b98995e5760b14771826ab79e7101cd2ec68fa15706e5106a28a2ac5`；grid `b179093be65368211ed56f88369ea415f4f1eee4f7c97fa959792d0ff1a106bf`。它们是冻结45min准备提前量，不是通信延迟。实际scenario.response_delay_minutes=15：GT2在15min请求/到达，25min实际启动完成；60min故障期决策75min到达。

|180min参考|独立纯费 USD2001|一次准备费 USD2001|实际UPS末 kWh_e|规划UPS末 kWh_e|
|---|---:|---:|---:|---:|
|normal|103.2194328928529|3.097263157894737|40|40|
|grid|105.98169127612638|3.097263157894737|22.9510700141808|0|

两条 fee_validation 的 own-minus-readonlyCLI 与 own-minus-producer_actual_fee均0；费用未以优化目标或cost_USD_step积分。低费用不能脱离实际服务、库存和共同终点解释为净节费。规划输入仍为历史C条件静态包络，不能把R03称为同信息策略排名、费用下界或性能上界。

R03是真正修复后重放；R01正式failed_unknown与unknown_tail180min继续保留，不能因另有时间跨度0–180的ACTUAL_PREFIX而升级正式成功，也不补机械/队列未知。R02保留此前开发身份。Cdev仅是不同producer的返回事件描述性参照，不混入同版因果对比。

## 4. DG端电0但实际耗轴功的准确解释

grid RESULT.metrics.actual_energy：

```text
DG_electrical_work_kWh = 0.0
DG_mechanical_work_kWh = 16.43054368208118
DG_loss_work_kWh = 16.430543682081172
DG_fuel_budget_debit_kWh_equivalent = 16.43054368208118
```

轴功差额费4.929163104624354 USD2001。不要因Pe=0写轴功0，也不要把上述轴功预算称作柴油化学能、油量或升数；化学能和柴油升数仍null。

机制审计确认 requested/queued/arrived/effective DG ceiling/actual terminal五种最大值全部0；不存在R_off偷偷申请正DG供能。60min外网不可用使母线进入ISLAND，原共同automatic island-support状态机在硬件、剩余预算和专用DC启动资格满足时自行STARTING，75min计时后CONNECTED。STARTING/READY走spin_up，CONNECTED的本地转子模型即使Pe0也有轴功/损失。该路径来自共同物理执行器；v23准备hook只补当前关键热锅炉与冷EC ceiling，没有改DG权限，也未调用C控制器。

“自动孤岛支撑启动”比笼统写“策略增加DG发电”准确。R03 120min首次返回目的路径探测失败、同刻新命令执行，121min下一原生边界探测通过，继而.25+.25min转供，121.5min才到GRID。120..121时锅炉已经ready、restart power0，额外1min不能称锅炉热身；日志未给唯一活跃约束，不猜具体哪条约束造成域耗尽。

## 5. 塔不可行与实际未运行的分层

塔 plan schema为 `hospital_ies.offline_reference.v30.v1`：raw_status=2，solver_message含 `HiGHS Status 8: model_status is Infeasible`，termination_reason=proved_infeasible、incumbent=false，objective_fee/plan/audit均null；变量2002、二元455、约束1548。这与time_limit/no_incumbent不同。

独立必要条件证书在第15段约15min：原有用冷需求500.4978920618397kW_c；塔排热能力42.20223408kW；即使乐观冷机上界34.621913165228406kW_c加可交付冷库存功率上界79.16487809810818kW_c，也只有113.78679126333658，低于原需求，静态硬服务最少缺口386.71110079850314kW_c。证书明确 `hospital_actual_ENS_or_survival_prediction=false`、`loads_removed=false`。

因此无R_off塔实际重放；实际费用与供能为未知/null，不能写0。该证书只判本静态规划包络不可行，不是新医院生存时间、临床失效或实际ENS模拟。

静态normal/grid计划均optimal，raw_status0且有incumbent；冻结plan文件中的actual_replayed=false是原型阶段记录，后续启停/准备重放的状态另由R03 AUDIT_STATUS证明。原型使用SciPy milp/HiGHS（README登记Python3.10.6、SciPy1.15.3、NumPy2.2.6）；不要把原型求解器名称套成原论文实际软件版本。

## 6. 完整默认测试与producer冻结

`steps/full_default_after_fix01/COMMAND.json` 实际命令为 `python -B -m pytest -q --basetemp E:/hospital_ies_v30_20261027/temp/full_default_after_fix01`，cwd为D盘工作树，exit_code0。stdout明确 `1363 passed in 488.06s`，stderr为空；wrapper elapsed_seconds为488.84299999987707。命令08:19:43开始、08:27:52结束，均UTC。

`FROZEN_SOURCE_V30_C02.json` 08:38:54 UTC冻结producer325/source2509/inputf414，role为 `final_same_fixed_core_all32_C_M_after_actual_counterexample_and_default_gates`，physical_patch为FIX01，并声明原C/M policy/planner文件未改。测试COMMAND自身没有嵌入source/git摘要，报告应把“真实测试日志”和“后续源冻结”分列引用，不给测试日志虚构不存在的SHA字段。

## 7. 关键文件SHA

|相对路径|SHA256|
|---|---|
|`reference_replay_audit/FINAL_READY.json`|`2c18ec0d2d9d9e357def2427e881e9030d5ad0a3a6332cf7eb2ab89f9e37a1bf`|
|`reference_replay_audit/audit_02/AUDIT_STATUS.json`|`b856beec75fb290255c8bc7ff47a9de4e392d7187025f88b916f2a6b2274ef63`|
|`reference_replay_audit/audit_02/PAPER_COMPLETION.json`|`68cabf74eb9a7df99806f21d00e6e69ce9c8b05b99c9d10c57433bd83ee944bf`|
|`reference_replay_audit/mechanisms_01/MECHANISMS.json`|`15a344896bd7618166ee875476f0e409c6bf6ebffc056c30f37d49cc1324bc08`|
|`literature/hirwa/mapping.json`|`2af36ade37cdc33bf23f9df400f933ccd89c090c29a24cce97c80664ffaa4a38`|
|`literature/rosales/mapping.json`|`81989147e0f9a9b60b4b91fcd8f9647a204e9456d1aa2e52474729832dc4945a`|
|`offline_reference/hospital_tower.plan.json`|`f09d7cc0d0c9e826a62fc6a0d2cb9da2871f374c46c80cf3522100631d2dd249`|
|`offline_reference/hospital_tower.infeasibility_certificate.json`|`dba7168d63db3b49fe469921d155430a13aa7face14e2bb2251afd0c4a4a907f`|
|`steps/full_default_after_fix01/COMMAND.json`|`33220ba53bc671757ffb3006db38a07802ef204ed91ca7c7f5cc67a69b0a3ba1`|
|`steps/full_default_after_fix01/stdout.log`|`67087ccc41e04fae3675f0a39a9a6c5254cfcbf041eec8a21f40a2ef794d09a4`|
|`FROZEN_SOURCE_V30_C02.json`|`7d5f96864801c01d2b242530cfd5239fedb58f863b44e5cb8e31c1ef29fff761`|
|`runs/r03_normal_R_off/RESULT.json`|`993b3ca6d2d9761a967f5c4af3d63adefefc212e0809750e7aa1f8838d1922ee`|
|`runs/r03_grid_R_off/RESULT.json`|`ee0f9591e8ea40347d322483c0c836b64115eb61a2662300c1be2fb7697a2277`|

原生trajectory.gz SHA分别normal `320740ff9f4bd960b6948acb73a7f07b8e991abf5d972104f8ef4a7132f5df5e`、grid `7822e54543c1d33a8a3f752aec9951016329f1d929e036819b4c88ad9724cd46`。

中文总括建议：“两篇文献全文映射与一个共同离线参考已完成到明确层级；修复后正常、外网中断的启停/准备请求已在共同受约束执行器上真实走完180min，塔硬服务规划不可行并保留未重放状态。完整连续出力/储能计划、原作者程序和原文精确数值尚未复现；不据此给C/M与文献方法作同信息排名或上下界声明。”
