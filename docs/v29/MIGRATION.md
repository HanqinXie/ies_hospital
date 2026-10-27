# V28 → V29 迁移

V29复用V28采用版物理/请求核心和M25执行器，公开reset/step与原生日志主体不变，不是新控制算法。V28原入口与注册保持原字节，旧结果不改。V29新入口：

```powershell
python -B -m tools.model_review_v29.validation --case grid_winter_fault --policy C --configuration H1 --output E:\V29_new_grid
```

每条新CASE必须显式v28_demand_version=V28_adopted，并精确绑定原两项正式热输入。V29不接受A_original_heat回退或动态冷水/温度耦合；保留C/M及after_cost。--configuration只能重复该注册工况的硬件身份，不能借同一case偷偷切换库存。

新增文件：tools/model_review_v29/{__init__,validation,source_scope}.py，configs/model_review_v29/cases.json，两份test_v29测试和三项inputs/model_review_v29质量输入。唯一改动的V28冻结文件是pytest.ini追加两份测试；余836原科学文件、全部33原输入逐项SHA相同。完整diff/文件列表与命令见execution/git及execution/FROZEN_SOURCE_V29_01.json。

V29实际生产提交8926dfb7a1632d98b71b0692c6e37df9f4a61855，source ec2a2e8cd42470a70e87fdb5f8a8921374f409cb88267d7b45798b32eb6ee72d，input f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15（843源码、36输入）。后续文档提交与科学producer分开，不重命名旧producer。

worker仍使用原策略身份如C_fixed_direct_aux_v28_V28_adopted；study_id单列hospital_ies.same_version_mechanisms.v29.v1，RESULT新增v29_core_reuse、v29_mechanism、v29_source_scope和SOURCE_SCOPE.json。这些是身份/评价字段，不改实际供能与指标。观察prefix未知时保持observed=null；失败不填全零或假装完成。

研究分类取实际事件及v29_mechanism.research_class，原stage/event_class只作合法parser编码。正常匹配例清除原事件，保留原背景/seed/硬件；泵故障的旧normal_verification标签改为真实故障元数据，未改事件时刻/幅度。smoke曾缩窗而保留越界参考时间，测试捕获后改为完整复用原合法15min采用版，未派发任何错误物理运行。

复用V28 B_N/D/FD三条结果置于reused/v28，保留CASE/RESULT/BINDINGS/原轨迹SHA及原冻结source9cb3ea82/input5c6fea46。新source质量只作为另附评价，不改写这些原件。新7例独立原生复算与旧3例分别调用同一只读入口/对应freeze，避免混版本。

所有PROCESS/BASE_PROCESS大CSV可标准XZ无损存储；restore_native_tables.py按原字节/原SHA还原，再运行原生算术。原trajectory.json.gz不改编码；大SQL与深系数缓存保留本地，省略清单给真实路径/SHA和作用。无远程推送、main合并或历史删除。
