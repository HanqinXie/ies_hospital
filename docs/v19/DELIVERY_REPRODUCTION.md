# V19 精简交付与独立复现

封包前状态：模型已正式冻结，最终 long000/E 参考已完整运行；复现配置已绑定该 RESULT、plan、manifest、terminal 及全部 66 项输入。格式沿用 V18 的 solid metadata 编解码器，保留所选逻辑文件的原字节。本稿不预先声明最终 ZIP 或独立科学复现通过，实际完成以同名前缀外置收据为准。首包实测 200,322,622 B 被硬限制拒绝，失败证据保留。第二次选择 11 个完整 raw，共 80,752,617 B，覆盖四类、long000/C19、接点负例配对、传播 ON/OFF、正常和旧失败前缀；只省略可由独立科学复跑重新生成的 long000/E raw 副本，全部 compact/时序/SHA保留。这不是最终 ZIP 大小。

第二包实测 195,879,111 B，储存身份通过；首次独立测试为 154 passed/11 failed，定位到未归档的延迟读取配置。第三次构建补入原字节的 V13 策略表、V11 standby manifest，以及两份 P3 来源收据，共 4,338,395 B；同时进入外层选择和 `SCIENCE_ROOT`，均有固定 SHA，记录于 `delivery_metadata/RUNTIME_METADATA_CLOSURE.json`。这些是补充运行/分析来源，不改原 345 源码及 66 项清单。V11 只用 schema/contract/options，不复制旧轨迹；P3 收据仅证明基线来源，不加载其 CSV 或宣称扰动温度。失败目录不补写，旧包/收据保留于 `deliverables/v19_packaging_logs/failed_build02`。

修正后的轻量诊断副本实际 165 passed，四策略工厂构造成功，注册重新生成与参考完全一致的 66 项清单，实际物理尝试为零；这不是最终 ZIP 的独立通过声明。来源、版本快照、真实 RED/GREEN 和诊断退出码见 `review/runtime_closure_repair03`。最终仍须新 ZIP、新目录、完整测试及真实科学轨迹通过。

## 固定范围与体积初估

选择与科学验收工具为 `outputs/v19_model_closeout_20261018_run01/scripts/build_delivery_v19.py`；最终存储入口为同目录 `pack_metadata_adapter.py`，不改冻结的 `tools/src/tests`。最终一个 ZIP 必须严格小于 **200,000,000 字节**，等于上限也失败，不分卷。JSON/CSV 逐字节沿用 V18 的 USTAR/XZ preset6、64 MiB 字典和 CRC64 单流格式，其余直接保留；不重新压缩 raw，不研究新压缩算法。下面普通 ZIP 的各次 dry 数值是早期估计，不能当最终大小或压缩率保证。适配细节和全部已执行小测试见 `review/packaging_feasibility/README_ADAPTER_CANDIDATE.md`。

2026-10-18 的首次 dry（科学实验尚在运行）读到：

| 范围 | 未压缩字节 |
|---|---:|
| 原必要 inputs：loads 四 CSV、IDF/EPW、reference、optional 模板 | 35,162,423 |
| src | 216,078 |
| tools | 4,984,779 |
| tests | 2,071,298 |
| configs | 34,827 |
| 其他新 B19 元数据/来源/说明等 | 约 64 MB，随结果增长 |

初次选中 2,078 个文件，共 106,429,723 字节；不带代表 raw 的普通 ZIP 流式估计为 18,905,992 字节。这不是最终 archive size。dry 使用一次固定 DEFLATE6 流计数并计算元数据 SHA，不创建 ZIP、不读取科学 raw；gzip raw 的估计直接用现存压缩文件字节数。dry 明确登记尚未终态批次和未完成的 raw SHA。后续正式结果、报告及失败证据增加后，必须重新 dry。

早期 `B19_delivery_dry02`（11:46:50 UTC）选中 2,188 文件、227,204,105 字节未压缩，普通 ZIP 估计 **25,030,489 字节**；当时为 0 条代表 raw，盘点 7 条新 raw 的 producer SHA 均已取得，10 个批次尚未终态。此数仅为历史估算，不代表最终大小。

## 文件选择和科学完整性

保留：

