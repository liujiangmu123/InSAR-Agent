"""步骤技能文档端点(独立 APIRouter,由 api/app.py include_router 挂载)。

- GET /api/skills            全列表:{"skills": [{capability / name / version /
                             content_hash / description / applies_to /
                             sections: {章节: 首行摘要}}]}。
- GET /api/skills/{step_id}  单步全文:按章节结构化返回;无技能 → 404。

响应形状:列表带 skills 信封、步骤号字段名用 capability(对齐 registry 语汇)。

纪律:
- 每请求经 loader 重扫技能目录(loader 无缓存):改技能文件即生效,无需重启;
- 坏文档已在 loader 层警告跳过,端点绝不因单个坏文件 500;
- 响应不携带磁盘路径(与 app.py 产物端点同一「不泄露路径」口径)。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from insar_agent.skills.loader import StepSkill, load_skills

router = APIRouter(prefix="/api/skills", tags=["skills"])

#: 列表端点的章节摘要长度上限(全文走单步端点,列表只做预览)
_SUMMARY_MAX = 80


def _summary(text: str) -> str:
    """章节首个非空行,超长截断加省略号。"""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:_SUMMARY_MAX] + ("…" if len(line) > _SUMMARY_MAX else "")
    return ""


def _meta(skill: StepSkill) -> dict:
    return {
        "capability": skill.capability,
        "name": skill.name,
        "version": skill.version,
        "content_hash": skill.content_hash,
        "description": skill.description,
        "applies_to": list(skill.applies_to),
    }


@router.get("")
def list_skills() -> dict:
    """全部已加载技能的清单(skills 信封,按步骤号排序;章节只给首行摘要)。"""
    return {"skills": [
        {**_meta(skill),
         "sections": {name: _summary(text) for name, text in skill.sections.items()}}
        for _sid, skill in sorted(load_skills().items())
    ]}


@router.get("/{step_id}")
def get_skill(step_id: int) -> dict:
    """单步技能全文(章节名 → 完整正文);该步无技能 → 404。"""
    skill = load_skills().get(step_id)
    if skill is None:
        raise HTTPException(404, f"步骤 {step_id} 没有技能文档")
    return {**_meta(skill), "sections": dict(skill.sections)}
