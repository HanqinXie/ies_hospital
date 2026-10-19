# V19 登记、冻结和运行适配

此层新增 `tools/model_closeout/{runner,data_design,gate,freeze,campaign,baseline}.py`，不修改旧V18工具、原结果或公共reset/step。`RUN`固定为 `outputs/v19_model_closeout_20261018_run01`，模型身份为 `M19_model_closeout`。本次仅运行元数据与合成记录测试，没有启动真实物理轨迹、签正式freeze或选择确认背景。

## 策略和数据契约

主策略键为 `E,K1,C19`；兼容键为 `C18_M19`。E/K1完整沿用原策略参数，动作经新环境路由到M19规划器。C19身份为 `C19_critical_reserve_v19r1`，兼容为 `C18_service_rich_M19compat`；两者继承原C18 coordination，分别增加 `critical_reserve=True/False`。

源码绑定包含原继承注册链和本地src/tools全部生产Python、configs JSON，覆盖动态路由依赖；还显式绑定运行期参数`references/EMISSIONS_FACTOR_REGISTRY.json`，不递归扫描outputs。不执行或导入 `bootstrap_planners.py`，该一次性工具也不算运行绑定。每份manifest另外保留原输入字节清单。不可覆写的plan/manifest/source snapshot、普通命令队列、started/finished/error/unknown账本与原匹配证据字段沿用。analysis保留`v18`兼容分支及V18 CSV名，外层使用V19/M19身份；`v19`新增量由新analysis薄层提供。

内部预登记工程界为700实际尝试、700登记槽、48小时实验墙钟、4策略配置，均不是声称用户要求耗尽的预算。`runner.attempt_accounting()`分别返回登记槽、实际started、terminal、缺terminal及terminal状态分布；失败和未完结started均计入。墙钟从本轮canonical experiments里首个真实started起算，包含其后经过时间，不拿CPU时间替代。测试临时合成账本不进入canonical experiments总账。

最终运行在创建环境前、每个pair开始前重验冻结路径/SHA、签名、源/模型/参数/策略；有未知完整尾段或分析不可用时CLI返回2。未知不补零，也不改写早期失败。签名敏感性只允许`execution_recovery.grid_open_transfer_minutes`和`local_contact_restore_delay_minutes`变化；其他物理参数、model_closeout协调参数不借敏感性通道修改。

小确认使用 `data_design.panel(..., evaluation_role='independent_confirmation', freeze_signature=..., isolation_registration=...)`。每根须恰有L1/L2/L3/L4，各8–12根，不硬编码每类20。隔离证书schema为`hospital_ies.v19.confirmation_isolation.v1`，必须带相同freeze_signature、`historical_training_included=True`、`v19_development_included=True`、`source_inventory_sha256`和`excluded_support`，最后以`digest(unsigned_record)`签名。支持含完整预热/尾段/端点小时，并检查两侧60min与先前排除、根之间相交。该函数验证提供的登记证书，不自行搜寻或选择新背景；根任务必须在冻结后刷新真实历史/V19排除清单。

## 运行入口示例

以下是后续执行方法，本次没有执行其中的基线、正式gate、freeze、登记或物理命令。每次用全新名字，并由`tools/p0_audit/run_logged.py`保存原始stdout/stderr和实际退出码。

```powershell
python -B -m tools.model_closeout.baseline --name initial

python -B tools/p0_audit/run_logged.py --run-dir outputs/v19_model_closeout_20261018_run01 --name affected_gate_final01 -- python -B -m tools.model_closeout.gate --name affected_gate_final01 -- python -B -m pytest -q <本轮所有受影响测试> --basetemp outputs/v19_model_closeout_20261018_run01/test_tmp/affected_gate_final01

python -B -m tools.model_closeout.campaign register --name key01 --design key --policies E,K1,C19,C18_M19 --config M19_MODEL_CONFIG.json

python -B -m tools.model_closeout.freeze --name MODEL_FREEZE --tests affected_gate_final01 --main --model-config M19_MODEL_CONFIG.json

python -B -m tools.model_closeout.campaign register --name main_final01 --design main --policies E,K1,C19 --freeze MODEL_FREEZE.json
python -B -m tools.model_closeout.campaign register --name compat_final01 --design compatibility --policies C18_M19 --freeze MODEL_FREEZE.json

python -B -m tools.model_closeout.campaign run --name main_final01 --workers 1
```

