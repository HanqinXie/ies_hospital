# V21 运行与复核入口

状态（2026-10-25北京时间整理）：a5d3ed7/source4dfe共同工程模型及F1/A240未改。主1920、Q1补充240、支持252、必要界112、硬件126与最终240六科学阶段均已接受，原master256四构造失败仍UNKNOWN；硬件22图及final12正式F1/F2已发布。科学计算、审查与正式图件已完成；冻结紧凑ZIP已通过成员核验与新解压，当前4dfe源实际完成365/365标量完整重放及新默认pytest（692 passed, 1 skipped in 218.45s (0:03:38)）。最终普通Git提交/推送状态以Git和外置最终回执为准，不由本proof代签；本次归档与运行证据见交付验证说明。 下列命令是复核配方，历史运行与本地镜像再执行状态分别说明。

本轮模型为 `M21_sci_engineering`。旧 M20、旧失败与审计记录保持原身份。新研究源码在独立仓库 `D:\hospital_ies_v21_20261023`，本轮大日志在 `E:\hospital_ies_v21_20261023`。不能直接调用已过期的历史发布预算来证明新运行成功。

## 软件与文件

实际环境为 Windows、Python 3.10、NumPy、SciPy、pandas、Gurobi、pytest 和 psutil；运行求解需要本机有效 Gurobi 许可，许可文件不随交付提供。MATLAB R2024b 用于最终科学绘图和 FIG 验证。完整版本见实际环境清单。无需原始 EnergyPlus SQL 即可使用已发布、按 SHA 绑定的辅助电分配 CSV 复现 M21；重新抽取辅助拓扑及执行独立 EnergyPlus 验证需要原始 IDF/EPW/SQL 与 EnergyPlus，外部文件不伪装成仓库自带文件。

## 默认测试

本轮实际使用 CPython 3.10.6。紧凑安装清单在 `reproduction/requirements.txt`，可用 `python -m pip install -r docs/v21/reproduction/requirements.txt` 安装；Gurobi 仍需单独有效许可。清单保留历史测试所需 Matplotlib 依赖，本轮科学图实际由 MATLAB R2024b 生成。

重跑MATLAB导出物的Python格式检查另需 `python -m pip install -r docs/v21/reproduction/requirements-figure-qa.txt`，其中PyMuPDF 1.27.2.3与Pillow 12.2.0已在当前实际检查器导入中核对。它们检查PDF/SVG/PNG结构和字体，不代替MATLAB重开FIG的原生对象验证或目视检查。此次依赖导入检查没有运行物理；实际reset/step依赖仍以最终干净目录完整重放验证为准。

从仓库根目录运行。临时目录的父级必须先存在；大临时文件使用 D/E。

```powershell
New-Item -ItemType Directory -Force 'E:\hospital_ies_repro\tmp'
python -m pytest -q -rs --basetemp E:\hospital_ies_repro\tmp\pytest01
```

`pytest.ini` 保留历史支持的物理/数值核心，并加入 M21 的真实辅助依赖、有限存储、因果观测、执行身份、日志完整性、封存释放、统计及当前阶段优先级回归。测试中原始 SQL 不存在的可选源抽取项可以明确跳过；已知核心问题不能靠 xfail、扩大容差或删除断言隐藏。

便携内容另须携带旧数学回归的test-only支持文件。当前静态审查发现48个已有文件（4,127,061字节）不在643源/28输入/4运行时引用/7代表重放覆盖清单内，其中两份V18数学replay在默认pytest收集阶段就被导入；只带365代表依赖不能继承本机692通过。本次冻结包除这48项外另补三份原字节历史测试依赖，共51项/8,509,214字节，均按原相对outputs路径携带，并在新解压目录实际运行默认回归（692 passed, 1 skipped in 218.45s (0:03:38)）；原命令与摘要见DELIVERY_VERIFICATION.md。未增加skip、改pytest入口或扩大容差。具体已知缺口及SHA见E reviews/portable_default_test_dependencies01；这48项静态发现也不冒充动态依赖全部闭合。

