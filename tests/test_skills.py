"""步骤技能系统验收(skills/loader + /api/skills + provenance skill 字段)。

  - 契约解析:frontmatter 五键 + 五章节 + applies_to 匹配语义
  - 容错:坏 frontmatter / 缺章节 / 非法字段 / 重复步骤号 → 警告跳过,绝不抛
  - 哈希:LF/CRLF 检出形态不影响 content_hash(跨平台 provenance 稳定),且可独立复算
  - API:GET /api/skills 列表 + GET /api/skills/{step_id} 全文 / 404
  - provenance:有技能的步骤记 skill: {name, version, content_hash},无技能省略字段
  - 零影响:技能目录不存在时 loader/API/provenance 全部按「无技能」走,行为不变
仓库自带的 skills/06-unwrap/SKILL.md 是与内容代理共用的契约样例,单测锁死其可加载。
"""

from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insar_agent.api.app import create_app
from insar_agent.audit.contract import load_contract
from insar_agent.brain.facade import Brain
from insar_agent.core.failures import FailureClass
from insar_agent.core.ledger import export_provenance
from insar_agent.skills.loader import (
    REQUIRED_SECTIONS,
    SECTION_FAILURES,
    SECTION_PARAMS,
    SkillDocWarning,
    load_skills,
    skill_for_step,
    skill_section_text,
)

HASHES = {"task_hash": "t", "args_hash": "a", "local_hash": "l", "eval_hash": "e"}

#: 契约样例(与 skills/06-unwrap/SKILL.md 同构的最小版):五键 frontmatter + 五章节
VALID_DOC = """---
name: unwrap-method-selection
description: 第 6 步解缠的方法选择与失败处置启发式。
capability: 6
version: 1.2.3
applies_to:
  - permafrost
  - quake
---

# 解缠方法选择

## 适用判据

第 6 步有多个可行候选时。

## 参数启发式

低相干选 snaphu_mcf;同震 cost_mode 用 DEFO。

### 细则

min_coherence 默认 0.25。

## 常见失败与处置

OOM 回第 4 步加多视。

## QA 依据

unwrap_coverage >= 0.70(contract.yaml,PENDING)。

## 参考文献

- Chen & Zebker (2001), JOSA A 18(2).
"""


def write_skill(root: Path, dirname: str, text: str) -> Path:
    d = root / dirname
    d.mkdir(parents=True)
    path = d / "SKILL.md"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def make_doc(front: str, sections=REQUIRED_SECTIONS) -> str:
    """frontmatter + 契约五章(可裁剪)的最小合法文档。"""
    body = "\n".join(f"## {s}\n\n{s}正文。\n" for s in sections)
    return f"---\n{front}\n---\n\n{body}"


FRONT_OK = ("name: demo-skill\ndescription: 测试用。\n"
            "capability: 6\nversion: 1.0.0\napplies_to: all")


# ---------------- 契约解析 ----------------

def test_parse_contract_fields_and_sections(tmp_path):
    write_skill(tmp_path, "06-unwrap", VALID_DOC)
    skills = load_skills(tmp_path)
    assert set(skills) == {6}
    sk = skills[6]
    assert sk.name == "unwrap-method-selection"
    assert sk.capability == 6 and sk.version == "1.2.3"
    assert sk.applies_to == ("permafrost", "quake")
    assert set(REQUIRED_SECTIONS) <= set(sk.sections)
    # 章节正文不含 ## 标题行;### 子标题归属所在章节
    assert sk.sections[SECTION_PARAMS].startswith("低相干选 snaphu_mcf")
    assert "### 细则" in sk.sections[SECTION_PARAMS]
    assert sk.sections[SECTION_FAILURES] == "OOM 回第 4 步加多视。"
    # applies 语义:列表按 key 命中;None(分诊语境)不过滤
    assert sk.applies("permafrost") and not sk.applies("landslide")
    assert sk.applies(None)


def test_applies_to_all_and_scene_query(tmp_path):
    write_skill(tmp_path, "06-unwrap", make_doc(FRONT_OK))
    sk = load_skills(tmp_path)[6]
    assert sk.applies_to == ("all",)
    assert sk.applies("任意场景")
    # 按场景查询与章节取文(loader 无缓存,每次返回新对象,按内容断言)
    got = skill_for_step(6, scene="quake", root=tmp_path)
    assert got is not None and got.name == "demo-skill"
    assert skill_section_text(6, SECTION_PARAMS, root=tmp_path) == "参数启发式正文。"
    assert skill_section_text(6, "不存在的章节", root=tmp_path) == ""
    assert skill_section_text(3, SECTION_PARAMS, root=tmp_path) == ""


