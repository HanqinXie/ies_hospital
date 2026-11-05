# 获取与复现范围

## 本仓库能直接核验什么

运行 `python -B release/verify_publication.py` 可检查：发布清单覆盖、逐文件SHA、科学快照复制身份、Python静态语法、禁入文件和最大文件限制。该核验不加载医院模型，不启动优化或训练，不证明科学完整测试通过。

model/含年度冻结源码的所有Git tracked文件；studies/fixed_prefault/source/含本地研究工作树的tracked及已识别untracked文件。两个快照均保留原字节（Git设置为不作文本换行转换）。源码中原README、旧release和历史结果的通过记录按原producer解释。

## 为什么不是直接双击即可运行的完整年度包

annual/normal_year和annual/busy_year保留原启动器、案例/模型、日历配置、测试及来源清单，未复制各自的大体积历史夹具、核账域存储、原始运行目录及独立Git数据库。原MANIFEST保留原字节，仍描述原完整包，不能拿它给当前精简源码仓库授予完整交接包资格。

原 `verify_source_copy.py` 要求原包中source/独立Git HEAD和冻结身份，不能在本次新发布提交上直接伪装通过。model/conftest.py也保留原历史路由；完整测试需要同包真实7fa上下文与历史夹具。未伪造这些材料，没有修改测试断言或跳过策略。

本地完整包原路径为工作区 transfers/normal_year 和 transfers/busy_year。正式服务器复现须使用完整原包或另行审核构造的同身份包，核对SOURCE_FREEZE、PAYLOAD_PROVENANCE、输入和真实历史夹具，随后执行当次默认测试、短门、恢复/核账和服务器准入。不能仅凭本仓库源码存在启动年度实验。

## 环境

当前年度目标平台为Windows/EPYC9654/256GB；输出使用包外E盘与登记守卫。Python及科学依赖见 model/requirements*.txt；它们是历史文件，未包含所有实际求解依赖。当前SF2路径额外要求自行配置的Gurobi13及有效许可；许可证不随仓库发布。训练依赖仅用于另行授权的历史学习研究。

本次没有执行pip安装、默认科学pytest、医院轨迹、年度、训练或参数搜索。发布核验记录单列于 release/VALIDATION.json，禁止借用旧实验数字作为本次运行结果。
