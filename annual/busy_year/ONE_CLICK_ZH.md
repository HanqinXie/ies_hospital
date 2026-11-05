# Python 三并发一键启动

运行本目录的 `start_three_parallel.py`。不需要 BAT 或 PowerShell 启动文件。
脚本相对自身定位交接包，在包外实际 E 盘自动创建带时间戳的新工作目录。

- 正常年：依次执行包核验、源/fixture核验、独立监督探测、现场预检、完整默认科学测试、三 worker 许可验证、真实短门、准入，然后调用原 `launch_server.py`。
- 繁忙年：先输入正常年同源同 profile 的 `M_ready` 完整8760小时**原始 producer 输出目录**；脚本冻结日历，再执行预检、监督探测、完整科学验证/恢复/核账、准入核验和原独立启动。另两个正常方法不是冻结依赖。保留繁忙年独立许可/environment探测的 skip 政策。
- 并发固定为三条生产轨迹（M_available、M_ready、C_ready），一个核账进程，总配额四个。每条求解器/BLAS单线程；不是三个启动器各启动三条。
- 任一步失败立即停止后续步骤；不覆盖旧目录、不自动从初态重跑、不重写准入通过收据。运行守卫和600GB初始空间要求继续有效。
- 控制台显示当前步骤及日志路径。完整输出在工作目录 `oneclick/*.log`，命令/退出码在 `oneclick/COMMANDS.jsonl`，结果在 `oneclick/RESULT.json`。耗时步骤可打开对应日志查看进度。
- 双击正常结束或出错后等待回车。命令行传参运行不会等待回车；文件关联到编辑器时，请在终端用 Python 执行。
- 年度采用原同用户 WMI 独立监督；启动命令成功不代表年度完成。停止/恢复继续使用 CODEX_TASK.md 的原入口及关闭收据，不反复点击启动器。

命令行用法（在对应包目录）：

```console
python -B start_three_parallel.py
python -B start_three_parallel.py --dry-run
python -B start_three_parallel.py --work-root "E:\ies_normal_new_run01"
```

繁忙年可省去交互，直接传参考路径；也可修改脚本顶部的 `NORMAL_REFERENCE` 后双击：

```console
python -B start_three_parallel.py --normal-reference "E:\normal_run\runtime\cases\M_ready\attempt_000"
```

上面的参考路径只是格式示例，必须换成实际完成且通过来源/核算验证的 producer 目录。
可用 `--frozen-root "E:\ies_busy_calendar_new01"` 指定新的独立日历目录。
`--dry-run` 只打印命令，不创建目录、不调用科学入口；省略 busy 参考时以 NORMAL_M_READY_OUTPUT 标记显示。
本机只验证交接工具，未运行 IES 科学测试或年度；复制到目标服务器后才执行正式流程。

