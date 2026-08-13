"""AI 识图质检:识图模型(vision_model)审流水线图件,发现人眼级质量问题。

定位(与 audit/ 其他模块的关系):
  - verify/runok 是「数值层」质检(文件存在/NaN/阈值),本模块是「视觉层」质检:
    解缠跳变、失相干噪声、参考点位置、色标失真 —— 这些问题数值指标看不见,
    传统上靠人工审图,这里交给识图模型做第一遍初筛(结论仅供参考,不进 gate)。
  - 领域判据来源:reference/RESEARCH-insar-step-knowledge-2026-08.md
    (§4 干涉条纹/多视伪影、§5 滤波十字伪影、§6 解缠台地/孤岛、§10 色标科学)。

结构纪律(对齐 brain/provider 的约束哲学,§3.4):
  - prompt 里给检查项闭集,响应做代码层值域校验 —— 约束保证结构不保证语义;
  - verdict/issue/severity 越界、JSON 坏形状 → BrainUnavailable(调用方走降级);
  - 图件超 4MB 不做静默缩放,直接拒绝并说明(FigureRejected)—— 送审的字节
    必须与落盘 hash 一致,缩放会让 provenance 里的 image_sha256 失去意义。

落盘:图件旁 <name>.aiqa.json(与出图元数据 sidecar <name>.json 同目录同风格),
含 model/image_sha256/时间戳/verdict/findings,可直接并入 provenance 叙事。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from insar_agent.brain.llm_config import vision_route_from_config
from insar_agent.brain.provider import BrainUnavailable, describe_image_stream
from insar_agent.core.fsio import atomic_write_text

#: 结果 schema 版本(落盘/接口的兼容锚点)
AIQA_SCHEMA = "aiqa/1"

#: 图件类型闭集(从文件名 + sidecar 推断;推断不出 = unknown,走通用检查)
FIGURE_KINDS = ("interferogram", "coherence", "unwrapped", "velocity", "unknown")

#: 结论闭集
VERDICTS = ("pass", "warn", "fail")

#: 单条发现的严重度闭集
SEVERITIES = ("info", "warn", "critical")

#: 检查项闭集:id → (中文名, 判据说明)。prompt 与响应校验共用同一张表,
#: 模型只允许输出这五个 issue 标识 —— 开放集会让前端徽章/统计无法归类。
CHECK_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("unwrap_jump", "解缠跳变/条纹不连续",
     "阶梯状 2π 整数倍相位台地、孤岛状相位块、条纹突然错断(非断层成因)"),
    ("decorrelation", "失相干大区块/噪声",
     "大面积盐椒状随机噪声或斑点,覆盖主要研究区;低相干区被外推成可疑的过分平滑场"),
    ("reference_point", "参考点异常",
     "参考点标注落在形变中心、图边缘、水体或明显噪声区(应在形变小且相干高的稳定区)"),
    ("colorbar_scale", "色标与量纲问题",
     "色标缺失/单位与量纲不符、发散量未零点居中、循环量未用循环色标、jet/rainbow 类感知失真色带"),
    ("processing_artifact", "异常纹理/处理伪影",
     "方向性拖尾、像元矩形化(多视比不当)、规则十字/网格状伪影(滤波过强)、条带、拼接缝"),
)

ISSUES = tuple(item[0] for item in CHECK_ITEMS)

#: 识图送审的图件字节上限:4MB。超限不做静默缩放(送审字节必须与 hash 一致),
#: 直接拒绝并提示改用 browse 档(2048px,engines/figures.py 三档契约)。
MAX_IMAGE_BYTES = 4 * 1024 * 1024

#: 送审支持的图像格式闭集(与 /api/figures 的扩展名闭集一致,svg 同样排除)
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}

#: 识图输出预算:findings 数条 + summary,512 容易截断(BrainTruncated 整体拒绝)
_MAX_TOKENS = 1024


class FigureRejected(ValueError):
    """图件本身不适合送审(超限/格式不支持/不可读)—— 与 LLM 可用性无关的入口拒绝。"""


# ---------------- 图件类型推断 ----------------

def infer_kind(name: str, meta: dict | None = None) -> str:
    """文件名 + sidecar 元数据 → FIGURE_KINDS 之一。

    顺序敏感:unwrapped 含 "wrap" 子串,解缠判定必须先于干涉图;
    文件名证据优先于 sidecar(名字由出图脚本按产物语义命名,更可靠)。
    """
    stem = Path(str(name)).stem.lower()
    if any(t in stem for t in ("unw", "unwrap")):
        return "unwrapped"
    if "coh" in stem or stem.endswith("_corr") or "coherence" in stem:
        return "coherence"
    if any(t in stem for t in ("ifg", "interf", "wrap", "fringe", "phase")):
        return "interferogram"
    if "vel" in stem:
        return "velocity"
    meta = meta if isinstance(meta, dict) else {}
    units = str(meta.get("units", "")).lower()
    title = str(meta.get("title", "")).lower()
    blob = f"{units} {title}"
    if "mm/yr" in blob or "m/yr" in blob or "velocity" in blob:
        return "velocity"
    if "coherence" in blob:
        return "coherence"
    if "unwrap" in blob:
        return "unwrapped"
    if units.startswith("rad") or "interferogram" in blob or "fringe" in blob:
        return "interferogram"
    return "unknown"


# ---------------- prompt 构造 ----------------

#: 各图件类型的领域判读要点(RESEARCH-insar-step-knowledge-2026-08 提炼)
_KIND_GUIDE: dict[str, tuple[str, str]] = {
    "interferogram": ("缠绕干涉图(wrapped interferogram)", """\
