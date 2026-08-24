"""场景技能包加载器(冻土→周期,地震→阶跃)。intent 的规则层依据,brain 拔除后仍可用。

absorb-E7(reference/PI_FRAMEWORK_ANALYSIS.md §5.2):场景知识外置为技能包目录,
本模块只负责扫描、闭集校验与构造 Scenario:

    registry/scenario_packs/<key>/
    ├── SKILL.md          # frontmatter(闭集)+ 领域知识正文(选参依据/模型设定/质量门侧重)
    └── overrides.yaml    # step_overrides + cloud_completed(机器读)

目录名用 scenario_packs 而非 scenarios:同名目录会在 import 系统里遮蔽本模块
(包目录优先于同名 .py),导致全项目 `registry.scenarios` 导入全部断裂。

渐进披露(吸收 pi skills):启动只解析 frontmatter(name/description/metadata 常驻),
正文经 Scenario.knowledge() 按需惰性读取(场景确认后供 narrate/select 使用)。

闭集校验(对齐 scientific-agent-skills 纪律,COMPARISON_LEARNING §2.4):
frontmatter 顶层与 metadata 都是闭集,未知字段警告不拒载;缺 name/description、
frontmatter 不可解析、name 与目录名不一致、metadata 缺机器必填字段、
overrides.yaml 不可解析 —— 这些会破坏 Scenario 构造或规则层行为,一律拒载并警告。

对外接口与硬编码时代完全一致:SCENARIOS / classify_text / scenario_of。
metadata.version 进 Scenario.version,供 provenance 使用。
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PACKS_DIR = Path(__file__).resolve().parent / "scenario_packs"

# frontmatter 顶层闭集(对齐 agentskills.io / scientific-agent-skills 的 6 字段)
_TOP_FIELDS = {"name", "description", "license", "compatibility", "allowed-tools", "metadata"}
# metadata 闭集:version + 排序 + Scenario 机器字段
_META_FIELDS = {
    "version", "priority", "label", "match", "chain", "model", "pick_7",
    "diag", "reason", "data_ready", "region", "dates", "scenes",
}
# 缺了就无法构造有意义的 Scenario → 拒载
_META_REQUIRED = ("label", "match", "chain", "model", "pick_7")
# overrides.yaml 顶层闭集
_OVERRIDES_FIELDS = {"step_overrides", "cloud_completed"}


class ScenarioPackWarning(UserWarning):
    """技能包校验警告(未知字段 / 拒载原因)。"""


def _warn(msg: str) -> None:
    warnings.warn(f"scenario_packs: {msg}", ScenarioPackWarning, stacklevel=2)


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    match: str  # 正则(规则层意图识别)
    chain: str  # SBAS | PS
    model: str  # 形变模型 method id
    pick_7: str  # 第 7 步方法
    diag: str
    reason: str
    data_ready: bool = False
    region: str = ""
    dates: str = ""
    scenes: str = ""
    step_overrides: dict = field(default_factory=dict)  # {step_id: {method?, params?}}
    # 云端(HyP3)已完成的步骤:计划时标 skipped,不做可行性检查,不执行
    cloud_completed: tuple = ()
    version: str = ""  # SKILL.md metadata.version(进 provenance)
    # SKILL.md 路径(渐进披露的正文来源;不参与相等性/展示)
    skill_path: str = field(default="", repr=False, compare=False)

    def knowledge(self) -> str:
        """惰性读取 SKILL.md 正文(渐进披露:描述常驻,全文按需)。"""
        cached = self.__dict__.get("_knowledge")
        if cached is not None:
            return cached
        body = ""
        if self.skill_path:
            try:
                _front, body = _split_frontmatter(
                    Path(self.skill_path).read_text(encoding="utf-8"))
            except OSError:
                body = ""
        body = body.strip()
        object.__setattr__(self, "_knowledge", body)  # frozen:仅缓存,不改字段
        return body


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    """拆 frontmatter 与正文。返回 (frontmatter dict | None, 正文);None 表示缺失或不可解析。"""
    m = re.match(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", text, re.DOTALL)
    if not m:
        return None, text
    body = text[m.end():]
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, body
    return (data, body) if isinstance(data, dict) else (None, body)


def _load_overrides(pack_dir: Path) -> tuple[dict, tuple] | None:
    """解析 overrides.yaml。文件缺失 → 空覆写;不可解析/结构错误 → None(拒载)。"""
    path = pack_dir / "overrides.yaml"
    if not path.is_file():
        return {}, ()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _warn(f"{pack_dir.name}: overrides.yaml 解析失败({exc.__class__.__name__}),拒载")
        return None
    if data is None:
        return {}, ()
    if not isinstance(data, dict):
        _warn(f"{pack_dir.name}: overrides.yaml 顶层必须是映射,拒载")
        return None
    unknown = set(data) - _OVERRIDES_FIELDS
    if unknown:
        _warn(f"{pack_dir.name}: overrides.yaml 未知字段 {sorted(map(str, unknown))}(闭集外,已忽略)")  # noqa: E501
    raw = data.get("step_overrides") or {}
    if not isinstance(raw, dict):
        _warn(f"{pack_dir.name}: step_overrides 必须是映射,拒载")
        return None
    try:
        step_overrides = {int(k): dict(v or {}) for k, v in raw.items()}
        cloud_completed = tuple(int(x) for x in (data.get("cloud_completed") or ()))
    except (TypeError, ValueError):
        _warn(f"{pack_dir.name}: step_overrides/cloud_completed 键值不可解析,拒载")
        return None
    return step_overrides, cloud_completed


def _load_pack(pack_dir: Path) -> tuple[int, Scenario] | None:
    """加载单个技能包。返回 (priority, Scenario);校验不过返回 None(拒载)。"""
    skill = pack_dir / "SKILL.md"
    if not skill.is_file():
        _warn(f"{pack_dir.name}: 缺 SKILL.md,拒载")
        return None
    front, _body = _split_frontmatter(skill.read_text(encoding="utf-8"))
    if front is None:
        _warn(f"{pack_dir.name}: frontmatter 缺失或不可解析,拒载")
        return None

    unknown = set(front) - _TOP_FIELDS
    if unknown:
        _warn(f"{pack_dir.name}: 未知 frontmatter 字段 {sorted(map(str, unknown))}(闭集外,已忽略)")
    name = front.get("name")
    description = front.get("description")
    if not isinstance(name, str) or not name or not isinstance(description, str) or not description:
        _warn(f"{pack_dir.name}: 缺 name/description,拒载")
        return None
    if name != pack_dir.name:
        _warn(f"{pack_dir.name}: name={name!r} 与目录名不一致,拒载")
        return None

    meta = front.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    unknown_meta = set(meta) - _META_FIELDS
    if unknown_meta:
        _warn(f"{pack_dir.name}: 未知 metadata 字段 {sorted(map(str, unknown_meta))}(闭集外,已忽略)")  # noqa: E501
    missing = [k for k in _META_REQUIRED if not meta.get(k)]
    if missing:
        _warn(f"{pack_dir.name}: metadata 缺必填字段 {missing},无法构造 Scenario,拒载")
        return None
    version = str(meta.get("version") or "")
    if not version:
        _warn(f"{pack_dir.name}: 缺 metadata.version(provenance 需要,建议语义化版本)")

    match = str(meta["match"])
    try:
        re.compile(match)
    except re.error as exc:
        _warn(f"{pack_dir.name}: match 正则不合法({exc}),拒载")
        return None

    overrides = _load_overrides(pack_dir)
    if overrides is None:
        return None
    step_overrides, cloud_completed = overrides

    scenario = Scenario(
        key=name,
        label=str(meta["label"]),
        match=match,
        chain=str(meta["chain"]),
        model=str(meta["model"]),
        pick_7=str(meta["pick_7"]),
        diag=str(meta.get("diag", "")),
        reason=str(meta.get("reason", "")),
        data_ready=bool(meta.get("data_ready", False)),
        region=str(meta.get("region", "")),
        dates=str(meta.get("dates", "")),
        scenes=str(meta.get("scenes", "")),
        step_overrides=step_overrides,
        cloud_completed=cloud_completed,
        version=version,
        skill_path=str(skill),
    )
    return int(meta.get("priority", 100)), scenario


def load_scenarios(root: Path | str = PACKS_DIR) -> tuple[Scenario, ...]:
    """扫描技能包目录,返回按 (priority, key) 排序的场景元组。

    priority 决定 classify_text 的先验命中顺序(数值小者先试,
    保持硬编码时代 quake→permafrost→landslide 的歧义消解语义)。
    """
    root = Path(root)
    if not root.is_dir():
        return ()
    loaded: list[tuple[int, Scenario]] = []
    for pack_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if pack_dir.name.startswith((".", "_")):
            continue
        item = _load_pack(pack_dir)
        if item is not None:
            loaded.append(item)
    loaded.sort(key=lambda t: (t[0], t[1].key))
    return tuple(sc for _prio, sc in loaded)


SCENARIOS: tuple[Scenario, ...] = load_scenarios()


def classify_text(text: str) -> Scenario | None:
    """规则层场景识别。返回 None 表示无法判定(交给 LLM 或表单)。"""
    for sc in SCENARIOS:
        if re.search(sc.match, text, re.IGNORECASE):
            return sc
    return None


def scenario_of(key: str) -> Scenario | None:
    for sc in SCENARIOS:
        if sc.key == key:
            return sc
    return None