## 新预算下的完整代表运行

以下仅使用已见 hour 4001，生成四类扰动，每条包含 60 min 连续准备、120 min 事件、180 min 恢复。静态初态只初始化一次，不把准备次数当独立样本。该工程重放不是新的独立确认。

```powershell
python scripts/sci_campaign.py pilot-proposal --output E:\hospital_ies_repro\PILOT_PANEL.json
python scripts/sci_campaign.py freeze --output E:\hospital_ies_repro\FREEZE.json --policies C --attempt-limit 8 --wall-hours 4
python scripts/sci_campaign.py register --panel E:\hospital_ies_repro\PILOT_PANEL.json --freeze E:\hospital_ies_repro\FREEZE.json --directory E:\hospital_ies_repro\experiment01 --raw-root E:\hospital_ies_repro\raw01 --policies C --phase pilot
python tools/p0_audit/run_logged.py --run-dir E:\hospital_ies_repro\audit --name representative01 -- python scripts/sci_campaign.py run --directory E:\hospital_ies_repro\experiment01
```

使用新的路径和运行名；不可覆盖旧 PLAN、RESULT、raw 或日志。每次尝试写 started/finished journal、实际 RESULT、主轨迹 gzip 和完整求解行 sidecar。失败保留真实前缀与未知尾段，不以零替代。`--retry-failed` 只在定位问题且源绑定仍一致时使用，产生下一 attempt，不改写旧失败。源码或输入改变必须新冻结；复用目录会被明确拒绝。

## 按原五方案或硬件配置重放

`reproduction/archived_input_export/export_reproduction_inputs.py` 是已独审的修订二输入导出器（SHA `6f74470ef7554fd00fb10624a301f0bb5eeac5dcf7cb23c10dbdd9a9c7e93cfb`）。它从明确指定、已核验SHA的非封存原PLAN及匹配FREEZE，逐字节导出原PANEL、POLICY_SPECS、CONFIGURATIONS，并生成新的 `reproduction` 阶段命令；不执行研究、不改变原方案或容量，也不把新重放计为独立验证。原五方案的完整规范及H0/H1/H2配置由所选PLAN决定，不能把单C示例称为五方案或硬件比较。

使用时先按交付清单核验完整源码及28输入，或在匹配的新工程根重新运行此导出器；导出器实际会核验643源及28输入。提供所选原PLAN和FREEZE的实际文件路径及清单内已知SHA，选择互不包含、均未存在的E盘输入目录与未来运行根，并显式给定新的正墙钟预算。CLI可通过 `python docs/v21/reproduction/archived_input_export/export_reproduction_inputs.py --help` 查看；完整通用参数见同目录 `PARENT_INPUT_EXPORT_README.md`，采用当前修订二程序重新导出，不能执行父版旧脚本。

生成的 `FUTURE_COMMANDS.ps1.txt` 在运行前重新检查未来根不存在、manifest及三输入SHA，并在每条原生命令非零退出时立即停止。核验后另存为PS1，用原 `run_logged.py` 包裹整个PS1；外层日志目录必须位于未来运行根之外，不能预建未来根。JSON中的argv只是命令数据，消费者必须实施其完整execution_contract。失败前缀保留，定位后另选新路径，不自动接管已有实验。此处源码及配方修订已通过29项真实元数据/PowerShell模拟测试；模拟测试中的fake命令没有调用物理，不能替代最终解压后的365标量完整重放。随附测试及作者/独审收据保留原D/E历史路径，用于追溯该次验证，不能直接当任意机器上的便携测试入口。

## 汇总与 MATLAB 图表

```powershell
python -m tools.sci_engineering.analysis --campaign E:\hospital_ies_repro\experiment01 --output E:\hospital_ies_repro\analysis01
python -m tools.sci_engineering.figures --analysis E:\hospital_ies_repro\analysis01 --output E:\hospital_ies_repro\FIGURE_CONTRACT.json
```

