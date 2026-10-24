# main02 primary19：F1/F2 MATLAB 审查图

本目录为主阶段内的真实统计审查图。生成时独立整池物理审计和 root 科学接受仍待完成；本目录不签科学 gate，不代表边界、硬件、Q1 补充或最终盲测研究已完成。物理源、28输入、策略与统计值均未更改，没有新增 physics。

## 图注

**F1 关键服务。** 医院单一原型/天气年 primary19 的同窗720 min结果；每类76个登记场景来自19个源块，五方案在同场景/H0下配对。每个柱先在源块内对该类场景平均，再对19块等权平均，不能把76场景或五方案扩写成独立样本数。电、热、冷及P0/P1分别展示，不跨载能相加。L3微小电ENS仍保留，在当前纵轴尺度下不明显；material中断为0不等于数学零ENS。F1类别标签只旋转以便阅读，bar数值、颜色和UserData与原生成图逐项精确相同。

**F2 一般服务与代价。** 同一primary19面板、同一块内/块间平均口径。P2/P3为原判据下各载能中断时间并集，非电热冷时长求和。费用按既有来源的**2001年历史USD**口径，条件CO₂按已登记排放系数计算；不是当前报价、设备投资、生命周期成本或实测碳排放。关键供能改善、普通区少供、费用和条件排放须共同阅读，不能刻意放大A−C微小差。

两图只展示均值，不含CI。每类76场景/19块不等于76个独立背景；primary19分季Q1只有1个源块，不能给出可估的源块区间，见对应统计表。其余区间也须按原统计表和源块聚类口径解释，不能从柱图目测构造显著性。旧debug5另列，未混入本目录。

## 实际来源与代码

- 数据：E:/hospital_ies_v21_20261023/main02/cohorts01/primary19/tables/CASE_METRICS.csv 与 SCI_TABLES.mat，完整逐项绑定见 SOURCE_DATA_BINDING.json。
- 派生收据：DERIVED_COHORT_RECEIPT.json，SHA 5ad2900ca973e014b1cbc0b5bef8c77503c08ef5fb030fe37d77b383a2ce3825。
- 原生父收据 SHA：93551709c42acd6656eeefe79e39e25ab656c9f1d2ddb777e50b3610ae02323d；当前物理source为4dfe62ae…，643文件/28输入，未变。
- 本目录两份.m为已批准源的原字节副本：plot_sci_engineering_publication.m（e4d95df…）与style_sci_class_ticks.m（eec2216d…）。原D文件保留，调用命令与日志在E:/hospital_ies_v21_20261023/main02/figure_qa01/logs。
- 单线程MATLAB实际生成→字体/标签既有样式处理→重开两FIG数值/字体QA，exit0，38.756884 s。已有QA03只读核10轴、50个bar序列、200个柱值及原CSV/MAT/类别/源块数/颜色/UserData，不用UserData单独自证数值。
- 两图均交付FIG、SVG、PDF、PNG。PNG原metadata为599.9988 dpi；exportgraphics的紧裁剪尺寸与整FIG几何理论尺寸不同，该理论尺寸检查保留NOT_VERIFIED，不改PNG metadata。完整PDF与PNG预览已目视，无明显文字重叠或截字。
- 只读PDF/SVG结构检查通过：0栅格；实际嵌入Times New Roman和宋体；未放宽字体规则。
- 首次只读PDF预览命令因Windows命令行引号传递失败而exit1，原日志保留；修正传参后完整PDF渲染exit0。它不是绘图/物理失败，未删除任何科学证据。

对象、格式、预览与目视收据在E:/hospital_ies_v21_20261023/main02/figure_qa01。PUBLICATION_RECEIPT.json绑定最终八个图文件、代码和本说明；它仅为图形交付回执。

