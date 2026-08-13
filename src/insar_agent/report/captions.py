"""论文图注(figure caption):图件元数据 sidecar + provenance 账本 → 双语期刊风格图注。

与 report/draft.py(方法章节草稿)同一事实纪律范式:

  1. 图注中的每个数字/方法名/单位/色标名只允许来自 build_caption_facts 抽出的
     事实闭集,闭集之外一个数值都不许出现;缺项显式「未记录 / not recorded」,绝不编;
  2. 两段式防幻觉:双语骨架由纯代码拼接(确定性,中英数字同源);LLM 只润色措辞,
     且润色稿逐语种通过三重校验(数值双向一致 / 方法名保全 / 模拟警示保全),
     任一语种任一项不过 → 双语一并回退骨架(llm_polish=False),绝不让中英事实脱节;
  3. 模拟 run 的图注强制附双语警示,润色稿丢失即回退(draft.py 同律);
  4. LLM 不可用(未配置/网络失败/截断)→ 直接返回骨架,功能不依赖 LLM。

数值口径直接复用 draft.numeric_tokens(同一正则切分,双向校验同口径)。

模块边界:build_caption_facts / caption_skeleton / generate_caption 不碰文件系统
(generate_caption 只经传入 provider 触网);文件 IO(locate_figure / load_caption /
save_caption)集中在模块尾部,供 api/report_router.py 薄接线。
"""

from __future__ import annotations

import json
from pathlib import Path

from insar_agent.brain.provider import BrainUnavailable, LLMProvider
from insar_agent.core.fsio import atomic_write_text
from insar_agent.report.draft import MISSING, numeric_tokens

#: 资源类参数不进图注(与 report/draft.py 同口径:与科学复现无关)
_RESOURCE_PARAMS = frozenset({"threads", "parallel_workers"})

#: 模拟 run 的强制双语警示(骨架必带;润色稿丢失核心短语即回退)
SIMULATED_ZH = "(模拟运行产物,不构成科学证据。)"
SIMULATED_EN = "(Simulated run; not scientific evidence.)"

#: 缺项占位词的英文对应(facts 统一存中文 MISSING,英文骨架渲染时映射)
MISSING_EN = "not recorded"

#: 润色输入上限(双语骨架合计;超长不送 LLM —— 截断送审会让校验失去意义)
_POLISH_INPUT_MAX = 4000


def _int_or_zero(k) -> int:
    try:
        return int(k)
    except (TypeError, ValueError):
        return 0


def _clean_params(params: dict | None) -> dict:
    """关键参数:剔除内部键(_ 前缀)与资源类参数(draft._clean_params 同口径)。"""
    return {k: v for k, v in (params or {}).items()
            if not str(k).startswith("_") and k not in _RESOURCE_PARAMS}


def _fmt(v) -> str:
    """事实值直排:标量 str() 原样(0.7 不重排版为 0.70),复合值 canonical JSON
    —— 保证图注里任何数字都能在事实闭集里逐字反查(draft._fmt 同口径)。"""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _intent_text(intent) -> str:
    """intent 字段兼容 dict(goal/text/intent 键)与字符串;空 → 未记录。"""
    if isinstance(intent, dict):
        for key in ("goal", "text", "intent"):
            v = intent.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return MISSING
    if isinstance(intent, str) and intent.strip():
        return intent.strip()
    return MISSING


# ---------------------------------------------------------------------------
# ① 图件事实闭集(sidecar 元数据 + 账本 → facts;缺项显式「未记录」)
# ---------------------------------------------------------------------------

