"""本地数据导入(step 1 · local_import / nisar_import 与 step 2 · dem_local 的真实实现)。

把已有数据接入工作区,按 source 形态分两条路:
  - Windows 目录(HyP3 产品目录等):目录联接(mklink /J,秒级、零拷贝、
    无需管理员),失败(跨盘符限制等)回退为复制;mintpy/inputs(如缓存的
    ERA5.h5)复制(小文件,进指纹);
  - WSL 侧 POSIX 绝对路径(如 /home/insar/work/baja,ALOS Baja 手工链装配的
    数据,docs/VALIDATION-isce2-wsl.md):不搬运任何字节 —— 引擎作业本来就在
    WSL 内跑、按绝对路径直接读,宿主只经 \\\\wsl.localhost 核验在位并把清单
    登记进工作区(data/slc/manifest.json),缺文件显式失败(§1.4)。

nisar_import(step 1):NISAR GUNW 已是云端解缠产品(跳过 2-6,类似 HyP3)。
登记到 data/nisar/(MintPy processor=nisar 吃 HDF5,不复用 hyp3/ 布局),
缺 source / 无 HDF5 显式失败,不下载。

dem_local(step 2):核验声明的本地/WSL DEM(ISCE 格式,须有 fixImageXml 产物
.xml)在位并登记 data/dem/manifest.json —— 修复此前 dem_local 无真实构建器、
真实 run 里必然 ToolMissing 的接线缺口(Baja 预检发现)。

数据源解析顺序:params.source > INSAR_HYP3_SOURCE 环境变量;都缺 → 显式失败。
WSL 宿主视图根可用 INSAR_WSL_HOSTROOT 覆盖(测试缝:无 WSL 的机器可指向任意目录)。
"""

from __future__ import annotations

import os
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
        # CREATE_NO_WINDOW:本脚本运行于无窗进程,cmd 子进程不加此标志会弹黑窗
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(b), str(a)],
                           capture_output=True, text=True, creationflags=0x08000000)
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

# WSL 工作区登记脚本:数据已在发行版内(手工链装配),零拷贝,只核验 + 清单。
# 宿主视图用 //wsl.localhost/<distro>/...(正斜杠形态,pathlib 同样接受 UNC),
# INSAR_WSL_HOSTROOT 可整体替换宿主视图根(单测在无 WSL 的机器上指向临时目录)。
_WSL_IMPORT_PY = '''\
# insar-agent WSL 工作区数据登记脚本(真实执行,零决策)
# 数据已在 WSL 发行版内:不搬运字节,只核验在位并登记清单(§1.4 显式失败)
import json, os, sys
from pathlib import Path

WS = Path(".").resolve()
SOURCE = {source!r}  # WSL 侧 POSIX 绝对路径
DISTRO = (os.environ.get("INSAR_WSL_DISTRO") or "insar").strip() or "insar"
# 测试缝:INSAR_WSL_HOSTROOT 把宿主视图根指向任意目录(无 WSL 也能单测)
host_root = os.environ.get("INSAR_WSL_HOSTROOT") or f"//wsl.localhost/{{DISTRO}}"
src = Path(host_root) / SOURCE.lstrip("/")
if not src.exists():
    print(f"ERROR: WSL 数据源不可达: {{src}}(发行版 {{DISTRO}} 未注册或路径不存在)",
          flush=True)
    sys.exit(2)
raw = src / "raw" if (src / "raw").is_dir() else src
imgs = sorted(p.name for p in raw.glob("IMG-*"))
leds = sorted(p.name for p in raw.glob("LED-*"))
print(f"数据源(WSL): {{SOURCE}} @ {{DISTRO}}", flush=True)
print(f"raw 布局: {{'source/raw' if raw.name == 'raw' else 'source 平铺'}}"
      f" · IMG x{{len(imgs)}} · LED x{{len(leds)}}", flush=True)
for name in imgs + leds:
    print(f"  {{name}}  {{(raw / name).stat().st_size / 1e6:.1f}} MB", flush=True)
if len(imgs) < 2 or len(leds) < 2:
    print("ERROR: 干涉对不完整(需要 >=2 个 IMG-* 与 >=2 个 LED-*)", flush=True)
    sys.exit(3)
raw_posix = SOURCE + "/raw" if raw.name == "raw" else SOURCE
dem_posix = SOURCE + "/dem/dem.wgs84"
dem_ok = (src / "dem" / "dem.wgs84").exists() and (src / "dem" / "dem.wgs84.xml").exists()
print(f"DEM(ISCE 格式): {{'在位' if dem_ok else '缺失(第 2 步 dem_local 将显式失败)'}}",
      flush=True)
target = WS / "data" / "slc"
target.mkdir(parents=True, exist_ok=True)
manifest = {{
    "mode": "wsl_workspace",
    "distro": DISTRO,
    "source": SOURCE,
    "raw_dir": raw_posix,
    "images": imgs,
    "leaders": leds,
    "dem": dem_posix if dem_ok else None,
}}
(target / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("登记完成: data/slc/manifest.json(引擎按 WSL 绝对路径直接读取,零拷贝)",
      flush=True)
'''

