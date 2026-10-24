# 有限 UPS 与蓄冷：真实功能核验过程图

两图来自已独立通过 pilot35 工程门槛的 H1/H2 各一条 C 方案轨迹。两者均为 90 min（15 min 预热、15–75 min 故障、15 min 尾段），未配对 H0。图用于核验真实接续、有限扣账和恢复路径，不能称为硬件效果比较、投资结论或全医院普遍保障时长。

H1 的复合故障是 GRID 与全部 GT 不可用；H2 是共用冷却塔不可用。物理源/请求/参数没有因绘图改变。两条原始数据由冻结生产 `tools.sci_engineering.figures.export_process` 真实导出，保留 CSV/MAT、raw SHA、原生 interval 与初始/终端库存。

## 读图

- a：只画原生库存初始与各段终端散点；没有将库存端点插值成连续曲线。圆圈标明实际最后端点，虚线是登记容量与实际耗尽端点。
- b：电网、三台 GT 实际电端口之和与 DG 实际电端口。它们是实际发出量，不是容量、命令或“可用”状态。
- c/d/e：P0+P1 分别对电、热、冷的原请求和实际交付，不将不同载能相加。重叠曲线表示当前绘图精度下接近，CSV 保留微小原始短缺。
- f：UPS 电功率或冷库存冷功率，以及冷库存泵的实际电功率；右轴属于冷泵电。H1 冷库存泵真实为零。

灰带仅表示登记故障条件 15–75 min，不表示其后已经完成全部实际恢复。H1 的 DG 在 30 min 已为 CONNECTED、额定转速已到 1.0 pu；30–36.0776 min 的实际电端口保持约 17.332801 kW，UPS 则保持约 108.171648 kW，不能将这段并行供电写成 DG 逐步爬升或仍未就绪。当前末层调度在 P0—P3 服务锁定后择优成本：UPS 放电没有新增边际费用项，DG 电端口按 0.30 USD/kWh 的研究假设计价；UPS 初始库存另行计费，不等于储能免费。H1 在故障结束后实际仍处 ISLAND、没有电网进口，尾段未观察到 UPS 回充；H2 塔恢复后有实际充冷，但未充满。H2 预热段的充冷/GRID/冷泵脉冲来自实际充冷和自放冷损失补偿，全部保留，没有平滑、降采样或新增样点。

30 min 时 DG 的既到达许可上限为 125.504365 kW。从该原生转子状态独立复算，更高电端口在已登记动态域内可达；因此事件后 21.0776 min 只是本条运行下的 UPS 耗尽时刻，不是 DG 必需等待时长或厂家爬升时间。此时 17.332801 kW 的电平衡分量约为 ABS 转换耗电 4.474730 kW 加 P2 聚合建筑电 12.858071 kW，不能全称关键冷转换用电，也不是独立馈线归属计量。UPS 耗尽后首段 DG 实际输出 125.504352 kW；这不构成未运行的 H0 配对收益。

## 复现

将源目录和新输出目录作为参数传入，MATLAB 生成可编辑 FIG、SVG、矢量 PDF 与 600 dpi PNG：

```powershell
& 'C:\Program Files\MATLAB\R2024b\bin\matlab.exe' -batch "addpath('D:\hospital_ies_v21_20261023\docs\v21\figures\storage_functional_validation'); plot_storage_functional_process('D:\hospital_ies_v21_20261023\docs\v21\figures\storage_functional_validation','E:\hospital_ies_v21_20261023\storage_figures_replay_new');"
```

实际最终运行采用 E 源目录与 `figures05` 输出目录，命令和 exit 0 日志在随附 `evidence/MATLAB_RUN05.log`。CSV 与 MAT 实际逐数差 0；重开每个 FIG 后校验 14 条原生数据线 X/Y 完全相同；库存端点为 H1 93 个、H2 106 个；没有 raster/image 对象。字体检查只允许 Times New Roman 与本机实际字族名“宋体”（SimSun），PDF 实际嵌入二者。MATLAB SVG 是无嵌入位图的矢量路径；可编辑文字和线条优先使用 FIG。

绘图函数的数据校验、interval 阶梯展开和库存端点模块可复用。正式硬件研究结束后应对新原始数据重新导出、生成相应工况 metadata，并明确新比较范围；不可把这两张旧功能图改标签当作正式结果。

## 文件与未完成范围

`H1/`、`H2/` 是原生源数据和来源 metadata；`figures/` 是最后视觉修订和 QA 通过的版本；`DELIVERY_MANIFEST.json` 保留绘图原始交付时的文件绑定；其 README 条目对应本次因果解释澄清前版本，图、源数据及绘图 `.m` 的原 SHA 均不变。本次两份文稿的新 SHA、原件和 diff 单列于 `E:/hospital_ies_v21_20261023/reviews/UPS_takeover_mechanism01/`，不把原 manifest 冒称为新文字的绑定。E 盘原 `storage_process01` 保留绘图语法错误、字体预检、初版布局与最终运行日志，未删除任何失败尝试。绘图阶段新增物理尝试为 0，未改生产 source/input/tests 或 source binding。

本功能图不替代正式 H0/H1/H2 共同工况比较，也不包含 H2×CHW 泵失效的投资负对照。蓄冷仍依赖真实 CHW/风机/冷泵路径；该负对照在已登记硬件面板中另行完成。温度与临床阈值验证不在本图结论范围内。
