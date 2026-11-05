# 医院 IES V26 审查与复现包

先读本包根目录 `MODEL_REPORT.md`。它先说明医院如何接续供能及其代价，再解释冷量与温度资格。`ASSUMPTIONS.md`列出新增假设；`FINAL_ACCEPTANCE.json`列真实通过、失败和条件未执行项目；`USER_TASK_20261026.txt`为本轮原始完整任务。

V26在独立研究分支延续真实M25物理核心。四例同背景N/F/D/FD均维持关键工程服务，但故障后的电网/锅炉补偿提高费用。冷侧全环闭合未通过，不能把停源后OR盘管121.58kWh全归有限水库存。温度有限候选及其适用边界见报告。默认 `thermal_coupling=None`；原容量、负荷等级、设备恢复与失效判据不改，无MPC、未来故障信息或学习控制。

## 最短实际入口

使用Python3.10，原依赖入口为 `project/requirements-v20.txt`；本次实际numpy2.2.6、pandas2.3.3、scipy1.15.3、Gurobi13.0.2，完整运行版本随验证记录。IES需要本机有效Gurobi许可，包内不含账号/凭据/许可证。输出目录必须全新。

从解压根进入 `project/`：

```powershell
python -B -m tools.model_correction_v26.validation --case smoke_normal --policy C --configuration H0 --operating-rule current_service_bridge --output E:/hospital_ies_v26_user_smoke01
```

回到解压根，只读复算9例、共81阶段的原生冷边界，无需启动EnergyPlus或优化器：

```powershell
python -B replay_cold_evidence.py --project project --evidence cold --output E:/hospital_ies_v26_user_cold01
```

只读复算全部温度指标、绝对背景、有限16次拟合记录与双重门槛：

```powershell
python -B temperature/replay_temperature_evidence.py --project project --evidence temperature --output E:/hospital_ies_v26_user_temperature01
```

这三条均有本轮独立便携路径的实际运行日志与新输出，见 `verification/`。前一条实际运行公共IES入口；后两条不重新模拟/拟合，也不修改原数据。默认测试从 `project/` 执行 `python -B -m pytest -q`，本轮最终实测次数、结果及原日志见验收文件，不能用本README的存在代替通过证据。

## 内容与版本

- `project/`：完整当前科学源码/测试/配置、原28输入与必要12项小型运行闭包。V26公共入口明确转发M25，独立记录study ID。
- `combined/final02/`：四例最终匹配比较；`runs/`保留全部5条公共实际运行的PROCESS、BASE_PROCESS、轨迹全部原生segments、请求/命令/执行/保护、RESULT及状态。
- `cold/observational04/`：9例新增同轨迹原生观测。`OBSERVED_DETAIL.csv`含原PORT全部物理列及新增字段；重复API_PORT省略不等于删除该边界。`cold/original_ports/`、`temperature/original_native/`保留原17字段参考；一份原夏季normal SQL可复核实际几何与水体积。
- `cold/summary_fixed09/`、各ledger目录：完整有符号/绝对/峰值账本、真实端点重建、连接节点与循环驱动限制。`cold/diagnostic03/`：两个预登记假设、仅3次限定诊断，包含原生阶段追踪、数值派生设置和全部资格。
- `temperature/fit01/`：每次优化开始/终端、参数、64条双口径指标、16条正常绝对指标、完整拟合/冬季轨迹。注册表、真实恢复分解、BDF3/表面机制、失败原假设均保留。原生EP自适应重放不等于自身时钟因果验证。
- `task/`：本轮任务包的独立审阅与参考材料；`patches/`：V25至V26实际diff及修改清单；`verification/steps/`：实际命令、退出码和原始stdout/stderr；源码/输入/component冻结分别绑定实际数据生产版本。

大SQL/ESO/HTML、完整solver矩阵和深层domain缓存原件仍在D/E本机。为严格小于100,000,000 bytes，只省重复/深层复核文件：逐个路径、实际字节SHA、大小与原因见 `verification/LOCAL_LARGE_OMISSIONS.csv`；每例固定按文件名排序取前12份原始domain记录入包，全部科学场景和所有发布原生区间均保留。深层矩阵级审查需要使用所列本机原件或重跑，当前包不冒充全量矩阵档案。

`MANIFEST.json`给包内各成员的真实字节SHA，不含自身；最终ZIP自身SHA/大小在交付回复提供。没有旧全量ZIP嵌套、第二份说明包、旧项目全量嵌套或自哈希链。原V25审查包及原28输入保持原状。
