# S1 局部执行适配实施记录

日期：2026-11-02（Asia/Shanghai）。独立工作区 `D:/hospital_ies_eight_fault_20261102`，分支 `research-eight-fault-20261102`，继承基线 `60602c5795887a74092b115619d96a32d19fb0b5`。当前授权为同目录 `NEXT_MILESTONE_CODEX.md`，合同 SHA256 `b97a68632c51264cd20c3e4191a1f6736955c7f0be775b6219ad95e6c7e443aa`；该副本与原合同逐字节一致。

## 改动清单与边界

- 新增 `tools/eight_fault_study/__init__.py`、`runtime.py`、`runner.py`，实现 detached-calendar 因果等待、具名 source-only context 和复用原完整前缀执行链的单局部案例入口。
- `tools/annual_program/witness.py` 只新增 source-freeze resolver 并替换原取 freeze 的一行；所有原 compiled/source/code/object/rows 校验保留。
- `tools/annual_program/flow_source_contracts.py` 更新当前 recorder literal 并保留 A60602、FROZEN16、FROZEN12、legacy 精确 pair；没有动态审批接口。
- `tools/annual_readiness/runner.py` 只增加私有 optional runtime_builder 参数并在原构造点使用；公共接口、完整历史、失败链和默认构造不变。
- 新增 `tests/test_eight_fault_local_runtime.py`、`test_eight_fault_local_witness.py`、`test_eight_fault_local_runner.py`；最小迁移 `test_annual_exact_cache_source_releases.py` 的旧 recorder 共享假设，详见 `MIGRATION.md`。
- 新增本记录与 `MIGRATION.md`；原合同仅复制、未修改。原工作区、历史输出、原始输入、所有 `src/hospital_ies` 文件均未修改。

原 `tools/annual_program/source_runtime.py` SHA256 仍为 `992d80719dd0728b047b30e847ac6ab86225035a4712c4658efc90fd831e61c2`。新 recorder SHA256 为 `3f774bc3a0871b00703c948a680ec4295146f487d8f496d925b82ec2ac36c14c`。

## 实际命令、退出码和原始证据

所有命令从独立工作区运行，使用 `python -B E:/hospital_ies_eight_fault_20261102_run01/run_step.py NAME python -B -m pytest ...`；helper 为每个唯一 NAME 保存完整 `COMMAND.json`、`stdout.log`、`stderr.log`，TEMP/TMP 均限定到 E 盘该次目录，`PYTHONDONTWRITEBYTECODE=1`。未覆盖或删除失败日志。

原始目录统一为 `E:/hospital_ies_eight_fault_20261102_run01/steps/`。每个目录的 `COMMAND.json` 是实际完整命令、退出码、环境和墙钟记录。

| NAME | exit | 实际结果及解释 |
|---|---:|---|
| local_adapter_red01 | 2 | 新测试初版 src 导入路径收集错误，原日志保留 |
| local_adapter_red02 | 1 | 3 failed/17 errors；fixture 使用非规范 EC 名称，原日志保留 |
| local_adapter_red03 | 1 | 20 failed；明确缺少局部 adapter 的有效 RED |
| local_adapter_green01 | 0 | 20 passed |
| local_witness_runner_red01 | 2 | 误导入不存在的 synthetic helper，原日志保留 |
| local_witness_runner_red02 | 1 | 10 failed/2 passed；local compiled freeze/release/builder 的 RED |
| local_witness_runner_green01 | 1 | 1 failed/11 passed；root drift 原 native 先给 unknown，而测试误要求抛异常。改成明确 unknown 且无 recorded/rows 的覆盖 |
| local_runner_preflight_red01 | 1 | 1 failed/3 passed；capture-origin preflight 失败未进入原 RESULT 链的 RED |
| local_targeted_green02 | 1 | 2 failed/255 passed；旧测试误把新 recorder 绑定 FROZEN16，且漏保留 A60602+S17。最小迁移理由见 MIGRATION |
| local_config_drift_red01 | 1 | 1 failed/25 deselected；实际 env.config 漂移的 RED |
| local_sci_config_drift_red01 | 1 | 1 failed/25 deselected；实际 sci_config 漂移的 RED |
| local_config_drift_green01 | 1 | 3 failed/5 passed/18 deselected；synthetic reset 缺少 native 初始库存赋值，补齐 fixture，未放松实现 |
| local_config_drift_green02 | 0 | 8 passed/18 deselected |
| local_targeted_green03 | 0 | 263 passed |
| local_native_rule_gate_red01 | 1 | 1 failed/25 deselected；原严格 rule parser 拒绝带 rule_id 的输入 |
| local_native_rule_gate_green01 | 0 | 5 passed/21 deselected；保持构造输入字段与实际规范 rule 的分工 |
| local_capture_reset_red01 | 1 | 单独运行新 sentinel 暴露测试 src 导入路径缺失，原日志保留 |
| local_capture_reset_red02 | 1 | 1 failed/4 deselected；synthetic warmup-hook 捕获到过早 scope 的有效 RED |
| local_capture_reset_green01 | 0 | 5 passed；scope 仅包原 step，reset/snapshot 不捕获 |
| local_builder_interface01 | 0 | 2 passed/5 deselected；M_available 与 M_ready 严格构造门和原 all-row 日志参数 |
| local_targeted_final01 | 0 | **335 passed in 85.20s**；17 个相关 synthetic/只读回归文件 |
| local_diff_check01 | 0 | tracked diff 无空白问题 |
| local_staged_diff_check01 | 2 | staged check 发现三个新增测试文件 EOF 多余空行；只删除末尾空行，未改测试语义，失败日志保留 |
| local_staged_diff_check02 | 0 | 全部 staged 文件 diff --check 通过 |

