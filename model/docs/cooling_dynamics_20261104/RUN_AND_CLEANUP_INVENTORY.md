# 运行资源与删除前输入依赖盘点

本盘点只读取旧目录、操作系统资源和旧运行入口；未启动正式实验，未删除、移动或归档旧结果。逐文件输入大小和 SHA-256 见同名 JSON。2026-11-04 现场总内存 47.38 GiB，可用内存先后为 21.34、20.73、20.59 GiB；CPU 为 Intel Core Ultra 9 275HX，24 核、24 逻辑处理器。E 盘约剩余 399.8 GiB，D 盘约剩余 156.56 GiB。

| 明确删除的旧目录 | 文件数 | 文件字节数 | 范围 |
|---|---:|---:|---|
| `E:/hospital_ies_minute1_20261103_run01` | 1,578,444 | 39,797,044,628 | 整树，包括旧 raw、审计、后验、临时产物 |
| `D:/云大文件/论文/医院ies/code_4_2/plotting/problems_8` | 411 | 254,760,389 | 全部旧内容，包括 archive 和两个 preview |

E 旧目录为 37.064 GiB，但含 96,147 个子目录，删除耗时主要可能来自小文件数量。其 `native_runs` 占 1,349,709 文件、15,261,750,000 bytes；`input_generation` 占 16,746,785,813 bytes。D 中 archive 为 165 文件；两个 preview 分别为 87 和 76 文件。

## 并发准入

旧 19 条指去重复后的主轨迹总数，不是 19 路同时运行。旧 `design/PARALLEL_EXECUTION_POLICY02.json` 采用 2、4、8 路分级准入，每个求解器 1 线程。旧 `ROOT_CONTINUATION_RESOURCE04.json` 实测 producer 约 1 GiB，而 T02_N/T06_N 的旧完整审计分别达到 9.877/9.726 GiB，同时审计曾导致可用内存低于 6 GiB并受控停止。

本次主轨迹先开 4 路，实际稳定且资源足够时增至最多 8 路；每个 solver 1 线程，BLAS/OMP 同时限制为 1 线程。可用内存至少 10 GiB 才允许新启动，低于 8 GiB 暂停新准入，低于 6 GiB 时请求自有 worker 在明确步边界停止并留下前缀和原因。独立进程分别拥有输出、临时目录及 stopfile。大审计不得并发；本轮改用真实逐段账本的轻量工程核对，不重复旧大 native-primal 后验重构。现场内存快照不能替代后续实时监测。

## 删除前迁为新版本输入的最小白名单

运行必需的四个文件均位于旧 `input_generation/extraction01/`，合计 6,408,953 bytes：

- `minute_loads.csv`：1,205 行，2,075,944 bytes；SHA `29721c9758a0d4814d44df102af400ebd8467c34cb7f2907fcffd503028b3467`。
- `minute_critical_zones.csv`：16,870 行，4,063,344 bytes；SHA `73e0759eb8fcdcd664a399d4f47fe9619a3b7501e07c643acf2dd4c905f580b9`。
- `minute_thermal_components.csv`：1,205 行，220,598 bytes；SHA `783097d04b21cb226cdacff61ab19a6f604173a87962cef1dfca182a9fcf7ddc`。
- `MINUTE_SOURCE_METADATA.json`：49,067 bytes；SHA `ee8e7ff4d56b603a3b6ca3f0c6aa5689b457b9258ae686fe82900ca8ce7e91ff`。

另保留 JSON 白名单中的 18 项小型来源材料及冻结 IDF，合计 2,298,100 bytes。用途是记录负荷来源、原准入范围、设备冻结值、已知 SQL 完成标志异常和警告；不继承旧 IES 运行结论。迁移时复制字节并复核 SHA；旧 manifest/admission 保留为来源记录，新活动 manifest 必须使用新绝对路径，另写迁移映射，不能覆盖旧记录。四个核心输入的内容和哈希保持不变。

旧 `design/MINUTE_REGISTRATION01.json` 及 19 个 CASE 只作为试验设计输入，生成新注册和新 CASE。N/U/R 使用同负荷、初始状态和原故障脚本；共 19 个唯一 producer，T08 与 T05 使用相同连续轨迹。故障 60 分钟、修复 120 分钟，除 T07 为 240 分钟外均为 180 分钟。

源数据生成需要的五个脚本另列为可移植源码；若要重新生成原生负荷，须同时保留冻结 IDF、天气文件身份及 EnergyPlus 引擎身份。天气仍位于原始 `Hospital_SanDiego_Starter`，不在删除范围。新工作区已有 `inputs/`、`configs/model_review_v30/cases.json`、V28 热服务组件及 V29 质量文件；这些原输入不在删除范围。

