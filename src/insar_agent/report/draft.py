"""论文方法章节草稿:provenance 账本 → 事实闭集 → 骨架段落 →(可选)LLM 润色。

与 report/methods.py(结构化 Markdown 报告)互补:本模块产出**连续中文段落**,
可直接改写进论文方法章节。事实纪律(硬约束):

  1. 草稿中的每个数字/方法名/版本号只允许来自 build_facts 抽出的事实闭集,
     闭集之外一个字符的数值都不许出现;缺字段显式写「未记录」,绝不编造;
  2. 两段式防幻觉:骨架由纯代码拼接(无 LLM,单独可用);LLM 只做措辞润色,
     且润色稿必须通过双向数值校验(骨架数值一个不缺、润色稿不多出未知数值)
     与方法名保全校验,任一不过 → 拒绝润色稿、回退骨架(llm_polish=False);
  3. 模拟 run 强制附「不构成科学证据」句,润色稿丢失该句同样回退;
  4. LLM 不可用(未配置/网络失败/截断)→ 直接返回骨架,功能不依赖 LLM。

纯函数边界:build_facts / skeleton_text / numeric_tokens 不碰网络与文件系统;
draft_methods 只通过传入的 provider 触网。
"""

from __future__ import annotations

import json
import re

from insar_agent.brain.provider import BrainUnavailable, LLMProvider

#: 资源类参数不进方法章节(与 report/methods.py 同口径:线程数等与科学复现无关)
_RESOURCE_PARAMS = frozenset({"threads", "parallel_workers"})

#: 缺字段的统一占位词(任务纪律:显式「未记录」,绝不编)
MISSING = "未记录"

#: 模拟 run 的强制警示句(骨架必带;润色稿丢失即回退)
SIMULATED_SENTENCE = "本次运行为模拟执行,以上数值仅验证处理链结构与账本贯通性,不构成科学证据。"

#: 数值 token:整数/小数/版本号/日期/时刻的最大匹配段(0.6 / 2.6.3 / 2019-07-04 /
#: 09:12:00 都是单个 token)。骨架与润色稿用同一正则切分,保证双向校验同口径。
_NUM_RE = re.compile(r"\d+(?:[.:\-/]\d+)*")


def numeric_tokens(text: str) -> set[str]:
    """文本里的全部数值 token 集合(润色校验的唯一数字口径)。"""
    return set(_NUM_RE.findall(text or ""))


# ---------------------------------------------------------------------------
# ① 事实闭集抽取(账本 → facts;缺字段显式「未记录」)
# ---------------------------------------------------------------------------

def _clean_params(params: dict | None) -> dict:
    """关键参数:剔除内部键(_ 前缀)与资源类参数,其余原值保留。"""
    return {k: v for k, v in (params or {}).items()
            if not str(k).startswith("_") and k not in _RESOURCE_PARAMS}


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


def _int_or_zero(k) -> int:
    try:
        return int(k)
    except (TypeError, ValueError):
        return 0


def build_facts(provenance: dict) -> dict:
    """从 provenance 账本抽事实闭集(草稿允许使用的全部事实,仅此一处产生)。

    覆盖:场景/意图/步骤链(能力+方法+关键参数)/软件版本/数据规模/
    QA 指标与判定/证据级/干预次数/模拟标记。缺字段显式 MISSING,绝不编。
    """
    doc = provenance or {}
    env = doc.get("environment") or {}
    tools = env.get("tools") or {}

    steps: list[dict] = []
    raw_steps = doc.get("steps") or {}
    for sid in sorted(raw_steps, key=_int_or_zero):
        s = raw_steps.get(sid) or {}
        steps.append({
            "step_id": _int_or_zero(sid),
            "name": s.get("name") or MISSING,
            "capability": s.get("capability") or MISSING,
            "method": s.get("method") or MISSING,
            "state": s.get("state") or MISSING,
            "failure_class": s.get("failure_class"),
            "params": _clean_params(s.get("params")),
        })

    # 数据规模:只认账本步骤参数里的既有键(platform/scenes/dates/pairs),
    # 任何一键都可能缺 —— 缺就是 MISSING,绝不推算补数
    scale: dict = {"platform": MISSING, "scenes": MISSING,
                   "dates": MISSING, "pairs": MISSING}
    for s in steps:
        for key in ("platform", "scenes", "dates", "pairs"):
            if scale[key] == MISSING and s["params"].get(key) is not None:
                scale[key] = s["params"][key]
    scale["artifact_count"] = len(doc.get("artifacts") or {})

    metrics = [{
        "name": name,
        "value": (m or {}).get("value"),
        "unit": (m or {}).get("unit") or "",
    } for name, m in (doc.get("metrics") or {}).items()]

    interventions = doc.get("interventions")
    return {
        "run_id": doc.get("run_id") or MISSING,
        "scenario": doc.get("scenario") or MISSING,
        "intent": _intent_text(doc.get("intent")),
        "simulated": bool(doc.get("simulated")),
        "generated_at_utc": doc.get("generated_at_utc") or MISSING,
        "python": env.get("python") or MISSING,
        "platform": env.get("platform") or MISSING,
        "tools": {str(k): str(v) for k, v in tools.items()},
        "steps": steps,
        "data_scale": scale,
        "metrics": metrics,
        "qa_status": (doc.get("qa") or {}).get("status") or MISSING,
        "evidence_level": (doc.get("evidence_level")
                           or (doc.get("evidence") or {}).get("level") or MISSING),
        "interventions": len(interventions) if isinstance(interventions, list) else 0,
    }


