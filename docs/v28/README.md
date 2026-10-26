# Hospital IES V28：可运行研究与复核包

先读`MODEL_REPORT.md`了解医院供能、实际中断与保障限制；`ASSUMPTIONS.md`是资格边界，`MIGRATION.md`是接口/语义迁移，`FINAL_ACCEPTANCE.json`逐项指向证据。此包不是论文图包，也不是全院物理真实性认证。

## 干净目录与依赖

完整解压这个单一ZIP到D/E的新目录，例如`E:\V28_review`，不要在压缩软件预览窗口直接执行。Python建议使用本次实测3.10，安装：

```powershell
cd E:\V28_review
python -m pip install -r project\requirements-v20.txt
```

NumPy/Pandas/SciPy为计算和数据复核依赖；matplotlib/pytest由既有依赖表保留。真正的IES运行还需可用的Gurobi 13.0.2及许可，包不携带许可。原生只读重放不调用求解器或EnergyPlus；不需要原Starter目录、原SQL、旧V27包或网络。新的年度EP观察需外部准确26.2.0-4bd7a1f26f引擎及天气；它已实际完成，本包最小观察足以复核本轮结论，无需用户再运行年度EP。

`execution/RUNTIME_ENVIRONMENT.json`记录本机实际版本。路径示例可替换；输出必须放在包外D/E新目录，禁止覆写原始输入/旧结果。不要设置PYTHONUTF8=1；本机stdout需要时只为命令设置PYTHONIOENCODING=utf-8。

## 一条命令只读重放全部主要账目

```powershell
python -B replay_review.py --root E:\V28_review --output E:\V28_replay_new
```

该命令先自动还原25份XZ保存的完整CSV并核对原字节数/SHA，随后依次复算A六例、B五例、普通热水、四罐/未满足服务和冷定位。保留逐条实际command/cwd/退出码/stdout/stderr及汇总；不会运行新模型、EP或拟合。输出目录必须不存在。首次还原增加约92.2MB，源重放的完整派生表还需额外空间，建议预留1GB。重复执行时用另一个新输出目录；既有还原CSV只核验，绝不覆写不同文件。

`COMPRESSED_NATIVE_ARTIFACTS.json`列每份CSV及XZ的原/现字节数和SHA；`restore_native_tables.py`只用Python标准库。原始11条`trajectory.json.gz`没有再编码。大SQL、深矩阵及重复派生表的本地保留位置见`OMITTED_LOCAL_ARTIFACTS.json`，这些本机绝对路径是历史定位，正常重放不依赖它们。`PACKET_EXCLUSIONS.json`解释本地staging中哪些冗余文件没有装入ZIP，不是丢失的必需载荷。

## 真正运行公开V28入口

```powershell
cd E:\V28_review\project
python -B -m tools.model_review_v28.validation --case smoke_normal --policy C --configuration H0 --output E:\V28_smoke_new
```

这是15分钟正常实际物理运行，默认采用V28新热输入、固定直接电和既有after_cost顺序。公共reset/step签名不变。该调用需要外部Gurobi，不能把进程启动或配置读取代替运行通过；检查输出`RESULT.json`及原生日志。完整注册工况见`project/configs/model_review_v28/cases.json`，主轨迹见`runs/`。A_original_heat仍采用V28电边界，仅保留原热，不能称作V27入口。

本包project是采用版B生产文件，837源码/33输入逐项匹配`execution/FROZEN_SOURCE_V28_B01.json`。A轨迹由冻结A源码生产，其全体hash、差异补丁与原始结果保留；只读A审计检查相应生产身份及被消费算术文件，不将B源码假称A生产版。确需重建A可从`execution/git/V28_A_to_B.patch`审阅并反向应用到一份独立project复制，再按A冻结清单核SHA；不要改本包采用版再混跑。

## 测试与实际证据

```powershell
cd E:\V28_review\project
python -B -m pytest -q
```

本机冻结B实际结果为1278 passed、0 skipped、退出0，原始日志见`steps/full_pytest_final01/`。当次临时SCI_SOURCE_ROOT提供了可选原年度SQL只读测试，另有`execution/ENVIRONMENT_RECEIPT.json`补记实际调用上下文。其他干净机器若无该旧SQL，可选来源测试允许跳过，不应照搬本机1278/0skip为其成绩；公开V28正常入口和本包只读重放不需要旧SQL。

`execution/`保存任务读取、初始审计、注册、源码/输入冻结、真实命令及独立审阅；`steps/`和`batch_logs/`是原命令日志。`ATTEMPT_LEDGER.json`区分主11、公开smoke、前置失败、后端58518/678、年度EP1、只读复算及未执行内容。只读分析中的早期物性/元数据失败不当作成功模型结果。

## 完整性与最终交付记录

`MANIFEST.json`列所有打包成员（自身除外）的相对路径/字节数/SHA。25份CSV以XZ成员出现，原逻辑CSV哈希另在压缩映射中。`FINAL_ACCEPTANCE.json`的模型与软件门槛来自实际证据；归档后CRC、实际大小/整包SHA、独立解压后的全部哈希、正常公开入口和五项复算，记录于最终ZIP同目录的`ARCHIVE_VERIFICATION.json`、`EXTRACTION_VERIFICATION.json`和`post_extract_verification01/`。这些记录在包外，避免自哈希/自归档循环；它们不是需要拼接的第二个交付包。

保留本地研究分支，不推送、不合并main；Git公共目录与历史结果保持原状。原始输入、V27结果和失败证据未删除。
