"""数据产品导出(0814B 契约 §2):MintPy HDF5 → h5 / csv / gtiff / kmz / shp。

分两层,能力与依赖诚实对齐:
  - venv 层(零引擎依赖):fmt=h5 直传源文件;fmt=csv 用 h5py+numpy 读栅格
    转点表 —— 列名按 EPSG 诚实分派(经纬度 lon,lat / 投影坐标 x,y),UTM 米
    坐标绝不冒充经纬度;h5py 缺失 → 501 诚实拒绝,绝不出假文件。
  - 引擎层:gtiff/kmz/shp 组装 MintPy CLI 命令,复用既有「以引擎前缀跑命令」
    机制 —— engines/mintpy.engine_python()(INSAR_ENGINE_PYTHON >
    INSAR_ENGINE_PREFIX > 隐式 conda 发现,与 smallbaselineApp/出图脚本同源),
    经 subprocess 同步执行(CREATE_NO_WINDOW,超时保护);引擎缺失 → 501 带
    安装指引;失败原样透传 stderr,绝不造假文件。

产物落 run 工作区 export/ 子目录,文件名 {run_id}_{product}.{ext};
同参幂等复用(目标在盘且不旧于源文件 → 直接复用,不重算)。
shp 例外:ESRI Shapefile 是多文件格式(.shp/.dbf/.shx/.prj),单发 .shp
不可用 —— 交付物是打包全套 sidecar 的 {run_id}_{product}.shp.zip。

命令签名对照真实 MintPy CLI(本机 E:\\miniforge3\\envs\\insar 实装源码核对):
  save_gdal.py <file> -d <dset> -o <out> --of GTiff   (-o 给定时原样使用)
  save_kmz.py  <file> [dset] -o <out.kmz>
  save_qgis.py <ts_file> -g <geom.h5> -o <out.shp>    (只支持时序产物)

本模块不 import FastAPI:错误一律抛 ExportError(status, message),
由 api/export_router.py 翻译为 HTTPException。
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # 后台导出不弹控制台

#: CSV 点数防线:超过即 400 提示先降采样(2D 是 rows×cols,时序是 ×dates)。
#: 5e6 行 ≈ 200-300 MB 文本,再大就该走 gtiff/h5 而不是点表。
MAX_CSV_POINTS = 5_000_000

#: 引擎导出默认超时(秒);INSAR_EXPORT_TIMEOUT 可放宽(大栅格 kmz 渲染慢)
_DEFAULT_ENGINE_TIMEOUT = 600.0


def engine_timeout() -> float:
    try:
        return float(os.environ.get("INSAR_EXPORT_TIMEOUT", "") or _DEFAULT_ENGINE_TIMEOUT)
    except ValueError:
        return _DEFAULT_ENGINE_TIMEOUT


class ExportError(Exception):
    """导出失败的闭集错误(status 即 HTTP 状态码,message 不携带磁盘绝对路径)。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class ProductSpec:
    key: str                    # 产品名(API 闭集)
    dataset: str                # h5 内数据集名(MintPy 命名)
    ndim: int                   # 2=单栅格,3=时序立方体
    own_file: bool              # h5 直传是否成立(velocityStd 寄生在 velocity.h5 → False)


#: 产品闭集。velocity_std 与 velocity 同文件不同数据集(MintPy velocity.h5
#: 内含 velocity + velocityStd 两个数据集)。
PRODUCTS: dict[str, ProductSpec] = {
    "velocity": ProductSpec("velocity", "velocity", 2, True),
    "velocity_std": ProductSpec("velocity_std", "velocityStd", 2, False),
    "timeseries": ProductSpec("timeseries", "timeseries", 3, True),
}

#: 格式闭集与扩展名/媒体类型(shp 交付 zip,见模块头)
FORMATS: tuple[str, ...] = ("h5", "csv", "gtiff", "kmz", "shp")
EXTENSIONS: dict[str, str] = {
    "h5": "h5", "csv": "csv", "gtiff": "tif", "kmz": "kmz", "shp": "shp.zip",
}
MEDIA_TYPES: dict[str, str] = {
    "h5": "application/x-hdf5",
    "csv": "text/csv",
    "gtiff": "image/tiff",
    "kmz": "application/vnd.google-earth.kmz",
    "shp": "application/zip",
}

#: 源文件搜索目录(对齐 registry ArtifactSpec 候选与 data_router 的搜索序)
_SEARCH_DIRS = ("mintpy", "products", ".")

