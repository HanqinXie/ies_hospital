# 单设备派生注册与运行接口交付记录

基线：`911d7587295d2ca6f5cf29a315585498fdb6875b`。本子任务仅新增注册、显式场景降译及因果账本；没有修改旧注册器、事件白名单、物理约束、设备参数、库存积分、命令队列、求解器或旧结果。

新增生产代码：`tools/single_unit_20261104/__init__.py`、`registration.py`、`runtime.py`。接口为 `make_case(base_case, *, kind, variant, base_binding)`、`validate_case(case)`、`build_runtime(case, source_freeze, cooling_config)`。

CASE 保留逐字节文件绑定及完整原 canonical base CASE；`minute1` 元数据原样保留。严格重建验证派生差异、声明场景、内部原生场景及两者摘要。新增 reset wrapper 只接受该声明场景，并将明确绑定的内部场景交给原生 reset；没有扩大旧校验权限。

单塔声明及内部事件均为 `TOWER_1`。一次泵声明为 `EC_1_PRIMARY_PUMP`，内部 `EC_1` 可用率只是支路流量联锁。每个实际区间的 `single_unit_causal_ledger` 分列泵状态、EC1 本体原始健康状态与 EC1 功能可用率，保留公共二次泵及储冷路径。新增的唯一 core hook 在原实际提交完成后追加账本，不重复提交；使用 accepted result 的 `_cooling_raw_availability`，因为原生 `hardware_availability` 要在外层 advance 导出时才附加。没有覆盖 `_solve`、`_local_interval_action`、`_hardware_data_at`、boundary 或物理分段函数。

新增假设：DOE 专用一次泵支路映射到三冷机研究站的 EC1；这是明确的功能联锁代理，不是 OEM 水力标定。四塔模型中的一个等权 slot 也不能认定为 DOE 某个 tower cell。没有新增水量、流量、扬程、独立泵功率、备用泵或恢复时间常数。泵联锁硬退出及修复后恢复沿用原 EC 120 s 动态；单塔剩余组仍可用时，公共 tower readiness 不归零。

## 实际验证

以下命令均在 `D:/hospital_ies_single_unit_20261104` 执行，终端与退出码留在 `logs/`。

1. 初始 `python -m pytest -q docs/single_unit_20261104/test_registration_runtime.py`，退出码 4。原根 conftest 的历史路由只绑定另一显式工作树，因此拒绝该新工作树。没有修改旧 conftest 或断言。
2. 设置进程级 `PYTHONPATH=D:/hospital_ies_single_unit_20261104;D:/hospital_ies_single_unit_20261104/src`，执行 `python -m pytest -q --confcutdir=docs/single_unit_20261104 docs/single_unit_20261104/test_registration_runtime.py`。`02_RED...` 为缺失接口的 18 项失败，退出码 1；`03_GREEN...` 为 18 通过，退出码 0。
3. 原生提交/导出时序回归：`04_RED...` 真实捕获账本提前读取尚未导出的 `hardware_availability`，1 失败，退出码 1。改为 accepted result 原始功能可用率后，`05_GREEN...` 为 18 通过，退出码 0。
4. 最终同一 focused 命令在 `07_FINAL_registration_runtime.log` 中为 **18 通过 / 0 失败**，退出码 0。
5. `python docs/single_unit_20261104/probe_runtime.py` 的最终终端记录为 `08_FINAL_REAL_RESET_PROBE.log`，退出码 0。三项独立接口探针分别为 T05 单塔 R、T06 一次泵 U、T06 健康 U0：均由真实 native reset 起步并提交实际 `[0,1)` 分钟；验证了受保护 `_solve` / `_local_interval_action` 身份及因果账本与原生导出一致。59、60、119、120 分钟仅作纯当前硬件函数查询，没有移动物理钟、修改库存或伪造连续轨迹。JSON 明确标记 `formal_scientific_trajectories_executed=false`。

未执行默认全量历史测试、正式 180 分钟轨迹、训练、参数搜索或提交；正式结果必须由后续 runner 单独产生，以上接口探针不是案例科学结果。
