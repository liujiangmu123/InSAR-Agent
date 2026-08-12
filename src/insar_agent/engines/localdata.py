"""本地数据导入(step 1 · local_import 的真实实现)。

把已有数据(HyP3 产品目录等)接入工作区:
  - hyp3 目录:Windows 目录联接(mklink /J,秒级、零拷贝、无需管理员),
    失败(跨盘符限制等)回退为复制;
  - mintpy/inputs(如缓存的 ERA5.h5):复制(小文件,进指纹)。

数据源解析顺序:params.source > INSAR_HYP3_SOURCE 环境变量;都缺 → 显式失败。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from insar_agent.registry.model import Capability
from insar_agent.runtime.jobs import CommandPlan

_IMPORT_PY = '''\
# insar-agent 本地数据导入脚本(真实执行,零决策)
import os, shutil, subprocess, sys
from pathlib import Path

WS = Path(".").resolve()
SOURCE = os.environ.get("INSAR_HYP3_SOURCE") or {source!r}
if not SOURCE:
    print("ERROR: 未指定数据源(params.source 或 INSAR_HYP3_SOURCE)", flush=True)
    sys.exit(2)
src = Path(SOURCE)
if not src.exists():
    print(f"ERROR: 数据源不存在: {{src}}", flush=True)
    sys.exit(2)

hyp3_src = src / "hyp3" if (src / "hyp3").exists() else src
dst = WS / "hyp3"

def link_or_copy(a: Path, b: Path) -> str:
    if b.exists():
        return "已存在,跳过"
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(b), str(a)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return "目录联接(零拷贝)"
    try:
        b.symlink_to(a, target_is_directory=True)
        return "符号链接"
    except OSError:
        shutil.copytree(a, b)
        return "复制"

pairs = [d for d in hyp3_src.iterdir() if d.is_dir()]
print(f"数据源: {{hyp3_src}}", flush=True)
print(f"干涉对: {{len(pairs)}} 个", flush=True)
mode = link_or_copy(hyp3_src, dst)
print(f"hyp3 -> 工作区: {{mode}}", flush=True)

# 缓存的大气校正文件(有则接入,免 CDS 凭据)
era5 = src / "mintpy" / "inputs" / "ERA5.h5"
if era5.exists():
    target = WS / "mintpy" / "inputs" / "ERA5.h5"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(era5, target)
    print(f"ERA5.h5 已接入 ({{era5.stat().st_size/1e6:.1f}} MB, 免 CDS 凭据)", flush=True)
else:
    print("无缓存 ERA5.h5(大气校正将需要 CDS 凭据或降级)", flush=True)

n_unw = len(list(dst.glob("*/*unw_phase_clipped.tif")))
print(f"解缠相位栅格: {{n_unw}} 个(HyP3 已完成 2-6 步)", flush=True)
if n_unw == 0:
    print("ERROR: 数据源里没有 *unw_phase_clipped.tif", flush=True)
    sys.exit(3)
print("导入完成", flush=True)
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    script_rel = ".import/import_local.py"
    content = _IMPORT_PY.format(source=str(params.get("source", "") or ""))
    return CommandPlan(
        argv=[sys.executable, "-X", "utf8", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8",
             **({"INSAR_HYP3_SOURCE": os.environ["INSAR_HYP3_SOURCE"]}
                if os.environ.get("INSAR_HYP3_SOURCE") else {})},
        files={script_rel: content},
        shell_line=f"python {script_rel}",
    )
