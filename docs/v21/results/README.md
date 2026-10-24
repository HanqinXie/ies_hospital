# 已完成研究结果的本地数据

这里保存已真实接受阶段的CSV/MAT原字节副本，供读取、复核和MATLAB重画。`MIRROR_MANIFEST.json`逐文件列出原路径、当前本地相对路径、字节数和SHA；`provenance/`保留原生或派生分析收据及root阶段接受收据。原收据内绝对路径保持原样，不把复制位置冒充原生运行位置。此处副本清单只证明复制来源，不新签物理或科学接受。

| 目录 | 实际分母与用途 |
|---|---|
| `main02_full24_native` | 1920轨迹、384场景、24源块，原生全池结果，含5个已调试背景 |
| `main02_primary19` | 1520轨迹、304场景、19源块，主比较；每类76场景，Q1/Q2/Q3/Q4源块数1/6/6/6 |
| `main02_debug5` | 400轨迹、80场景、5个调试源块，单独描述，不冒称未见验证 |
| `q1_supplement3` | 240轨迹、48场景、3个Q1新小时背景，日历日期历史已见；与主19分别统计 |
| `followup_supported252` | 原256中受支持的252条：机制64/交互32/正常20/A64/B48/敏感性24，分家族/窗口/配置分析；root已接受受测运行结果，112必要界另行已接受 |
| `followup_original256_ledger` | 同一原256完整登记账：252完成加4构造失败，科学量NULL/CSV空/MAT NaN保留；与上一池重叠，不相加成508，不称256全通过 |
| `hardware126` | 14场景×C/A/M×H0/H1/H2共126轨迹；360 min全窗，4个已见季节块，复合仅2块；原CA56与补充CAM84/M28分别保留 |
| [`final12_actual01`](final12_actual01/README.md) | 最终独立单池12源块（季度4/3/2/3）、48场景×五方案=240，H0/720 min；原生parent、final12 child、全物理紧凑证据、统计与posthoc、root gate均保留，不含raw |

主1920、Q1补充240、支持252、硬件126、最终240这五个实际科学池共2778条轨迹；必要界112属于支持252子集，作为第六科学阶段单独审计但不再加112。原后续master256另有4条构造失败/UNKNOWN，不能当作已执行事故或补零。primary19、Q1_3、final12分别以19、3、12个源块为统计单位，不合并CI，也不是34家医院。

main02_full24_native、primary19、debug5、Q1补充及final12每景五方案均使用共同H0和720分钟完整窗。派生表保留原生值、行序和各自源块聚类区间，不计算22背景合并CI。支持252及原256账中的机制/交互/正常/边界/敏感性使用各自登记方案、配置及360/720/900分钟等完整窗，不能混池排名。原生结果中空的MECHANISM/HARDWARE等表如实保留，表示该池不承担该试验，不补零；正式硬件全126已交hardware126目录，原native的CA56与补充CAM84/M28范围明确分开。没有为减小文件改变数字、统计或筛除不利记录。

`CASE_METRICS.csv`提供逐scenario/policy/configuration/model/source_block身份与工程指标，`CARRIER_SERVICE.csv`提供4等级×3载能请求、实际交付及两类未满足量，`ATTEMPT_STATUS.csv`保留实际状态，`PAIRED_DIFFERENCES.csv`和`PAIRED_SUMMARY.csv`保留完整匹配比较及原生区间。`SCI_TABLES.mat`保留对应数字精度，MATLAB绘图必须与同目录CSV配套读取。窗口、数值epsilon与实质中断阈值不能混用；电、热、冷能量不能相加抵销，时间并集不等于单设备续航。

所有JSON、CSV及MAT沿各自原SHA可核验。第一批实际复制命令见E研究目录`table_publication01/logs/publish_completed_main_q1_tables01.*`，实际1.253761秒、exit0；四组数据共167264328字节（不含新镜像清单），没有新求解、物理尝试或CI重估。完整raw及求解行大证据仍在E原目录；最终交付包按固定完整代表组保留必要raw，并提供所有科学案例和历史失败/未知的索引。

第二批实际复制见`table_publication02/logs/publish_followup252_and_original256_01.*`，0.117735秒、exit0；252受支持结果和完整原256失败账共30个原文件、16,180,848字节，逐文件SHA相同。`PUBLICATION_TABLE_RECEIPT02.json`绑定两镜像；原失败量的JSON NULL/CSV空/MAT NaN已在原生导出独立核验，复制不重算统计或改写未知。

本地重画主比较的入口示例，从仓库根目录启动MATLAB，输出必须是尚不存在的新目录：

```matlab
addpath(fullfile(pwd,'docs','v21','figures'));
dataDir = fullfile(pwd,'docs','v21','results','main02_primary19');
outputDir = 'E:\hospital_ies_replot\primary19_new';
receipt = plot_sci_engineering_publication(string(dataDir),string(outputDir));
```

这是可重画调用配方；已交付图的实际对象、格式与目视核验以`figures/main02_primary19`和`figures/main02_C_process_F6`的原收据为准。原图内保留来源路径，不会因本地重画而自动成为已核验正式图。运行与依赖见上级`REPRODUCTION.md`；新的大输出和临时数据使用D/E。


硬件正式池`hardware126`已发布43文件、10,242,889字节，actual0.102618 s、exit0；原native全126/1512载能、CAM84严格配对、原物理小账和两段执行/独立核查来源均保留。14场景只有4已见季节块、复合2块，不新增独立背景。出版receipt SHA4974ac1089bceefcd0b3bd7b74f24eb2f35c64247e8796aa76f5a5e8ef4479b0。

最终240已由root科学gate0b81…接受，final12原cohort CLI110.5482911 s、exit0；`pooled_CI=true`仅在该12块池内，不是19+3+12合并。结果镜像351文件/42,357,426 B，配套图件85文件/25,082,714 B，实际出版receipt SHA `f6331743752a9040af8f6ffa0cdd263ae1e695e03886927e073c61336b4be300`，两次fresh逐文件0漂移；复制不产生新统计/物理接受。

final12不能复制主19/Q1的全部A/C服务tie：关键33 material及三阶段gap exact，关键电ENS各一景+4.063206741e−7 kWh；一景P3电material−60 min/ENS−4.063166 kWh，伴费用+1.21894084历史USD、条件CO2+2.83484935 kg，初始/恢复窗相同。全部176/65、每类原CI与改善/同/恶化计数在final12_actual01/reviews/native_statistics，事后101段/48命令解释在reviews/posthoc；后者新增独立n=0，不改先验八raw组。F/M unionties不等于所有ENS相同。

科学结果与正式图件已完成；final12本地CSV/MAT配方随后实际30.7815965 s、exit0并通过功能/原生/格式检查（closure6fe0…64bf），新输出未重复四完整页视觉；原镜像README保留复制时未运行状态。本次冻结紧凑ZIP、新解压当前4dfe源365/365完整重放及新默认pytest（692 passed, 1 skipped in 218.45s (0:03:38)）已实际验收；最终普通Git提交/推送状态以Git和外置最终回执为准，不由本proof代签，见[交付验证](../DELIVERY_VERIFICATION.md)。旧clean03和本机692历史测试没有冒充新包验收。

后续实际本地绘图/原生FIG再导出功能证明已单列于[functional_entry_checks01](../figures/functional_entry_checks01/README.md)，未改本目录已封子镜像。功能/native/格式通过与新输出完整页视觉NOT_RUN分开记录。