- 干涉条纹应连续、走向平滑;一个完整色周 = 2π 相位(约半个雷达波长的视线向形变)。
- 失相干区呈盐椒状随机噪声:小块属正常,大面积覆盖研究区才是问题(decorrelation)。
- 缠绕相位是循环量:色标应为循环色带(如 romaO);若 ±π 处出现生硬假边界或
  非循环色带(jet/彩虹)→ colorbar_scale。
- 方向性拖尾/像元被明显拉长 → 多视比与传感器几何不匹配(processing_artifact)。
- 规则的十字/网格状伪影 → Goldstein 滤波过强(α 过大)(processing_artifact)。
- 同震近场条纹密集属正常形变信号,不要误报;条纹糊成一片才值得提示。"""),
    "coherence": ("相干图(coherence)", """\
- 相干取值 0-1,应为单调 sequential 色标;色标发散、值域超出 0-1 → colorbar_scale。
- 大面积低相干(暗区)集中在植被/水体属常见现象(info);覆盖主要研究区或呈
  异常几何形状(整条带/整块突变)→ decorrelation(warn 以上)。
- 条带状、块状规则伪影(burst 边界、拼接缝)→ processing_artifact。"""),
    "unwrapped": ("解缠相位图(unwrapped phase)", """\
- 解缠相位应空间连续:阶梯状 2π 整数倍台地、孤岛状相位块是典型解缠错误
  (unwrap_jump,critical)—— 快速判据:好的解缠对 2π 取模应能还原连续条纹。
- 沿断层的相位不连续可以是真形变;但水体/低相干区之外的大台阶要报 unwrap_jump。
- 低相干区被"解"出大片过分平滑的外推场 → 报 decorrelation 或 unwrap_jump(warn)。
- 若图上标注了参考点:参考点附近相位应接近 0(reference_point)。"""),
    "velocity": ("LOS 速度场(velocity)", """\
- 速度场有物理零点:应用发散色标且零点(白/浅色)居中;色标单位应为 mm/yr
  并注明 LOS 方向语义(正=朝卫星);缺单位/零点偏置/彩虹色带 → colorbar_scale。
- 参考点(常为黑方块 + "Ref" 标注)应落在形变小、相干高的稳定区;落在形变
  中心、图边缘、水体或噪声区 → reference_point。
- 大片斑点噪声/散点跳变 → decorrelation;条带、网格、拼接缝 → processing_artifact。
- 局部集中且边界自然的形变信号(盆地沉降、断层两侧反号)属正常,不要误报。"""),
    "unknown": ("类型未知的 InSAR 产物图件", """\
