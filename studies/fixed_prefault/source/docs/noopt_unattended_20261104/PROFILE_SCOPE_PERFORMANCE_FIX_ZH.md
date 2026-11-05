# 优化审计profile作用域性能修复

仅改变审计器安装条件：N/R直接yield；U步结束仍严格早于60-EPS时直接yield；59→60及U post60继续安装原审计。没有改变物理流、输入、容量、动态或失效规则。

必要fake-context回归：修改前3 failed、2 passed（退出1，logs/25_profile_scope_red.txt）；修改后5种作用域加“优化器函数体前拒绝”共6 passed（退出0，logs/26_profile_scope_green.txt）。sys.profile在上下文结束后恢复原值；59→60仍安装。没有reset、轨迹或全套测试。最终git diff --check退出0。
