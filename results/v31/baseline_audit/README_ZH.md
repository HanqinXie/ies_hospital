# V31 冻结基准与8条历史复用核验

结果：passed。858个冻结科学文件和36个原输入均与C02逐SHA一致；8条指定N02/D02原C/M运行均完整180min、unknown_tail=0，压缩原生字节、bindings、case/scenario摘要、H0设备、fix1及终态记录核对通过。

当前文档HEAD为`e635b919dae9e1c89793a4c29eecbab4e27a05ec`，历史科学producer为`3252727135645037cedc57ebc188a48d2aa34ccb`；两者之间仅4个docs/v30文件变化。复用结果不得改称由当前文档HEAD或未来V31策略产生。

准确复用名单：

- `n02_b05_L3_ER_normal_C`
- `n02_b05_L3_ER_normal_M`
- `d02_b05_L3_ER_disturbed_C`
- `d02_b05_L3_ER_disturbed_M`
- `n02_b07_L4_GT_ER_normal_C`
- `n02_b07_L4_GT_ER_normal_M`
- `d02_b07_L4_GT_ER_disturbed_C`
- `d02_b07_L4_GT_ER_disturbed_M`

b05起始小时3416、b07为3128，均H0、180min。正常模式保留小时负荷变化、无故障及需求冲击；扰动模式只在[60,120)对四个ER区域e/h/c各加25%，b07另固定GT_3故障。准备[0,60)，恢复[120,180]；真实response_delay_minutes=15。

SCENARIO_PREREGISTRATION中GT_2是未覆盖的原始占位；ACTIVE_TARGET_LOCK与所有实际b07扰动记录明确GT_3。本轮不得重新选择目标。M的完整采用配置已记录，online_GT_count_order=after_cost、cooling_preparation=false、time_energy_preparation=false等均一致；不能从裸工厂默认重新拼M。

本审计只读取原E:/hospital_ies_v30_20261027这一套历史源；仅复制五份小型元数据到frozen_metadata，不复制轨迹/solver/domain大件，不修改旧E或D。不调用模型/求解器。下一阶段只允许登记的4条新增正式运行，与此8条组成12条；不扩展32条或历史1363测试。

机器复用清单为REUSE_SELECTION.json/csv，逐文件SHA与所有核对见BASELINE_AUDIT.json；实际只读git命令/退出码见COMMANDS.json及原始文本日志。

限制：本次不宣称新增V31方法完成或模型开发可以结束；尚需root完成4条正式新运行及其必要核验。
