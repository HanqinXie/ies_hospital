# V24 封包前验收与包外最终回执

模型实现、受影响计算与独立核账已终结。本文件在封包前写入Git；补丁重建、ZIP最终字节/CRC/成员SHA和解压后的新运行，分别在包外生成实际回执，避免把待执行验收预写成通过或制造自引用ZIP哈希。

## 已实际完成的模型门槛

| 验收 | 实际命令/记录 | 实际结果 |
|---|---|---|
| V23基线 | `provenance/BASELINE.json` | 741源码、28输入逐文件一致；V23原ZIP95,124,996字节及原SHA保留 |
| Source01首次默认回归 | `execution/steps/full_pytest01/COMMAND.json`与stdout | 1029 passed / 1 skipped，329.43s，exit0 |
| 冷库循环反例/局部回归 | `review/cold_core/source02/`与原日志 | 先真实RED，47 passed / 48.75s；包含新17项，原失败及夹具错误保留 |
| precollector默认回归 | `execution/steps/full_pytest02/` | 1029 passed / 1 skipped，336.43s，exit0；当时默认入口漏收17，未冒称覆盖 |
| 最终默认入口 | `python -B -m pytest -q --basetemp E:/hospital_ies_v24_20261026/pytest_full03_release` | 1046 passed / 1 skipped，341.42s，exit0；日志在`execution/steps/full_pytest03_release/` |
| Source01实际首批 | `formal_stage01`与`formal_remaining01`原命令/批次记录 | 26项完整、每项1次实际episode、exit0；冷12标为历史开发结果 |
| Source02受影响完整重算 | `formal_cold02`原命令/批次记录 | 12项完整、每项1次实际episode、unknown_tail0、exit0；720.593s，独立双进程，无资源guard中断 |
| 最终cohort | `provenance/FINAL_COHORT.json` | 明确14 Source01无冷库+12 Source02冷库，26项独立native审计通过，不改写实际source身份 |
| 对照资格 | `review/metrics/`最终小表 | 4个同源策略配对、9个同源资源配对；跨版本legacy仅作实现诊断 |
| 温度有界诊断 | `review/temperature/` | 8次实际fit失败门槛保留；4次同日期EP补输出exit0/无severe/fatal、1090尺寸一致；定量夏季恢复未合格，默认关闭 |

唯一skip是未设`SCI_SOURCE_ROOT`时的可选只读原年度SQL重建测试；已发布8760分配与独立缓存重放实际覆盖。没有xfail掩盖已知核心问题，没有删除旧断言或扩大原容差。默认入口覆盖的是pytest.ini保留的支持模块，不声称重新跑遍全部历史研究/训练工作流。

最终发布科学源码SHA为`c8fc844dcad5a6ed59968bf548a7a62b95157d4c91f5fb4af52a377670d2f776`，758文件。Source01为`9cf688e48421e64603f28fdac68aa37a3abc00e8062c9f2978e35fc42bb94d88`，756文件。28原输入聚合SHA始终为`659ecc488bda87c9338afde283cbb74135ca2ebdf2769ce463d0e61f0c6288d8`。

`388631…`是修复核心但未补默认收集项的precollector；其真实局部/小过程证据保留，正式episode数0。发布版本只比它改pytest.ini测试收集，生产字节相同。Source01→发布仅一项生产文件变化（cold_modes.py），另有新增回归/精确旧源码fixture和收集配置。14复用的容量/功率/实际库存均为0，1309原段与UPS0/20半库/40满库旧新路径一致性证明见scope/reuse，不仅凭H0名字判断。

原独立scope审查及封包前早期core回执在其写入时正确标记root后续门槛pending，原字节保留。`provenance/SCOPE_REUSE_FINAL_ADDENDUM.json`以真实full03、formal_cold02、FINAL_COHORT及最终26审计的SHA引用关闭这些后续科学门槛；不要将旧快照pending误读为最终未验收，也不要改写它冒充当时已完成。

## 包外最后验收

本机根目录为`E:/hospital_ies_v24_20261026`。下列回执由真实执行产生；本文件不预填其通过结果：

- `PATCH_VERIFICATION.json`：从V23 `732fd6bd1bdaae754cce930e1084fe2d849d0771`完整补丁，用独立Git index重建最终tree，记录命令、退出码与tree值。
- `FINAL_SOURCE_VERIFICATION.json`：最终源码/输入、38项主批次与历史RESULT未改、V23原ZIP及Source01精确runtime仍完整、Git状态与最终提交。
- `HOSPITAL_IES_V24_REVIEW_under100MB.zip.receipt.json`：严格小于100,000,000字节的最终ZIP、SHA、CRC及全部manifest载荷SHA，manifest自身明确排除自引用。
- `CLEAN_REPLAY_VERIFICATION.json`：全新E盘目录解压，实际执行当前M24短例、独立服务/库存/费用核账、可移植蓄冷小反例，以及冻结温度只读重放；逐命令/退出码/新输出路径记录。

不因封包/文字/标签变化再重算全部物理过程。解压新短例仅验证交付运行闭合，单列为封包后验证，不混进正式26项结论。没有远程push或main合并。

原PROCESS为完整文件；ACTUAL_PREFIX保持原行和值，精简包没有以COMPACT冒充原表。大RAW/SQL/ESO/solver CAS留本机原位，省略清单逐文件给路径/大小/SHA；小反例RAW、全部失败/未知/回退账保留。来源资料及研究假设、温度不合格和传播边界分别见模型报告、ASSUMPTIONS及FAILURE_AND_UNKNOWN。
