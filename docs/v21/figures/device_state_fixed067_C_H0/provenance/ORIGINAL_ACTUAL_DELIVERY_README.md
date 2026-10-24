# 固定067 C/H0状态补图：实际交付

本次实际MATLAB命令已成功退出，原生FIG、SVG、PDF和600dpi PNG位于 `../figures01/STATE_fixed067_C_H0.*`。实际命令、原始stdout/stderr、退出码与运行时间保存在相邻 `fixed067_state_figure01_audit`。本次只新增E盘图件和证据；没有编辑D盘研究源码、正常模型、设备容量、负荷、阈值、策略或原固定三图。

实际命令为 `matlab.exe -singleCompThread -batch "run('E:/hospital_ies_v21_20261023/reviews/fixed067_state_figure01/run_state_actual01.m');"`，2026-10-24 09:54:03.092560至09:54:28.342021 UTC，25.2490719999秒，退出码0。外层监测命令28.4915761999秒，退出码0。旧10 RED、10 GREEN数据映射测试仍引用只读准备收据；本轮没有重复运行Python绿测。

独立重开检查实际通过1/1 FIG：3轴、150个patch、4个scatter、19个字体对象；检查真实X/Y/CData和已登记分类。四个明确非发布临时副本分别改变GT坐标、GT RGB、标题文本、空layout.Title字体，全部由其指定断言拒绝。临时副本仅为反例，保存在 `../negative_object_tests01`，不属于科学数据或正式图。

原 `artifact_scan.py` 实际检查通过，issue0；PDF/SVG零栅格，PDF Times New Roman与宋体均有实际嵌入字节；PNG4252×4724像素，pHYs599.9988dpi，18×20cm几何也符合600dpi。完整PDF一页预览及PNG完整预览在 `proofs01`。作者已实际看完整PDF和原始PNG，未发现裁切、缺字或布局重叠。

MATLAB checkcode真实返回11条建议：PRTPT1、ISCL7、MSNU3。它不是“零消息”检查；消息为绘图renderer/性能/过时抑制建议，实际解析和执行完成，未为消除建议修改冻结源码。

监测文件 `../monitor01` 原样保留。它在同一条已记录batch命令中观察到bin/matlab.exe launcher与bin/win64/MATLAB.exe，故保留的 `more_than_one_MATLAB_samples=24` 不能改写为0；两者命令同一脚本，未观察到另一独立任务或native重叠。退出附近两个进程查询出现NoSuchProcess，保留原记录。fresh终端查询实际为零MATLAB进程。资源样本不是连续峰值或内存硬上限。

25个准备成员和7个实际读取的原始CSV/MAT/RESULT/收据已在本轮运行前后逐字节SHA复验一致。全643核心/28原始输入清单在此限定图件任务中 `NOT_REEXECUTED`；来源仍继承 `4dfe62ae105d2696b7782dc36c83ec63c6551e2cded601d6f15c0d4083058add`，没有把继承绑定写成新全量验证。没有重新读raw/sidecar，没有新物理尝试、背景、样本或策略比较。

冻结入口 `../replay_state_local.m` 保留，需未来将code/data按其接口布置到bundle。独立相对MATLAB重放本轮 `NOT_REEXECUTED`。实际重放依据是已经真实执行的绝对入口及其已绑定脚本；原科学发布门 `NOT_RUN`，本图的自定义原生/结构/视觉检查不替代该门。六动作、原P0–P3服务判据与旧结果边界均未改变。

`FIGURE_CAPTION_ACTUAL01.md` 为可以随图使用的实际图注。`ACTUAL_DELIVERY_RECEIPT01.json` 将封存本次可复核文件及原始命令日志；原 `PREPARATION_RECEIPT.json` 不作修改。
