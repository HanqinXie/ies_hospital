"""Render the scoped engineering note from the already qualified 123-row evidence."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DOC = ROOT / "docs/v20/FIXED_BACKGROUND_AND_CLOCK_ANALYSIS.md"
PREFIX = "../../outputs/v20_load_accounting_20261019_run01/review/fixed_sensitivity_analysis01/"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name):
    with (HERE / (name + ".csv")).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def number(row, key):
    value = row[key]
    if value == "":
        raise ValueError("Unknown value cannot be converted to zero: " + key)
    return float(value)


def total(rows, key):
    return sum(number(row, key) for row in rows)


def table(lines, headers, rows):
    lines.extend(["| " + " | ".join(headers) + " |",
                  "|" + "|".join("---" for _ in headers) + "|"])
    lines.extend("| " + " | ".join(str(v) for v in row) + " |" for row in rows)
    lines.append("")


def main():
    proof = json.loads((HERE / "PROOF.json").read_text(encoding="utf-8"))
    assert proof["status"] == "passed" and proof["target_pairs"] == 123 and proof["target_batches"] == 41
    for name, expected in proof["output_files"].items():
        assert sha(HERE / name) == expected, name
    assert sha(HERE / "ANALYSIS.json") == proof["analysis_sha256"]
    analysis = json.loads((HERE / "ANALYSIS.json").read_text(encoding="utf-8"))
    service, unions, energy, versions, clocks, props, events, selected = [
        load(name) for name in ("PRIMARY_SERVICE", "PRIMARY_UNION", "PRIMARY_ENERGY",
            "V19_V20_CRITICAL", "CLOCK_PAIRS", "PROPAGATION_PAIRS",
            "ACTUAL_EVENT_COUNTS", "SELECTED_EVENTS")]
    assert len(service) == 1476 and len(unions) == 492 and len(energy) == 123
    assert len(clocks) == len(props) == 9 and len(versions) == 738
    assert all(r["comparison_valid"] == "True" for r in versions)
    assert Counter(r["cohort"] for r in energy) == Counter(fixed8=96, propagation=18, open1=6, contact025=3)
    lines = [
        "# 固定八背景、传播开关与有限时钟的工程分析", "",
        "状态：已保存的41个目标批、123条轨迹均逐批通过资格核验；本报告只覆盖下表分母。"
        "分析只读取既有compact RESULT及其绑定的analysis CSV，不新跑物理，不读取raw或8760年度数值。", "",
        "## 1. 分母、窗口与证据", "",
        "固定八背景是已见规格的复核。当前登记的evaluation_role/background_status决定本轮身份，"
        "不沿用旧历史元数据中的未见措辞，也不把它称为原封存12背景的评价。"
        "L1为外生设备故障，L2为允许继发保护的设备事故，L3为需求冲击，L4为复合情形；"
        "是否实际发生继发跳机另据事件记录判断。", "",
    ]
    table(lines, ["队列", "目标批", "E/K1/C19轨迹", "本报告唯一主窗口"], [
        ["fixed8", 32, 96, "full [0,600) min"],
        ["propagation", 6, 18, "full [0,600) min"],
        ["open1", 2, 6, "full [0,600) min"],
        ["contact025", 1, 3, "registered_full [0,1065) min"],
        ["合计", 41, 123, "不把600与1065 min混合成单一总量"],
    ])
    lines.extend([
        "时钟对照另读main003、main067及long005三批基线，共9条配对上下文。"
        "因此资格证明含44批/132条上下文，目标结果分母仍为123。"
        "full与registered_full不重复计数；两个OPEN对照按600 min，contact及long005双方均按1065 min。", "",
        "正式命令：D:/hospital_v20_capture_20261019.py --name fixed_sensitivity_readback01 -- "
        "python -B scripts/v20_report.py --label FIXED_SENS_VERIFIED01 --baseline-root <原V19工程>；"
        "实际退出0。随后本目录analyze.py实际退出0。等待器从41批终态齐备后各调用一次，"
        "WAIT_RESULT记录completed_readonly_collection。", "",
        "逐批复用release_gate资格：完整registered_full服务与源账本、source/test/runtime freeze、"
        "RESULT/plan/manifest/终态/attempt及实际reset/外生/输入证据；再与报告STATUS和INPUT_INDEX交叉绑定。"
        "本报告采用各主窗口内的逐值读回，不以completed标签代替资格。"
        "大EVENT_TIMELINE.csv只在本地，原导出SHA已核后仅提取877条小型事件行；其中含9条基线轨迹上下文。", "",
        "## 2. 固定八背景的关键服务", "",
        "以下每个L类、每策略均为8个规格。病例数按该优先级e/h/c失供时间并集判断，"
        "分钟为8个规格的累计并集分钟，最长为单条轨迹内最长段。"
        "载能ENS分别保留kWh_e、kWh_h、kWh_c，不相加。", "",
    ])
    critical_rows = []
    critical_summary = []
    for cls in ("L1", "L2", "L3", "L4"):
        for alias in ("E", "K1", "C19"):
            values = [cls, alias]
            item = dict(canonical_class=cls, alias=alias, n=8)
            for priority in ("P0", "P1"):
                own = [r for r in unions if r["cohort"] == "fixed8" and r["canonical_class"] == cls and r["alias"] == alias and r["priority"] == priority]
                cases = sum(number(r, "material_union_minutes") > 0 for r in own)
                minutes = total(own, "material_union_minutes")
                longest = max(number(r, "longest_union_minutes") for r in own)
                repeats = sum(int(r["loss_run_count"]) > 1 for r in own)
                values.extend([f"{cases}/8", f"{minutes:g}", f"{longest:g}"])
                item[priority] = dict(material_cases=cases, union_minutes=minutes, max_longest_minutes=longest, repeated_cases=repeats)
            critical_summary.append(item)
            critical_rows.append(values)
    table(lines, ["类", "策略", "P0病例", "P0并集min", "P0最长min", "P1病例", "P1并集min", "P1最长min"], critical_rows)
    lines.extend([
        "固定组没有P0/P1电的实质失供，也没有P0热的实质失供；这不等于ENS严格为零。"
        "例如电ENS各类8例合计约0.0023–0.0033 kWh_e，仍原样保留，未用扩容差清零。"
        "所有固定组P0/P1失供均为单段，重复段病例为0。", "",
        "day269公共冷却塔TOWER在[90,270) min外生故障，对应fixed020/L1、fixed021/L2、fixed023/L4。"
        "三策略在这3例均有P0冷180 min及P1冷180 min；每例P0冷ENS约126.305 kWh_c、P1冷约85.6714 kWh_c。"
        "首次干净时刻270 min，随后[270,300)满足30 min干净确认。"
        "该结果保留公共冷却边界下的失败，不能表述为C19保障全部P0/P1。", "",
        "E另有两个固定L4坏例：fixed007（day123，v19_confirm_L4_f4164a603797253f61fe6125）"
        "P1热在[90,105)失供15 min，ENS=4.520920316 kWh_h，干净确认[105,135)；"
        "fixed011（day142，v19_confirm_L4_ff8909deb73822334206a161）"
        "P1冷在[90,120)失供30 min，ENS=6.930467581 kWh_c，干净确认[120,150)。"
        "K1和C19没有这两项实质失供。E总P1病例为5/32，K1/C19各3/32；三策略P0各3/32。"
        "首次干净与30 min确认均只是已观测窗口内状态，不保证永久恢复。", "",
        "### 2.1 逐载能ENS与跨版变化", "",
    ])
    carrier_rows = []
    aggregate = load("FIXED8_AGGREGATE_SERVICE")
    for cls in ("L1", "L2", "L3", "L4"):
        for alias in ("E", "K1", "C19"):
            row = [cls, alias]
            for priority in ("P0", "P1"):
                own = {r["carrier"]: r for r in aggregate if r["canonical_class"] == cls and r["alias"] == alias and r["priority"] == priority}
                row.append(" / ".join(f'{number(own[c], "ENS_kWh"):.7g}' for c in ("e", "h", "c")))
            carrier_rows.append(row)
    table(lines, ["类", "策略", "P0 ENS e / h / c，各自kWh", "P1 ENS e / h / c，各自kWh"], carrier_rows)
    lines.extend([
        "旧版读取以已登记V19 RESULT的SHA为依据，显式UTF-8读取compact；"
        "旧载能material从旧RESULT实际读回，不用优先级并集推断载能。"
        "固定96条的576项P0/P1×载能同策略比较中，574项实质分钟不变、2项下降、0项上升。"
        "两项均为fixed031 E（v19_confirm_L4_962fe1d3ec454657b5f607b7）的P1热和P1冷，均15→0 min；"
        "K1、C19的各192项均不变。这里的计数是载能项，不是576个病例。", "",
        "全123目标共有738项载能比较且comparison_valid均为true；"
        "不同队列的窗口与时钟有区别，本节上述574/2/0只指固定96。"
        "V20改正了请求分组，尤其P3为建筑总量扣除P0–P2后的残量；"
        "旧版没有完整请求/交付账本，old_request_delivery_unknown保持true。"
        "不能仅凭跨版P3 ENS升降宣称相同比例的服务改善或恶化。", "",
        "## 3. 固定组P2/P3及实际能源取舍", "",
        "下表每策略累计相同32个600 min规格，仅用于本组等分母比较，"
        "不是连续运行320小时或年度估计。各载能请求在三策略间仅有浮点舍入微差；"
        "请求、交付、主动断开与连接少供均来自实际服务账本。P2热请求为0，不计算热服务比例。", "",
    ])
    service_rows = []
    general_summary = []
    for priority in ("P2", "P3"):
        for carrier in ("e", "h", "c"):
            for alias in ("E", "K1", "C19"):
                own = [r for r in service if r["cohort"] == "fixed8" and r["priority"] == priority and r["carrier"] == carrier and r["alias"] == alias]
                values = {f: total(own, f) for f in ("requested_kWh", "delivered_kWh", "ENS_kWh", "disconnected_ENS_kWh", "connected_unserved_ENS_kWh")}
                general_summary.append(dict(priority=priority, carrier=carrier, alias=alias, **values))
                service_rows.append([priority, carrier, alias] + [f"{values[f]:.6f}" for f in values])
    table(lines, ["优先级", "载能", "策略", "请求kWh", "交付kWh", "ENS kWh", "主动断开ENS", "连接少供ENS"], service_rows)
    lines.extend([
        "C19相对E/K1降低了本组P2冷、P3热与P3冷ENS，但P3电ENS增至323.267 kWh_e"
        "（E 215.176、K1 292.579）。C19的P3电ENS中193.069 kWh_e为主动断开，"
        "130.198 kWh_e为连接少供；另两策略本组P3电主动断开ENS为0。"
        "不能把关键服务择优称为全层级不劣。", "",
    ])
    energy_rows = []
    energy_summary = []
    source_rows = []
    source_fields = (("GRID_electric_kWh", 1000.), ("GT_electric_kWh", 1000.), ("DG_electric_kWh", 1.),
                     ("HRSG_steam_lb", 1000.), ("BOILER_steam_lb", 1000.), ("EC_cooling_kWh", 1000.),
                     ("ABS_cooling_kWh", 1000.), ("TOWER_rejection_kWh", 1000.))
    for alias in ("E", "K1", "C19"):
        own = [r for r in energy if r["cohort"] == "fixed8" and r["alias"] == alias]
        values = {f: total(own, f) for f in ("actual_energy_fee_USD", "grid_plus_gas_CO2_kg", "DG_CO2_kg_conditional", "total_CO2_kg_conditional", "DG_shaft_work_kWh", "DG_fuel_budget_debit_kWh_equivalent")}
        exhausted = sum(bool(json.loads(r["saved_observed_DG_exhaustion_minutes"])) for r in own)
        energy_summary.append(dict(alias=alias, n=32, observed_exhaustion_cases=exhausted, **values))
        energy_rows.append([alias] + [f"{values[f]:.3f}" for f in ("actual_energy_fee_USD", "grid_plus_gas_CO2_kg", "DG_CO2_kg_conditional", "total_CO2_kg_conditional", "DG_shaft_work_kWh")] + [f"{exhausted}/32"])
        integrals = [json.loads(r["actual_source_integrals"]) for r in own]
        source_rows.append([alias] + [f'{sum(number(v[k], "value") for v in integrals)/scale:.3f}' for k, scale in source_fields])
    table(lines, ["策略", "实际能源费USD", "grid+gas CO2 kg", "DG条件CO2 kg", "条件总CO2 kg", "DG轴功kWh", "观测耗尽"], energy_rows)
    table(lines, ["策略", "GRID MWh电", "GT MWh电", "DG kWh电", "HRSG klb", "锅炉klb", "EC MWh冷", "ABS MWh冷", "塔MWh排热"], source_rows)
    lines.extend([
        "本固定组C19相对E能源费少365.271 USD而条件总CO2多6284.738 kg；"
        "相对K1费用少77.005 USD且条件总CO2少2544.229 kg。"
        "费用与排放不必同向。C19较E更多GT/HRSG和ABS、较少GRID/EC与锅炉；"
        "较K1则GT/HRSG更少、ABS与锅炉更多。塔排热、蒸汽lb、冷量和电量分别列示，不能直接相加。", "",
        "固定32例内三策略均未观测到DG预算耗尽。K1/C19的DG实际电量为0，"
        "轴功预算仍分别支出321.783/317.283 kWh等价，不能将启动、损耗或转子过程预算当成母线供电。"
        "实际能源费用原DG电量计费，轴功替代费用若使用，必须减原DG费再换入，不重复相加。"
        "grid+gas为可计算小计；DG及总CO2依赖CAMX2023r2_DG75pct的恒定轴功油耗映射，"
        "exact化学燃料/启动怠速低载排放仍unknown，不是测得排放或统计区间。", "",
        "## 4. 传播ON/OFF：实际跳机与服务结果", "",
        "三对规格依登记次序为gamma=0.15（propagation000/001）、0.35（002/003）、0.6（004/005），"
        "前者ON、后者OFF。双方实际reset、source、物理config、输入相同；"
        "外生物理投影相同。实际流各自已对注册定义重算，其SHA含有意不同的propagation标志，"
        "因此不要求两侧原始流SHA相等。下表差值为OFF减ON。", "",
    ])
    prop_rows = []
    for row in props:
        prop_rows.append([row["propagation_pair_id"].split("_")[-1], row["alias"],
            f'{number(row,"base_P0_material_union_minutes"):g}→{number(row,"variant_P0_material_union_minutes"):g}',
            f'{number(row,"base_P1_material_union_minutes"):g}→{number(row,"variant_P1_material_union_minutes"):g}',
            f'{number(row,"base_P2_e_ENS_kWh"):.6f}→{number(row,"variant_P2_e_ENS_kWh"):.6f}',
            f'{number(row,"base_P3_e_ENS_kWh"):.3f}→{number(row,"variant_P3_e_ENS_kWh"):.3f}',
            f'{number(row,"delta_actual_energy_fee_USD"):+.3f}', f'{number(row,"delta_total_CO2_kg_conditional"):+.3f}'])
    table(lines, ["gamma", "策略", "P0并集ON→OFF min", "P1并集ON→OFF min", "P2电ENS ON→OFF", "P3电ENS ON→OFF", "Δ费用USD", "Δ条件CO2 kg"], prop_rows)
    lines.extend([
        "9条ON轨迹各有1次GT_1实际residual_stress_protection跳机，时刻均37.5 min，hold=15 min；"
        "9条OFF均0次。ON压力比从gamma .15的约1.138–1.179递增至gamma .6的约1.579–1.620，"
        "不是只按L2标签推定传播。K1三条ON的P0/P1电冷各7.5 min，OFF均无关键实质失供；"
        "E/C19在ON和OFF均无关键实质失供，但ON的P2/P3电ENS较高。"
        "关闭该机制使本配对服务改善，不等于真实医院可合法关闭保护；本节是登记模型机制对照。", "",
        "## 5. 有限时钟对照与直接序列", "",
        "两组OPEN仅把execution_recovery.grid_open_transfer_minutes从0.25改1 min；"
        "contact组仅把local_contact_restore_delay_minutes从0改0.25 min。"
        "完整scene及策略面板相同、source/freeze/runtime inputs及实际reset/外生流相同，"
        "同策略injection相同；config只允许上述单叶变化，config签名因此有意不同。"
        "各对请求积分最大差异不超过4.55e-13 kWh，未更换负荷或事故靶。", "",
    ])
    clock_rows = []
    for row in clocks:
        clock_rows.append([row["variant_batch"].replace("v20_", ""), row["alias"],
            f'{number(row,"base_P0_material_union_minutes"):.6f}→{number(row,"variant_P0_material_union_minutes"):.6f}',
            f'{number(row,"base_P1_material_union_minutes"):.6f}→{number(row,"variant_P1_material_union_minutes"):.6f}',
            f'{number(row,"delta_actual_energy_fee_USD"):+.6f}',
            f'{number(row,"delta_total_CO2_kg_conditional"):+.6f}',
            f'{number(row,"delta_DG_shaft_work_kWh"):+.6f}'])
    table(lines, ["变体/对应登记基线", "策略", "P0并集base→variant min", "P1并集base→variant min", "Δ能源费USD", "Δ条件CO2 kg", "ΔDG轴功kWh"], clock_rows)
    table(lines, ["变体", "策略", "ΔP2电ENS kWh_e", "ΔP3电ENS kWh_e", "ΔP3冷ENS kWh_c", "ΔP3并集min"],
          [[r["variant_batch"].replace("v20_", ""), r["alias"],
            f'{number(r,"delta_P2_e_ENS_kWh"):+.7g}',
            f'{number(r,"delta_P3_e_ENS_kWh"):+.7g}',
            f'{number(r,"delta_P3_c_ENS_kWh"):+.7g}',
            f'{number(r,"delta_P3_material_union_minutes"):+.7g}'] for r in clocks])
    lines.extend([
        "open1_000对应main003，open1_001对应main067，contact025_000对应long005。"
        "9对P0/P1的逐载能实质分钟、失供段、最长段及并集分钟均不变，"
        "ENS微差原样保留：关键电ENS的非零变化约−4.96e-7至−6.82e-7 kWh，未称其为实质改进。", "",
        "OPEN真实回网序列：main003 E/C19两侧均在105.25 min进入TRANSFER_OPEN，"
        "GRID时刻由105.5改106.25；K1两侧均在120.25进入TRANSFER_OPEN，GRID由120.5改121.25。"
        "main067三策略均在150.25进入TRANSFER_OPEN，GRID由150.5改151.25。"
        "实际回GRID均推迟0.75 min。K1既有关键坏段仍保留：main003 [82.5,85)共2.5 min，"
        "main067 [97.5,105)共7.5 min，均为P0/P1电冷；不能写成所有时钟变体无关键失供。", "",
        "OPEN没有新增关键实质分钟；6条中4条P3电ENS增加，另2条仅有约−5.56e-5/−5.91e-5 kWh_e微差。"
        "特别K1在open1_000增加1.891236 kWh_e，P3并集52.749988→53.499988 min；"
        "E同组增加0.859018 kWh_e、P3并集42.75→43.5 min。"
        "这些是仍需保留的正延迟代价，不能用关键层不变掩盖。", "",
        "contact两侧三策略回网状态时间完全相同：825 min RETURN_PREP、825.25 TRANSFER_OPEN、825.5 GRID。"
        "E和K1本对所有已提取服务/费用/源预算差值为0；各自既有P0/P1电冷重复失供仍在。"
        "E为[32.5,47.5)及[528.486431,535)，累计21.513569 min；"
        "K1为[32.5,47.5)及[509.025981,520.600478)，累计26.574497 min，"
        "最长均15 min、30 min干净确认后复发1次。C19只有初始[32.5,47.5)共15 min，复发0次。"
        "这些是完整1065 min同窗结果，不能只取首次恢复或600 min截面代替。", "",
        "contact C19的关键层没有新增实质中断，但P2电ENS增加27.175815 kWh_e，"
        "P2并集57.5→102.5 min；P3电ENS增加218.757538 kWh_e、P3冷增加180.253593 kWh_c。"
        "同时轴功支出增45.081382 kWh、费用少9.181457 USD、条件CO2少217.469066 kg。"
        "variant在452.5 min发生GT_3 no_export_interlock，base无此事件；"
        "该差异早于825 min回网，现有小型事件证据不足以把整段差异归因为回网恢复时钟。"
        "本登记单参数对照显示轨迹有变化，但不声称每个数值差异均有连续路径物理因果证明。", "",
        "## 6. 事件分类与适用边界", "",
    ])
    event_rows = []
    event_summary = []
    for cohort in ("fixed8", "propagation", "open1", "contact025"):
        for alias in ("E", "K1", "C19"):
            own = [r for r in events if r["cohort"] == cohort and r["alias"] == alias]
            vals = {f: total(own, f) for f in ("residual_stress_protection", "no_export_isolation", "minimum_output_interlock", "unknown_protection_cause")}
            event_summary.append(dict(cohort=cohort, alias=alias, n=len(own), **vals))
            event_rows.append([cohort, alias, len(own)] + [f"{vals[f]:g}" for f in vals])
    table(lines, ["队列", "策略", "轨迹数", "残余stress跳机", "no-export隔离", "minimum-output联锁", "未知保护原因"], event_rows)
    lines.extend([
        "123目标合计18次实际residual-stress protection跳机、1次no-export隔离、0次minimum-output联锁。"
        "SELECTED_EVENTS中共有24条protection_trip，其中6条来自额外配对基线上下文，"
        "不能把24作为123目标的跳机数。固定组实际3次跳机为fixed025 E GT_1@105、"
        "fixed027 E GT_1@105、fixed031 K1 GT_3@105 min；C19固定组为0。", "",
        "外生设备故障、残余stress继发跳机、no-export隔离和minimum-output联锁分别记录。"
        "健康设备因失排气、失热源或失公共塔而暂时无功能，缺直接日志时保持functional_loss_count=null、"
        "not_directly_logged，不把0出力改记实际trip。"
        "公共母线没有源到优先级的唯一分摊，本表也不把DG轴功或源积分分配为P0/P1供给。", "",
        "这些结论只限已登记的有限场景、时钟值与已实现接口。"
        "不保证任意机械动态、任意延迟、临床温度或连续路径均可行；"
        "零实质分钟、零ENS、硬件恢复、执行许可到达及30 min服务确认互不替代。"
        "各载能、窗口及队列分母始终分开，不由本123条推出完整406条或全年结论。", "",
        "## 7. 可追溯产物", "",
    ])
    table(lines, ["文件", "行数/用途", "SHA256"], [
        [f"[{name}]({PREFIX}{name})", analysis["table_rows"][name[:-4]], proof["output_files"][name]]
        for name in proof["output_files"]
    ])
    lines.extend([
        f"正式PROOF.json SHA256：{sha(HERE / 'PROOF.json')}；"
        f"ANALYSIS.json：{proof['analysis_sha256']}；"
        f"INPUT_INDEX.json：{proof['input_index_sha256']}；"
        f"冻结报告RECEIPT：{proof['report_receipt_sha256']}。", "",
        f"本轮freeze文件SHA256：{analysis['source_freeze_sha256']}。"
        "ANALYSIS中的batch_proofs、source_refs与稳定report inputs记录每条RESULT及旧RESULT的真实路径/SHA；"
        "不把历史成功测试数字冒充本次实验。", "",
        "有限外置合成guard实际先21 RED后21 GREEN，独立review补interlock反例实际2 RED后23 GREEN。"
        "正式readback01与analysis01均exit0；验证的是证据/窗口/配对口径，不是新增物理真实性测试。", "",
    ])
    # All final document numbers above derive from verified tables. The receipt
    # names the exact writer and saved inputs; it does not claim new physics.
    summary = dict(schema="hospital_ies.v20.fixed_sensitivity_document_derivation.v1",
        target_pairs=123, qualified_batches=41, context_pairs=132,
        formal_proof_sha256=sha(HERE / "PROOF.json"), critical_summary=critical_summary,
        general_service_summary=general_summary, energy_summary=energy_summary,
        event_summary=event_summary, document_path=DOC.relative_to(ROOT).as_posix(),
        raw_opened=0, annual_values_read=0, physical_episodes_started=0)
    with DOC.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines))
    summary["document_sha256"] = sha(DOC)
    summary["writer_sha256"] = sha(Path(__file__))
    with (HERE / "DOCUMENT_DERIVATION.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(document=str(DOC), bytes=DOC.stat().st_size,
                         sha256=summary["document_sha256"]), ensure_ascii=True))


if __name__ == "__main__":
    main()