# dem_local 核验脚本:DEM 是 science 输入(params.dem),ISCE 格式要求同目录有
# fixImageXml 产物 .xml(绝对路径已固化,docs/VALIDATION-isce2-wsl.md DEM 转换节)
_DEM_LOCAL_PY = '''\
# insar-agent 本地 DEM 核验脚本(真实执行,零决策)
import json, os, sys
from pathlib import Path

WS = Path(".").resolve()
DEM = {dem!r}
if not DEM:
    print("ERROR: dem_local 需要 params.dem 指向 ISCE 格式 DEM(如 .../dem.wgs84)",
          flush=True)
    sys.exit(2)
if DEM.startswith("/"):
    DISTRO = (os.environ.get("INSAR_WSL_DISTRO") or "insar").strip() or "insar"
    host_root = os.environ.get("INSAR_WSL_HOSTROOT") or f"//wsl.localhost/{{DISTRO}}"
    dem_path = Path(host_root) / DEM.lstrip("/")
    where = f"WSL({{DISTRO}})"
else:
    dem_path = Path(DEM)
    where = "本地"
xml_path = Path(str(dem_path) + ".xml")
missing = [str(p) for p in (dem_path, xml_path) if not p.exists()]
if missing:
    print(f"ERROR: DEM 不在位({{where}}): {{missing}}"
          "(需 gdal_translate -of ISCE + fixImageXml.py -f,见场景包 SKILL)", flush=True)
    sys.exit(2)
size = dem_path.stat().st_size
print(f"DEM 在位({{where}}): {{DEM}}  {{size / 1e6:.1f}} MB(+ .xml 元数据)", flush=True)
target = WS / "data" / "dem"
target.mkdir(parents=True, exist_ok=True)
(target / "manifest.json").write_text(
    json.dumps({{"mode": "local_dem", "dem": DEM, "xml": DEM + ".xml",
                 "size_bytes": size}}, ensure_ascii=False, indent=1), encoding="utf-8")
print("登记完成: data/dem/manifest.json", flush=True)
'''


