"""产物发现:声明式候选列表 + 显式失败(AGENT-DESIGN §1.4)。

绝不假设单一路径 —— MintPy 输出文件名随配置变(InSAR_Agent tools/mintpy.py:343-344
写了 4 个候选名),目录也不定(根目录 or geo/)。候选按声明顺序匹配,先中先得;
required 全不中则显式失败并报告尝试过的每一个候选。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from insar_agent.registry.model import ArtifactSpec


@dataclass(frozen=True)
class Found:
    spec: ArtifactSpec
    path: Path  # 绝对路径


@dataclass(frozen=True)
class Missing:
    spec: ArtifactSpec
    tried: tuple[str, ...]


def discover_artifacts(
    workspace: Path, specs: tuple[ArtifactSpec, ...]
) -> tuple[list[Found], list[Missing]]:
    found: list[Found] = []
    missing: list[Missing] = []
    for spec in specs:
        hit: Path | None = None
        tried: list[str] = []
        for candidate in spec.candidates:
            tried.append(candidate)
            if any(ch in candidate for ch in "*?["):
                matches = sorted(workspace.glob(candidate))
                if matches:
                    hit = matches[0]
                    break
            else:
                p = workspace / candidate
                if p.exists():
                    hit = p
                    break
        if hit is not None:
            found.append(Found(spec, hit))
        elif spec.required:
            missing.append(Missing(spec, tuple(tried)))
    return found, missing


def missing_message(missing: list[Missing]) -> str:
    parts = []
    for m in missing:
        parts.append(f"{m.spec.id}: 尝试了 {list(m.tried)},全部不存在")
    return "必需产物缺失 —— " + ";".join(parts)