# ---------------------------------------------------------------------------
# ② 事实骨架(纯代码拼接,确定性;没有 LLM 也是完整可用的草稿)
# ---------------------------------------------------------------------------

def _fmt(v) -> str:
    """事实值直排:标量 str() 原样(0.7 不重排版为 0.70),复合值 canonical JSON
    —— 保证草稿里任何数字都能在事实闭集里逐字反查。"""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _params_phrase(params: dict) -> str:
    if not params:
        return ""
    body = ",".join(f"{k}={_fmt(v)}" for k, v in sorted(params.items()))
    return f"(关键参数:{body})"


def _step_sentence(s: dict) -> str | None:
    """单步 → 一句方法描述;不纳入方法章节的状态返回 None。"""
    name, method, state = s["name"], s["method"], s["state"]
    if state == "done":
        return f"{name}采用 {method} 方法{_params_phrase(s['params'])}"
    if state == "skipped":
        return f"{name}(方法 {method})由云端/缓存完成,本地未重复执行"
    if state == "failed":
        fc = s.get("failure_class") or MISSING
        return (f"{name}(方法 {method})执行失败(失败类别 {fc}),"
                "处理链的方法描述止于该步")
    return None  # pending 等未执行状态不进方法章节(如实不描述)


def skeleton_text(facts: dict) -> str:
    """事实闭集 → 中文方法章节骨架(确定性:同 facts 必得同文本)。"""
    f = facts
    scale = f["data_scale"]
    paras: list[str] = []

    # 段 1:数据与场景
    paras.append(
        f"本研究的 InSAR 数据处理由 InSAR-Agent 自动化流水线执行并全程记账"
        f"(运行标识 {f['run_id']},账本导出时间 {f['generated_at_utc']} UTC)。"
        f"应用场景为 {f['scenario']},研究意图:{f['intent']}。"
        f"数据规模:影像平台 {_fmt(scale['platform'])},影像 {_fmt(scale['scenes'])} 景,"
        f"时间窗 {_fmt(scale['dates'])},干涉对 {_fmt(scale['pairs'])} 对,"
        f"入账产物 {scale['artifact_count']} 项。")

    # 段 2:处理链(逐步方法 + 关键参数)
    sentences = [t for t in (_step_sentence(s) for s in f["steps"]) if t]
    if sentences:
        paras.append(f"处理链共 {len(f['steps'])} 步。" + ";".join(sentences) + "。")
    else:
        paras.append("本次运行没有可纳入方法描述的已执行步骤(处理链记录为空或全部未执行)。")

    # 段 3:软件环境
    tools_line = ("、".join(f"{k} {v}" for k, v in sorted(f["tools"].items()))
                  if f["tools"] else MISSING)
    paras.append(f"处理环境:Python {f['python']}({f['platform']});"
                 f"工具链版本:{tools_line}。")

    # 段 4:质量指标与判定
    if f["metrics"]:
        metric_bits = []
        for m in sorted(f["metrics"], key=lambda x: x["name"]):
            value = _fmt(m["value"]) if m["value"] is not None else MISSING
            unit = f" {m['unit']}" if m["unit"] else ""
            metric_bits.append(f"{m['name']} = {value}{unit}")
        metric_line = ",".join(metric_bits)
    else:
        metric_line = MISSING
    paras.append(f"质量控制:QA 判定为 {f['qa_status']};质量指标:{metric_line};"
                 f"证据级别为 {f['evidence_level']}(六级证据阶梯)。")

    # 段 5:干预与可复现性
    n = f["interventions"]
    iv = (f"执行期间共 {n} 次人工干预,逐条记录于账本" if n
          else "执行全程无人工干预(全自动)")
    paras.append(f"{iv};全部参数、命令行与产物指纹随附 provenance 账本,"
                 "本节数字均可回溯到对应执行记录。")

    # 段 6:模拟 run 强制警示(任务硬约束)
    if f["simulated"]:
        paras.append(SIMULATED_SENTENCE)
    return "\n\n".join(paras)


