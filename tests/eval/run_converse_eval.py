#!/usr/bin/env python3
"""converse 对话大脑中文评测 harness(金标集驱动,默认零出网)。

背景:Brain 的 converse 职责(pi 式对话:自由聊天 + 动作闭集调用)输出契约固定为
    {"reply": str, "action": null | {"type": <动作闭集之一>, ...args}}
其中 plan 带 {scenario, region?, timerange?},set_params 带 {step: 1-11, params: dict}。
本 harness 针对该契约开发,converse 落地后即插即用;未落地时优雅跳过。

三种运行方式(本文件不监听任何端口,自然满足「随机高位端口/绝不动 8873」约束):

  1. 金标集格式自检(零依赖、零出网,pytest 的格式锁定测试也复用这里的校验器):
       python tests/eval/run_converse_eval.py --contract-only

  2. mock 模式(默认;注入假 provider,只验管道,零出网):
       python tests/eval/run_converse_eval.py --mode mock
     converse 已落地 → 真 converse + 假 provider(oracle 按金标产出契约 JSON);
     未落地 / --pipeline-only → 纯管道自检(oracle 响应直接进判定器,应得 100%)。
     另有 --provider naive 阴性对照:全部回纯聊天,验证判定器确实会挂掉动作类条目。

  3. real 模式(真实 LLM 配置;显式双闸门,默认绝不运行、绝不出网):
       PowerShell> $env:INSAR_EVAL_REAL="1"
       PowerShell> python tests/eval/run_converse_eval.py --mode real --out report.json
     环境变量沿用 brain/provider.py 的真实配置:INSAR_LLM_BASE_URL / _API_KEY / _MODEL。
     未设 INSAR_EVAL_REAL=1、converse 未落地、或未配置 LLM 路由 → 打印原因后跳过(exit 0)。

判定纪律(对齐 brain/facade.py 的闭集/越界拒绝哲学):
  - 契约校验先行:动作类型越出闭集、plan 缺 scenario/越出场景闭集、set_params 的
    step 越出 1-11 或 params 非 dict —— 无论期望是什么一律判负(绝不奖励越界动作)。
  - 期望匹配在后:expect 可为单候选或候选列表(可接受集合),任一候选完全命中即通过;
    候选内 kind / action_type / scenario / step / must_contain(reply 子串,忽略大小写)
    全部满足才算命中。
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

# 允许不安装直接跑(与 tests/conftest.py 同款纪律):src 布局的一方包路径先进 sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

GOLDEN_PATH = Path(__file__).resolve().parent / "converse_golden.jsonl"

# ---- converse 输出契约闭集(与并行实现对齐;契约演进时同步改这里与 README) ----
ACTION_TYPES = ("plan", "execute", "status", "check_env", "list_data",
                "set_params", "set_method")
SCENARIOS = ("stripmap_coseismic", "quake", "permafrost", "landslide")
STEP_MIN, STEP_MAX = 1, 11

# ---- 金标集自身 schema 闭集 ----
TOP_FIELDS = {"id", "text", "expect", "note"}
EXPECT_FIELDS = {"kind", "action_type", "must_contain", "scenario", "step"}
KINDS = ("chat", "action")
CATEGORIES = ("chat", "env", "data", "plan", "param", "exec", "attack", "fuzzy")
_ID_RE = re.compile(r"^(chat|env|data|plan|param|exec|attack|fuzzy)-\d{2,}$")


# ======================================================================
# 金标集加载与格式校验(--contract-only 与 pytest 格式锁定测试共用)
# ======================================================================

def category_of(entry_id: str) -> str:
    """id 前缀即类别(schema 保证前缀在闭集内)。"""
    return entry_id.split("-", 1)[0]


def normalize_expect(expect) -> list[dict]:
    """expect 统一成候选列表:单 dict → [dict];list 原样。不做合法性校验。"""
    if isinstance(expect, dict):
        return [expect]
    if isinstance(expect, list):
        return [c for c in expect if isinstance(c, dict)]
    return []


def _is_step(value) -> bool:
    """步骤号合法性。bool 是 int 子类,step=true 不能被当成 1 混过去(闭集纪律)。"""
    return isinstance(value, int) and not isinstance(value, bool) \
        and STEP_MIN <= value <= STEP_MAX


def _validate_candidate(cand: dict, where: str) -> list[str]:
    """单个期望候选的闭集校验。"""
    errs: list[str] = []
    unknown = set(cand) - EXPECT_FIELDS
    if unknown:
        errs.append(f"{where}: expect 含闭集外字段 {sorted(unknown)}")
    kind = cand.get("kind")
    if kind not in KINDS:
        errs.append(f"{where}: kind 必须是 {KINDS} 之一,实际 {kind!r}")
        return errs
    if kind == "chat":
        for banned in ("action_type", "scenario", "step"):
            if banned in cand:
                errs.append(f"{where}: kind=chat 不允许携带 {banned}")
    else:
        atype = cand.get("action_type")
        if atype not in ACTION_TYPES:
            errs.append(f"{where}: action_type 必须在闭集 {ACTION_TYPES} 内,实际 {atype!r}")
            return errs
        if "scenario" in cand:
            if atype != "plan":
                errs.append(f"{where}: scenario 只能配 action_type=plan(实际 {atype})")
            elif cand["scenario"] not in SCENARIOS:
                errs.append(f"{where}: scenario 越出闭集 {SCENARIOS}:{cand['scenario']!r}")
        if "step" in cand:
            if atype not in ("set_params", "set_method"):
                errs.append(f"{where}: step 只能配 set_params/set_method(实际 {atype})")
            elif not _is_step(cand["step"]):
                errs.append(f"{where}: step 必须是 {STEP_MIN}-{STEP_MAX} 的整数,"
                            f"实际 {cand['step']!r}")
    mc = cand.get("must_contain")
    if mc is not None:
        if (not isinstance(mc, list) or not mc
                or not all(isinstance(s, str) and s for s in mc)):
            errs.append(f"{where}: must_contain 必须是非空字符串的非空列表")
    return errs


def validate_entry(obj, where: str) -> list[str]:
    """单条金标记录的 schema 校验(顶层闭集 + id 规则 + expect 语义)。"""
    if not isinstance(obj, dict):
        return [f"{where}: 每行必须是 JSON 对象,实际 {type(obj).__name__}"]
    errs: list[str] = []
    unknown = set(obj) - TOP_FIELDS
    if unknown:
        errs.append(f"{where}: 含闭集外顶层字段 {sorted(unknown)}")
    missing = TOP_FIELDS - set(obj)
    if missing:
        errs.append(f"{where}: 缺顶层字段 {sorted(missing)}")
        return errs
    if not isinstance(obj["id"], str) or not _ID_RE.match(obj["id"]):
        errs.append(f"{where}: id 必须形如 <类别>-<两位数字>,类别闭集 {CATEGORIES},"
                    f"实际 {obj['id']!r}")
    if not isinstance(obj["text"], str) or not obj["text"].strip():
        errs.append(f"{where}: text 必须是非空字符串")
    if not isinstance(obj["note"], str) or not obj["note"].strip():
        errs.append(f"{where}: note 必须是非空字符串(设计意图)")
    cands = normalize_expect(obj["expect"])
    if not cands:
        errs.append(f"{where}: expect 必须是候选对象或候选对象非空列表")
    if isinstance(obj["expect"], list) and len(obj["expect"]) != len(cands):
        errs.append(f"{where}: expect 列表内混入了非对象元素")
    for i, cand in enumerate(cands):
        errs.extend(_validate_candidate(cand, f"{where} expect[{i}]"))
    return errs


def load_golden(path: Path = GOLDEN_PATH) -> tuple[list[dict], list[str]]:
    """读金标集。返回 (条目列表, 错误列表);错误列表为空才可用于评测。"""
    if not Path(path).is_file():
        return [], [f"金标集不存在:{path}"]
    entries: list[dict] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    text = Path(path).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            errors.append(f"第 {lineno} 行: JSONL 不允许空行")
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"第 {lineno} 行: JSON 解析失败({exc})")
            continue
        where = f"第 {lineno} 行"
        errs = validate_entry(obj, where)
        errors.extend(errs)
        if errs:
            continue
        if obj["id"] in seen_ids:
            errors.append(f"{where}: id 重复:{obj['id']}")
            continue
        seen_ids.add(obj["id"])
        entries.append(obj)
    return entries, errors


def golden_stats(entries: list[dict]) -> dict:
    """分布统计:各类别条数、期望形态、动作类型覆盖。"""
    by_cat = Counter(category_of(e["id"]) for e in entries)
    action_types = Counter()
    scenario_cover = Counter()
    single, multi = 0, 0
    for e in entries:
        cands = normalize_expect(e["expect"])
        if isinstance(e["expect"], list):
            multi += 1
        else:
            single += 1
        for c in cands:
            if c["kind"] == "action":
                action_types[c["action_type"]] += 1
                if "scenario" in c:
                    scenario_cover[c["scenario"]] += 1
    return {
        "total": len(entries),
        "by_category": {c: by_cat.get(c, 0) for c in CATEGORIES},
        "expect_shape": {"单候选": single, "可接受集合": multi},
        "action_type_coverage": dict(action_types),
        "scenario_coverage": dict(scenario_cover),
    }


# ======================================================================
# 判定器:契约校验先行,期望匹配在后
# ======================================================================

def check_contract(resp) -> list[str]:
    """converse 输出契约校验。返回违规清单;非空即整条判负(绝不奖励越界动作)。"""
    if not isinstance(resp, dict):
        return [f"响应顶层不是 JSON 对象:{type(resp).__name__}"]
    problems: list[str] = []
    if not isinstance(resp.get("reply"), str):
        problems.append("缺 reply 或 reply 不是字符串")
    action = resp.get("action")
    if action is None:
        return problems
    if not isinstance(action, dict):
        problems.append(f"action 既不是 null 也不是对象:{type(action).__name__}")
        return problems
    atype = action.get("type")
    if atype not in ACTION_TYPES:
        problems.append(f"动作类型越出闭集 {ACTION_TYPES}:{atype!r}")
        return problems
    if atype == "plan":
        if action.get("scenario") not in SCENARIOS:
            problems.append(f"plan.scenario 越出闭集 {SCENARIOS}:{action.get('scenario')!r}")
    elif atype == "set_params":
        if not _is_step(action.get("step")):
            problems.append(f"set_params.step 越界(合法 {STEP_MIN}-{STEP_MAX}):"
                            f"{action.get('step')!r}")
        if not isinstance(action.get("params"), dict):
            problems.append("set_params.params 缺失或不是对象")
    elif atype == "set_method":
        if "step" in action and not _is_step(action.get("step")):
            problems.append(f"set_method.step 越界(合法 {STEP_MIN}-{STEP_MAX}):"
                            f"{action.get('step')!r}")
    return problems


def match_candidate(resp: dict, cand: dict) -> tuple[bool, str]:
    """响应与单个期望候选的匹配。返回 (是否命中, 未命中原因)。"""
    action = resp.get("action")
    reply = resp.get("reply") or ""
    if cand["kind"] == "chat":
        if action is not None:
            return False, f"期望纯聊天,实际产出动作 {action.get('type')!r}"
    else:
        if not isinstance(action, dict):
            return False, f"期望动作 {cand['action_type']},实际纯聊天"
        if action.get("type") != cand["action_type"]:
            return False, (f"动作类型不符:期望 {cand['action_type']},"
                           f"实际 {action.get('type')!r}")
        if "scenario" in cand and action.get("scenario") != cand["scenario"]:
            return False, (f"scenario 不符:期望 {cand['scenario']},"
                           f"实际 {action.get('scenario')!r}")
        if "step" in cand and action.get("step") != cand["step"]:
            return False, f"step 不符:期望 {cand['step']},实际 {action.get('step')!r}"
    for frag in cand.get("must_contain", ()):
        if frag.casefold() not in reply.casefold():
            return False, f"reply 缺少必需子串 {frag!r}"
    return True, "命中"


@dataclass
class Verdict:
    entry_id: str
    category: str
    passed: bool
    reason: str
    expected: str
    got: str


def _describe_candidate(cand: dict) -> str:
    if cand["kind"] == "chat":
        desc = "chat"
    else:
        extra = []
        if "scenario" in cand:
            extra.append(f"scenario={cand['scenario']}")
        if "step" in cand:
            extra.append(f"step={cand['step']}")
        desc = f"action:{cand['action_type']}" + (f"({', '.join(extra)})" if extra else "")
    if cand.get("must_contain"):
        desc += f"+含{cand['must_contain']}"
    return desc


def expected_summary(entry: dict) -> str:
    return " 或 ".join(_describe_candidate(c) for c in normalize_expect(entry["expect"]))


def _got_summary(resp) -> str:
    if not isinstance(resp, dict):
        return f"<非对象:{type(resp).__name__}>"
    action = resp.get("action")
    if action is None:
        return "chat"
    if not isinstance(action, dict):
        return "<action 非对象>"
    extra = [f"{k}={action[k]!r}" for k in ("scenario", "step") if k in action]
    return f"action:{action.get('type')}" + (f"({', '.join(extra)})" if extra else "")


def judge(entry: dict, resp) -> Verdict:
    """单条判定:契约违规直接判负;否则任一期望候选完全命中即通过。"""
    eid, cat = entry["id"], category_of(entry["id"])
    exp = expected_summary(entry)
    problems = check_contract(resp)
    if problems:
        return Verdict(eid, cat, False, "契约违规:" + ";".join(problems), exp,
                       _got_summary(resp))
    misses = []
    for cand in normalize_expect(entry["expect"]):
        ok, why = match_candidate(resp, cand)
        if ok:
            return Verdict(eid, cat, True, f"命中候选 {_describe_candidate(cand)}", exp,
                           _got_summary(resp))
        misses.append(why)
    return Verdict(eid, cat, False, "全部候选未命中:" + ";".join(misses), exp,
                   _got_summary(resp))


# ======================================================================
# 假 provider(mock 模式;鸭子类型对齐 brain/provider.LLMProvider 接口)
# ======================================================================

NAIVE_REPLY = "嗯嗯,我在呢,你说。"


def oracle_response(entry: dict) -> dict:
    """按金标第一候选构造契约 JSON(作弊 provider:判定管道自检应得 100%)。"""
    cand = normalize_expect(entry["expect"])[0]
    reply = "(oracle)收到。" + "".join(cand.get("must_contain", ()))
    if cand["kind"] == "chat":
        return {"reply": reply, "action": None}
    atype = cand["action_type"]
    action: dict = {"type": atype}
    if atype == "plan":
        action["scenario"] = cand.get("scenario", "quake")
        action["region"] = "示例区域"
        action["timerange"] = "2020-2023"
    elif atype == "set_params":
        action["step"] = cand.get("step", 6)
        action["params"] = {"min_coherence": 0.3}
    elif atype == "set_method":
        action["step"] = cand.get("step", 6)
        action["method"] = "icu"
    return {"reply": reply, "action": action}


class OracleProvider:
    """作弊 provider:从 prompt 里认出金标话术,产出该条期望的契约 JSON。

    接口鸭子类型对齐 insar_agent.brain.provider.LLMProvider(enabled +
    complete_json),可直接注入 Brain(provider=...);converse 内部提示词
    只要携带用户原文即可命中(长话术优先,防子串错配)。
    """

    enabled = True

    def __init__(self, entries: list[dict]):
        self._entries = sorted(entries, key=lambda e: len(e["text"]), reverse=True)

    def complete_json(self, *, system: str = "", user: str = "", max_tokens: int = 512) -> dict:
        haystack = f"{system}\n{user}"
        for e in self._entries:
            if e["text"] in haystack:
                return oracle_response(e)
        return {"reply": "(oracle)未在 prompt 中认出金标话术", "action": None}


class NaiveChatProvider:
    """阴性对照 provider:永远纯聊天。动作类条目应当全部被判定器挂掉。"""

    enabled = True

    def complete_json(self, *, system: str = "", user: str = "", max_tokens: int = 512) -> dict:
        return {"reply": NAIVE_REPLY, "action": None}


def naive_satisfiable(entry: dict) -> bool:
    """独立于判定器的谓词:纯聊天固定回复能否满足该条目(用于阴性对照交叉验证)。"""
    for cand in normalize_expect(entry["expect"]):
        if cand["kind"] != "chat":
            continue
        if all(f.casefold() in NAIVE_REPLY.casefold()
               for f in cand.get("must_contain", ())):
            return True
    return False


# ======================================================================
# converse 定位与调用适配(未落地 → 优雅跳过)
# ======================================================================

def resolve_converse():
    """定位 converse 实现。

    返回 (adapter, 描述) —— adapter(text, provider) 负责一次对话调用;
    未找到时返回 (None, 尝试记录列表),调用方打印「等待 converse 落地」。
    按约定优先级探测:
      1. insar_agent.brain.facade.Brain.converse(实例方法,provider 走构造函数)
      2. insar_agent.brain.converse 模块的 converse 函数
      3. insar_agent.brain 包顶层的 converse 函数
    """
    tried: list[str] = []
    brain_cls = None
    try:
        from insar_agent.brain.facade import Brain as brain_cls  # noqa: N813
    except Exception as exc:  # 包未安装/依赖缺失也归入「未落地」
        tried.append(f"insar_agent.brain.facade 导入失败:{exc!r}")
    if brain_cls is not None:
        if hasattr(brain_cls, "converse"):
            def brain_adapter(text: str, provider, _cls=brain_cls):
                return _cls(provider).converse(text)
            return brain_adapter, "insar_agent.brain.facade.Brain.converse"
        tried.append("insar_agent.brain.facade.Brain 尚无 converse 方法")

    for mod_name in ("insar_agent.brain.converse", "insar_agent.brain"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:
            tried.append(f"{mod_name} 导入失败:{exc!r}")
            continue
        fn = getattr(mod, "converse", None)
        if not callable(fn):
            tried.append(f"{mod_name}.converse 不存在或不可调用")
            continue
        return _fn_adapter(fn), f"{mod_name}.converse"
    return None, tried


def _fn_adapter(fn):
    """模块级 converse 函数的调用适配:尽力把假/真 provider 注入进去。"""
    try:
        param_names = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        param_names = set()

    def adapter(text: str, provider):
        if "provider" in param_names:
            return fn(text, provider=provider)
        if "llm" in param_names:
            return fn(text, llm=provider)
        if "brain" in param_names:
            from insar_agent.brain.facade import Brain
            return fn(text, brain=Brain(provider))
        return fn(text)  # 无注入点:函数自己从环境构建 provider(real 模式可用)
    return adapter


def normalize_response(raw) -> dict:
    """converse 返回值归一化成契约 dict:支持 JSON 字符串 / dataclass / 属性对象。"""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if is_dataclass(raw) and not isinstance(raw, type):
        raw = asdict(raw)
    if not isinstance(raw, dict) and hasattr(raw, "reply"):
        action = getattr(raw, "action", None)
        if action is not None and not isinstance(action, dict):
            if is_dataclass(action) and not isinstance(action, type):
                action = asdict(action)
            elif hasattr(action, "__dict__"):
                action = dict(vars(action))
        raw = {"reply": raw.reply, "action": action}
    return raw


# ======================================================================
# 评测执行与报表
# ======================================================================

def _short(text: str, limit: int = 42) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def run_eval(entries: list[dict], respond, *, title: str,
             max_failures: int = 20, out_path: str | None = None) -> dict:
    """逐条调用 respond(entry) → 判定 → 打印逐条结果 + 分类准确率表 + 失败样例。"""
    print(f"== {title} ==(共 {len(entries)} 条)\n")
    verdicts: list[Verdict] = []
    for e in entries:
        try:
            resp = normalize_response(respond(e))
            v = judge(e, resp)
        except Exception as exc:  # 单条异常不中断整体评测
            v = Verdict(e["id"], category_of(e["id"]), False,
                        f"调用异常:{exc!r}", expected_summary(e), "<异常>")
        verdicts.append(v)
        mark = "PASS" if v.passed else "FAIL"
        print(f"[{mark}] {v.entry_id:<10} {_short(e['text'])}")
        if not v.passed:
            print(f"       └─ {v.reason}")

    total = len(verdicts)
    passed = sum(v.passed for v in verdicts)
    print("\n---- 分类准确率 ----")
    print(f"{'类别':<8}{'条数':>6}{'通过':>6}{'准确率':>10}")
    by_cat: dict[str, dict] = {}
    for cat in CATEGORIES:
        vs = [v for v in verdicts if v.category == cat]
        if not vs:
            continue
        ok = sum(v.passed for v in vs)
        acc = ok / len(vs)
        by_cat[cat] = {"total": len(vs), "passed": ok, "accuracy": round(acc, 4)}
        print(f"{cat:<8}{len(vs):>6}{ok:>6}{acc:>9.1%}")
    overall = passed / total if total else 0.0
    print(f"{'总计':<8}{total:>6}{passed:>6}{overall:>9.1%}")

    failures = [v for v in verdicts if not v.passed]
    if failures:
        print(f"\n---- 失败样例(前 {min(len(failures), max_failures)}/{len(failures)} 条)----")
        text_of = {e["id"]: e["text"] for e in entries}
        for v in failures[:max_failures]:
            print(f"· {v.entry_id}  话术:{_short(text_of[v.entry_id], 60)}")
            print(f"    期望:{v.expected}")
            print(f"    实际:{v.got}")
            print(f"    原因:{v.reason}")

    report = {
        "title": title, "total": total, "passed": passed,
        "accuracy": round(overall, 4), "by_category": by_cat,
        "failures": [vars(v) for v in failures],
    }
    if out_path:
        Path(out_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入:{out_path}")
    return report


def print_stats(stats: dict) -> None:
    print(f"金标集共 {stats['total']} 条")
    print(f"{'类别':<8}{'条数':>6}")
    for cat, n in stats["by_category"].items():
        print(f"{cat:<8}{n:>6}")
    print(f"期望形态:单候选 {stats['expect_shape']['单候选']} 条,"
          f"可接受集合 {stats['expect_shape']['可接受集合']} 条")
    print(f"动作类型覆盖(按候选计):{stats['action_type_coverage']}")
    print(f"plan 场景覆盖(按候选计):{stats['scenario_coverage']}")


# ======================================================================
# 各模式入口
# ======================================================================

def _mode_mock(entries: list[dict], args) -> int:
    """mock:注入假 provider 只验管道。converse 已落地则走真 converse,否则纯自检。"""
    use_naive = args.provider == "naive"
    provider = NaiveChatProvider() if use_naive else OracleProvider(entries)

    adapter, desc = (None, ["--pipeline-only 显式跳过 converse"]) \
        if (args.pipeline_only or use_naive) else resolve_converse()
    if adapter is not None:
        title = f"mock 评测:真 converse({desc})+ oracle 假 provider"
        def respond(entry):
            return adapter(entry["text"], provider)
    else:
        for line in desc:
            print(f"[探测] {line}")
        print("等待 converse 落地:未找到可注入的 converse 实现,"
              "退化为纯管道自检(假 provider 输出直接进判定器)。\n")
        title = ("mock 纯管道自检:naive 阴性对照" if use_naive
                 else "mock 纯管道自检:oracle 假 provider")
        def respond(entry):
            return provider.complete_json(user=entry["text"])

    report = run_eval(entries, respond, title=title,
                      max_failures=args.max_failures, out_path=args.out)

    if use_naive:
        # 阴性对照交叉验证:判定结果必须与独立谓词逐条一致(判定器没有放水/错杀)
        expect_pass = {e["id"]: naive_satisfiable(e) for e in entries}
        got_pass = {f["entry_id"]: False for f in report["failures"]}
        mismatch = [eid for eid, want in expect_pass.items()
                    if want != got_pass.get(eid, True)]
        if mismatch:
            print(f"\n[自检失败] 阴性对照与独立谓词不一致:{mismatch}")
            return 1
        print("\n[自检通过] 阴性对照:判定器对纯聊天响应的放行/拦截逐条符合预期。")
        return 0
    if report["accuracy"] < 1.0:
        print("\n[自检失败] oracle 假 provider 应得 100%:判定器、金标集或 converse "
              "注入协议存在不一致(可先用 --pipeline-only 隔离 converse 排查)。")
        return 1
    print("\n[自检通过] oracle 100%:金标集与判定管道自洽。")
    return 0


def _mode_real(entries: list[dict], args) -> int:
    """real:INSAR_EVAL_REAL=1 双闸门,走真实 LLM 配置逐条调 converse。默认不运行。"""
    import os
    if os.environ.get("INSAR_EVAL_REAL") != "1":
        print("默认零出网:未设置环境变量 INSAR_EVAL_REAL=1,real 评测跳过(exit 0)。")
        return 0
    adapter, tried = resolve_converse()
    if adapter is None:
        for line in tried:
            print(f"[探测] {line}")
        print("等待 converse 落地:converse 尚未合入主线,real 评测优雅跳过(exit 0)。")
        return 0
    try:
        from insar_agent.brain.provider import LLMProvider
        provider = LLMProvider()
    except Exception as exc:
        print(f"真实 provider 构建失败({exc!r}),跳过。")
        return 0
    if not provider.enabled:
        print("未配置 INSAR_LLM_BASE_URL / INSAR_LLM_MODEL,无可用 LLM 路由,"
              "real 评测跳过(保持零出网)。")
        return 0

    def respond(entry):
        return adapter(entry["text"], provider)

    report = run_eval(entries, respond, title=f"real 评测:{tried}",
                      max_failures=args.max_failures, out_path=args.out)
    if args.fail_under is not None and report["accuracy"] < args.fail_under:
        print(f"\n总体准确率 {report['accuracy']:.1%} 低于门限 {args.fail_under:.1%} → exit 1")
        return 1
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Win 控制台中文
    parser = argparse.ArgumentParser(
        description="converse 中文金标集评测 harness(默认零出网)")
    parser.add_argument("--golden", default=str(GOLDEN_PATH), help="金标集 jsonl 路径")
    parser.add_argument("--contract-only", action="store_true",
                        help="只校验金标集自身格式(jsonl 可解析/字段闭集/无重复 id)")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock",
                        help="mock=假 provider 验管道(默认);real=真实配置(需 INSAR_EVAL_REAL=1)")
    parser.add_argument("--provider", choices=("oracle", "naive"), default="oracle",
                        help="mock 用假 provider:oracle=按金标作弊;naive=永远纯聊天(阴性对照)")
    parser.add_argument("--pipeline-only", action="store_true",
                        help="mock 时跳过 converse 注入,纯管道自检")
    parser.add_argument("--out", default=None, help="评测报告 JSON 输出路径")
    parser.add_argument("--fail-under", type=float, default=None,
                        help="real 模式:总体准确率低于该值(0-1)则 exit 1")
    parser.add_argument("--max-failures", type=int, default=20, help="失败样例展示上限")
    args = parser.parse_args(argv)

    entries, errors = load_golden(Path(args.golden))
    if errors:
        print(f"金标集格式校验未通过,共 {len(errors)} 处:")
        for err in errors:
            print(f"  - {err}")
        return 1
    stats = golden_stats(entries)
    if len(entries) < 60:
        print(f"金标集不足 60 条(实际 {len(entries)}),不满足验收下限。")
        return 1
    if args.contract_only:
        print_stats(stats)
        print("\n金标集格式校验通过(jsonl 可解析 / 字段闭集 / 无重复 id / 总数达标)。")
        return 0

    if args.mode == "mock":
        return _mode_mock(entries, args)
    return _mode_real(entries, args)


if __name__ == "__main__":
    sys.exit(main())
