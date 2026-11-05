# 医院 IES V27 审查与可运行交付

本包对应 `Hospital_IES_V27_Codex_Task_20261026.txt`，在 V26 已封存结果上完成运行目标层级审查、完整状态真实续跑、四工况重算、年度冷需求来源恢复与有限条件敏感性。原28项输入、设备容量、价格、优先级、保护/失效判据保持不变；新增3项条件输入独立保存。没有 MPC、学习训练、新 EnergyPlus 仿真或热模型拟合。

## 先看工程结果及边界

- 同一 F90 状态下，C 的“在线台数”改到原费用目标之后；90–180 min 真实能源费减少 **6.877047 USD2001**，真实启动耗电 **3.333333 kWh**，条件 CO₂ 增加 **175.227612 kg**。新分支 GT1/3 从100 min才实供；两分支燃机末态不同，不能推为长期等终态收益。
- 新规则下 N/F/D/FD 全部重新运行180 min，原判据下关键工程中断均为0。故障恢复可用不等于必须重启原机组；F/FD 的 GT1 在75–85 min付启动电，85 min首次出力，GT3继续接续。微小原始 ENS保留，未发生中断时恢复为null/NA。
- 真正年度26.2派生 IDF/SQL/提取器已找回，实际重导四CSV与原输入逐字节相同。但年度仅保存Hourly，完成标志与成功日志存在未解矛盾，并保留 **22,832,532** 次警告；来源找回不等于年度物理真实性合格。
- 一个预登记14 h窗口、两既有策略、原/条件两负荷，四次840 min真实运行全部完成。结果为**给定原聚合需求与既有辅机分配假设下的条件 S**。逐台直接临床设备需求固定与年度真实冷负荷的强命题仍为 **U**：条件负荷使辅机电增加15.349983 kWh、直接电分解等量减少。动态图水侧及两温度模型仍不合格，默认热耦合为null。

完整解释与数字在根 `MODEL_REPORT.md`、`ASSUMPTIONS.md`、`FINAL_ACCEPTANCE.json`。结果、资格及未完成项分别记录，不能把1237个测试通过解释为全部物理用途合格。

## 环境与公开入口

本次实际环境为 Windows、Python3.10.6，具体依赖版本见 `execution/RUNTIME_VERSIONS.json`。只读重放需要 `project/requirements-saved-recheck.txt` 中的 NumPy/Pandas；公开物理计算及完整测试还需要 `project/requirements.txt`、真实可用的 Gurobi。未捆绑许可证、二进制求解器或用户环境。

将整个目录解压到 D 或 E 盘。以下 PowerShell 命令从**本包根目录**运行，输出均使用新的包外目录；已存在的重放输出不会覆盖。先运行普通入口：

```powershell
$packageRoot = (Get-Location).Path
$env:PYTHONPATH = "$packageRoot\project;$packageRoot\project\src"
Set-Location -LiteralPath "$packageRoot\project"
python -B -m tools.model_review_v27.validation --case smoke_normal --policy C --objective-order after_cost --configuration H0 --output ..\..\V27_smoke_replay
Set-Location -LiteralPath $packageRoot
```

V27默认 `after_cost`，策略身份 `C_reliability_then_cost_v27`。以 `--objective-order legacy_before_cost` 复现独立旧顺序。旧 M25 路由原缺省行为保留。全四案用 `--case combined_N/combined_F/combined_D/combined_FD` 分别运行；条件窗口用 `W1_C_original/W1_C_conditional`配C，以及`W1_M_original/W1_M_conditional`配M，均H0、相同当前观测接口及已有物理核心。不需要旧绝对路径 SQL 才能运行普通供能入口。显式请求未合格动态冷水/温度用途会被V27前置拒绝。

## 四项只读重放

仍从包根、沿用上述PYTHONPATH运行。它们只读包内真实数据，无 IES/EP/优化调用：

```powershell
python -B -m tools.model_review_v27.load_replay --project project --evidence load_source --output ..\V27_load_replay
python -B objective_order\analyze_static_and_branches.py --project project --objective-root objective_order --output ..\V27_fork_replay
python -B objective_order\analyze_complete_cases.py --project project --runs-root runs --output ..\V27_four_replay
python -B load_sensitivity\audit_sensitivity.py --project project --runs-directory runs --output ..\V27_sensitivity_replay
```