#: 时序变体优先级:校正越完整越靠前(与 api/data_router 的口径一致 ——
#: 导出的科学交付物应当是校正后的时序,裸 timeseries.h5 垫底)
_TS_VARIANT_PRIORITY = (
    "timeseries_ERA5_ramp_demErr.h5",
    "timeseries_ERA5_demErr.h5",
    "timeseries_ramp_demErr.h5",
    "timeseries_demErr.h5",
    "timeseries_corrected.h5",
)


# ---------------- 源文件解析 ----------------

def _ts_rank(p: Path) -> tuple[int, str]:
    if p.name in _TS_VARIANT_PRIORITY:
        return (_TS_VARIANT_PRIORITY.index(p.name), p.name)
    if p.name != "timeseries.h5":
        return (len(_TS_VARIANT_PRIORITY), p.name)
    return (len(_TS_VARIANT_PRIORITY) + 1, p.name)


def find_source(workspace: Path, product: str) -> Path | None:
    """产品 → run 工作区内的源 h5(绝对路径);不存在返回 None。

    velocity/velocity_std 都取 velocity.h5(mintpy/ → products/ → 根目录);
    timeseries 取校正程度最高的 timeseries*.h5 变体。
    """
    spec = PRODUCTS[product]
    if spec.key == "timeseries":
        found: list[Path] = []
        for d in _SEARCH_DIRS:
            base = workspace if d == "." else workspace / d
            if not base.is_dir():
                continue
            for p in sorted(base.glob("timeseries*.h5")):
                if p.is_file() and p not in found:
                    found.append(p)
        found.sort(key=_ts_rank)
        return found[0] if found else None
    for d in _SEARCH_DIRS:
        p = (workspace if d == "." else workspace / d) / "velocity.h5"
        if p.is_file():
            return p
    return None


def _rel(workspace: Path, p: Path) -> str:
    """工作区相对路径(响应只回相对路径,不泄露磁盘布局)。"""
    try:
        return p.relative_to(workspace).as_posix()
    except ValueError:
        return p.name


def find_geometry(src: Path) -> Path | None:
    """save_qgis 需要的几何 h5:按 MintPy 标准布局在时序文件旁找。"""
    for rel in ("inputs/geometryGeo.h5", "geometryGeo.h5", "inputs/geometryRadar.h5"):
        cand = src.parent / rel
        if cand.is_file():
            return cand
    return None


# ---------------- 依赖可用性(options 矩阵与导出前置检查共用) ----------------

def h5py_available() -> bool:
    return importlib.util.find_spec("h5py") is not None


_H5PY_MISSING = ("服务端未安装 h5py,无法读取 HDF5 —— "
                 "pip install 'insar-agent[raster]' 后重试")


def engine_status() -> tuple[bool, str]:
    """MintPy 引擎可用性(gtiff/kmz/shp 的前置)。

    探测口径与 runtime/probe 完全同源(INSAR_ENGINE_PREFIX 显式配置或
    已知 conda 安装位的隐式发现);不可用时给安装指引(501 的 detail)。
    测试打桩点:monkeypatch 本函数即可密封引擎在/不在两种世界。
    """
    from insar_agent.runtime.probe import probe_environment

    probe = probe_environment(check_wsl=False)
    if probe.engines.get("mintpy"):
        return True, ""
    from insar_agent.runtime.install_guide import install_hint_for

    return False, ("MintPy 引擎不可用(未配置 INSAR_ENGINE_PREFIX 且未发现 "
                   "含 mintpy 的 conda 环境),gtiff/kmz/shp 导出需要引擎。"
                   f"安装指引 —— {install_hint_for('mintpy')}")


def _format_applicable(product: str, fmt: str) -> str:
    """产品×格式的结构性适配(与引擎/文件在场无关);可用返回 ""(空串)。"""
    spec = PRODUCTS[product]
    if fmt == "h5" and not spec.own_file:
        return ("velocityStd 数据集寄生在 velocity.h5 文件内:h5 请导出 "
                "product=velocity(同一文件),或用 csv/gtiff 单独导出该数据集")
    if fmt in ("gtiff", "kmz") and spec.ndim != 2:
        return (f"{product} 是 3D 时序立方体,{fmt} 单栅格导出需指定单期"
                "(本期不支持);可导出 csv/h5,或用 shp 携带全部历元")
    if fmt == "shp" and spec.key != "timeseries":
        return "shp 经 MintPy save_qgis 导出,只支持 timeseries 产品"
    return ""