该汇总实际逐行核验 sidecar、文件 SHA、请求/交付/缺口与完成身份。未完成或证据漂移的科学字段保留未知。C 单方案代表运行不会生成五方案比较柱；五方案图必须来自五方案匹配完整的数据，不能补零凑图。

`docs/v21/figures/plot_sci_engineering_publication.m` 读取实际 CSV/MAT；保留原 `plot_sci_engineering.m` 作为历史配方。新版只修复分类标签、Bar对象默认字体，数值、统计和数据不变。绘图后可用 `style_sci_class_ticks.m` 将多类别刻度旋转45°，输出到新目录并保留全部源导出。当前正式主比较F1/F2已实际在primary19上重开核对，交付于`figures/main02_primary19/`；四张固定C实际过程F6已独立完成对象、嵌入字体、页边及标签复核，交付于`figures/main02_C_process_F6/`。后续九图已完成MATLAB、对象、矢量/字体/600dpi及全页视觉核验，见`figures/followup_supported252/`：正常五方案F1/F2、机制F3及六张能力界F5。其原始256登记数、有效252及4项未知在随附证据中分别保留；图不得被解释为全部256成功。追加的正常C/H0及固定边界067C/A三张过程图已实际完成MATLAB、原生对象、格式与root完整PNG/PDF视觉核验，见`figures/fixed_process_supported252_02/`，含本地CSV/MAT、原绘图代码和相对路径入口；本地入口未另重复执行，不将复制与静态检查冒充便携MATLAB重放。同一067C设备状态补图见figures/device_state_fixed067_C_H0，四F4及十八硬件过程图见figures/hardware126_actual01，均已实际完成MATLAB、原生/格式与全页视觉核验；硬件本地FIG再导出入口单列于figures/hardware126_reexport_entry01，硬件已验证原生FIG的本地再导出已于2026-10-24 20:13:24 UTC取得真实exit0（246.4177489 s），输出为E:/hospital_ies_v21_20261023/reviews/hardware22_portable_runtime01/generated01；4+18 FIG及88新格式、源FIG至staging链和native snapshot数据exact检查通过，原scanner两组均exit0。字体验收为宋体/Times白名单和PDF嵌入，不证明逐对象FontName/Size前后exact；新44完整页视觉未重复，不能继承原正式22图的44页视觉接受。它是原生FIG再导出，不是CSV重算或科学重放。实际小证明已发布于docs/v21/figures/functional_entry_checks01/README.md。先前资格池图保留原身份，不能代替正式图。过程图使用 `tools.sci_engineering.figures --raw ... --raw-sha256 ...` 导出的原生区间，保持事件时刻，不插值制造轨迹。最终交付 FIG、SVG、PDF、600 dpi PNG、`.m`、CSV/MAT、重开 FIG 的原生对象验证与目视 QA。中文采用宋体，拉丁字符和数字采用 Times New Roman。

`results/`现有已接受全主池、primary19、debug5、Q1补充及supported252的原字节CSV/MAT与各自来源清单，另有原256完整失败披露表，见`results/README.md`及各池README。这些数据允许直接用相对仓库路径重画，不依赖旧E绝对路径存在；原生收据与图内UserData中的绝对路径仍作为历史来源保留。副本不重估CI，不把调试或Q1混入主19；252支持表和原256账是同一批数据的不同视图，不能合为508个样本，四失败量不得补零。正式硬件全126表已发布于results/hardware126，含原CA56与补充CAM84/M28，正式22张硬件图及其原生CSV/MAT、代码、实际对象/格式/视觉收据已发布。

