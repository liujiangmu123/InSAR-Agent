"""指标来源契约:重解析比对(AGENT-DESIGN §7.1 / DESIGN §7.1)。

每个指标声明 source_artifact + source_field;verify 重新打开产物、重新解析、
比对数值,回填 reparsed_ok。「LLM 说的数」永远不作数 —— 只有重解析通过的指标
才能进入报告与证据阶梯。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from insar_agent.core.store import Store

_REL_TOL = 1e-9


def _find_artifact_path(store: Store, run_id: str, source_artifact: str) -> Path | None:
    for art in store.artifacts_of(run_id):
        p = art["path"]
        if p == source_artifact or Path(p).name == source_artifact:
            return Path(p)
    return None


def verify_metrics(store: Store, run_id: str, workspace: Path) -> list[dict]:
    """逐指标重解析。返回 [{name, ok, detail}];同时回填 metrics.reparsed_ok。"""
    results: list[dict] = []
    for metric in store.metrics_of(run_id):
        name = metric["name"]
        rel = _find_artifact_path(store, run_id, metric["source_artifact"] or "")
        if rel is None:
            results.append({"name": name, "ok": False, "detail": "来源产物未登记"})
            store.record_metric(run_id, name, value=metric["value"],
                                source_artifact=metric["source_artifact"] or "",
                                source_field=metric["source_field"] or "",
                                reparsed_ok=False)
            continue
        path = workspace / rel
        ok = False
        detail = ""
        if not path.exists():
            detail = f"来源产物不存在:{rel}"
        elif path.suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                fresh = data.get(metric["source_field"])
                if isinstance(fresh, (int, float)) and metric["value"] is not None:
                    ok = math.isclose(float(fresh), float(metric["value"]),
                                      rel_tol=_REL_TOL, abs_tol=1e-12)
                    detail = f"重解析 {fresh} vs 记录 {metric['value']}"
                else:
                    detail = f"字段 {metric['source_field']} 不是数值"
            except (json.JSONDecodeError, OSError) as exc:
                detail = f"重解析失败:{exc}"
        else:
            detail = f"暂不支持的来源格式:{path.suffix}(h5 支持待 raster extra)"
        store.record_metric(run_id, name, value=metric["value"],
                            source_artifact=metric["source_artifact"] or "",
                            source_field=metric["source_field"] or "",
                            reparsed_ok=ok)
        results.append({"name": name, "ok": ok, "detail": detail})
    return results
