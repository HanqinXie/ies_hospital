Python 三生产并发入口：`python -B start_three_parallel.py`，也可直接用 Python 打开该文件。用法与日志说明见 [ONE_CLICK_ZH.md](ONE_CLICK_ZH.md)。

Current numerical acceleration policy: [ENGINEERING_NUMERICS.md](ENGINEERING_NUMERICS.md). Use the registered engineering_fast_v1 CASE; target-server and annual admission remain pending.

# H2 正常年服务器交接包

本包使用当前冻结源码的 `annual_responsive_dynamic_bridge_20261104.v1`，在 Windows / AMD EPYC 9654 / 256 GB RAM 上运行 `M_available`、`M_ready`、`C_ready` 各一条连续8760小时正常年。当前服务器科学默认测试、真实短门、断点恢复、独立核算、主机准入及年度均为待执行；打包机只运行交接工具纯测试。

先读 [START_HERE.md](START_HERE.md)、[AGENTS.md](AGENTS.md)、[CODEX_TASK.md](CODEX_TASK.md) 和 [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)。完整复制本目录，包括两个独立Git仓库的隐藏 `.git`；工作根须在包外实际E盘。来源身份以 `evidence/SOURCE_FREEZE.json`、`evidence/PAYLOAD_PROVENANCE.json`、`cases/INDEX.json` 及 `MANIFEST.json` 为准，不使用历史固定提交或文件数。

原8760小时负荷保持原小时边界；上层调度15分钟；物理子段最长1分钟。连续设备状态、库存、普通命令队列和延迟跨月保留，不按月重置，不虚构全年分钟负荷。容量、P0–P3服务判据、六动作和信息边界保持，不引入MPC、未来故障脚本或执行快通道。

`source/` 为当前生产与完整默认测试源码；`legacy_context_7fa/` 是真实历史提交的独立只读上下文，仅承载323个保留原nodeid/测试字节的历史身份节点；`legacy_fixtures/` 保留两个真实旧producer与epoch09源文件。历史上下文通过不能给当前profile授予物理资格。新结果写包外；`evidence/refresh_20261104/` 记录本次打包核验，旧收据只作历史材料。

服务器先产生当次证据，再准入。正常年进程数量由显式limits.json配置；示例三生产、一核账、总四，不设固定数量上限，每条solver/BLAS一线程；实际吞吐须在目标机测量。busy日历只依赖本次同源同profile正常 `M_ready` 全年结果，其余两个正常方法无需等待busy日历。

只读检查：

```console
python -B verify_source_copy.py
python -B build_payload.py
python -B build_legacy_fixtures.py
python -B transfer_checks.py verify
```

这些命令不运行科学轨迹或授予年度准入。完整服务器命令见 [CODEX_TASK.md](CODEX_TASK.md)。