- 当前 M19 的签名 `source_binding` 全闭包、原运行输入、配置、全部回归测试及本轮方法/报告。当前签名为所有生产 Python 依赖，不能以静态 import 推测删掉动态路由依赖。
- 全部新 B19 元数据、CSV、日志、RESULT/analysis_error/未处理异常、注册计划、manifest、终态记录和未知结论；包括 completed 但带 `error` 或 `analysis_error` 的记录。兼容 CSV 名中的 `V18_` 是格式名，内容仍按其 M19 producer 绑定。
- 原只读 SOURCE05 freeze、必要 V18 design JSON 以及 source_binding 中的旧 `global_manifest.json`；它们只作原出处/执行依赖，不复制旧归档或旧 raw。
- 有明确覆盖理由的整条新 M19 gzip raw。其余所有新 raw 均在 `NEW_RAW_LEDGER.json` 和外置 `OMITTED.json` 中保留原绝对路径、项目相对路径、字节数、producer SHA、状态、策略/场景及错误标志。

不复制 V18 历史 ZIP/raw、原 `source_snapshots` 重复树、缓存或嵌套 ZIP。新旧开发记录需要的**独有**源码变体按 SHA 存为 `delivery_metadata/source_objects/`，通过 `SOURCE_VARIANT_MAP.json` 映射原 plan 与逻辑路径；不把数十份完整源码树再打一次。全部注册 manifest.files 声明的路径–SHA 必须纳入闭包，包括 `inputs/literature/` 原文献、optional/reference 以及根目录 inventory 文件；即使当前 runtime 不读取其中某项，也不裁剪清单以冒充严格同输入复现。冲突 SHA 显式失败；`REGISTERED_INPUT_INVENTORY.json` 保存所有声明路径与来源 manifest。

raw census 优先采用 producer 的 SHA，不重开全量 raw。仍在写入、没有 RESULT、没有有效 SHA 的文件会标为 unknown。最终构建禁止遗漏一个没有 SHA 的 raw：应等待终态收据，或把该未知 raw 明确列为代表并在正常写入 ZIP 的同一数据流中取得 SHA；不以零值或虚假精度补账。小于 2 MB 的独立测试 gzip fixture 可单独计算 SHA，并标明 synthetic，不冒充科学轨迹。

## 代表 raw 的覆盖策略

科学元数据及完整 manifest.files 闭包必须保留，最终选择固定于 `design/DELIVERY_REPRESENTATIVES_FINAL02.json`，不为用尽容量追加 raw。同一 raw 可以承担多个角色。保留 long000/C19 整轨迹及所有策略完整时序表，long005/K1 与 contact025/K1 的有限时延负例配对；long000/E 将在独立目录实际重新运行完整 1065 min。正常保留 E/normal000 完整 24 h raw，其余正常窗完整 compact 保留。原十二条选择仍保留作首包证据；第二次选择理由见 `review/delivery_selection_final02/SELECTION_REVISION.json`。

覆盖角色为 normal、main_L1–L4、long000、long005、propagation_ON/OFF、contact_0/contact_0p25、unknown_or_failure。四类按 manifest 的 **canonical_class** 核对，不能把 legacy L2/L3 标签混用；propagation/contact 也按已登记布尔值和时钟核对。long000/005 的具体哈希场景 ID 由最终选择表显式给出。没有某类未知/失败时，应写明事实及 coverage waiver，不制造失败，也不把成功案例伪装成 unknown。

选择文件格式示例（这是格式示例，不是已选定的实验）：

```json
{
  "selection_basis": "scenario_clock_and_failure_coverage_not_policy_winner",
  "raw_entries": [
    {
      "path": "outputs/v19_model_closeout_20261018_run01/experiments/BATCH/pairs/SCENE__POLICY/trajectory.json.gz",
      "roles": ["main_L1"],
      "reason": "预登记四类覆盖；同场景匹配策略集合"
    }
  ],
  "role_waivers": {},
  "acknowledged_incomplete_batches": {}
}
```

停止且未终态的批次必须在 `acknowledged_incomplete_batches` 中逐批写原因，保留其 started/部分 RESULT/未知尾部；不自动视为已完成。正常运行中的批次不宜封包。coverage waiver 及 incomplete acknowledgment 是显式局限，不是成功结论。