先将M19解析配置写到`M19_MODEL_CONFIG.json`。pytest必须显式使用D盘basetemp，并先创建其父目录：登记路径测试有意保留生产D/E保护，默认C盘tmp_path会被正确拒绝，不应放宽生产gate来迁就测试。gate检查其内部真实子命令返回0，且全部生产源码和测试字节前后未变；外层run_logged证据名应与gate名一致，供freeze读取。freeze还核原保护清单；核对过程中源码或测试变化会拒绝写入。可用`--evaluation-config`绑定明确评价JSON；省略时绑定代码内默认评价契约。两种路径均强制补入真实排放registry SHA、主`CAMX2023r2_DG75pct`、4个case canonical SHA和`is_dispatch_objective=False`，不能由custom评价覆盖。写作说明或此前普通pytest通过不替代源码绑定gate。

`campaign.proposal()`直接读取SOURCE05最终R1外生清单。`main/normal/long/propagation/compatibility`用于最终已见面板；`key/development`允许冻结前针对性开发。关键开发清单包括主001/003/007/043/067及真实转换替代、共塔例，另加long000/005。若只需两长例，可在`key`登记时选`--indices 9,10`。最终正常、长窗和敏感性保留原完整轨迹，不拿主[0,600)替代全部已登记支持。

独立确认只能读取已经冻结后选择的签名proposal：`campaign register --design confirmation --proposal <已登记JSON> ...`，不允许`--indices`破坏完整四类面板。直接`runner register`另支持显式`--freeze`；最终proposal的freeze_signature必须一致。

保存敏感性JSON需含schema=`hospital_ies.v19.assumption_preregistration.v1`、source_freeze_signature、source_sha256、model_id、解析model_config、准确policy_specs、非空reason及signature。再用campaign的`--config`和`--sensitivity-registration`登记原有OPEN或接点变体。

baseline仅核原src/configs/inputs、SOURCE05源码链和冻结、交付ZIP/sidecars与ZIP清单中三份代表raw；不重核全历史raw、不复制全量数据。gate和freeze均不启动物理轨迹。

## 实测验证和限制

`logs/adapters_red02`实际14项因缺少适配而失败。`adapters_red03`发现旧manifest把小确认新背景数压回0；已保留新8根分母。`adapters_red04`验证实际attempt分账缺失；已实现。`adapters_red06`发现baseline核对期间源码漂移仍可签新source；已改为写入前再次核对source/tests。red01的临时目录缺失及red05的测试argv接入错误也保留，不冒充预期业务RED。

`adapters_red07`又实际验证排放runtime参数漏绑、评价case未绑定及development子集角色丢失；已分别修复。最终 `logs/adapters_green05` 实际38 passed、exit0：24项V19适配测试及14项原V18 runner/freeze回归。覆盖真实元数据manifest构造、合成异常与未知尾段、不可覆写、冻结重验、时钟白名单、源码漂移、正常并发拒绝、排放参数及CLI非零。这里没有真实物理或正式SOURCE-bound gate结论。最终差异/逐文件SHA及全部命令见`review/adapters/ADAPTER_CHANGES_FINAL.diff`和`CHANGE_MANIFEST_FINAL.json`。

campaign在单次调用内强制正常24/48/72h仅1 worker，其他最多2；它不是跨进程资源调度器，也不提供RAM硬上限。根任务仍须让正常长窗与其他campaign/日志大内存处理互斥。沿用已有观测峰值和D/E存储规则，不改系统分页或全局TEMP。此层没有新增物理能力或信息假设。
