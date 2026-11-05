Current numerical acceleration policy: [ENGINEERING_NUMERICS.md](ENGINEERING_NUMERICS.md). Use the registered engineering_fast_v1 CASE; target-server and annual admission remain pending.

# busy年服务器执行顺序

本包当前profile为 `annual_responsive_dynamic_bridge_20261104.v1`。原8760小时负荷在原边界保持，15分钟调度，物理子段最长1分钟，连续设备/库存/保护/队列状态跨月保留。全部科学门仅在目标服务器实际执行；本机不得运行科学默认测试或IES轨迹。

1. 读取AGENTS、限制及当前 `config/SOURCE_PROVENANCE.json`，完整核验包及两个独立Git仓库、真实历史fixtures。检查help可了解接口，不启动年度。

```console
python -B runtime_checks.py verify
python -B runtime_checks.py --help
python -B freeze_calendar.py --help
python -B launch_server.py --help
```

2. 等待本次同源同profile正常 `M_ready` 全年及其真实来源/独立核算，指定该原始producer目录 `NORMAL_M_READY_OUTPUT`。另外两个正常方法不是依赖。使用包外新目录 `FROZEN_ROOT` 固定统一日历；该目录不能覆盖旧冻结或与参考结果重叠。

```console
python -B freeze_calendar.py --reference-output "NORMAL_M_READY_OUTPUT" --output "FROZEN_ROOT"
```

核对E5为TOWER_1四等份代理；E8为EC1_PRIMARY_PUMP 60分钟，9月15日14:00至其后6天同钟、按正常参考前60分钟EC1实际正产冷取首个合法日期；未找到则保留失败。E6a/E6b原common-tower部分双脉冲扩展保留。冻结前不能根据扰动后的损失筛选日期；冻结不是实际执行证据。

3. 指定包外实际E盘 `WORK_ROOT`，开始至少600GB可用。现场采集Windows/EPYC9654/256GB、有效CPU/NUMA/处理器组、可用内存/文件系统事实；不照搬打包机亲和。保持用户对独立许可/environment探测的skip政策，不重新启动被skip探测，不将skip转成通过。

```console
python -B runtime_checks.py preflight --frozen-root "FROZEN_ROOT" --work-root "WORK_ROOT"
python -B scripts/verify_launcher.py --work-root "WORK_ROOT"
python -B runtime_checks.py validate --frozen-root "FROZEN_ROOT" --work-root "WORK_ROOT" --limits-file "WORK_ROOT/limits.json"
```

validate须实际执行当前 `source/` 完整 `python -m pytest -q`、同源同profile真实短正常/扰动、完整参考与非干净中断恢复比较、当前工程实际owner账本独立核算、资源和归属树关闭。323个历史节点在真实7fa上下文执行并前后核验；其通过不能借给当前profile。真实执行遇依赖/许可/求解失败保留原始日志并停止相关分支，不降规模或改阈值；skip的独立探测仍保持skip。

当次门必须绑定当前host、package、source、inputs、日历、cases、tools和路径。旧verification/玩具收据不能充当新门。busy进程计数采用limits.json配置，不再限定一条总并发；生产与独立核账可以重叠，每条solver/BLAS一线程。候选守卫为RSS10GB、可用RAM16GB、运行空闲盘30GB、开始600GB、有限墙钟最多30天、全批一次恢复；当前实现约束以脚本为准，进程计数可显式配置，物理/恢复校验和实时资源守卫不得删除。

4. 核验当前准入，显式独立启动年度。

```console
python -B runtime_checks.py check-admission --frozen-root "FROZEN_ROOT" --work-root "WORK_ROOT"
python -B launch_server.py --frozen-root "FROZEN_ROOT" --work-root "WORK_ROOT"
```

正式长程默认同用户WMI独立监督，先取得当前主机父Job关闭后心跳/停止/实际Job空集证据。WMI拒绝不自动切换方法或提权；启动返回0不代表年度完成。`run_years.py` 与 `server_runner.py run` 为显式前台接口，不直接依赖聊天父Job承担长程任务。

```console
python -B server_runner.py status --work-root "WORK_ROOT"
python -B server_runner.py stop --work-root "WORK_ROOT" --reason "controlled operator stop"
```

写stop后等待归属树关闭收据，不结束第三方任务。年度全批最多一次合法已提交vault恢复，恢复失败也计数；新attempt保留旧attempt，不从初态自动重跑。第二次事故、核心门失败或预算/资源不足则受控停止，保留完成前缀、事故位置及未知尾段。

5. 三方法各自真实完成8760小时且unknown tail为0，当前profile实际owner账本独立核算、来源与进程闭合均通过后才能称完整busy年。交付中文报告、完整日历与前窗选择证据、命令/退出码/原始日志、主机/环境与SHA、所有attempt/恢复链、分P0–P3的服务/温度/保护/缺供/恢复和成本结果。计划、历史、实际执行分别标记；失败、未知、未执行分列。结论仅为本模型/输入/扰动范围，不构成盲测、真实医院年可靠率、OEM/并网许可或现价全成本证明。


2026-11-05并发更新：先将包内 `limits.example.json` 复制到包外 `WORK_ROOT/limits.json`，按RAM、磁盘吞吐和实际许可调整进程计数。参阅 `CONCURRENCY.md`。不设置自动启动年度；旧工具绑定的准入收据不得复用。