本次已经在独立便携目录实际完成上述四项，退出码均0，见 `portable_replays01/COMMANDS.json`及各原始stdout/stderr。普通公开入口实际成功记录见 `execution/CLEAN_PUBLIC_ENTRY.json`与`clean_public01/RESULT.json`。这次独立便携核验使用复制后同SHA源码；不声称在未安装依赖的任意新机器上已测试。

采用的Source04前缀另做了真实只读遗产等值复核，使用包内原V26 F与完整prefix04快照，3829个原生前缀叶、1750个合法当前观测叶无差异。可在包根执行：

```powershell
python -B objective_order\verify_prefix03.py --project project --old-run history\v26_combined_F --new-prefix objective_order\prefix04 --output ..\V27_prefix04_equivalence_replay
```

脚本保留历史文件名，显式`--new-prefix`选择本次真正采用的04。原03验证记录仍以03身份留档，没有改名成04；本次04实际记录见`prefix04_portable_equivalence01/`。

完整测试在`project`目录执行`python -B -m pytest -q`。本次原工作区实际1237 passed、0 skipped、exit0，原始日志见`steps/full_pytest_final01`。当时仅给该命令设置已恢复Starter的`SCI_SOURCE_ROOT`，原年度辅助源检查以只读SQL实际执行；紧凑包不含四个大型SQL，外部源检查在无该资料时可能跳过，不能复制本次“零跳过”数字。

## 证据与版本

| 路径 | 内容 |
|---|---|
|`project/`|826项绑定科学源码/配置/测试、31输入及必要12项运行引用闭包；公共reset/step未改。|
|`objective_order/prefix04/EXECUTION_SNAPSHOT.json`|完整采用F90状态，含执行器、库存、队列、当前状态及合法历史；Source04。|
|`objective_order/static_A0_02`、`static_A1_02`|同一完整状态独立重建的静态原层级/新层级；计划值与实际分开。|
|`objective_order/continuation_A0_01`、`continuation_A1_01`|真实90–180续跑；容器前0–90共同legacy，逐决策身份保留，不能当全程新策略样本。|
|`runs/`|新完整N/F/D/FD及四个14 h敏感性主工况，所有原生轨迹均保留。|
|`load_source/`|8760八盘管源值、时间字典、实际14区120 h原/Min8值、全signed映射、三派生IDF、提取/修复脚本、原.err/.end及警告。|
|`load_sensitivity/final01/`|独立原生账户、辅机/直接电、触顶、服务、启动及后端拒绝记录；`interpretation01`解释S/U范围。|
|`verification/`|四主案只读原生验算、旧四个误标*_W积分量的新*_kWh导出与对应表，旧值/文件不变。|
|`steps/`、`execution/`|真实命令/退出码/日志、四次源码冻结、保守16次尝试最终账及未执行项；首失败/修复/回退不删除。|
|`history/v26/`、`task_package/`|原V26限制/失败摘要和完整新任务包展开材料；未嵌套旧96 MB ZIP。|
|`patches/`|V26基线至V27改动、文件清单与最终Git身份；未推远程或合并main。|
|`MANIFEST.json`|包内全部其他文件的字节与SHA，不对自身递归哈希。|

正式科学生产绑定为 `4cb5e019783591ce2ae1c8949c18c69b1d7c9e95` / Source04：源码SHA `ac3a4f6aba22a4d0ef51ecfc427490af5d2e9d1be5d8b97764067a175a9c898a`；31输入SHA `10070101f2b016f6e238fae4e4b1b1ccb0a9cdf8b36a70bfefcfb8ab6e44564a`。后续文档提交单列，不将它混为生产源码版本。

此包严格小于100,000,000十进制字节。为保留全部科学工况，省略原输入的完全相同候选CSV副本、被Source04替代的Source03大快照及大多数深层矩阵缓存；逐文件真实路径/大小/SHA和理由在`OMITTED_LOCAL_ARTIFACTS.json`。完整采用快照、所有主轨迹、拒绝/未知记录和代表性原矩阵入包。被省略原件仍留本地；完整原矩阵级审查需要对应原件，或按公开入口重新计算。封包后ZIP完整CRC/逐成员SHA/最终字节/ZIP自身SHA见同目录外置`ARCHIVE_VERIFICATION.json`，避免自哈希循环。

必要后续是先明确独立直接用电与辅机计量边界，再验证新增冷需求对总电需求的影响；年度来源警告/完成标志矛盾、动态冷水和温度模型另行资格验证。本轮没有为让某策略获胜改变边界或继续扩样。
