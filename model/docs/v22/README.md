# V22 最小运行入口

本版本在 `D:/hospital_ies_v22_20261025`，分支 `sci-v22-model-closure`，继承V21 `298ec4a`。实际修改、结果与未解决边界见 [MODEL_VALIDATION_REPORT.md](MODEL_VALIDATION_REPORT.md)；参数见 [ASSUMPTIONS_AND_SELECTION.md](ASSUMPTIONS_AND_SELECTION.md)，实现迁移见 [MODEL_CHANGES.md](MODEL_CHANGES.md)。

本工作树的Git common目录实际位于 `D:/hospital_ies_v21_20261023/.git`，须保留该目录。旧V21及历史工程/结果未清理，本轮不创建新的全量副本或交付压缩包。

在V22根目录使用原Python环境，逐条执行；每次输出须为新的E盘子目录：

```powershell
python -m tools.model_closure_v22.validation --list
python -m tools.model_closure_v22.validation --case normal_winter --output E:/hospital_ies_v22_20261025/runs/reproduce_normal_winter_01
python -m tools.model_closure_v22.validation --case boundary_067_600 --output E:/hospital_ies_v22_20261025/runs/reproduce_067_C_01
python -m tools.model_closure_v22.validation --case ups_original_90 --output E:/hospital_ies_v22_20261025/runs/reproduce_ups90_01
python -m tools.model_closure_v22.validation --case cold_partial_tower --output E:/hospital_ies_v22_20261025/runs/reproduce_partial_tower_H2_01
```

五方案覆盖使用同一个case、同一个硬件：

```powershell
python -m tools.model_closure_v22.validation --case boundary_067_600 --policy A --configuration H0 --output E:/hospital_ies_v22_20261025/runs/reproduce_067_A_01
```

`--policy`支持R/M/F/C/A，读取实际完整策略配置；F=1h、A=240min。`--configuration`只支持已登记H0/H1/H2；原默认短DG案例本身引用专门登记的10s设备配置。不能通过命令行替换任意设备容量。

精确冬季及有限研究点使用完整recipe：

```powershell
python -m tools.model_closure_v22.validation --case-file configs/model_closure_v22/recipes/winter_compound.json --configuration H1 --output E:/hospital_ies_v22_20261025/runs/reproduce_winter_H1_01
python -m tools.model_closure_v22.validation --case-file configs/model_closure_v22/recipes/aux_fault_0p75.json --output E:/hospital_ies_v22_20261025/runs/reproduce_aux_075_01
```

目录中21个recipe覆盖冬季H0/H1、二次停电、站AC/DC、辅助电合法分摊、锅炉辅助电范围、机制四格与已见L2/L3/L4。它们均保持原负荷与设备容量；每个输入有原来源及构造身份。旧提案补齐导出元数据时逐原场景字段保持不变，原字节与失败前缀均保留在E盘记录。

旧源对照须保留原工作树，给同一个兼容case增加：

```powershell
python -m tools.model_closure_v22.validation --case boundary_067_600 --legacy-root D:/hospital_ies_v21_20261023 --output E:/hospital_ies_v22_20261025/runs/reproduce_legacy_067_01
```

带新分段事件或附加M22配置的case会明确拒绝在旧源运行。原V21工作树、输入、旧结果不改写。

集中检查沿原入口，临时数据放E盘：

```powershell
$env:TEMP = 'E:/hospital_ies_v22_20261025/reproduce_tests/temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
python -m pytest -q --basetemp E:/hospital_ies_v22_20261025/reproduce_tests/pytest01
```

实际温度精度回放和供冷恢复能量是两项不同验证：

```powershell
python -m tools.model_closure_v22.recovery_validation --output E:/hospital_ies_v22_20261025/reproduce_recovery_energy01
```

上述3区间恢复例使用原OR1参数并对实际冷源、风机、泵、电和热库存闭账；它不证明温度精度通过。旧SQL净显热回放的命令、一次fit及16项未通过目标见E盘 `temperature01` 与正式报告，不重复拟合以改变验收结论。

每个实际case目录保留 `CASE/RESOLVED_CASE/INVOCATION/RESULT/STATUS.json`、原stdout/stderr、`trajectory.json.gz`与原生 `PROCESS.csv`。完整结果、失败前缀、未启动排队项分别记录，未知不补零。

只读导出显式结果：

```powershell
python -m tools.model_closure_v22.analysis --runs E:/hospital_ies_v22_20261025/runs/reproduce_067_C_01 E:/hospital_ies_v22_20261025/runs/reproduce_legacy_067_01 --output E:/hospital_ies_v22_20261025/reproduce_comparison01 --pair E:/hospital_ies_v22_20261025/runs/reproduce_legacy_067_01 E:/hospital_ies_v22_20261025/runs/reproduce_067_C_01
```

导出RUNS/SERVICE/STORAGE/PAIR_DIFF及一份输入SHA收据。差值为after−before，保留原始小量；工程等价选择使用独立 `equivalence.py`，不会把存在关键取舍的方案强行排成唯一赢家。

本轮冻结后为补足实际未绑定的覆盖，仅另登记三份E盘recipe、四条实际轨迹：同一t=0全冷机退出的满/空初库存一对，以及同一300min GRID停电的C/A一对。原42例不改名、不覆盖；它们不是新的独立背景。源数据副本在 `data/cases/`，复现前复制到入口允许的目录（已有同名文件先核对SHA，勿改动原证据）：

```powershell
New-Item -ItemType Directory -Path E:/hospital_ies_v22_20261025/recipes -Force | Out-Null
Copy-Item -LiteralPath docs/v22/data/cases/cold_bank_t0_full.json -Destination E:/hospital_ies_v22_20261025/recipes/cold_bank_t0_full.json
Copy-Item -LiteralPath docs/v22/data/cases/cold_bank_t0_empty.json -Destination E:/hospital_ies_v22_20261025/recipes/cold_bank_t0_empty.json
Copy-Item -LiteralPath docs/v22/data/cases/winter_grid_long_release.json -Destination E:/hospital_ies_v22_20261025/recipes/winter_grid_long_release.json
python -m tools.model_closure_v22.validation --case-file E:/hospital_ies_v22_20261025/recipes/cold_bank_t0_empty.json --output E:/hospital_ies_v22_20261025/runs/reproduce_true_empty_01
python -m tools.model_closure_v22.validation --case-file E:/hospital_ies_v22_20261025/recipes/winter_grid_long_release.json --policy A --configuration H1 --output E:/hospital_ies_v22_20261025/runs/reproduce_release_A_01
```

`MODEL_VALIDATION_REPORT.md`按实际交付电/热/冷解释收益、恢复代价及失败边界。`data/`保留全精度小表、显式源文件清单和收据；完整原始轨迹及失败日志留在E盘。原始冷中断的数值微脉冲次数/最长值不能直接解释为有工程意义的多次恢复，说明见 `data/COLD_PULSE_EXPLANATION.md`。本轮温度精度0/16通过，不把代码回归通过当作温度验收。