## 执行入口

以下均从项目根目录执行。标签、目标 ZIP 和 stage 必须全新；工具拒绝覆盖旧证据。当前实际执行到 dry；最终数据和代表选择齐备后，按本任务授权继续 build 与独立复现。

```powershell
python outputs/v19_model_closeout_20261018_run01/scripts/build_delivery_v19.py self-test --label storage_new
python outputs/v19_model_closeout_20261018_run01/scripts/build_delivery_v19.py dry-run --label estimate_new
```

最终数据及代表选择到齐后，使用明确的最终 shared-model freeze；`build` 不接受 development freeze。以下入口使用已配置的最终文件名，archive、work、stage 必须是新路径。包装日志应写在 B19 以外的 `deliverables/v19_packaging_logs`，避免 census 包含自身正在写入的日志；先停止资源采样等所有 B19 writer。

```powershell
python -B outputs/v19_model_closeout_20261018_run01/scripts/pack_metadata_adapter.py build --destination D:\V19_delivery\Hospital_IES_V19.zip --work outputs/v19_model_closeout_20261018_run01/delivery_builds/solid_final03 --freeze outputs/v19_model_closeout_20261018_run01/MODEL_FREEZE_CLOSEOUT01.json --representatives outputs/v19_model_closeout_20261018_run01/design/DELIVERY_REPRESENTATIVES_FINAL02.json --repro-commands outputs/v19_model_closeout_20261018_run01/design/DELIVERY_REPRO_COMMANDS.json

python -B outputs/v19_model_closeout_20261018_run01/scripts/pack_metadata_adapter.py verify --archive D:\V19_delivery\Hospital_IES_V19.zip --destination E:\hospital_v19_standalone_fresh
```

先检查空间。D 足够时可用 D；不足时显式选 E。stage 必须在原工作区之外且从未存在，只创建一个独立解压副本。不会改全局 TEMP/TMP；仅独立子进程的 TEMP/TMP/PYTHONPATH 指向 stage。ZIP 不包含 Python 安装、求解器许可、账号或凭据，沿用本机已有授权，任何正式物理复现仍需可用求解环境。

## 构建和独立验证的证据

构建先写 `.building.zip`。一次实际最终 ZIP 构建完成后，按完整名单流式读取每个 ZIP 成员，检查**精确成员集合、非重复路径、regular-file 类型、CRC32、SHA256、未压缩字节数**和实际 archive 字节数。只有严格 `<200000000` 才发布目标名。超限或来源变化则保留 building ZIP 及 `.FAILED*.json`，不删事故、不自动更换压缩算法或分卷。

最终外置 sidecars：

- `.FILE_SELECTION.json`：`entries` 是实际外层 ZIP 成员，`logical_entries` 是位精确恢复后的原始文件；两层分别保留 SHA、大小及来源/理由，放外侧避免自引用。
- `.OMITTED.json`：全部遗漏的新 raw 状态/SHA/路径与理由。
- `.VERIFICATION.json`：archive 的实际 size/SHA 和每个成员的 CRC/SHA/size；不会将 dry 估算写成实际值。
- `.STANDALONE_VERIFICATION.json`：全新 stage 的提取身份、命令 argv/cwd/exit、stdout/stderr SHA 以及真实验证范围。
- `.sha256`：最终单包 SHA。

ZIP 内另有 `delivery_metadata/NEW_RAW_LEDGER.json`、`OMITTED.json`、`SOURCE_BINDING.json`、`SOURCE_VARIANT_MAP.json`、`REPRESENTATIVE_SELECTION.json` 和 `REPRO_COMMANDS.json`。自身构建完成后的 packaging 结果是外置 sidecar；不能通过递归把最终 ZIP/自身哈希塞回自身。科学日志按打包时刻逐字保留，随后产生的包装日志由外置收据记录。

独立验证首先仅根据 ZIP 和外置 selection 校验、解压和重算所有成员，不打开 selection 里记录的原 `source_path`。然后在 stage 中检查必要输入、全部 `source_binding` 和项目模块 `__file__` 所在位置，运行 runner/analysis 的 CLI 入口检查。原工作区不会作为 PYTHONPATH 或 cwd。

