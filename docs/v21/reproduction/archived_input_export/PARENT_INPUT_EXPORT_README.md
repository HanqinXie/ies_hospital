# 非封存归档 PLAN 的未来复现输入

本目录只导出未来复现所需的输入和命令，没有创建新 FREEZE、登记实验、读取 RESULT 或 raw，也没有启动轨迹。两份实际样本均来自已归档的非封存 PLAN；当前研究的尝试数和预算没有增加。这里的重放不是新的独立验证，也不是本轮阶段接受证明。

`export_reproduction_inputs.py` 是薄导出器。原生 `scripts/sci_campaign.py` 已有 freeze、register、run，但没有同时验证外部已知 SHA、拒绝封存支持区间并精确保留归档字段的输入导出命令，因此仅补充此步骤，没有复制求解器或运行器。

## 输入合同

调用方必须分别提供原始 PLAN 和其 FREEZE 的外部已知 SHA。工具不以文件内部自报的 SHA 代替外部权威，也不按 PLAN 记录的旧绝对路径自动寻找 FREEZE。允许显式传入归档副本；原路径仅记录来源。

支持范围固定为 M21 当前源绑定 `4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add` 的 643 个源文件和输入绑定 `659ecc488bda87c9338afde283cbb74135ca2ebdf2769ce463d0e61f0c6288d8` 的 28 个文件。导出时调用原生 `runner.verify_freeze`，核对当前完整源、输入及原冻结排放因子字节。归档 PLAN、FREEZE、PANEL 签名，以及原策略、配置和 Cartesian 规格数均须匹配。

`final_blind` 阶段、最终场景标志和 final 角色均拒绝。每条场景使用原生 `design.source_support`，包括真实预热、完整尾段、端点、声明的扩展支持及初始化小时；与原生 `SEALED_DAYS` 的任何交集均拒绝。此工具不签发、迁移或绕过 final release。最终封存数据的阅读入口仍只能是已正式导出的表，不能由本工具重放旧十二背景。

所有输入映射必须是根目录内的相对路径。重复 JSON 键、非有限值、篡改 spec/config、错误源、既有输出、旧预算或 raw 目录复用及路径逃逸均拒绝。输出是 create-only；若导出中途失败，应保留已产生的前缀和外层日志，再换新目录排查。

## 输出及未来命令

每个成功目录仅含六个文件：`PANEL.json`、`POLICY_SPECS.json`、`CONFIGURATIONS.json`、`SOURCE_MANIFEST.json`、`FUTURE_COMMANDS.json` 和 `FUTURE_COMMANDS.ps1.txt`。前三份文件直接截取原 PLAN 中对应 JSON 值的原始 UTF-8 字节，保留内部空白、键顺序、数值表达、转义、布尔值和 null；不是重新序列化所得。工具还验证这些值在当前原生 normalize/register 契约下保持不变。

命令模板依次使用原生 freeze、register、run；仅 register 的阶段改为 `reproduction`，原场景、策略参数、硬件配置、初始条件和完整窗口不变。策略和配置 ID 均显式传入，不依赖 CLI 的默认五方案或 H0。新 FREEZE 使用显式正尝试数和正墙钟预算，时钟按原生语义从该新冻结的首次真实尝试开始，绝不复用原来已过期的研究预算或 RESULT。示例将尝试额度设为原规格数，默认不重试；若未来需要保留失败后再尝试，应预先另给合理的新额度，而不是覆盖旧证据。

生成的 `.ps1.txt` 是可审查配方，本次未执行。实际未来使用时，应在另一个研究运行根目录执行并保留日志；本目录两份示例分别预留 `E:\hospital_ies_user_reproduction\main_L2_from_archive01` 和 `E:\hospital_ies_user_reproduction\pilot_H2_from_archive01`，本次闭合检查证明这两个目录仍不存在。它们在当前研究树之外，不会与正在执行的后续批次混账。

通用输入导出命令如下，其中所有 SHA 和路径都由使用者替换为其已核验归档：

