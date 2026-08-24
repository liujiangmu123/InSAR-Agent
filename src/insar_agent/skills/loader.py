"""步骤技能文档加载器(SKILL.md 开放标准变体,挂在核心 1-11 步与分析链 20-28 的单个步骤上)。

与 registry/scenario_packs(场景技能包)是两套正交的知识外置:场景包回答
「这类任务整体怎么打」,步骤技能回答「这一步怎么选参 / 失败了怎么救」。
目录契约(与内容代理共用,严格一致):

    skills/<两位步骤号>-<能力短名>/SKILL.md      如 skills/06-unwrap/SKILL.md

frontmatter(YAML,扁平键闭集):
    name         小写连字符标识
    description  触发条件式描述(何时该读这份技能)
    capability   整数步骤号(必须存在于 registry/capabilities.py;目录前缀
                 只是人类可读约定,以本字段为准)
    version      semver 字符串(与 content_hash 一起进 provenance)
    applies_to   场景 key 列表(registry/scenario_packs 的 key)或 "all"

正文章节(## 标题,五章缺一不可):
    适用判据 / 参数启发式 / 常见失败与处置 / QA 依据 / 参考文献

容错纪律(对齐 scenarios.py 的拒载口径):坏 frontmatter / 字段非法 / 缺章节
→ 警告跳过该文件,绝不拖垮启动;消费方对「无技能」一律零行为差异 ——
skill_section_text 返回空串,facade 对空串不附加任何上下文,逐字节不变。

INSAR_SKILLS_DIR 覆盖技能目录(冻结打包由 desktop 侧 entry.py 在 import 前
注入,与 api/app.py 的 INSAR_UI_DIR 同款手法);源码运行缺省取仓库根 skills/。
不做模块级缓存:技能文件小且少(1-11 与 20-28,无 12-19),每次重扫换来
「改文件即生效」与测试间零状态泄漏。
"""

from __future__ import annotations

import hashlib
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import yaml

from insar_agent.registry.capabilities import REGISTRY

#: 技能目录的唯一环境变量覆盖点
SKILLS_DIR_ENV = "INSAR_SKILLS_DIR"

#: 源码运行缺省:仓库根 skills/(src/insar_agent/skills/loader.py 上溯三级)
_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "skills"

#: 正文必备章节(契约五章;消费接线只取其中两章,其余供 API 全文披露)
REQUIRED_SECTIONS = ("适用判据", "参数启发式", "常见失败与处置", "QA 依据", "参考文献")

#: 消费接线取用的章节名(driver 接线用常量,防手写串漂移)
SECTION_PARAMS = "参数启发式"
SECTION_FAILURES = "常见失败与处置"

#: frontmatter 扁平键闭集(未知字段警告不拒载,与 scenario_packs 同口径)
_FRONT_FIELDS = {"name", "description", "capability", "version", "applies_to"}

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")
_FRONT_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
#: 目录名的两位步骤号前缀(仅用于「前缀与 capability 不一致」的告警,不参与解析)
_DIR_PREFIX_RE = re.compile(r"^(\d{2})-")


class SkillDocWarning(UserWarning):
    """技能文档校验警告(未知字段 / 跳过原因)。"""


def _warn(msg: str) -> None:
    warnings.warn(f"skills: {msg}", SkillDocWarning, stacklevel=2)


@dataclass(frozen=True)
class StepSkill:
    name: str
    description: str
    capability: int  # 步骤号(registry/capabilities.py 的 Capability.id)
    version: str
    applies_to: tuple[str, ...]  # ("all",) 表示全场景适用
    sections: dict[str, str]  # 章节名 → 正文(不含 ## 标题行,已去首尾空白)
    content_hash: str  # 归一化全文 sha256(见 _content_hash)
    path: str  # SKILL.md 绝对路径(诊断用;API 不外泄)

    def applies(self, scene: str | None) -> bool:
        """scene=None 表示不按场景过滤(分诊语境);否则须命中 all 或该 key。"""
        return scene is None or "all" in self.applies_to or scene in self.applies_to


def skills_dir() -> Path:
    override = os.environ.get(SKILLS_DIR_ENV, "")
    return Path(override) if override else _DEFAULT_DIR


def _content_hash(text: str) -> str:
    """全文 sha256(frontmatter + 正文)。

    换行归一为 \\n 后取 UTF-8 字节:进 provenance 的哈希必须跨平台稳定,
    git autocrlf 会让同一技能版本在 Windows 检出为 CRLF,不归一就会对
    同一份文档记出两个哈希(read_text 的通用换行已翻译大部分场景,这里
    是对绕过文本模式的调用方 / 孤立 \\r 的兜底,双保险成本为零)。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    """拆 frontmatter 与正文。返回 (dict | None, 正文);None = 缺失或不可解析。"""
    m = _FRONT_RE.match(text)
    if not m:
        return None, text
    body = text[m.end():]
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, body
    return (data, body) if isinstance(data, dict) else (None, body)


def _split_sections(body: str) -> dict[str, str]:
    """正文按 ## 标题切章节:{标题: 正文}(### 子标题归属所在章节)。"""
    sections: dict[str, str] = {}
    marks = list(_SECTION_RE.finditer(body))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        sections[m.group(1)] = body[m.end():end].strip()
    return sections


