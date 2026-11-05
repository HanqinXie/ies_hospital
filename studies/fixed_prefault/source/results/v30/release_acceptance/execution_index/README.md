# V30 真实执行与证据分层索引

当前研究实际INVOCATION记录60条，最终预计60；最终固定C/M背景32/32。索引状态：complete_accounting。独立解压public15 smoke另列1条，不混入60条研究执行。

角色计数：{"prior_development_or_failure": 25, "final_fixed_core_C_M_comparison": 32, "physical_fix1_counterexample_verification": 1, "fixed_core_R_off_reference": 2}。正式成功58、已终结命令60；失败2条。没有按胜负、缺供或费用删记录。

RESULT字段优先且保留原null：不以STATUS或ACTUAL_PREFIX覆盖RESULT.observed_until_minute。R01正式failed_unknown/observed=null/unknown_tail180仍保留；其原生ACTUAL_PREFIX末时刻180仅附事实说明，不补DG机械功、完整队列或成功账户。D01 b02 M真实failed90min及unknown90min尾段保留，修复后FIX01是另一次执行。

未知值在JSON中为null，CSV中为空；已记录的0仍保留为0。solver rejected、timeout、策略回退、投影回退、服务实质中断分别列出，不互相代替。原生文件SHA来自本次只读读取，活跃写入时的文件稳定性另记，不能把部分快照称最终冻结。

源码冻结比较：843→858；共同不变838、改动5、新增15、删除0。原36输入逐键SHA一致=True；当前原输入实读匹配=True。

唯一既有物理core改动是M22 EngineeringCore._clamp两处pending.ready锚点，改为max(now,pending.started)后再加剩余已供电启动工作，另有两行注释。原src与原policy/planner不变。共享runner新增纯实际费字段、V23 worker可选显式controller factory、V22失败导出物化solver行/保留已有终态、pytest增加6入口均单列；不能把这些说成物理核心全无改动。CODE_DIFF.patch/GIT_HISTORY.txt来自只读38c5259..3252727，当前未提交docs改动单列WORKTREE_DOCS_DIFF.patch，未commit或改D。

历史V29实际结果单列7条，属于旧producer复用；离线导出回执18条；原型数学优化结构化记录18条，均不冒充新医院仿真。未单独仪表化的内部单元测试求解数量为unknown，不编0。

尚未终结/未知命令：当前记录中无；仍须核对60条与独立smoke门槛。

实际命令、cwd、返回码、producer/git/source/input SHA、patch、硬件、case、method、原生轨迹/前缀/solver sidecar哈希和每载能服务字段见ACTUAL_ATTEMPTS.json/csv。PUBLIC_SMOKE.json、HISTORICAL_V29.json、OFFLINE_EXPORTS.json、MATHEMATICAL_PROTOTYPE_ATTEMPTS.json、GLOBAL_PHASES.json分别保留证据身份。

本脚本仅建立索引，不调用模型/求解器、不复算或修改原结果、不清理失败目录。当前partial状态不能当作本轮研究已完成；最终需在真实60条终结且独立解压public15完成后，用新的output目录再执行。
