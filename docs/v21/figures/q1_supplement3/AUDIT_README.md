# Q1独立补充：实际MATLAB两图

正式候选交付为 `styled_figures02/` 的两张F1/F2，各有原生FIG、SVG、PDF、600 dpi PNG；完整PDF预览在 `previews02/`。对应实际重开对象收据在 `objects02/MATLAB_OBJECT_QA.json`，格式在 `structure02/ARTIFACT_STRUCTURE_QA.json`，边界在 `boundary_inspection02/BOUNDARY_OBSERVATIONS.json`。本目录仅E交付，root目视/发布D另立记录；没有新science gate、模型计算或参数选择。

来源为root已接受的Q1补充240条：gate d65d9843…、原native parent3822c6c9…、独立q1_supplement3 child a66186ab…。完整SHA见PREPARATION_BINDING。D已发布的21成员与原E逐SHA/size核同，原CSV/MAT、行序和统计均不改。QA03派生authority要求canonical E child/tables；实际EXPECTED和图内UserData保留该真实来源，未将D镜像伪装成原child自身输出。数据是3个Q1源块、48scene×五策略=240；每类12scene来自每块4变体，块内平均后3块等权。不与19合为n22，不新增医院/天气年或置信区间。完整图注见CAPTIONS.md。

实际流程与退出：

1. preparation_binding01 exit0：21镜像成员与原E一致；本地最小数据/源码复制并核SHA。
2. EXPECTED exit0/5.5266s：原QA03完整authority链、240 CASE→2图，SHA6c9638fb…。
3. 第一次MATLAB 04:49:12.328–04:49:45.465 UTC，33.1362s exit0：原e4d绘图、原eec class tick、原QA03重开全部FIG。原01数值/字体/格式均通过，但PDF边缘较tight；真实页边发现、metric与字形区别及只读诊断完整保留。
4. 新页面函数d60c0ba1…复用原437的restoreViewLimits/snapshot/hashFile逐字tail；仅Paper、白色无填充矢量画布与fullpage导出。第二次MATLAB 04:55:34.306–04:55:57.510 UTC，23.2035s exit0。严格图元/figure与axes及series UserData/axislimits前后和导出后相等；同一EXPECTED重开全部FIG通过。
5. 新02格式、完整PDF渲染和边界三命令均exit0。FIG无image，PDF/SVG无raster，实际字体嵌入宋体/TNR；PNG4252×3307与4252×3071、599.9988dpi，FIG几何理论600 dpi也通过。两PDF所有font metric bbox越页0，完整PDF渲染最小白边14px，PNG最小白边37px。作者已实际看完整PDF/PNG，见VISUAL_REVIEW.md；不把原01初看回写成已完成最终边界复核。

所有启动均先核可用内存≥8 GiB、commit≤85%、无另一MATLAB，单`-singleCompThread`；实际启动使用guard12（84.8474%、16.257GiB）和guard13（84.2633%、16.384GiB）。guard01–08/10–11的真实未达门记录保留，guard09只用于随后EXPECTED，未被当作MATLAB旧观察沿用。第一次临时内存查询的PowerShell/Python引号SyntaxError也如实另记；没有把它算成物理失败或图形通过。

原e4d/eec/QA03和所有科学数据不改，原native/styled01/objects01/structure01与边缘诊断不覆盖。新fullpage函数只处理本次两张Q1图，不修改已封主图或F6。`local_replay/`保留原字节CASE/SCI_TABLES/空MECHANISM及三份MATLAB源和相对入口；本地重画命令见其README。该相对入口本次未另生成重复图，完整authority重验与便携数据重画范围明确分开。

实际命令/UTC/exit/stdout/stderr都在 `logs/`、`commands_and_exit_codes.txt`。最终RECEIPT逐项绑定这些文件和外部来源；原失败和NOT_RUN项不抹去。该收据不等于整项研究、HW/followup或最终盲测已完成。

首次封存脚本因合并已有name键时重复传参TypeError退出1，第二次又因引用审计日志的parent层级错误退出1，均尚未生成最终收据；不是图形/数值失败。两版原脚本保存在history，两次原外层日志在reviews/Q1_figure_closure_audit01。修复仅取已有command对象、引用准确日志路径，并对第一次已生成的本地manifest精确比较复用、不覆盖；所有源数据/图形与原断言不改，重新完整执行封存检查。