def test_scene_mismatch_treated_as_no_skill(tmp_path):
    front = FRONT_OK.replace("applies_to: all", "applies_to:\n  - permafrost")
    write_skill(tmp_path, "06-unwrap", make_doc(front))
    assert skill_for_step(6, scene="quake", root=tmp_path) is None
    assert skill_section_text(6, SECTION_PARAMS, scene="quake", root=tmp_path) == ""
    assert skill_for_step(6, scene="permafrost", root=tmp_path) is not None


# ---------------- 容错:坏文件警告跳过,绝不拖垮 ----------------

def test_bad_frontmatter_skipped(tmp_path):
    write_skill(tmp_path, "05-bad", "---\nname: [未闭合\n---\n\n## 适用判据\n\nx\n")
    write_skill(tmp_path, "06-good", make_doc(FRONT_OK))
    with pytest.warns(SkillDocWarning, match="frontmatter 缺失或不可解析"):
        skills = load_skills(tmp_path)
    assert set(skills) == {6}  # 坏文件不影响好文件


def test_missing_section_skipped(tmp_path):
    write_skill(tmp_path, "06-partial",
                make_doc(FRONT_OK, sections=REQUIRED_SECTIONS[:-1]))  # 缺「参考文献」
    with pytest.warns(SkillDocWarning, match="缺章节"):
        assert load_skills(tmp_path) == {}


@pytest.mark.parametrize("patch, match", [
    ("capability: 6->capability: 99", "capability 必须是流水线步骤号"),
    ("capability: 6->capability: true", "capability 必须是流水线步骤号"),
    ("version: 1.0.0->version: v1", "version 必须是 semver"),
    ("applies_to: all->applies_to: permafrost", "applies_to 必须是场景"),
    ("name: demo-skill->name: Demo_Skill", "name 必须是小写连字符"),
])
def test_invalid_field_skipped(tmp_path, patch, match):
    old, new = patch.split("->")
    write_skill(tmp_path, "06-demo", make_doc(FRONT_OK.replace(old, new)))
    with pytest.warns(SkillDocWarning, match=match):
        assert load_skills(tmp_path) == {}


def test_duplicate_capability_first_wins(tmp_path):
    write_skill(tmp_path, "06-first", make_doc(FRONT_OK))
    write_skill(tmp_path, "06-second",
                make_doc(FRONT_OK.replace("demo-skill", "demo-skill-b")))
    with pytest.warns(SkillDocWarning, match="已有技能"):
        skills = load_skills(tmp_path)
    assert skills[6].name == "demo-skill"  # 目录名序第一份生效


def test_dir_prefix_mismatch_warns_but_loads(tmp_path):
    write_skill(tmp_path, "07-misnamed", make_doc(FRONT_OK))  # frontmatter 仍是 6
    with pytest.warns(SkillDocWarning, match="目录前缀"):
        skills = load_skills(tmp_path)
    assert set(skills) == {6}  # frontmatter 为准


# ---------------- 哈希稳定 ----------------

def test_content_hash_stable_across_line_endings(tmp_path):
    write_skill(tmp_path / "lf", "06-unwrap", VALID_DOC)
    crlf = tmp_path / "crlf" / "06-unwrap"
    crlf.mkdir(parents=True)
    (crlf / "SKILL.md").write_bytes(VALID_DOC.replace("\n", "\r\n").encode("utf-8"))

    h_lf = load_skills(tmp_path / "lf")[6].content_hash
    h_crlf = load_skills(tmp_path / "crlf")[6].content_hash
    assert h_lf == h_crlf  # git autocrlf 检出形态不改变 provenance 哈希
    # 口径可独立复算:归一化(\n)全文的 UTF-8 sha256
    assert h_lf == hashlib.sha256(VALID_DOC.encode("utf-8")).hexdigest()


# ---------------- API 两端点 ----------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "06-unwrap", VALID_DOC)
    monkeypatch.setenv("INSAR_SKILLS_DIR", str(skills_root))
    with TestClient(create_app(home=tmp_path / "home")) as c:
        yield c