最终实际 pytest 参数如下（外层 helper 与原始日志如上）：

```text
python -B -m pytest -q tests/test_eight_fault_local_runtime.py tests/test_eight_fault_local_witness.py tests/test_eight_fault_local_runner.py tests/test_annual_program_witness.py tests/test_annual_compiled_witness.py tests/test_annual_loaded_capture_sources.py tests/test_annual_flow_source_contracts.py tests/test_annual_exact_cache_source_releases.py tests/test_annual_exact_cache_source_profile.py tests/test_annual_program_source_runtime.py tests/test_annual_program_source_pin_cache.py tests/test_operating_rules_clock.py tests/test_annual_readiness_runner.py tests/test_annual_runner_capture.py tests/test_annual_observer_source_compatibility.py tests/test_annual_program_runner.py tests/test_annual_engineering_runner_mode.py
```

## 通过、失败、未执行与限制

最终相关 17 文件：335 passed、0 failed、0 skipped。回归覆盖 detached-calendar onset/释放/未来因果/no-event、native source/error MRO、完整合法/segment 历史、partial failure 原始日志、原 exact actual hook、无 solve/observe、candidate/源漂移/I/O 不产生 recorded、双 context/final grant/sealed/support 在构造前拒绝、实际 env/config/reset 漂移以及各 literal release pair。

首批四条 `M_ready/H2`、summer hour 4137 和 winter hour 33、健康/TOWER/GT_3 的 canonical 元数据配置已覆盖通过；没有执行这些案例。没有添加物理参数、设备能力、故障标准、服务容差或新的数值读取权限，假设登记无物理假设变更。

本实现任务未执行默认全套、未构造任何真实医院环境、未启动任何正式物理轨迹/年度/35 天/优化训练、未进行数值生产。基线默认测试由主任务实际运行；本记录不把其数字当作本实现结果。后续默认全套、独立规范/质量审查及登记短例由主任务继续。相关测试通过不代表八专题已经科学验收。

`git diff --check` 和限定文件提交随本阶段完成；最终源码身份以本次提交及主任务新 manifest 为准，不能覆盖启动基线 991/36 清单。

## d05c5ad 后独立规范审查的窄修复

审查对象为 `d05c5adbb81bd2fac500df3b9d75956437fcf07f`，原报告 `E:/hospital_ies_eight_fault_20261102_run01/s1/LOCAL_SPEC_REVIEW01.json` 的 LOCAL-SPEC-01 指出实际物理 solve 接收 `operating_execution_contract`，而此前静态快照仅包含其精简副本 `execution_contract`。本轮在同一冻结映射中增加实际字典，先复现构造后/reset 内/reset 后/witness 漂移的四个 RED，再做仅一个字段的实现。正常 reset 已允许的初始库存赋值和 readiness 元数据语义不变。

