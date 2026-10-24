# 当前生产代理的独立温度检验图

本图显示原生产1R1C代理与独立EnergyPlus在两个检验日期的差异，**定量温度验证未通过**。生产参数仍为质量参与比例0.5、U=1.5，拟合候选没有部署或绘入。四个日期×baseline/interruption共8次原EnergyPlus运行，其中两日期用于校准、两日期用于检验；不是8个独立医院。共享OR盘管服务四个OR，OR1为代表观测，不能称逐房间独立末端故障。

当前生产温度模块与参数的字节相同，故保留原独立数据；本次未新增EnergyPlus运行。新出版源仅修改函数名、全新输出目录要求和脚注字体。原 `../temperature_validation/` 与原`.m`保留。`source_data/`三份CSV与原始副本字节相同；原生10 min记录不平滑或重采样。图仅显示事件前1 h至后12 h，标注的whole-tail RMSE/max取完整共同尾段。

本次实际MATLAB生成并重开FIG，直接对源CSV核4轴10条曲线、158时间行/790系列X/Y，逐值完全相同；灰带、事件边界、图例和负结果脚注通过。FIG可编辑，PDF/SVG无位图，PDF实际嵌入宋体/TNR，脚注`+2°C`实为TNR；PNG存储分辨率约600 dpi。root已实际查看完整PDF预览和PNG。收据、原始命令与日志见 `qa/`、`PUBLICATION_RECEIPT.json`，通过的是图源/对象/格式核验，不是温度模型通过。

从完整仓库根目录运行MATLAB，输出目录必须不存在：

```matlab
repoRoot = pwd;
figureSource = fullfile(repoRoot,'docs','v21','figures','temperature_validation_current_source');
addpath(figureSource);
plot_temperature_validation_publication('E:/hospital_ies_repro/temperature_new01', ...
    fullfile(figureSource,'source_data'));
```

`qa/qa_temperature_objects.m`与Python格式检查保留本次严格的原D数据/E绘图源known-SHA和真实路径，属于历史核验证据，不声称其硬绑定目录已自动迁移为便携QA入口。上面的绘图函数使用两个显式路径，可读取随交付提供的同字节CSV；对任何新数据/模型，须重新审来源、对象与视觉，不能继承本次旧收据。

夏季4 h检验原代理研究阈值越线迟140 min，whole-tail RMSE2.927°C、最大误差7.361°C。EnergyPlus恢复包含真实追加冷量；代理自然衰减不等于已完成实际恢复。设定点+2°C和恢复至基线±0.2°C仅研究诊断，温度未越线也不抵销冷ENS，不能据本图给出临床环境合规或安全保证。