def build_caption_facts(figure_meta: dict | None, provenance: dict | None,
                        step: int | None = None) -> dict:
    """图注允许使用的全部事实,仅此一处产生。

    figure_meta:/api/figures 同口径读到的图件 sidecar(engines/figures.py 落盘,
    含 title/units/cmap/vlim/date_range/ref_point/step/params,任一可缺);
    provenance:core/ledger.export_provenance 的账本;step:图件产物行的
    produced_by 步骤号(sidecar 自带 step 时以 sidecar 为准 —— 与图同源更可信)。
    """
    meta = figure_meta if isinstance(figure_meta, dict) else {}
    doc = provenance or {}
    raw_steps = doc.get("steps") or {}

    step_id = meta.get("step") if isinstance(meta.get("step"), int) else step
    fig_step = (raw_steps.get(str(step_id)) or {}) if step_id is not None else {}

    # 卫星平台与时间窗兜底:只认账本步骤参数里的既有键(platform/dates),缺就是缺
    platform = MISSING
    dates_param = None
    methods: list[dict] = []
    for sid in sorted(raw_steps, key=_int_or_zero):
        s = raw_steps.get(sid) or {}
        params = _clean_params(s.get("params"))
        if platform == MISSING and params.get("platform") is not None:
            platform = params["platform"]
        if dates_param is None and params.get("dates") is not None:
            dates_param = params["dates"]
        # 处理链:done 步的方法与关键参数;出图步骤本身单列(骨架的导出句),不重复进链
        if s.get("state") == "done" and _int_or_zero(sid) != (step_id if step_id is not None
                                                              else -1):
            methods.append({"step_id": _int_or_zero(sid),
                            "name": s.get("name") or MISSING,
                            "method": s.get("method") or MISSING,
                            "params": params})

    # 时间窗:sidecar date_range 优先(与图同源);缺则回落账本 dates 参数;再缺 MISSING
    window: list[str] | str = MISSING
    dr = meta.get("date_range")
    if isinstance(dr, (list, tuple)):
        vals = [_fmt(v) for v in dr if v]
        if vals:
            window = vals[:2]
    if window == MISSING and dates_param is not None:
        window = [_fmt(dates_param)]

    ref = meta.get("ref_point")
    ref_point = (list(ref) if isinstance(ref, (list, tuple)) and len(ref) == 2
                 and all(isinstance(x, (int, float)) for x in ref) else None)
    vl = meta.get("vlim")
    vlim = (list(vl) if isinstance(vl, (list, tuple)) and len(vl) == 2
            and all(isinstance(x, (int, float)) for x in vl) else None)

    intent = _intent_text(doc.get("intent"))
    return {
        "title": meta.get("title") or MISSING,          # 产物类型/标题(sidecar)
        "units": meta.get("units") or MISSING,
        "cmap": meta.get("cmap") or MISSING,            # 实际所用色标(含兜底记录)
        "vlim": vlim,                                   # [下限, 上限] 或 None
        "time_window": window,                          # [起, 止] / [单值] / MISSING
        "ref_point": ref_point,                         # [lat, lon] 或 None
        "step_id": step_id,
        "step_method": fig_step.get("method") or MISSING,   # 出图方法
        "figure_params": _clean_params(meta.get("params")   # 出图参数(dpi/cmap/format)
                                       if isinstance(meta.get("params"), dict) else None),
        "scenario": doc.get("scenario") or MISSING,
        # 区域/研究对象:意图文本优先,缺则回落会话名(任务口径:区域或会话名)
        "subject": intent if intent != MISSING else (doc.get("session_id") or MISSING),
        "run_id": doc.get("run_id") or MISSING,
        "platform": platform,
        "methods": methods,
        "metrics": [{"name": name,
                     "value": (m or {}).get("value"),
                     "unit": (m or {}).get("unit") or ""}
                    for name, m in (doc.get("metrics") or {}).items()],
        "qa_status": (doc.get("qa") or {}).get("status") or MISSING,
        "simulated": bool(doc.get("simulated")),
    }


# ---------------------------------------------------------------------------
# ② 双语骨架(纯代码拼接,确定性;中英逐句对应,数字集合逐字相同)
# ---------------------------------------------------------------------------

def _miss(v: str, lang: str) -> str:
    """缺项占位的语种渲染:facts 统一存中文 MISSING,英文骨架显示 not recorded。"""
    return MISSING_EN if lang == "en" and v == MISSING else v


