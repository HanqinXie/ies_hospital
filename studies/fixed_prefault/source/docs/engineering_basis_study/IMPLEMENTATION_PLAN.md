# 医院能源站工程依据与运行边界 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Independent source and arithmetic tasks may run in parallel; root alone edits registration, environment adapters and any physical state-transition implementation.

**Goal:** 按用户已授权三项目标，以可信接口语义、同等锅炉可调范围对照及可复核边界，完成研究能源站的有条件运行准则。

**Architecture:** 复用原V30物理执行、共同准备、队列与观测；新增命名engineering_basis_study入口，不改原入口和参数。独立蒸汽计量与边界函数分别自成模块，来源与实际运行在E盘新目录保存。

**Tech Stack:** 现有Python/pytest/Gurobi环境；新独立核算只用标准库；沿用已验证Windows Job进程归属和有界派发机制。无新付费依赖。

## 执行授权与责任

用户已明确“如果可以直接运行”和“暂时没有，按研究能源站继续”；不因技能的通用方案确认或旧goal停止点再次申请内部确认。所有范围按DESIGN.md；不新增设备、改原服务阈值或打开final12。root串行维护tools/engineering_basis_study/{registration,environment,validation,campaign}.py及对应测试；独立蒸汽实现者仅steam_meter.py与其测试；独立边界实现者仅bounds.py与其测试。E盘各分析目录分别所有，禁止同时改同一函数。

## A 来源与工程语义

- [x] 当前linked worktree、源HEAD9400029、新分支和D/E空间核验；新E目录及初始873/36 SHA核验真实退出0。
- [x] 供汽实际源调用、旧三个native、原报告页码交叉核验，记录已到可调上限/非强制产汽/非现场授权。
- [x] 官方资料分级核验：塔ton两解释、冷机启动流程、泵公式与待命费用；未知不可伪标定。
- [x] 来源表已分级保存：23项证据、7份官方PDF/2份HTML及本地原稿副本，33项SHA实际核验；所有未取得保留。型号、真实曲线及费用仍未知，不作实院标定。

## B 新注册及公平工程入口（root）

Files: create tools/engineering_basis_study/__init__.py, registration.py, environment.py, validation.py, campaign.py; tests/test_engineering_basis_registration.py and test_engineering_basis_environment.py. 不修改原operating_rules_study文件。

- [ ] 先写回归：C_available必须保留C完整配置且只加当前观测的同等锅炉上限；M_available同上；未就绪不得直接产汽，未来事件变化不得改变当前规则；拒绝隐式physics override。
- [ ] 先写场景反例：仅改demand_amplitude不能通过实际zone增量一致性；fault duration改变必须更新真实fault duration和恢复锚点，但shock duration固定60；N/F/D/FD互斥字段、全scenario哈希和protected参数必须完整校验；拒绝final12/越源年/越1440min。
- [ ] 运行并保存红灯：python -B E:/hospital_ies_engineering_basis_20261029/run_step.py registration_red01 python -B -m pytest -q tests/test_engineering_basis_registration.py tests/test_engineering_basis_environment.py。
- [ ] 最小实现：新case元数据engineering_basis_study v1；完整scenario与policy摘要决定ID；所有新字段校验后才建新输出。映射主方法为M/C与available钩子；诊断冷准备沿原M冷准备配置，不改执行层。
- [ ] 1/5/15min锅炉重启仅允许新登记named assumption，绑定复制registry的station唯一键；其他字段仍原值。结果完整记录实际resolved_model_config，原registry只读。
- [ ] 同命令运行绿色新名日志，逐项检视，不放宽原容差、不删除断言。

## C 原求解变量的独立蒸汽核算（专属实现者）

Files: create tools/engineering_basis_study/steam_meter.py; tests/test_engineering_basis_steam_meter.py; E/analysis/steam_meter。

- [ ] 先构造伪造/歧义/缺失/失败解反例：缺raw_x、错误源SHA、时间错配、仅候选、动作或服务不符均不能变为已计量弃汽。唯一实际采用解或完整等价且弃汽一致的证据才可给数值。
- [ ] 运行steam_meter_red01，确认故障性质；实现只读bind与逐段source+boiler−ABS−heat−metered_waste核算，不用余项填端口。
- [ ] 运行steam_meter_green01；用旧已完成普通及复杂储能/转供轨迹只读抽核，报告覆盖与未绑定段，source/action/服务/sidecar关联不可缺。
- [ ] 新native ledger保留原正证据要求，并把弃汽已恢复/未知独立列，不以unknown隐藏可观察失败。

## D 量纲与物理包络（专属实现者）

Files: create tools/engineering_basis_study/bounds.py; tests/test_engineering_basis_bounds.py; E/analysis/operating_bounds。

