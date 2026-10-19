# 当前提交内存瓶颈只读诊断

采样北京时间 2026-10-19 13:48:49–13:49:33。此时仍有两个 M20 comparison 子进程；所有数值均是当前 OS 实测，不能代替 normal 新实测峰值。未关闭程序，未修改 pagefile、系统、冻结源或参数，未新增仿真。

## 同一进程枚举窗口的系统与进程数据

数据源为 `COMMIT_DIAGNOSTIC01.json`。枚举 504 个进程成功；一个 PID 在枚举期间已经退出，明确记为 `NoSuchProcess`，没有 AccessDenied。枚举不是原子快照，窗口内系统 commit 上涨约 0.46 GiB。

| 项目 | 实测 GiB |
|---|---:|
| 系统 commit，窗口开始 → 结束 | 47.65 → 48.11 |
| 当前 commit limit | 57.54 |
| 当前 commit headroom，窗口开始 → 结束 | 9.89 → 9.42 |
| 当前物理可用，窗口结束 | 22.42 |
| 所有成功读取进程的 private 合计 | 38.77 |
| 其中两个 M20 child 的 private 合计 | 5.125 |
| 两个 M20 child 的 RSS 合计 | 2.222 |
| 非 M20 进程 private 合计 | 33.65 |
| 系统 paged pool | 1.769 |
| 系统 nonpaged pool | 1.669 |
| 上述两池合计 | 3.438 |

M20 子进程是 PID 13908/main032 与 PID 80504/main031，private 分别约 2.35 和 2.77 GiB；RSS 分别约 0.90 和 1.32 GiB。正在运行的 comparison 有记录积累，进程和系统数值会变化。

后续独立计数器窗口 `COMMIT_COUNTERS01.json`（13:49:31–33）已经测到 commit **50.03 / 57.54 GiB**，headroom **7.51 GiB**，物理可用约 **20.34 GiB**。这是另一个时点，不能与前一窗口的进程 private 做精确减法。

## 进程按同名聚合的主要 private

以下各行 private 是可相加的进程私有提交量。RSS 含共享页面，多个进程 RSS 相加不能当作可释放物理内存的保证。

| 应用或进程族 | private GiB | 说明 |
|---|---:|---|
| Code.exe，17 个进程 | 7.290 | 当前开发宿主；不能假定关闭它不影响会话或现有监督进程 |
| 两个 M20 科学子进程 | 5.125 | 继续运行并自然结束，不终止 |
| Chrome，20 个进程 | 3.748 | 未读取普通应用命令行或标签内容 |
| QQ.exe + QQEX.exe | 3.358 | 12 + 7 个进程 |
| Weixin.exe + WeChatAppEx.exe | 2.370 | 5 + 13 个进程 |
| codex.exe | 1.331 | 当前任务会话，不作为可安全关闭建议 |
| svchost.exe | 1.268 | 系统服务聚合，不建议直接结束 |
| Steam + steamwebhelper | 1.471 | 1 + 7 个进程 |
| msedgewebview2.exe | 1.147 | 不同应用可能共用该运行时，未归属到具体宿主 |
| QQMusic.exe | 1.006 | 一个进程 |
| explorer.exe | 0.859 | 系统桌面 |
| dwm.exe | 0.592 | 系统桌面合成 |
| NVIDIA Overlay.exe | 0.494 | 5 个进程 |
| claude.exe | 0.403 | 一个进程 |
| Rterm.exe | 0.375 | 2 个进程，未检查任务或未保存状态 |
| MCHOSE HUB.exe | 0.368 | 5 个进程 |
| node.exe | 0.362 | 未假定其归属，可能涉及开发/监督工具 |

## 归因与未知边界

