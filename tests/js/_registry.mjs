/* ============================================================
   注册表夹具 —— GET /api/registry（无 session 探测）载荷的快照。
   由服务端能力声明（src/insar_agent/registry/capabilities.py）生成,
   与 api/app.py registry() 端点逐字段一致（probe=None 分支:
   ok=True / simulated=False / blocked=""）。

   用途:前端测试/校验脚本喂 state.setRegistry(REGISTRY),
   在 Node 里复现「注册表水合后的 STEP_DEFS」——这是真实服务端
   目录的快照,不是手写演示数据。registry 声明变更后重生成:
     .venv\Scripts\python scripts\dump_registry_fixture.py
   （若脚本已删除,按本文件头的字段映射从 capabilities.py 重导即可。）
   ============================================================ */
export const REGISTRY =
[
  {
    "id": 1,
    "name": "数据获取",
    "deps": [],
    "method": "local_import",
    "methods": [
      {
        "id": "asf_search_slc",
        "label": "asf_search_slc",
        "engine": "asf_api",
        "why": "下载原始 SLC,可控性最强",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "hyp3_submit",
        "label": "hyp3_submit",
        "engine": "hyp3",
        "why": "云端处理,跳过 2-6 步,失去中间产物控制权",
        "recommend": false,
        "extra": "需 ASF 配额",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "local_import",
        "label": "local_import",
        "engine": "-",
        "why": "已有本地数据",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "scenes": {
        "default": 7,
        "kind": "science",
        "type": "int",
        "min": 2,
        "max": 200,
        "hint": "景数 2-200"
      },
      "platform": {
        "default": "sentinel-1",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "dates": {
        "default": "2019-06-10..2019-08-15",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "source": {
        "default": "",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "local_import 的数据源目录(HyP3 产品目录或 SLC 目录)"
      }
    },
    "outputs": [
      {
        "path": "data/slc",
        "kind": "SLC",
        "layout": "isce2"
      },
      {
        "path": "hyp3",
        "kind": "IFG_UNWRAPPED",
        "layout": "hyp3"
      },
      {
        "path": "mintpy/inputs/ERA5.h5",
        "kind": "CONFIG",
        "layout": ""
      }
    ],
    "replay": "safe",
    "timeouts": {
      "idle": 1800,
      "total": 21600
    }
  },
  {
    "id": 2,
    "name": "辅助数据",
    "deps": [
      1
    ],
    "method": "dem_copernicus",
    "methods": [
      {
        "id": "dem_copernicus",
        "label": "dem_copernicus",
        "engine": "dem_service",
        "why": "Copernicus 30 m,覆盖全球且质量稳定",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "dem_srtm",
        "label": "dem_srtm",
        "engine": "dem_service",
        "why": "SRTM 30 m,高纬度覆盖缺口",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "dem_local",
        "label": "dem_local",
        "engine": "-",
        "why": "使用本地 DEM 瓦片",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "dem": {
        "default": "copernicus-30m",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "orbit": {
        "default": "poeorb",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "data/dem",
        "kind": "DEM",
        "layout": "isce2"
      }
    ],
    "replay": "safe",
    "timeouts": {
      "idle": 600,
      "total": 3600
    }
  },
  {
    "id": 3,
    "name": "配准",
    "deps": [
      1,
      2
    ],
    "method": "isce2_tops_geom_esd",
    "methods": [
      {
        "id": "isce2_tops_geom_esd",
        "label": "isce2_tops_geom_esd",
        "engine": "isce2",
        "why": "S1 IW 标准路径:几何配准 + ESD 精化",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "isce2_stripmap_xcorr",
        "label": "isce2_stripmap_xcorr",
        "engine": "isce2",
        "why": "条带模式(ALOS raw,2026-08 WSL 实测全链通过)",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "snap_backgeocoding",
        "label": "snap_backgeocoding",
        "engine": "snap",
        "why": "走 SNAP 链,需换 layout",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "esd_coherence_threshold": {
        "default": 0.85,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": "ESD 相干阈值 0-1"
      },
      "reference_image": {
        "default": "data/raw/reference/IMG-HH",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:参考 raw 影像 IMG 相对路径"
      },
      "reference_leader": {
        "default": "data/raw/reference/LED",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:参考影像 LED 头文件相对路径"
      },
      "secondary_image": {
        "default": "data/raw/secondary/IMG-HH",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:从 raw 影像 IMG 相对路径"
      },
      "secondary_leader": {
        "default": "data/raw/secondary/LED",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:从影像 LED 头文件相对路径"
      },
      "resample_flag": {
        "default": "",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:FBD 从影像配 FBS 主影像时用 dual2single,空=不重采样"
      },
      "dem_path": {
        "default": "data/dem/dem.wgs84",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": "stripmap:ISCE 格式 DEM 相对路径"
      },
      "threads": {
        "default": 8,
        "kind": "resource",
        "type": "int",
        "min": 1,
        "max": 32,
        "hint": "线程数 1-32(本机 24 核,留 4 核给系统)"
      }
    },
    "outputs": [
      {
        "path": "data/coreg",
        "kind": "RSLC",
        "layout": "isce2"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 14400
    }
  },
  {
    "id": 4,
    "name": "干涉",
    "deps": [
      3
    ],
    "method": "isce2_ifg_multilook",
    "methods": [
      {
        "id": "isce2_ifg_multilook",
        "label": "isce2_ifg_multilook",
        "engine": "isce2",
        "why": "可调多视比。小基线网络按时空基线剪枝,非全组合",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "snap_interferogram",
        "label": "snap_interferogram",
        "engine": "snap",
        "why": "SNAP 链对应步骤",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "isce2_stripmap_ifg",
        "label": "isce2_stripmap_ifg",
        "engine": "isce2",
        "why": "条带链干涉:stripmapApp 分频谱→干涉→滤波段(ALOS raw)",
        "recommend": false,
        "extra": "实测(ALOS Baja):阶段1 startup→filter 约 20 min;全链 33 min/32 GB",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "range_looks": {
        "default": 10,
        "kind": "science",
        "type": "int",
        "min": 1,
        "max": 40,
        "hint": "距离向多视 1-40"
      },
      "azimuth_looks": {
        "default": 2,
        "kind": "science",
        "type": "int",
        "min": 1,
        "max": 40,
        "hint": "方位向多视 1-40"
      },
      "pairs": {
        "default": 11,
        "kind": "science",
        "type": "int",
        "min": 1,
        "max": 5000,
        "hint": ""
      },
      "threads": {
        "default": 8,
        "kind": "resource",
        "type": "int",
        "min": 1,
        "max": 32,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "data/ifg",
        "kind": "IFG_WRAPPED",
        "layout": "isce2"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 3600,
      "total": 28800
    }
  },
  {
    "id": 5,
    "name": "滤波",
    "deps": [
      4
    ],
    "method": "goldstein",
    "methods": [
      {
        "id": "goldstein",
        "label": "goldstein",
        "engine": "isce2",
        "why": "低相干区推荐",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "boxcar",
        "label": "boxcar",
        "engine": "isce2",
        "why": "简单快速,但边缘模糊",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "none",
        "label": "none",
        "engine": "-",
        "why": "不滤波,保留全部细节",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "isce2_stripmap_filter",
        "label": "isce2_stripmap_filter",
        "engine": "isce2",
        "why": "条带链滤波:stripmapApp filter 单步(ALOS raw)",
        "recommend": false,
        "extra": "单步重跑,分钟级(实测全链 33 min 内占比很小)",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "alpha": {
        "default": 0.4,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": "Goldstein alpha 0-1"
      },
      "filter_strength": {
        "default": 0.5,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "data/ifg_filt",
        "kind": "IFG_WRAPPED",
        "layout": "isce2"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 14400
    }
  },
  {
    "id": 6,
    "name": "解缠",
    "deps": [
      5
    ],
    "method": "snaphu_mcf",
    "methods": [
      {
        "id": "snaphu_mcf",
        "label": "snaphu_mcf",
        "engine": "snaphu",
        "why": "Minimum Cost Flow。低相干区稳健,MintPy 原生兼容",
        "recommend": true,
        "extra": "耗时/内存待实测",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "snaphu_smooth",
        "label": "snaphu_smooth",
        "engine": "snaphu",
        "why": "精度更高但需人工调 cost function",
        "recommend": false,
        "extra": "需交互配置",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "icu",
        "label": "icu",
        "engine": "isce2",
        "why": "区域增长法,大范围低相干区易产生解缠孤岛",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "3D_FULL",
        "label": "3D_FULL",
        "engine": "unw3d",
        "why": "需 3D 相位解缠工具链,输出格式与下游不兼容",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "isce2_stripmap_unwrap_snaphu",
        "label": "isce2_stripmap_unwrap_snaphu",
        "engine": "isce2",
        "why": "条带链解缠:stripmapApp 内置 snaphu + 地理编码(ALOS raw)",
        "recommend": false,
        "extra": "实测 filter_low_band→geocode 约 13 min(snaphu 约 10 min)",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "min_coherence": {
        "default": 0.25,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": "相干性阈值 0-1"
      },
      "cost_mode": {
        "default": "SMOOTH",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "threads": {
        "default": 8,
        "kind": "resource",
        "type": "int",
        "min": 1,
        "max": 32,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "data/unw",
        "kind": "IFG_UNWRAPPED",
        "layout": "isce2"
      },
      {
        "path": "params/unwrap.yaml",
        "kind": "CONFIG",
        "layout": ""
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 7200
    }
  },
  {
    "id": 7,
    "name": "时序反演",
    "deps": [
      6
    ],
    "method": "mintpy_sbas",
    "methods": [
      {
        "id": "mintpy_sbas",
        "label": "mintpy_sbas",
        "engine": "mintpy",
        "why": "小基线集,适合低相干面状形变区",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "pystamps_ps",
        "label": "pystamps_ps",
        "engine": "pystamps",
        "why": "永久散射体,适合高相干点状目标;需 ISCE2→PyStamps 桥",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "network": {
        "default": "small_baseline",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "max_temporal_baseline": {
        "default": 120,
        "kind": "science",
        "type": "int",
        "min": 6,
        "max": 730,
        "hint": "时间基线 6-730 天"
      },
      "parallel_workers": {
        "default": 4,
        "kind": "resource",
        "type": "int",
        "min": 1,
        "max": 16,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "mintpy/timeseries.h5",
        "kind": "TIMESERIES",
        "layout": "mintpy_h5"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 86400
    }
  },
  {
    "id": 8,
    "name": "误差校正",
    "deps": [
      7
    ],
    "method": "tropo_era5_pyaps",
    "methods": [
      {
        "id": "tropo_era5_pyaps",
        "label": "tropo_era5_pyaps",
        "engine": "pyaps",
        "why": "ERA5 大气校正(需 CDS 凭据或已缓存的 ERA5.h5)",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "tropo_gacos",
        "label": "tropo_gacos",
        "engine": "gacos",
        "why": "GACOS 产品,需在线申请",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "tropo_height_corr",
        "label": "tropo_height_corr",
        "engine": "mintpy",
        "why": "无气象数据时的降级方案(证据级别下降)",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "ramp": {
        "default": "no",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "dem_error": {
        "default": true,
        "kind": "science",
        "type": "bool",
        "min": null,
        "max": null,
        "hint": ""
      },
      "solid_earth_tides": {
        "default": false,
        "kind": "science",
        "type": "bool",
        "min": null,
        "max": null,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "mintpy/timeseries_ERA5_ramp_demErr.h5",
        "kind": "TIMESERIES",
        "layout": "mintpy_h5"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 1800,
      "total": 21600
    }
  },
  {
    "id": 9,
    "name": "形变模型",
    "deps": [
      8
    ],
    "method": "linear",
    "methods": [
      {
        "id": "poly_periodic",
        "label": "poly_periodic(1,[1,0.5])",
        "engine": "mintpy",
        "why": "线性 + 年周期 + 半年周期,匹配季节冻融机理",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "linear",
        "label": "linear",
        "engine": "mintpy",
        "why": "仅线性趋势",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "step",
        "label": "step(date)",
        "engine": "mintpy",
        "why": "同震阶跃",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "exponential",
        "label": "exponential",
        "engine": "mintpy",
        "why": "震后/矿区衰减形变",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "periods": {
        "default": [
          1,
          0.5
        ],
        "kind": "science",
        "type": "list",
        "min": null,
        "max": null,
        "hint": ""
      },
      "poly_order": {
        "default": 1,
        "kind": "science",
        "type": "int",
        "min": 0,
        "max": 3,
        "hint": ""
      },
      "step_date": {
        "default": "",
        "kind": "science",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "mintpy/velocity.h5",
        "kind": "VELOCITY",
        "layout": "mintpy_h5"
      }
    ],
    "replay": "never",
    "timeouts": {
      "idle": 600,
      "total": 3600
    }
  },
  {
    "id": 10,
    "name": "出图导出",
    "deps": [
      9
    ],
    "method": "figure_journal",
    "methods": [
      {
        "id": "figure_journal",
        "label": "figure_journal",
        "engine": "-",
        "why": "期刊级排版:600 dpi、色盲安全色带、比例尺",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "mintpy_geocode",
        "label": "mintpy_geocode",
        "engine": "mintpy",
        "why": "仅地理编码,不做排版",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "gdal_warp",
        "label": "gdal_warp",
        "engine": "gdal",
        "why": "导出 GeoTIFF 供 GIS 使用",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "dpi": {
        "default": 600,
        "kind": "presentation",
        "type": "int",
        "min": 72,
        "max": 1200,
        "hint": "出图 DPI 72-1200"
      },
      "cmap": {
        "default": "roma",
        "kind": "presentation",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      },
      "format": {
        "default": "png+pdf",
        "kind": "presentation",
        "type": "str",
        "min": null,
        "max": null,
        "hint": ""
      }
    },
    "outputs": [
      {
        "path": "products/figures",
        "kind": "FIGURE",
        "layout": ""
      }
    ],
    "replay": "safe",
    "timeouts": {
      "idle": 600,
      "total": 1800
    }
  },
  {
    "id": 11,
    "name": "质检",
    "deps": [
      10,
      7
    ],
    "method": "crossval_ps_sbas",
    "methods": [
      {
        "id": "crossval_ps_sbas",
        "label": "crossval_ps_sbas",
        "engine": "-",
        "why": "PS/SBAS 双链交叉验证 —— 本项目独有质量门",
        "recommend": true,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "loop_closure",
        "label": "loop_closure",
        "engine": "mintpy",
        "why": "闭合回路残差检查,只验解缠不验反演",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      },
      {
        "id": "coherence_mask",
        "label": "coherence_mask",
        "engine": "mintpy",
        "why": "相干性掩膜,最弱的质检",
        "recommend": false,
        "extra": "",
        "ok": true,
        "simulated": false,
        "blocked": ""
      }
    ],
    "params": {
      "corr_threshold": {
        "default": 0.85,
        "kind": "science",
        "type": "number",
        "min": 0,
        "max": 1,
        "hint": "交叉验证相关阈值 0-1"
      }
    },
    "outputs": [
      {
        "path": "products/report/qa.json",
        "kind": "REPORT",
        "layout": ""
      }
    ],
    "replay": "safe",
    "timeouts": {
      "idle": 900,
      "total": 7200
    }
  }
];

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
