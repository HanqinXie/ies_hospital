# 最终工程报告生成器独立只读复核

复核源码快照：`build_engineering_report.py` SHA256 `a7a8023add3cf80629e92be11f1d93d62bca52f64f65862bb3b0f2ec687709da`；当时 `test_report.py` SHA256 `c98cd9c7041467517039d77cb0cbcad838a67ced3b477597d678836d24606bdb`。父任务正在独占补充门槛，本文件记录复核时发现的问题，不替后续新版测试作通过声明。

本次只读代码、测试与先前实际证据提取；未运行生成器/测试/仿真，未修改实现、旧产物或D盘源码。

## 当前已具备的门槛与正确表达

- validate_final_gate确实要求final_matrix_complete_and_clean和final_patch_id_consistent都为true；明确限定n02_/d02_和exact FIX01，单一producer/source/input冻结绑定一致。
- 冻结case集合和audited_runs都须恰好32个唯一ID且集合相同；每行status completed、complete true、unknown_tail0、new_actual_simulation、producer文件未变、每个已有check严格true，metadata与前缀路径要匹配冻结case。不是仅凭顶层clean字符串放行。
- collect_case核对每个已审计原文件的SHA、完整BINDINGS.source/input与C02一致、原生producer/patch、raw SHA、actual terminal在payload/RESULT/EVALUATION中相同并等于最后实际post_state；费用与独立审计一致。正常按context/method先收集，再对同一方法的扰动形成真实正常基线差。
- CSV使用float repr，微小ENS保留，None写null；没有跨电热冷总ENS、无改善百分比。源文件/输入计数读冻结映射长度，测试passed计数从真实stdout解析，没有固定1363。
- 工程文字对R03 DG的解释准确：请求/排队/到达/有效正出力上限均0，端电0仍可由共同自动孤岛支撑状态机启动/连接并发生轴侧损失；没有写成R_off请求正DG发电或C控制器的准备动作。
- 文献映射与后续R03层级分开，Rosales无编号方程记不适用；塔infeasible且未实际重放、连续出力/储能计划未重放、非原作者/官方软件、非同信息排名/上下界均写明。
- 报告首句仅声称注册边界内模型/评价冻结，archive_delivery_verified仍false；最终ZIP恢复、逐字节SHA和大小门槛另列。测试COMMAND没有内嵌source/git的事实也被明确保留。

## A. 父任务已说明正在补的三个明确问题

1. **180min不能由complete/unknown_tail0间接代替。** 当前validate_final_gate未逐行检查observed_until/requested_end是否均等于冻结context共同终点180，collect_case也尚未独立核对原生summary/last segment/terminal的180。应显式检查主矩阵与原生轨迹终点；已完成但只观察90min的改名记录不能满足最终180min门槛。
2. **material_minutes=null不能当0。** 当前compare_cell使用`not c_minutes`，critical_description使用`(material_minutes or 0)`，会把缺失中断观测描述为“未观察到实质中断”。None应输出unknown或让当前完整门槛拒绝，不能静默写无中断。数值ENS相同不应自动抹去中断时长未知。
3. **CSV服务数不能只信独立审计副本。** 当前collect_case费用做交叉核对，但服务列直接取item.service；应逐P0–P3、逐e/h/c与原生ledger积分/RESULT.reliability_v23及EVALUATION核对，防止审计表项与真实源不一致却仍生成成功表。

## B. 额外具体的错误成功保护缺口

### B1. “完整默认测试”的命令筛选仍可接受子集

位置：default_test_gate，当前仅拒绝单独`-k`/`-m`和`.py`结尾参数。形如 `pytest tests/some_subdirectory`、`pytest -k=some_name` 或 `pytest --ignore=tests/...` 配上exit0/positive passed stdout仍会被当作“完整默认测试”。这些是明确可构造的输入，不需要修改真实日志来理解。

建议按实际允许的默认测试命令形状解析tail（-q、--basetemp及其值等），拒绝额外位置选择器和所有选择/排除选项；至少覆盖目录子集与合并形式-k=。保持passed计数动态。当前真实default_after_fix命令本身没有这些选择器，所以此项是门槛完备性问题，不是声称真实1363项日志无效。

### B2. G1五项passed声明尚未检查全部支持产物状态

位置：build_report的evidence_paths/evidence_sha以及G1_five_items构造。历史VALIDATION_SUMMARY.status会验证，但METHOD_CONFIGURATIONS、CAUSALITY_CHECK、IMPACT_AUDIT只计算SHA而不读取其成功/失败状态。仅有一个可读且可哈希的失败/空状态JSON，并不足以支持之后硬编码的`passed_for_recorded_scope`。

建议对各支持产物约定的status/失败列表/必要数量检查后再逐项给passed；不要用文件存在和SHA替代已通过结论。若某产物只作说明而非冻结门槛，状态也应在completion matrix原样记录，不能统一写通过。

### B3. R03数值来源文件未纳入本轮报告指纹闭合

位置：literature_and_reference读取normal_AUDIT/grid_AUDIT，build_report支持evidence_paths列表。入口FINAL_READY、AUDIT_STATUS、PAPER_COMPLETION、MECHANISMS会被哈希，但实际复制R03费用/资源数值的normal_AUDIT/grid_AUDIT既不在evidence_sha中，也未与`audit_02/file_manifest.json`校验。身份和formal_status正确不能证明其余数值没有被改动。

建议读取并校验FINAL_READY采用目录的file_manifest，对R03数值文件至少执行预期SHA核对与报告生成前后SHA检查；同理将实际被引用的terminal/command链按采用清单绑定。无需重跑R03或优化器，只需只读哈希。旧产物仍保持只读。

### B4. CO2差目前把“两侧factor SHA都缺失”当共同因子

位置：flatten_case中conditional_CO2_delta_from_actual_normal_kg。当前条件仅为 `co2.factor_sha256 == normal_co2.factor_sha256`，因此None==None也成立；若两侧value有数字而因子元数据缺失，会输出一个看似可比较的条件排放差。

建议要求非空且相同的factor SHA，并保留合适的因子case/status边界；缺因子时差为null。当前真实结果的已绑定共同因子仍可比较，此修补不改变已有真实数值。

## 其它限界

当前render_report将许多字典直接嵌入中文段落；数据完整但读者扫描成本较高。若后续做文字整理，不应改变CSV/JSON的原数值、层级和未知状态。C/M的改善/相同/变差是按原单元规则的绝对差，不能将无实质中断的微小浮点差包装为算法优势，也不能把较低费用单独判成净收益。

本复核建议只补上述具体false-success门槛及相应人工回归夹具，不新增模型或实际运行。新版通过后需要将后续源码SHA、RED/GREEN实际日志与最终采用报告目录分别记录，保留本旧review快照。