- [ ] 红灯覆盖：瞬时功率不足即使kWh足够仍失败；库存每前缀而非仅总量；有充电盈余时不错误使用正缺口和作一般必要界；电热冷单位不可混加；未知泵与暖机不给虚假保证。
- [ ] 红灯覆盖塔两解释及all-in拆分包络：原G6系数1/入塔0.77界7.256597；系数1.25界0。零界仅撤销无解证明，不标可行。
- [ ] 实现有明确输入单位的纯函数：冷源共塔乐观上界、库存可达前缀、保证不了新冷机即时备用的启动桥接需求、目的地/OPEN/后恢复三种路径资格。充分保证仅在明确供给下界与真实库存递推全满足时给出。
- [ ] 运行bounds_green01，用独立算式复核原G6及反例。任何必要界所放宽资源逐项列出。

## E 执行器与冻结（root）

- [ ] E盘复制并最小适配旧controlled_parallel_driver.py与owned_process.py；只允许新validation模块，max3归属子树/独占锁/绝对24h监督/20GB余量/内存门槛/每次源和case哈希核验。冻结后任何源变动必须停止新派发。
- [ ] campaign注册仅接新合法case，完整核验CASE/RESULT/BINDINGS/raw/ledger/完整终点；续跑不得依靠摘要或空账本，失败不静默删。
- [ ] 先导12个默认FD槽；最多3个相同源的独立执行重复验证并行科学值，所有重复计实际预算。保存原计时诊断差异，不能以容差抹去物理差异。
- [ ] 根据真实先导测时、字节和资源结果登记主预算（<=512实际starts/24h）。先导是已登记主槽子集，来源不变时显式引用，不重新运行。

## F 主矩阵与边界验证

- [ ] 事前生成并冻结DESIGN的288主槽、12相位、48个完整配对锅炉重启诊断及24个来源核验驱动的塔时长槽，排除完整支持中的sealed day。每个case记录实际区域增量向量、fault与shock各自持续、共同观察终点及原恢复锚点。
- [ ] 运行主矩阵；每个独立结果接纳后再继续。核心或正证据失败停止依赖分支；只读分析继续；有限纠错必须保留失败及新attempt身份。
- [ ] 指令与真正供能、P0/P1/P2/P3逐载能缺供及原material、完整费用、库存谷/末与恢复尾段逐项汇总，不将分钟或载能混加。
- [ ] 对同方法改变未来事件的事故前段核实际action/服务/共享状态/生成命令，缺非共享端点保留unknown，明确差异不放宽容差。
- [ ] 在24个事前资源时长槽之外，最多24个预登记局部邻点：只对已有相邻网格服务结果不同且完整的条件，按固定排序选中点；报告非单调与所有并列，不称全局阈值。
- [ ] 固定轨迹燃耗重计只称prospective surrogate替代核算，使用既有离散模型40–100%假定计算域，越域保留unknown；该域不称原稿验证有效域，也不称800/缩放320kW设备实测燃耗。就绪但无蒸汽的时长与未定待命/启停系数分列，不编造现价年节费。

## G 完成验证与对话

- [ ] 对新代码做规格/工程复核；运行新增显式测试和实际最终python -B -m pytest -q。只有新变更/失败才重跑相应已过检查。
- [ ] 全局started/finished/实际episode计数独立核对，所有进程归属结束；原输入及旧源变更清单、旧结果只读/当前源身份核对。若旧源有任何必要变动须明确diff与迁移，不能冒称SHA全不变。
- [ ] 直接中文工程汇报三问题的已解决部分、定量准则、反例、费用资格和仍需资料；不制作审查包、压缩包或发布。只有范围外决定或真正材料/权限阻碍剩余工作时讨论。

## 新入口开发中的语义迁移

registration_red01首先暴露新测试未加src路径而无法collection；按既有测试显式加入root/src后，red02实际35项失败，均为新入口尚未实现。green01实际14失败/21通过：根因是旧L4b输入schema强制enable_propagation=true，该标签不是执行层已关闭的research_stress_proxy。新测试最初把标签false误当实际损坏机制关闭，现以“FD必须满足旧标签且实际proxy严格false、室温耦合None、新meta明确endogenous_damage_enabled=false”替代，保留拒绝标签/实际资格篡改的覆盖，不改任何原schema或物理核心、不放宽容差。

## 首批科学代码冻结前实际检查

新增代码只在tools/engineering_basis_study及五个同名前缀测试，原物理/正常工况源码不改。integrated_new_tests01实际144通过；integrated_regressions03同时运行新增144项与原7个operating_rules文件的74项，实际218通过、退出0。integrated_regressions02因手写了不存在的旧test_operating_rules_environment.py而退出4，完整保留，随后按rg实际文件名重跑，不冒充计算失败或删除证据。

新增steam meter已49项测试通过；复杂H1的154/222段可绑定，68段仍unknown，整轨弃汽积分None。新bounds最终39项通过，充分证书额外绑定store_config并重放完整数值库存，拒绝伪pass、改写流量及空观察。case身份改用规范JSON摘要以拒绝bool/0/1类型替换；verify入口直接核计划文件、当前源身份和case字节。每项反例和红绿日志均在新E steps，不改原容差。

新E执行器55项合成回归退出0；owned_process字节同旧。serial_pilot为1个受归属进程、后续为3个；source baseplan的physical_workers=1与执行revision声明1/3分别记录，不拿上限当实际峰值。执行器冻结前仍须真实先导/3对科学重复，合成测试不是医院结果。