1. 当前观察支持“非 M20 用户/服务进程占比较大”。不能将几十 GiB 全归因于 M20 或内核池。paged/nonpaged 池的合计约 3.44 GiB，也不能凭单点数值诊断内核泄漏。
2. 系统 commit 减去进程 private 合计约 **8.88–9.34 GiB**。可见内核两池合计约 3.44 GiB；扣除后的约 **5.44–5.90 GiB** 仅是算术残差，受枚举时间差影响，还可能包括共享提交、页表与其他系统结构。没有读取到足以精确分拆这些类别的证据，不能把残差全称作“未知内核占用”或“可回收缓存”。
3. 此机器公开的 Memory counter 列表没有 `Page Table Bytes`。仅有 `Free System Page Table Entries`，当前约 4,294,560,000 个，是条目数；本次将页表已分配字节明确保留为 **unknown**，不将条目数乘以 4 KiB/8 字节来制造大小。
4. Memory 的 standby/cache 与物理可用存在口径重叠。大约 18 GiB 的 cache/standby 不能再次加到可用物理内存，更不能当作可等量增加 commit headroom。
5. 第二窗口 GPU adapter counter 的 shared usage 合计约 0.378 GiB、dedicated usage 约 2.643 GiB、total committed 约 3.533 GiB。这里只保留 GPU 原计数，不把 GPU total committed 加到/减去系统 commit，也不据此填平上述残差。
6. pagefile 当前分配 10,404 MiB，当前磁盘使用 1,606 MiB，历史峰 5,799 MiB。磁盘已用 pagefile 与系统 commit 是不同指标，不能用 1.57 GiB 推断当前系统提交压力很低。未更改任何 pagefile 配置。

## 对 72 小时 C19 串行准入的含义

已证 V19 profile 对该 case 的固定门槛为：物理可用 **33.216 GiB**，commit headroom **35.613 GiB**。这是既有 `1.2 × historical peak + 6 GiB`，没有降低。

只用第一个窗口做条件算术：如果两个 child 自然退出后，恰好仅释放本次量到的 5.125 GiB private，且其他提交、limit 完全不变，headroom 约为 **14.55–15.01 GiB**，还差 **20.60–21.07 GiB**。这不是结束后 headroom 的保证；child 后续会增长，其他应用也会变化。

Chrome、QQ 两族、微信两族、Steam、QQMusic，以及 Edge、qmbrowser、Claude、Rterm、Everything、MCHOSE、NVIDIA Overlay 的已测 private 合计 **14.545 GiB**。这是一个有具体名称的应用占用清单，不能承诺关闭会话安全、能全部等量释放，或不会重启。即便按全部等量释放的条件算术，加上两个 child 退出，也只有约 **29.09–29.56 GiB** headroom，仍低于 35.613 GiB。

再把 Code.exe 的 7.290 GiB 全算上，理想算术才约 **36.38–36.85 GiB**，余量仅约 0.77–1.23 GiB，且 Code.exe 涉及当前研发会话，不能当作安全可关闭项。其他同时增长、共享资源和物理准入都会改变结果，故不能据此保证 72h C19 可启动。

可执行结论是继续让资源监督器按每个已登记 case 的双重门槛串行准入；等 comparisons 完成后再重新测量。当前证据不支持把物理空闲视为提交余量，也不支持自动降低 48/72h 的历史门槛。若有用户自行关闭的闲置应用，仍以之后的新实测值决定准入。

## 计数器语义来源

仅使用下列 Microsoft 官方说明，未引用非官方优化建议：

- [Memory Performance Information](https://learn.microsoft.com/en-us/windows/win32/memory/memory-performance-information)：计数器与系统/进程 API 字段对应；private、working set、两类池口径。
- [PERFORMANCE_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-performance_information)：系统 commit、commit limit、物理 available 与 system cache 的定义。
- [PROCESS_MEMORY_COUNTERS_EX](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters_ex)：PrivateUsage 为进程私有 commit。
- [Introduction to the page file](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/introduction-to-the-page-file)：系统提交承诺与 pagefile 支持的关系。

以上只读诊断未确认具体共享提交/页表/驱动标签归属，也未进行任何程序关闭或 OS 改动。