仅这些检查通过时，状态为 `storage_and_interface_only_reproduction_unexecuted`，**不称实际科学复现通过**。`DELIVERY_REPRO_COMMANDS.json` 必须再登记 stage 内真正的独立科学复现命令，格式如下：

```json
{
  "commands": [
    {"kind": "independent_regression", "argv": ["python", "-m", "pytest", "-q", "tests/test_model_closeout_analysis.py", "tests/test_model_closeout_emissions.py"]},
    {"kind": "scientific_reproduction", "argv": ["python", "-m", "tools.model_closeout.runner", "run", "--directory", "outputs/v19_model_closeout_20261018_run01/experiments/FRESH_STAGED_REGISTERED_CASE"], "proof_json": "outputs/v19_model_closeout_20261018_run01/experiments/FRESH_STAGED_REGISTERED_CASE/BATCH_RESULT.json", "expected_fields": {"actual_attempts": 3, "complete_full": 3, "unknown_full": 0}}
  ]
}
```

上例运行目录须先由同一命令表中**实际**的注册命令创建；示例本身不代表已存在的可执行案例，示例的 3/3/0 必须换成最终预登记预期。不可把 `--help` 标成 scientific_reproduction：工具会拒绝。科学复现必须实际执行 M19 runner，并产生运行前不存在的 `BATCH_RESULT.json`，通过 M19/plan/manifest/source/正 attempt 数的绑定检查及明确 expected_fields；不能仅凭命令标签或零退出码认定复现通过。失败案例可以登记其真实预期失败/未知计数，不要求制造全通过。

命令是 argv 数组，无 shell 拼接，输出仅在 stage 新目录，严禁引用原工作区；最终选择的有限复现命令应同时记录真实场景、策略和预期比较，不开旧最终 12 背景、不训练或扩样。任一子命令非零即 failed_unknown，保留 stage、日志和失败收据。所有命令 exit 0 且真正科学复现证明通过后才写 `actual_scientific_reproduction_verified=true`；它仍不是临床/厂家验证。

## 本次已执行验证及限制

`B19_delivery_builder_red01` 退出码 1（新工具尚不存在）；`B19_delivery_selftest_green01` 退出码 0。新增最终 freeze 身份回归后，`B19_delivery_finalfreeze_red02` 退出码 1，复现 development freeze 曾被接受的问题；修正后 `B19_delivery_selftest_green02` 退出码 0。`B19_delivery_dry01` 退出码 0，其原日志/退出码和 `review/delivery_dry_estimate01/` 三份清单全部保留。

最终工具回归为 `B19_delivery_selftest_green03`，退出码 0，补充检查损坏的 ZIP 数据不能通过；`B19_delivery_dry02` 退出码 0，stderr 为空。所有测试只创建小型 synthetic ZIP，未创建最终交付 ZIP、未读取科学 raw。

尚未执行最终 build、完整最终成员校验、真实 fresh-stage 复现，不能据工具实现或小 ZIP 自测宣称交付包已通过。最终报告必须补充实际单包字节数、SHA、成员数、独立 stage 路径和真实科学命令结果。

## manifest 闭包与固定 SCIENCE_ROOT 补充

2026-10-18 12:22 UTC 的 `B19_delivery_inventory_dry03` exit 0，仅作补闭包前的对照：
已有非 raw ZIP 估计 45,269,591 B；66 项原 manifest.files 共 93,382,998 B，
其中原选择遗漏 19 项 57,969,606 B，实际普通 ZIP 增量估算 57,952,070 B。
该估算不含最终主批次新增元数据，也不是实际 ZIP 大小。新 builder 已自动补全这些声明，
不通过删文献或改变 inventory 获取“严格复现”。

完整证据 stage 中包含全部历史注册，直接再 register 可能触及原预算枚举。允许 prepare
在同一独立 stage 内新建固定名 `SCIENCE_ROOT`，只复制本轮绑定源码、全部必需输入、
必要 freeze/proposal/gate/helper/test 与原出处元数据，不复制 experiments 历史树；原 stage
证据保持原样。SCIENCE_ROOT 的构建和引用数值比较由外置 `portable_reproduce.py` 完成，
不改生产预算。builder 只新增以下命令路由：

