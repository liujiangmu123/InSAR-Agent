# -*- coding: utf-8 -*-
"""数据读取路由:点位时序(面板 2「影像」的地图-曲线双联动数据源)。

GET /api/timeseries-point:按 lat/lon(或 row/col 备选)从 run 工作区的
MintPy timeseries*.h5 取单像元时序。行业收敛交互「点图出时序」
(MintPy tsview / EGMS / InSAR Explorer 同构,见 RESEARCH-insar-viewer-ux)
的后端最小闭环:

  - 惰性读取:h5py 只取单像元列 ds[:, row, col],绝不整块载入
    dates × rows × cols 立方体(大 h5 也只触碰命中的 chunk);
  - 变体优先:校正程度越高越优先(demErr/ERA5 等,对齐
    registry/capabilities.py 第 8 步 timeseries_corrected 的候选表);
  - 模拟运行的占位文件(非 HDF5)→ 404 结构化说明「需要真实时序产物」;
  - 像元无效(全 NaN)→ 404 结构化说明,并携带 extent/shape 网格元数据
    (前端探测模式即使打在无效像元上也能学到坐标换算参数)。

会话归属校验与 app.py 的 resolve_run 同口径(不存在/不属于该会话一律 404,
不泄露其他会话 run 的存在性);本模块自实现,不动 app.py。
错误信息一律不携带磁盘绝对路径(与 /api/artifact-file 的口径一致)。
"""

from __future__ import annotations

import math
from pathlib import Path

from fastapi import APIRouter, HTTPException

from insar_agent.core.store import Store

#: 时序 h5 变体优先级:校正越完整越靠前(对齐注册表第 8 步候选表);
#: 未列出的带后缀变体排在其后,裸 timeseries.h5(第 7 步原始反演)垫底。
_VARIANT_PRIORITY = (
    "timeseries_ERA5_ramp_demErr.h5",
    "timeseries_ERA5_demErr.h5",
    "timeseries_ramp_demErr.h5",
    "timeseries_demErr.h5",
    "timeseries_corrected.h5",
)

#: 依次搜索的工作区子目录:MintPy 标准布局在 mintpy/,products/ 与根目录兜底
#: (对齐注册表第 7/8 步 ArtifactSpec 的候选路径)。
_SEARCH_DIRS = ("mintpy", "products", ".")


def _rank(p: Path) -> tuple[int, str]:
    """候选排序键:优先级表内 < 其他带后缀变体 < 裸 timeseries.h5。"""
    name = p.name
    if name in _VARIANT_PRIORITY:
        return (_VARIANT_PRIORITY.index(name), name)
    if name != "timeseries.h5":
        return (len(_VARIANT_PRIORITY), name)
    return (len(_VARIANT_PRIORITY) + 1, name)


def _find_candidates(workspace: Path) -> list[Path]:
    """run 工作区内全部 timeseries*.h5 候选,按变体优先级稳定排序。"""
    found: list[Path] = []
    for d in _SEARCH_DIRS:
        base = workspace if d == "." else workspace / d
        if not base.is_dir():
            continue
        for p in sorted(base.glob("timeseries*.h5")):
            if p.is_file() and p not in found:
                found.append(p)
    # sort 稳定:同名文件保持 mintpy/ → products/ → 根目录的目录优先序
    found.sort(key=_rank)
    return found


def _attr_float(attrs, key: str) -> float | None:
    """MintPy 属性一律按字符串落盘;bytes/str/数值都归一成有限 float。"""
    v = attrs.get(key)
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _attr_str(attrs, key: str, default: str = "") -> str:
    v = attrs.get(key, default)
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v)


