Current numerical acceleration policy: [ENGINEERING_NUMERICS.md](ENGINEERING_NUMERICS.md). Use the registered engineering_fast_v1 CASE; target-server and annual admission remain pending.

# busy年服务器执行边界

先读 `START_HERE.md`、`CODEX_TASK.md`、`KNOWN_LIMITATIONS.md` 和当前 `config/SOURCE_PROVENANCE.json`。本包顶层授权限定本次服务器交接；`source/AGENTS.md` 的历史阶段停止点不能扩大或阻止已授权工作。

## 本轮授权

在Windows / EPYC9654 / 256GB服务器按真实依赖完成包与主机核验、当前源码完整默认测试、同源同profile短门、停止/断点恢复、独立实际执行账本核算、资源与归属监督准入，并运行 `M_available`、`M_ready`、`C_ready` 各一条连续8760小时busy年；内部节点通过后自主继续，不重复聊天确认。当前这些服务器科学门和年度均为待执行；本机只准纯交接工具检查，不跑科学测试/轨迹。

保留用户对busy包既有独立许可/environment探测的skip政策，不主动重新运行被skip的探测、不扩账号/许可/付费权限，skip记录必须保持skip，不能冒充通过。实际科学执行本身若遇依赖、许可或模型规模错误，原样保留并暂停其依赖分支，不降规模、不绕过许可、不伪造准入。现场只读主机事实、明确授权的真实科学门和归属监督验证继续按本包执行。

## 不变边界

- 当前生产身份唯一来自 `config/SOURCE_PROVENANCE.json`、包清单及冻结日历。生产profile为 `annual_responsive_dynamic_bridge_20261104.v1`；源码/输入逐文件验SHA，不套用旧提交或固定文件数。
- 原8760小时负荷在原小时边界保持；15分钟上层调度；物理子段最长1分钟。不虚构全年分钟输入、不按月重置状态，不拼接独立月份为全年。
- 保持DOE/IDF、原设备来源/容量/保护/失效标准、P0–P3逐步服务锁、六动作、普通队列/延迟、合法观测。不得使用MPC、未来故障脚本、执行快通道、新策略、ML训练或额外硬件来获得优势。
- 原始输入、旧输出、审计快照及原最终12背景继续保护；现有DATA_APPROVAL的全8760小时非盲测研究授权保留，不能把已使用源小时称为未暴露。系统/账号/凭据/付费权限不扩大。
- 323个历史身份节点保留原nodeid和13个原模块字节，在真实独立只读 `legacy_context_7fa/` 内执行；前后校验真实7fa HEAD、干净树及全部tracked-file SHA。真实旧producer/epoch09 fixtures原样保留，仅已钉住插件迁移两个依赖路径。历史通过不能资格化当前profile。

## 日历依赖与允许扰动

仅先等待本次同源同profile正常 `M_ready` 的完整8760小时结果及其真实来源/核算。其余两个正常方法不是冻结busy日历的依赖。先冻结所有日期和对照，再运行busy；不能依据扰动后的损失、恢复好坏或策略输赢选日期。

保留原busy负荷窗口与既定日期/时长，唯一既定目标迁移为E5从整体TOWER改为 `TOWER_1`；四等份是研究代理，不是厂家实物拓扑。新增E8：`EC1_PRIMARY_PUMP`、9月15日14:00起、60分钟，使用正常 `M_ready` 故障前60分钟EC1实际正产冷为条件；同钟向后最多6天，取首个合法日期，不能按损失择优。E6a/E6b保留原common-tower部分双脉冲重复恢复扩展，不冒称为本地T08单故障自然恢复图的相同复制。

## 执行纪律

- 完整复制两个独立Git仓库及真实fixtures，源与日历只读，新输出只写包外实际E盘。无实际E盘或至少600GB开始可用空间则报告缺口；不映射盘符或改核心。
- 现场检测Windows/CPU/NUMA/处理器组/可用内存/盘，不复制打包机亲和。每条solver/BLAS一线程；busy生产/核账/总进程数为可配置正整数，示例三生产、一核账、总四。现场实测吞吐和实时资源监测决定实际配置，短物理门的串行执行不构成年度串行上限。
- 默认科学入口必须实际运行当前 `source/` 的 `python -m pytest -q` 并保存全部输出/退出码；不删断言、加xfail、扩容差或只挑通过子集。短门、真实恢复、实际owner账本独立核算必须使用当前profile，不借历史native-primal通过身份。
- 当前host/包/source/input/calendar/cases/path/tools绑定及当次真实门有效后才准入。verify、help、preflight、工具测试和历史回执均不授予年度准入。旧verification或玩具回执不可复用为当前证据。
- 正式长程使用 `launch_server.py` 的同用户WMI独立监督；先取得当前主机父Job关闭后心跳/停止/所拥有Job空集证明。WMI拒绝保留失败，不自动换方式或提权，不承诺注销/重启/断电后自动恢复。
- 有限年度墙钟最多30天，全批最多一次合法已提交vault恢复；失败恢复也计数，新attempt保留旧attempt。禁止静默从初态重跑，第二次事故、核心门失败或预算/资源不足则受控停止。
- 写stop不是完成，须核对归属树关闭，不结束第三方任务。求解失败、回退、不可执行动作、投影、保护和评价分开记录，不用全零/空表代替异常。

只有范围外决策、关键材料/权限缺失、核心验证失败、预算/资源耗尽或无可执行剩余工作时暂停依赖分支；独立只读分析可继续。每节点保留修改清单/diff、命令/退出码、日志、SHA、通过/失败/未执行、假设与限制。最终中文报告以每条实际完成长度和未知尾段为准，不把模型结果称为真实医院可靠率、OEM/并网认证、盲测或现价全成本证明。
