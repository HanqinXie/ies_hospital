# V32 最终独立静态审查

`STATIC_REFERENCE_REVIEW.json` 的 35 项检查全部通过，未发现冻结运行的阻断问题。审查只读取源文件、登记、五个参照文件和 6 条运行的小型 CASE/RESULT/STATUS/INVOCATION；未运行医院模型、求解器或测试，也未读取或重新验证全部原生大文件。

- 当前 873 个源文件、36 个输入逐文件 SHA 与 P01 冻结相同；旧 864/36 绑定未变。两条新增实际运行均为 producer `575cb02b2ac3a1bbdce99a6f283891dc05b3d51a`，完成 180 分钟、未知尾段 0、退出码 0。登记总数为 4 条旧 V30 复用与 2 条新 V32。
- 两条新 CASE 与冻结 case 完全相等。spec、plan、actions、initial、selection 的 SHA 在现存文件、CASE、INVOCATION 和 RESULT.extra_input_bindings 中一致。均为 1160 小时起点、180 分钟、H1，采用同一个 strict 版本。
- 参照控制器通过显式 `controller_factory=OfflinePlanPolicy` 注册，没有构造 C/M 策略或调用 C/M 决策。snapshot 中 M 后缀 case 仅提供原物理配置，共同 reset 不构造控制器、不执行 episode。
- actions 仅生成 ordinary_dispatch，经原 pending_commands 与实际延迟规则；45 分钟为登记的准备提前量，实际场景通信参数为 15 分钟，非保证启动完成时刻。原共享 M23 当前关键热/冷最小准备会扩大锅炉/EC 权限，已在源码、动作限制和 CASE 披露。
- 文件与每条请求的 `offline_known_window` 和三个 false 标志一致：完整连续计划、计划储能端口及计划缺供均未直接执行；缺供未转换为主动断供，储能计划另存而不直接写库存。

需保留的限制：独立初始化证书绑定的是当时 872 源文件状态，最终执行绑定 873。随后新增 reference.py，输入/planner 和三个新测试发生修改；原 864 科学文件及 36 输入在两时点相同。审查如实保存这一差异，不把证书改称最终 873 源版本。初始化的全层求解器调用数未单独仪表化，因此为 null；post-reset core 日志数量为 0 不能推成底层初始化未求解。

运行时构造器独立校验了前两个 false 标志，未单独拒绝 `planned_unmet_directly_executed=True`。本轮实际 SHA 绑定的动作文件及 CASE/RESULT 全部把三个字段设为 false；因此这是通用接口的防御性校验限制，不影响已冻结两条运行的事实。原 CASE 中绝对路径是历史证据，最终交付的重定位与恢复由独立包验收负责。

这些证据支持“文献适配的离线启停/准备请求＋共同实时分配”。不支持原作者软件复现、同信息 C/M 排名、完整动态可行性保证或费用下界。SELECTION 中 actual_hospital_runs_started=false 是选择时点记录，并非当前运行状态。

复核命令见 `STATIC_REVIEW01_COMMAND.json`，实际退出码 0，stdout 见 `STATIC_REVIEW01.log`。脚本只用标准库并以 `--project --root --output` 接收位置，输出文件必须新建。前置一次交互读取省略 UTF-8 时出现 GBK 解码错误，随后显式 UTF-8 重读成功；未改任何输入或证书。
