# 复现输入导出修订二：未来脚本遇错停止

本目录是 `reproduction_inputs_prepare01` 的新兄弟版本。父目录 44 个成员、原收据 `ad985626535e8c7025d06b456bb0f5478afa14740639dea9b71b4fab850f3710` 和原工具 `d8055f33893fdc7debdfd564f1c0990835d0d219db63488c3c74387421502c2a` 全部保持原字节。父版本的输入导出和字节核验有效，但其中未来 PowerShell 配方有失败传播缺口，不能作为当前可运行配方使用。本次只修复生成模板，没有改研究源、物理、策略或预算系统。

## 已实际定位的缺口

Windows PowerShell 5 默认不会因原生命令非零退出而自动停止脚本。root 已真实运行 `reviews/reproduction_inputs_root_review01/REPRODUCE_POWERSHELL_CONTINUATION.ps1`，日志名 `powershell_native_failure_continuation01`，外层 exit 0、0.280475 秒；第一条模拟命令 exit 7，后续模拟命令仍运行并以 0 结束。此证据不包含研究冻结、登记或物理运行。

此外，导出时未来根目录尚不存在，不能保证使用者晚些执行配方时它仍不存在。父模板可能在新 freeze 或 register 失败后继续指向既有实验目录。本次没有自动接管、重试、清理或覆盖这些目录。

## 最小修订

生成的 `FUTURE_COMMANDS.ps1.txt` 在任何原生命令之前重新核对未来运行根不存在，并对 `SOURCE_MANIFEST.json` 及三份原始输入文件的当前字节做 SHA 检查。所有已知 SHA 都直接固化在生成模板中，不能通过同时重写 manifest 来接受输入漂移。缺文件、目录已存在或任意字节漂移均立即 throw，尚不启动任何原生命令。

模板设置 `$ErrorActionPreference = 'Stop'`，并在三条原生命令各自返回后立即读取 `$LASTEXITCODE`；非零则抛出包含实际退出码和步骤号的错误，后续步骤不执行。失败输出及已产生的前缀应保留，不自动重试、采用旧结果、删目录或回退到其他配置。成功执行过的根目录也不能第二次采用同一配方。

`FUTURE_COMMANDS.json` 新增 `execution_contract`，明确相同的运行前目录及四文件 SHA 检查、逐步退出码检查、首次非零停止和禁止接管既有 campaign。JSON 本身只是参数与合同，不会执行或强制执行它们。另写消费者必须完整实现这些约束，不能仅循环调用三份 argv 后以最后一步返回码报成功；优先使用已审生成的 PowerShell 配方。

原 JSON 字段提取、外部已知 PLAN/FREEZE SHA、签名、643 源和 28 输入、原 spec/config、完整 source support 与封存十二背景排除、create-only 及新正预算要求保持不变。原 22 项测试文件逐字节保留。三份导出输入与父目录对应样本也逐字节一致，原 F1/A240 和 H2 均未修改。

## 真实验证及边界

新增七项测试在真实 PowerShell 5 进程中执行生成模板，但把 `python` 名称解析到专用 fixture 函数，该函数只运行 `fake_native.py`。它只记录调用次序、返回模拟退出码或创建 pytest 临时 future 目录，绝不调用 `sci_campaign.py`。测试保留 stdout/stderr 原始字节和退出记录。

测试覆盖 freeze、register 和 run 各自 exit 17 时停止；既有 future 根、PANEL 或 manifest 字节漂移时零原生命令；成功时仅三次模拟调用，第二次执行拒绝且不增加调用。旧模板真实红测为七项全部失败；首次红测另外暴露混合终端编码的捕获警告，原日志和测试快照保留。随后仅把测试捕获改为原始字节，第二次红测仍七项全部失败且无警告。修订模板后，原 22 项加新七项共 29 项实际通过。最终日志和精确耗时见 `logs/script_stop_green02.command.json`。

两份实际非封存 PLAN 又在本目录 create-only 导出：`actual_main_L2_01` 保留一场景五策略 H0，`actual_pilot_H2_01` 保留一场景 C/H2。它们只导出元数据，未来运行根使用 `E:\hospital_ies_user_reproduction\main_L2_from_archive02` 和 `E:\hospital_ies_user_reproduction\pilot_H2_from_archive02`；闭合检查证明这两处仍不存在。三份字段与原 PLAN 及父版样本逐字节一致，四个新 preflight SHA 均与当前文件相同。

本次没有执行真正的 freeze、register、run、求解或轨迹，没有读取 RESULT、raw、sidecar 或最终十二背景。模拟函数并不是研究物理通过证明。未来重放仍须使用匹配的完整工程、输入和运行时引用，在新的路径和正预算内执行，阶段为 `reproduction`，不能增加本轮独立背景数或替代阶段接受。

通用导出调用参数及移植说明沿用父 README，当前应采用本目录工具重新导出；不要直接执行父版旧配方。新生成的 `.ps1.txt` 是可审查文本，未来使用者核验后可另存为 `.ps1`，并用既有 `run_logged.py -- ... powershell.exe -NoProfile -NonInteractive -File <新配方.ps1>` 保存整个脚本的真实退出、stdout 和 stderr。这个外层日志目录必须与未创建的 future 根分开，否则提前创建 future 根会被本模板正确拒绝。本次未执行这条真实研究命令。

新目录只属于交付准备修复，不改变共享 D 工程、643 源枚举、28 输入、七项 overlay、365 标量守卫或正在运行的后续批次。差异与 AST 证明见 `TEMPLATE_ONLY_REVISION_PROOF01.json`，所有红、绿和模拟失败记录保留，不将模板存在视为原生运行通过。
