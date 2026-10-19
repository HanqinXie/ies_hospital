# V20 复现说明

## 本轮已执行的独立候选验收

发布候选 `D:\hospital_ies_v20_candidate_20261019` 已实际完成默认核心测试：463项通过，退出码0；完整600分钟E策略重放亦退出0。94项预先登记标量的最大绝对差为0，实际初态、已施加外生、注入状态、配置和输入匹配，父/子进程原树读取审计通过。命令、原日志和严格独立证明的紧凑副本见RUN的 `provenance/independent_candidate01/`。

这次是推送前候选目录验收，不是推送后的GitHub新克隆验收。后者将在最终发布后另行执行并登记；不能用上述结果提前记作完成。

V20保留原4份负荷CSV、IDF/EPW与设备参数，改变字段分组算法。`MODEL_FREEZE_ACCOUNTING01.json`绑定当前生产、实际输入、完整测试字节、共同配置和策略；不修改V19签名宣称同版本复现。

## 独立克隆的运行入口

Windows下少数嵌套候选验收证据的相对路径达248字符。使用新D/E目录，初次检出即启用Git长路径，并在新仓库保留本地设置：

```powershell
git -c core.longpaths=true clone https://github.com/HanqinXie/ies_hospital.git D:\ies_v20
Set-Location D:\ies_v20
git config --local core.longpaths true
```

正式交付的`publication/clone_release.py`保持原克隆argv，通过子进程专用Git配置启用首次检出的长路径，成功后另执行新仓库本地设置并保存真实命令/退出码。继承的其他Git环境配置不改，不记录其敏感值；不修改系统或Git全局设置。配置失败保留clone实际退出码与独立helper失败状态，不能冒称远端验证通过。

```powershell
python -m pip install -r requirements-v20.txt
python scripts/v20_repository.py verify
python -m pytest -q
python publication/reproduce_v20.py --name fresh01
```

在D/E盘目录执行，自行配置适用的Gurobi许可。默认测试由 `pytest.ini` 明列，覆盖适用原核心与新增回归；其余保留的历史测试可能需要额外研究数据，V19特定冻结只在原标签验证。

`verify`逐项核对新生产、测试、运行输入与冻结，实际构造四策略工厂，并检查项目导入没有逃出当前根目录；发布树存在 `publication/V20_FILE_SELECTION.json` 时还核对所选文件SHA。M20运行不需要未使用的第三方论文及来源PDF/XLSX。

发布层 `reproduce_v20.py` 要求选择清单和发布marker存在，拒绝在原科学工作树执行独立复现声明。从已登记 `v20_main_000` 获取同一外生规格与E偏好，新建 `experiments/reproduce_<name>`。先登记参考标量与固定容差，再实际运行完整600分钟，比较请求/交付/ENS分解、中断、能源及费用；另核验双方真实attempt资格、窗口/服务ledger完整性、实际初态与已施加外生条件、源/配置/运行输入匹配和当前目录导入。失败证据不删除，每次使用新名称。它不重跑全部406条，也不启用最终12背景。冻结基础helper的94项标量通过本身不能称为严格独立克隆证明。

## 固定面板与结果整理

本轮登记由 `scripts/v20_campaign.py register` 建立148批次、406对，其中正常12对拆为独立单策略批次。已经存在的注册/attempt拒绝覆盖；`run`只处理对应登记中的待运行批次，对已有记录逐项验证，不能把跳过旧结果称为新重跑。

本轮实际执行命令、资源准入与检查点见新RUN的 `logs/`、`orchestration/` 和 `design/CAMPAIGN_final01.json`。短窗并发按可用内存与每个在途子进程的完整预留控制；正常长窗全局隔离串行。原72小时C19的采样private峰约24.7GiB、OS峰RSS约22.7GiB；本轮正常窗另检查系统提交余量。准入估计不是硬保证，资源不足保留未开始记录。

提交前实际闭合为148批、406条全部严格合格，完整census及逐批证据见`review/normal008_continuation01/L72_RUN02/FINAL_406_PROOF.json`，同窗最终报告为`paper_results/FINAL01`。`normal_analysis03_full_guard01`实际188项通过后，唯一`normal_analysis_collect03`退出0形成12条正常窗分析。四个正常窗口分别比较，不用其时长不同的总量相加排名。

只读重建报告时使用新标签，不覆盖旧表：

```powershell
python scripts/v20_report.py --label READBACK01
```

如需重建与V19逐例对照，加 `--baseline-root "D:\path\to\complete_original_v19_project"`。该步骤只读旧紧凑RESULT、plan、manifest及attempt日志，不开启旧raw/年度物理值；缺少旧证据则明确未知。全部面板再次运行应另建明确的新研究目录/版本，不删除现有attempt或修改其manifest。

## 来源审查条件

原SQL约115MB，完整EnergyPlus产物和第三方原件留在本地。导出脚本、SQL版本/字典/键、IDF连接、逐小时匹配和来源SHA见 `provenance/thermal` 与 `provenance/load_grouping`。这些紧凑表证明已执行的审查；重新在源SQL上取证仍需原字节，不能据此宣称重新执行了EnergyPlus年模拟。

14份未分发文献及来源PDF/XLSX见 [外部原件清单](EXTERNAL_ASSETS.json)。本轮重新核对了它们在原工程中的长度和SHA，但它们不属于M20运行输入，不要求为上述调度命令补齐旧V19的66文件库存。Gurobi适用许可仍须由运行者提供。

