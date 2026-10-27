# GT 启动就绪投影缺陷：只读影响审计

实际扫描结果与原始文件 SHA 见 IMPACT_AUDIT.json。审查没有修改 D 盘源码、运行环境、求解器或仿真。

有轨迹原生文件的新运行数：24；全部新真实尝试数：25；V29 历史运行数：7。观察到未来合格启动时间的轨迹：d01_b02_L1_heat_pump_disturbed_M。保存观测中发现 ready < started 的轨迹：d01_b02_L1_heat_pump_disturbed_M。

第25个新尝试r01_normal_R_off的RESULT为failed_unknown、actual_episode_attempts=1，错误为KeyError strategy_fallback，没有trajectory.json.gz，未知尾段180 min。其RESULT、BINDINGS和ACTUAL_PREFIX仍保留SHA；pending历史覆盖为未知，不能当成未开始、空队列或零尾段。

明确反例为 d01_b02_L1_heat_pump_disturbed_M：producer 7e1a5320d98027ec9c5897e778a8b63b01ed41bb，实际前缀止于90 min，尚有90 min未知尾段。90 min决策和真实终态都保存GT_1 started=105、ready=100、requested=126.83648，实际供电启动进度为空。原失败和原生前缀保持原样，不能把未知尾段当作零缺供、零费用或已恢复。

纯算术反例：原最小停机30 min、last_off=75使实际启动资格为105；原启动延迟10 min与请求/爬坡给出的8.455765333333334 min取最大值，所需实际供电启动为10 min。旧计算90+10=100，早于资格105；建议计算max(90,105)+10=115。115仍是继续实际供电条件下的预测时刻，不是已经完成或已经供电。

最小修补应只改两处ready投影的基准时钟，保留super()._clamp的最小停机、权限与爬坡约束，以及原实际启动电力充分性和progress<required时实际GT输出为0的门控。新投影相对旧投影增加max(0,started-time)，当前时刻已达启动资格时完全相同；该修补不放宽容量、阈值、最小停机或实际供电启动要求。

保存决策通常相隔15 min，真实_clamp可能在其间多次调用；原生分段没有保存每次调用的完整pending字典。未在已完成轨迹采样点发现同类错误，不足以证明这些轨迹从未触及路径。V29没有新live终态队列，历史末端pending未知。

建议将物理修补显式版本绑定，并在同一新核心下重新运行完整32条C/M正常与扰动矩阵；保留旧N01/D01及失败source作开发与失效证据。不能按结果有利与否只替换部分背景。本次盘点25个新真实尝试，另32条复算合计57；64上限的授权与登记由根任务负责，本审查不自行扩权。
