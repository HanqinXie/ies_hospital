# Q1三源块图的本地数据重画

本目录的CASE_METRICS.csv、SCI_TABLES.mat、空MECHANISM_EFFECTS.csv，以及publication和class tick两份MATLAB源，均从root已发布的Q1数据/获批源码逐字节复制；第三份style_q1_fullpage.m是本轮实际通过的页面/导出几何补丁原字节副本。完整21成员的原E/D镜像对照见上一级PREPARATION_BINDING.json，全部本地成员见LOCAL_REPLAY_MANIFEST.json；这里仅保留原plotter重画两图所需的最小数据，不冒称包含全部统计审计链。

在MATLAB中将当前目录设为本目录，使用全新输出目录：

```matlab
replay_q1_figures('E:/a_new_Q1_figure_replay');
```

若解压到其他位置，将上例输出改为实际新目录。函数从自身目录定位data与code，不依赖旧E源码或原raw；相同publication、class tick样式和无填充矢量画布只调整图形几何/导出，不更改数值、统计、字体或策略。原始CASE中的路径字段不改写，它们仍是来源历史记录。

本地重画与完整authority重验不同。正式图的EXPECTED/FIG核验仍由原QA03、已知child/parent receipt及其canonical E tables完成；不能把本地data路径当作该receipt的原输出，也不能伪造新native/cohort receipt。复现时要先核可信archive/member清单与本目录文件SHA；重画后的新图仍需真实对象、格式与视觉检查。

这是单列Q1补充：3个源块、每类12场景（每块4个变体）、五策略；图的均值先块内再3块等权。未与19块主分析合并，不表示22块汇总或22块置信区间，也不是新增医院/天气年。本地重画入口当前未实际执行，不复制其他已封存图作为产物。