主任务及规范审查还确认首例前需要原资源 harness 可使用的窄 stopfile 通道。新局部 `run_case` / `_execute_attempt` 增加可选 `stopfile`，CLI 增加 `--stopfile`；解析绝对路径后记录 INVOCATION，只在原 native attempt builder try 内的环境构造前、及薄 step wrapper 的 capture/原 step 之前做存在检查。具名 `ControlledStopRequested` 和 `controlled_stop_requested` category 交给原失败链，不改原执行循环，不删除 marker、不读取内容、不提供给策略、不在初始化或 solve 内加入中断逻辑。初始化/单步运行期间即时停止的局限及外部 grace/Job 兜底见新 README。

本轮修改文件：`tools/eight_fault_study/runtime.py`、`runner.py`、`tests/test_eight_fault_local_runtime.py`、`test_eight_fault_local_witness.py`；新增 `test_eight_fault_local_stop.py`、`docs/eight_fault_20261102/README.md`，并更新本记录与 MIGRATION。未修改旧 native loop、witness/source_runtime、literal release 或物理实现。原 recorder/hook SHA 保持上文值。

| NAME | exit | 本轮实际结果 |
|---|---:|---|
| local_operating_contract_red01 | 1 | 4 failed/37 deselected；实际 operating contract 漂移未被拒绝的有效 RED |
| local_operating_contract_green01 | 0 | 5 passed/36 deselected；三种漂移时机、witness 和正常 reset |
| local_stop_red01 | 1 | 7 failed；stopfile 私有/公共参数与 CLI 尚不存在的 RED |
| local_stop_green01 | 0 | 7 passed；预存在不构造、初始化后 first-step 前、一步后保留前缀、无 stop/不存在 marker、CLI/INVOCATION 绑定 |
| local_review_fix_targeted01 | 0 | **346 passed in 89.32s**；本轮最终 18 文件相关 synthetic/只读回归 |

完整最终命令与环境/退出码见 `steps/local_review_fix_targeted01/COMMAND.json`，原始 stdout/stderr 同目录。其 pytest 参数为上一轮 17 文件完整命令另加 `tests/test_eight_fault_local_stop.py`。没有只挑 stop 或漂移用例冒充完整相关回归，未覆盖或删除任何 RED 记录。

本轮最终相关测试 346 passed、0 failed、0 skipped；未运行默认全套，未构造真实环境或启动任何物理轨迹，未改变假设、物理参数、数值授权或预算。后续仍须由独立审查按新提交重新核对，测试通过不赋予生产或八专题验收资格。最终 staged diff 检查与提交的命令、退出码由 `local_review_fix_staged_check01`、`local_review_fix_commit01` 的原始 helper receipts 记录。

## 533fcef 后默认测试发现清单修复

主任务发现 `pytest.ini` 使用显式 testpaths，尚未包含本轮四份 `test_eight_fault_local_*.py`，此前默认执行的历史日志因此不构成本轮新回归的默认入口覆盖。本次只给 pytest.ini 追加 runtime、witness、runner、stop 四条现有文件路径，并补本实施记录；未改变 runtime/core/source、测试文件或断言。

先实际执行 `python -B -m pytest --collect-only -q` 保存原默认收集输出。E 盘独立审计脚本 `s1/check_default_discovery.py` 从实际 stdout 解析 nodeids，同时核对收集命令 exit0，并断言四个文件均至少收集一项；修改前失败、修改后使用完全相同脚本通过。

| NAME | exit | 实际收集/断言结果 |
|---|---:|---|
| local_default_collect_red01 | 0 | 2536 tests collected；四份局部回归各 0 项 |
| local_default_discovery_assert_red01 | 1 | 明确缺少全部四文件的有效 RED；不是收集异常 |
| local_default_collect_green01 | 0 | 2591 tests collected；仅新增现有局部 55 项 |
| local_default_discovery_assert_green01 | 0 | runtime 29 / witness 12 / runner 7 / stop 7，missing=[] |

原始 `COMMAND.json`、stdout/stderr 均保存在同名 steps 目录，没有覆盖旧结果。本次只执行 collection 和其断言，没有执行测试 body、默认全套或重新执行 346 专项；没有构造真实环境或启动科学轨迹。完整默认执行由主任务在新提交上另外实际运行，不能把 2591 collected 表述为 passed。

