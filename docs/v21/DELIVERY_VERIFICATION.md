# 本次冻结归档、当前源重放与默认测试的实际验收

本说明在外部实际proof已通过后生成，属于ZIP之外的后续验收记录。医院服务结论仍以[工程报告](SCI_ENGINEERING_REPORT.md)的接续、持续、恢复与费用/库存取舍为准；本次复现不改科学值、CI、132行参数表、原生结果和已封图件。

[外置验收证据镜像](reproduction/final_archive_actual02/README.zh-CN.md)提供root随后按原字节/SHA发布的少量验收证据；它位于现有ZIP之外，不改变冻结归档或物理核心。

- 真实冻结ZIP：`E:\hospital_ies_v21_20261023\delivery_final02\Hospital_IES_SCI_V21_20261025_r02.zip`；319053385十进制字节，严格小于400,000,000；SHA `b92d5f2024d8fb5605a5f52b9f58e6bb7a1aec074525ffd5331e6d1e2cfc46d0`。本次新解压工程：`E:\hospital_ies_v21_20261023\archive_clean_v21_final02\project`。
- 原件、全部打包成员及新解压已实际核验。当前643源码绑定`4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add`，28输入绑定`659ecc488bda87c9338afde283cbb74135ca2ebdf2769ce463d0e61f0c6288d8`。
- 从本次归档的新E解压工程，以原guarded verify/prepare/run三步实际完成C/H0、L2的360 min已见案例；365/365预登记标量通过、1 started/1 finished/completed、unknown tail为0，原生主轨迹/sidecar证据完整。它新增独立背景n=0，没有重新执行2778条科学轨迹。
- 新解压根目录默认pytest真实摘要：`692 passed, 1 skipped in 218.45s (0:03:38)`。退出0、0 failed/error/xfail/xpass/deselected；可选跳过数按原摘要披露。没有借旧692项或旧2088 clean03代签。
- 实际proof：`E:\hospital_ies_v21_20261023\reviews\final_archive_actual_verification02\ACTUAL_FINAL_PROOF.json`；外部已知SHA `184440e9502175f7fc5b36d87ed0965c31c4e376a54dae0fdbd25194b8a2474b`；proof生成UTC `2026-10-24T22:45:41.624096+00:00`。本更新器另读原命令、stdout/stderr和三步OUTER_RECEIPT，未运行物理、MATLAB或pytest。

首个316,998,489字节冻结ZIP及final01解压曾实际完成360 min/365标量native重放，但随后默认pytest真实退出1（209.3587084 s；测试摘要20 failed、672 passed、1 skipped，208.70 s）。完整20 traceback中19项缺744字节历史策略配置`outputs/v13_executed_service_20261017_run01/design/DEVELOPMENT_POLICY_SPECS.json`，另1项缺55,229字节`outputs/v18_execution_recovery_20261018_run01/design/LONG_FINAL_R1_PROPOSAL.json`。两原件SHA分别为`c89c50716465e8b0b3ee34e134e2a9ae7dcbf99f359472e69e3fe40885a15e9d`和`4c5ef87c24c4e8000bf8b54c663801178de8516b61a78090381cc392b34e3e48`；这是历史测试支持缺项，不能将首包默认测试称为通过。

root随后只在已失败的final01解压作诊断：补两项后目标20为19 passed、1 failed、55 deselected（12.598962 s、exit1），进一步显露`outputs/v11_autonomous_dispatch_20261016_run01/new_data/standby_manifest.json`缺失（4,326,180字节，SHA `ca01d42fd1e2cefdf5e9ead956d4237910eedcf382177695682a04e53088e1a3`）。补第三项后剩余目标为1 passed、23 deselected（3.220843 s、exit0）。这两次过滤诊断不代替新包默认测试；原ZIP/失败/stdout/stderr/native365证据保留。本页上方通过只属于外部proof实际指认的final02归档，不从首包继承。原48项加三份原字节缺项共51项支持、8,509,214字节；新增4,382,153字节不改变643物理源码或28输入，也不修改测试断言、容差或skip。新增权威索引为`E:/hospital_ies_v21_20261023/reviews/test_support_addendum_inputs01/EXPLICIT_DEFAULT_TEST_SUPPORT_ADDENDUM02.json`，SHA `15e1c44f7e2c0d32afdc8e64a5ee6638906d5c20b66e21a83103005255816a6b`；本更新器实际准入时另核三原件在D和新解压位置的SHA。

a3补充组合已实际62.2496104 s、exit0并通过原4171 inspect：原23,478成员所有字段不变，追加12个测试支持/索引/失败日志/说明/工具/active及lineage成员，selection共23,490；7535 external_raw不变、原active33520前缀exact。receipt为`E:/hospital_ies_v21_20261023/staging_final_package03/runs/a3/ADDENDUM_RECEIPT.json`（SHA `1ae5d2b7e1a406646449ab74aa66f0995270ab279da6f49f5e71e0aff8f27c62`），selection SHA `9054fbb21a1911fe15d3cadcf7eaf83c923115b5ff07d8a5707cf126594ecaca`。这只是实际组合/inspect记录，不是第二次原03 prepare、科学gate或新ZIP/新默认测试通过；后者由本页上方外部actualproof另证。

首次原八完整raw组真实build得到812,808,427字节并因十进制硬上限失败；原失败包、ValueError及exit1保留。仅在该真实失败之后，按原协议优先级完整保留前两组：当前源固定参考1条和主L2的五方案5条，共6轨迹/12 gzip。尾六组整体省略raw打包，仍在external_raw全部7535行及新PATH_REMAP_ACTIVE.json中追溯；全科学RESULT、失败、UNKNOWN、表与原23,453非raw成员字节不变，原八组先验和原indices作为历史保留。不是按服务结果排名或删事故。