最终12背景原生240已科学接受；原cohort CLI实际110.5482911 s、exit0，final12只读取自身原生池，保持H0/720 min和12源块4/3/2/3，不与主19/Q1合并CI。已发布[结果与完整统计/事后解释](results/final12_actual01/README.md)及[正式F1/F2与本地recipe](figures/final12_actual01/README.md)。当前F1只将Padding compact改loose并重新openQA和视觉通过，旧裁切失败另保留；CSV/MAT本地配方依次调用原plotter、tick和F1-padding三个函数，原镜像复制时recipe为NOT_REEXECUTED；随后root于2026-10-24 20:24:49 UTC实际从D本地CSV/MAT和三个原函数重绘至新E/generated02，30.7815965 s、exit0。10轴/50bar/200Y点及X/Y/CData/RGB、轴UserData/limits/ticks/labels/fonts/papersize exact，436原D镜像文件currentbyte不变，新八格式矢量/600dpi检查通过；figure source路径按新D来源如实变化。功能收据为E:/hospital_ies_v21_20261023/reviews/final12_local_recipe_runtime01/FUNCTIONAL_ENTRY_CLOSURE.json（SHA `6fe0b7cc02dce8e9b98723e67615f4b44373586e200726f834f5d45d4d1264bf`）。旧外层struct collection失败01（31.2254081 s、exit1）保留，最小cell修复02未改D三个函数。新输出未重复四完整页视觉，原正式图的视觉接受不代签这批新输出。

有限UPS与蓄冷的当前源功能过程图、数据、`.m`和实际核验收据在 `figures/storage_functional_current_source/`，该目录README给出可运行MATLAB入口。两条90 min功能轨迹只验证账目和接续机制，没有H0配对，不代表正式硬件增益。旧 `storage_functional_validation/` 保留原源身份。

当前供能关系图在 `figures/supply_topology_current_source/`，同目录README提供两个显式参数的MATLAB入口、实际四格式及重开/结构/视觉收据。图已澄清建筑直接电、风机/水泵依赖、P3非空气热量上界、中央附机一次计入及H1/H2资格；它是共同聚合模型示意，不是已验证现场回路或实际保障时长。

温度独立检验的当前出版图在 `figures/temperature_validation_current_source/`。两显式路径的MATLAB函数读取随附三CSV，已实际生成、重开FIG逐10条曲线对源CSV精确核验，并完成字体、格式和视觉检查。该通过只针对图表；生产代理的夏季温度/恢复误差仍然较大，拟合候选未部署，不能当临床温度保障验证通过。原八次EnergyPlus数据及旧图保留，当前温度模块未改，本次未新增EnergyPlus运行。

## 开发、正式与封存数据

F=0.5/1/2 h、A=120/240 min 的筛选必须使用开发角色 PLAN 和预登记规则；原 `selection.finalize` 拒绝确认集、失败/缺失候选及不一致配对。关闭选择后，R/M/F/C/A 及 H0、代码、输入、评价口径与确认面板共同冻结。正式运行必须提供真实通过的工程 pilot proof。

旧 12 日没有默认通行权。只有开发永久关闭、最终五方案/H0 冻结、完整最终面板固定、原历史实际支持审计通过后，才允许一次 grant 和绑定的 execution receipt。不能清空 sealed-days、伪造 receipt、局部查看或以旧12结果调参。大批量 JSON 任务清单和各阶段状态保留原证据；本次实际全局预算与重放命令见DELIVERY_VERIFICATION.md及其外置proof。

两项实际本地入口检查及完整原命令/小回执现已单列发布于[functional_entry_checks01](figures/functional_entry_checks01/README.md)。该小证明补充旧镜像README的复制时状态，不修改原镜像或给新输出补签未运行的4/44完整页视觉。

## 当前源紧凑交付的实际验收

主1920、Q1补充240、支持252、硬件126、最终240这五个实际科学池共2778条轨迹；必要界112属于支持252子集，作为第六科学阶段单独审计但不再加112。原后续master256另有4条构造失败/UNKNOWN，不能当作已执行事故或补零。primary19、Q1_3、final12分别以19、3、12个源块为统计单位，不合并CI，也不是34家医院。