旧命名的小配置、来源收据与数值域仍有实际运行/回归依赖，保留原路径。V20新入口和M20身份区分本轮结果；历史运行与旧首页通过V19标签追溯。

完整发布入口是 `python publication/build_v20_release.py --destination D:\新的发布目录`。它调用冻结基础选择器，补齐7份必要旧设计fixture并逐项核对来源SHA，保留原名 `README_setup.txt`，恢复三个来源原件的排除规则，并执行Git单文件大小门槛。加 `--candidate` 会排除运行中的批次和日志，仅供核心门槛和代表案例预验收。详细路径与原材料边界见 [发布范围](PUBLICATION_SCOPE.md)。

正式复制前还须通过 `release_gate.py` 的148批/406条完整资格核验；窗口的complete标志不能替代完整服务账本、实际终态与版本绑定。门槛证明及8类小型资源/服务CSV随最终选择清单保存。该门槛只读已有结果，不能将它计为再次执行406条。

正常窗口的资源采样工具另需 `python -m pip install -r publication/requirements.txt`，其中psutil按本轮实际7.2.2版本登记。`python publication/run_normal_serial.py --check-only --name <新名称>`只输出资源与资格计划，不启动调度；真正 `--run` 仅执行原已注册的12个单策略normal批次。它要求其他394条比较严格合格、未发现M20 runner，并同时检查物理可用内存与系统提交余量。准入按同规格V19实际RSS/private峰的1.2倍加6GiB宿主余量，不能用E的小内存替代72小时C19估计。其lease只约束该工具的合作调用，不是OS级全局互斥或内存硬保证；操作前必须撤去其他本轮launcher，不能并发手动启动原normal入口。

本轮 `normal02/canonical01` 在首条真实资源采样时发生重复 `utc` 参数错误，capture退出2，`supervision02` 如实停止。其拥有的 `normal009` 调度子进程被等待到正常退出0，随后独立严格核验其唯一attempt、完整1440分钟账本及绑定，科学结果合格。该批SAMPLES为0字节、缺少完整资源结果，故本轮RSS/private/OS峰仍为unknown，不能用V19估计或其他M20批次补填。发布层仅修正日志字典构造，先实际复现2项失败，再获18项通过；当时全发布层115项及7个subtest通过。原源码、最小diff、红绿命令/退出码/日志、旧失败和续行绑定见 `provenance/publication_layer/normal_timestamp_fix01/FIX_AND_CONTINUATION_RECEIPT01.json`。冻结科学源码、输入和测试字节没有改变。

`normal03/canonical02` 严格跳过009并实际完成另外10条，剩余008受原资源门槛阻塞。405条严格前置通过后，向原STOP写入交接意向，旧normal03自然退出125，旧S3保留`failed_unknown`；不把它们改成通过。新单例资源估计及进程边界见 [资源续行计划](RESOURCE_CONTINUATION_PLAN.md)：同72小时C19的V19峰×1.1加至少2GiB，实际连续稳定观测后才可启动，不能冒称满足旧1.2倍加6GiB规则。轻监测的重资格在短命子进程内，父进程实测约16MB且无重计算模块。

首个轻监测`L72_RUN01`实际暴露巡检耗时叠加固定sleep导致约4秒采样间隔的问题，稳定守门正确拒绝启动；008没有attempt。该自建闲置监测器经身份/无子进程/无科学记录核验后停止，capture真实exit15，旧源码和49条样本保留。修正采用按deadline扣除工作耗时的采样调度，不能放宽最大3秒间隔、30秒稳定窗口或科学判据。新的实际启动、后验及analysis03须另有命令和终态；旧analysis02/once02保留为准备未执行，不复用其要求旧S3通过的契约。

修正后的真实`L72_PLAN02`末16点37.156秒、最大间隔2.641秒，时序谓词合格但提交余量不足，实际exit3、科学启动0；全71点另有4次早期超3秒，不声称整个探针都达标。最终`L72_RUN02`重新观察31.313秒、17点、最大间隔2.125秒并满足双资源门槛，以1.1倍同72小时C19历史峰加2GiB准入；随后唯一008实际1024.062秒、exit0，430样本无未知，private采样峰24,820,436,992字节，OS峰读数22,719,717,376字节。后验和FINAL01严格148/406，外层`normal008_lightweight_run02`实际exit0。11条新峰有实测、009仍unknown，不能将历史估计填成新测量。analysis03真实一次性采集已验证完整前驱链及这些终态，旧错误/停止收据均未改写。

## 发布后的验收与单包交付

在实际推送成功的正式发布目录启动一次新的GitHub克隆，再在新克隆运行上面的核心门槛和代表调度；不能把候选目录结果复制为后验结果。实际验收通过后，在新克隆执行：

```powershell
python publication/build_delivery_archive.py --destination D:\hospital_ies_v20_delivery_20261019\Hospital_IES_V20_20261019.zip --proof-name fresh01
```

该入口要求实际clone收据、`core01`命令、`fresh01`严格重放证明及Git/选择清单一致，再生成并逐成员核对单个小于200,000,000字节的ZIP。每个名称必须对应本轮真实执行记录。推送后证据在提交之后产生，保留于新克隆 `publication_checks/` 和最终交付包；不预填为Git提交内的已完成事实，也不为回填它们追加第二次main提交。
