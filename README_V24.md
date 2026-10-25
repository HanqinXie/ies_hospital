# 医院 IES V24：工程模型与最小复现

先读 [模型报告](docs/v24/MODEL_COMPLETION_REPORT.md)、[假设与限制](docs/v24/ASSUMPTIONS.md)及[交付验收记录](docs/v24/DELIVERY_VERIFICATION.md)。本轮不包含论文、图表、MPC或新学习策略。默认供能、既有有限源传播与探索性温度分别验收；温度定量恢复仍未合格。

工作版本：`sci-v24-engineering-completion`，基于V23 `732fd6bd1bdaae754cce930e1084fe2d849d0771`。未push、未合并main。每项计算的实际源码SHA和输入SHA以`RESULT.json`及随包`provenance/FINAL_COHORT.json`为准，不用Git标题替代实际绑定。

## 环境与最小实际供能运行

实际环境为Windows、Python3.10.6、numpy2.2.6、pandas2.3.3、scipy1.15.3、Gurobi13.0.2；详见`provenance/RUNTIME_ENVIRONMENT.json`。需要可用的外部Gurobi安装与许可，包内不含引擎许可或凭据。温度只读重放不需要Gurobi/EnergyPlus；重新执行4份派生IDF需另有EnergyPlus26.2。

以下从解压后的`project`运行。每个输出目录必须尚不存在；大型输出放D/E盘，不放C盘。环境安装按已有环境需要执行，不修改原始数据。

```powershell
Set-Location -LiteralPath '实际解压目录\project'
python -m pip install -r requirements-v20.txt
python -B -m tools.engineering_completion_v24.validation --list
python -B -m tools.engineering_completion_v24.validation --case smoke_normal --policy C --configuration H0 --output E:/v24_replay_smoke_new
```

`smoke_normal`是注册15min短例。执行后应有`STATUS.json`、`RESULT.json`、原生`ACTUAL_PREFIX.jsonl`、原始`PROCESS.csv`及RAW/求解证据；只有status=completed、unknown_tail_minutes=0且source_and_inputs_unchanged=true才是完整实际运行。stdout出现命令结束不代替这些检查。

## 蓄冷修复与完整过程

```powershell
python -B -m pytest -q tests/test_v24_cold_modes.py tests/test_v24_cold_admission.py
python -B -m tools.engineering_completion_v24.validation --case fan_recharge_repeat --policy C --configuration H2_empty --output E:/v24_replay_fan_new
python -B -m tools.engineering_completion_v24.validation --case l2_long_150 --policy C --configuration H0 --output E:/v24_replay_L2_new
```

完整回归入口为`python -B -m pytest -q --basetemp E:/v24_pytest_new`。具体修复回归文件名以`tests/`与交付实际命令为准。小反例的可移植脚本在`review/cold_core/reproduce_cold_core.py`，按其README指定`--root`与全新`--output`；它会实际调用求解器。正常期费用循环问题及Source02新增回归另保留独立证据，不删Source01失败。

## 随包结果与版本边界

以下`review/`、`runs/`、`entry_smoke01/`、`provenance/`和`execution/`均相对于解压根，是`project/`的同级目录。

- `review/metrics/`：最终26项RUNS/RECOVERY/SERVICE/STORAGE/COST、4个同源策略配对与9个资源配对；首批26项另标历史，不混入最终蓄冷数字。
- `runs/`：首批Source01全部26、修后Source02蓄冷12、同事故legacy M23反事实；封包前短例另在`entry_smoke01/`。`PROCESS.csv`为原始全文件，未以COMPACT替代；`ACTUAL_PREFIX.jsonl`保持原行次和值。
- `review/sources_and_propagation/`：来源、塔单位、有限源真实传播与原行、Source01健康循环独立审查及Source02修复/无冷库复用依据。
- `review/temperature/`：既有原生校准数据、8次实际fit失败、4次原日期节点补输出、冻结参数及可移植只读重放；其README给出精确命令，不重复拟合，不生成新日期。
- `provenance/`：两版实际科学源码冻结、14旧无冷库+12新蓄冷的最终cohort、精确Source01运行ZIP、V23基线完整补丁与重建验证、运行环境。
- `execution/`：真实命令、退出码、stdout/stderr、批次记录；RED、中间失败、回退/未知保留。

大RAW/SQL/ESO/CAS按SHA和原路径记录于省略清单，仍保留在本机；不是删除科学场景来压缩。包内完整PROCESS/原生段和小反例RAW足以审查关键过程，但需要完整求解CAS或全部建筑SQL的额外问题应按清单取本地文件。旧JSON里的D/E绝对路径是历史来源，不是搬移后的运行依赖；从当前project入口复现使用当前目录的输入。

包外`.receipt.json`记录最终严格小于100,000,000字节、ZIP CRC/成员SHA核验；解压后的实际新短例与只读热重放另有验收记录。旧V23包和所有历史结果原样保留。
