# 医院综合能源系统：调度、故障传播与恢复研究

本仓库发布截至 **2026-11-06** 已核对的研究源码快照、关键参数、必要输入和精选证据。当前主线采用考虑执行状态及有限资源的词典序多目标调度，并通过事件驱动的电—热—冷耦合动态模型计算故障影响和恢复。

**这是带有明确限制的研究源码发布，不是新的仿真结果，也不是已通过全部测试的服务器安装包。** 本次没有改变科学模型、参数、负荷、容量、判据或策略，没有运行新医院轨迹、年度实验或训练。

## 从哪里开始

| 目录 | 内容与用途 |
|---|---|
| [model/](model/) | 年度主线完整受版本控制的源码快照；基于 `b4d1ed20dc37e6add516a20a96979c2e29efdd36`，包含必要输入、配置、测试和保留夹具 |
| [studies/fixed_prefault/source/](studies/fixed_prefault/source/) | 本地最新冻结故障前支路比例 U 对照的完整工作文件快照；基于 `468f5fe`，包含明确登记的未提交修改，不冒充该提交原貌 |
| [annual/](annual/) | 正常年和扰动年启动脚本、案例、日历及原始来源清单；作为交接参考，完整运行仍需原交接包 |
| [docs/MODEL_ZH.md](docs/MODEL_ZH.md) | 调度算法、实际执行、故障传播与动态计算说明 |
| [docs/KEY_PARAMETERS.csv](docs/KEY_PARAMETERS.csv) | 从实际配置提取的关键参数及来源，原值保留 |
| [docs/PARAMETERS_ZH.md](docs/PARAMETERS_ZH.md) | 参数单位、研究假设及启用状态解释 |
| [docs/REPRODUCIBILITY_ZH.md](docs/REPRODUCIBILITY_ZH.md) | 获取、核验与复现范围 |
| [docs/VALIDATION_AND_LIMITATIONS.md](docs/VALIDATION_AND_LIMITATIONS.md) | 实际测试证据、失败与未执行项目 |
| [release/SOURCE_MANIFEST.json](release/SOURCE_MANIFEST.json) | 两条模型来源、工作树状态和逐文件 SHA256 |

两个源码快照独立保留，不能把其中的文件随意混用。`model/README.md`、源码内旧 `release/` 和 `results/` 属于其原有历史记录，不代表本次远端存在相应旧 release、标签或新版本全部通过。当前发布说明以本页及 `docs/` 为准。

## 当前方法

- 词典序逐层优化与显式目标锁：保护实际可执行关键服务，再按所选 M/C 策略处理其他服务、准备与费用。
- 线性部分为混合整数规划；启用 SF2 燃机非线性燃料关系的路径包含混合整数非线性求解。后端使用 Gurobi。
- 每15分钟生成上层命令，普通命令队列、延迟、启动、爬坡、连接和库存限制决定实际执行；最长1分钟物理段遇事件继续细分。
- 原始8760小时负荷保持原小时边界，不生成虚构的分钟负荷。状态、库存、燃料、队列及恢复过程连续演化。
- 故障影响通过能源与共同辅机依赖传播；功能受限或服务失效不自动等于继发设备损坏。
- 当前年度主流程没有 MPC、在线未来事故信息或机器学习调度。历史 RF、GRU、行为克隆代码保留为研究历史。

## 只读发布核验

```powershell
git clone https://github.com/HanqinXie/ies_hospital.git
cd ies_hospital
python -B release/verify_publication.py
```

核验文件完整性、快照字节和 Python 语法，不调用优化器、训练或医院仿真。科学默认测试及服务器准入范围见 [验证说明](docs/VALIDATION_AND_LIMITATIONS.md)。

## 本次没有上传的内容

大体积原始轨迹、重复便携副本、缓存、临时数据、Git数据库、模型权重、许可证和凭据不入库。完整历史夹具及核账域存储仍保留在原本地交接包；本次不会伪造这些依赖或将缺失结果填零。没有为第三方数据或材料擅自添加许可授权。