旧源码 `source/final04` 的 admission/QA 只认证 `9198b6b...`，不能用于新源码。新版本必须冻结新代码和输入并重新绑定。旧 `runner.py` 的 `OUTPUT_ROOT` 强制指向旧 E 根，旧后验/绘图工具亦有 root、epoch、SHA 和旧结果金标硬编码；如借用算法，先迁为新源码再适配，不得从即将删除的目录动态加载。

## 不迁移的旧科研结果与可复现边界

不将旧 IES raw、轨迹前缀、NL/primal、审计结果、命令 sidecar、图、旧后验、预览或 archive 复制到别处。旧 EnergyPlus `eplusout.sql` 和 `eplusout.eso` 也不在迁移白名单中；删除后不能直接对原 SQL 再提取，只能用保留的 IDF、天气、引擎和脚本重新生成。已保留的四个分钟输入足以重放本次 IES 输入，不能宣称保留了完整原生源模拟备份。

最终删除应在新输入迁移哈希核对、新轨迹及图件实际验收之后，由根代理对已解析的两个绝对目录执行，并检查新结果根不在目标树内。不得将目录重命名或隐藏归档称为删除。本子任务没有执行删除。

## 后续已授权准备工作实际状态

根代理追加授权后，已实现 `run_worker.py`、`run_campaign.py`、`extract_results.py`，并仅执行 prepare：新根 `E:/hospital_ies_cooling_dynamics_20261104_run01` 中迁入 27 个输入/来源材料/生成脚本，共 8,792,867 bytes，另重建 19 个 canonical CASE。已实际只读加载新 MinuteSource 并验证 19 个 CASE，T08 与 T05 别名相同；未启动正式轨迹。旧 raw、图、后验、命令 sidecar 复制数为零。

聚焦账本/区间/已复现非有限元数据回归实际为 `3 passed`。新工作区继承的历史 conftest 强制绑定旧目录，因此仅本轮新测试使用 `--noconftest`；没有修改历史 hook、旧断言或科学容差。原生状态中的 `temperature.P3=-inf` 表示没有支持热区，原生验证行中的无界上下限及残差使用明确 sentinel 标签，保留路径与符号；实际供能/库存中的 NaN 或非有限值仍拒绝。新工程日志明确省略完整 native 行/primal 数组，以 count/leading_shape/omitted_reason 和不可重建标记说明，不能称为旧无损证书；输出文件另有完整文件级 SHA。省略的巨大数组不再先遍历序列化求哈希，避免无意义开销。

控制器保持全历史：现行 SciPolicy 通过所有历史 demands 的逐元素最小值计算 risk_loads_kW，不能裁为最后一条。仅对已读源码证实只读的精确 SciPolicy / StudySteamPolicy 类型，取消外围重复 deepcopy；内部原 legal_history 的特征/来源复制和全部历史范围不变，未知控制器仍保留原外围复制。

精确命令、退出码、失败原因、文件绑定和未执行项见 `RUN_AND_CLEANUP_INVENTORY_EXECUTION.json`。生产前必须等待物理集成修复完成，最后 freeze 新来源；freeze 后改动源文件会被 worker 闭环拒绝。

最后追加 `audit_campaign.py` 独立工程核账，不调用优化器或状态转移：单次读取每条正式 SEGMENTS，按 RESOLVED_CASE 中原物理系数及实际 reset 库存参数，重算电/蒸汽/有用热/冷/塔排热、建筑辅助电一次计入、应急与普通母线、锅炉有用目的、SF1 连续损耗及充放电效率、柴油轴功/转子/有限预算，以及冷动态的均值/端点/累计能量和实际支持运行旗。关键字段未知即失败，不以 0 填补。

功率/蒸汽收支沿原工程验收为 `1e-4 kW` / `1e-4 lb/h`，库存沿原 SF1 为 `1e-7 kWh`；辅助电分项、母线及锅炉目的约束保持 `1e-7` 的原量纲核对。实际动态均值上限使用原模型 `1e-6*max(1,mean_kW)`。仅读三组已存在的 component raw 共 50 段，公式组件核对通过，未新增 reset 或轨迹；这些局部/合成锚点不是正式 19 条连续轨迹验收。正式结果必须先通过 `audit01/CAMPAIGN_AUDIT.json`，提取器再核对 source 和选定 producer SHA，随后才可绘图。

任一正式 worker 失败后，campaign 停止新准入并写本版本 STOP.request，其余自有 worker 在步边界留前缀后停；不操作其他用户进程。根代理决定是否同源重试或另建新物理 epoch，禁止混用旧 selection。
