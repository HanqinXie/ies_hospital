# 已实际运行的 MATLAB 本地入口检查

本目录补充旧出版镜像 README 中“复制时尚未执行”的历史状态，不改旧收据或图件。

- final12：交付的本地 CSV/MAT 和三个原函数实际重新生成 F1/F2；2026-10-24 UTC 20:24:18–20:24:49，30.7815965 s、exit 0。重新打开后10轴、50个bar系列、200个Y值及X/CData/RGB/轴UserData/范围/刻度/文字/字体/页面尺寸与正式FIG严格相同。figure 的 actual_source 路径按本地数据身份变化，不冒用旧源路径。8个新格式结构检查通过；全部436个原出版文件当前SHA仍一致。新输出没有另做4个完整页视觉审查；正式交付图仍由原qa02的实际完整页记录验收。
- hardware22：本机从本地286成员bundle运行原 reexport_verified_hardware22_figs，UTC 20:09:17–20:13:24，246.4177489 s、exit 0。4+18 FIG重新导出，88新格式、原图至staging链与native snapshot检查通过；原格式扫描器实际两次exit 0。字体验收为宋体/Times白名单与PDF嵌入，未证明每个对象全部字体属性前后相等。22图的新44完整页视觉审查未运行，旧44页记录只属于原正式图件。

首次 final12 验证外围脚本在 collection 的 struct([])赋值失败，exit 1/31.2254081 s；三个绘图阶段已完成，未影响研究模型。原脚本、日志和仅改为cell收集的diff保留。修正版重新执行完整入口后才通过。无物理/LP/新统计或cohort计算。

用户直接重绘继续使用 ../final12_actual01/README.md 的三函数recipe及 ../hardware126_reexport_entry01/README.md 的原入口，选择全新E输出目录。`final12/run_local_recipe02.m`是本次严格原生语义比较用wrapper；`run_local_recipe.m`是失败原证据，不作为推荐入口。当前平台为MATLAB R2024b、已安装宋体和Times New Roman；没有声称其他主机或版本实际通过。

新导出的重复图件保留于E工作树，不重复纳入正式22+2出版图集合；本目录仅装实际小收据、源代码、完整原命令及日志。绝对E路径保留为provenance。