def test_api_list(client):
    r = client.get("/api/skills")
    assert r.status_code == 200
    items = r.json()["skills"]  # skills 信封(前端 skillpanel.js 的消费契约)
    assert [it["capability"] for it in items] == [6]
    it = items[0]
    assert it["name"] == "unwrap-method-selection" and it["version"] == "1.2.3"
    assert len(it["content_hash"]) == 64
    assert it["applies_to"] == ["permafrost", "quake"]
    # 章节摘要:五章俱在,取首个非空行(预览,不是全文)
    assert set(it["sections"]) == set(REQUIRED_SECTIONS)
    assert it["sections"][SECTION_FAILURES] == "OOM 回第 4 步加多视。"
    assert "path" not in it  # 不泄露磁盘路径


def test_api_detail_and_404(client):
    r = client.get("/api/skills/6")
    assert r.status_code == 200
    full = r.json()
    assert full["capability"] == 6 and full["content_hash"]
    # 全文按章节结构化返回(含 ### 子标题的完整正文)
    assert "### 细则" in full["sections"][SECTION_PARAMS]
    assert "path" not in full
    assert client.get("/api/skills/5").status_code == 404  # 该步无技能
    assert client.get("/api/skills/abc").status_code == 422  # 非整数步骤号


# ---------------- provenance skill 字段 ----------------

def _seed_run(store, tmp_path):
    store.create_session("s1", "s1")
    store.create_run("r1", "s1", workspace=str(tmp_path / "ws"))
    store.create_step("r1", 6, capability="6", name="解缠", method="snaphu_mcf",
                      params={}, hashes=HASHES)
    store.create_step("r1", 7, capability="7", name="时序反演", method="mintpy_sbas",
                      params={}, hashes=HASHES)


def test_provenance_skill_field(store, tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "06-unwrap", VALID_DOC)
    monkeypatch.setenv("INSAR_SKILLS_DIR", str(skills_root))
    _seed_run(store, tmp_path)
    doc = export_provenance(store, "r1", contract=load_contract(),
                            workspace=tmp_path / "ws")
    expected = load_skills(skills_root)[6].content_hash
    assert doc["steps"]["6"]["skill"] == {
        "name": "unwrap-method-selection", "version": "1.2.3", "content_hash": expected}
    assert "skill" not in doc["steps"]["7"]  # 无技能的步骤省略字段


# ---------------- 无技能目录:零影响 ----------------

def test_zero_impact_without_skills_dir(store, tmp_path, monkeypatch):
    monkeypatch.setenv("INSAR_SKILLS_DIR", str(tmp_path / "nonexistent"))
    assert load_skills() == {}
    assert skill_for_step(6) is None
    assert skill_section_text(6, SECTION_PARAMS) == ""
    _seed_run(store, tmp_path)
    doc = export_provenance(store, "r1", contract=load_contract(),
                            workspace=tmp_path / "ws")
    assert all("skill" not in step for step in doc["steps"].values())
    with TestClient(create_app(home=tmp_path / "home")) as c:
        assert c.get("/api/skills").json() == {"skills": []}
        assert c.get("/api/skills/6").status_code == 404


def test_brain_no_llm_path_unchanged_by_skill_context():
    """铁律守护:技能上下文只进 LLM 提示词 —— 无 LLM 降级路径行为不变。"""
    brain = Brain(None)
    t = brain.triage("完全无法匹配规则的日志", skill_notes="任何技能内容")
    assert t.failure_class == FailureClass.UNKNOWN and t.source == "fallback"
    t2 = brain.triage("Out of memory", skill_notes="任何技能内容")
    assert t2.failure_class == FailureClass.OOM and t2.source == "rules"


# ---------------- 仓库契约守护 ----------------

def test_repo_skills_tree_is_clean():
    """仓库 skills/ 树(内容代理产出的 11 份正式文档)必须零告警加载:
    十一步全覆盖、frontmatter 与目录前缀一致、五章节非空 —— 契约由本测试锁死,
    任何一份文档漂移都在这里显形,而不是在规划/分诊运行时静默缺知识。"""
    root = Path(__file__).resolve().parents[1] / "skills"
    with warnings.catch_warnings():
        warnings.simplefilter("error", SkillDocWarning)
        skills = load_skills(root)
    assert set(skills) == set(range(1, 12))  # 11 步全覆盖
    for sid, sk in skills.items():
        assert sk.capability == sid
        assert all(sk.sections[s].strip() for s in REQUIRED_SECTIONS), \
            f"步骤 {sid}({sk.name})存在空章节"
    assert skills[6].name == "06-unwrap" and skills[6].applies_to == ("all",)