def facts_used_list(facts: dict) -> list[str]:
    """事实闭集 → 扁平「键=值」清单(API 回给前端/测试,便于逐条反查)。"""
    f = facts
    out = [
        f"run_id={f['run_id']}",
        f"scenario={f['scenario']}",
        f"intent={f['intent']}",
        f"simulated={'true' if f['simulated'] else 'false'}",
        f"generated_at_utc={f['generated_at_utc']}",
        f"python={f['python']}",
        f"platform={f['platform']}",
        f"qa_status={f['qa_status']}",
        f"evidence_level={f['evidence_level']}",
        f"interventions={f['interventions']}",
    ]
    out.extend(f"tool.{k}={v}" for k, v in sorted(f["tools"].items()))
    for key in ("platform", "scenes", "dates", "pairs", "artifact_count"):
        out.append(f"data_scale.{key}={_fmt(f['data_scale'][key])}")
    out.append(f"steps.count={len(f['steps'])}")
    for s in f["steps"]:
        sid = s["step_id"]
        out.append(f"step{sid}.method={s['method']}")
        out.append(f"step{sid}.state={s['state']}")
        out.extend(f"step{sid}.param.{k}={_fmt(v)}"
                   for k, v in sorted(s["params"].items()))
    out.extend(f"metric.{m['name']}={_fmt(m['value']) if m['value'] is not None else MISSING}"
               for m in sorted(f["metrics"], key=lambda x: x["name"]))
    return out


# ---------------------------------------------------------------------------
# ③ LLM 润色 + 双向校验(不过即回退骨架)
# ---------------------------------------------------------------------------

_POLISH_SYSTEM = (
    "你是论文方法章节的措辞润色器。输入是一份由账本事实拼接的中文方法章节骨架,"
    "其中每个数字与方法名都经过溯源审计。你只允许改写措辞使行文更符合期刊习惯,"
    "严禁增加、删除或改动任何数值、方法名、版本号、run 标识与「未记录」占位,"
    "严禁补充骨架之外的任何事实或数字;骨架若含「不构成科学证据」警示句,必须原样保留。"
    '只输出 JSON:{"draft": "<润色后的全文>"},不要输出其他字段。'
)

#: 润色输入上限(骨架超长时不送 LLM,直接用骨架 —— 截断送审会让校验失去意义)
_POLISH_INPUT_MAX = 6000


def _polish_valid(skeleton: str, polished: str, facts: dict) -> bool:
    """润色稿三重校验:数值双向一致 / 方法名保全 / 模拟警示句保全。"""
    if not isinstance(polished, str) or not polished.strip():
        return False
    # 数值 token 双向:骨架的每个数值必须原样出现;润色稿不得多出未知数值
    if numeric_tokens(skeleton) != numeric_tokens(polished):
        return False
    # 方法名保全:骨架里出现过的方法 id 一个不许丢(改写措辞可以,改方法名不行)
    for s in facts["steps"]:
        method = s["method"]
        if method and method != MISSING and method in skeleton and method not in polished:
            return False
    # 模拟 run 的警示句不许被润掉
    if facts["simulated"] and "不构成科学证据" not in polished:
        return False
    return True


def draft_methods(provider: LLMProvider | None, facts: dict) -> dict:
    """facts → {"draft": str, "llm_polish": bool, "facts_used": [...]}。

    骨架是保底且单独可用;LLM 润色是增强:未配置/失败/校验不过一律回退骨架。
    """
    skeleton = skeleton_text(facts)
    result = {"draft": skeleton, "llm_polish": False,
              "facts_used": facts_used_list(facts)}
    if provider is None or not provider.enabled or len(skeleton) > _POLISH_INPUT_MAX:
        return result
    try:
        data = provider.complete_json(system=_POLISH_SYSTEM, user=skeleton,
                                      max_tokens=2048)
    except BrainUnavailable:
        return result
    polished = data.get("draft")
    if _polish_valid(skeleton, polished, facts):
        result["draft"] = polished
        result["llm_polish"] = True
    return result
