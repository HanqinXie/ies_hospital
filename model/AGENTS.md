# Hospital IES 当前协作边界（2026-10-28）

先读 `docs/publication/IMPLEMENTATION_PLAN.md`、`release/CURRENT.json` 及对应版本任务书。本轮用户明确授权清理并更新GitHub仓库默认分支、写入最新模型/结果；旧科研任务的 no-push/main-merge 阶段停止点不适用于此次发布。不得由此扩大系统、账户、凭据或付费权限。

- 当前科学基线为V30 fix1/C02物理核心、V31 P01措施、V32 P01外部参照。873个科学源码（含pytest.ini）和36项原始输入按 `release/FROZEN_SOURCE_V32_P01.json` 原字节保留；任何后续模型修改必须另立研究版本和验证，不覆盖既有结果producer。
- 不使用MPC，不新增未来信息在线控制器，不改容量、负荷、保护阈值、分级/服务判据来制造优势。V32仅自身参照允许登记的offline_known_window外生信息，C/M权限不变。
- 旧文件清理仅针对明确清单中的当前Git工作树文件；保留历史提交、恢复标签和E盘科研原始/失败/审计数据。`outputs/`保留的是默认测试所需夹具，不得按旧版本号整目录删除。
- 不删除断言、扩大容差或改写pytest选择集掩盖失败。先复现，定位具体数据/接口/物理问题，再最小修复和复跑。
- source_binding会枚举src/tools/scripts/tests内Python、configs内JSON、根Python及pytest.ini；input_binding枚举原始inputs和requirements。发布辅助工具放 `release/`，避免无意改变873/36绑定。
- 默认只读核验：`python -B release/verify_repository.py`。冻结默认测试：`python -B -m pytest -q`。V31/V32新增测试按README显式补验，记录真实命令/退出码/日志及失败与跳过。
- 不自动启动新医院实验、调参、训练或扩展背景。新原生/临时大数据放D/E，不能写C盘；每条真实重跑使用全新输出目录。
- 故障状态、命令生成/到达、实际执行、保护和评价分离。库存、缺供、费用、末态、失败/回退及未知尾段不允许静默丢弃或填零。
- 涉及同一物理状态转移函数的修改仅一个实现任务串行处理。发布/资料整理可并行，只在各自目录工作。
- 不重写Git历史、不强推、不删除已有分支或release。推送前检查当前远端头，远端发生新工作时先保留并整合。