def _parse_applies_to(value) -> tuple[str, ...] | None:
    """"all" 或非空字符串列表;其余形态(含裸场景串)返回 None 交上层拒载 ——
    契约保持窄口径,内容代理写错能立即从警告里看到,而不是被静默宽容。"""
    if value == "all":
        return ("all",)
    if (isinstance(value, list) and value
            and all(isinstance(x, str) and x for x in value)):
        return tuple(value)
    return None


def _load_one(path: Path) -> StepSkill | None:
    """加载单份 SKILL.md;任何契约违背 → 警告并返回 None(跳过)。"""
    rel = path.parent.name  # 告警里用目录名定位,不泄漏绝对路径
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _warn(f"{rel}: SKILL.md 不可读({exc.__class__.__name__}),跳过")
        return None
    front, body = _split_frontmatter(text)
    if front is None:
        _warn(f"{rel}: frontmatter 缺失或不可解析,跳过")
        return None

    unknown = set(front) - _FRONT_FIELDS
    if unknown:
        _warn(f"{rel}: 未知 frontmatter 字段 {sorted(map(str, unknown))}(闭集外,已忽略)")

    name = front.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        _warn(f"{rel}: name 必须是小写连字符标识,收到 {name!r},跳过")
        return None
    description = front.get("description")
    if not isinstance(description, str) or not description.strip():
        _warn(f"{rel}: 缺 description(触发条件式描述),跳过")
        return None
    capability = front.get("capability")
    # bool 是 int 子类:yaml 的 true 若不拦会被当步骤号 1(与 facade 的 choice 同款守卫)
    if (isinstance(capability, bool) or not isinstance(capability, int)
            or capability not in REGISTRY):
        _warn(f"{rel}: capability 必须是流水线步骤号 {sorted(REGISTRY)},"
              f"收到 {capability!r},跳过")
        return None
    version = front.get("version")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        _warn(f"{rel}: version 必须是 semver 字符串(如 1.0.0),收到 {version!r},跳过")
        return None
    applies_to = _parse_applies_to(front.get("applies_to"))
    if applies_to is None:
        _warn(f"{rel}: applies_to 必须是场景 key 列表或 \"all\","
              f"收到 {front.get('applies_to')!r},跳过")
        return None

    sections = _split_sections(body)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        _warn(f"{rel}: 缺章节 {missing}(契约五章缺一不可),跳过")
        return None

    prefix = _DIR_PREFIX_RE.match(rel)
    if prefix and int(prefix.group(1)) != capability:
        # frontmatter 为准,仍加载;前缀漂移多半是复制目录忘改,留痕提醒
        _warn(f"{rel}: 目录前缀 {prefix.group(1)} 与 capability={capability} 不一致"
              f"(以 frontmatter 为准)")

    return StepSkill(name=name, description=description.strip(), capability=capability,
                     version=version, applies_to=applies_to, sections=sections,
                     content_hash=_content_hash(text), path=str(path))


def load_skills(root: Path | str | None = None) -> dict[int, StepSkill]:
    """扫描技能目录 → {步骤号: 技能}。目录缺失返回空表(零影响)。

    同一步骤号出现多份技能时按目录名序保留第一份并警告 —— 与「坏文件跳过」
    同一纪律:加载器绝不替内容代理做合并,冲突必须在文件层解决。
    """
    root = Path(root) if root is not None else skills_dir()
    if not root.is_dir():
        return {}
    out: dict[int, StepSkill] = {}
    for doc in sorted(root.glob("*/SKILL.md")):
        skill = _load_one(doc)
        if skill is None:
            continue
        if skill.capability in out:
            _warn(f"{doc.parent.name}: 步骤 {skill.capability} 已有技能"
                  f"({out[skill.capability].name}),本份忽略")
            continue
        out[skill.capability] = skill
    return out


def skill_for_step(step_id: int, *, scene: str | None = None,
                   root: Path | str | None = None) -> StepSkill | None:
    """按步骤号取技能;scene 给定时还须 applies_to 命中,否则视同无技能。"""
    skill = load_skills(root).get(step_id)
    if skill is None or not skill.applies(scene):
        return None
    return skill


def skill_section_text(step_id: int, section: str, *, scene: str | None = None,
                       root: Path | str | None = None) -> str:
    """消费接线的统一入口:该步技能的指定章节正文;无技能/不适用/无此章一律空串。

    调用方(driver/facade)对空串零行为差异,故本函数永不抛错、永不返回 None。
    """
    skill = skill_for_step(step_id, scene=scene, root=root)
    if skill is None:
        return ""
    return skill.sections.get(section, "")
