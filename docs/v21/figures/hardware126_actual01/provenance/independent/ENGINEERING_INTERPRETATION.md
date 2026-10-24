# HW126：逐载能硬件效果与证据范围

这次全池记录支持“有限硬件在特定已见故障中改变了服务缺口”，也完整保留了无收益、微量数值变差、一般服务代价和费用/条件排放增加。不能据此写成普遍消除危险、所有载能等价保障或新独立临床验证。本说明是新原生表与小 RESULT 的描述，不签署 root 科学门槛。

## 池、版本和执行事实

完整设计为 14 scene × C/A/M × H0/H1/H2 = 126 CASE，42 campaign，每 CASE 单次实际 attempt；C42/A42/M42。仅使用原 `hardware03/JOBS.json`（SHA `2f4ae1fecca9db43ae5413f8a4dbd2f7344cb05e84735c247b398cfec40c9944`），没有用续行99子池冒充完整126。原STOP终态27完成/99未启动保持原状；新增99在原42 campaign路径排空后的实际联合终态由 root 独立对账。本次没有重跑物理。

原 parallel wrapper、原 analysis.py、workers=2、8GiB/85%内存gate及原100/2000次bootstrap编排未改。实际 native 命令 2026-10-24 09:55:31.615819→09:59:15.087066 UTC，223.4720686秒，exit 0；native内部223.3368191秒。完整表含126 ATTEMPT、126 CASE、1512逐级逐载能 CARRIER。原生 parent SHA `14bb55011f3e47aa86e45de58ab013808dbdb6854457271c4fee1401a39c6084`。未加 `--serial-check`，本次 serial equality 字段为 NULL，不能写成本次重跑串行同表验证。

原 CASE identity guard 实际0.0477091秒、exit0，126 CASE的phase/model/source/scene/policy/config/window及重复身份验证通过，receipt SHA `c5fb7e31acdafce9f13f2766108e30cd99a3f4ca97af4c2edd17c5bc535475c6`。该guard本身不评价科学数值。新126 CASE均eligible=True。

原生 `HARDWARE_DIFFERENCES` 8736条长表只包含 C/A 的56比较组（每scene/policy的H1−H0、H2−H0）；其不包含M是原函数接口边界，不能解释成M没有运行或M硬件零效果。原PAIRED_DIFFERENCES的M−C属于同配置策略比较，也不能改名替代M硬件比较。补充表明确提供CAM84组，M28组全部保留。

补充02 parent/result/freeze/source/input当前字节绑定使用已审 `cohort_io.load_parent/recheck` 与原 `result_extension/input_identity/hardware_pairs`。全84组满足同phase、scene、sourceblock、model、source SHA、scenario SHA、runtime-input SHA、window、policy ID和完整policy SHA，且12项原始请求完整已知。84组请求累计值都非bit-exact，最大绝对差 `1.8417267710901797e-11 kWh`，在原已批准 `1e-8 kWh` 的累计积分尾差join容差内；保留原值和exact=False，不改物理/material判据。runtime输入28个的binding为 `659ecc488bda87c9338afde283cbb74135ca2ebdf2769ce463d0e61f0c6288d8`，source643为 `4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add`。

## H1：关键电的收益不能被union零变化遮盖

H1为预登记有限UPS：40kWh、160kW、初始SOC1、充放电效率均0.95，未增容量。其实际放电为0–38kWh，实际充入0–42.105263kWh，转换损失0–4.105263kWh；这是同窗口真实账本，不能把名义40kWh都视作有用负荷供能。

两个冬/夏 `GRID+ALL_GT` compound scene中，C/A/M的P0电和P1电material缺口各减少15分钟，各少一次中断；其余12个scene的这两项material时长不变。C/A的P0热/P1热在两个compound中也各减少15分钟，M的热material时长不变。P0/P1冷material时长在H1全部不变。逐载能结果不可跨e/h/c相加。

与此同时，H1所有policy/scene的关键P0 OR P1联合material分钟、最长时段、中断次数和重复次数均保持不变。联合指标为零差并不能否定上述逐载能收益；它只能说明联合故障区间没有缩短。本小表不自行指定实际DG/UPS交接时刻或将联合不变归因于某条未查原始区间的因果路径。

