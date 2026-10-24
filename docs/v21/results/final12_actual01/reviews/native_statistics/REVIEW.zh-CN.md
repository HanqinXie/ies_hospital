# final12原生240：独立工程/统计审查

**NOT_GATE / NOT_NEW_INDEPENDENT_POOL。** 这是一轮已合法完成的final240原生表与小RESULT摘要审查，没有新物理、LP、bootstrap、生产analysis/cohort、MATLAB、pytest、派发、注册或Git调用，没有改D/策略/参数/既有成果。未打开任何raw/solver-sidecar，也未读取另一路物理审计的输出。是否科学接受由root另签；本报告不代签。

主要发现：A/F/M相对C的关键material时间/次数没有改善；A出现一个可识别的**一般电服务—费用/排放取舍**，所以不能复制主池/Q1的全部A/C服务平局，也不能称严格Pareto改善。R在同一来源块的L1/L2/L4各有一例关键union增加30 min。下列数据均来自本轮真实原生final240，不与primary19或Q1_3合并。

## 原始绑定、分母和统计单位

主入口`audit_native_tables01.py`真实执行0.5975664998404682 s、exit0；全部29,952条PAIRED_DIFFERENCES逐项与canonical CASE的candidate/reference/delta exact核对，5,616条PAIRED_SUMMARY的分母、原比较零值、source blocks、正负计数及原区间协议核对，**0 issues**。已有2000次、seed20261023的source-block bootstrap上下界只读取、未重算。纯标量block均值与native均值最大舍入差2.2737367544323206e−13，原值保留，没有扩大容差或改CI。

读前已知SHA全部exact：

| 原始文件（E:/hospital_ies_v21_20261023/下） | SHA256 |
|---|---|
| final_blind01/analysis01/PARALLEL_ANALYSIS_RECEIPT.json | `994a42eb6b9382e5a2645c470e2af0655a6d06227b2a0d22aa9045fc4c92dc59` |
| final_blind01/analysis01/TABLES.json.gz | `f70141272d4f62a20bd82d51991d36b33aa86cdeee201e3c05a02c7f877facd9` |
| final_blind01/analysis01/tables/CASE_METRICS.csv | `3cab466b53b0080f666dce46f003a92fe3ad3c478e353033bc634d604011ac93` |
| final_blind01/analysis_guard01/CASE_IDENTITY_GUARD.json | `c740a8a199ed74bb8077fc01b51b088fb4e0f53d6d04db167014b9ec3dbec950` |
| final_blind01/JOBS_ANALYSIS_ONLY.json | `ea4a3c81687334074669535b92d5b24e3c8f95b7a91812b98703e903f501bf46` |

实际240 CASE、48场景、12源块，每策略48；每L为12场景、同场景五策略/H0/720 min。12块的quarter分布为4/3/2/3。全部CASE为completed、attempt_0001、attempts_observed=1、eligible=true、evidence_complete=true、unknown_tail_minutes=0；原ATTEMPT_STATUS也为240 completed。2880 carrier cells、240 RESOURCE与240 ATTEMPT不构成额外独立样本。carrier/CASE逐字段相同，原请求平衡及ENS分项残差均另保留在JSON，未重做物理审计。

独立统计单位为source_block：先块内场景均值再对块等权；all层每块4类场景相关，不把48场景、240轨迹、分钟或求解次数当作独立n。各L区间使用12块；季度仅4/3/2/3块，尤其n=2的区间只能按此有限设计解释。这些是一个医院原型/天气年的固定实验，非事故自然概率、临床显著性、跨医院可靠率或先验必须获胜的证据。

## A/C：原176服务、65 material字段完整核对

字段范围严格沿`reviews/main02_primary19_stat_independent01/audit_primary19.py:92,109`与Q1相同规则：从已冻结native analysis的METRICS排除费用、CO2、两项solver timing得到176；其中含material_minutes/interruption_count或critical_union_minutes得到65。仅对原常量赋值做AST枚举，没有导入/执行analysis。native source SHA为`8b2aaee6de03705d03b3f0b2e8c2ff70ddcf46cce05de77559913c33ab5b1ae2`。全部名单及原scope脚本SHA在`actual01/FIELD_SCOPE176_65.csv`。