原03一次全量选择3038.456697 s、exit0已真实完成。新E-only size-only组合helper及其独立44项合成metadata检查不是研究轨迹；a2 actualderive为167.4909182 s、exit0，不能称第二次原03 prepare通过。该次a2派生元数据ZIP实测5,719,522+128=5,719,650字节上界小于8 MB；这是历史a2量，不是加入测试支持后的最终ZIP大小，最终大小只取本页上方实际proof。上述边界绑定`E:/hospital_ies_v21_20261023/staging_final_package03/runs/a2/SIZE_ONLY_DERIVATION_RECEIPT.json`、`TRANSFORM_LEDGER.json`及实际ALL8 size review。随后只读独审确认23,453原nonraw exact、12 raw/6 case保留、74 raw仅按六完整尾组省略，receipt为`E:/hospital_ies_v21_20261023/reviews/actual_prefix2_selection_independent01/attempt02/INDEPENDENT_AUDIT_RECEIPT.json`（SHA `e14ff3689a44d614ead88126881689ca04ebb56b41726d458656db8f53fcbc90`）；该审查不是科学gate或ZIP实际验收。

实验核心HEAD为`a5d3ed762e616b0c2537c74971753380999b0d4d`。研究分支为`sci-v21-engineering`；本次后续说明提交可以有不同Git HEAD，物理源码643/输入28仍按上述绑定。最终普通Git提交/推送状态以Git和外置最终回执为准，不由本proof代签；真实HEAD和远端状态不在此预填，不填写未来commitSHA。

ZIP是实际验收之前冻结的科学快照，内部pending标记反映冻结当时状态。包根README来自固定guarded overlay（SHA `6e36c6bf125f23e6a0d8c008523aeb2f59120721f537c9fddb485b99115bd0fd`），与仓库研究README用途及字节不同。包含ZIP自身SHA的未来proof、解压后重放/pytest和本文档更新放ZIP外，不重新打包形成自SHA循环。历史V20、旧2088 clean03、失败和UNKNOWN不改写。

全局5000次/96 h沿用UTC epoch `2026-10-23T06:06:02.282879+00:00`，跨freeze不重置。最新实际census由proof外部引用，下面原值展示；主1920+Q1补充240+支持252+硬件126+final240=2778仍为五实际科学池，必要界112已包含在252内，12/19/3的源块统计不合并。

主机技术披露仅取2026-10-24 20:36:26 UTC只读`powercfg /QUERY SCHEME_CURRENT SUB_SLEEP STANDBYIDLE`：方案Turbo、AC与DC睡眠索引均0。证据为`E:/hospital_ies_v21_20261023/reviews/final_host_state01/logs/read_sleep_state01.*`。该记录不能说明更早原值、是谁设置或已恢复；本更新器不修改系统电源设置。

CPython审计守卫不是OS级native-I/O沙箱。归档/重放/默认测试通过不升级为跨医院验证、临床安全、OEM保护/黑启动、真实油耗或硬实时保证；原温度夏季及恢复未验证边界保持。后续本地绘图的新4/44完整视看仍NOT_RUN，原正式图件视觉接受仅适用于原图。

原生三步实际命令：

- `E:\hospital_ies_v21_20261023\reviews\final_archive_actual_verification02\logs\guarded_verify01.command.json`；SHA `f2544995c5a390a793b70154b772378dc1df6b11d7b9101081bfdaaf06a3ebe3`。
- `E:\hospital_ies_v21_20261023\reviews\final_archive_actual_verification02\logs\guarded_prepare01.command.json`；SHA `c9afc7910dfb87df85ffcd6879d059438322db89d8761911aaf8c4de65f8bbb2`。
- `E:\hospital_ies_v21_20261023\reviews\final_archive_actual_verification02\logs\guarded_run01.command.json`；SHA `c060bf368db37269895ba68917ca6d0a13ad0208f34a2170cc1999d2b3738aee`。

实际默认pytest命令：`E:\hospital_ies_v21_20261023\reviews\final_archive_actual_verification02\logs\fresh_archive_default_pytest01.command.json`；SHA `95f0d68a549ebe7223a2903b7302edc4b86fef459448a56bc347f074e6946e7d`。

最新外部全局普查：`E:\hospital_ies_v21_20261023\reviews\final_archive_actual_verification02\POST_RUN_CENSUS07_01.json`；SHA `211fe3a0120703f77b669b605fb6438d2f16252bd4978ff91eeb5bfab1873c6c`。

```json
{
  "overall_budget": {
    "epoch_utc": "2026-10-23T06:06:02.282879+00:00",
    "current_utc": "2026-10-24T22:45:25.517679+00:00",
    "max_actual_attempts": 5000,
    "max_wall_hours": 96,
    "started_since_epoch": 3728,
    "reserved_since_epoch": 3728,
    "charged_attempts_conservatively": 3728,
    "remaining_attempts": 1272,
    "elapsed_wall_hours": 40.65645411111111,
    "remaining_wall_hours": 55.34354588888889,
    "within_budget": true,
    "freeze_changes_reset_budget": false,
    "statuses_since_epoch": {
      "completed": 3722,
      "failed_unknown": 6
    }
  },
  "all_M21_terminal_counts": {
    "completed": 3775,
    "failed_unknown": 29
  }
}
```