## H2：四季TOWER冷缺口变浅、变短，但中断次数未下降

H2为预登记有限冷库存：60kWh、120kW、初始SOC1、充放效率均0.95、每小时损失0.01、泵电比例0.02。全H2实际放冷0–56.778636kWh，补充充入0.149918–65.492916kWh，转换损失0.039196–6.262995kWh，静置损失0.818901–3.599700kWh。不能根据容量单独推断可用送冷路径或无限续航。

H2在每个policy的4个四季TOWER scene缩短P0冷/P1冷及关键联合material缺口，其余10个scene的关键联合material分钟不变。四个TOWER的逐scene结果保存在 `TOWER_CRITICAL_COLD.csv`，不把它们按14个独立背景计数。

| Policy | 4个TOWER的关键冷/联合material缩短范围(min) | P0冷ENS减少范围(kWh冷) | P1冷ENS减少范围(kWh冷) |
|---|---:|---:|---:|
| C/A | 43.828049–49.893279 | 32.969997–34.779068 | 21.971475–23.808640 |
| M | 43.504109–49.522927 | 32.726183–34.520757 | 21.808272–23.632540 |

这4组关键联合最长material时段缩短量等于对应联合material总分钟的缩短量；全84组的关键联合中断次数/重复次数均无变化。因此结论是既有缺口部分缩短，不能写成完全消除中断。M的四季TOWER也全部有实质冷ENS/material收益，不能漏掉M42或用原CA56长表代替。

## 微量数值变化与一般服务代价

原helper逐项按原 `1e-8` 数值epsilon标记时，84组中24组存在至少一个关键载能ENS增加，25组存在至少一个一般载能ENS增加；关键material变差0组，一般material变差2组。关键ENS正差最大为 `0.00012690290665773318 kWh`（约0.1269Wh），位于M/H2夏季AUX_CHW_PUMP的P0冷。这些真实微量差不能清零、改符号、隐藏或自动称为数值误差；它们也不能直接等同于material缺口变差或房间级临床损害。

冬季compound `m21_hardware_compound_5478464ed043b4fef98f` 的M策略出现需要保留的代价：

| 对H0比较 | P3电ENS差(kWh电) | P3电material差(min) | P3电中断/重复次数差 | 费用差(2001 USD) |
|---|---:|---:|---:|---:|
| H1 | +0.796180 | +0.25 | +1 / +1 | −0.936468 |
| H2 | +0.789144 | +0.25 | +1 / +1 | −0.523569 |

84组中6组同时费用下降与至少一项数值服务ENS变差；其中上述2组同时费用下降与一般material缺口变差。这是并列关联，不把费用下降归因为少供电，也不把其解释为“以经济性换掉临床安全”的临床因果证据。

## 费用、条件CO2、阶段和断开语义

| Policy/配置对H0 | 同窗费用差范围(2001 USD) | 同窗条件CO2差范围(kg) |
|---|---:|---:|
| C/A，H1 | +0.429875 至 +3.099747 | +4.815402 至 +8.183074 |
| M，H1 | −0.936468 至 +3.551242 | +1.517140 至 +8.430627 |
| C/A，H2 | +0.992411 至 +2.340374 | +2.919521 至 +6.195064 |
| M，H2 | −0.523569 至 +1.210327 | −7.992521 至 +3.185062 |

C/A在H1/H2的14个scene费用和条件CO2均增加。M/H1费用1组下降、13组增加，条件CO2全部增加；M/H2费用5组下降9组增加，条件CO2也各5/9组下降/增加。没有根据这些结果优化容量、阈值、选择代表样本或重跑策略。

费用来自原runner `energy_cost_USD`：原区间账本 + 原DG轴功修正 + 初始库存按首时电价购入的条件费用；实际补能已在区间费用内，不再重复计费。单位为原2001年USD，不能冒称当前价格、硬件CAPEX/O&M或完整生命周期成本。条件CO2按原排放因子账本，并计入条件初始库存电网购电；它不是实测排放或生命周期碳足迹。