def create_data_router(store: Store) -> APIRouter:
    """构造数据读取路由。store 由 create_app 注入(与 admin_router 同模式)。"""
    router = APIRouter()

    def resolve_run(session: str, run_id: str | None) -> dict:
        """按会话解析 run 并做归属校验 —— 与 app.py 的 resolve_run 同口径:
        run_id 缺省 → 该会话最近一个 run;不存在或不属于该会话 → 404
        (统一按「不存在」处理,不泄露其他会话的 run 是否存在)。"""
        run = store.get_run(run_id) if run_id else store.latest_run(session)
        if run is None:
            raise HTTPException(404, "no run")
        if run["session_id"] != session:
            raise HTTPException(
                404, f"run {run['run_id']} 不存在或不属于会话 {session}")
        return run

    @router.get("/api/timeseries-point")
    def timeseries_point(session: str, run_id: str | None = None,
                         lat: float | None = None, lon: float | None = None,
                         row: int | None = None, col: int | None = None):
        """单像元时序:lat/lon(优先)或 row/col 定位,返回
        {dates, values_mm, ref_point, source, point, shape, extent}。

        约定:
          - lat/lon 与 row/col 同时给出时以 lat/lon 为准;
          - 时序值统一换算为 mm(MintPy UNIT=m → ×1000);
          - 单历元 NaN 的历元被丢弃(dates 与 values_mm 保持配对),
            全 NaN 像元 → 404 结构化说明。
        """
        run = resolve_run(session, run_id)
        workspace = Path(run["workspace"])

        candidates = _find_candidates(workspace)
        if not candidates:
            raise HTTPException(404, {
                "error": "no_timeseries",
                "message": "该 run 工作区没有时序产物(mintpy/timeseries*.h5):"
                           "需要真实时序产物 —— 请先执行真实的时序反演步骤。",
            })

        try:
            # 惰性导入:h5py 属可选依赖组 raster,仅此端点需要
            import h5py
        except ImportError:
            raise HTTPException(
                500, "服务端未安装 h5py(pip install 'insar-agent[raster]')")

        # 按优先级取第一个「真 HDF5 且是 MintPy 时序布局」的候选;
        # 模拟运行产出的占位文件(纯文本/假字节)在此被全部滤掉。
        chosen: Path | None = None
        for p in candidates:
            if not h5py.is_hdf5(str(p)):
                continue
            with h5py.File(str(p), "r") as f:
                if "timeseries" in f and "date" in f and f["timeseries"].ndim == 3:
                    chosen = p
                    break
        if chosen is None:
            raise HTTPException(404, {
                "error": "placeholder",
                "message": "时序文件不是有效的 MintPy HDF5(模拟运行产出的占位文件):"
                           "需要真实时序产物 —— 模拟模式只演示流程,不产出可读数据。",
                "files": [p.name for p in candidates],
            })

        with h5py.File(str(chosen), "r") as f:
            ds = f["timeseries"]
            n_dates, n_rows, n_cols = (int(x) for x in ds.shape)
            attrs = dict(f.attrs)

            # 地理参考(MintPy geo 布局属性;radar 坐标产物没有这些键)
            x_first = _attr_float(attrs, "X_FIRST")
            x_step = _attr_float(attrs, "X_STEP")
            y_first = _attr_float(attrs, "Y_FIRST")
            y_step = _attr_float(attrs, "Y_STEP")
            has_geo = (None not in (x_first, x_step, y_first, y_step)
                       and x_step != 0 and y_step != 0)
            # extent 取像素「边缘」范围(X_FIRST 按第 0 列像元中心解释,
            # 边缘外扩半个像素):前端把点击相对坐标线性映射进 extent 后,
            # 最近像元取整不会在图边缘留出半像素宽的「假越界」死区
            extent = {
                "lon_min": min(x_first - x_step / 2,
                               x_first + x_step * (n_cols - 0.5)),
                "lon_max": max(x_first - x_step / 2,
                               x_first + x_step * (n_cols - 0.5)),
                "lat_min": min(y_first - y_step / 2,
                               y_first + y_step * (n_rows - 0.5)),
                "lat_max": max(y_first - y_step / 2,
                               y_first + y_step * (n_rows - 0.5)),
            } if has_geo else None
            shape = {"rows": n_rows, "cols": n_cols}

            # ---- 像元定位:lat/lon 优先,row/col 备选 ----
            if lat is not None and lon is not None:
                if not (math.isfinite(lat) and math.isfinite(lon)):
                    raise HTTPException(400, "lat/lon 必须是有限数值")
                if not has_geo:
                    raise HTTPException(400, {
                        "error": "no_geo",
                        "message": "该时序文件没有地理参考属性(X_FIRST/Y_STEP 等,"
                                   "可能是 radar 坐标产物):请改用 row/col 参数定位。",
                        "shape": shape, "source": chosen.name,
                    })
                # 半像素容差:落在边缘像元的外半格内仍算命中该像元
                # (与 extent 的边缘语义配对),真越界才 404
                rf = (lat - y_first) / y_step
                cf = (lon - x_first) / x_step
                if not (-0.5 <= rf <= n_rows - 0.5 and -0.5 <= cf <= n_cols - 0.5):
                    raise HTTPException(404, {
                        "error": "out_of_coverage",
                        "message": f"坐标超出数据覆盖范围(网格 {n_rows}×{n_cols})",
                        "shape": shape, "extent": extent, "source": chosen.name,
                    })
                r = min(n_rows - 1, max(0, round(rf)))
                c = min(n_cols - 1, max(0, round(cf)))
            elif row is not None and col is not None:
                r, c = row, col
            else:
                raise HTTPException(
                    400, "需提供 lat/lon(地理坐标)或 row/col(像元行列)参数")

            if not (0 <= r < n_rows and 0 <= c < n_cols):
                raise HTTPException(404, {
                    "error": "out_of_coverage",
                    "message": f"坐标超出数据覆盖范围(网格 {n_rows}×{n_cols})",
                    "shape": shape, "extent": extent, "source": chosen.name,
                })

            # 像元中心地理坐标(供前端标注;无地理参考时为 None)
            p_lat = y_first + r * y_step if has_geo else None
            p_lon = x_first + c * x_step if has_geo else None

            # ---- 惰性单像元列读取:hyperslab 只命中该像元所在 chunk,
            #      绝不把 dates × rows × cols 立方体整块载入内存 ----
            column = ds[:, r, c]

            raw_dates = f["date"][()]
            dates_all = [d.decode("utf-8", "replace") if isinstance(d, bytes)
                         else str(d) for d in raw_dates[:n_dates]]

            # 参考点:MintPy 校正后属性 REF_LAT/REF_LON 优先,
            # 缺失时由 REF_Y/REF_X 像元坐标换算兜底
            ref_lat = _attr_float(attrs, "REF_LAT")
            ref_lon = _attr_float(attrs, "REF_LON")
            if (ref_lat is None or ref_lon is None) and has_geo:
                ref_y = _attr_float(attrs, "REF_Y")
                ref_x = _attr_float(attrs, "REF_X")
                if ref_y is not None and ref_x is not None:
                    ref_lat = y_first + ref_y * y_step
                    ref_lon = x_first + ref_x * x_step
            ref_point = ({"lat": ref_lat, "lon": ref_lon}
                         if ref_lat is not None and ref_lon is not None else None)

            unit = _attr_str(attrs, "UNIT", "m").strip().lower()
            scale = 1.0 if unit.startswith("mm") else 1000.0  # MintPy 时序单位是 m

        # 单历元 NaN 丢弃(dates/values 保持配对);全 NaN → 像元无效
        dates: list[str] = []
        values_mm: list[float] = []
        for i, v in enumerate(column):
            fv = float(v)
            if math.isfinite(fv) and i < len(dates_all):
                dates.append(dates_all[i])
                values_mm.append(round(fv * scale, 3))
        if not values_mm:
            raise HTTPException(404, {
                "error": "pixel_invalid",
                "message": "该像元无有效数据(全 NaN):可能落在低相干掩膜区,"
                           "请换一个像元。",
                "point": {"row": r, "col": c, "lat": p_lat, "lon": p_lon},
                "shape": shape, "extent": extent, "source": chosen.name,
            })

        return {
            "dates": dates,
            "values_mm": values_mm,
            "ref_point": ref_point,
            "source": chosen.name,
            "point": {"row": r, "col": c, "lat": p_lat, "lon": p_lon},
            "shape": shape,
            "extent": extent,
            "n_dropped": n_dates - len(values_mm),
        }

    return router
