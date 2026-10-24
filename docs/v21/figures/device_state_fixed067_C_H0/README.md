# 固定067 C/H0：设备状态记录

这一张图补充同一既定长时复合案例的功率与库存图，不新增物理轨迹或独立背景。75个原生执行区间、60个原观测点保持原时钟；DG起端色块和段末点、GT观测点有不同的记录语义，详见FIGURE_CAPTION.md。段内GT/HRSG就绪字段未知，没有从功率补造状态。

实际MATLAB、原生FIG重开、四项明确反例、格式和作者及root完整PNG/PDF视觉均通过。FIG可编辑；SVG/PDF为矢量；PNG约600dpi。原数据与代码均按原字节复制，历史E路径保留为来源，不是本机重画依赖。

MATLAB从本目录的父级或任意位置运行以下代码，第一项替换为此目录的真实绝对路径，最后一项必须是不存在的新D/E目录：

```matlab
bundleRoot = "D:/hospital_ies_v21_20261023/docs/v21/figures/device_state_fixed067_C_H0";
addpath(fullfile(bundleRoot,"code"));
replay_state_local(bundleRoot,"25a8d2097832cc42e3271cf392e57397305a4551e95599c7d2a4308ee52cfce7","E:/hospital_ies_state_replay01");
```

相对数据入口在本出版步骤未另重复执行（NOT_REEXECUTED）；不能把复制、静态路径核对或图件QA称为独立科学重放。全643核心/28原输入复验未在这项限定图件任务重跑；图件来源继承source4dfe。正式科学池为支持252/原256四未知，不能当256全通过。