```json
{"kind":"scientific_reproduction","cwd":"SCIENCE_ROOT","argv":["python","-m","tools.model_closeout.runner","run","--directory","outputs/v19_model_closeout_20261018_run01/experiments/standalone_replay01"],"proof_json":"SCIENCE_ROOT/outputs/v19_model_closeout_20261018_run01/experiments/standalone_replay01/BATCH_RESULT.json","expected_fields":{"actual_attempts":1,"complete_full":1,"unknown_full":0}}
```

`cwd` 只接受缺省/`.` 或固定 `SCIENCE_ROOT`，须为已存在且解析后仍在 stage 内的目录。
每个 child 的 cwd/PYTHONPATH/TEMP/TMP 均指向其实际运行根；日志仍留原 stage，proof
路径须带相同根前缀。prepare 与 post-verify 可在原 stage 执行，runner 真正运行于新根。
这不会把 helper 自报、CLI help 或原 stage 的旧 BATCH_RESULT 当作 fresh run 证明。

`B19_delivery_inventory_cwd_red01` exit 1 实际暴露缺失 inventory 闭包；修复后
`B19_delivery_inventory_cwd_green01`、`green02` exit 0。小自测覆盖 13 类约束，
包括冲突 inventory SHA 拒绝、固定子根 proof 路由、非法 cwd 拒绝及实际 child 的
cwd/PYTHONPATH/TEMP/TMP 检查。小 child 未运行科学环境；真实独立复现仍待最终包。

根目录 gate04 测试另外需要原只读小型数学 fixture：V17
`review/ablation_interface_math_01/selected_original_domain.npz`、`original_rows.json`、
21 个 `layers/layer_00..20.npz`、V17 `design/MAIN_PROPOSAL.json`，以及 V18
`review/key01_c4_solver/key01_000` 和 `key01_001` 中的 `domain.npz/rows_variables.json`。
builder 精确选中这 28 项（1,500,773 B，普通 ZIP payload 估计 224,778 B），不复制所在
review/experiments 整树。V18 所有小型 design JSON 原已纳入；新增 B19 数值故障
canonical NPZ/JSON 随本轮全部非 raw 证据保留。没有承诺整个历史测试全集均已独立复现。

`B19_delivery_historical_fixture_red01` exit 1，`green01` exit 0；新增白名单回归后
小自测共 14 类 guard。最新 `B19_delivery_inventory_dry05` exit 0：3,762 个文件，
595,597,441 B 未压缩，非 raw 普通 ZIP 估计 **103,978,605 B**，0 条代表 raw，
剩余约 96 MB 只是当前估计；其中正在运行和已停止批次的全部状态仍保留。
最终主结果与报告到齐后使用固定代表选择重新盘点、构建，核实实际 `<200000000` 字节；无需为估算重复整轮压缩。

## 只持有 ZIP 时的本地入口

最终外置收据用于完整外层 CRC/SHA 和独立解压证明；它们不构成解读 ZIP 内容的隐藏运行依赖。核对最终公布的 ZIP SHA 后，在新的 D/E 目录解压外层。JSON/CSV 采用旧版通用格式；在解压根目录执行交付的通用 decoder，可逐字节恢复全部元数据，再执行本轮独立科学复现入口：

```powershell
python delivery_metadata/metadata_codec_r1.py restore --map delivery_metadata/DELIVERY_METADATA_MAP.json --bundle delivery_metadata/METADATA_ALL_JSON_CSV.tar.xz --destination .
python -B outputs/v19_model_closeout_20261018_run01/scripts/portable_reproduce.py reproduce
```

decoder 拒绝覆盖已存在的逻辑元数据文件，因此解压目录必须是全新的。`reproduce` 会创建独立 `SCIENCE_ROOT`，顺序执行 prepare、真正的 M19 runner、92 项预登记指标对照，另存实际退出码；不会调用原工作区。针对性独立 pytest 命令另见恢复后的 `delivery_metadata/REPRO_COMMANDS.json`。这些是提供的入口，最终实际独立验收仍以外置 `.STANDALONE_VERIFICATION.json` 为准。
