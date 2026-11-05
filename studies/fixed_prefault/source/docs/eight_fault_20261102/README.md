# 局部八故障入口与受控停止

授权范围见 `NEXT_MILESTONE_CODEX.md`；配置、数据与证据迁移见 `MIGRATION.md`，实际测试/失败索引见 `IMPLEMENTATION_RECORD.md`。

下面是已登记单局部案例的调用格式，不会自动发起年度、35 天或案例集合：

```text
python -B -m tools.eight_fault_study.runner --case-file E:/.../registered_case.json --output E:/.../fresh_attempt --stopfile E:/.../control/stop.request
```

`--stopfile` 是可选的外部运行管理路径。入口将其解析为绝对路径并记录到 `INVOCATION.json`，只检查存在与否，不读取内容、不删除标记、不传给 controller。`run_case(..., stopfile=...)` 是同一能力的可调用形式。没有配置标记或标记不存在时，原 native 执行行为保持。

检查发生在原 native attempt 的 try 内、任何环境构造之前，以及每次原 `env.step(action)` 之前。后者先于 output-only capture_scope，且保留原 step/reset 接口和原执行循环。标记出现时抛出具名 `ControlledStopRequested`，由原失败 journal/RESULT/SUMMARY 收尾；已经持久化的实际前缀保留，未观测尾段继续为 unknown，结果不会标作 completed。原循环可能已经取得当前合法观察并形成请求，但 marker 不会进入这些观察/策略，下一次 native step 与命令执行不会发生。

初始化或一次 step/solve 正在运行时不会被此检查即时中断；外部资源 harness 的 grace/Job 兜底仍需保留实际退出、已确认前缀与未知尾段。不能把 stopfile 已写入等同于进程树已停止或科学验收通过。

规范审查 `LOCAL-SPEC-01` 已使静态执行冻结同时核对 `execution_contract` 与实际传入物理 solve 的 `operating_execution_contract`。这只补齐漂移认证，不改变任何原物理参数或求解语义。本实现只做 synthetic/只读回归，真实短例仍须经主任务准入与独立复核。
