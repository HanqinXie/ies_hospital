# 最终文档只读审查（待owner更新）

状态：仅保存五文档的实际SHA及发现，不是最终406、正常12、推送或ZIP成功报告。资源plan701d未修改。

| 编号 | 优先级 | 具体位置与必要处置 |
|---|---|---|
| D01 | important_before_final_release | docs/v20/ENGINEERING_RESULTS_REPORT.md:166,169。旧analysis02被写成当前可复用prepared collect入口；它硬要求S3 passed、normal03 exit0及canonical02的11次launch，已与真实stop链不兼容。 保留analysis02/旧oneshot验证历史及exit15，明确superseded；最终改指实际成功且另行资格的analysis03及新continuation，不改旧receipt为成功。 |
| D02 | important_before_final_release | docs/v20/REPRODUCTION.md:54；docs/v20/IMPLEMENTATION_PLAN.md:3；docs/v20/ENGINEERING_RESULTS_REPORT.md:163,164,166,169。仍用现在时说S3继续另外11条；实际canonical02完成10条后原门槛阻塞008，normal03明确退出125，S3状态failed_unknown。 补真实链：S2错误/009科学完成而资源unknown；canonical02十条完成；normal03 exit125与S3 failed_unknown；新轻控制器仅剩008。125只属于normal03，不移写给S3。 |
| D03 | required_actual_completion_update | README.md:30；docs/v20/ENGINEERING_RESULTS_REPORT.md:3,36,42,48,83,133,138,154；docs/v20/IMPLEMENTATION_PLAN.md:3,38,42,43,44,45,46,47。正常窗与最终406的pending当前安全正确，但终稿需统一从实际S4/analysis03/406证明更新，不能仅按外层run开始计完成。 最终按真实148批/406科学轨迹及12正常同窗资格/结果填值，保留不同正常持续时间；009资源仍unknown，不能把科学unknown_full=0扩大为全部资源无unknown。 |
| D04 | required_chain_and_resource_identity_update | docs/v20/REPRODUCTION.md:30,52,54；docs/v20/ENGINEERING_RESULTS_REPORT.md:156,163,164,165,166,169；docs/v20/IMPLEMENTATION_PLAN.md:3。终稿尚未交代原1.2×+6与单008新1.1×+2（有余量优先3GiB）的资源调度区别。 链接不可改的701d资源plan及本目录独立比值；解释只是资源调度、无科学freeze/阈值/solver修改；资格短命、常驻轻监控、旧辅助失败原样保留。资源分母必须是10 canonical02＋008新controller＋009未知。 |
| D05 | required_incident_disclosure_pending_new_candidate | docs/v20/ENGINEERING_RESULTS_REPORT.md:156,169；docs/v20/REPRODUCTION.md:54；docs/v20/IMPLEMENTATION_PLAN.md:3。Root新报告L72_RUN01实际观察循环工作＋固定sleep导致约4s采样，<=3s gap守门正确拒绝，008未started；687合成通过/check-only不证明真实稳定窗口已通过。 保留旧RUN01/源SHA/真实STOP/未知状态；若最小deadline cadence修复通过，按新SHA/新RUN02另登记并读实际30s观测；不放宽窗口、gap、容量或科学容差，不称为物理008失败。 |
| D06 | clarification_before_final_delivery | docs/v20/REPRODUCTION.md:58,61,64；docs/v20/PUBLICATION_SCOPE.md:37,39。单普通commit/后验clone及外部receipt的时序原则正确；实际archive capture必须继续在clone外，否则会把自身未闭合log纳入payload。 最终执行说明区分ZIP内closed clone/core/replay证明与ZIP外archive实际START/log/COMMAND/final receipt；不把预估deflate字节当实际ZIP大小，不追加第二次main提交回填后验证据。 |
| D07 | minor_wording | docs/v20/ENGINEERING_RESULTS_REPORT.md:99,113。99行费用和轴功评价分项的措辞可被误读为实际能源费按轴功；113行已正确说明不重复计DG。 可统一表述为实际能源费用沿原DG电量计费，轴功替代费用另列，减原项再换入；无需改数值或源码。 |

## 已正确保留的边界

- README57 already explains inherited modules/smallfixtures kept at original paths and old training/audit is not a current command; PUBLICATION_SCOPE13/14/17/29 supplies closure detail.
- Four originalCSV/IDF/EPW and28runtime kept; PDFs/XLSX/old66inventory are not requiredM20dispatch; applicable Gurobi license remains external.
- Fixed8 is seen; final12 unopened; different carrier/window totals not pooled; no zero-service-success claim.
- PUBLICATION_SCOPE25 and REPRODUCTION64 require strictlybelow200000000bytes, not200MiB; one commit/no secondmain commit/postpush evidence future wording is correct.
- Current pending wording prevents premature406/postpush success; update only when actual evidence exists.

## 用户最终八项

- 1：covered_confirmed；ENGINEERING 7–22; FIELD_CONTRACT/DATA_CORRECTION_REPORT links: separate e/h no second multiplier and c one aggregation multiplier
- 2：covered_confirmed；ENGINEERING11–22 and README16:668.520165MWh electric and490.685218MWh heat returned toP3;P0heat4695h rises;137cold compression retained
- 3：pending_final_actual_evidence；ENGINEERING36–50/156–169; final405+008/normal12/148406 qualification, actual errors/stops/unknown ledger must be updated from saved proof
- 4：covered394_pending_normal12；ENGINEERING54–138 and main/long/fixed analyses; normal four unequal windows require actualanalysis03
- 5：covered_with_limits；ENGINEERING140–152 preserves actual bad cases and proxy/OEM/topology/temperature/fuel/emissions boundaries
- 6：future_actual_git_operation；README5 V19tag present; PUBLICATION_SCOPE31/39 one ordinary main replacement preserving history; actual V20 commit belongs final external report/receipt after push
- 7：candidate_actual_postpush_future；REPRODUCTION3–7 separates actual local candidate463/600min/94 scalars from future fresh GitHub clone; final must state actual clone/core/replay scope only
- 8：covered_remaining_scientific_work；ENGINEERING146–152 lists data mapping/cold137, physical/OEM timing and protection, temperature/clinical calibration, independent evaluation and paper table updates

精确源SHA与完整发现见FINAL_DOCS_READONLY_REVIEW01.json。没有改README、docs、pinned plan、源码或测试，没有执行控制器或物理。
