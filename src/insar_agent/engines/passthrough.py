"""规范路径物化:硬链接优先、拷贝兜底(零决策)。

与 engines/mintpy.py 的区别:那个跑 smallbaselineApp 分段,这个只把已存在的
真实文件落到分析链的规范位置。register_sources(第 20 步)是同一构建器的
双源形态:params.primary(与非空的 params.secondary)→ analysis/source.h5
(与 source_2.h5)。后续 passthrough 按步骤把规范输入物化为规范输出。

红线:绝不走 simulate —— 物化的是真实文件,不是合成产物。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan, wrapper_python

_OUT_DIR = "analysis"

# cap.id → ((src_rel, dst_rel, required), ...); 次源 required=False,存在才物化
_PASSTHROUGH_PAIRS: dict[int, tuple[tuple[str, str, bool], ...]] = {
    21: (("analysis/source.h5", "analysis/masked.h5", True),
         ("analysis/source_2.h5", "analysis/masked_2.h5", False)),
    22: (("analysis/masked.h5", "analysis/corrected.h5", True),
         ("analysis/masked_2.h5", "analysis/corrected_2.h5", False)),
    23: (("analysis/corrected.h5", "analysis/decomposed.h5", True),),
    25: (),
    26: (("analysis/decomposed.h5", "analysis/change.h5", True),),
    27: (),
    28: (),
}

# cap.id → JSON 声明产物(跳过科学动作时仍留下可审计文件)
_PASSTHROUGH_MARKERS: dict[int, tuple[tuple[str, dict], ...]] = {
    25: (("analysis/figures/passthrough.json",
          {"method": "passthrough", "reason": "本次分析不出图"}),),
    26: (("analysis/change_summary.json",
          {"method": "passthrough", "significant_fraction": None,
           "note": "本次分析不做变化检测"}),),
    27: (("analysis/prediction.json",
          {"status": "passthrough",
           "assumptions": ["no extrapolation requested (passthrough)"],
           "not_a_forecast_of": [
               "earthquake occurrence or timing",
               "landslide failure time",
               "new coseismic or outburst step events",
           ],
           "ci": {"level": None, "lower": None, "upper": None,
                  "note": "passthrough: no prediction computed"},
           "note": "本次分析不做外推,prediction.json 仅为规范链占位"}),),
    28: (("analysis/bridge/passthrough.json",
          {"method": "passthrough", "reason": "本次分析不导出反演桥"}),),
}

_MATERIALIZE_PY = '''\
# insar-agent 规范路径物化(真实文件硬链接/拷贝,零决策)
import hashlib, json, os, shutil, sys
from pathlib import Path

PAIRS = {pairs!r}  # [(src, dst, required), ...]
MARKERS = {markers!r}  # [(relpath, payload), ...]

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def materialize(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"

failed = False
for src_s, dst_s, required in PAIRS:
    src, dst = Path(src_s), Path(dst_s)
    if not src.is_file():
        msg = f"源文件不存在: {{src}}"
        if required:
            print("ERROR:", msg, flush=True)
            failed = True
        else:
            print("SKIP:", msg, flush=True)
        continue
    mode = materialize(src, dst)
    digest = sha256_of(dst)
    sidecar = dst.with_name(dst.name + ".json")
    sidecar.write_text(json.dumps({{
        "source": str(src), "dest": str(dst), "sha256": digest, "mode": mode,
    }}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {{mode}} {{src}} -> {{dst}} sha256={{digest[:12]}}", flush=True)

for rel, payload in MARKERS:
    path = Path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK marker {{path}}", flush=True)

sys.exit(2 if failed else 0)
'''


def _safe_rel(workspace: Path, rel: str, label: str) -> Path:
    """相对路径解析 + 逃逸防御:结果必须仍在 workspace 内。"""
    raw = str(rel or "").strip()
    if not raw:
        raise ValueError(f"{label} 为空")
    p = Path(raw)
    if p.is_absolute() or p.drive:
        raise ValueError(f"{label} 必须是工作区相对路径:{rel}")
    base = workspace.resolve()
    target = (workspace / p).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError(f"{label} 逃逸出工作区:{rel}")
    return target


def _rel_posix(workspace: Path, target: Path) -> str:
    return target.resolve().relative_to(workspace.resolve()).as_posix()


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    if method == "register_sources":
        primary = _safe_rel(workspace, str(params.get("primary") or ""), "primary")
        if not primary.is_file():
            raise FileNotFoundError(f"register_sources 源不存在:{primary}")
        pairs: list[tuple[str, str, bool]] = [
            (_rel_posix(workspace, primary), f"{_OUT_DIR}/source.h5", True),
        ]
        secondary = str(params.get("secondary") or "").strip()
        if secondary:
            sec = _safe_rel(workspace, secondary, "secondary")
            if not sec.is_file():
                raise FileNotFoundError(f"register_sources 次源不存在:{sec}")
            pairs.append((_rel_posix(workspace, sec), f"{_OUT_DIR}/source_2.h5", True))
        markers: list[tuple[str, dict]] = []
    elif method == "passthrough":
        if cap.id not in _PASSTHROUGH_PAIRS and cap.id not in _PASSTHROUGH_MARKERS:
            raise ValueError(f"passthrough 无此步骤的规范链:{cap.id}")
        declared = _PASSTHROUGH_PAIRS.get(cap.id, ())
        pairs = []
        for src_rel, dst_rel, required in declared:
            src = workspace / src_rel
            if required and not src.is_file():
                raise FileNotFoundError(f"passthrough 规范输入不存在:{src_rel}")
            pairs.append((src_rel, dst_rel, required))
        markers = list(_PASSTHROUGH_MARKERS.get(cap.id, ()))
    else:
        raise ValueError(f"passthrough 无此方法:{method}")

    script_rel = ".analysis/passthrough.py"
    return CommandPlan(
        argv=[wrapper_python(), "-X", "utf8", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        files={script_rel: _MATERIALIZE_PY.format(pairs=pairs, markers=markers)},
        shell_line=f"python {script_rel}",
    )