def format_matrix(workspace: Path, *, simulated: bool) -> dict:
    """options 能力矩阵:产品×格式×可用性,不可用一律给诚实原因。"""
    engine_ok, engine_reason = engine_status()
    have_h5py = h5py_available()
    products = []
    for key, spec in PRODUCTS.items():
        src = find_source(workspace, key)
        formats: dict[str, dict] = {}
        for fmt in FORMATS:
            reason = ""
            if simulated:
                reason = "模拟产物不可导出为数据产品(runs.simulated=1)"
            if not reason:
                reason = _format_applicable(key, fmt)
            if not reason and src is None:
                reason = (f"run 工作区没有 {key} 产物 h5"
                          "(上游步骤未完成或产物已被清理)")
            if not reason and fmt == "csv" and not have_h5py:
                reason = _H5PY_MISSING
            if not reason and fmt in ("gtiff", "kmz", "shp") and not engine_ok:
                reason = engine_reason
            if not reason and fmt == "shp" and find_geometry(src) is None:
                reason = ("缺少几何文件 inputs/geometryGeo.h5"
                          "(save_qgis 需要它换算点位坐标)")
            formats[fmt] = {"available": not reason, "reason": reason or None}
        products.append({
            "product": key,
            "source": _rel(workspace, src) if src else None,
            "formats": formats,
        })
    return {
        "engine": {"available": engine_ok, "reason": engine_reason or None},
        "products": products,
    }


# ---------------- venv 层:CSV 转换 ----------------

def _attr_text(attrs, key: str) -> str | None:
    v = attrs.get(key)
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v)


def _attr_float(attrs, key: str) -> float | None:
    """MintPy 属性一律字符串落盘;bytes/str/数值归一为 float(data_router 同款)。"""
    v = _attr_text(attrs, key)
    if v is None:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    import math

    return f if math.isfinite(f) else None


def _coordinate_columns(attrs) -> tuple[str, str]:
    """按 EPSG 诚实分派坐标列名:地理坐标 lon,lat / 投影坐标 x,y。

    MintPy 经 HyP3 装载的产物常是 UTM(带 EPSG/UTM_ZONE 属性),米坐标
    绝不冒充经纬度;无 EPSG 属性或 EPSG=4326 才算地理坐标。
    """
    epsg = _attr_float(attrs, "EPSG")
    if epsg is not None and int(epsg) != 4326:
        return ("x", "y")
    if epsg is None and _attr_text(attrs, "UTM_ZONE"):
        return ("x", "y")
    return ("lon", "lat")


def export_csv(src: Path, product: str, target: Path) -> dict:
    """h5 栅格 → 点表 CSV(tmp+replace 原子落位)。

    - 2D:列 <lon,lat|x,y>,value;值非有限的像元行剔除;
    - 3D 时序:列 <lon,lat|x,y>,d<date>...;全 NaN 像元剔除,
      部分缺失的历元单元格写空串(保留像元,不编数);
    - 坐标取像元中心:X_FIRST + col*X_STEP(与 /api/timeseries-point 同口径);
    - 未地理编码(缺 X_FIRST 系属性)→ 409:行列号冒充坐标是造假。
    """
    if not h5py_available():
        raise ExportError(501, _H5PY_MISSING)
    import h5py
    import numpy as np

    spec = PRODUCTS[product]
    if not h5py.is_hdf5(str(src)):
        raise ExportError(422, f"{src.name} 不是有效的 HDF5 文件(可能已损坏)")
    with h5py.File(src, "r") as f:
        if spec.dataset not in f:
            raise ExportError(404, f"{src.name} 内没有数据集 {spec.dataset}")
        ds = f[spec.dataset]
        if ds.ndim != spec.ndim:
            raise ExportError(
                422, f"数据集 {spec.dataset} 维度异常({ds.ndim}D,预期 {spec.ndim}D)")
        total = 1
        for n in ds.shape:
            total *= int(n)
        if total > MAX_CSV_POINTS:
            raise ExportError(
                400, f"栅格共 {total:,} 点,超过 CSV 导出上限 {MAX_CSV_POINTS:,}:"
                     "请先降采样/裁剪子区,或改用 gtiff/h5 格式")
        attrs = dict(f.attrs)
        x0 = _attr_float(attrs, "X_FIRST")
        y0 = _attr_float(attrs, "Y_FIRST")
        dx = _attr_float(attrs, "X_STEP")
        dy = _attr_float(attrs, "Y_STEP")
        if None in (x0, y0, dx, dy) or dx == 0 or dy == 0:
            raise ExportError(
                409, f"{src.name} 缺少地理编码属性(X_FIRST/Y_FIRST/X_STEP/Y_STEP):"
                     "CSV 点表需要真实坐标,行列号冒充坐标是造假 —— 请先完成 geocode")
        cx, cy = _coordinate_columns(attrs)

        if spec.ndim == 2:
            data = ds[:]
            header = f"{cx},{cy},value"
            rows_iter = _rows_2d(data, x0, y0, dx, dy, np)
        else:
            cube = ds[:]
            raw_dates = f["date"][()] if "date" in f else []
            dates = [d.decode("utf-8", "replace") if isinstance(d, bytes) else str(d)
                     for d in raw_dates]
            if len(dates) != cube.shape[0]:
                raise ExportError(
                    422, f"{src.name} 的 date 数据集与时序立方体不配对"
                         f"({len(dates)} vs {cube.shape[0]})")
            header = ",".join([cx, cy, *(f"d{d}" for d in dates)])
            rows_iter = _rows_3d(cube, x0, y0, dx, dy, np)

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        rows = 0
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as out:
                out.write(header + "\n")
                for line in rows_iter:
                    out.write(line + "\n")
                    rows += 1
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    return {"rows": rows, "header": header}


