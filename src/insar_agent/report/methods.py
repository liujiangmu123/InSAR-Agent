"""方法章节草稿:provenance JSON → Markdown(确定性模板;brain/narrate 可选润色)。

模板填空是降级路径的保证(§3.5:narrate 失败降级为模板填空)——
没有 LLM 也能产出结构完整、数字可溯源的方法章节。

质量纪律(2026-08 升级;措辞与文献素材:reference/RESEARCH-insar-params-2026-08-12.md、
reference/RESEARCH-pub-figures-2026-08-12.md):
  1. 正文里的每一个数字都取自 provenance 原值(str() 直排,不重排版),并紧跟可溯引用:
     〔prov-N〕= 第 N 步执行记录,〔prov-产物#字段〕= 指标来源(与审计面板的来源契约同形);
  2. 关键参数值后附依据锚点〔ref:…〕:阈值台账(contract)有 ref 就引台账原文,
     没有的诚实写「本项目配置」;方法学定义类引用(如 Goldstein 1998)只绑定在
     实际执行的方法 id 上,不为未执行的方法编造出处;
  3. 数字禁令:模板自身不携带任何裸数字 —— 社区参考值(如 α 惯例带)只允许出现在
     〔ref:…〕锚点内部,以便测试把「文中数字」与「引用注释」严格分开逐一反查;
  4. 图件引用段仅在 provenance 确有 FIGURE 产物时生成("Figure X shows…" 句式);
  5. 证据边界按 evidence.level 如实声明:达到哪级、为什么停在这级、再往上还差什么。

纯函数:输入只有 provenance dict(+ 可选 contract 台账);不 import registry、不读文件。
"""

from __future__ import annotations

# 资源类参数不进方法章节(线程数等与科学复现无关,完整记录仍在 provenance)
_RESOURCE_PARAMS = frozenset({"threads", "parallel_workers"})

# 参数名 → 阈值台账键(两边命名不一致时的映射)
_PARAM_ALIAS = {"step_date": "stepFuncDate"}

_SOURCE_LABEL = {
    "upstream_default": "上游默认",
    "literature": "文献来源",
    "local_calibration": "本地标定",
}

# 台账外参数的社区参照(只进〔ref:…〕锚点,不进正文;素材:RESEARCH-insar-params)
_COMMUNITY_NOTE = {
    "alpha": "本项目配置;社区惯例带 0.2–1.0:ISCE2 topsApp FILTER_STRENGTH 默认 0.5、"
             "ASF HyP3 adf 0.6、MintPy Galápagos 示例 0.2(RESEARCH-insar-params §2)",
    "cost_mode": "本项目配置;snaphu man page:SMOOTH 假设缓变形变(DEFOMAX=0),"
                 "同震近场跨断层跳变应改 DEFO(DEFOMAX_CYCLE 默认 1.2 周)",
    "range_looks": "本项目配置(多视比按分辨率/信噪比权衡,无统一文献值)",
    "azimuth_looks": "本项目配置(多视比按分辨率/信噪比权衡,无统一文献值)",
    "pairs": "本项目配置(小基线网络按时空基线剪枝后的对数)",
    "network": "本项目配置;MintPy 官方主张宽松阈值 + 冗余网络(Yunjun et al. 2019 §6.3)",
    "ramp": "本项目配置;MintPy 注释:同震/震间等长波长信号不推荐 deramp"
            "(smallbaselineApp.cfg §9)",
    "dpi": "本项目配置;期刊 combination art 惯例 500–600 dpi"
           "(Elsevier artwork sizing;AGU ≥300 dpi)",
    "cmap": "Crameri 科学色标家族(Crameri et al. 2020, Nat. Commun. 11:5444;"
            "发散数据 vmin/vmax 关于零对称)",
    "format": "本项目配置;投稿交付矢量 PDF + 栅格 TIFF,Elsevier 明确不收 PNG"
              "(RESEARCH-pub-figures §1.2)",
    "periods": "本项目配置;年 + 半年项吸收冻融季节循环的非正弦不对称"
               "(Daout et al. 2017, GRL 44)",
}