本轮176中：155字段在全部48场景均为数值tie（1e−8），147字段逐值exact；29字段存在合计36个非零cell，分布在4场景，全部原差值保留。65 material中61字段全48 exact；另4字段全来自下述同一P3电分钟/最长变化。`A_MINUS_C_NONZERO_SERVICE_DELTAS.csv`保留低至10⁻¹⁸的原差，不清零或自动归因求解误差。

**关键tie的精确范围**：P0/P1逐载能material分钟、最长、次数、重复次数，P0/P1各自union上述指标，加global critical union共33字段，在全部48场景逐值exact；三个critical initial-handoff/post-handoff/recovery gap字段也全exact。P0/P1热、冷共4个全窗ENS字段全48 exact。

但关键电ENS不能称相同：下述L1场景的P0_e、P1_e ENS各增加`4.063206741358955e−7 kWh_e`，在原1e−8数值比较中各是1例worse/47 tie，虽material分钟/次数未变。P2_e也同增。P0/P1相应electric delivered略减；其他极微小交付/分阶段尾差亦保留。全176表中的`numeric_improved/worsened`只是原差值符号命名：**requested/delivered不具有一律越小越好的含义**，不得将交付减少称服务改善；真正native policy loss比较的COMPARE_METRICS本来排除requested/delivered。

### 唯一一般服务取舍案例

`scenario_id=m21_L1_55e24889a6e1b237d15a`，`source_block_id=m21_final12_b046c742da517542`，L1/Q1，GRID availability0，原fault duration360 min、response delay15 min、window720 min。该例是事后描述索引，不改变原8组raw优先级、不增加独立n，也未据小摘要推断A释放分支的因果机制。完整原值及RESULT path/SHA见`actual01/LOCAL_A_C_TRADEOFF_CASE.json`（实际提取0.18761010002344847 s、exit0）。

| 指标 | C | A | A−C |
|---|---:|---:|---:|
| P3电ENS/kWh_e | 75.59000202874498 | 71.52683591945393 | −4.063166109291046 |
| P3电material分钟 | 135.25 | 75.25 | −60 |
| P3电最长material分钟 | 120 | 60 | −60 |
| P3电中断/重复次数 | 3 / 2 | 3 / 2 | 0 / 0 |
| P3热ENS/kWh_h | 6.5914525027498625 | 同C | 0 |
| P3冷ENS/kWh_c | 21.444253115704356 | 同C | 0 |
| P3热、冷material/最长分钟 | 各15 / 15 | 同C | 0 |
| P3热、冷中断/重复次数 | 各1 / 0 | 同C | 0 |
| P3 union分钟/最长 | 135.25 / 120 | 75.25 / 60 | −60 / −60 |
| P3 union中断/重复次数 | 3 / 2 | 3 / 2 | 0 / 0 |
| 恢复阶段P3电ENS/kWh_e | 0.3911963438108626 | 同C | 0 |
| 恢复阶段P3热、冷ENS | 0 / 0 | 同C | 0 |
| critical union/recovery gap分钟 | 0 / 0 | 0 / 0 | 0 / 0 |
| P0电ENS/kWh_e | 0.0004246855428130813 | 0.0004250918634872172 | +4.063206741358955e−7 |
| P1电ENS/kWh_e | 0.0004246855428130813 | 0.0004250918634872172 | +4.063206741358955e−7 |
| 同窗费用/历史2001 USD | 505.5349974791259 | 506.7539383203635 | +1.218940841237611 |
| 条件CO2/kg | 3405.9256081041112 | 3408.7604574522893 | +2.834849348178068 |

这表示P3**聚合电缺口的material计时**少60 min，而不是整个一般区额外连续供能60 min；真正少供电量仅少4.063166 kWh，热冷未变，次数未变，恢复窗原字段相同。不能称恢复窗收益或严格Pareto改善。P3断开ENS减少4.063206741358496 kWh，但已连接不足ENS增加4.0632067438650665e−5 kWh，也保留分项取舍。

