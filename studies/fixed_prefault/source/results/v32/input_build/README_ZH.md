# V32 独立输入构建

正式产物位于`formal_isolated_01/b01_normal.spec.json`与`b01_grid.spec.json`。它们由冻结原输入/configs的独立副本和root提供的共同t=0证书生成，不读取C/M完成结果。构建器没有构造环境、reset、episode或调用优化器；只调用新planner.validate_spec进行系数/形状校验。

API：

```python
from tools.model_reference_v32.inputs import build_hospital_spec
spec = build_hospital_spec(project_root, branch='grid', initial_snapshot=certificate)
```

证书须显式含initial_stocks（ups/cold_store）、diesel_shaft_budget_kWh、initial_state、provenance（origin或source）及实际execution_contract/operating_execution_contract。非零time_minute拒绝；缺失证书先于任何输入读取拒绝。完整原证书保留在spec.initial_snapshot，不从历史t0补值。原始证书SHA在BUILD_RECEIPT.json中绑定。

输出与旧flat离线spec兼容，额外保留T×4×3完整需求、direct/aux分账、逐区间ledger、原小时/时间戳和外生availability。flat demand只对分级完整请求求和一次。建筑辅助电与站用电分开：锅炉固定/随供汽站用电在设备系数里，GT starter/DG启动和旋转等动态工作参数与未完全建模范围显式登记，由本方计划近似和共同实际执行分别承担。

数据链为原read_data→build_zone_groups→hash验证的aux cache/M22纯allocation→apply_auxiliary_allocation→纯DataFrame namespace上apply_thermal_profile→V28 construct_request。临床区域是建筑总量内守恒分配，不加第二遍区域负荷。源小时为1160/1161/1162，终点1163仅作源支持；全部网格0/15/.../180及原hourly/外生事件边界，无原生分段时刻。

V28供热为原space heat＋ordinary DHW target，target−legacy delivered仅调整P3；原fan热驱动剔除新增DHW并保留原代理，完整热循环泵跟随新总热需求。booster电已含direct参考，不另加；laundry/booster只是源参考，不被当作免费中央备用热。冷需求是原有用负荷，未再乘COP。具体字段/单位见SOURCE_FIELD_MAP.json。

H1 UPS保留40kWh/160kW，供电仅限P0/P1完整建筑E_req（含建筑aux），不含站用电。未安装cold_store仍为0。DG按共同实际contract：legacy_explicit_permission没有旧envelope的保守critical_e上限；emergency_bus才按显式allowed priorities限定。DG预算为轴功等效kWh，未创造天然气预算。

真实生成隔离检查：13份input/config原字节复制，进程级audit hook覆盖823个实际open读事件，outputs/runs/trajectory/RESULT/ACTUAL_PREFIX/solver sidecar/PROCESS读取禁令从导入构建器到完成两spec/纯validate期间均有效，禁读尝试为0。未删除或修改原数据。read_trace、输入SHA、证书SHA和生成spec SHA见formal_isolated_01/BUILD_RECEIPT.json。

两分支demand ledger逐字节相同；3h完整建筑请求：1383.5059150240131 kWh_e、510.2469939558224 kWh_h、492.24366466160814 kWh_c。它们是离线原始需求，不是实际已服务量，也不是新的医院运行结果。

实际验证：首轮缺实现RED为3 failed＋5 errors；证书source/非零t0边界各有真实RED，完整保留。最终10项新增纯构建测试通过（GREEN02.log），没有重跑历史全套。两正式spec已由新planner.validate_spec校验n=12，未求解规划。

初始化求解分层：证书的initialization_solver_calls=[]只说明reset后core日志0项。总初始化MILP次数未单独仪表化，明确为null；这不证明共同初始化零求解，也未重跑初始化。纯输入构建自己的planner solve/医院episode/C-M decision调用均为0。

可重现独立生成命令（output必须新目录；不启动模型/求解器）：

```powershell
python -B build_isolated.py --project D:/hospital_ies_v22_20261025 `
  --initialization E:/hospital_ies_v32_20261027/initialization `
  --output E:/hospital_ies_v32_20261027/input_build/fresh_isolated_output
```

历史C/M结果的需求核对属于后续独立audit，未进入上述生成。此目录的test_tmp_*、pytest_cache及isolated_raw输入副本可按打包去重方式不重复装入，但不得删RED/失败日志、正式spec、ledger、read-trace、初始化及原输入SHA绑定。
