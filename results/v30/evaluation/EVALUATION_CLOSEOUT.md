# V30 评价导出模块交付

实现仅修改 `tools/model_review_v30/evaluation.py` 和 `tests/test_v30_evaluation.py`。未改物理执行器、输入、历史输出；未进行新仿真。

冻结源码 SHA256：`15c974a35b7ad4d9ab60ac663a93c3f76348a06735db2631a41e362f6716cb08`。
冻结测试 SHA256：`a2fb81f148ce192bfd0b3c533a6775f151280699dbcbf5fcc310c2e0226e27a6`。

公共函数 `export_evaluation(payload, result) -> dict` 不改变两个输入；新终态快照存在时深复制 `payload.terminal_snapshot`，历史终态由最后实际 `segment.post_state`、库存回执和已发生事件恢复。历史队列、未保存启动进度及剩余需求保留 null；不使用最后一次决策填补。

费用保留旧字段，新增 `economics.actual_fee_USD2001`，由来源纯费用、一次 DG 差额和一次初始准备费组成。短于原 RESULT 的前缀不得沿用全窗费用。独立复算函数 `independent_energy_audit` 只读原生分段购电、GT/锅炉燃料和实际 DG 轴功，使用 SHA 校验的冻结配置与原始小时对应的价格；不调用 `economic_window` 或读取 `cost_USD_step` 作费用计算。柴油化学能和油量未知。

库存的物理初值、准备充入、准备购电、末值、实际充放、静置损失、转换损失、独立闭合残差分别保留单位和来源。电/热/冷服务以原始请求减实际交付独立积分，保留微小 ENS；中断和恢复沿用 V23 原判据，实际恢复与确认恢复分列。电需求 direct/aux 与厂站 EC/ABS 转换及启动耗电分开。

## 实际验证

- `python -B -m pytest -q tests/test_v30_evaluation.py --basetemp E:/hospital_ies_v30_20261027/evaluation/pytest_final`：退出 0，14 passed；原始输出 `GREEN_FINAL.log`。
- 初始缺导出实现 RED：退出 1，11 failed，`RED_missing_exporter.log`；初版 GREEN：退出 0，11 passed，`GREEN_attempt01.log`。
- 原生 UTF-8 BOM CSV 错误复现 RED：退出 1，1 failed/11 passed，`RED_native_bom.log`；修正读取编码后 GREEN：退出 0，12 passed，`GREEN_native_bom.log`。
- 无观测中断状态与厂站转换单列 RED：退出 1，2 failed/12 passed，`RED_unknown_and_conversion.log`；GREEN：退出 0，14 passed，`GREEN_unknown_and_conversion.log`。
- `python -B E:/hospital_ies_v30_20261027/evaluation/validate_offline_exports.py --repo D:/hospital_ies_v22_20261025 --runs E:/hospital_ies_v29_20261027/runs --output E:/hospital_ies_v30_20261027/evaluation/final_01`：退出 0。七条历史导出，各独立 CLI 均退出 0；完整 argv/退出码为 `final_01/COMMANDS.json`，总报告为 `final_01/VALIDATION_SUMMARY.json`。
- 七条结果的原生文件（trajectory/RESULT/BINDINGS）导出前后 SHA 相同。费用、库存闭合、12 个服务单元 ENS 与原判据复核通过；网故障独立纯费用差 `8.526512829121202e-14 USD2001`，其余六条为 0。
- 人工塔故障 60 min 截取夹具明确标为 synthetic：已观测费用有值，未知 30 min 尾段费用 null，恢复 censored、时刻 null；没有冒充真实新运行。

| 历史轨迹 | 纯费用 USD2001 |
|---|---:|
| 外网正常 C/H1 | 182.95180610883705 |
| 外网故障 C/H1 | 229.07510750771758 |
| 塔正常 C/H2 | 65.21215227941997 |
| 塔故障 C/H2 | 62.62513886069754 |
| 供热循环泵正常 C/H0 | 100.11491859436988 |
| 供热循环泵故障 C/H0 | 100.11445527506666 |
| 外网正常 M/H1 | 182.9572940946002 |

塔故障 P0/P1 冷 ENS 分别为 2.897757436922679 / 8.550081569442586 kWh_c，实质中断 17.064466129021838 min，75 min 实际恢复、76 min 确认。终态 90 min 的 GT_1 已运行，已发生启动完成事件为 85 min。供热循环泵故障总热 ENS 为 73.709162021949 kWh_h，120 min 实际恢复、121 min 确认。

初次七条 CLI 尝试的 BOM 错误日志及命令索引保留，未删；重试成功导出也保留，最终应引用 `final_01` 与冻结 SHA。未执行总默认测试、真实新入口仿真或打包，均由根任务统一完成。现有物理、价格、容量及服务阈值未变；本模块未新增物理假设。
