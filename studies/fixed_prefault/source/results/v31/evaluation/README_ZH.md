# V31 独立只读评价工具

本目录只负责评价与汇总，不改共享源码，不构造环境、控制器或求解器，不启动仿真，也不生成压缩包。正式矩阵固定为 12 条记录：b05/b07 各有 C、M、该背景专用新增方案，各含正常和扰动。8 条原 C/M 是 V30 N02/D02 历史复用，4 条新增方案属于各自 V31 真实生产来源。

## 输入与调用

从交付根目录运行：

```text
python -B evaluation/audit_v31.py --root . --manifest EXPERIMENT_REGISTRATION.json --support audit_support --output evaluation/review_FRESH
```

输出目录必须不存在；重复复核请换新目录。相对路径均相对 `--root`；登记可以明确使用研究目录的绝对历史路径，便携交付时应由根任务映射到恢复后的真实位置并保留原研究来源。默认命令只读结果，不会自动重跑模型。

`EXPERIMENT_REGISTRATION.json` 顶层包含 `runs` 和 `allowed_changed_source_files`。本轮后者为 `[]`。每个 run 必须提供：

```json
{
  "id": "unique_record_id",
  "context": "b05_L3_ER",
  "mode": "normal",
  "method": "M_cooling_permission",
  "evidence_role": "new_v31",
  "path": "runs/v31_b05_normal_M_cooling_permission",
  "freeze_file": "FROZEN_SOURCE_V31_P01.json",
  "case_id": "actual_case_id_in_own_freeze"
}
```

方法固定为 b05 的 `C/M/M_cooling_permission` 和 b07 的 `C/M/M_steam_permission`；模式固定为 `normal/disturbed`。旧 C/M 的角色为 `reused_v30`，目录须明确为 `n02_` 或 `d02_`；不接受 N01/D01。新增方法的内部 alias 仍为 M，独立方法名称来自登记，避免误合并为原 M。

每个 `freeze_file` 沿用 V30 的 `producer/source_sha256/source_files/input_files/configuration.cases/hardware_profiles` 结构。每条结果只对其真实生产冻结核验。共同输入与共同已有源码逐文件核验；新增源码允许增加，不要求整个源树摘要与 V30 相同。物理核心和评价文件不可作为例外允许改变。完整 M 配置按原冻结展开：b05 只允许 `cooling_preparation=true`；b07 配置本身不增加别的开关。场景除明确的 scene id 标签外与相同模式的原 M 全量比较，保持 H0、起始小时、180 分钟和固定 GT_3 目标。

只读依赖是 `audit_support/audit_campaign.py` 和同目录 `FROZEN_ACCOUNTING.json`，由根任务保存 V30 原字节副本。使用其 `audit_run/integrate_service/fee_audit/store_audit/compare/prefix_view/decode`，不另改服务、费用或物质平衡公式。恢复/实质中断使用原生 `reliability_v23`，同时以独立实际请求和交付积分交叉核对 ENS；不改物理阈值。

## 输出与含义

- `AUDIT.json`：12 项索引、实际可审计记录、原生文件 SHA、逐项核查、来源连续性、共同 t=0 与前 60 分钟配对、准备及机制窗口、终态及紧凑指令链。
- `MAIN_RESULT_TABLE.csv`：每条实际记录的 P0–P3 电/热/冷请求、交付、主动切除、连接缺供、ENS、实质中断与恢复；实际费用、DG 轴功/端电、库存与准备窗费用；原始浮点值以 Python 完整可往返标量表示写出。
- `PAIRED_DELTAS.json`：每种方法扰动减自身正常的带符号差。未齐的一对不补零。
- `METHOD_CONTRASTS.json`：新增方案减 M、C 减新增方案的正常/扰动绝对差，以及两者各自扰动增量之差。只保留绝对单位，不制造改善百分比或总缺供分数。
- `COMPLETION_MATRIX.json`：完成/失败/未开始、未知尾段和审计失败门槛。
- `ENGINEERING_REPORT_ZH.md`：事实驱动的中文汇总，是否可以结束开发仍须根任务结合接口、信息边界、实际验证和交付验收决定。
- `OUTPUT_SHA256.json`：该次派生输出的 SHA256。

`final_matrix_complete_and_clean` 仅在 12 条记录全部真实完成、六个正常/扰动配对齐备、来源连续性和全部审计无失败时为 true。源文件/输入/库存/终态/费用核对、求解错误、策略回退、完整窗口与未知尾段均参与门槛。缺记录时明确 partial；程序对无异常的 partial 返回 0，表示只读复核执行成功，**不表示最终实验完成**。审计异常或完整性失败返回 1。

准备窗 `[0,60)` 的实际运行能耗和费用与初始库存准备单列。初始库存准备费用只在全程总费用计入一次；窗口费用不再叠加它。原生精确边界的 pre_state/post_state 才作为窗口状态，不插值状态。若边界切入 DG 已积分收据，相关轴功和窗口总费用保持 unknown，不按时长估造；GT 部分区间沿原 `economic_window` 的线性出力均值口径裁分燃气。JSON 保留这一限制。

计时归一只沿 V30 规则，在明确 `solver_calls/solver_backend_layers` 容器内处理 `elapsed_seconds/TimeLimit/remaining_original_budget_seconds`。实际命令、状态、队列和其他同名字段继续比较；RNG 另列诊断，不伪称所有随机状态相同。

## 小测试与证据

```text
python -B -m unittest discover -s evaluation -p test_audit_v31.py -v
```

全部测试均为明确标注的 synthetic 小夹具，不是运行轨迹。真实 RED/GREEN 日志和命令/退出码保存在本目录；没有启动模型或全量历史测试。最初 partial 选择文件用于只读核查现有 8 条历史记录，其中 4 条计划名称明确代表尚不存在的证据，不能替代最终实验登记或声称已经运行。

本工具不证明容量充分、年可靠率、最大续航或临床保障资格。许可与实际出力分别报告；C、M 与新增方案可以无改善或变差。新增方案到 C 的剩余差异不归结为单一机制，也不强制拆成 100% 贡献。V30 两篇文献及离线参考的已有限制继续有效。归档完整性、大小和独立全量恢复由根任务另外实际验收。
