Python 三生产并发入口：`python -B start_three_parallel.py`，也可直接用 Python 打开该文件。用法与日志说明见 [ONE_CLICK_ZH.md](ONE_CLICK_ZH.md)。

Current numerical acceleration policy: [ENGINEERING_NUMERICS.md](ENGINEERING_NUMERICS.md). Use the registered engineering_fast_v1 CASE; target-server and annual admission remain pending.

# H2 busy年服务器交接包

本包在Windows / EPYC9654 / 256GB上运行当前冻结 `annual_responsive_dynamic_bridge_20261104.v1` 的三方法连续8760小时busy年。当前服务器完整默认科学测试、真实短门、断点恢复、独立核算、主机准入和年度均待执行；打包机只运行纯交接工具检查。

先读 [START_HERE.md](START_HERE.md)、[AGENTS.md](AGENTS.md)、[CODEX_TASK.md](CODEX_TASK.md) 和 [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md)。完整复制 `source/`、真实独立 `legacy_context_7fa/`、它们的隐藏 `.git` 及真实 `legacy_fixtures/`，活动身份以 `config/SOURCE_PROVENANCE.json` 与包清单为准。历史路径只作来源记录。

先由本次同源同profile正常 `M_ready` 全年结果冻结日历；另外两个正常方法不是依赖。保留原busy需求窗口/既定扰动，E5改为单元 `TOWER_1`（四等份代理）；新增E8为EC1支路主泵60分钟，按正常参考前窗正产冷在9月15日14:00及其后最多6天同钟选择首个合法日期。E6a/E6b保留common-tower部分双脉冲重复恢复扩展，不能当作本地T08的相同复制。

原小时负荷保持原小时边界，15分钟调度，物理子段最长1分钟，连续状态跨月保留。不制造全年分钟负荷、不拆月重置。busy采用可配置并发，默认三生产、一核账、总四，不再强制串行；每条solver/BLAS单线程；两包分别使用各自limits.json，整机资源需合并考虑。

保留用户对busy独立许可/environment探测的skip政策，skip不能冒充通过；真实科学门遇缺依赖或许可问题保留失败、暂停依赖分支，不购许可、不绕过、不降模型。

只读包核验入口：

```console
python -B runtime_checks.py verify
python -B runtime_checks.py --help
python -B freeze_calendar.py --help
```

这些命令不运行年度。正式日历冻结、服务器验证与启动见 [CODEX_TASK.md](CODEX_TASK.md)。
