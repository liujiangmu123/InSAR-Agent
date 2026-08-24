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
  - 坐标系诚实:HyP3 装载的产物是 UTM 投影(EPSG=326xx,X_FIRST/REF_LAT
    等属性都是米坐标)—— lat/lon 入参按 WGS84 度换算成网格坐标再定位
    (api/geo.py,Krüger 级数,对照 pyproj 亚毫米级),响应里的
    lat/lon/extent 一律真经纬度,原生米坐标以 x/y/extent_native 并行给出;
    非 UTM 的投影网格不假装会换算,结构化 400 引导 row/col;
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

from insar_agent.api import geo
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


def _grid_epsg(attrs) -> int | None:
    """网格 CRS:EPSG 属性优先,缺失时由 UTM_ZONE('11N')推导;
    两者都缺 → None(按 MintPy geo 默认的地理坐标解释)。"""
    v = _attr_float(attrs, "EPSG")
    if v is not None:
        return int(v)
    zone = _attr_str(attrs, "UTM_ZONE").strip()
    if zone:
        return geo.utm_epsg_from_zone(zone)
    return None


def _extent_to_deg(epsg: int, native: dict) -> dict:
    """原生米坐标 extent → WGS84 度 extent(四角换算取包络;UTM 域内
    边缘弯曲远小于像元,包络即诚实近似)。"""
    corners = [(native["x_min"], native["y_min"]), (native["x_min"], native["y_max"]),
               (native["x_max"], native["y_min"]), (native["x_max"], native["y_max"])]
    lats, lons = zip(*(geo.utm_to_latlon(epsg, x, y) for x, y in corners))
    return {"lon_min": min(lons), "lon_max": max(lons),
            "lat_min": min(lats), "lat_max": max(lats)}


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
            # CRS 判定:HyP3 装载的产物是 UTM(EPSG=326xx),X/Y 是米 ——
            # 米坐标绝不冒充经纬度(report/export.py CSV 列名同一纪律)
            epsg = _grid_epsg(attrs) if has_geo else None
            projected = epsg is not None and epsg != 4326
            convertible = bool(projected and geo.epsg_is_utm(epsg))
            crs = ({"epsg": epsg,
                    "projected": projected,
                    "unit": "meter" if projected else "degree",
                    "latlon_convertible": (not projected) or convertible}
                   if has_geo else None)
            # extent 取像素「边缘」范围(X_FIRST 按第 0 列像元中心解释,
            # 边缘外扩半个像素):前端把点击相对坐标线性映射进 extent 后,
            # 最近像元取整不会在图边缘留出半像素宽的「假越界」死区
            extent_native = {
                "x_min": min(x_first - x_step / 2,
                             x_first + x_step * (n_cols - 0.5)),
                "x_max": max(x_first - x_step / 2,
                             x_first + x_step * (n_cols - 0.5)),
                "y_min": min(y_first - y_step / 2,
                             y_first + y_step * (n_rows - 0.5)),
                "y_max": max(y_first - y_step / 2,
                             y_first + y_step * (n_rows - 0.5)),
            } if has_geo else None
            if not has_geo:
                extent = None
            elif not projected:
                extent = {"lon_min": extent_native["x_min"],
                          "lon_max": extent_native["x_max"],
                          "lat_min": extent_native["y_min"],
                          "lat_max": extent_native["y_max"]}
            elif convertible:
                extent = _extent_to_deg(epsg, extent_native)
            else:
                extent = None  # 不会换算的投影:不假装有经纬度范围
            # 原生 extent 只在投影网格时随响应给出(地理网格 extent 本身就是度)
            native_out = extent_native if projected else None
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
                if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                    raise HTTPException(400, {
                        "error": "not_wgs84",
                        "message": "lat/lon 需为 WGS84 度(lat∈[-90,90],lon∈[-180,180]);"
                                   "若手里是投影米坐标,请改用 row/col 定位。",
                        "shape": shape, "crs": crs, "source": chosen.name,
                    })
                if projected and not convertible:
                    raise HTTPException(400, {
                        "error": "crs_unsupported",
                        "message": f"该产物是投影坐标网格(EPSG {epsg}),本服务只支持"
                                   " UTM 带的经纬度换算:请改用 row/col 参数定位。",
                        "shape": shape, "crs": crs,
                        "extent_native": extent_native, "source": chosen.name,
                    })
                # WGS84 度 → 网格原生坐标(UTM 米或地理度),再做像元换算
                if convertible:
                    qx, qy = geo.latlon_to_utm(epsg, lat, lon)
                else:
                    qx, qy = lon, lat
                # 半像素容差:落在边缘像元的外半格内仍算命中该像元
                # (与 extent 的边缘语义配对),真越界才 404
                rf = (qy - y_first) / y_step
                cf = (qx - x_first) / x_step
                if not (-0.5 <= rf <= n_rows - 0.5 and -0.5 <= cf <= n_cols - 0.5):
                    raise HTTPException(404, {
                        "error": "out_of_coverage",
                        "message": f"坐标超出数据覆盖范围(网格 {n_rows}×{n_cols})",
                        "shape": shape, "extent": extent, "crs": crs,
                        "extent_native": native_out, "source": chosen.name,
                    })
                r = min(n_rows - 1, max(0, round(rf)))
                c = min(n_cols - 1, max(0, round(cf)))
            elif row is not None and col is not None:
                r, c = row, col
            else:
                raise HTTPException(
                    400, "需提供 lat/lon(WGS84 度)或 row/col(像元行列)参数")

            if not (0 <= r < n_rows and 0 <= c < n_cols):
                raise HTTPException(404, {
                    "error": "out_of_coverage",
                    "message": f"坐标超出数据覆盖范围(网格 {n_rows}×{n_cols})",
                    "shape": shape, "extent": extent, "crs": crs,
                    "extent_native": native_out, "source": chosen.name,
                })

            # 像元中心坐标:lat/lon 一律真 WGS84 度(投影网格经反算),
            # 原生米坐标以 x/y 并行给出;无地理参考/不可换算 → None,不编造
            px = x_first + c * x_step if has_geo else None
            py = y_first + r * y_step if has_geo else None
            if has_geo and convertible:
                p_lat, p_lon = geo.utm_to_latlon(epsg, px, py)
            elif has_geo and not projected:
                p_lat, p_lon = py, px
            else:
                p_lat = p_lon = None
            point: dict = {"row": r, "col": c, "lat": p_lat, "lon": p_lon}
            if projected and px is not None:
                point["x"] = px
                point["y"] = py

            # ---- 惰性单像元列读取:hyperslab 只命中该像元所在 chunk,
            #      绝不把 dates × rows × cols 立方体整块载入内存 ----
            column = ds[:, r, c]

            raw_dates = f["date"][()]
            dates_all = [d.decode("utf-8", "replace") if isinstance(d, bytes)
                         else str(d) for d in raw_dates[:n_dates]]

            # 参考点:REF_LAT/REF_LON 属性优先,缺失时由 REF_Y/REF_X 像元
            # 坐标换算兜底。注意 MintPy 对 UTM 产物往 REF_LAT/REF_LON 里装的
            # 是 northing/easting 米(E2E 实测 REF_LAT=3914960)—— 按网格
            # CRS 诚实解释:投影网格反算成度,不可换算就不给经纬度
            ref_a = _attr_float(attrs, "REF_LAT")   # 纬向/北向
            ref_b = _attr_float(attrs, "REF_LON")   # 经向/东向
            ref_y_nat = ref_x_nat = None
            if ref_a is not None and ref_b is not None:
                ref_y_nat, ref_x_nat = ref_a, ref_b
            elif has_geo:
                ref_yi = _attr_float(attrs, "REF_Y")
                ref_xi = _attr_float(attrs, "REF_X")
                if ref_yi is not None and ref_xi is not None:
                    ref_y_nat = y_first + ref_yi * y_step
                    ref_x_nat = x_first + ref_xi * x_step
            ref_point = None
            if ref_y_nat is not None and ref_x_nat is not None:
                if convertible:
                    r_lat, r_lon = geo.utm_to_latlon(epsg, ref_x_nat, ref_y_nat)
                    ref_point = {"lat": r_lat, "lon": r_lon,
                                 "x": ref_x_nat, "y": ref_y_nat}
                elif not projected:
                    ref_point = {"lat": ref_y_nat, "lon": ref_x_nat}

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
                "point": point, "shape": shape, "extent": extent,
                "crs": crs, "extent_native": native_out, "source": chosen.name,
            })

        return {
            "dates": dates,
            "values_mm": values_mm,
            "ref_point": ref_point,
            "source": chosen.name,
            "point": point,
            "shape": shape,
            "extent": extent,
            "extent_native": native_out,
            "crs": crs,
            "n_dropped": n_dates - len(values_mm),
        }

    return router
