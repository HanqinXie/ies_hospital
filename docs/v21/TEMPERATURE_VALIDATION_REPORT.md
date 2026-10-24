# 独立受限供冷温度核验：结果与适用边界

本轮实际完成四组 EnergyPlus 配对计算，其中两组用于离线校准、两组保持检验身份。结果表明：原 1R1C 温度代理不能可靠支持“何时超过设定点 +2°C”或恢复时间的定量保障结论。独立夏季 4 h 停冷检验中，实际首次超过研究判据为 12:20，原代理为 14:40，迟 140 min；故冷量未满足必须单独报告，不能由代理温度未越限抵销。

## 干预、来源与独立性

- 可执行文件实际版本：`EnergyPlus, Version 26.2.0-4bd7a1f26f`，本机 `C:\EnergyPlusV26-2-0\energyplus.exe`。
- 只读源：`Hospital_SanDiego_Starter/modified_idf/Hospital_SanDiego_262_engineering_audit.idf`、原 `Hospital_SanDiego.idf` 和 Brown Field TMY3 EPW；原文件 SHA 在 `temperature_validation/PROTOCOL.json` 中。结束时再次核对，源文件未改变。
- 每例保留事件前一日和事件后两日，另由 EnergyPlus 执行自身暖机；原风机、其他冷盘管、区域设备/照明、围护结构、温度设定及天气均保留。仅替换该派生 IDF 的 RunPeriod，并改变 `VAV_OR Cooling Coil` 的可用日程。该真实共享盘管服务四个 OR 区域，选 `OR1_FLR_2` 作为代表观测；不是独立切断某个区域末端的试验。
- 缺冷期间保留风机和通风，关闭的是水冷盘管，不能把这种干预说成所有送入空气冷量恒等于零。实际盘管供冷在四个登记事件窗均为零，逐 10 min 输出核验。
- 6 月 15 日/2 h 与 9 月 15 日/4 h 为校准；1 月 15 日/2 h 与 8 月 15 日/4 h 为检验；事件均按原 IDF 日程的当地时间 12:00 开始。保留原夏令时，读取 SQL 的 `Hour + Minute + Dst` 与日程对齐。输出中的 2006 年仅为统一坐标，天气是 TMY，不能称为该日实测天气。
- 配对基线也是本轮实际运行，不使用旧年度温度替代短窗基线。四个日期、同一 DOE 原型和同一 TMY，只支持案例内的日期检验，不支持跨医院、跨气象年的外部验证。

## 主要结果

下表使用未改变的生产代理参数：家具质量参与比例 0.5、有效 U=1.5 W/(m²·K)，OR1 的 C=1.597270355 kWh/K、UA=0.0836082281 kW/K。RMSE 统计事件发生后至共同恢复尾段结束；另外保留事件期和恢复前 6 h 的分开误差，见完整 CSV。

| 角色/日期/停冷 | 全尾段 RMSE °C | 最大绝对误差 °C | 独立峰值温升 °C | 原代理峰值温升 °C | 独立/代理首次超过设定点+2°C |
|---|---:|---:|---:|---:|---|
| 校准 6/15，2 h | 1.618 | 4.846 | 7.018 | 3.816 | 12:20 / 窗内未越限 |
| 校准 9/15，4 h | 2.717 | 6.857 | 6.671 | 7.036 | 12:30 / 14:50 |
| 检验 1/15，2 h | 0.535 | 2.268 | 3.295 | 1.027 | 两者均未越限 |
| 检验 8/15，4 h | 2.927 | 7.361 | 9.314 | 7.550 | 12:20 / 14:40 |

原代理在独立夏季检验的事件期 RMSE 为 4.627°C，恢复前 6 h RMSE 为 5.782°C。独立过程恢复至基线温度 ±0.2°C 并保持三个连续样本需要 20 min，原代理在恢复尾段内未达到该研究恢复条件；冬季对应 30 min 与 1880 min。这里的 ±0.2°C 只是误差与恢复诊断条件。