`HW_EXTRA_ENGINEERING_DESCRIPTIONS.csv`保留候选/参照原值，并只在strictpaired组逐项计算额外描述差：48项`phase_<preparation|initial_handoff|endurance|recovery>_<P0..P3>_<e|h|c>_ENS_kWh`、24项断开ENS/已连接未满足ENS、原gap、条件CO2、资源/实际能源/库存账本。H1 compound关键电收益主要出现在登记initial_handoff阶段；H2 TOWER冷ENS收益分布于initial_handoff/endurance。登记阶段边界不是实际DG启动、母线闭合、UPS/冷库存耗尽或再并网时刻，实际时序须用另行预声明的代表原raw过程导出佐证。

断开ENS与已连接未满足ENS保留分列。主要关键电/冷收益出现在已连接未满足项；P3电断开量可有微量变化，不能用总ENS抹去断开原因，也不能把载能ENS相互抵销。精确全项范围和正/负/epsilon内计数见1320条 `NUMERIC_DIFFERENCE_SUMMARY.csv`；这些是同池描述，非新的统计假设检验或CI。

## 求解器、回退和在线成本

已知126个小RESULT原值均显示策略fallback步0、projection fallback步0、projection changed步0。这些是原RESULT累计计数，不是本适配器在缺列时补零。后端尝试386096次、solver调用56115次；582个后端拒绝分布在12个compound的C/A CASE，最大单CASE59次，全部保留。M在这些计数中没有后端拒绝不构成算法优越性证明；接受层尝试、回退步、最终trajectory完成属于不同口径。solver timeout计数0，upper period exceedance计数0。

| 每CASE记录范围 | 秒 |
|---|---:|
| full-step总计 | 13.832621–111.903144 |
| full-step最大单步 | 0.997378–7.095044 |
| solver总计 | 7.403100–71.074039 |
| solver最大单次 | 0.286709–2.538461 |
| upper总计 | 6.080678–53.846021 |
| upper最大单次 | 0.318326–2.566700 |

原调度周期900秒、每CASE24个upper decision。进程与主机条件影响测时，实际执行包含旧27和续行99两段host profile；这些量不能当作硬实时保证、执行器实际响应时长或并网许可。`RESOURCE_CASE_SUMMARY.csv`保留126行原资源量及小RESULT计数。

## 推断、实现与未执行边界

14个scene仍只来自4个已见季节sourceblock，分布4/3/4/3；compound仅冬/夏2块。配置、policy、scene行和分钟均不是新增独立n。原硬件长表没有硬件bootstrap CI，本补充也不重bootstrap，不拿策略CI当硬件CI，不读封存final。上述极值、行数和收益比例都是该登记池的描述，不外推到独立医院、未见背景或临床可用性。

新适配只写E目录，D物理核心、策略、source643、输入28、容量、阈值、material判据均未改，没有新MPC/未来信息/执行快通道，没有新增物理attempt。物理参数/OEM/拓扑未新增假设；新增的只有诚实的数据描述范围：小RESULT回退计数的原值拷贝，和原CASE在strictpaired组上的额外工程差值。

小适配先有RED5fail→GREEN25pass；发现原CASE遗漏已有RESULT回退计数后，新增RED1fail5pass→GREEN26pass。旧断言未删、容差未扩。旧adapter v1精确保存在`reviews/hardware126_engineering01/history/engineering_supplement_v1.py`，SHA `a5abd7d0c4ab429f86ced4538a049ed387771e1b3b42a4410115b00b1aa2802c`；旧补充01仍保留，补充02是本说明authority。最终实际补充02运行3.2368984秒、exit0，SHA `67f433c4ae46dee1debbdf6ca038f489bd2032fcc01b1ed95de261564ac5e981`。描述投影实际0.1165982秒、exit0。

本任务执行的是原生evidence分析、CASE身份guard和小RESULT/CASE描述。没有在补充脚本中重复整池raw/sidecar current-byte或物理审计，没有运行HW18代表原raw导出、MATLAB、扩大样本/训练、封存final或root科学门槛；这些由root分别调度和绑定。测试26pass验证本次小适配及原helper回归，不冒称完整D项目pytest新运行。所有真实命令、UTC、退出码、stdout/stderr保存在`hardware126_review_execution01/logs`与`reviews/hardware126_engineering01/logs`。