def _params_inline(params: dict) -> str:
    """参数 k=v 逗号串(排序确定;两语种同一渲染,保证数字集合逐字相同)。"""
    return ", ".join(f"{k}={_fmt(v)}" for k, v in sorted(params.items()))


def _vlim_phrase(vlim: list, lang: str) -> str:
    """值域短语:对称限幅渲染 ±上限(字符串级判断,不重排数字),否则「下 至 上」。"""
    lo, hi = _fmt(vlim[0]), _fmt(vlim[1])
    if lo == "-" + hi:
        return f"±{hi}"
    return f"{lo} 至 {hi}" if lang == "zh" else f"{lo} to {hi}"


def _window_phrase(window, lang: str) -> str:
    if window == MISSING:
        return "时间窗未记录" if lang == "zh" else "time window not recorded"
    joiner = " 至 " if lang == "zh" else " to "
    return ("时间窗 " if lang == "zh" else "time window ") + joiner.join(window)


def caption_skeleton(facts: dict, lang: str = "zh") -> str:
    """事实闭集 → 单段图注骨架(确定性:同 facts 必得同文本;lang 只换措辞,
    中英渲染同一批事实值,numeric_tokens(zh) == numeric_tokens(en) 是模块不变量)。"""
    if lang not in ("zh", "en"):
        raise ValueError(f"lang 只支持 zh/en,得到 {lang!r}")
    f = facts
    zh = lang == "zh"
    parts: list[str] = []

    # 句 1:编号占位 + 标题(产物类型)+ 单位
    title, units = _miss(f["title"], lang), _miss(f["units"], lang)
    parts.append(f"图 X:{title}(单位:{units})。" if zh
                 else f"Figure X: {title} (units: {units}).")

    # 句 2:研究对象/区域(意图文本或会话名)
    subject = _miss(f["subject"], lang)
    parts.append(f"研究对象:{subject}。" if zh else f"Study subject: {subject}.")

    # 句 3:数据平台 + 时间窗
    platform = _miss(_fmt(f["platform"]), lang)
    win = _window_phrase(f["time_window"], lang)
    parts.append(f"数据基于 {platform} 平台影像,{win}。" if zh
                 else f"Data: {platform} imagery, {win}.")

    # 句 4:处理链(done 步的方法 + 关键参数;中文带步骤名词,英文只列方法 id)
    if f["methods"]:
        bits = []
        for m in f["methods"]:
            inline = _params_inline(m["params"])
            paren = f"({inline})" if inline else ""
            bits.append(f"{m['method']} {m['name']}{paren}" if zh
                        else f"{m['method']}{f' ({inline})' if inline else ''}")
        chain = "、".join(bits) if zh else ", ".join(bits)
        parts.append(f"处理方法:{chain}。" if zh else f"Processing: {chain}.")
    else:
        parts.append("处理方法:未记录。" if zh else "Processing: not recorded.")

    # 句 5:出图步骤与出图参数
    step_txt = _miss(_fmt(f["step_id"]) if f["step_id"] is not None else MISSING, lang)
    method = _miss(f["step_method"], lang)
    fp = _params_inline(f["figure_params"])
    tail = f",{fp}" if (fp and zh) else (f", {fp}" if fp else "")
    parts.append(f"图件由 InSAR-Agent 流水线第 {step_txt} 步导出(方法 {method}{tail})。"
                 if zh else
                 f"The figure was exported at pipeline step {step_txt}"
                 f" (method {method}{tail}).")

    # 句 6:色标与值域(色带含义 = 色标名 + 显示范围 + 单位,不越 sidecar 半步)
    cmap = _miss(f["cmap"], lang)
    if f["vlim"] is not None:
        vp = _vlim_phrase(f["vlim"], lang)
        parts.append(f"色标 {cmap},显示范围 {vp} {units}。" if zh
                     else f"Colour scale {cmap}, display range {vp} {units}.")
    else:
        parts.append(f"色标:{cmap}。" if zh else f"Colour scale: {cmap}.")

    # 句 7:参考点(engines/figures.py 的领域惯例:黑色方块标注)
    if f["ref_point"] is not None:
        lat, lon = _fmt(f["ref_point"][0]), _fmt(f["ref_point"][1])
        parts.append(f"参考点(图中黑色方块)位于 {lat}°N、{lon}°E。" if zh
                     else f"The reference point (black square) is at {lat}°N, {lon}°E.")
    else:
        parts.append("参考点未记录。" if zh else "Reference point not recorded.")

    # 句 8:QA 相关值(指标按名排序 + 整体判定)
    qa = _miss(f["qa_status"], lang)
    if f["metrics"]:
        bits = []
        for m in sorted(f["metrics"], key=lambda x: x["name"]):
            val = _fmt(m["value"]) if m["value"] is not None else _miss(MISSING, lang)
            bits.append(f"{m['name']} = {val}{' ' + m['unit'] if m['unit'] else ''}")
        joined = ",".join(bits) if zh else "; ".join(bits)
        parts.append(f"质量指标:{joined};QA 判定:{qa}。" if zh
                     else f"Quality metrics: {joined}; QA status: {qa}.")
    else:
        parts.append(f"质量指标未记录;QA 判定:{qa}。" if zh
                     else f"Quality metrics not recorded; QA status: {qa}.")

    # 句 9:模拟 run 强制警示(任务硬约束,双语)
    if f["simulated"]:
        parts.append(SIMULATED_ZH if zh else SIMULATED_EN)
    # 中文句号自带停顿,直拼;英文句间需空格
    return "".join(parts) if zh else " ".join(parts)


