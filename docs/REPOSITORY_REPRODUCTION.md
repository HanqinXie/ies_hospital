# V19 仓库复现范围

本仓库是 M19_model_closeout 的白名单发布，不是原研究工作区的全量镜像。`publication/ORIGINAL_FILE_SELECTION.json` 逐项记录原始路径、字节数、SHA256及选择原因。原工程及其封存结果没有被整理脚本改写。

## 两种检查

1. **仓库闭包检查与针对性测试**：克隆后即可读取已提交的负荷、源码、历史小依赖和数值域。测试需要实际求解器环境；包含 Gurobi 数值回归，须自行安装并配置适用许可。`requirements.txt` 保持原字节，`publication/TESTED_ENVIRONMENT.json` 记录此次实际安装版本，不保证所有依赖版本均等价。
2. **完整冻结科学复现**：除以上条件，还必须补齐 `publication/EXTERNAL_ASSETS.json` 中14份外部原文件（11篇文献PDF及3份来源PDF/XLSX），且全部原66项注册输入的路径、字节、SHA一致。缺失这些外部原件不妨碍第一种检查，但严格原冻结入口会拒绝运行。重新下载的PDF版本也可能具有不同SHA，不能用改清单或跳过校验代替原字节。

```powershell
python -m pip install -r requirements.txt
python -m pip install gurobipy
python scripts/repository_assets.py verify
python scripts/test_v19.py
```

此次验证使用的依赖版本见实际记录；以上安装命令不安装 Gurobi 许可。测试默认把原始日志、命令和退出码写入本仓库 `publication_checks/<UTC>/`，该目录不提交。可用 `--output-root D:\hospital_v19_checks_01` 指定新的 D/E 目录。

## 补齐外部原件

外部资料文件名和原SHA见外部清单；原研究依据及来源见 `docs/v19/PAPER_MODEL_SPECIFICATION.md`、`inputs/reference/` 和排放参数注册表。仓库不自动下载或公开转发文献。已具有相应原文件时，用包含原相对目录的本地根目录恢复：

```powershell
python scripts/repository_assets.py restore --source-root "D:\path\to\local_original_project"
python scripts/repository_assets.py verify --require-full
```

脚本先核对全部输入，再复制缺失项；遇到已有不同文件会停止，不覆盖。补齐的 `inputs/literature/` 及3个指定来源文件始终被 Git 忽略。普通 `verify` 会明确列出尚缺外部文件，同时验证已发布闭包；只有 `--require-full` 要求原66项全部存在。

## 严格长窗口复现

补齐输入、求解器和许可后，从新的 D/E 克隆目录执行：

```powershell
python scripts/repository_assets.py verify --require-full
python outputs/v19_model_closeout_20261018_run01/scripts/portable_reproduce.py reproduce
```

此入口只支持事先登记的 long000/E 全1065分钟案例，先创建小型 `SCIENCE_ROOT` 独立源码/输入副本，再实际运行一次轨迹并按原容差比较92项指标。它不会重新计算406条最终轨迹，亦不重建所有未提交的原始轨迹。首次运行后保留所有日志与失败证据；第二次复现应使用新的克隆目录，不能删除结果假装首次运行。原记录中的绝对 D 盘路径只用于历史来源标识；上述入口使用当前仓库相对路径。

## 历史小依赖为何保留

V11 standby manifest、V13策略定义、两份V15来源收据会被继承代码惰性读取；V17数值域/逐层fixture、V18设计与冻结文件用于数学回归和登记来源。V43 authority记录封存的支持边界。不能删除这些目录后仍宣称与原冻结一致。

原345项生产字节、全部235项测试字节与最终冻结逐项一致；新文档和 `scripts/` 中的发布辅助脚本不参与该生产绑定。`.gitattributes` 禁止Git换行转换，避免签名字节被工作树设置改写。`.gitignore` 对 `outputs/` 采用逐文件允许清单；未来运行的大输出不会自动进入提交。

## 结果与历史文档

`docs/v19/V19_MODEL_CLOSEOUT_REPORT.md` 和 `paper_results/FINAL02` 是原研究结果；`publication/` 中的发布验证记录才描述本次仓库检查。旧阶段文档（例如 ADAPTER_MIGRATION）按其当时阶段解读，不能据其尚未执行的状态推断最终结果，也不能把最终历史结果当成本次新跑结果。

完整尝试分母保留于 `review/final_attempt_census02/`；研究有失败、未开始及历史未知过程，不能只根据406条最终完成记录声称整个活动全部成功。大规模 raw、旧阶段便携副本、训练数据、缓存和压缩包未上传。

## 使用与来源

公开可访问不等于所有材料均采用同一开源许可。本次未擅自为原代码或第三方数据添加统一许可证。设备、DOE/PNNL原型、天气、文献和公开来源各按其原来源条件使用；仓库内来源记录不构成临床有效性、OEM认证或现场运行许可。