- 无法从文件名/元数据确定类型:按通用 InSAR 图件判读 —— 色标是否合理可读、
  是否有大面积噪声/失相干、是否有明显处理伪影(条带/网格/拼接缝)。
- 类型不明时不要虚构针对特定类型的问题(如没有参考点标注就不要报 reference_point)。"""),
}


def _meta_lines(meta: dict | None) -> str:
    """sidecar 元数据 → prompt 里的要点行;无可用字段时明确说明。"""
    meta = meta if isinstance(meta, dict) else {}
    parts: list[str] = []
    if meta.get("title"):
        parts.append(f"标题:{meta['title']}")
    if meta.get("units"):
        parts.append(f"数值单位:{meta['units']}")
    if meta.get("cmap"):
        parts.append(f"色标:{meta['cmap']}")
    vlim = meta.get("vlim")
    if isinstance(vlim, (list, tuple)) and len(vlim) == 2:
        parts.append(f"色标值域:{vlim[0]} ~ {vlim[1]}")
    ref = meta.get("ref_point")
    if isinstance(ref, (list, tuple)) and len(ref) == 2:
        parts.append(f"参考点(纬, 经):{ref[0]}, {ref[1]}(图上应有对应标注)")
    dr = meta.get("date_range")
    if isinstance(dr, (list, tuple)):
        dates = [str(d) for d in dr if d]
        if dates:
            parts.append(f"日期区间:{' → '.join(dates)}")
    if not parts:
        return "(无 sidecar 元数据:按图面信息判断,不要虚构元数据)"
    return "\n".join(f"- {p}" for p in parts)


def build_prompt(figure_kind: str, meta: dict | None = None) -> str:
    """按图件类型构造领域化质检 prompt(检查项闭集 + 严格 JSON 输出契约)。

    未知类型一律按 unknown 的通用检查处理(不抛:调用方可能直接传文件名推断值)。
    """
    kind_label, guide = _KIND_GUIDE.get(figure_kind, _KIND_GUIDE["unknown"])
    checks = "\n".join(f"- {cid}:{cname} —— {crit}"
                       for cid, cname, crit in CHECK_ITEMS)
    return f"""\
你是 InSAR(合成孔径雷达干涉测量)数据处理流水线的图件质检员。
待审图件类型:{kind_label}。请只依据图面可见的证据做出判断。

【图件元数据】(随图落盘的 sidecar,可信)
{_meta_lines(meta)}

【领域判读要点】
{guide}

【检查项闭集】逐项检查;findings 里的 issue 只允许使用以下五个标识,不得自创:
{checks}

【输出要求】只输出一个 JSON 对象,不要 markdown 代码围栏,不要任何多余文字:
{{"verdict": "pass" | "warn" | "fail",
 "findings": [{{"issue": "<五个标识之一>", "severity": "info" | "warn" | "critical",
               "detail": "<中文一两句:指出图中大致位置与判断依据>"}}],
 "summary": "<中文一句话总评>"}}

判级规则:无问题 → verdict="pass" 且 findings=[];仅有 info/warn 级发现 → "warn";
存在 critical 级发现或图件基本不可用 → "fail"。
纪律:不确定的不要编造;findings 只写确有图面证据的问题;正常的形变信号不是缺陷。"""


# ---------------- 响应解析与闭集校验 ----------------

def _extract_json(text: str) -> dict:
    """识图流式输出 → JSON 对象(识图通道无 response_format 保证,需自行剥壳)。

    容忍两种常见包装:markdown 代码围栏、JSON 前后附带说明文字;
    剥壳后仍不是合法 JSON 对象 → BrainUnavailable(坏形状,整体拒绝)。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        parsed = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise BrainUnavailable(f"识图质检响应不是 JSON:{t[:200]}")
        try:
            parsed = json.loads(t[start:end + 1])
        except json.JSONDecodeError as exc:
            raise BrainUnavailable(f"识图质检响应不是合法 JSON:{t[:200]}") from exc
    if not isinstance(parsed, dict):
        raise BrainUnavailable(f"识图质检响应顶层不是对象:{t[:200]}")
    return parsed


