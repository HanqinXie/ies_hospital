# Hospital IES V29：同版本机制验收与定版包

先读MODEL_REPORT.md了解医院怎样接续、哪些工况保不住服务及定版范围；ASSUMPTIONS.md保留工程假设与不支持的主张，MECHANISM_COVERAGE.csv列机制/实际证据/版本。复用V28核心，没有新控制算法、MPC、ML、拟合、容量优化或论文图表。

## 干净目录与依赖

将这个单一ZIP完整解压到D/E的新目录，例如E:\V29_review，不在压缩软件预览窗口执行。只读复算需要Python、NumPy和Pandas；本次Python3.10，实际版本见execution/RUNTIME_ENVIRONMENT.json。

```powershell
cd E:\V29_review
python -m pip install -r project\requirements-saved-recheck.txt
python -B replay_review.py --root E:\V29_review --output E:\V29_replay_new
```

该一条命令先自动还原22份完整CSV并核原字节/SHA，然后串行复算新7例、复用V28三例、10个主结果的源质量/本地能耗范围。输出目录必须不存在且位于包外；全部command/cwd/退出码/stdout/stderr写新目录。它不调用模型、Gurobi、EnergyPlus或训练，不需要原Starter、原SQL、旧V28 ZIP或网络。建议预留2GB供还原和派生表。

COMPRESSED_NATIVE_ARTIFACTS.json列原CSV与XZ的字节/SHA；restore_native_tables.py仅用标准库无损还原，不覆盖不同文件、不降低精度。全部10条主trajectory.json.gz按原字节保留。深层系数缓存和大派生表仍在本地，OMITTED_LOCAL_ARTIFACTS.json给原路径/SHA/用途及便携替代；这些历史绝对路径不是正常运行依赖。PACKET_EXCLUSIONS.json列staging原CSV的完整XZ替代，不代表缺数据。

可先检查打包载荷：

```powershell
python -B verify_manifest.py --root E:\V29_review
```

## 真正运行采用版公开入口

实际物理运行需要SciPy、既有依赖和Gurobi13.0.2及可用外部许可，包不携带许可。无须安装EnergyPlus来运行本轮IES。

```powershell
cd E:\V29_review
python -m pip install -r project\requirements-v20.txt
cd project
python -B -m tools.model_review_v29.validation --case smoke_normal --policy C --configuration H0 --output E:\V29_smoke_new
```

这是15分钟真正模型运行，输出RESULT.json状态须completed。所有新注册case显式V28_adopted，强制正式热输入、after_cost、thermal_coupling=null、research_stress_proxy=false；不允许退回A_original_heat。H0无储能、H1原40kWh/160kW UPS、H2原60kWh/120kW蓄冷，--configuration不能偷偷换同一case硬件。完整注册见project/configs/model_review_v29/cases.json，--list可列入口。

公开reset/step与原执行/观测/命令队列接口不变。新增SOURCE_SCOPE.json及RESULT的v29字段仅用于事后评价，不进入控制器。内部旧stage/event_class不是正式L1–L4研究分类；实际类别在v29_mechanism及机制覆盖表。

## 科学版本、实际计算和测试

本轮科学producer8926dfb7a1632d98b71b0692c6e37df9f4a61855，source ec2a2e8cd42470a70e87fdb5f8a8921374f409cb88267d7b45798b32eb6ee72d，inputs f414d21fc55d5a63282435b9bed9a1c8c289a049f24507f953fb86162fb41d15。843源码/36输入逐项绑定execution/FROZEN_SOURCE_V29_01.json；836原科学文件与33旧输入不变，仅pytest.ini追加两份测试、另增质量3输入。后续文档提交不冒充科学生产提交。

runs/为7条新增实际主轨迹。reused/v28/runs/为B_N/D/FD三条原生证据，绑定旧09f92284/source9cb3ea82/input5c6fea46；原CASE/RESULT/BINDINGS不改写。两组分别用对应freeze只读复算，不混版本。条件transfer_300 N/F未运行，不声明柴油耗尽或离网耐久；M仅一条采用版正常路线回归。

```powershell
cd E:\V29_review\project
python -B -m pytest -q
```

本机一次实际默认测试1320 passed、0 skipped、431.20s、退出0，原日志在steps/full_pytest_final01。execution/FULL_DEFAULT_TEST_CONTEXT.json于执行前捕获选定环境：当次临时SCI_SOURCE_ROOT提供可选原源SQL只读检查。干净机器缺该原SQL时某些可选检查可明确skip，不能照抄本机0skip成绩；公开V29入口/便携只读复算不依赖它。不要设置PYTHONUTF8=1，本机需要时仅为命令设置PYTHONIOENCODING=utf-8。

ATTEMPT_LEDGER.json区分主7、复用3、公开验证、条件未执行、51457后台尝试/918未接受，以及RED/登记/冻结预检等非物理失败。全部新主轨迹完成，算术/匹配通过；没有以全零补未知或删除拒绝。模型可以有服务失败：塔关键冷约17.06min、泵关键热60min、网例P3一般服务不足。

## 最终归档与解压后验

MANIFEST.json列全部打包载荷的路径/大小/SHA（自身不递归自哈希）；22份CSV以完整XZ成员呈现，原逻辑SHA另在映射。FINAL_ACCEPTANCE.json逐项给实际模型/软件证据及不支持资格，科学定版不等于全院物理认证。

portable_verification/含归档前从便携project运行公开入口和三项只读重放的真实证据。唯一ZIP创建后再独立解压一次，核全部CRC/大小/SHA、新还原CSV、正常公开入口与三项只读重放。归档后验为避免自哈希循环单列在ZIP同目录的ARCHIVE_VERIFICATION.json、EXTRACTION_VERIFICATION.json、post_extract_verification01/与POST_ARCHIVE_RELEASE_REVIEW.json；它们是完成记录，不是需要手动拼接的另一个包。

保留本地研究分支和Git公共目录，不推送、不合并main、不删除旧结果。后续扩模应由新研究问题触发，不因未认证的水环路、源罐Cp/SQL标志或全部warning未清零自动重开。