该例使L1的P3 union块均差−5 min、原native95%区间[−15,0]；all设计层−1.25 min、[−3.75,0]。all费用差+0.025394600859116895 USD、[0,0.07618380257735069]；all条件CO2差+0.05905936142035747 kg、[−5.684341886080802e−14,0.17717808426112924]。原CO2区间的微负尾数未改为0。

## 四类、四方案相对C：关键服务及一般服务/代价

所有原“改善/相同/恶化”计数均逐场景按native 1e−8损失差分类，物理material指标直接沿原生定义，不由ENS再分类。各L的33关键material及恢复窗口字段、12个逐载能ENS、P2/P3 material/次数/最长、费用/CO2与原native CI完整列于`actual01/NATIVE_SELECTED_PAIRED_SUMMARY.csv`；原值见`ABSOLUTE_CLASS_POLICY_SELECTED.csv`，每个非零case差见`SELECTED_NONZERO_CASE_PAIRS.csv`。这些可审表包含全部四类各12配对场景，不是仅选有利例。

| candidate−C | L1关键union 改善/同/恶化 | L2 | L3 | L4 |
|---|---|---|---|---|
| A | 0/12/0 | 0/12/0 | 0/12/0 | 0/12/0 |
| F | 0/12/0 | 0/12/0 | 0/12/0 | 0/12/0 |
| M | 0/12/0 | 0/12/0 | 0/12/0 | 0/12/0 |
| R | 0/11/1 | 0/11/1 | 0/12/0 | 0/11/1 |

R的三例均多30 min；L1/L2/L4各类块均+2.5 min，原95%区间各[0,7.5]。同一个Q1块`m21_final12_6f9356e6d3f682aa`、HRSG_1槽：`m21_L1_cb9edd74abe64735f0f7`、`m21_L2_811dd5c19ef6aeeb2c2a`、`m21_L4_c0f7d5eacbe5c61fe336`。C关键union0，R30，P3 union也各增30；critical_recovery_gap差均0。P0/P1 union各增加一次中断，最长差30 min。不能仅凭“HRSG_1/L2”标签推实际故障机制或保护跳闸。

A/F/M虽然关键material相同，**ENS并非全部相同**。除A上述电微差，F有3例P0/P1 electric ENS增加，单例最大各约0.0001555913/0.0001558676 kWh_e；M有P0/P1冷微差（all块均约+6.4007747e−6/+6.1609718e−6 kWh_c）及电正负差。R既有关键电微小下降也有恶化、冷ENS增加；不能把跨载能差相加或用微小电改善抵销真正material恶化。所有原小量、class分层、区间和最差scene都在原值数据内，不把它们伪造为0或临床实质收益。

以下是all固定设计层，仍按12源块等权，不是事故期望。括号为改善/同/恶化；区间为原native95% source-block bootstrap：

| 方案 | P2 union分钟差 | P3 union分钟差 | 费用差/2001 USD | 条件CO2差/kg |
|---|---|---|---|---|
| A | 0（0/48/0） | −1.25 [−3.75,0]（1/47/0） | +0.0253946 [0,0.0761838]（0/47/1） | +0.0590594 [−5.68e−14,0.177178]（0/47/1） |
| R | +8.257823 [2.007823,15]（2/28/18） | +28.944850 [6.692708,58.716787]（3/16/29） | −16.043555 [−25.719712,−0.663213]（43/0/5） | −407.198938 [−456.955896,−353.256783]（48/0/0） |
| F | −0.492177 [−1.476532,0]（2/46/0） | −2.489584 [−7.468751,0]（1/45/2） | +22.554700 [0,67.664099]（0/45/3） | +22.587720 [0,67.763161]（0/45/3） |
| M | +7.007823 [0.757823,13.75]（2/30/16） | +9.375 [−1.354167,18.854167]（6/17/25） | −55.319219 [−79.626716,−28.016682]（33/0/15） | +1187.263108 [1064.480422,1276.218519]（0/0/48） |

