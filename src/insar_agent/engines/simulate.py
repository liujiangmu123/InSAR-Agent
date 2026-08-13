"""合成执行构建器:引擎缺失环境下跑通全链的诚实替身。

原则(借鉴 InSAR-Pro result_metadata 的 real/fallback/simulated 三级 + 取最差传播):
  - 每一行日志带 [simulated] 前缀,产物内容带 SIMULATED 标记;
  - run.simulated 记录在 runs 表,provenance 显式导出,证据阶梯封顶 runnable;
  - 绝不产出以假乱真的数值 —— qa.json 里的指标是「结构演示值」,同样带标记。

演示脚本被渲染为工作区内的 .sim/s{step}.py(可 diff、可复现),
argv = [python, 脚本],与真实引擎走完全相同的作业目录契约。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

# 每步的演示日志行(与 prototype backend.mock.js SCRIPTS 呼应,但显式标注 simulated)
_LOG_LINES: dict[int, list[str]] = {
    1: ["扫描本地数据目录", "发现 HyP3 干涉对产品", "校验清单完成"],
    2: ["检查 DEM 缓存", "DEM 就绪"],
    3: ["几何配准 + ESD 精化", "配准完成"],
    4: ["构建小基线网络", "生成干涉图"],
    5: ["Goldstein 滤波", "滤波完成"],
    6: ["读取干涉对", "构建 Delaunay 网络 · 代价函数 SMOOTH",
        "MCF 最小费用流求解 … 32%", "MCF 最小费用流求解 … 78%",
        "94.2% 像元已解缠 · 残差点 1204 · 孤岛 3 处(已掩膜)"],
    7: ["闭合回路检查通过", "SBAS 反演 · 加权最小二乘 · 4 次迭代收敛"],
    8: ["ERA5 对流层延迟(缓存命中)", "固体潮校正", "DEM 误差估计"],
    9: ["拟合形变模型", "R² = 0.96"],
    10: ["地理编码 → WGS84", "出图:速率图 / 时序图"],
    11: ["PS 链: 214880 点 · SBAS 链: 1689305 像元",
         "重叠区 Pearson r = 0.92 · RMSE 3.1 mm/yr"],
}

# 质检步写进 qa.json 的演示指标(结构演示值,带 simulated 标记)
_QA_METRICS = {"crossval_r": 0.92, "crossval_rmse_mm": 3.1, "unwrap_coverage": 0.942}


def _script(cap: Capability, method: str, params: dict[str, Any], sleep: float) -> str:
    """生成演示脚本源码。产物取每个 ArtifactSpec 的第一个候选路径。"""
    lines = _LOG_LINES.get(cap.id, ["处理中", "完成"])
    artifact_writes: list[str] = []
    for spec in cap.artifacts:
        target = spec.candidates[0]
        if "*" in target or "?" in target:
            target = target.replace("*", "sim").replace("?", "x")
        is_dir = "." not in Path(target).name
        if is_dir:
            artifact_writes.append(
                f"    make_dir_artifact({target!r})")
        elif target.endswith(".json"):
            artifact_writes.append(
                f"    write_json({target!r})")
        else:
            artifact_writes.append(
                f"    write_file({target!r})")
    body_writes = "\n".join(artifact_writes) or "    pass"
    log_stmts = "\n".join(
        f"    log({line!r}); time.sleep({sleep!r})" for line in lines)
    return f'''# 自动生成的合成执行脚本(simulated)。与真实引擎共用作业目录契约。
import json, os, sys, time
from pathlib import Path

WS = Path({str('.')!r}).resolve()  # cwd 即工作区
METHOD = {method!r}
PARAMS = {params!r}
QA = {_QA_METRICS!r}

def log(msg):
    print("[simulated] " + msg, flush=True)

def write_file(rel):
    p = WS / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"SIMULATED insar-agent artifact step={cap.id} method=" + METHOD.encode())

def make_dir_artifact(rel):
    d = WS / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "sim.dat").write_bytes(b"SIMULATED " + METHOD.encode())

def write_json(rel):
    p = WS / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(QA)
    payload["simulated"] = True
    payload["method"] = METHOD
    p.write_text(json.dumps(payload, indent=1), encoding="utf-8")

def main():
    log("$ {cap.name} --method " + METHOD)
{log_stmts}
{body_writes}
    log("完成(simulated)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    sleep = float(params.get("_sim_sleep", 0.02))
    script_rel = f".sim/s{cap.id:02d}.py"
    files = {script_rel: _script(cap, method, params, sleep)}
    # 参数快照(config 的一部分,进 config_hash;真实引擎在此渲染 topsApp.xml/*.cfg)
    files[f"params/s{cap.id:02d}.json"] = _params_snapshot(method, params)
    # wrapper_python:冻结态 sys.executable 是后端 exe 本身,直接用会误派生
    # 第二个后端(DESKTOP-PARITY GAP-1);源码运行两者等价
    from insar_agent.runtime.jobs import wrapper_python
    return CommandPlan(
        argv=[wrapper_python(), "-X", "utf8", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8"},
        files=files,
        shell_line=f"python {script_rel}",
    )


def _params_snapshot(method: str, params: dict[str, Any]) -> str:
    import json

    return json.dumps({"method": method, "params": {
        k: v for k, v in params.items() if not k.startswith("_")
    }}, ensure_ascii=False, indent=1, sort_keys=True)
