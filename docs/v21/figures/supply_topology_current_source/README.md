# 当前源供能关系图

本图已在MATLAB R2024b实际生成，并重开FIG核验：74个文本、12条线、8个箭头patch，无image对象；图内标签、输入端口、字体和文本边界通过。PDF/SVG为矢量，PDF实际嵌入宋体及Times New Roman，PNG存储分辨率约600 dpi。root已实际查看PDF预览及PNG，未发现可见截字或重叠。真实命令、退出码、对象与格式收据见 `qa/`；`PUBLICATION_RECEIPT.json`逐文件绑定发布字节。旧 `../supply_topology/` 保留原状。

图的三部分分别表示能量平衡、服务依赖和独立硬件配置。服务模型为P0—P3×电/热/冷聚合，不是逐房间独立馈线或完整水力网络。中央EC/ABS系数已含冷凝泵及塔风机，建筑冷冻水泵另列；供热循环泵为来源数据移用代理。P3可识别非空气侧热量仅是DHW上界，不是实际DHW计量。

H1只增加有限UPS，放电资格为P0/P1建筑电，不能据图宣称冷站独立供电；H2只增加有限蓄冷及额外泵电，不含UPS，仍需建筑冷冻水泵和风机。资格上限与共同平衡不是专用回路证明。图本身不证明保障持续时间、策略优势或医疗环境合规。

从完整交付仓库根目录，在MATLAB运行（输出路径必须全新）：

```matlab
repoRoot = pwd;
figureSource = fullfile(repoRoot,'docs','v21','figures','supply_topology_current_source');
addpath(figureSource);
plot_supply_topology_current_source('E:/hospital_ies_repro/topology_new01',repoRoot);
addpath(fullfile(figureSource,'qa'));
qa_topology_objects('E:/hospital_ies_repro/topology_new01', ...
    'E:/hospital_ies_repro/topology_new01/NATIVE_OBJECT_QA_NEW.json');
```

`source_spec.json`绑定10项真实源码/配置/映射依赖，绘图前后核SHA；`service_dependencies.csv`是7条模型关系的可审源说明，不是新增物理输入。两份reviewed_documents只记录当时已审文档SHA，后续合法报告改写不构成运行依赖。改变真实模型或映射后必须重新审图和更新来源，不可跳过SHA检查。

`qa_topology_artifacts.py`和原命令日志保留本次E工作区的历史核验路径，不声称是便携绘图入口。新目录可直接使用上面的MATLAB绘图及对象核验入口；格式核验/实际目视须对每次新输出重新执行。checkcode保留一条Renderer未来弃用提示，当前实际退出0。