# nisar_import:GUNW 已是解缠产品,不要求 HyP3 *unw_phase_clipped.tif。
# 目标 data/nisar/(MintPy processor=nisar 吃 HDF5,不复用 hyp3/ GeoTIFF 布局)。
# 清单写在 data/nisar/manifest.json;产品联接到 data/nisar/products,避免
# 目录联接后往源数据里写清单。缺 source / 无可识别 HDF5 → 显式失败,不下载。
_NISAR_IMPORT_PY = '''\
# insar-agent NISAR GUNW 登记脚本(真实执行,零下载)
import json, os, shutil, subprocess, sys
from pathlib import Path

WS = Path(".").resolve()
SOURCE = os.environ.get("INSAR_NISAR_SOURCE") or {source!r}
if not SOURCE:
    print("ERROR: nisar_import 未指定数据源(params.source)", flush=True)
    sys.exit(2)
src = Path(SOURCE)
if not src.exists():
    print(f"ERROR: NISAR 数据源不存在: {{src}}", flush=True)
    sys.exit(2)

root = WS / "data" / "nisar"
root.mkdir(parents=True, exist_ok=True)
dst = root / "products"

def link_or_copy_dir(a: Path, b: Path) -> str:
    if b.exists():
        return "已存在,跳过"
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(b), str(a)],
                           capture_output=True, text=True, creationflags=0x08000000)
        if r.returncode == 0:
            return "目录联接(零拷贝)"
    try:
        b.symlink_to(a, target_is_directory=True)
        return "符号链接"
    except OSError:
        shutil.copytree(a, b)
        return "复制"

def link_or_copy_file(a: Path, b: Path) -> str:
    b.parent.mkdir(parents=True, exist_ok=True)
    if b.exists():
        return "已存在,跳过"
    try:
        os.link(a, b)
        return "硬链接"
    except OSError:
        shutil.copy2(a, b)
        return "复制"

def collect_products(folder: Path) -> list[Path]:
    gunw = sorted(folder.rglob("*GUNW*.h5")) + sorted(folder.rglob("*gunw*.h5"))
    if gunw:
        return gunw
    h5 = sorted(p for p in folder.rglob("*")
                if p.is_file() and p.suffix.lower() in (".h5", ".hdf5", ".nc"))
    return h5

if src.is_file():
    if src.suffix.lower() not in (".h5", ".hdf5", ".nc"):
        print(f"ERROR: nisar_import 需要 GUNW HDF5/NetCDF,得到: {{src.suffix}}",
              flush=True)
        sys.exit(2)
    products = [src]
    mode = link_or_copy_file(src, dst / src.name)
else:
    products = collect_products(src)
    if not products:
        print("ERROR: 数据源里没有 GUNW/HDF5 产品(*GUNW*.h5 或 *.h5/*.hdf5/*.nc)",
              flush=True)
        sys.exit(3)
    mode = link_or_copy_dir(src, dst)

rel_products = []
for p in products:
    try:
        rel_products.append(p.relative_to(src if src.is_dir() else src.parent).as_posix())
    except ValueError:
        rel_products.append(p.name)
print(f"数据源: {{src}}", flush=True)
print(f"GUNW/HDF5 产品: {{len(products)}} 个", flush=True)
print(f"data/nisar/products <- {{mode}}", flush=True)
manifest = {{
    "mode": "nisar_gunw",
    "source": SOURCE,
    "dest": "data/nisar",
    "link_mode": mode,
    "products": rel_products,
    "note": "GUNW 为云端已解缠干涉产品,本步只登记不下载;"
            "场景包用 cloud_completed: [2,3,4,5,6] + 第 7 步 processor=nisar",
}}
(root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("登记完成: data/nisar/manifest.json", flush=True)
'''


def build(*, cap: Capability, method: str, params: dict[str, Any], run: dict,
          workspace: Path) -> CommandPlan:
    if method == "dem_local":
        script_rel = ".import/dem_local.py"
        content = _DEM_LOCAL_PY.format(dem=str(params.get("dem", "") or ""))
    elif method == "nisar_import":
        source = str(params.get("source", "") or "")
        script_rel = ".import/import_nisar.py"
        content = _NISAR_IMPORT_PY.format(source=source)
    else:
        source = str(params.get("source", "") or "")
        script_rel = ".import/import_local.py"
        # POSIX 绝对路径 = 数据在 WSL 工作区(零拷贝登记);其余走 HyP3 目录导入
        if source.startswith("/"):
            content = _WSL_IMPORT_PY.format(source=source)
        else:
            content = _IMPORT_PY.format(source=source)
    # wrapper_python:冻结态 sys.executable 是后端 exe 本身(GAP-1),源码运行等价
    from insar_agent.runtime.jobs import wrapper_python
    extra_env: dict[str, str] = {}
    if method == "nisar_import" and os.environ.get("INSAR_NISAR_SOURCE"):
        extra_env["INSAR_NISAR_SOURCE"] = os.environ["INSAR_NISAR_SOURCE"]
    elif os.environ.get("INSAR_HYP3_SOURCE"):
        extra_env["INSAR_HYP3_SOURCE"] = os.environ["INSAR_HYP3_SOURCE"]
    return CommandPlan(
        argv=[wrapper_python(), "-X", "utf8", script_rel],
        cwd=str(workspace),
        env={"PYTHONIOENCODING": "utf-8", **extra_env},
        files={script_rel: content},
        shell_line=f"python {script_rel}",
    )
