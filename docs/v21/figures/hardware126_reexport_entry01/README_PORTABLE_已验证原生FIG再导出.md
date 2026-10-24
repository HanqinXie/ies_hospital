# 已验证原生FIG的可迁移再导出

此入口接受`bundleRoot`和`freshOutputRoot`。当前仅完成静态接口与本地路径核对，**MATLAB、checkcode和迁移后再导出均为NOT_REEXECUTED**；没有新增物理、raw/sidecar读取或重复测试。它不是CSV重算、仿真或科学重放，不重新签发原科学门和原22图视觉接受。

将本目录的`reexport_verified_hardware22_figs.m`、`PORTABLE_INPUT_CONTRACT.json`及本README放在一起，例如发布包根的`portable_reexport/`。`bundleRoot`应为按原PUBLICATION_FILE_SELECTION01 target_relative_path复制的完整286文件根；合同保留原286成员的相对路径、SHA和大小。入口不要求旧E/D项目存在，也没有从旧路径补找文件或raw的回退。

先在同一MATLAB实例的资源门合格时运行：单实例、`-singleCompThread`、物理可用≥8GiB、系统commit≤0.85，沿原任务保留commit余量≥6GiB。使用原MATLAB R2024b及宋体/Times New Roman环境；其他版本或字体组合没有验证。输出应选空间足够的D/E等工作盘，必须不存在且位于bundle外；不要默认写C盘TEMP。

```matlab
bundleRoot="X:/path/hardware126_actual01";
freshOutputRoot="Y:/work/hardware22_fig_reexport_run01";
addpath(fullfile(bundleRoot,'portable_reexport'));
reexport_verified_hardware22_figs(bundleRoot,freshOutputRoot);
```

这里的X/Y只是调用者实际路径的占位；参数本身不会改变合同、源数据或原收据。

入口先核本地286文件、完整F4本地manifest成员及HW18原CSV/MAT和manifest SHA。随后仅在freshOutputRoot/staging中按原字节重组：4个已验证初始F4 FIG、原F4 MATLAB_FIGURE_RECEIPT、同级f4_objects原审计、4个真实PAIRED_VALUES CSV；18个已验证初始HW18 FIG及其原对象审计。全部来源是bundle本地的`provenance/initial_native_figures`、`provenance/matlab01`与`figures/f4_figures`，无旧盘查找。

重组后调用未改923b原style两次，写freshOutputRoot/final的4F4与18HW18四格式。原style仍严格保留原审计SHA、初始FIG SHA、原尺寸、全部原生科学系列、双Y轴范围/RGB/UserData与manifest期望向量检查。合同、原文件、原收据均只读。收据/UserData内原绝对路径保留为历史provenance；入口和style没有跟随这些字段打开旧盘文件。

选择这一模式是因为原style硬绑定原F4审计`af0b83a1…`、HW18回执`080d17f9…`及原FIG SHA。由CSV/MAT重新生成的FIG和回执可能产生新字节，不能伪称旧SHA、修改旧收据或放宽原style去接新结果。新入口只对已封初始FIG作同一页面/图例/shaft显示排版再导出，不宣称重新计算了柱值或原生功率。

若未来确需从本地CSV/MAT独立重画原布局，原接口也完整在包内，可另选全新输出根调用：

```matlab
bundleRoot="X:/path/hardware126_actual01";
rawLayoutRoot="Y:/work/hardware22_original_layout_run01";
assert(~isfolder(rawLayoutRoot));mkdir(rawLayoutRoot);
addpath(fullfile(bundleRoot,'data_and_code','f4_bundle'));
replay_remaining_figures( ...
  "da08cafd99494d49cee2f6c036e3bb83ca7715ed9c05ca05a0b855adaceb3905", ...
  fullfile(rawLayoutRoot,'f4_figures'));
addpath(fullfile(bundleRoot,'code','native'));
plot_hardware_native_process( ...
  fullfile(bundleRoot,'data_and_code','hw18','HW18_ACTUAL_MANIFEST.json'), ...
  "8fa4872d98493c137f4abe4dabe3f1dacc694ff392aefa7632b5c5c24b1875a0", ...
  fullfile(rawLayoutRoot,'hw18_figures'));
```

这两个原接口只复原原布局；原布局曾因页边/图例遮挡而未获版式PASS。独立重画后的新图必须重新做原生对象、格式、完整PDF/PNG视觉核验，不能继承正式final_style01版式等价或原root44页接受，也不能直接交给硬绑旧SHA的style假装同一次结果。此处命令当前同样未执行。

未来执行再导出后仍需对新生成的四格式做字体嵌入、零栅格/600dpi、bbox及每张完整PDF/PNG实际视检。函数运行完成的PORTABLE_FIG_REEXPORT_RECEIPT只记录艺术品再导出，不代签格式、视觉或科学接受。旧E封存包、原selection及400MB归档口径均未改变；本小入口没有建立或宣称任何归档包。