此次 discovery 扩展须经两份独立审查确认除 pytest.ini 与本记录外，所有运行实现/源/测试字节均与 `533fcef9e811485aeecfbe200ccd4edd62bdd6b4` 相同，方可延伸原审查结论。源码新身份及 pytest.ini 新 SHA 由主任务重绑，不沿用旧 source manifest。

## 14627e1 首正式 healthy 失败的派生上下文修复

失败 producer `eight01_summer_H2_ready_healthy` 绑定 `14627e1bb35210293142b5663c02fa867b907198`，RESULT 为 failed，实际前缀 0..0.25 min，未知尾段 179.75 min，source/input 未变。原错误为 `_check_env` 报 Actual local execution parameters differ from bound runtime；20 条原生调用及 solver rows、一个无 actual-witness receipt 的失败 segment 均保留在原输出。无 standalone FAILURE_CONTEXT 文件，因为带错误的实际前缀已经进入 `evidence/blocks/block_000000.json.gz`。不把该失败当通过，不改写其 producer/状态/数值；主任务记账的一次正式尝试及 8.61s 所属进程墙钟继续保留，本实现不启动 retry。

唯一只读诊断为 `E:/hospital_ies_eight_fault_20261102_run01/s1/FIRST_NATIVE_CONFIG_DRIFT_DIAGNOSIS01.json`，SHA256 `2b6fbc57b89697d77311a57db590e411d9042c62f2425f5623e8cfcabf7032e5`。原始 bound/live env.config 整字典没有被持久化，诊断明确不能声称从文件直接重建全字典 diff；命名字段及变动路径由真实规则和确定的 M25/M22 源码建立，并由下面 source-faithful synthetic 回归复现。

已定位 `_model_correction_v25_reserve`：原 M25 `solver_context(core,dt_h)` 读取当时库存/目标/损失/效率，`SustainedReserveMixin._physical_solver_config` 仅在 sustained 规则加入该字段；原 M22 `generate_command` 的 env.config.update 将命令时长上下文缓存。实际 solve 会按实际子段 dt/库存重新生成，不可在 post-commit hook 用已变化状态重算要求与旧命令缓存相同。

经主任务及独立审查确认，生产修复只在已验证 `sustained_critical` 时，从 env.config 静态投影分离这一个具名派生字段。非 sustained 注入、同名静态字典、unknown underscore key、原容量/效率/rule 和实际 operating_execution_contract 继续拒绝漂移；不泛排私有字段，不修改原 M25/M22/solver/阈值/数据权限。原实际 packet/source/code/object/rows 认证保留；不声称 compiled DTO 直接包含 V25 reserve 对象。

source-faithful 测试使用真实 `SustainedReserveMixin._physical_solver_config`、`solver_context`、M22 `generate_command`，仅将上下游数值求解/观察边缘换成禁止调用的纯 synthetic 桩，未运行 native 构造器或物理解。两条源码路径回归与一个完整 local freeze 正例先失败，七项静态/范围负例保留。

| NAME | exit | 实际结果 |
|---|---:|---|
| local_reserve_context_red01 | 1 | 3 failed / 7 passed / 29 deselected；真实配置链的合法派生缓存误报漂移 |
| local_reserve_context_green01 | 0 | 10 passed / 29 deselected；库存/子段时长差异、完整 sustained freeze 与七负例全部通过 |
| local_reserve_context_targeted01 | 0 | **356 passed in 103.95s**；完整 18 文件相关 synthetic/只读回归，0 failed/0 skipped |

本次源码修改限定为局部 runtime 的具名 conditional 分离、同一既有 runtime 测试文件，以及 MIGRATION/本记录；原失败目录、诊断和红灯日志不覆盖。未启动真实环境、solver、物理轨迹、重试或默认全套；默认验证和唯一技术 retry 由主任务在独立复核后另行执行。

最终完整命令、实际环境/退出码见 `steps/local_reserve_context_targeted01/COMMAND.json`；参数为上一轮 18 文件原命令，新增十项均位于既有 runtime 文件。staged diff 检查与提交的原始 receipts 另见 `local_reserve_context_diffcheck01`、`local_reserve_context_commit01`，不沿用旧测试数字。
