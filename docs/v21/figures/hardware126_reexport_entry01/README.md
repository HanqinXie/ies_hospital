# 正式硬件图的可迁移原生FIG再导出入口

正式22图与完整本地286源成员位于相邻的 `../hardware126_actual01/`。本目录单列入口，避免改动已经封存的300文件出版镜像。函数、合同和作者说明保持原字节。

MATLAB从任意位置调用（将两个路径换为真实目录，输出必须是不存在的D/E工作目录）：

```matlab
entryRoot="D:/hospital_ies_v21_20261023/docs/v21/figures/hardware126_reexport_entry01";
bundleRoot=fullfile(fileparts(entryRoot),"hardware126_actual01");
addpath(entryRoot);
reexport_verified_hardware22_figs(bundleRoot,"E:/hospital_ies_hardware22_reexport01");
```

这是对同字节原生FIG和审计记录的再导出，不是CSV重算、物理重放或新的科学门。原CSV/MAT独立重画命令及其旧布局限制见同目录作者说明。入口已由root全文检查且286个本地数据/图件成员的SHA实际核对；本次MATLAB/checkcode/迁移再导出仍为 **NOT_REEXECUTED**，当前最终物理批次运行期间不启动MATLAB。原native/格式/视觉证据针对已发布正式图，不能被说成新入口已经实际执行。

旧v1缺少copyExact辅助定义，在作者静态自读时发现；v2只补精确复制辅助函数，旧v1保持历史而不列为发布候选。没有物理模型、策略、参数、原科学表或既有收据修改。
