Current numerical acceleration policy: [ENGINEERING_NUMERICS.md](ENGINEERING_NUMERICS.md). Use the registered engineering_fast_v1 CASE; target-server and annual admission remain pending.

# 正常年服务器执行顺序

当前profile为 `annual_responsive_dynamic_bridge_20261104.v1`；所有生产、短门、恢复、核算使用同一当前冻结身份。原8760小时负荷原样保持，15分钟调度、物理子段最长1分钟、年度连续状态跨月保留。以下 `WORK_ROOT` 均替换为包外实际E盘的新目录。脚本导入、verify、preflight和help不运行年度；科学命令仅在目标服务器执行。

1. 读本包AGENTS、限制、当前 `evidence/SOURCE_FREEZE.json` / `PAYLOAD_PROVENANCE.json` / `cases/INDEX.json`。核验完整清单及两个独立Git仓库，保留旧材料原样。

```console
python -B verify_source_copy.py
python -B build_payload.py
python -B build_legacy_fixtures.py
python -B transfer_checks.py verify
python -B transfer_checks.py --help
```

2. 采集现场Windows/EPYC9654/256GB、CPU/NUMA/处理器组/内存/盘与依赖事实，建立用户权限独立环境。无实际E盘、资源不足或权限缺失则报告并暂停依赖分支。不照搬打包机CPU亲和。先做当前主机WMI独立监督的纯玩具验证。

```console
python -B scripts/verify_launcher.py --work-root "WORK_ROOT"
python -B transfer_checks.py preflight --work-root "WORK_ROOT"
python -B scripts/server_validate.py --help
```

3. 实际运行完整默认科学入口、正式规模许可及两个独立短进程。suite必须在当前 `source/` 执行完整 `python -m pytest -q`，不能以挑选子集替代。323个历史节点由原收集入口路由至真实独立7fa上下文，前后检查HEAD/工作树/所有tracked-file SHA；其通过不资格化当前profile。

```console
python -B scripts/server_validate.py suite --work-root "WORK_ROOT" --timeout-seconds 43200
python -B scripts/server_validate.py license --work-root "WORK_ROOT" --workers 3 --timeout-seconds 1800
python -B scripts/server_validate.py short --work-root "WORK_ROOT" --timeout-seconds 43200
```

这些有限超时是门的候选上限，须按实际规模事先登记；超时保留失败，不改成通过。真实短门必须包含当前profile正常/故障、完整参考、强制中断后合法恢复、严格比较、实际owner账本独立核算、资源采样与归属树关闭。当前工程账本不能借历史native-primal账本的通过身份。没有对应真实原始账本或未闭合则门失败。

4. 依据本机当次短门设置 `WORK_ROOT/limits.json`。将包内 `limits.example.json` 复制为 `WORK_ROOT/limits.json`，示例 `max_producers=3,max_accounts=1,max_total_processes=4`，这三个计数可配置，不再设固定上限；`threads_per_producer=1,max_recoveries_global=1`继续保留；`max_wall_seconds` 为正且不超过2592000（30天）。候选RSS上限10000000000字节、可用内存底线16000000000、运行空闲盘底线30000000000、初始可用盘600000000000；`grace_seconds`、`restore_timeout_seconds`、`poll_seconds` 取已测有限值。全部字段以 `transfer_checks.py --help` 和当前验证器约束为准，不直接复制旧限额或手写通过收据。

```console
python -B transfer_checks.py admit --work-root "WORK_ROOT" --limits-file "WORK_ROOT/limits.json" --valid-hours 168
python -B launch_server.py --work-root "WORK_ROOT"
```

仅真实门齐全且主机/源码/案例/路径/工具/预算绑定有效时准入。正式长程使用同用户WMI独立监督；失败不自动切方法、提权或后台依附聊天父Job。启动返回0不代表年度完成。

5. 监督、停止、恢复与三条年度核算。

```console
python -B server_runner.py status --work-root "WORK_ROOT"
python -B server_runner.py stop --work-root "WORK_ROOT" --reason "controlled operator stop"
```

每条时间连续，只有不同case可并行。跨月不能重置温度、库存、燃料、延迟命令或保护状态。停止文件不等于已停止，必须取得所拥有Job空集及树关闭收据；不结束第三方任务。全批只允许一次合法快照恢复，新attempt保留旧attempt，禁止从初态自动重跑。核心门失败、第二次事故或预算/资源不足则受控停止并保留已完成前缀和未知尾段。

6. 逐条核对8760小时、unknown tail为0、当前profile独立核算及来源和进程闭合后，才报告完整正常年。输出中文报告、全部命令/退出码/原始日志、主机/环境、source/input/case/tool SHA、每次attempt与恢复链、实际执行和求解异常、逐时数据、年度结果与限制。失败、未知、未执行分列，历史证据分开。busy日历只等待其中同源同profile `M_ready` 全年，其他两个正常方法无需等待busy；不把旧35天拼成全年。


2026-11-05并发更新：先将包内 `limits.example.json` 复制到包外 `WORK_ROOT/limits.json`，按RAM、磁盘吞吐和实际许可调整进程计数。参阅 `CONCURRENCY.md`。不设置自动启动年度；旧工具绑定的准入收据不得复用。