def parse_review(text: str) -> dict:
    """模型输出全文 → {"verdict", "findings", "summary"}(闭集校验后的规范形)。

    与 provider.complete_json 同一哲学:结构由代码层校验保证 —— verdict/issue/
    severity 越界、findings 坏形状、summary 缺失,一律 BrainUnavailable 整体拒绝
    (半坏的质检结论比没有结论更危险:徽章会骗人)。
    """
    data = _extract_json(text)
    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        raise BrainUnavailable(
            f"识图质检 verdict 越界:{verdict!r}(闭集 {'/'.join(VERDICTS)})")
    findings_raw = data.get("findings")
    if not isinstance(findings_raw, list):
        raise BrainUnavailable("识图质检响应缺 findings 数组(坏形状,整体拒绝)")
    findings: list[dict] = []
    for i, item in enumerate(findings_raw):
        if not isinstance(item, dict):
            raise BrainUnavailable(f"识图质检 findings[{i}] 不是对象(坏形状,整体拒绝)")
        issue = item.get("issue")
        if issue not in ISSUES:
            raise BrainUnavailable(
                f"识图质检 issue 越界:{issue!r}(闭集 {'/'.join(ISSUES)})")
        severity = item.get("severity")
        if severity not in SEVERITIES:
            raise BrainUnavailable(
                f"识图质检 severity 越界:{severity!r}(闭集 {'/'.join(SEVERITIES)})")
        detail = item.get("detail", "")
        if not isinstance(detail, str):
            raise BrainUnavailable(f"识图质检 findings[{i}].detail 不是字符串")
        findings.append({"issue": issue, "severity": severity, "detail": detail})
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise BrainUnavailable("识图质检响应缺 summary(坏形状,整体拒绝)")
    return {"verdict": verdict, "findings": findings, "summary": summary.strip()}


# ---------------- 执行与落盘 ----------------

def aiqa_path(image_path: Path) -> Path:
    """图件 → 质检结果 sidecar 路径(velocity.png → velocity.aiqa.json)。"""
    return Path(image_path).with_suffix(".aiqa.json")


def review_figure(home: Path, image_path: Path, meta: dict | None = None) -> dict:
    """对单张图件执行识图质检并把结果落盘在图件旁,返回完整结果记录。

    失败语义(调用方按类型分流):
      FigureRejected  —— 图件入口拒绝(格式不支持/不可读/超 4MB),不出网;
      BrainUnavailable —— 未配置识图模型 / 请求失败 / 响应坏形状或闭集越界。
    """
    image_path = Path(image_path)
    mime = _IMAGE_MIME.get(image_path.suffix.lower())
    if mime is None:
        raise FigureRejected(
            f"图件 {image_path.name} 格式不支持识图送审"
            f"(仅 {'/'.join(sorted(e.lstrip('.') for e in _IMAGE_MIME))})")
    try:
        data = image_path.read_bytes()
    except OSError as exc:
        raise FigureRejected(f"图件 {image_path.name} 不可读:{exc}") from exc
    if len(data) > MAX_IMAGE_BYTES:
        raise FigureRejected(
            f"图件 {image_path.name} 为 {len(data) / 1024 / 1024:.1f} MB,"
            f"超过 4MB 识图上限:暂不做自动缩放(送审字节必须与 provenance 哈希"
            f"一致),请改审 2048px 的 _browse 浏览档")
    route = vision_route_from_config(home)
    if route is None:
        raise BrainUnavailable("未配置识图模型,在模型设置里选择")
    kind = infer_kind(image_path.name, meta)
    prompt = build_prompt(kind, meta)
    image_data_url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    text = describe_image_stream(route, prompt=prompt, image_data_url=image_data_url,
                                 max_tokens=_MAX_TOKENS)
    review = parse_review(text)
    record = {
        "schema": AIQA_SCHEMA,
        "figure": image_path.name,
        "kind": kind,
        "verdict": review["verdict"],
        "findings": review["findings"],
        "summary": review["summary"],
        "model": route.model,
        "image_sha256": hashlib.sha256(data).hexdigest(),
        "image_bytes": len(data),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    atomic_write_text(aiqa_path(image_path),
                      json.dumps(record, ensure_ascii=False, indent=1))
    return record
