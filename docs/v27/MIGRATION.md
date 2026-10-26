# V27 接口与复现迁移

公共环境reset/step及日志原字段未删除。仅对原C配置添加`online_GT_count_order`，旧路由未给该键时保持`legacy_before_cost`；V27公开命名路由默认`after_cost`，只改变原在线GT台数软目标相对于同一个货币目标的位置。未改服务锁/目标向量/价格/物理约束或动作权限。

`tools/model_correctness_v23/validation.py`私有worker增加可选`study_identity`、`execution_policy_id`、`environment_adapter`、`registered_extra_inputs`，全部None时沿旧路径。V27输入适配器只在原reset完成后替换已登记且原授权小时范围内的当前冷需求，不授予未来故障/真实恢复/停电结束信息；外部总E/H和reference不变，但原聚合电内部aux/direct重分配限制见ASSUMPTIONS。

V27以`--objective-order legacy_before_cost`显式复现旧顺序；`C_reliability_then_cost_v27`表示新命名C，`M_current_service_v27_after_cost`为已有当前需求优先在同物理/输入通道下的身份。A1真实续跑容器的前0–90仍为共同旧顺序；读取时按逐决策policy_diagnostic识别，不能将容器顶层身份套给全部历史。

完整快照JSON使用显式实际类型白名单，含frozenset、真实essential_fixes.DieselRotor及全部可变执行状态。保存/载入重新绑定I/O sidecar和已哈希不可变引用；不复制writer/callback资源。命令生成不冒充环境执行。数学无限日志边界以显式`__nonfinite_float__`标记输出，未填零；早期失败原件保留。

单位修正仅对新导出：旧四个phase *_W积分字段在V27输出改标*_kWh，数值不变，映射和原件SHA见verification。计划目标、弱偏好和实际能源费用分别使用；启动电已经在实际来源电平衡中计费，不另加OEM固定费。

新工具命名空间`tools/model_review_v27`，具体快照factory/inspection API见objective_order的实现记录。V27前置接口拒绝显式未合格温度/动态冷水用途，正常供能入口可运行；默认热耦合None。大SQL只读来源资格与最小数据重放区分。旧文件/输入/已有结果不覆写。
