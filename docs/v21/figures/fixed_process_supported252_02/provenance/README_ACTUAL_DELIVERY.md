# 固定三条过程的实际 MATLAB 交付

最终图目录为 `styled_figures03`，完整保留 12 张原生 FIG 与 SVG/PDF/600 dpi PNG。正式固定过程是 `F6_process_1`（原首个 NORMAL C/H0，720 min）、`F6_process_2`（原 067 C/H0，900 min）和 `F6_process_3`（同 067 A/H0，900 min）。另外九张是完整原生表带来的副产物，均保留 E，不替换已封存的九张正式统计图。

三条选择、策略、场景、初始库存和时钟来自原预声明。NORMAL 没有外生故障/冲击/修复，120/480 min 仅为原分析槽；其余两条为 GRID+ALL_GT 复合诊断，故障窗 60–780 min、恢复观察尾段 780–900 min。它们不增加独立背景数，也不是新物理计算。图中 P0+P1 只在同一载能内合并，电、热、冷不相加；它们是聚合服务代理，不是逐临床房间的实测服务。

## 数据与实际执行

`commands02 → 三条原只读导出各一次 → completed_index01 → assemble01` 全部实际 int0。三个有边界的读模式各执行原 7ref `begin/finish`，验证 master256 完整失败披露、supported252 原生/全池物理/当前字节和原 source643/input28。清单诚实写 `complete_native_CASE_count=252`、`original_registered_CASE_count=256`、四构造失败保留，绝不把 252 换称 256 全通过。

`assembled01` 包含完整 native 表字节镜像及三套原生 PROCESS.csv/MAT/原收据。新 PROCESS 不冒称原 native table export 的成员。原 RESULT、raw 与 export receipt 的 SHA 均保留。`ACTUAL_FIXED3_DATA_QA.json` 核验原生区间完整覆盖、CSV 请求/交付/ENS 积分与 CASE 对照及 H0 库存字段，不插值、不补零、不修改任何物理容差。

本次 NORMAL 48 段，DG 轴功等价库存 2400 kWh 恒定、实际 DG 出力未启；微小电 ENS 不是数学零。067 C/A 各 75 段，实际 DG 首次正出力所在区间从 75 min 开始，记录到库存首次为 0 的原生区间末端为 386.6666720726607 min。后者是本案例记录的耗尽端点，不是普遍保障时长或设备就绪时间。H0 没有 UPS 或独立冷储，两个零库存行表示硬件未配置，不表示耗尽。库存只画每段 pre 端点加最后实际 post 端点；功率按原半开区间显示。

## 图形检查与保留失败

原 e4d publication plotter、已封 v2、原 QA03 和 d866 Title/RGB 窄检查均不改。新 12 图 display 仅将已通过九图样式的作用域扩至 9+3，逆变换逐字一致。全部科学 X/Y/Z/CData、轴界和 UserData 在各显示变换前后严格保持；色条仍为原 256 色、原色域/刻度的矢量对象。

首轮完整绘图在严格重开时，三张 F6 因空 `TiledChartLayout.Title` 默认 Helvetica 而真实 int1；数值已匹配。只读诊断确认三个空标题后，新字体副本仅把它们设为 TNR，未删字体断言。自有首个诊断脚本假设 layout.Text 有 Tag 而失败，日志和原脚本保留；第二版将未暴露属性记为 `not_exposed` 后真实成功。

`styled_figures02` 已通过对象和格式，但完整读图发现 boundary 第一轴原图例文字被实际请求虚线穿过，root 也独立确认。新 03 仅将该现有图例改为 northoutside 横排，保留原字符串、PlotChildren、数据、范围、字体和所有断言。02 及其原通过/未过视觉记录保留，不回写为早已通过。

最终 `objects03` 对全部 12 张 FIG 重开并逐项核对 CSV/MAT、原生数据/库存端点、身份、标题、原 RGB 映射和宋体/TNR；`structure03` 对全部格式真实核验：PDF/SVG 无栅格、字体嵌入、PNG 600 dpi。`previews03` 是只读整页 PDF 预览和页面/墨迹边界证据，全部 12 张文字 bbox 无越页。作者已实际查看完整 12 PNG 与 12 PDF 预览；root 也已逐一实际查看三过程的 PNG 与完整 PDF 预览并认可视觉，独立字节记录为 `reviews/fixed3_root_visual01/ROOT_VISUAL_BYTE_BINDINGS.json`（SHA b72fc9496dc39cccbf364e6a6e41e1ac45240adbdbdba1ce3685a7ed58a95c01）。D 的发布由 root 另行 byteexact 完成。页面几何检查不代替肉眼，也不等于临床或科学阶段接受。

## 资源记录

root 明确采用外部单 MATLAB 受监控准入：真实 GetPerformanceInfo 提交余量 ≥6 GiB、物理可用 ≥8 GiB、无 native/gov/另一 MATLAB，singleCompThread。6 GiB 是 4 GiB 任务资源预算加 2 GiB 系统余量，并非已验证峰值上界或模型门槛。用户随后释放内存，首实际准入 fraction=.7227917152；原 85% 失败与新政策选择均保留，不伪称旧门通过。

`monitor01` 至 `monitor05` 保存所有实际生命周期、至少目标 1 s 的系统提交/物理内存、MATLAB PrivateBytes/RSS 采样、实际间隔与查询 unknown。Windows 单个 MATLAB 调用包含 bin 启动器和其 win64 引擎两个 OS PID；`MATLAB_PROCESS_TREE_LIVE01.json` 记录实际父子关系，不把两个 PID 偷换成两次绘图或删掉启动器。采样观测峰值不是连续时间硬上界，也未执行资源硬杀。

## 状态覆盖边界

当前七行 F6 是实际功率/库存图，不是设备状态图。`EXISTING_STATE_COVERAGE01.json` 只读核对既有导出 CSV：DG/母线状态与辅助可用率有原生 segment 时钟；GT 物理状态在 decision/observation 时刻记录，segment 的 GT/HRSG readiness 为空，不能用实际功率补 ready 或前填。EC/锅炉到达数值端口上限也不能等同实际供能/就绪。尚未增加新状态图或新 raw 导出。

## 本地代码与路径

`assembled01` 的原 E 路径及收据保持历史来源；本地相对重画与完整原生科学证据重验是不同能力。`local_replay_bundle01` 提供 byteexact 数据/原收据和相对 MATLAB 源。便携重画入口本身没有另起 MATLAB 验证，明确为 `NOT_RUN`；本次实际执行的是已记录的 canonical E 管线。完整权威复验仍须原已知收据及其完整父链/文件，不能制造替代 receipt。