本次最终单ZIP为319053385十进制字节，严格小于400,000,000，SHA为`b92d5f2024d8fb5605a5f52b9f58e6bb7a1aec074525ffd5331e6d1e2cfc46d0`；其build、成员核验与新解压已有实际证据。首次原八完整raw组真实ZIP为812,808,427字节并因硬上限失败，失败包和退出保留。随后仅按原优先级完整保留前两组（1条当前源参考和5条主L2方案，共6轨迹/12 gzip），尾六组以external_raw索引和PATH_REMAP_ACTIVE.json保留；全科学RESULT、失败、UNKNOWN、表及原23,453非raw成员没有因尺寸删改。原48项加三份原字节缺项共51项test-only支持文件已按原相对路径携带并实际通过新默认pytest；365重放与默认测试分别验收。当前source4dfe的643源码、28输入及4运行时/7代表复现文件仍按原manifest固定。

便携包项目根README来自`delivery_current_source02/project_overlay`的guarded复现文件，固定SHA `6e36c6bf125f23e6a0d8c008523aeb2f59120721f537c9fddb485b99115bd0fd`，与当前仓库研究README是两个有各自用途的文件；本次未改overlay或固定4+7。本次新解压完整365重放、默认pytest和源/成员闭合已有实际命令与终态，见[交付验证](DELIVERY_VERIFICATION.md)；最终普通Git提交/推送状态以Git和外置最终回执为准，不由本proof代签。ZIP是验收前冻结的科学快照，其中pending是当时状态；包含ZIP自身SHA的后续验收proof与本文档更新保留在ZIP外，不重新打包形成自SHA循环。

## 历史基线

历史基线是 `c9226ea15d6cf9e2f0e1d6570d04929aecd26d2e` 的 M20。它的发布 wrapper 已实际因旧 48 h 时钟到期而在物理开始前失败；该失败不是新模型失败，也不能更改历史时钟制造通过。独立新预算下的未改动 M20 完整 600 min 重放已与 94 项原参考量逐项一致，另一个干净 c922 工作树也完成核验。证据及限制见 `BASELINE_CONFIRMATION.md`。

新 M21 的独立目录 `E:\hospital_ies_v21_20261023\clean_m21_replay03` 已真实完整运行 C/H0、L2、360 min，365/365 个预登记标量与同源参考一致；代码绑定为 `2088cd0a61b1d6e07a34619cfafa2b559a25c30aa1f40b8a5bb62848ea95cfd7`。源、输入与复现工具前后 SHA 验证通过，运行守卫检查 7128 次操作、0 违规，项目导入均来自独立目录。验收为 `independent/INDEPENDENT_REPLAY_PROOF.json`，SHA `6b7f4256138df66a3add71c169662555ea5da324104cd231ebdc10806381d2ae`。这是运行依赖独立性验收，不是新增医院或未见时间背景。

此前 clean02 已有一次物理完整轨迹，但工具收尾使用旧的 attempt 计数字段而报 KeyError、外层退出 1；该次不计独立复现通过。原目录、包、成功物理结果和外层失败均保留，新目录修复工具后实际重跑，未修改物理模型或旧证据。

2026-10-23当前源码补充：a5d3ed7 / source4dfe62ae 的643个源文件和原28输入已通过692项回归、45条全完成/全物理核验资格批次。上述clean03属于旧source2088，不能充当a5d的新独立复现。新的小型复现覆盖材料位于 `E:\hospital_ies_v21_20261023\delivery_current_source02`，使用当前资格批次完整C/L2/360 min结果重新取齐365个标量，原守卫/重放算法字节保持相同。新增 `tests/fixtures/sci_heat_pump_start_surplus.json` 不在原生Python源码枚举中，必须作为第7个单独绑定的复现文件携带；它不改变28个物理输入。实际小型元数据闭合验证及损坏拒绝检查通过，但尚未从最终压缩包解压或运行新物理案例，不构成独立复现通过。最终交付仍须新压缩包、成员核验、新解压目录及跨版本累计预算下的一次真实完整重放。