F的P3均值下降由一例改善120 min主导，另两例各约0.25 min恶化，不能用均值隐藏恶化。M费用下降伴一般服务更多material缺口及CO2增加；R费用/CO2下降不能抵销三例关键material恶化。各L费用/CO2正负、次数、最长、恢复、逐载能ENS全部在上列完整CSV中，未据CI是否跨0使用“临床显著性”或赢家标签。

## 实际保护事件、后端拒绝与在线时长

独立小RESULT子审`child_resources/audit_resources.py`真实0.431883 s、exit0。240份小RESULT逐CASE原path/SHA核验；CASE/RESOURCE CSV与canonical全单元格exact，2400项RESULT/RESOURCE scalar交叉0unknown、0mismatch，247个已读源前后0漂移。字段路径与原Q1口径在子审脚本及JSON明确保留：完整有效sparse `metrics.event_counts`中缺kind才按0解释；strategy/projection/timeout等字段缺失应保留unknown，本批实际均有已知数值。

- `metrics.event_counts['protection_trip']`实际8事件/8 case：同一块`m21_final12_b046c742da517542`中的L2 `m21_L2_84c0f09da5c6174eda81`与L4 `m21_L4_0763da9749409cf4f77f`，各R/F/C/A各1；M为0。不是从L2标签计算，也不是所有L2都会跳闸。小摘要事件计数不提供单台GT的实际跳闸时间/完整过程，不能据此编造设备时序。
- 显式strategy fallback、projection fallback、projection_changed、timeout均为已知0；这不掩盖内部backend rejection。实际670次reject/14 case：A221次/5 case，C221/5，F228/4，R/M0。最终completed不能把这些内部拒绝改写为未发生。
- 每case记录的full-step最大值范围0.2828827–7.3498989 s，upper最大值范围0.0727184–2.6123775 s；每case48 upper decisions，900 s周期，upper exceedance=0。upper/solver/full-step口径重叠，不能相加。只是该主机实际计时，不是医院硬实时许可；没有完整逐step时长分布时也不伪造它。

完整逐policy/class事件、拒绝和计时记录在`child_resources/actual01/`；正事件case表为`PROTECTION_TRIP_POSITIVE_CASES.csv`。R三例额外关键缺口属于另一来源块，不能借8次保护事件替它们编因果解释。

## 输出、日志与边界

- `actual01/NATIVE_STATISTICAL_REVIEW.json`：0issues、完整分母/176/65、canonical/table digest/全部pair与summary计数、平衡残差和限制。
- `actual01/FIELD_SCOPE176_65.csv`：逐字段名单、原native/source与旧scope脚本SHA；`A_MINUS_C_SERVICE176.csv`及`A_MINUS_C_NONZERO_SERVICE_DELTAS.csv`保留全部176与36个非零cell。
- `NATIVE_SELECTED_PAIRED_SUMMARY.csv`保留所有所选字段的all/四L原native CI/分母/正负计数；`SCENARIO_COMPARISON_INDEX192.csv`保留4方案×48scene的原CASE RESULT path/SHA、关键/恢复/一般服务及费用原值差；两个selected/absolute表提供逐载能追溯。
- `actual01/SOURCE_CLOSURE.json`有主审22源读前后exact；子审主JSON有其247源闭合，数量有重叠不可相加当独立文件数。MAT只核原export SHA，不声称另做MAT语义审计。没有重读原sidecar/物理约束行、重求LP或重算bootstrap端点。
- 本目录与child的`commands_and_exit_codes.txt`、`logs/*.command.json/stdout/stderr`保留真实命令、UTC、退出码；脚本均只在本新E目录，根输入与原成果只读。SHA索引及整体回执另封，所有原量不改。

固定比较、物理material与数值EPS含义不混用。P0/P1聚合能量服务不等于每房间临床合规，GT应力/温度、source-side utilities、真实油耗等既有模型边界不因本审查解除。root随后若作配对raw事后解释，应另立合法只读证据，不将本事后案例当新先验代表、改原8组优先、增加独立n或反向调参。本审查在现有结果上结束，不延长为额外图、轨迹或更多新计算。
