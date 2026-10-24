# 当前源有限备用功能过程图

两条已开放的90 min、C方案资格工况，分别检验H1 UPS及H2蓄冷。共同源为a5d3ed7/source4dfe62ae，原28输入未改。每图六面板展示原生库存端点、实际电源端口、P0/P1电热冷请求与交付、实际放充及冷泵电。没有配对H0，不能据此给出硬件增益或一般保障时长。

实际MATLAB R2024b单线程绘图退出0（39.180 s）；另一次只读打开FIG、直接对原CSV逐条比较14条曲线的X/Y，退出0（17.345 s），未借用FIG的UserData作为外部真值。CSV与MAT误差为零。两PDF为矢量，SVG/PDF无图像对象，字体为宋体/Times New Roman，PNG存储分辨率599.9988 dpi（600 dpi的整数像素单位换算），且两张完整PDF预览已实际目视检查。核验与原始导出、来源和逐文件SHA见本目录JSON收据和qa/；旧2088图保留在相邻历史目录。

数据在data/，四格式图在figures/。在MATLAB中可从本目录重新生成新的输出：

```matlab
base = pwd;
addpath(fullfile(base,'data'));
plot_storage_functional_process(fullfile(base,'data'),fullfile(base,'replot_NEW'));
addpath(base);
qa_storage_objects(fullfile(base,'data'),fullfile(base,'replot_NEW'), ...
                   fullfile(base,'OBJECT_QA_NEW.json'));
```

完整性收据保留当时E盘真实路径；`PUBLICATION_RECEIPT.json`映射到此目录相同字节的相对路径。`qa_storage_artifacts.py`是原E盘布局下执行的只读QA源码与证据，不是上述便携MATLAB绘图入口。原始大轨迹仍在E盘，不会在重绘时读取；绘图只读取已记录的CSV/MAT及PLOT_CASE，未生成新的物理结果。

H1的DG在事件后15 min已CONNECTED，UPS事件后21.0776 min耗尽不代表DG必须等21 min。H2事件后43.0360 min耗尽，恢复尾段输入30 kWh冷、末态库存28.4668 kWh冷；预热损失补偿脉冲与尚未充满状态均原样保留。初始库存对应的付费输入及全过程冷泵电应连同其他正式同窗成本核算，不能只用图中放出量替代。
