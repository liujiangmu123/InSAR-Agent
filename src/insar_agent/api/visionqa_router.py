"""AI 识图质检 API(/api/vision-qa):对 run 的图件执行/查询视觉质检。

契约:
  POST /api/vision-qa  {session, run_id?, figure}
    - 归属与路径校验复用 /api/figures 的解析口径:figure 按「文件名」在该 run
      产物枚举结果里精确匹配 —— 枚举本身就限定在 run 工作区内(路径规范化 +
      越界排除),名字对不上一律 404,不泄露磁盘布局;
    - 未配置识图模型 → {ok:false, error:"未配置识图模型,在模型设置里选择"}
      (200 降级,与 /api/llm/test 同风格:配置缺失是常态,不是异常);
    - 图件超 4MB / LLM 失败 / 响应坏形状 → {ok:false, error:...};
    - 成功 → {ok:true, run, review},并在图件旁落盘 <name>.aiqa.json。
  GET /api/vision-qa?session=&run=
    - 列出该 run 已有的 .aiqa.json(坏文件/超限容忍跳过,绝不 500 整表);
    - 无 run → {run:null, items:[]}(与 /api/figures 空态口径一致)。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from insar_agent.audit.vision_qa import FigureRejected, aiqa_path, review_figure
from insar_agent.brain.llm_config import vision_route_from_config
from insar_agent.brain.provider import BrainUnavailable
from insar_agent.core.store import Store

#: 图像扩展名闭集(与 app._IMAGE_MEDIA_TYPES 的键一致:枚举口径必须与
#: /api/figures 相同,否则「列表里有的图审不了」)
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

#: .aiqa.json 读取上限:防坏文件/误命名的大 JSON 拖垮列表(同 sidecar 口径)
_AIQA_MAX_BYTES = 256 * 1024


class VisionQABody(BaseModel):
    session: str
    run_id: str | None = None
    figure: str


def create_visionqa_router(home: Path, store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/vision-qa", tags=["vision-qa"])

    def resolve_run(session: str, run_id: str | None, *,
                    required: bool = True) -> dict | None:
        """与 app.resolve_run 同口径:缺省取最近 run;跨会话按「不存在」404。"""
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            if required:
                raise HTTPException(404, "no run")
            return None
        if run["session_id"] != session:
            raise HTTPException(
                404, f"run {run['run_id']} 不存在或不属于会话 {session}")
        return run

    def iter_figures(run: dict):
        """该 run 全部图像产物的落盘路径(与 /api/figures 同一安全判据)。

        - 产物相对路径规范化后必须仍落在 run 工作区内(绝对路径/盘符/../ 不放行);
        - 目录型产物枚举顶层图像成员,resolve 后越界的符号链接成员排除;
        - 枚举窗口内消失的文件/目录逐项跳过,绝不整表失败。
        """
        base = Path(run["workspace"]).resolve()
        for art in store.artifacts_of(run["run_id"]):
            rel = Path(art["path"])
            if rel.is_absolute() or rel.drive:
                continue
            target = (base / rel).resolve()
            if target == base or not target.is_relative_to(base):
                continue
            if rel.suffix.lower() in _IMAGE_EXTS:
                if target.is_file():
                    yield target
            elif target.is_dir():
                try:
                    children = sorted(p for p in target.iterdir() if p.is_file()
                                      and p.suffix.lower() in _IMAGE_EXTS)
                except OSError:
                    continue
                for child in children:
                    try:
                        if child.resolve().is_relative_to(target):
                            yield child
                    except OSError:
                        continue

    def locate_figure(run: dict, name: str) -> Path | None:
        for p in iter_figures(run):
            if p.name == name:
                return p
        return None

    def read_meta(image: Path) -> dict | None:
        """图件元数据 sidecar(app.read_sidecar_meta 的同口径委托)。"""
        from insar_agent.api.app import read_sidecar_meta
        return read_sidecar_meta(image)

    @router.post("")
    def review(body: VisionQABody) -> dict:
        run = resolve_run(body.session, body.run_id)
        target = locate_figure(run, body.figure)
        if target is None:
            # 名字不在该 run 的产物枚举里:含路径穿越/跨 run 名字,一律按不存在
            raise HTTPException(404, "no figure")
        if vision_route_from_config(home) is None:
            return {"ok": False, "error": "未配置识图模型,在模型设置里选择"}
        try:
            record = review_figure(home, target, read_meta(target))
        except FigureRejected as exc:
            return {"ok": False, "error": str(exc)}
        except BrainUnavailable as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "run": run["run_id"], "review": record}

    @router.get("")
    def list_reviews(session: str, run: str | None = None) -> dict:
        r = resolve_run(session, run, required=False)
        if r is None:
            return {"run": None, "items": []}
        items: list[dict] = []
        seen: set[str] = set()
        for image in iter_figures(r):
            key = str(image)
            if key in seen:  # 同一文件可能同时是文件型产物与目录成员:只报一次
                continue
            seen.add(key)
            sidecar = aiqa_path(image)
            try:
                if (not sidecar.is_file()
                        or sidecar.stat().st_size > _AIQA_MAX_BYTES):
                    continue
                data = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # 坏文件容忍:单个 .aiqa.json 损坏不拖垮列表
            if isinstance(data, dict):
                items.append(data)
        return {"run": r["run_id"], "items": items}

    return router
