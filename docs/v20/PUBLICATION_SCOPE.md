# V20 发布选择与路径兼容

本轮发布先在D/E盘独立目录准备文件树。`publication/build_v20_release.py --destination <新的绝对目录>` 是完整发布入口；`--candidate` 只生成用于核心测试和代表案例验收的候选快照，不代表406条运行已经完成。科学源码仍由原冻结校验，发布层脚本另以选择清单中的SHA绑定。

正式入口在任何复制前调用只读 `publication/release_gate.py`：重新核对148批/406条的精确分母、登记/冻结/输入/源码绑定、唯一实际attempt、严格终态、完整登记窗口及服务账本、实际reset与已施加外生匹配，以及8类紧凑CSV和审计JSON的原收据SHA。未完成、失败、未知或绑定缺失均拒绝生成final树；通过证明写入 `publication/V20_FINAL_CAMPAIGN_PROOF.json` 并由marker及选择清单绑定。这个门槛不要求零失供或某策略获胜，也不新跑任何物理轨迹。

## 保留什么

- 当前源码、全部冻结测试字节、28份实际运行输入及默认核心门槛。
- 四份原始8760小时负荷表、审查IDF、EPW、设备参数、导出说明与来源收据。
- V20冻结、注册、plan/manifest、每对RESULT、attempt与批次终止记录、紧凑分析、实际门槛日志、字段审计和工程结果表。
- 各批保留8类小型CSV：`V20_RESOURCES`、`V20_EMISSIONS`、`V18_CARRIER_SERVICE`、`V18_ECONOMICS`、`GT_RUNS`、`GT_TRANSITIONS`、`MODEL_DOMAIN`、`THERMAL_COVERAGE`。它们由发布层显式补回，不能用“省略逐区间CSV”将这些资源和服务汇总一并省略。
- 继承实现仍调用的V11/V13小配置、V15来源收据、V17/V18数学及接点验证fixture、v43权限清单。名称反映来源，不能把它们重命名为V20原始实验。
- 9份V19设计fixture支撑固定外生规格与默认campaign回归。基础选择器原含2份，发布入口补足另外7份，并逐项核对本轮起点收据的原始SHA与长度；不从旧raw重新构造。
- `inputs/energyplus/README_setup.txt` 保留原文件名与字节。不能将它改名为README.md而在冻结后改变运行输入库存。

默认核心测试所需的6份V18局部重放fixture见 `provenance/historical_regression_closure/COPY_RECEIPT.json`。其小型JSON可独立满足数学重放，不要求读取本地旧事故轨迹。

## 本地保留什么

原始工程、V19输出与审计快照保持原状。大规模 `trajectory.json.gz`、逐区间CSV、完整SQL/EnergyPlus产物、第三方论文及来源PDF/XLSX、训练数据、模型权重、缓存、重复归档和求解器许可证不进入新版Git树。逐对RESULT和attempt不因失败或未知被筛掉。原始轨迹路径与SHA保存在本轮结果收据中，外部审计原SQL仍需相应原材料。

明确省略的逐区间/逐命令导出包括 `COMMAND_EXECUTION_INTERVALS`、`COMMAND_LIFECYCLE`、`DIESEL_LOCAL_RESPONSE`、`ENGINEERING_INTERVALS`、`EVENT_TIMELINE`、`TRAJECTORY_WINDOWS`、`V18_RECOVERY_STAGES` 和 `V18_TEMPERATURE` CSV；原始本地分析收据中仍保留其SHA，这些引用不意味着文件已随仓库分发。年度原表、逐对紧凑RESULT和最终工程表不因此删减。14份外部文献/来源原件的当前原工程字节已重新核对，登记见 [EXTERNAL_ASSETS.json](EXTERNAL_ASSETS.json)；这些原件均不是M20的28份运行输入，不能沿用V19“严格重放须补齐14原件”的旧说明。

发布脚本对每个选择文件执行104,857,600字节的排除门槛，并在复制前检查档案、SQL和许可证后缀。完整选择清单列出每个实际发布文件的长度和SHA；清单自身不参与递归哈希。三个来源PDF/XLSX的明确Git排除规则保留。若交付另行生成压缩包，单包必须小于200,000,000字节，不能用拆包绕过限制。

## 当前入口与历史追溯

README中的M20入口是当前运行指引。少量根目录旧辅助脚本与继承工具模块在本轮完整生产绑定中保留，不能在运行途中删除并仍声称同一冻结。它们不作为本轮训练、搜索或主比较入口。过时首页和大批旧结果不再作为当前发布内容；V19完整已发表文件树由 `v19-model-closeout` 标签与原提交追溯。

替换main使用普通新提交，父历史保留，不重建仓库、不强制推送、不改变访问设置。实际新增、修改、删除清单在最终发布审查时生成。

## 独立复现的证据强度

冻结的 `scripts/v20_repository.py` 提供基础闭包校验与94个标量比较。完整独立目录验收使用发布层 `publication/reproduce_v20.py`，额外检查发布marker、选择文件SHA、真实attempt资格、窗口及服务ledger完整性、实际初态和已施加外生条件匹配，以及从当前根目录导入项目代码。原工作树中的标量比较不能称为独立克隆验证。

发布后的远程克隆还需实际执行默认核心测试与完整600分钟代表调度。其命令、退出码、原始日志与独立证明保留在该新克隆的review目录；运行未结束前不能记作通过。

Windows初次检出及后续Git审查显式使用新仓库的长路径支持，避免嵌套证据文件因默认路径长度而漏检。仅对克隆子进程追加该配置，并另记录目标仓库本地`core.longpaths=true`的实际命令和退出码；不更改系统全局设置、账户、凭据或其他继承配置。最终审查仍要求全部所选路径、索引blob和有效`-text`属性逐项一致，长路径支持不放宽文件白名单或SHA核验。

本轮要求一次普通提交替换main；推送后的验证证据时间上晚于该提交，不能预填到同一提交中。因此新克隆的 `publication_checks/`、独立代表调度紧凑证明及实际交付状态随最终ZIP追加交付，不为回填证明另建第二次main提交。仓库内保留提交前真实验收与复现命令，最终ZIP及外部receipt记录实际Git提交、后验命令、退出码、成员SHA/CRC和字节数。压缩包须从实际GitHub新克隆执行 `publication/build_delivery_archive.py`，不能用本地候选快照冒充。