真实 EnergyPlus 恢复过程提供了额外冷量，而原代理在供冷重新满足后只让温度增量自然衰减。独立冬季与夏季检验 OR1 的恢复附加显热冷量分别为 1.879 和 12.517 kWh；这些冷量实际进入 EnergyPlus 盘管与区域过程，并非免费恢复。生产代理没有这一追加恢复冷量模型，不能把其恢复曲线当成已验证的真实运行过程。

仅用两校准例，在预先列出的质量参与比例 `{0.1,0.25,0.5,0.75,1.0}` 与有效 U `{0.5,1,1.5,3,5}` 上筛选。按两例事件后全尾段平均 RMSE 选出 0.1 和 5；候选逐项误差完整保存。该离线拟合在独立夏季检验的 RMSE 降至 1.239°C，但最大误差反而为 8.915°C，阈值仍迟 30 min，恢复诊断为 340 min。平均误差改善不足以证明阈值或恢复过程可用，因此没有把拟合值写入共同核心。

## 实际执行、失败与复核

首次基线 pilot 实际耗时 5.10 s，峰值进程树 RSS 313.2 MB；随后八个配对运行实际合计 37.58 s，峰值 315.94 MB，八次均退出 0、0 severe、0 fatal。另有一次实现阶段尝试在完成一个基线后因 IDF 对象替换误把 Branch 内的组件引用当对象而停止；失败证据保留，增加反例后改为按真正对象起点判断。

分析自检还检出了细粒度 SQL 时钟误用：旧分析使用 Hour−1 并遗漏原 IDF 夏令时，导致事件窗错位；增加回归后仅重做受影响的解析与离线拟合。原八次 EnergyPlus 运行未重复计数，原错误分析也未覆盖。最终只引用 `analysis04_final`，不引用 `study02` 中的旧结果表。

- 原始有效八运行：`E:\hospital_ies_v21_20261023\temperature_validation\study02`，含派生 IDF、SQL、ERR、stdout 与逐运行实际命令/退出码。
- 最终分析：`E:\hospital_ies_v21_20261023\temperature_validation\analysis04_final`；本目录下紧凑副本为 `temperature_validation/`。
- 红绿原日志：`E:\hospital_ies_v21_20261023\temperature_validation\evidence`。当前四项针对性回归实际通过；它们覆盖仅改目标对象、保留恢复热状态、SQL/DST 时钟以及“事件窗目标盘管仍供冷即拒绝验收”。
- 这些检验没有现场实测校准、临床温度阈值认证、湿度、压差、感染控制或跨医院验证。生产结果中的温度仅作探索性辅助分析。

## MATLAB 独立验证图

最终图为 `figures/temperature_validation/temperature_validation.fig`，同目录提供 SVG、PDF、PNG、实际绘图样本和逐字节绑定的源 CSV；脚本为 `figures/plot_temperature_validation.m`。真实 MATLAB R2024b 执行与 FIG 重开均退出 0，FIG 含 4 个轴、10 条曲线、4 个区间色块、0 张位图，SVG 也不含嵌入位图，PNG 已目视检查。命令、输出 SHA 和对象 QA 见该目录 `MATLAB_VERIFICATION_RECEIPT.json`。

图使用已修正时钟的 `analysis03_clock_corrected` 两个独立季节原生 10 min CSV，与 `analysis04_final` 所用温度曲线相同；后者增加恢复分阶段指标。没有使用 `study02` 中旧的错误分析曲线。图明确标记定量验证未通过，+2°C 仅为研究判据，不作为临床安全阈值。

## 复现

在仓库根目录，设置当前进程 `PYTHONPATH=src`，执行：

```powershell
python -m tools.sci_engineering.temperature_validation --root D:\hospital_ies_v21_20261023 --source "D:\云大文件\论文\医院ies\Hospital_SanDiego_Starter" --energyplus C:\EnergyPlusV26-2-0\energyplus.exe --destination E:\hospital_ies_v21_20261023\temperature_validation\new_full_run
```

输出目录必须全新。仅重做解析可另指定新的输出目录并增加 `--replay-from E:\hospital_ies_v21_20261023\temperature_validation\study02`；此时状态表显式记为重用原 SQL，不声称新增物理计算。原 SQL 与论文原件留本地，不进入 Git。