```powershell
python export_reproduction_inputs.py --root <相同版本工程根> --plan <原PLAN副本> --plan-sha256 <外部已知SHA> --freeze <原FREEZE副本> --freeze-sha256 <外部已知SHA> --output <尚不存在的输入目录> --future-run-root <另一个尚不存在的运行根> --future-wall-hours 4 --future-attempt-limit <不少于原规格数的正整数>
```

随后审查该目录中的 `FUTURE_COMMANDS.ps1.txt`。其中 cwd 必须改为实际解压后的工程根，三份输入路径和未来运行路径也应按实际位置重新导出或明确替换，不能照抄本机 D/E 路径。核心 CLI 使用工程内相对脚本路径。工具本身可放在 `docs/v21/reproduction`，但本轮没有复制到 D，未改变 643 源枚举、365 标量守卫或七项复现 overlay。

输入导出目录不是完整运行包。实际运行还需要同 SHA 工程、必要 inputs、原运行时引用文件及相应 Python/求解器环境；导出器不会下载、复制或伪造它们。新 phase、登记时间和新冻结会改变运行身份，因此未来结果应作为重放证据逐项核验，不能直接与原 RESULT 文件字节相等，也不能据重放增加独立背景样本数。

## 两个真实元数据样本

| 输出 | 原阶段及规格 | 原 PLAN 外部已知 SHA | 原 FREEZE 外部已知 SHA |
|---|---|---|---|
| `actual_main_L2_01` | formal；一场景，五策略，H0；保留原 F1/A240 | `ed3c2e5a5e224ec413a9edac41d5bfe805c22a554a59eac4939ac66734e617b2` | `a868f610fa593b4e8b0184ef72f35e1ca76b16693da44cfd516ab9c8e0f22e43` |
| `actual_pilot_H2_01` | pilot；一场景，C，H2；原 90 分钟功能例 | `1f8ebb46c7755557a879d6d5f480d77fbe54b3229eea84c86e7e537197f00ddf` | `adfebdc794ba55aba18f5a7fe1ac83f344edc9f38b27575972cc6968413c0e05` |

H2 样本用于检验非默认配置的完整传递，不构成与 H0 配对的硬件优势。两次导出的真实外层耗时分别为 0.9815964 和 1.0157395 秒，exit 0。`METADATA_EXPORT_QA01.json` 重新检查六文件精确集合、原字段逐字节相等、签名及完整 643/28 字节、未来目录未创建、命令未执行；实际 exit 0，1.9157288 秒。

## 实际验证与保留的失败

原始命令、退出码和 stdout/stderr 均由 `tools/p0_audit/run_logged.py` 保留在 `logs` 及 `commands_and_exit_codes.txt`。本次没有运行原生 freeze、register 或 run；只读过它们的源接口。

| 日志名 | 实际结果 | 含义 |
|---|---|---|
| `exporter_red01` | 22 failed；exit 1 | 先写必要测试，导出器尚不存在 |
| `exporter_green01` | 1 failed、21 passed；exit 1 | Windows 上 `/absolute.py` 没有盘符，原初始路径判定遗漏 rooted 路径 |
| `exporter_green02` | 22 passed in 0.32s；exit 0 | 增加 `PureWindowsPath.root` 拒绝；未改变原生源码、物理或容差 |
| `actual_main_L2_export01` | exit 0 | 第一份真实非封存元数据导出 |
| `actual_pilot_H2_export01` | exit 0 | 第二份真实非封存元数据导出 |
| `metadata_export_closeout01` | exit 0 | 两份原始字段与当前源输入再次闭合 |
| `exporter_help01` | exit 0 | 新工具 CLI 实际帮助入口 |

修复前原工具字节及原因保存在 `history/before_rooted_path_fix`，原 SHA 为 `ec54ace1515ab6a73a7923a6e4f552b758f1a1baf657c013e7a9c406d712c5b4`。测试覆盖原对象保持、JSON 原始片段、spec/config/源篡改、final 标志与支持区间、初始化封存小时、布尔小时、重复键、五类路径逃逸、错误外部 SHA、既有输出和非正或非有限未来预算。

未执行项：新冻结、新登记、物理重放、数值重算、最终十二背景读取、独立解压运行、科学或临床验收。原生未来命令的真实运行仍由使用者在新的预算和输出目录内完成，本目录不把命令存在视为执行通过。
