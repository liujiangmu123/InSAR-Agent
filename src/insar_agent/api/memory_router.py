"""跨会话记忆 REST 面(前端「记忆」面板与萃取触发的数据源)。

  GET    /api/memory                列表;?q= 内容搜索,?kind= 类型过滤,
                                    ?include_archived=1 含已归档
  POST   /api/memory                手动添加 {kind, content, session_id?}
                                    (kind/content 校验,非法 400)
  DELETE /api/memory/{id}           归档软删(数据保留;不存在 404)
  POST   /api/memory/extract        {run_id} → done run 规则萃取 outcome 记忆
                                    (run 不存在 404;非 done 409)

纪律:
  - 存取全部走 brain/memory.MemoryStore,本路由零 SQL;
  - 萃取端点是唯一触发点(不挂 driver 钩子 —— loop/driver.py 归并行分支
    所有权):run 结束后由前端建议卡或用户点击调用,幂等可重入;
  - 响应里的记忆行原样含 weight/source/ts,前端据此渲染徽章与时间。

接线:api/app.py 加一行 include_router(create_memory_router(store))。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from insar_agent.brain.memory import MEMORY_KINDS, MemoryStore, extract_from_run
from insar_agent.core.store import Store


class MemoryBody(BaseModel):
    kind: str
    content: str
    session_id: str | None = None  # 缺省全局记忆(所有会话可见)


class ExtractBody(BaseModel):
    run_id: str


def create_memory_router(store: Store) -> APIRouter:
    router = APIRouter(tags=["memory"])
    memories = MemoryStore(store)

    @router.get("/api/memory")
    def list_memory(q: str | None = None, kind: str | None = None,
                    include_archived: bool = False):
        if kind is not None and kind not in MEMORY_KINDS:
            raise HTTPException(
                400, f"未知记忆类型 {kind}(可用:{'|'.join(MEMORY_KINDS)})")
        if q:
            items = memories.search(q, include_archived=include_archived)
            if kind:  # 搜索 + 类型的组合过滤(搜索结果集内存过滤,量小)
                items = [m for m in items if m["kind"] == kind]
        else:
            items = memories.list(kind=kind, include_archived=include_archived)
        return {"items": items}

    @router.post("/api/memory")
    def add_memory(body: MemoryBody):
        try:
            mid = memories.add(body.kind, body.content, source="user",
                               session_id=body.session_id)
        except ValueError as exc:  # kind 闭集 / 内容长度校验
            raise HTTPException(400, str(exc))
        return {"ok": True, "memory": memories.get(mid)}

    @router.delete("/api/memory/{memory_id}")
    def delete_memory(memory_id: int):
        if not memories.archive(memory_id):
            raise HTTPException(404, f"记忆 {memory_id} 不存在")
        return {"ok": True, "archived": True, "id": memory_id}

    @router.post("/api/memory/extract")
    def extract_memory(body: ExtractBody):
        try:
            out = extract_from_run(memories, body.run_id)
        except KeyError:
            raise HTTPException(404, f"run {body.run_id} 不存在")
        except ValueError as exc:  # 非 done run
            raise HTTPException(409, str(exc))
        return {"ok": True, **out, "memory": memories.get(out["memory_id"])}

    return router
