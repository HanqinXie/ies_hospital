# C02 只读审计器参数化复核

复核对象为旧 `comparison_audit/audit_campaign.py`，SHA256：`8c9102eef5d93aab8c1c3263fbf17b59e66a61410dc9052124cbcff843668f4c`。本文件只给最小改动与风险清单；未写审计实现或测试，未修改旧脚本、D 盘源码或注册表，未构造环境/求解器或仿真。

## 最小参数化面

新增显式参数 `--normal-prefix n02_ --disturbance-prefix d02_ --freeze-file FROZEN_SOURCE_V30_C02.json`，冻结文件路径相对 `--root` 解析；记录实际解析路径与文件 SHA，不从前缀猜文件名。normal/disturbed 两个阶段引用同一个 C02 freeze，其 producer、source_files、source_sha256、input_files、input_sha256 都是同一组真实绑定。

旧脚本需替换的位置集中在 main，不应改能量、费用、库存、服务积分或比较算术：

|旧位置/逻辑|必要变化|
|---|---|
|345–349：循环读 N01/D01，取二者其一作 base|加载一个显式 freeze，两个 phase 均引用它；不存在即显式失败|
|351–352：固定 n01_/d01_ 与 dirname[:3].upper()|用 phase→完整 prefix 映射识别，禁止三字符 tag 推导|
|355/368：attempted key 与按 tag 取 freeze|用被验证的 expected case/phase 槽位与单一 freeze|
|373–378：normal→N01、disturbed→D01 以及 not_started 路径|逐32个期望 case 生成配置前缀下的确切路径|
|416–420：b07 的 D01 注册目标|从同一 C02 freeze 的 disturbed case 取目标；输出名称说明实际 freeze，而非伪称 D01|
|438–439：报告硬拼 FROZEN_SOURCE_V30_TAG.json|记录实际 `--freeze-file` 路径/SHA及 phase→prefix，不捏造 N02/D02 manifest|

旧 `base` 冻结选择结果只用于存在性门槛；参数化后应直接校验同一 freeze 的32条完整期望索引，而非保留“优先 N01，否则 D01”的隐式回退。dev case 即使存在于 registry 也不计入八背景×四组合的32个期望槽位。

## 路径、阶段、case 与角色防误配

1. 两个前缀必须非空、不同，忽略大小写后也不同；任一前缀不得是另一个前缀的前缀，否则一次 startswith 可能命中两个阶段。拒绝路径分隔符、`..` 与绝对路径形式，避免前缀变成路径跳转。
2. 选择候选目录后，要求目录名精确等于 `prefix + case_id`；不能仅凭 startswith 接受意外的 retry 后缀或不同 case。若支持多次尝试，应另行显式定义 attempt 身份，不以字典序最后一个悄悄替代。
3. CASE 的 case_id 必须在冻结32个索引中，context、v30_mode、policy.alias 与预期槽位一致，prefix 识别出的阶段必须等于 case.v30_mode。未知阶段、外来 case、同一 case 重复或 CASE 缺失要列为明确审计异常，不能被吞掉或成为看似正常的“未开始”。
4. 本轮目录只允许 n02_/d02_ 满足新32个槽位。n01_/d01_、dev01、R_off 和其他角色应保留为显式 excluded/out_of_scope 记录，记录原 path、真实 producer/status 和排除原因；不能把旧记录本身标为 not_started，也不能用它填补新槽位。若仅旧结果存在，则对应新 n02_/d02_ 槽位确实仍是 not_started，二者须分开表达。
5. 不将“旧冻结正常轨迹”“修复后新扰动”“旧 R_off 参考”“开发机制例”拼成一个比较。选入的全部32条必须与同一真实 C02 producer/source/input 对齐；法定角色仍为新真实 matched_normal / finite_primary_disturbance，不能重命名旧结果制造新运行。

## Fix1 精确绑定

每条已选实际结果均应增加独立布尔检查：

```text
RESULT.resolved_model_config.v30_physical_patch.patch_id
  == hospital_ies.powered_start_minimum_off_readiness.v30.fix1
```

缺字段、null、不同 ID 都失败，不能只检查字段存在或包含 fix1 字样。旧 `frozen_configuration_checks` 仅检查硬件 profile 键的子集，不足以证明额外物理补丁已启用，所以此项不能借硬件匹配代替。

继续保留旧 producer/source aggregate 与 input map 核验。为满足“单一真实源”，建议同时直接核对 `BINDINGS.source == freeze.source_files`，而不只相信 RESULT 声明的 source_sha256；并核对 RESULT 的 producer/input/source 声明与实际冻结绑定。任何绑定不一致应使 gate 失败，不能仅在报告中记录后照常混入胜负比较。

## 必须保持的旧数值与计时口径

- 不改 `fee_audit`、`integrate_service`、`store_audit`、`change`、`close` 的计算、原精度和单位。
- 不改 `TIMING_KEYS={elapsed_seconds, TimeLimit, remaining_original_budget_seconds}` 与 `SOLVER_CONTAINERS={solver_calls, solver_backend_layers}`。仅 solver 容器中的这三个精确叶名归一；物理 rotor.elapsed_seconds、实际时钟、状态、命令和队列不得剔除。
- 继续分别报告 initial state/core 与 RNG；RNG 差异保留，不能据物理相同声称完整 snapshot 相同。
- 保持 `SEGMENT_FIELDS`、`DECISION_FIELDS` 与 `<60 min` 合法前缀范围；不得通过删真实差异来让正常/扰动或 C/M 的前缀相等。
- b07 仍只读 C 正常 [45,60) 的实际 action/端点 GT；旧函数对并列或区间赢家变化拒绝静默选取的行为保持。将锁定目标与同一 C02 freeze 的两条 disturbed case 核对。

## 门槛与保留风险

旧 `status=complete_audit` 只表示索引没有缺失/仍运行项，不等于全部仿真成功。旧 main 的退出码也主要由审计异常、检查失败和 context integrity 控制；保留失败前缀的审计可能计算正确而 producer 仍 failed。因此最终 C02 gate 还应明确要求恰好32条期望实际轨迹完成、producer_failed=0、未知尾段为0、无 solver/export 错误、无审计失败，不能只读进程退出码0或字符串 complete_audit。

旧选择器优先 `complete[-1]`，但 complete 基于 producer status 和尾段，而非 audit_failures。严格绑定失败的条目必须让总门槛失败；不要把这样的 complete 条目解释为已通过的比较证据。未知阶段/未知 case 应及早显式拒绝，避免后续 audit_run 使用 v30_context/v30_mode 索引时仅变成泛型读取异常。

旧脚本会复制 producer_error/error；某些完整 diagnostic 体积很大。若做紧凑错误摘要，只能保留真实原文件路径与 SHA、类型/消息等，不得删除失败证据或把详细未知改成零；这属于产物处理，不能改变本轮选入轨迹或数值。

建议新脚本保留 `inherited_auditor_sha256`，另记新 script_sha256、accounting receipt SHA、preregistration SHA，以及三个新参数。这样旧 8c9 审计与新 C02 参数化审计可追溯并并存。
