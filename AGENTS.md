# Current V26 model correction authorization (2026-10-26)

Follow docs/v26/USER_TASK_20261026.txt and docs/v26/IMPLEMENTATION_PLAN.md. The user authorizes continuous bounded cold-side energy/inventory closure, explicit thermal state/report/air-balance recovery diagnosis and at most one supported finite candidate, plus matched active-path N/F/D/FD. Existing linked worktree continues on sci-v26-model-correction from V25 a016e3119c7730e2c513839bbfe3576d6b3530ab; V25 branch/source/results/failures and original28 inputs remain preserved. New raw/SQL/temp/results only E:/hospital_ies_v26_20261026. One shared EP observer owner, one shared physical-core owner; independent temperature and case modules have explicit ownership. M25 supply core remains the common implementation unless an actual new defect is reproduced. Do not reopen closed DG/UPS/old22 accounting, add MPC/ML/future facts, change capacities/service/precision criteria, make paper/figures, push/main merge, send external messages, or add packaging/watchers. Calibration max16 actual calls/max200nfev, extra isolation max2 hypotheses/4runs, independent dates and experimental coupling gated exactly by task. Deliver one self-contained ZIP strictly under100000000 bytes, root task/README/report/assumptions/acceptance and real evidence inside. Current V26 scope supersedes conflicting older workflow stop points; no routine approval pauses.

# Current V25 model correction authorization (2026-10-26)

Follow docs/v25/USER_TASK_20261026.txt and docs/v25/IMPLEMENTATION_PLAN.md. Continue existing linked worktree from V24 c54f852 on sci-v25-model-correction; original inputs, V24 branch/results/failures remain read-only. V25 supersedes the bounded V24 thermal-stop point for observation correction and finite re-identification, not physical/information/precision boundaries. Shared physics/transfer/storage rule code has one writer. Independent thermal and read-only evidence tasks own separate files. New raw/SQL/temp under E:/hospital_ies_v25_20261026. No MPC/ML/future facts, paper/figures, push/main merge or external messages. Continuous authorized work; no routine approval stops. Deliver one self-contained ZIP under100000000 bytes with README/report/acceptance inside.

# Current V24 engineering completion authorization (2026-10-26)

Follow docs/v24/USER_TASK_20261026.txt and docs/v24/IMPLEMENTATION_PLAN.md. Current user authorization supersedes earlier stop points. Continue existing worktree from local V23 commit732fd6b on sci-v24-engineering-completion; old inputs/results/cohort identities stay read-only. New raw/SQL/temp on E:/hospital_ies_v24_20261026. One shared physical-core writer; thermal and source diagnosis own independent files. No MPC/ML predictor/future facts, paper/figures, capacity/threshold changes to force strategy wins, remote push or main merge. Record real failures and bounded thermal stop. Final compact archive under100000000 bytes.

# Current V23 correctness authorization (2026-10-25)

Follow docs/v23/USER_TASK_20261025.txt and docs/v23/IMPLEMENTATION_PLAN.md. Continue in this existing worktree on sci-v23-model-correctness from local V22 commit08eb3fb; preserve the V22 branch, original inputs and all old results. New raw/SQL/temp on E:/hospital_ies_v23_20261025, model M23_model_correctness. Continuous bounded code repair, high-resolution EnergyPlus inputs, affected reruns and lightweight content-bearing review package are authorized. One shared physical-core writer (root); independent package file ownership is recorded in the plan. No MPC, ML predictor, future information, altered capacities to force success, paper figures, broad strategy search, remote publish or main merge. This current task supersedes conflicting older stop, freeze, one-fit and no-package limitations below; old result freezes do not prohibit new model repair.

# Current V22 model-closure authorization (2026-10-25)

Follow docs/v22/USER_TASK_20261025.txt and docs/v22/IMPLEMENTATION_PLAN.md. The user authorizes continuous bounded model repair and verification; prior V21 final12 is now seen regression. Preserve original inputs, V21 results and history. New model identity M22_model_closure; raw/temp/results on E:/hospital_ies_v22_20261025. One shared physical-core writer. No MPC, ML predictor, future information, paper figures, large audit/packaging workflow or remote publication/main merge. Local research branch commits and necessary targeted reruns are authorized. Current V22 scope overrides conflicting older stage/budget/plotting/release instructions below.

# 医院 IES V20 协作规则

## 本轮 V21 SCI 授权（2026-10-23）

当前主文件为 `docs/v21/USER_TASK_20261023.txt`，执行设计与阶段门槛为
`docs/v21/IMPLEMENTATION_PLAN.md`。用户已授权共同工程模型、五方案、正式独立
工况、边界与有限硬件扩展连续完成；此授权替代下文 V20 仅负荷口径修正的范围。
旧版输入、结果、失败和 Git 历史保持只读；新模型身份 `M21_sci_engineering`。
大型 raw、独立运行与临时目录用 E/D，不使用 C；本轮原始证据根为
`E:/hospital_ies_v21_20261023`。同一物理核心串行修改。禁止 MPC、未来事故信息、
隐藏执行权限或通过改变共同条件制造策略优势。最终十二背景继续封存；任何最终
启用必须有冻结与明确一次性记录。本轮新预算从本轮实际 attempt 开始，不继承
发布 V20 的旧 48 小时墙钟。默认 pytest 保留 V20 支持门槛并增补 V21 回归，
临时目录通过 `--basetemp E:/...` 明确指向本轮新目录。

先读 `docs/v20/IMPLEMENTATION_PLAN.md`、`docs/v20/USER_TASK_20261019.txt`、字段合同和README。用户已授权连续完成修正、验证、固定比较、整理和推送，不沿用旧阶段停止点。

- V19通过 `v19-model-closeout` 标签追溯。原始输入及旧结果不可改写为新结果；新版本建立独立源、输入、测试、模型、策略、配置与冻结。
- 不增加MPC、未来故障/修复信息、物理容量或动作权限；不调整保护/服务阈值、冲击或评价容差制造优势。最终12背景封存；原八背景仅作已见复核。
- 逐字段处理倍率，不统一修改CSV。保留压缩前代理和、超量、比例与P3余量；温度使用代表单区量。
- 故障、命令生成/到达、实际执行、接点、保护及评价分开。保留失败、回退、未知尾段和未开始分母。
- 先复现和有效红绿回归，再最小修复；同一物理核心串行改动。禁止删除断言、扩大容差或用xfail隐藏核心问题。
- 默认 `python -m pytest -q` 的文件范围由 `pytest.ini` 明确，不冒充全部历史研究测试。结论必须有本次实际命令、退出码和原日志。
- 冻结后不得更改被绑定生产/输入/测试继续混跑。修复需新冻结与新批次，不覆写旧attempt。
- 大产物及TEMP/TMP放D/E。正常长窗单策略单进程全局串行；并发扣除在途子进程预留，不关闭用户程序。
- 使用 `scripts/v20_repository.py verify` 校验科学闭包；`python publication/build_v20_release.py --destination <新D/E绝对目录>`按完整白名单发布并补齐冻结后发现的小型回归fixture。独立复现用 `python publication/reproduce_v20.py --name <新名称>`。raw、SQL、论文原件、许可证、凭据和缓存不入Git。
- 替换main使用普通提交，保留地址、历史、标签及访问设置；推送前检查增改删清单，推送后独立克隆并运行核心门槛和完整代表案例。