# ---------------------------------------------------------------------------
# ③ LLM 润色 + 三重校验(逐语种;任一不过 → 双语一并回退骨架)
# ---------------------------------------------------------------------------

_CAPTION_SYSTEM = (
    "你是论文图注(figure caption)的措辞润色器。输入 JSON 含同一事实闭集拼接的"
    "中文骨架 zh 与英文骨架 en。你只允许改写措辞使其更符合期刊图注习惯,"
    "严禁增加、删除或改动任何数值、方法名、单位、色标名、坐标与"
    "「未记录 / not recorded」占位;严禁补充骨架之外的任何事实或数字;"
    "骨架若含模拟运行警示(不构成科学证据 / not scientific evidence),必须双语保留。"
    '只输出 JSON:{"zh": "<润色后中文图注>", "en": "<polished English caption>"},'
    "不要输出其他字段。"
)


def _caption_valid(skeleton: str, polished, facts: dict, simulated_mark: str) -> bool:
    """单语种三重校验:数值双向一致 / 方法名保全 / 模拟警示保全(draft 同范式)。"""
    if not isinstance(polished, str) or not polished.strip():
        return False
    # 数值 token 双向:骨架数字一个不缺;润色稿一个未知数字不许多
    if numeric_tokens(skeleton) != numeric_tokens(polished):
        return False
    # 方法名保全:处理链与出图方法的 id 一个不许丢(措辞可改,方法名不行)
    named = [m["method"] for m in facts["methods"]] + [facts["step_method"]]
    for mid in named:
        if mid and mid != MISSING and mid in skeleton and mid not in polished:
            return False
    if facts["simulated"] and simulated_mark not in polished:
        return False
    return True


def generate_caption(provider: LLMProvider | None, facts: dict) -> dict:
    """facts → {"zh": str, "en": str, "llm_polish": bool}。

    双语骨架是保底且单独可用;LLM 润色是增强:未配置/失败/任一语种校验不过,
    一律双语回退骨架 —— 中英图注永远出自同一批事实。
    """
    zh = caption_skeleton(facts, "zh")
    en = caption_skeleton(facts, "en")
    result = {"zh": zh, "en": en, "llm_polish": False}
    if provider is None or not provider.enabled or len(zh) + len(en) > _POLISH_INPUT_MAX:
        return result
    try:
        data = provider.complete_json(
            system=_CAPTION_SYSTEM,
            user=json.dumps({"zh": zh, "en": en}, ensure_ascii=False),
            max_tokens=1024)
    except BrainUnavailable:
        return result
    pz, pe = data.get("zh"), data.get("en")
    if (_caption_valid(zh, pz, facts, "不构成科学证据")
            and _caption_valid(en, pe, facts, "not scientific evidence")):
        result.update(zh=pz, en=pe, llm_polish=True)
    return result


