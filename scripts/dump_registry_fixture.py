# -*- coding: utf-8 -*-
"""一次性生成 tests/js/_registry.mjs（/api/registry 无 session 探测载荷的快照夹具）。

字段与 api/app.py registry() 端点逐项对齐（probe=None 分支：ok=True、
simulated=False、blocked=""）。生成后本脚本即可删除；重生成命令见
tests/js/_registry.mjs 文件头。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from insar_agent.registry.capabilities import PIPELINE  # noqa: E402

HEADER = """\
/* ============================================================
   注册表夹具 —— GET /api/registry（无 session 探测）载荷的快照。
   由服务端能力声明（src/insar_agent/registry/capabilities.py）生成,
   与 api/app.py registry() 端点逐字段一致（probe=None 分支:
   ok=True / simulated=False / blocked=""）。

   用途:前端测试/校验脚本喂 state.setRegistry(REGISTRY),
   在 Node 里复现「注册表水合后的 STEP_DEFS」——这是真实服务端
   目录的快照,不是手写演示数据。registry 声明变更后重生成:
     .venv\\Scripts\\python scripts\\dump_registry_fixture.py
   （若脚本已删除,按本文件头的字段映射从 capabilities.py 重导即可。）
   ============================================================ */
export const REGISTRY =
"""

FOOTER = """\
;

/**
 * 步骤镜像种子:模拟服务端 /api/state 的步骤载荷（含权威 params）。
 * doneThrough 前的步骤记 done,其余 pending —— 测试用来复现
 * 「部分完成的计划」;真实运行里同样形状由服务端下发。
 * @param {object} St  已 import 的 state.js 模块（先 setRegistry 再调用）
 * @param {number} doneThrough  1..N 标 done 的最大步骤 id（0=全 pending）
 */
export function seedSteps(St, doneThrough = 0) {
  St.initSteps();
  St.syncServerSteps(St.STEP_DEFS.map((d) => ({
    id: d.id,
    state: d.id <= doneThrough ? 'done' : 'pending',
    method: d.method,
    params: { ...d.params },
    stale: false,
  })));
}
"""


def dump() -> list[dict]:
    out = []
    for cap in PIPELINE:
        methods = [{
            "id": m.id, "label": m.label, "engine": m.engine, "why": m.why,
            "recommend": m.recommend, "extra": m.extra,
            "ok": True, "simulated": False, "blocked": "",
        } for m in cap.methods]
        out.append({
            "id": cap.id, "name": cap.name, "deps": list(cap.deps),
            "method": cap.default_method, "methods": methods,
            "params": {k: {"default": p.default, "kind": p.kind, "type": p.type,
                           "min": p.min, "max": p.max, "hint": p.hint}
                       for k, p in cap.params.items()},
            "outputs": [{"path": a.candidates[0], "kind": a.kind, "layout": a.layout}
                        for a in cap.artifacts],
            "replay": cap.replay,
            "timeouts": {"idle": cap.timeouts.idle, "total": cap.timeouts.total},
        })
    return out


if __name__ == "__main__":
    target = ROOT / "tests" / "js" / "_registry.mjs"
    body = json.dumps(dump(), ensure_ascii=False, indent=2)
    target.write_text(HEADER + body + FOOTER, encoding="utf-8", newline="\n")
    print(f"written: {target}")