def _fmt_val(v: float) -> str:
    return format(v, ".6g")


def _fmt_coord(v: float) -> str:
    # 坐标比观测值需要更多有效位:UTM northing 是 7 位整数米,.6g 会丢米级
    # 精度;.10g 对经纬度(度)与米坐标都保住亚像元精度且不放大浮点噪声
    return format(v, ".10g")


def _rows_2d(data, x0: float, y0: float, dx: float, dy: float, np):
    """2D 栅格行生成器:NaN/Inf 像元剔除(诚实点表只含有效观测)。"""
    mask = np.isfinite(data)
    for r, c in np.argwhere(mask):
        yield (f"{_fmt_coord(x0 + int(c) * dx)},{_fmt_coord(y0 + int(r) * dy)},"
               f"{_fmt_val(float(data[r, c]))}")


def _rows_3d(cube, x0: float, y0: float, dx: float, dy: float, np):
    """3D 时序行生成器:全 NaN 像元剔除;部分缺失历元写空串(不编数)。"""
    finite = np.isfinite(cube)
    mask = finite.any(axis=0)
    for r, c in np.argwhere(mask):
        cells = [(_fmt_val(float(cube[i, r, c])) if finite[i, r, c] else "")
                 for i in range(cube.shape[0])]
        yield ",".join([_fmt_coord(x0 + int(c) * dx),
                        _fmt_coord(y0 + int(r) * dy), *cells])


# ---------------- 引擎层:MintPy CLI 子进程 ----------------

def build_engine_argv(fmt: str, src: Path, dataset: str, out: Path,
                      *, geom: Path | None = None) -> list[str]:
    """组装 MintPy 导出命令(解释器复用 engines/mintpy.engine_python 机制)。"""
    from insar_agent.engines.mintpy import engine_python

    py = engine_python()
    if fmt == "gtiff":
        return [py, "-u", "-m", "mintpy.cli.save_gdal", str(src),
                "-d", dataset, "-o", str(out), "--of", "GTiff"]
    if fmt == "kmz":
        return [py, "-u", "-m", "mintpy.cli.save_kmz", str(src), dataset, "-o", str(out)]
    if fmt == "shp":
        assert geom is not None  # 调用方已做 404 检查
        return [py, "-u", "-m", "mintpy.cli.save_qgis", str(src),
                "-g", str(geom), "-o", str(out)]
    raise ExportError(400, f"未知引擎导出格式:{fmt}")


def _run_engine(argv: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    """引擎子进程执行(测试打桩点)。环境合并口径与 local_wrapper 一致
    (os.environ 打底 + 引擎侧惯例变量),CREATE_NO_WINDOW 不弹控制台。"""
    env = {**os.environ,
           "HDF5_USE_FILE_LOCKING": "FALSE",   # DESIGN.md:472,MintPy 惯例
           "PYTHONIOENCODING": "utf-8",
           "PYTHONUTF8": "1",                  # 中文 Windows:MintPy 不指定 encoding
           "MPLBACKEND": "Agg"}                # save_kmz 内部画图,无头环境防 GUI 后端
    return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True,
                          text=True, errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW)


