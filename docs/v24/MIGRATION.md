# V24 接口与实现迁移

公共`reset/step`接口未变。历史M23默认入口、原物理目标及已有结果身份继续有效；使用M24需显式调用`tools.engineering_completion_v24`，不能把旧RESULT中的model/source字段改名。

| 文件/接口 | 变化与兼容性 |
|---|---|
| `tools/engineering_completion_v24/environment.py` | 新M24环境/核心，继承已有实际队列、状态推进与结算。只在M24启用有限冷模式搜索；在实际no-export消纳证明前刷新当前模式，缓存区分当前12载体连接负荷。独立塔kW通过环境plant副本设置。 |
| `tools/engineering_completion_v24/cold_modes.py` | 同快照idle/discharge与受保护charge候选，不提交试算库存。Source02过滤仅费用收益的正放冷，原非费用层序/锁带与最终选择器不变。零冷容量不进入新增admission。 |
| `tools/model_closure_v22/environment.py` | 增加可选M24冷模式hook；未启用时保留原路径。未重写共同状态转移。 |
| `tools/model_correctness_v23/environment.py` | 环境核心类型/快照的默认行为保留，新增可选注入点供M24共用原实现。`run_episode`仍为同一实现，不复制一套主循环。 |
| `tools/model_correctness_v23/validation.py` | common runner增加有默认值的`model_version`及keyword registry/worker入口，M23默认调用不变。输出公共接口保持，M24日志附加字段以原时间区间导出。 |
| `tools/engineering_completion_v24/validation.py` | 薄入口：指向注册V24 cases、M24 worker身份，复用上述失败保留runner，不新增重复验证流程。 |
| `m24_cold_mode_selection`/`m24_tower_capacity` | 原生区间和PROCESS新增诊断字段，未改旧字段含义。候选后端证据放原solver log/CAS/sidecar，原段带摘要/索引；不可行、求解异常和probe失败显式保留。 |
| `thermal_diagnostics.py`、`thermal_low_order.py`、`thermal_nodes.py` | 独立诊断/离线受约束fit/原日期节点输出；没有把不合格温度模型接入默认供能核心。 |
| `pytest.ini` | 保留全部原默认路径，新增V24冷模式、admission及温度诊断路径。发现漏收admission后补入并实际运行最终1046项通过；不删除或放松旧断言。 |

Source01科学源码`9cf688…`和精确运行ZIP保留。纠正核心的precollector`388631…`用于实际局部回归/小过程，正式运行数0；发布`c8fc844…`仅比它多默认测试收集配置，生产字节相同。最终14项无冷库按严格不受影响证明复用Source01，12项冷库重新运行发布源码。每个正式策略或资源配对仍在单一实际源码内，不能把这项复用解释成所有26项都执行了发布源码。

本轮没有远程push/main合并；完整基线补丁从V23 `732fd6bd1bdaae754cce930e1084fe2d849d0771`生成，并以独立Git index重建最终tree。补丁按Git规范化blob验证；精确工作区字节另外由源冻结/ZIP成员SHA提供，避免混淆换行与实际执行绑定。