# 证据阶梯:每一级的达成要求(与 audit/ladder.py 的机器判定一一对应)
_LADDER_REQ = {
    "checked": "全部步骤 run_ok 通过(执行与产物契约完整)",
    "audited": "产物指纹齐备且全部指标重解析一致(provenance 审计完整)",
    "calibrated": "外部比对指标(GNSS/水准,gnss_rmse_mm)入账",
    "validated": "双链交叉验证(PS/SBAS)达契约阈值(且该阈值完成标定,非 PENDING)",
    "publishable": "跨环境复现记录与证据边界表",
}


def _fmt(v) -> str:
    """数值直排:一律 str(provenance 原值),绝不重排版(0.7 不写成 0.70)——
    保证正文任何数字都能在 provenance JSON 里逐字反查。"""
    return str(v)


def _cite(sid) -> str:
    """步骤级可溯引用标记(前端渲染为特殊样式)。"""
    return f"〔prov-{sid}〕"


def _cite_metric(m: dict) -> str:
    """指标级可溯引用:产物#字段,与审计面板「指标来源契约」同形。"""
    return f"〔prov-{m.get('source_artifact')}#{m.get('source_field')}〕"


def _th(contract: dict, key: str) -> dict | None:
    """台账条目归一化:兼容 audit.contract.Threshold 与 provenance 里的 dict 形态。"""
    t = (contract or {}).get(key)
    if t is None:
        return None
    if isinstance(t, dict):
        return {"value": t.get("value"), "source": t.get("source", ""),
                "ref": t.get("ref", ""), "status": str(t.get("status", "OK"))}
    return {"value": getattr(t, "value", None), "source": getattr(t, "source", ""),
            "ref": getattr(t, "ref", ""), "status": str(getattr(t, "status", "OK"))}


def _anchor(contract: dict, param: str) -> str:
    """参数依据锚点:台账有 ref 引台账原文(标注来源级与 PENDING),
    台账没有 → 社区参照注释或诚实的「本项目配置」。"""
    t = _th(contract, _PARAM_ALIAS.get(param, param))
    if t is not None and t.get("ref"):
        label = _SOURCE_LABEL.get(t["source"], t["source"] or "未注明来源")
        pending = ";PENDING 待标定" if t["status"].upper() == "PENDING" else ""
        return f"〔ref:{t['ref']}({label}{pending})〕"
    note = _COMMUNITY_NOTE.get(param)
    return f"〔ref:{note}〕" if note else "〔ref:本项目配置(阈值台账无此条目,无文献先例可引)〕"


def _params_of(step: dict) -> dict:
    return {k: v for k, v in (step.get("params") or {}).items()
            if not str(k).startswith("_") and k not in _RESOURCE_PARAMS}


# ---------------------------------------------------------------------------
# 各步骤的期刊化措辞(按 capability id 分派;方法 id 不认识时回落通用描述)
# ---------------------------------------------------------------------------

def _prose_acquire(sid, s, c):
    p = _params_of(s)
    head = ""
    if p.get("platform") is not None and p.get("scenes") is not None:
        window = f"(时间窗 {_fmt(p['dates'])})" if p.get("dates") else ""
        head = f"使用 {_fmt(p['platform'])} 影像 {_fmt(p['scenes'])} 景{window}{_cite(sid)},"
    tail = {
        "local_import": "自本地既有产品目录导入(来源目录以执行记录为准)",
        "asf_search_slc": "经 ASF 检索并下载原始 SLC〔ref:ASF Vertex / asf_search〕",
        "hyp3_submit": "提交 ASF HyP3 云端标准 InSAR 流程处理"
                       "〔ref:ASF HyP3 InSAR Product Guide〕",
        "nisar_import": "自本地 NISAR GUNW 解缠产品目录登记"
                        "(云端已完成解缠,跳过配准至解缠;不下载数据)",
    }.get(s.get("method"))
    if tail is None:
        return None
    return head + tail + "。"