def _tail(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


def _run_engine_or_raise(argv: list[str], cwd: Path, tool: str,
                         expect: list[Path]) -> None:
    """跑引擎命令并核验产出:超时 504、非零退出 502(stderr 原样透传)、
    退出 0 但产物缺失同样 502 —— 绝不造假文件。"""
    timeout = engine_timeout()
    try:
        cp = _run_engine(argv, cwd, timeout)
    except subprocess.TimeoutExpired:
        raise ExportError(504, f"引擎导出超时({tool} 超过 {timeout:.0f}s 未完成):"
                               "大栅格请先裁剪/降采样,或调大 INSAR_EXPORT_TIMEOUT")
    except OSError as exc:
        raise ExportError(502, f"引擎子进程启动失败({tool}):{exc}")
    if cp.returncode != 0:
        detail = _tail(cp.stderr) or _tail(cp.stdout) or "(无输出)"
        raise ExportError(502, f"引擎导出失败({tool} 退出码 {cp.returncode}):{detail}")
    missing = [p.name for p in expect if not p.is_file()]
    if missing:
        raise ExportError(502, f"引擎报告成功但未产出文件:{'、'.join(missing)}"
                               f"({tool} 行为异常,绝不伪造产物)")


def _export_gtiff_kmz(fmt: str, src: Path, spec: ProductSpec, target: Path) -> None:
    """gtiff/kmz:引擎写 .part 再原子替换(读者永远看不到半截产物)。"""
    part = target.with_name(target.name + ".part")
    part.unlink(missing_ok=True)
    argv = build_engine_argv(fmt, src, spec.dataset, part)
    tool = "save_gdal" if fmt == "gtiff" else "save_kmz"
    _run_engine_or_raise(argv, target.parent, tool, expect=[part])
    os.replace(part, target)


def _export_shp(src: Path, target: Path, geom: Path) -> None:
    """shp:save_qgis 产出 .shp/.dbf/.shx/.prj 多文件,打包成 zip 交付。

    工作目录固定 export/.shp_work(幂等重跑自然覆写),打包后清理。
    """
    work = target.parent / ".shp_work"
    work.mkdir(parents=True, exist_ok=True)
    base = work / (target.name.removesuffix(".shp.zip") + ".shp")
    argv = build_engine_argv("shp", src, "timeseries", base, geom=geom)
    _run_engine_or_raise(argv, work, "save_qgis", expect=[base])
    members = [p for p in sorted(work.iterdir())
               if p.is_file() and p.stem == base.stem and p.suffix != ".zip"]
    part = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            for m in members:
                zf.write(m, arcname=m.name)
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    for m in members:  # 打包完成后清理工作目录(失败保留现场供诊断)
        m.unlink(missing_ok=True)


# ---------------- 入口:导出编排(幂等复用) ----------------

@dataclass(frozen=True)
class ExportResult:
    path: Path        # 交付文件(绝对路径,router 做 FileResponse)
    filename: str     # 下载名 {run_id}_{product}.{ext}
    media_type: str
    reused: bool      # 幂等命中(h5 直传恒为 True:源文件即交付物)


def safe_filename_stem(run_id: str) -> str:
    """run_id → 文件名安全片段(repro_bundle 同款白名单清洗,防坏 DB 行
    携带路径分隔符逃出 export/ 目录)。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", run_id)[:64] or "run"


def perform_export(workspace: Path, run_id: str, product: str, fmt: str) -> ExportResult:
    """执行(或幂等复用)一次导出。product/fmt 已由 router 做闭集校验。

    错误闭集:源 h5 缺失 404;结构不适配 400;h5py 缺失/引擎缺失 501;
    几何缺失 404;引擎失败 502;超时 504。全部 ExportError,不出假文件。
    """
    spec = PRODUCTS[product]
    reason = _format_applicable(product, fmt)
    if reason:
        raise ExportError(400, reason)
    src = find_source(workspace, product)
    if src is None:
        raise ExportError(
            404, f"run 工作区没有 {product} 产物 h5(查找了 mintpy/、products/ 与根目录):"
                 "上游步骤未完成或产物已被清理")

    filename = f"{safe_filename_stem(run_id)}_{product}.{EXTENSIONS[fmt]}"
    media = MEDIA_TYPES[fmt]
    if fmt == "h5":
        # 直传源文件(velocity.h5 / timeseries*.h5 本体),不复制不改写
        return ExportResult(src, filename, media, reused=True)

    target = workspace / "export" / filename
    if target.is_file() and target.stat().st_mtime >= src.stat().st_mtime:
        return ExportResult(target, filename, media, reused=True)  # 同参幂等复用
    target.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        export_csv(src, product, target)
        return ExportResult(target, filename, media, reused=False)

    # ---- 引擎格式(gtiff/kmz/shp) ----
    ok, engine_reason = engine_status()
    if not ok:
        raise ExportError(501, engine_reason)
    if fmt == "shp":
        geom = find_geometry(src)
        if geom is None:
            raise ExportError(404, "缺少几何文件 inputs/geometryGeo.h5"
                                   "(save_qgis 需要它换算点位坐标)")
        _export_shp(src, target, geom)
    else:
        _export_gtiff_kmz(fmt, src, spec, target)
    return ExportResult(target, filename, media, reused=False)