# ---------------------------------------------------------------------------
# ④ 图件定位与图注落盘(供 api/report_router.py 薄接线;IO 集中在此)
# ---------------------------------------------------------------------------

#: 图注 sidecar 后缀:velocity.png → velocity.caption.json(与 <name>.json 元数据并排)
CAPTION_SUFFIX = ".caption.json"

#: 图像扩展名闭集与三档后缀 —— 与 api/app.py 的 _IMAGE_MEDIA_TYPES/_TIER_SUFFIXES
#: 同口径(不反向 import API 层;守护测试锁两处一致)
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_TIER_SUFFIXES = ("_browse", "_thumb")

#: 图注文件读取上限(read_sidecar_meta 的 _SIDECAR_MAX_BYTES 同律:防坏文件拖垮)
_CAPTION_MAX_BYTES = 64 * 1024


def locate_figure(run: dict, artifacts: list[dict], name: str) -> tuple[Path, dict] | None:
    """按文件名定位该 run 的图件 → (绝对路径, 产物行);找不到/越界一律 None。

    解析口径与 GET /api/figures 逐条对齐(那套逻辑在 create_app 闭包内,无法
    import,此处按同一规则复刻):产物相对路径必须落在 run 工作区内(绝对路径/
    盘符/../ 穿越不放行);单文件图像产物按文件名匹配;目录型产物取目录内成员
    (resolve 后仍须落在产物目录内);基图在场的 _browse/_thumb 三档不是独立
    条目,不接受为图注对象。name 必须是纯文件名(路径成分直接拒绝)。
    """
    if (not name or Path(name).name != name
            or Path(name).suffix.lower() not in _IMAGE_EXTS):
        return None
    if not run.get("workspace"):
        return None
    base = Path(run["workspace"]).resolve()
    for art in artifacts:
        rel = Path(art["path"])
        if rel.is_absolute() or rel.drive:
            continue
        try:
            target = (base / rel).resolve()
            if target == base or not target.is_relative_to(base):
                continue
            if rel.suffix.lower() in _IMAGE_EXTS:
                if target.name == name and target.is_file():
                    return target, art
            elif target.is_dir():
                child = target / name
                if not (child.is_file() and child.resolve().is_relative_to(target)):
                    continue
                stem = child.stem
                if any(stem.endswith(sfx)
                       and any((target / (stem[:-len(sfx)] + ext)).is_file()
                               for ext in _IMAGE_EXTS)
                       for sfx in _TIER_SUFFIXES):
                    continue  # 三档归并口径:基图在场,浏览/缩略档不单独立目
                return child, art
        except OSError:
            continue  # 枚举窗口内文件被清理/句柄异常:该产物行跳过,绝不 500
    return None


def load_caption(figure: Path) -> dict | None:
    """读已落盘图注(<name>.caption.json)。

    容忍一切失败:缺文件/超限/坏 JSON/缺 zh|en 字段都返回 None
    (api/app.read_sidecar_meta 同律,端点绝不因坏文件而 500)。
    """
    path = figure.with_suffix(CAPTION_SUFFIX)
    try:
        if not path.is_file() or path.stat().st_size > _CAPTION_MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not (isinstance(data.get("zh"), str) and isinstance(data.get("en"), str)):
        return None
    return data


def save_caption(figure: Path, payload: dict) -> bool:
    """图注落盘图件旁(原子写);失败不炸端点,如实返回 False(draft._save_draft 同律)。"""
    try:
        atomic_write_text(figure.with_suffix(CAPTION_SUFFIX),
                          json.dumps(payload, ensure_ascii=False, indent=1))
        return True
    except OSError:
        return False
