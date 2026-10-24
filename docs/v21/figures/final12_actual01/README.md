# final12_actual01：当前正式 F1/F2 图件与本地数据

根目录八个 FIG/PDF/PNG/SVG **仅来自 figure_qa02/styled_figures02**。它们是原 E 候选的逐字节镜像，已由原MATLAB流程实际生成、重开和对象检查，并由 root 与独立审查者实际重新查看四个完整页。F1左下P1电ENS标题的旧裁切已在当前qa02修复；本地镜像不是 canonical QA authority，不新增科学验收。

| 相对位置 | 角色 |
|---|---|
| F1_critical_service_final_blind_w720.* | 当前六轴关键电/热/冷 ENS |
| F2_service_cost_final_blind_w720.* | 当前四轴一般服务、费用及条件排放 |
| data/ | 原 final12 child/tables 全 CSV/MAT，含原样空表 |
| source/ | 实际运行的三个原MATLAB函数，SHA为 e4d95d…、eec221…、640456… |
| qa/objects02、qa/structure02 | 当前qa02原生对象与格式审查，保留原路径 |
| qa/previews02、qa/boundary_inspection02 | 当前完整PDFproof及已有边界细节 |
| qa/independent_visual02、qa/root_visual_actual02 | 两位审查者对新四完整页的实际记录；root原三联日志同存 |
| qa/FINAL12_PADDING_FULLPAGE_STYLE_RECEIPT.json | 当前实际版式流程收据，输入源路径仍为历史阶段 |
| provenance/stage_receipts/ | 旧native/tick阶段的原字节收据，引用qa01，不是当前八格式权威 |
| provenance/layout_fix01/ | 最小diff、真实RED失败/GREEN通过及其已运行源码和日志 |
| provenance/failed_visual01/ | e28旧qa01裁切失败收据，不覆盖、不伪装通过 |
| qa/expected/EXPECTED.json | 当前native QA使用的同一固定数据期望，虽来源目录叫qa01，但未分发旧裁切图 |

MIRROR_MANIFEST.json 完整记录 canonical source→本地相对路径、bytes和SHA。JSON内绝对E路径不改写；这些原收据中的 source identity/path 不能被解释成对一个尚未执行的本地重绘输出的验收。

## 本地 CSV/MAT 的可执行 MATLAB recipe（本次尚未执行）

以下直接调用 source/ 的三个原函数，不引入新复杂replay函数。需要本机MATLAB R2024b及已安装宋体/Times New Roman；不分发可执行程序、字体、许可证或论文。使用一个**全新E目录**，不得覆盖镜像或原qa02。

~~~matlab
delivery = "D:/hospital_ies_v21_20261023/docs/v21/figures/final12_actual01";
work = "E:/hospital_ies_v21_20261023/replays/final12_local_csv_recipe01";
assert(~isfolder(work), "Choose a fresh E output directory");
mkdir(work);
addpath(fullfile(delivery,"source"),"-begin");
r1 = plot_sci_engineering_publication(fullfile(delivery,"data"),fullfile(work,"native01"));
r2 = style_sci_class_ticks(fullfile(work,"native01"),fullfile(work,"ticks01"));
r3 = style_final12_f1_padding(fullfile(work,"ticks01"),fullfile(work,"final02"));
~~~

这段recipe及本地数据重绘在本次复制中**尚未执行、未声称通过**。重绘后需独立核对函数/输入SHA、原生对象、格式和完整页视觉；路径/UserData及文件字节可随新输出身份改变，不能拿旧E收据为新目录代签。原当前FIG已实际生成和重开，不依赖该尚未执行的recipe才存在。

## 图义与限制

当前是12源块、48情景、240条配对轨迹，H0、720min，季度源块4/3/2/3。F1/F2为源块等权均值，未图示CI；P2/P3是union material分钟，不能与各载能ENS互换。费用为2001 USD，CO2为事后条件估计。b=12不代表12家医院或临床可靠率。

P3一例60min差异对应边际电服务，不是全一般需求新增60min；tiny关键电ENS增加不清零。四个完整页没有独立脚注块，审查记录如实说明。视觉/格式通过不意味着统计显著、临床安全或普遍策略优势。

结果证据镜像见 ../../results/final12_actual01/README.md。只复制小型表、已实际运行源码及证据；无raw/solver sidecar、大型运行复制或冗余analysis group缓存。