def _prose_aux(sid, s, c):
    p = _params_of(s)
    tail = {
        "dem_copernicus": "参考 DEM 采用 Copernicus 全球产品",
        "dem_srtm": "参考 DEM 采用 SRTM",
        "dem_local": "参考 DEM 采用本地瓦片",
    }.get(s.get("method"))
    if tail is None:
        return None
    bits = [tail]
    if p.get("dem") is not None:
        bits.append(f"(`{_fmt(p['dem'])}`{_cite(sid)})")
    if p.get("orbit") is not None:
        bits.append(f",轨道星历 `{_fmt(p['orbit'])}`{_cite(sid)}")
    return "".join(bits) + "。"


def _prose_coreg(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    if m == "isce2_tops_geom_esd":
        out = "影像配准采用 ISCE2 topsApp 几何配准并以增强谱分集(ESD)精化方位向偏移"
        if p.get("esd_coherence_threshold") is not None:
            out += (f",ESD 相干阈值 {_fmt(p['esd_coherence_threshold'])}{_cite(sid)}"
                    f"{_anchor(c, 'esd_coherence_threshold')}")
        return out + "。"
    if m == "isce2_stripmap_xcorr":
        return ("影像配准采用 ISCE2 stripmapApp 互相关配准(条带模式,"
                "参考/从影像与 DEM 路径以执行记录为准)。")
    if m == "snap_backgeocoding":
        return "影像配准采用 SNAP Back-Geocoding(DEM 辅助几何配准)。"
    return None


def _prose_ifg(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    if m == "isce2_ifg_multilook":
        out = "差分干涉图经 ISCE2 生成"
        if p.get("pairs") is not None:
            out += f",小基线网络共 {_fmt(p['pairs'])} 个干涉对{_cite(sid)}{_anchor(c, 'pairs')}"
        if p.get("range_looks") is not None and p.get("azimuth_looks") is not None:
            out += (f",距离向 × 方位向多视 {_fmt(p['range_looks'])} × "
                    f"{_fmt(p['azimuth_looks'])}{_cite(sid)}{_anchor(c, 'range_looks')}")
        return out + "。"
    if m == "isce2_stripmap_ifg":
        return "差分干涉图经 ISCE2 stripmapApp 分频谱→干涉→滤波段生成(条带链)。"
    if m == "snap_interferogram":
        return "差分干涉图经 SNAP 干涉工作流生成。"
    return None


def _prose_filter(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    if m == "goldstein":
        out = ("干涉图采用 Goldstein 自适应谱滤波抑噪"
               "〔ref:Goldstein & Werner (1998), GRL 25(21):4035-4038〕")
        if p.get("alpha") is not None:
            out += f",滤波强度 α = {_fmt(p['alpha'])}{_cite(sid)}{_anchor(c, 'alpha')}"
        return out + "。"
    if m == "boxcar":
        return f"干涉图采用 boxcar 均值滤波{_cite(sid)}(简单快速,边缘细节有损)。"
    if m == "none":
        return "干涉图未做滤波(保留全分辨率相位细节)。"
    if m == "isce2_stripmap_filter":
        return "滤波由 ISCE2 stripmapApp filter 单步完成(条带链,自适应滤波)。"
    return None


def _prose_unwrap(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    if m in ("snaphu_mcf", "snaphu_smooth", "isce2_stripmap_unwrap_snaphu"):
        how = {
            "snaphu_mcf": "SNAPHU 统计代价网络流算法,最小费用流(MCF)初始化",
            "snaphu_smooth": "SNAPHU 统计代价网络流算法(SMOOTH 完整优化)",
            "isce2_stripmap_unwrap_snaphu": "ISCE2 stripmapApp 内置 SNAPHU(含地理编码)",
        }[m]
        out = (f"相位解缠采用 {how}"
               "〔ref:Chen & Zebker (2000/2001), JOSA A 17:401-414 / 18:338-351;"
               "Chen & Zebker (2002), IEEE TGRS 40:1709-1719〕")
        if p.get("cost_mode") is not None:
            out += f",统计代价模式 {_fmt(p['cost_mode'])}{_cite(sid)}{_anchor(c, 'cost_mode')}"
        if p.get("min_coherence") is not None:
            out += (f",相干性掩膜阈值 {_fmt(p['min_coherence'])}{_cite(sid)}"
                    f"{_anchor(c, 'min_coherence')}")
        return out + "。"
    if m == "icu":
        return "相位解缠采用 ISCE2 ICU 区域增长算法(大范围低相干区易产生解缠孤岛)。"
    return None


def _prose_invert(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    if m == "mintpy_sbas":
        out = ("形变时序经 MintPy 小基线集(SBAS)方法反演"
               "〔ref:Berardino et al. (2002), IEEE TGRS 40(11):2375-2383;"
               "Yunjun et al. (2019), Comput. Geosci. 133:104331〕")
        if p.get("network") is not None:
            out += f",网络构建方式 `{_fmt(p['network'])}`{_cite(sid)}{_anchor(c, 'network')}"
        if p.get("max_temporal_baseline") is not None:
            out += (f",最大时间基线 {_fmt(p['max_temporal_baseline'])} 天{_cite(sid)}"
                    f"{_anchor(c, 'max_temporal_baseline')}")
        out += ";参考点选取未入账本,以 MintPy 运行配置为准(诚实缺席)"
        return out + "。"
    if m == "pystamps_ps":
        # 滑坡技能包纪律:「LOS → 坡向投影假设必须进报告」;适用性/几何可见性
        # 引文与场景知识段(landslide SKILL)一致,数字只进 ref 锚点
        return ("形变时序经永久散射体(PS)方法反演"
                "〔ref:Ferretti et al. (2001), IEEE TGRS 39(1):8-20〕;"
                "PS 测得的是 LOS 分量,换算坡向/垂直形变须显式声明投影假设"
                "〔ref:Colesanti & Wasowski (2006), Engineering Geology 88:173-199 "
                "—— 缓慢滑坡 InSAR 适用性标准引文;LOS 对近南北向运动几乎不敏感,"
                "升降轨互补是 PSI 滑坡应用惯例〕。")
    if m == "dolphin_ps_ds":
        return ("相位连接采用 OPERA Dolphin 的 PS/DS 混合估计"
                "〔ref:Staniewicz et al. (2024), JOSS 9:6997〕;"
                "本步只产出缠绕相位/干涉栈,速度场仍由后续 MintPy 步骤计算,"
                "不把 Dolphin 输出当作 velocity.h5。")
    return None


def _prose_correct(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    how = {
        "tropo_era5_pyaps": "对流层延迟采用 ERA5 再分析资料经 PyAPS 校正"
                            "〔ref:Jolivet et al. (2011), GRL 38, L17311;"
                            "Jolivet et al. (2014), JGR 119:2324-2341〕",
        "tropo_gacos": "对流层延迟采用 GACOS 产品校正"
                       "〔ref:Yu et al. (2018), JGR Solid Earth 123:9202-9222〕",
        "tropo_height_corr": "对流层延迟采用高程相关经验校正(降级方案,"
                             "无法区分与地形相关的真实形变,证据级别相应下调)"
                             "〔ref:Doin et al. (2009), J. Appl. Geophys. 69:35-50〕",
    }.get(m)
    if how is None:
        return None
    out = how
    if p.get("ramp") is not None:
        out += f";去斜方式 `{_fmt(p['ramp'])}`{_cite(sid)}{_anchor(c, 'ramp')}"
    if p.get("dem_error") is True:
        out += ";并作 DEM 误差校正〔ref:Fattahi & Amelung (2013), IEEE TGRS 51(7)〕"
    if p.get("solid_earth_tides") is False:
        out += ";未启用固体潮校正"
    return out + "。"


def _prose_model(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    if m == "step":
        out = "形变模型采用同震阶跃(Heaviside)拟合,避免把阶跃摊成虚假线性趋势"
        if p.get("step_date"):
            out += f",阶跃日期 {_fmt(p['step_date'])}{_cite(sid)}{_anchor(c, 'step_date')}"
        return out + "。"
    if m == "linear":
        return f"形变模型采用线性速率拟合{_cite(sid)}(蠕滑/震间准匀速场景的标准产品形态)。"
    if m == "poly_periodic":
        out = ("形变模型采用线性趋势 + 年/半年周期项"
               "〔ref:Daout et al. (2017), GRL 44;Li et al. (2019), "
               "Remote Sens. 11(9):1000〕")
        if p.get("periods") is not None:
            out += f",periods = {_fmt(p['periods'])}{_cite(sid)}{_anchor(c, 'periods')}"
        return out + "。"
    if m == "exponential":
        return ("形变模型采用指数衰减(震后黏弹性松弛形态;对数/指数在短观测窗内近似"
                "不可辨识,报告残差而非断言机理)〔ref:Tobita (2016), EPS 68:41;"
                "Sobrero et al. (2020), J. Geodesy 94:84〕。")
    return None


def _prose_figure(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    if m == "figure_journal":
        out = "成图按期刊规格输出"
        bits = []
        if p.get("dpi") is not None:
            bits.append(f"{_fmt(p['dpi'])} dpi{_cite(sid)}{_anchor(c, 'dpi')}")
        if p.get("cmap") is not None:
            bits.append(f"色标 `{_fmt(p['cmap'])}`{_cite(sid)}{_anchor(c, 'cmap')}")
        if p.get("format") is not None:
            bits.append(f"格式 `{_fmt(p['format'])}`{_cite(sid)}{_anchor(c, 'format')}")
        if bits:
            out += "(" + ",".join(bits) + ")"
        return out + "。"
    if m == "mintpy_geocode":
        return "成图仅作 MintPy 地理编码,不做排版。"
    if m == "gdal_warp":
        return "成图导出 GeoTIFF 供 GIS 使用。"
    return None


def _prose_qa(sid, s, c):
    p = _params_of(s)
    m = s.get("method")
    if m == "crossval_ps_sbas":
        out = "质检采用 PS/SBAS 双链交叉验证(本项目独有质量门)"
        if p.get("corr_threshold") is not None:
            out += (f",相关阈值 {_fmt(p['corr_threshold'])}{_cite(sid)}"
                    f"{_anchor(c, 'corr_threshold')}")
        out += ("〔ref:互检惯例参照:多处理器速度差 std 0.5–1.1 mm/yr"
                "(Terrafirma;RSE 256:112306, 2021)〕")
        return out + "。"
    if m == "loop_closure":
        return ("质检采用闭合环残差检查(只验解缠不验反演)"
                "〔ref:Yunjun et al. (2019) §3.2 式(8)-(9);图级剔除惯例 RMS > 1.5 rad"
                "(LiCSBAS p12_loop_thre;Morishita et al. 2020 §2.4.2)〕。")
    if m == "coherence_mask":
        return f"质检采用相干性掩膜检查{_cite(sid)}(最弱质检形态,仅筛除低相干像元)。"
    if m == "gnss_compare":
        out = "质检采用 InSAR 与 GNSS 站点 LOS 速度对比(不自造平差)"
        if p.get("gnss_csv"):
            out += f",GNSS 表 `{_fmt(p['gnss_csv'])}`{_cite(sid)}"
        return out + "。"
    return None


_PROSE = {
    1: _prose_acquire, 2: _prose_aux, 3: _prose_coreg, 4: _prose_ifg,
    5: _prose_filter, 6: _prose_unwrap, 7: _prose_invert, 8: _prose_correct,
    9: _prose_model, 10: _prose_figure, 11: _prose_qa,
}


def _prose_generic(sid, s):
    """未收录方法的兜底:如实罗列参数键值(数字仍全部来自 provenance)。"""
    params = _params_of(s)
    ptxt = ",".join(f"{k}={_fmt(v)}" for k, v in sorted(params.items()))
    return (f"处理方法 `{s.get('method')}`{_cite(sid)}"
            + (f"(参数:{ptxt})" if ptxt else "") + "。")


def _int_or_zero(k) -> int:
    try:
        return int(k)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 章节组装
# ---------------------------------------------------------------------------

def _chain_lines(provenance: dict, contract: dict) -> list[str]:
    steps = provenance.get("steps") or {}
    if not steps:
        return ["(本次运行没有任何步骤执行记录 —— 处理链描述无从生成,请先完成一次执行。)"]
    order = sorted(steps, key=_int_or_zero)
    lines: list[str] = []
    i = 0
    while i < len(order):
        sid = order[i]
        s = steps.get(sid) or {}
        if s.get("state") == "skipped":
            # 连续的云端跳过步骤合并成一句,附凭据核对结论(跳过 ≠ 免检)
            group = []
            while i < len(order) and (steps.get(order[i]) or {}).get("state") == "skipped":
                group.append(order[i])
                i += 1
            names = "、".join(str((steps.get(g) or {}).get("name") or f"步骤 {g}")
                              for g in group)
            span = f"{group[0]}–{group[-1]}" if len(group) > 1 else f"{group[0]}"
            ev = (steps.get(group[0]) or {}).get("cloud_evidence") or {}
            if ev.get("present"):
                sha = str(ev.get("sha256") or "")
                evtxt = (f"云端完成凭据:`{ev.get('path')}`"
                         f"(sha256 `{sha[:12]}…`,条目数 {_fmt(ev.get('entries'))})")
            else:
                evtxt = ("云端完成声明暂缺本地凭据(未找到 hyp3_manifest.json),"
                         "证据阶梯已如实降级")
            lines.append(f"- 第 {span} 步({names})由云端(ASF HyP3)标准流程完成,"
                         f"本地未重复执行;{evtxt}。")
            continue
        name = s.get("name") or f"步骤 {sid}"
        method = s.get("method")
        state = s.get("state")
        if state == "done":
            builder = _PROSE.get(_int_or_zero(sid))
            text = builder(sid, s, contract) if builder else None
            if text is None:
                text = _prose_generic(sid, s)
            lines.append(f"- **{name}**(方法 `{method}`{_cite(sid)}):{text}")
        elif state == "failed":
            fc = s.get("failure_class") or "unknown"
            lines.append(f"- **{name}**(方法 `{method}`{_cite(sid)}):执行失败"
                         f"(失败类别 `{fc}`)—— 本步及下游结果不可用,"
                         "方法描述止于失败点。")
        else:
            lines.append(f"- **{name}**(方法 `{method}`):未执行(状态 {state}),"
                         "不纳入方法描述。")
        i += 1
    return lines


def _pass_word(value, threshold) -> str:
    """门禁达标与否的措辞(纯比较,不产生新数字);无法比较时如实说明。"""
    try:
        return "达到" if float(value) >= float(threshold) else "未达到"
    except (TypeError, ValueError):
        return "无法比较"


def _metric_lines(provenance: dict, contract: dict) -> list[str]:
    metrics = provenance.get("metrics") or {}
    if not metrics:
        return []
    lines = ["## 质量指标与门禁", ""]

    # 关键门禁指标的期刊化陈述(值与阈值都可反查;文献量级只进 ref 锚点)
    m = metrics.get("unwrap_coverage")
    t = _th(contract, "unwrap_coverage")
    if m is not None and m.get("value") is not None:
        sent = f"- 解缠有效覆盖率为 {_fmt(m['value'])}{_cite_metric(m)}"
        if t is not None and t.get("value") is not None:
            sent += (f",{_pass_word(m['value'], t['value'])}质量门要求"
                     f"(阈值 {_fmt(t['value'])}{_anchor(contract, 'unwrap_coverage')})")
        lines.append(sent + "。")
    m = metrics.get("crossval_r")
    t = _th(contract, "corr_threshold")
    if m is not None and m.get("value") is not None:
        sent = f"- PS/SBAS 双链交叉验证相关系数为 {_fmt(m['value'])}{_cite_metric(m)}"
        if t is not None and t.get("value") is not None:
            sent += (f",{_pass_word(m['value'], t['value'])}契约阈值 "
                     f"{_fmt(t['value'])}{_anchor(contract, 'corr_threshold')}")
        lines.append(sent + "。")
    m = metrics.get("crossval_rmse_mm")
    if m is not None and m.get("value") is not None:
        lines.append(f"- 双链重叠区速度差 RMSE 为 {_fmt(m['value'])} mm{_cite_metric(m)}"
                     "〔ref:多处理器互检惯例量级 0.5–1.1 mm/yr(Terrafirma;"
                     "RSE 256:112306, 2021)——仅作量级对照,非达标判据〕。")
    m = metrics.get("gnss_rmse_mm")
    if m is not None and m.get("value") is not None:
        lines.append(f"- InSAR−GNSS 时序 RMSE 为 {_fmt(m['value'])} mm{_cite_metric(m)}"
                     "〔ref:文献可信区间 0.5–1.8 cm(Yunjun et al. 2019 §5.1 Fig. 8)〕。")

    lines.append("")
    lines.append("| 指标 | 值 | 来源 | 重解析 |")
    lines.append("|---|---|---|---|")
    for name, m in metrics.items():
        rp = {True: "✓", False: "✗", None: "—"}[m.get("reparsed_ok")]
        lines.append(f"| {name} | {_fmt(m.get('value'))} | "
                     f"`{m.get('source_artifact')}#{m.get('source_field')}` | {rp} |")
    lines.append("")
    lines.append("> 重解析 ✓ = 该数字已从产物文件二次解析核对(不是从日志转述);"
                 "指标之外的正文数字均直引 provenance。")
    return lines


def _figure_lines(provenance: dict) -> list[str]:
    arts = provenance.get("artifacts") or {}
    figs = sorted((aid, a) for aid, a in arts.items()
                  if isinstance(a, dict) and a.get("kind") == "FIGURE")
    if not figs:
        return []
    steps = provenance.get("steps") or {}
    lines = ["## 图件引用", ""]
    for i, (aid, a) in enumerate(figs, 1):
        pby = a.get("produced_by")
        sname = (steps.get(str(pby)) or {}).get("name") or "成图步骤"
        fp = str(a.get("fp") or "")
        fp_txt = f"`{fp[:24]}…`" if len(fp) > 24 else (f"`{fp}`" if fp else "未记录")
        lines.append(f"- 图 {i}(Figure {i} shows the exported product)取自产物 "
                     f"`{a.get('path')}`(art_id `{aid}`,第 {_fmt(pby)} 步"
                     f"「{sname}」生成{_cite(pby)}),指纹 {fp_txt},"
                     "可据此核对图件与本账本的对应关系。")
    lines.append("")
    lines.append("> 图内元素规范(参考点标记/色标单位与正方向/比例尺/经纬度轴)见 "
                 "reference/RESEARCH-pub-figures-2026-08-12.md 的检查单。")
    return lines


def _evidence_lines(provenance: dict, contract: dict) -> list[str]:
    ev = provenance.get("evidence") or {}
    level = ev.get("level") or provenance.get("evidence_level") or "未知"
    ladder = [str(x) for x in (ev.get("ladder") or [])]
    lines = ["## 证据边界", ""]
    lines.append(f"- 当前证据级别:**{level}**"
                 + (f"(阶梯:{' → '.join(ladder)})" if ladder else ""))
    for reason in ev.get("reasons") or []:
        lines.append(f"- {reason}")
    pending = sorted(k for k in (contract or {})
                     if (_th(contract, k) or {}).get("status", "OK").upper() == "PENDING")
    if pending:
        lines.append(f"- 待标定阈值:{', '.join(pending)}(标定完成前证据级别封顶 audited)")
    # 再往上还差什么:对阶梯上每个未达级别如实声明其达成要求(不越级声称)
    if ladder and level in ladder:
        for lv in ladder[ladder.index(level) + 1:]:
            req = _LADDER_REQ.get(lv)
            if req:
                lines.append(f"- 尚未达到 {lv}(该级要求:{req})。")
    srcs = ev.get("step_sources") or {}
    if srcs:
        by_origin: dict[str, list[str]] = {}
        for sid, entry in srcs.items():
            by_origin.setdefault(str((entry or {}).get("origin") or "unknown"), []).append(sid)
        summary = "、".join(f"{origin} {len(ids)} 步"
                            for origin, ids in sorted(by_origin.items()))
        lines.append(f"- 步骤证据来源:{summary}(逐步明细见 provenance.evidence.step_sources)")
        for sid in sorted(by_origin.get("missing", []), key=_int_or_zero):
            detail = (srcs.get(sid) or {}).get("detail") or "证据缺口"
            lines.append(f"  - 第 {sid} 步:{detail}")
    if provenance.get("simulated"):
        lines.append("- 本次为模拟执行:以上仅验证处理链结构与账本贯通性,"
                     "不构成任何科学结论。")
    return lines


def methods_markdown(provenance: dict, contract: dict | None = None) -> str:
    """provenance(+ 可选阈值台账)→ 论文方法章节 Markdown。

    纯函数:contract 缺省取 provenance['thresholds'](ledger 导出时已内嵌台账),
    也可显式传 audit.contract.load_contract() 的 Threshold 台账。
    """
    contract = contract if contract is not None else (provenance.get("thresholds") or {})
    env = provenance.get("environment") or {}
    tools = env.get("tools") or {}

    lines: list[str] = []
    lines.append("# 处理方法(自动生成草稿)")
    lines.append("")
    if provenance.get("simulated"):
        lines.append("> ⚠ 本次运行为**模拟执行**(引擎缺失环境下的演示),"
                     "所有数值均为结构演示值,不构成科学结论。")
        lines.append("")
    lines.append(f"- 运行标识:`{provenance.get('run_id') or '—'}`")
    lines.append(f"- 场景:{provenance.get('scenario') or '未指定'}")
    if env.get("python") or env.get("platform"):
        lines.append(f"- 处理环境:Python {env.get('python')} / {env.get('platform')}")
    if tools:
        tool_str = ", ".join(f"{k} {v}" for k, v in tools.items())
        lines.append(f"- 工具链:{tool_str}")
    if provenance.get("generated_at_utc"):
        lines.append(f"- 账本导出时间(UTC):{provenance['generated_at_utc']}")
    lines.append("")

    lines.append("## 处理链")
    lines.append("")
    lines.extend(_chain_lines(provenance, contract))
    lines.append("")

    metric_block = _metric_lines(provenance, contract)
    if metric_block:
        lines.extend(metric_block)
        lines.append("")

    figure_block = _figure_lines(provenance)
    if figure_block:
        lines.extend(figure_block)
        lines.append("")

    from insar_agent.report.product_level import methods_section
    level_block = methods_section(provenance.get("metrics"), provenance.get("artifacts"),
                                  simulated=bool(provenance.get("simulated")))
    lines.extend(level_block)
    lines.append("")

    lines.extend(_evidence_lines(provenance, contract))
    lines.append("")
    # 记号说明用「」引用而非〔〕原样示例:文档性提及不能与真实引用标记同形,
    # 否则数字可溯审计(与前端特殊样式渲染)会把说明文字误当引用解析
    lines.append("> 本节由 provenance 自动生成:「prov-N」样式引用指向第 N 步执行记录,"
                 "「prov-产物#字段」样式指向指标来源产物,「ref:…」为文献/依据锚点;"
                 "正文数字均可回溯到产物与命令轨迹。")
    return "\n".join(lines) + "\n"
