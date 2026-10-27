# V30 fix1 统一核心比较审计器

本目录为独立新副本。旧 `comparison_audit/audit_campaign.py` 的冻结 SHA256 为 `8c9102eef5d93aab8c1c3263fbf17b59e66a61410dc9052124cbcff843668f4c`，旧脚本、旧结果和旧冻结 JSON 保持原样。

新 CLI：

```powershell
python -B E:/hospital_ies_v30_20261027/comparison_audit_fix1/audit_campaign.py --root E:/hospital_ies_v30_20261027 --runs E:/hospital_ies_v30_20261027/runs --normal-prefix n02_ --disturbance-prefix d02_ --freeze-file FROZEN_SOURCE_V30_C02.json --output E:/hospital_ies_v30_20261027/comparison_audit_fix1/final01
```

`--output` 必须为新目录。`--freeze-file` 相对 `--root` 解析，也可传绝对路径。一份真实 C02 冻结用于全部正常和扰动运行；原始积分和费用审计使用相邻的 `FROZEN_ACCOUNTING.json`，其冻结参数和 32 个原生小时戳与旧版相同，不需要复制巨大 CSV。

最终 32 个槽只接受指定的两个前缀，并核对精确目录名、case ID、背景、阶段、方法和硬件标识。旧 N01/D01、dev、R_off 和 fix 接入轨迹均单列于 `excluded_prior_attempts`，保留实际状态、producer、源码摘要和路径；它们不能填充新槽，也不会被冒称为新槽的未开始结果。

每个已审计最终结果必须具有：

- 同一 C02 的实际 producer、完整源码摘要及逐文件 BINDINGS.source；
- 原输入逐文件绑定；
- 精确 patch ID `hospital_ies.powered_start_minimum_off_readiness.v30.fix1`；
- 原生 ENS、纯费用、实际终态和库存与新 RESULT/EVALUATION 相符。

准备前 physical state/core 与 RNG 分开报告。相同方法正常/扰动的 60 min 前实际请求、GT、状态、命令和队列继续比较。计时归一仍只作用于明确求解记录中的三个精确键；真实物理时钟、命令与状态不删除。各 P0—P3 电、热、冷分别保留，不跨载能相加，不舍去微小 ENS。

比较只给原始量、绝对差与 `relation`，所有 `improvement_percent` 为 null；0 对 0 的 relation 为 same。未增设物理阈值，也不对微小非零 ENS 残差计算百分比。

b07 的实际故障目标从 C02 冻结读取，保持已锁定 GT_3。新正常轨迹的最大 GT 仅作只读核验，不改注册表、不重新选择故障对象；正常功能门槛检查锁定的真实对象。

`status=partial` 或退出码 0 可以表示尚未开始或正在运行的完整索引。最终交付必须同时检查 `final_matrix_complete_and_clean=true`、32 个完成结果、无逐运行/背景完整性失败，以及精确 patch ID 一致。不能仅凭退出码或 `complete_audit` 标签声称全矩阵已完成。

本次只进行了明确标记的人工目录/冻结夹具回归，没有新仿真或 D 盘源码修改。实际最终测试为 14 项通过、退出 0，原始输出为 `GREEN_FINAL.stdout.log`、`GREEN_FINAL.stderr.log`，命令与退出码为 `GREEN_FINAL_COMMAND.json`。参数化、微小 ENS 百分比及锁定对象的实际 RED 日志均保留。脚本、测试、会计输入与日志摘要见 `FROZEN_AUDITOR_FIX1.json`。
