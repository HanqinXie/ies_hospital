# final12_actual01：结果与审查证据的本地交付镜像

本目录为 E:/hospital_ies_v21_20261023 已完成 final240 结果的小文件逐字节镜像，**NOT 新计算、NOT 新 authority**。所有原 JSON 保持原字节，其中 E 盘绝对路径继续指向权威原证据，没有改写成本地路径。MIRROR_MANIFEST.json 提供每个 canonical source 到本地相对路径的 SHA/bytes/角色映射。

| 相对目录 | 角色 |
|---|---|
| native_parent/ | 原 PARALLEL_ANALYSIS_RECEIPT、正式 TABLES.json.gz 和全部原 CSV/MAT/export receipt |
| cohort/final12/ | 完整 final12 派生子集；DERIVED_TABLES、FILTER_INDEX、原导出目录均保留 |
| physical_audit/ | 原完整 compact 物理审计输出，包括 case 小 JSON、CASE_AUDITS、inventory、receipt；不是只摘通过子集 |
| reviews/native_statistics/ | 全35文件独立统计审查、失败/未知口径、源码及实际日志 |
| reviews/posthoc/ | 最终结果后选出的 A/C 局部解释和封存清单，新增独立样本数0 |
| root_metadata/ | 已存在 root scientific gate、JOBS/身份guard/current-byte/admission 等镜像 |

物理审计 receipt 内相对路径可在 physical_audit/ 下直接找到；JSON 内 RESULT/raw/sidecar 等原 E 路径继续是 canonical 引用。这里不含轨迹或 solver_rows sidecar，因此不能仅凭本镜像重新执行完整物理账审计。它保留原审查的紧凑输出，不发出新科学 gate。

范围为12个源背景、L1–L4每类12个情景，共48情景×5策略=240，H0、720min；季度源块分布4/3/2/3。它是同一DOE医院原型和气象年的案例，不是12家医院或自然事故概率。空 hardware/mechanism/boundary CSV 按原字节保留，不解释为完成了新增实验。

一般电服务局部收益并非无代价：A/C一例P3 material少60min，对应约4.063kW边际交付，不是全一般区额外供能60min；费用/条件排放及微小关键电ENS增加均保留，未清零。详细统计见 reviews/native_statistics/REVIEW.zh-CN.md，事后机制见 reviews/posthoc/POSTHOC_ENGINEERING_EXPLANATION.md。

配套当前图件见 ../../figures/final12_actual01/README.md。该图件的本地 CSV/MAT 再绘尚未在本次镜像操作中执行；原 E 图件已有实际MATLAB生成、对象/格式及完整页视觉证据。总报告、总README和Git收尾由主任务另行处理。

本轮实际执行仅为目录排他检查、容量估算、复制与逐文件SHA核对。未运行MATLAB、物理、LP、pytest、cohort或analysis。大型原raw/sidecar和冗余analysis group缓存均未复制。
