# 冷机接管与冷却链恢复的文献依据

日期：2026-11-04。只核对文献和现有实现，没有新增科研轨迹，没有将外部设备参数写入本院模型。

## 结论与适用范围

有直接研究支持：吸收式冷机和离心冷机的启动、负荷改变具有瞬态过程；冷却塔、泵和换热网络的温度、水力状态需要与设备开停状态分开描述。

“维修完成并不自动等于正常交冷恢复”是据此提出的**建模推论**，不是以下论文对本院维修过程的直接观测。是否存在明显延迟、具体启动时间和爬坡率，仍须依据相应设备及其当时状态确认。不能给所有故障一律追加同一个等待时间。

## 1. 吸收式冷机动态：ECM 2026

González, J. M.; Villa Ochoa, A. A.; Cardemil, J. M.; Godoy, F.; Zamora Zapata, M. **Unraveling the absorption chiller dynamics, quasi-linear behavior and potential for advanced control applications.** *Energy Conversion and Management* 353 (2026), 121191.

- DOI：https://doi.org/10.1016/j.enconman.2026.121191
- 出版社：https://www.sciencedirect.com/science/article/pii/S0196890426001603
- 已核内容：出版社公开摘要、引言及模型、验证、结果节的公开片段。未声称取得整篇付费全文。
- 支持：热水入口温度和流量改变后，制冷量与热输入具有动态响应；文章用响应时间和瞬态性能损失描述系统惯性。
- 不支持：本院OEM启动参数、维修后固定等待时长、故障复位许可或本院模型已验证的精度。

## 2. 离心冷机启动与负荷瞬态：IJR 2008

Bendapudi, S.; Braun, J. E.; Groll, E. A. **A comparison of moving-boundary and finite-volume formulations for transients in centrifugal chillers.** *International Journal of Refrigeration* 31 (2008), 1437–1452.

- DOI：https://doi.org/10.1016/j.ijrefrig.2008.03.006
- 出版社：https://www.sciencedirect.com/science/article/abs/pii/S0140700708000637
- 已核内容：出版社摘要及公开引言片段。
- 支持：研究比较经离心冷机试验台验证的动态模型，涉及启动与负荷变化，蒸发器、冷凝器动态对响应重要。
- 不支持：任意电冷机都采用该试验机的启动时间、换热器参数或维修恢复过程。

## 3. 塔—泵—换热网络耦合动态：JPC 2018

Viljoen, J. H.; Muller, C. J.; Craig, I. K. **Dynamic modelling of induced draft cooling towers with parallel heat exchangers, pumps and cooling water network.** *Journal of Process Control* 68 (2018), 34–51.

- DOI：https://doi.org/10.1016/j.jprocont.2018.04.005
- 出版社：https://www.sciencedirect.com/science/article/abs/pii/S0959152418300672
- 已核内容：出版社摘要、引言及模型、仿真、结论的公开片段。
- 支持：冷却水网络需同时处理泵/风机运行状态与连续水力、热力状态；开机状态不能完整代表交热能力已达到稳态。
- 不支持：本院真实共用回路拓扑、泵扬程曲线、阀门动作、故障检测延迟或维修后的恢复分钟数。

## 4. 预热阶段尚未交冷的直接实例：Energy Reports 2022

Guerrero Delgado et al. **Fresnel solar cooling plant for buildings: Optimal operation of an absorption chiller through inverse modelling.** *Energy Reports* 8 (2022), 3189–3212.

- DOI：https://doi.org/10.1016/j.egyr.2022.02.128
- 作者机构全文：https://idus.us.es/server/api/core/bitstreams/ba10dc11-1654-4231-a698-d59cb0808a48/content
- 已核内容：完整PDF可读取；重点为§4.2（印刷p.3199，Starting）与§4.4（Shutdown）。
- 支持：该试验机将发生器预热与正常制冷分开，预热阶段蒸发器尚不交冷，达到其工作条件后再进入制冷阶段。
- 不支持：将该试验机预热温度和分钟数移植为本院参数；引用其设备动态不授权引入其预测控制方法。

## 对当前模型结论的影响

1. 现有冷机许可上限可以约束准稳态出力，但不足以单独证明真实冷机启动和接管瞬态。
2. 现有塔/泵可用率解除，不能单独证明水温、流量和末端服务已恢复；扩展模型时需分别登记这些状态及资料来源。
3. 本次找到的是建模依据，不是本院设备验证。没有物理资料时应保留未知，而不是为使曲线出现差距而加入任意时延。
4. 正常蓝线与响应绿线重合可以是合法结果；严格未响应对照的验收目标是无优化、执行合法与物理核账成立，不是缺供最大或算法必胜。

