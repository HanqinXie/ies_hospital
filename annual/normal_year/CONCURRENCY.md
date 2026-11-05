# 年度进程并发配置（2026-11-05）

用户已授权解除旧normal2/busy1固定数量上限。生产、核账和总进程数使用limits.json中的正整数配置；生产及核账配额各自不得超过总配额。没有按CPU型号推导出的固定软件上限。

包内limits.example.json示例为3个生产、1个核账、总4个子进程。两包各有3个不同方法的年度case，因此单包实际最多同时有3条不同的年度生产轨迹；把max_producers写6不会生成更多方法或重复M_ready。核账可和其它生产重叠。保留独立output/temp/SQLite/journal/checkpoint/stopfile及实时RAM、RSS、磁盘、墙钟与归属树守卫。RSS等字段继续是可配置运行预算，不是容量实测结论。

一条H2/N/M_ready的8760小时轨迹仍仅有1个生产进程：下一步需要上一步的库存、温度、运行状态和已到达/待到达命令。source/tools/annual_program/runner.py按committed时间顺序调用policy_history.decide与env.step。不能把12个月当作12个独立任务并发后拼接；多个相同副本属于重复计算。可以启动三种方法的独立轨迹，同时各自保持连续时序。

本次仅解除外层进程数限制。冻结源src/hospital_ies/solver_backend.py仍为Threads=1、Method=1，BLAS也为1；更改求解器内部线程/算法或并行化单步候选求解属于另一个实现与数值验证任务。当前没有实测单条M_ready年度耗时，不承诺线程数倍加速。

正常包：完整默认科学测试、真实短物理/恢复/核账门仍保留；短门如实报告其少量case的实际并发数，不作为永久生产数量上限。全尺寸许可worker数量须覆盖请求的max_producers，server_validate.py license --workers可取任意正整数。本机不执行这些科学门。

busy包：runtime_checks.py validate支持--limits-file，未指定时使用同一3/1/4默认值。请求的规范化limits同时绑定在SCIENTIFIC_GATE和ADMISSION中，后续不能只改ADMISSION提高配额。原真实短门仍串行执行，其producer_concurrency=1表示该短门的实际测量，不限制年度并发，也不冒充已测3并发；原独立license/environment探测skip政策保留。实际执行遇许可错误须记录原异常。

busy仍须等待新版同源正常M_ready完整全年参考，冻结共同故障日历后再执行；并发数量解除不改变这一研究设计依赖。正常/忙年同时运行时须共同考虑整机内存和同盘I/O，现有两包没有全局自动吞吐优化器。

本轮本机只运行纯交接工具/调度测试和文件核验，不运行IES、默认科学测试、目标机性能基准或年度。source、inputs和cases保持原SHA。
